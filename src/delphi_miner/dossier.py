"""Structured JSON and Markdown dossier + Delphi note builder."""

from __future__ import annotations

from pathlib import Path

from delphi_miner.delphi_client import MAX_NOTE_CHARS, build_submission_packet
from delphi_miner.models import (
    ClaimDecomposition,
    CorroborationReport,
    EvidenceDossier,
    EvidenceSignal,
    MarketSpec,
    ProvenanceRecord,
)


def format_delphi_submission_note(
    market: MarketSpec,
    corroboration: CorroborationReport,
    signals: list[EvidenceSignal],
    max_links: int = 2,
) -> tuple[str, list[str]]:
    """
    Build a concise, high-signal Delphi submission note (<= 8000 chars) and up to 2 links
    so total items = 1 (note) + len(links) <= 3 (Delphi API constraint).
    """
    ranked = sorted(
        [s for s in signals if s.relevance_score >= 0.15],
        key=lambda s: (s.confidence, s.authority_weight),
        reverse=True,
    )
    selected_links: list[str] = []
    seen_urls: set[str] = set()
    for s in ranked:
        if s.source_url not in seen_urls and len(selected_links) < max_links:
            seen_urls.add(s.source_url)
            selected_links.append(s.source_url)

    lines = [
        f"Market: {market.question}",
        f"Assessment: {corroboration.recommended_stance.value} | Prior YES={corroboration.prior_yes_odds:.1%} -> Implied YES={corroboration.implied_yes_probability:.1%} (Corroboration={corroboration.corroboration_score:.2f}, Contradiction={corroboration.contradiction_index:.2f})",
        "",
        "Dated Verifiable Evidence:",
    ]
    for idx, sig in enumerate(ranked[:5], start=1):
        pub_str = sig.published_at or f"fetched {sig.fetched_at}"
        lines.append(
            f"{idx}. [{sig.stance.value} | conf={sig.confidence:.2f} | {pub_str}] {sig.title}"
        )
        lines.append(f"   Excerpt: {sig.excerpt}")
        lines.append(f"   Source: {sig.source_url} (sha256:{sig.content_sha256[:16]})")

    note = "\n".join(lines).strip()
    if len(note) > MAX_NOTE_CHARS:
        note = note[: MAX_NOTE_CHARS - 3].rstrip() + "..."
    return note, selected_links


def build_evidence_dossier(
    market: MarketSpec,
    decomposition: ClaimDecomposition,
    corroboration: CorroborationReport,
    signals: list[EvidenceSignal],
    provenance_records: list[ProvenanceRecord],
) -> EvidenceDossier:
    """Assemble complete EvidenceDossier including validated dry-run Delphi submission packet."""
    note, links = format_delphi_submission_note(market, corroboration, signals, max_links=2)
    try:
        dry_run = build_submission_packet(
            market_address=market.address,
            note=note,
            links=links,
        )
        valid = True
    except ValueError as exc:
        dry_run = {"error": str(exc)}
        valid = False

    return EvidenceDossier(
        market=market,
        decomposition=decomposition,
        corroboration=corroboration,
        signals=signals,
        provenance_records=provenance_records,
        submission_note=note,
        submission_links=links,
        submission_payload_valid=valid,
        dry_run_submission=dry_run,
    )


