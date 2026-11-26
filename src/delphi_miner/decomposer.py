"""Claim decomposition engine for forecasting markets."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from delphi_miner.models import ClaimDecomposition, MarketSpec, SubClaim, ThresholdSpec

_STOPWORDS = {
    "Will", "The", "This", "That", "In", "On", "At", "By", "Of", "For", "To", "From",
    "With", "And", "Or", "If", "Be", "Is", "Are", "Was", "Were", "Yes", "No",
    "Wins", "Resolution", "Clarifications", "Corrected", "Published", "Strictly",
    "Before", "After", "Between", "Official", "Preliminary", "Provisional", "Federal",
    "Act", "Annual", "Rate", "General", "Election", "Nov", "Dec", "Jan", "Feb", "Mar",
    "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "November", "December", "January",
}

_ISO_TS_RE = re.compile(r"\b(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)\b")
_DRAFT_RE = re.compile(r"\b(draft-[a-z0-9-]+|RFC\s*\d+|CVE-\d{4}-\d+|arXiv:\d{4}\.\d+)\b", re.I)


def extract_thresholds(text: str) -> list[ThresholdSpec]:
    """Extract numeric thresholds and comparison operators from market text."""
    specs: list[ThresholdSpec] = []
    seen: set[tuple[str, float, str]] = set()

    patterns = [
        # e.g. "≥60 of 350", ">= 50.0%", "≥1.6%"
        (r"(>=|<=|≥|≤|>|<)\s*(\d+(?:\.\d+)?)\s*(%|percent|seats|bps|points|USD|USDC)?", None),
        # e.g. "2.8% or higher", "30.00% or higher", "at least 50.0%"
        (r"at least\s+(\d+(?:\.\d+)?)\s*(%|percent|seats|bps|points)?", ">="),
        (r"(\d+(?:\.\d+)?)\s*(%|percent|seats|bps|points)\s+or\s+(higher|more|greater|above)", ">="),
        (r"(\d+(?:\.\d+)?)\s*(%|percent|seats|bps|points)\s+or\s+(lower|less|fewer|below)", "<="),
        (r"(?:below|less than|fewer than|under)\s+(\d+(?:\.\d+)?)\s*(%|percent|seats|bps|points)?", "<"),
    ]

    for pat, forced_op in patterns:
        for m in re.finditer(pat, text, flags=re.I):
            if forced_op is None:
                op_raw, val_s, unit = m.group(1), m.group(2), m.group(3) or ""
                op = {">=": ">=", "≥": ">=", "<=": "<=", "≤": "<=", ">": ">", "<": "<"}[op_raw]
            else:
                op = forced_op
                val_s = m.group(1)
                unit = m.group(2) or ""
            val = float(val_s)
            unit_norm = unit.strip()
            key = (op, val, unit_norm.lower())
            if key not in seen:
                seen.add(key)
                specs.append(
                    ThresholdSpec(
                        operator=op,
                        value=val,
                        unit=unit_norm,
                        raw_text=m.group(0).strip(),
                    )
                )
    return specs


def extract_entities(question: str, criteria: str) -> list[str]:
    """Extract key named entities, tickers, and technical identifiers."""
    combined = f"{question}\n{criteria}"
    entities: list[str] = []
    seen: set[str] = set()

    def _add(item: str) -> None:
        cleaned = item.strip(" .,;:!?\"'()[]{}")
        if len(cleaned) < 2 or cleaned in _STOPWORDS:
            return
        low = cleaned.lower()
        if low not in seen:
            seen.add(low)
            entities.append(cleaned)

    # Technical IDs first (IETF drafts, CVEs, arXiv, quoted strings)
    for m in _DRAFT_RE.finditer(combined):
        _add(m.group(1))
    for m in re.finditer(r'"([^"]{3,60})"', question):
        _add(m.group(1))

    # Multi-word capitalized phrases in the question (higher signal)
    for m in re.finditer(r"\b([A-Z][a-zA-Z0-9&'-]+(?:\s+[A-Z][a-zA-Z0-9&'-]+){0,3})\b", question):
        phrase = m.group(1)
        words = [w for w in phrase.split() if w not in _STOPWORDS]
        if words:
            _add(" ".join(words))

    # Acronyms / uppercase symbols like CPI, VPI, IESG, FIDE, ARC-AGI-3, SEC
    for m in re.finditer(r"\b([A-Z]{2,}(?:-[A-Z0-9]+)*)\b", question):
        _add(m.group(1))

    return entities[:12]


def extract_time_window(criteria: str, evidence_cutoff_at: str | None) -> tuple[str | None, str | None]:
    """Extract settlement window timestamps from resolutionCriteria."""
    m_after = re.search(
        r"strictly\s+after\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)",
        criteria,
        flags=re.I,
    )
    m_before = re.search(
        r"(?:at\s+or\s+before|by)\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z)",
        criteria,
        flags=re.I,
    )
    start = m_after.group(1) if m_after else None
    end = m_before.group(1) if m_before else None
    if not start and not end:
        ts = _ISO_TS_RE.findall(criteria)
        if len(ts) >= 2:
            start, end = ts[0], ts[-1]
        elif len(ts) == 1:
            end = ts[0]
    if not end and evidence_cutoff_at:
        end = evidence_cutoff_at
    return start, end


def extract_domains(urls: list[str]) -> list[str]:
    domains: list[str] = []
    seen: set[str] = set()
    for u in urls:
        host = urlparse(u).netloc.lower()
        if host.startswith("www."):
            host = host[4:]
        if host and host not in seen:
            seen.add(host)
            domains.append(host)
    return domains


def _extract_keywords(question: str, entities: list[str]) -> list[str]:
    tokens = re.findall(r"[A-Za-z0-9][A-Za-z0-9._-]{2,}", question)
    stop_lower = {s.lower() for s in _STOPWORDS} | {
        "per", "will", "the", "and", "for", "with", "that", "this", "from", "into",
        "more", "than", "least", "higher", "lower", "any", "record", "report", "win",
        "pass", "approve", "strictly", "points", "have", "rank", "final", "announced",
    }
    kws: list[str] = []
    seen: set[str] = set()
    for ent in entities:
        for part in ent.split():
            low = part.lower()
            if len(low) >= 3 and low not in stop_lower and low not in seen:
                seen.add(low)
                kws.append(low)
    for tok in tokens:
        low = tok.lower()
        if low not in stop_lower and not low.isdigit() and low not in seen:
            seen.add(low)
            kws.append(low)
    return kws[:15]


def decompose_market(market: MarketSpec) -> ClaimDecomposition:
    """Decompose a Delphi MarketSpec into atomic verifiable SubClaims and search queries."""
    entities = extract_entities(market.question, market.resolution_criteria)
    thresholds = extract_thresholds(f"{market.question}\n{market.resolution_criteria}")
    window_start, window_end = extract_time_window(
        market.resolution_criteria, market.evidence_cutoff_at
    )
    target_domains = extract_domains(market.data_sources)
    keywords = _extract_keywords(market.question, entities)

    threshold_desc = ", ".join(t.raw_text for t in thresholds) if thresholds else "binary outcome condition"

    sub_claims: list[SubClaim] = [
        SubClaim(
            claim_id="C1-primary-outcome",
            predicate=(
                f"Primary outcome verification: determine whether evidence indicates YES or NO for "
                f"'{market.question}' (thresholds: {threshold_desc})."
            ),
            claim_type="primary_outcome",
            target_entities=entities[:6],
            keywords=keywords[:8],
            target_domains=target_domains,
            expected_direction="binary",
        ),
        SubClaim(
            claim_id="C2-official-source",
            predicate=(
                f"Designated resolution source check across {target_domains or ['public primary records']} "
                f"for official notices/releases inside window [{window_start or 'open'} .. {window_end or 'cutoff'}]."
            ),
            claim_type="official_source_check",
            target_entities=entities[:4],
            keywords=keywords[:6] + ["official", "press", "release", "result"],
            target_domains=target_domains,
            expected_direction="binary",
        ),
        SubClaim(
            claim_id="C3-leading-indicator",
            predicate=(
                f"Leading indicator / pre-release check: polls, regional/preliminary prints, "
                f"ballot/tracker status, standings, or technical commits tied to {', '.join(entities[:4]) or market.category}."
            ),
            claim_type="leading_indicator",
            target_entities=entities[:5],
            keywords=keywords[:8] + ["preliminary", "poll", "estimate", "forecast", "update"],
            target_domains=target_domains,
            expected_direction="binary",
        ),
    ]

    if "clarifications" in market.resolution_criteria.lower() or thresholds:
        sub_claims.append(
            SubClaim(
                claim_id="C4-criteria-edge-guard",
                predicate=(
                    "Resolution criteria edge-case guard: verify exact metric definition "
                    "(preliminary vs final, valid+blank ballots, single vs double majority, UTC cutoff)."
                ),
                claim_type="edge_guard",
                target_entities=entities[:3],
                keywords=keywords[:5],
                target_domains=target_domains,
                expected_direction="neutral",
            )
        )

    # Build targeted search queries for Wikipedia, arXiv, GitHub, SEC
    ent_str = " ".join(entities[:3]) if entities else " ".join(keywords[:4])
    suggested_queries = [ent_str.strip()]
    if len(keywords) >= 2:
        suggested_queries.append(" ".join(keywords[:4]))
    for m_draft in _DRAFT_RE.finditer(market.question):
        suggested_queries.insert(0, m_draft.group(1))
    # Deduplicate preserving order
    dedup_queries: list[str] = []
    for q in suggested_queries:
        if q and q not in dedup_queries:
            dedup_queries.append(q)

    return ClaimDecomposition(
        market_address=market.address,
        question=market.question,
        entities=entities,
        thresholds=thresholds,
        window_start=window_start,
        window_end=window_end,
        evidence_cutoff_at=market.evidence_cutoff_at,
        sub_claims=sub_claims,
        suggested_queries=dedup_queries,
        target_domains=target_domains,
    )
