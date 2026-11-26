"""End-to-end evidence-mining orchestration pipeline."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from delphi_miner.decomposer import decompose_market
from delphi_miner.delphi_client import DelphiAPIError, DelphiEvidenceClient
from delphi_miner.dossier import build_evidence_dossier, write_dossier_files
from delphi_miner.fetchers import PublicSourceFetcher
from delphi_miner.ledger import ProvenanceLedger
from delphi_miner.models import EvidenceDossier, EvidenceSignal, MarketSpec, ProvenanceRecord, SourceDocument
from delphi_miner.scorer import aggregate_corroboration, score_document


def score_market_opportunity(market: MarketSpec, now: datetime | None = None) -> float:
    """Rank open markets by uncertainty (peak near 50%), USDC pot size, and cutoff readiness."""
    ref_now = now or datetime.now(timezone.utc)
    if market.status.lower() != "open":
        return -1.0
    if market.evidence_cutoff_at:
        try:
            cutoff = datetime.fromisoformat(market.evidence_cutoff_at.replace("Z", "+00:00"))
            if cutoff <= ref_now:
                return -1.0
        except ValueError:
            pass

    uncertainty = max(0.0, 1.0 - 2.0 * abs(market.odds.yes - 0.5))
    pot_factor = min(2.0, market.pots.total_usdc / 50.0)
    return round(0.60 * uncertainty + 0.40 * pot_factor, 4)


def rank_markets(markets: list[MarketSpec], limit: int = 10) -> list[MarketSpec]:
    """Filter open markets before cutoff and sort descending by opportunity score."""
    scored: list[tuple[float, MarketSpec]] = []
    now = datetime.now(timezone.utc)
    for m in markets:
        sc = score_market_opportunity(m, now=now)
        if sc >= 0.0:
            scored.append((sc, m))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [m for _, m in scored[:limit]]


class EvidenceMiningPipeline:
    """Coordinates market selection, claim decomposition, multi-source fetch, scoring, and ledgering."""

    def __init__(
        self,
        fetcher: PublicSourceFetcher | None = None,
        ledger: ProvenanceLedger | None = None,
        delphi_client: DelphiEvidenceClient | None = None,
    ) -> None:
        self._owns_fetcher = fetcher is None
        self.fetcher = fetcher or PublicSourceFetcher()
        self.ledger = ledger or ProvenanceLedger("delphi_ledger.db")
        self.delphi_client = delphi_client or DelphiEvidenceClient()

    def close(self) -> None:
        if self._owns_fetcher:
            self.fetcher.close()
        self.delphi_client.close()

    def __enter__(self) -> EvidenceMiningPipeline:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def gather_sources_for_market(
        self,
        market: MarketSpec,
        extra_urls: list[str] | None = None,
        include_external_apis: bool = True,
    ) -> tuple[list[SourceDocument], list[str]]:
        """
        Fetch designated market dataSources (+ discovered RSS/Atom feeds), optional extra URLs,
        and relevant public APIs (Wikipedia, arXiv, GitHub, SEC EDGAR).
        Returns (documents, non_fatal_fetch_errors).
        """
        decomp = decompose_market(market)
        docs: list[SourceDocument] = []
        errors: list[str] = []
        seen_keys: set[tuple[str, str]] = set()

        def _add_doc(d: SourceDocument) -> None:
            key = (d.normalized_url, d.content_sha256)
            if key not in seen_keys:
                seen_keys.add(key)
                docs.append(d)

        # 1. Designated market dataSources + extra URLs
        candidate_urls = list(dict.fromkeys(list(market.data_sources) + list(extra_urls or [])))
        discovered_feeds: list[str] = []
        for url in candidate_urls[:6]:
            try:
                fetched_docs, feeds = self.fetcher.fetch_url(url)
                for d in fetched_docs:
                    _add_doc(d)
                discovered_feeds.extend(feeds)
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"url:{url} -> {exc}")

        # 2. Follow up to 2 discovered RSS/Atom feeds on official sites
        for feed_url in list(dict.fromkeys(discovered_feeds))[:2]:
            try:
                feed_docs, _ = self.fetcher.fetch_url(feed_url)
                for d in feed_docs[:5]:
                    _add_doc(d)
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"feed:{feed_url} -> {exc}")

        # 3. Public knowledge / domain APIs
        if include_external_apis and decomp.suggested_queries:
            primary_q = decomp.suggested_queries[0]
            try:
                for d in self.fetcher.fetch_wikipedia(primary_q, max_results=2):
                    _add_doc(d)
            except (httpx.HTTPError, ValueError) as exc:
                errors.append(f"wikipedia:{primary_q} -> {exc}")

            cat = market.category.lower()
            q_low = market.question.lower()

            if cat in {"releases_standards", "niche_communities"} or any(
                w in q_low.split() for w in ["ietf", "rfc", "github", "kaggle", "arc-agi-3", "arxiv", "benchmark"]
            ):
                try:
                    for d in self.fetcher.fetch_github(primary_q, max_results=2):
                        _add_doc(d)
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append(f"github:{primary_q} -> {exc}")
                try:
                    for d in self.fetcher.fetch_arxiv(primary_q, max_results=2):
                        _add_doc(d)
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append(f"arxiv:{primary_q} -> {exc}")

            if cat in {"official_statistics", "macro_rates"} or any(
                k in q_low for k in ["sec", "cpi", "inflation", "fed", "rate", "treasury", "etf", "earnings"]
            ):
                try:
                    for d in self.fetcher.fetch_sec_edgar(primary_q, max_results=2):
                        _add_doc(d)
                except (httpx.HTTPError, ValueError) as exc:
                    errors.append(f"sec_edgar:{primary_q} -> {exc}")

        return docs, errors

    def mine_market(
        self,
        market: MarketSpec,
        extra_urls: list[str] | None = None,
        include_external_apis: bool = True,
        output_dir: str | Path | None = None,
        submit_live: bool = False,
    ) -> dict[str, Any]:
        """Run full mining pipeline on a single market and return dossier + metadata."""
        decomposition = decompose_market(market)
        docs, fetch_errors = self.gather_sources_for_market(
            market,
            extra_urls=extra_urls,
            include_external_apis=include_external_apis,
        )

        doc_signal_pairs: list[tuple[SourceDocument, EvidenceSignal]] = []
        for doc in docs:
            sig = score_document(doc, decomposition)
            doc_signal_pairs.append((doc, sig))

        # Sort highest confidence first
        doc_signal_pairs.sort(key=lambda pair: pair[1].confidence, reverse=True)
        top_pairs = doc_signal_pairs[:12]

        provenance_records: list[ProvenanceRecord] = []
        signals: list[EvidenceSignal] = []
        for doc, sig in top_pairs:
            signals.append(sig)
            if sig.relevance_score >= 0.05:
                rec = self.ledger.record_signal(market.address, doc, sig)
                provenance_records.append(rec)

        corroboration = aggregate_corroboration(market, signals)
        dossier = build_evidence_dossier(
            market=market,
            decomposition=decomposition,
            corroboration=corroboration,
            signals=signals,
            provenance_records=provenance_records,
        )

        written_files: list[str] = []
        if output_dir is not None:
            jpath, mpath = write_dossier_files(dossier, output_dir)
            written_files = [str(jpath), str(mpath)]

        submission_result: dict[str, Any] = {"mode": "dry_run", "submitted": False}
        if submit_live:
            if not self.delphi_client.has_api_key:
                submission_result = {
                    "mode": "skipped",
                    "submitted": False,
                    "reason": "DELPHI_API_ACCESS_KEY not configured",
                }
            elif not corroboration.passes_submission_threshold:
                submission_result = {
                    "mode": "skipped",
                    "submitted": False,
                    "reason": "Corroboration/delta threshold not met; avoiding low-value submission",
                }
            elif self.ledger.has_submitted_fingerprint(market.address, dossier.submission_note):
                submission_result = {
                    "mode": "skipped",
                    "submitted": False,
                    "reason": "Duplicate note fingerprint already submitted for this market",
                }
            else:
                try:
                    resp = self.delphi_client.submit_evidence(
                        market_address=market.address,
                        note=dossier.submission_note,
                        links=dossier.submission_links,
                    )
                    sub_id = resp.get("id")
                    status = str(resp.get("status", "queued"))
                    self.ledger.record_submission(
                        idempotency_key=f"sub-{market.address[:10]}-{sub_id or 'live'}",
                        market_address=market.address,
                        note=dossier.submission_note,
                        links=dossier.submission_links,
                        status=status,
                        response=resp,
                        submission_id=sub_id,
                    )
                    submission_result = {"mode": "live", "submitted": True, "response": resp}
                except DelphiAPIError as exc:
                    submission_result = {
                        "mode": "error",
                        "submitted": False,
                        "status_code": exc.status_code,
                        "code": exc.code,
                        "message": exc.message,
                    }

        return {
            "dossier": dossier,
            "fetch_errors": fetch_errors,
            "written_files": written_files,
            "submission": submission_result,
        }