def render_dossier_markdown(dossier: EvidenceDossier) -> str:
    """Render an EvidenceDossier into a human-auditable Markdown report."""
    m = dossier.market
    c = dossier.corroboration
    d = dossier.decomposition

    md: list[str] = [
        f"# Evidence Dossier — {m.question}",
        "",
        f"- **Market Address:** `{m.address}`",
        f"- **Delphi URL:** {m.delphi_url or 'N/A'}",
        f"- **Category:** {m.category_label} (`{m.category}`)",
        f"- **Current Odds:** YES `{m.odds.yes:.2%}` / NO `{m.odds.no:.2%}`",
        f"- **Reward Pots:** Judgment `${m.pots.judgement_usdc:.2f}` + Settlement `${m.pots.settlement_usdc:.2f}` USDC",
        f"- **Evidence Cutoff:** `{m.evidence_cutoff_at or 'N/A'}` | **Closes:** `{m.closes_at or 'N/A'}`",
        f"- **Generated At:** `{dossier.generated_at}`",
        "",
        "## 1. Claim Decomposition",
        f"- **Entities:** {', '.join(d.entities) if d.entities else 'None'}",
        f"- **Thresholds:** {', '.join(t.raw_text for t in d.thresholds) if d.thresholds else 'None'}",
        f"- **Resolution Window:** `{d.window_start or 'open'}` .. `{d.window_end or 'cutoff'}`",
        f"- **Target Official Domains:** {', '.join(d.target_domains) if d.target_domains else 'None'}",
        "",
        "### Sub-Claims",
    ]
    for sc in d.sub_claims:
        md.append(f"- **`{sc.claim_id}`** ({sc.claim_type}): {sc.predicate}")

    md.extend(
        [
            "",
            "## 2. Multi-Source Corroboration & Contradiction Analysis",
            f"- **Recommended Stance:** `{c.recommended_stance.value}`",
            f"- **Prior YES Odds:** `{c.prior_yes_odds:.2%}` -> **Implied YES Probability:** `{c.implied_yes_probability:.2%}` (**Delta:** `{c.delta_from_market:.2%}`)",
            f"- **Corroboration Score:** `{c.corroboration_score:.4f}` | **Contradiction Index:** `{c.contradiction_index:.4f}`",
            f"- **Domain Weights:** YES=`{c.supporting_yes_weight:.4f}` | NO=`{c.supporting_no_weight:.4f}` | NEUTRAL=`{c.neutral_weight:.4f}` across `{c.distinct_domains_count}` distinct domain(s)",
            f"- **Passes Submission Gate:** `{c.passes_submission_threshold}`",
            f"- **Summary:** {c.assessment_summary}",
            "",
            "## 3. Scored Evidence Signals & Citation Provenance",
        ]
    )

    if not dossier.signals:
        md.append("_No evidence signals fetched._")
    else:
        for idx, sig in enumerate(dossier.signals, start=1):
            md.extend(
                [
                    f"### Signal {idx}: {sig.title}",
                    f"- **Claim:** `{sig.claim_id}` | **Stance:** `{sig.stance.value}` | **Confidence:** `{sig.confidence:.4f}`",
                    f"- **Source:** {sig.source_url} (`{sig.source_type}`, domain `{sig.domain}`)",
                    f"- **Published:** `{sig.published_at or 'unknown'}` | **Fetched:** `{sig.fetched_at}`",
                    f"- **Content SHA-256:** `{sig.content_sha256}`",
                    f"- **Metrics:** relevance=`{sig.relevance_score:.3f}`, authority=`{sig.authority_weight:.3f}`, recency=`{sig.recency_score:.3f}`",
                    f"- **Rationale:** {sig.rationale}",
                    f"- **Excerpt:** > {sig.excerpt}",
                    "",
                ]
            )

    md.extend(
        [
            "## 4. Provenance Ledger Verification Hashes",
        ]
    )
    for rec in dossier.provenance_records:
        md.append(
            f"- `record#{rec.record_id}` `{rec.claim_id}` -> `{rec.record_hash}` (source sha256 `{rec.content_sha256[:16]}...`)"
        )

    md.extend(
        [
            "",
            "## 5. Delphi Submission Packet Preview",
            f"- **Packet Valid (<=3 items, <=8000 chars):** `{dossier.submission_payload_valid}`",
            f"- **Links ({len(dossier.submission_links)}):** {', '.join(dossier.submission_links) if dossier.submission_links else 'None'}",
            "",
            "```text",
            dossier.submission_note,
            "```",
            "",
        ]
    )
    return "\n".join(md)


def write_dossier_files(dossier: EvidenceDossier, output_dir: str | Path) -> tuple[Path, Path]:
    """Write JSON and Markdown dossier files to output_dir and return their paths."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    slug = dossier.market.address[:12].lower()
    json_path = out / f"dossier_{slug}.json"
    md_path = out / f"dossier_{slug}.md"
    json_path.write_text(dossier.model_dump_json(indent=2), encoding="utf-8")
    md_path.write_text(render_dossier_markdown(dossier), encoding="utf-8")
    return json_path, md_path
