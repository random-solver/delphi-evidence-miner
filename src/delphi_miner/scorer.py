"""Multi-source corroboration, contradiction, and information-gain scoring."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone

from delphi_miner.models import (
    ClaimDecomposition,
    CorroborationReport,
    EvidenceSignal,
    MarketSpec,
    SourceDocument,
    Stance,
    ThresholdSpec,
)

_HIGH_AUTHORITY_DOMAINS = (
    ".gov",
    ".gob.es",
    ".admin.ch",
    "ietf.org",
    "sec.gov",
    "destatis.de",
    "bfs.admin.ch",
    "tuik.gov.tr",
    "tcmb.gov.tr",
    "fide.com",
    "arxiv.org",
    "opendata.swiss",
    "arcprize.org",
)

_MEDIUM_AUTHORITY_DOMAINS = (
    "github.com",
    "wikipedia.org",
    "reuters.com",
    "apnews.com",
    "srf.ch",
    "swissinfo.ch",
    "kaggle.com",
    "yna.co.kr",
    "koreajoongangdaily.joins.com",
    "chess.com",
)

_POSITIVE_CUES = {
    "approved", "passed", "confirmed", "exceeded", "surpassed", "above", "higher",
    "recorded", "filed", "adopted", "won", "leads", "ahead", "ranked 1st", "first place",
    "majority", "enacted", "rose", "increased", "accelerated",
}

_NEGATIVE_CUES = {
    "rejected", "failed", "defeated", "below", "under", "lower", "fewer", "blocked",
    "withdrawn", "declined", "fell", "decreased", "trails", "behind", "lost", "dropped",
    "no discuss", "without discuss", "did not pass", "not approved", "not met",
}


def compute_authority_weight(domain: str, target_domains: list[str]) -> float:
    """Assign source authority weight in [0.2, 1.0]."""
    d = domain.lower()
    for td in target_domains:
        td_low = td.lower()
        if d == td_low or d.endswith("." + td_low):
            return 1.0
    if any(d == pat or d.endswith(pat) for pat in _HIGH_AUTHORITY_DOMAINS):
        return 0.90
    if any(d == pat or d.endswith("." + pat) for pat in _MEDIUM_AUTHORITY_DOMAINS):
        return 0.75
    return 0.45


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def compute_recency_score(
    published_at: str | None,
    window_start: str | None,
    cutoff_at: str | None,
) -> float:
    """Score temporal freshness relative to market window and evidence cutoff."""
    pub = _parse_iso(published_at)
    if pub is None:
        return 0.60
    cutoff = _parse_iso(cutoff_at)
    if cutoff is not None and pub > cutoff:
        return 0.15
    start = _parse_iso(window_start)
    if start is not None and pub >= start:
        return 1.0
    age_days = abs((datetime.now(timezone.utc) - pub).total_seconds()) / 86400.0
    if age_days <= 3.0:
        return 0.90
    if age_days <= 14.0:
        return 0.75
    if age_days <= 45.0:
        return 0.55
    return 0.40


def _extract_best_excerpt(text: str, terms: list[str], max_len: int = 380) -> str:
    """Locate the highest-density sentence/window matching query terms."""
    if not text.strip():
        return ""
    sentences = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    best_sent = sentences[0]
    best_score = -1
    lower_terms = [t.lower() for t in terms if len(t) >= 3]
    for sent in sentences[:120]:
        s_clean = re.sub(r"\s+", " ", sent).strip()
        if len(s_clean) < 20:
            continue
        low = s_clean.lower()
        hits = sum(1 for t in lower_terms if t in low)
        has_num = 1 if re.search(r"\d+(?:\.\d+)?%?", s_clean) else 0
        score = hits * 3 + has_num
        if score > best_score:
            best_score = score
            best_sent = s_clean
    best_sent = re.sub(r"\s+", " ", best_sent).strip()
    return best_sent[:max_len] + ("..." if len(best_sent) > max_len else "")


def _eval_threshold(num: float, th: ThresholdSpec) -> bool:
    if th.operator == ">=":
        return num >= th.value
    if th.operator == ">":
        return num > th.value
    if th.operator == "<=":
        return num <= th.value
    if th.operator == "<":
        return num < th.value
    if th.operator == "==":
        return abs(num - th.value) < 1e-6
    return False


def _extract_relevant_numbers(text: str, thresholds: list[ThresholdSpec]) -> list[float]:
    """Extract numeric values matching threshold units (e.g. %, seats) if present."""
    nums: list[float] = []
    units = {t.unit.lower() for t in thresholds if t.unit}
    if "%" in units or "percent" in units:
        for m in re.finditer(r"(-?\d+(?:\.\d+)?)\s*(?:%|percent)\b", text, flags=re.I):
            nums.append(float(m.group(1)))
        return nums[:10]
    if "seats" in units:
        for m in re.finditer(r"(\d+)\s+seats\b", text, flags=re.I):
            nums.append(float(m.group(1)))
        return nums[:10]
    if thresholds:
        for m in re.finditer(r"\b(\d+(?:\.\d+)?)\b", text):
            v = float(m.group(1))
            if 0.0 <= v <= 1000.0 and v not in {2025.0, 2026.0, 2027.0}:
                nums.append(v)
    return nums[:10]


def classify_stance(
    excerpt: str,
    full_text: str,
    thresholds: list[ThresholdSpec],
    relevance_score: float,
) -> tuple[Stance, float, str]:
    """Classify directional stance (SUPPORT_YES / SUPPORT_NO / NEUTRAL_CONTEXT) with rationale."""
    if relevance_score < 0.30:
        return Stance.NEUTRAL_CONTEXT, 0.0, "Low term overlap with market entities/keywords."

    sample = f"{excerpt}\n{full_text[:1500]}".lower()
    yes_votes = 0.0
    no_votes = 0.0
    reasons: list[str] = []

    # 1. Quantitative threshold comparison on matched excerpt
    nums = _extract_relevant_numbers(excerpt, thresholds)
    if thresholds and nums:
        th = thresholds[0]
        # Avoid years like 2026 or trivial 100% unless plausible
        plausible = [n for n in nums if not (1990 <= n <= 2035 and th.unit != "seats")]
        if plausible:
            val = plausible[0]
            if _eval_threshold(val, th):
                yes_votes += 2.0
                reasons.append(f"Observed value {val} satisfies threshold {th.operator} {th.value}{th.unit}")
            else:
                no_votes += 2.0
                reasons.append(f"Observed value {val} is below/opposite threshold {th.operator} {th.value}{th.unit}")

    # 2. Lexical cue polarity (negation-aware)
    for cue in _NEGATIVE_CUES:
        if cue in sample:
            no_votes += 1.0
            reasons.append(f"negative cue '{cue}'")
    for cue in _POSITIVE_CUES:
        if cue in sample:
            # Skip if preceded closely by negation
            if re.search(rf"\b(?:not|no|never|without)\s+(?:\w+\s+){{0,2}}{re.escape(cue)}", sample):
                no_votes += 1.0
                reasons.append(f"negated '{cue}'")
            else:
                yes_votes += 1.0
                reasons.append(f"positive cue '{cue}'")

    margin = abs(yes_votes - no_votes)
    if margin < 0.75 or (yes_votes == 0 and no_votes == 0):
        return (
            Stance.NEUTRAL_CONTEXT,
            round(0.35 * relevance_score, 4),
            "Informational context without decisive directional threshold or polarity signal.",
        )

    strength = min(1.0, 0.45 + 0.15 * margin)
    if yes_votes > no_votes:
        return Stance.SUPPORT_YES, round(strength, 4), "; ".join(reasons[:3])
    return Stance.SUPPORT_NO, round(strength, 4), "; ".join(reasons[:3])


def score_document(
    doc: SourceDocument,
    decomposition: ClaimDecomposition,
) -> EvidenceSignal:
    """Score a single SourceDocument against a ClaimDecomposition."""
    all_terms = list(dict.fromkeys(decomposition.entities + [kw for sc in decomposition.sub_claims for kw in sc.keywords]))
    title_low = doc.title.lower()
    body_low = doc.text_content.lower()

    matched: list[str] = []
    weighted_hits = 0.0
    for term in all_terms:
        t_low = term.lower()
        if len(t_low) < 3:
            continue
        in_title = t_low in title_low
        in_body = t_low in body_low
        if in_title or in_body:
            matched.append(term)
            weighted_hits += 2.0 if in_title else 1.0

    denom = max(3.0, min(10.0, float(len(all_terms))))
    relevance = min(1.0, weighted_hits / denom)

    authority = compute_authority_weight(doc.domain, decomposition.target_domains)
    recency = compute_recency_score(
        doc.published_at,
        decomposition.window_start,
        decomposition.evidence_cutoff_at,
    )

    excerpt = _extract_best_excerpt(doc.text_content, matched or all_terms)
    stance, polarity_strength, rationale = classify_stance(
        excerpt=excerpt,
        full_text=doc.text_content,
        thresholds=decomposition.thresholds,
        relevance_score=relevance,
    )

    # Pick best matching sub-claim
    claim_id = "C1-primary-outcome"
    if authority >= 0.99:
        claim_id = "C2-official-source"
    elif doc.source_type in {"arxiv", "github", "wikipedia", "rss", "atom"}:
        claim_id = "C3-leading-indicator"

    if stance == Stance.NEUTRAL_CONTEXT:
        confidence = round(0.40 * relevance * authority * recency, 4)
    else:
        confidence = round(
            min(0.99, (0.45 * relevance + 0.30 * authority + 0.25 * recency) * (0.65 + 0.35 * polarity_strength)),
            4,
        )

    extracted_nums = _extract_relevant_numbers(excerpt, decomposition.thresholds)

    return EvidenceSignal(
        claim_id=claim_id,
        source_url=doc.url,
        domain=doc.domain,
        source_type=doc.source_type,
        title=doc.title,
        published_at=doc.published_at,
        fetched_at=doc.fetched_at,
        content_sha256=doc.content_sha256,
        stance=stance,
        relevance_score=round(relevance, 4),
        authority_weight=round(authority, 4),
        recency_score=round(recency, 4),
        confidence=confidence,
        matched_terms=matched[:10],
        extracted_numbers=extracted_nums,
        excerpt=excerpt,
        rationale=rationale,
    )


def _logit(p: float) -> float:
    p_clamped = min(0.98, max(0.02, p))
    return math.log(p_clamped / (1.0 - p_clamped))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def aggregate_corroboration(
    market: MarketSpec,
    signals: list[EvidenceSignal],
) -> CorroborationReport:
    """Aggregate multi-source signals into corroboration, contradiction, and Bayesian delta."""
    # Deduplicate per (domain, stance) keeping highest confidence so 1 site can't spam weight
    domain_stance_best: dict[tuple[str, Stance], float] = {}
    distinct_domains: set[str] = set()
    for sig in signals:
        if sig.relevance_score < 0.15:
            continue
        distinct_domains.add(sig.domain)
        key = (sig.domain, sig.stance)
        prev = domain_stance_best.get(key, 0.0)
        if sig.confidence > prev:
            domain_stance_best[key] = sig.confidence

    yes_w = sum(w for (_, st), w in domain_stance_best.items() if st == Stance.SUPPORT_YES)
    no_w = sum(w for (_, st), w in domain_stance_best.items() if st == Stance.SUPPORT_NO)
    neu_w = sum(w for (_, st), w in domain_stance_best.items() if st == Stance.NEUTRAL_CONTEXT)

    dir_total = yes_w + no_w
    if dir_total > 0:
        contradiction_index = (2.0 * min(yes_w, no_w)) / dir_total
        majority_ratio = max(yes_w, no_w) / dir_total
        majority_stance = Stance.SUPPORT_YES if yes_w >= no_w else Stance.SUPPORT_NO
        majority_domains = sum(
            1 for (_, st) in domain_stance_best if st == majority_stance
        )
        diversity_factor = min(1.0, (majority_domains + 0.5) / 3.0)
        corroboration_score = majority_ratio * diversity_factor
    else:
        contradiction_index = 0.0
        corroboration_score = 0.0
        majority_stance = Stance.NEUTRAL_CONTEXT

    prior = market.odds.yes
    # Shrunk Bayesian log-odds shift damped by contradiction
    shift = 1.15 * (yes_w - no_w) * (1.0 - 0.5 * contradiction_index)
    implied = min(0.98, max(0.02, _sigmoid(_logit(prior) + shift)))
    delta = abs(implied - prior)

    max_dir_conf = max(
        (s.confidence for s in signals if s.stance != Stance.NEUTRAL_CONTEXT),
        default=0.0,
    )
    passes = bool(
        dir_total > 0
        and max_dir_conf >= 0.45
        and delta >= 0.03
        and contradiction_index < 0.85
    )

    if dir_total == 0:
        summary = (
            f"Fetched {len(signals)} signal(s) across {len(distinct_domains)} domain(s); "
            "no decisive directional shift over market baseline yet."
        )
    else:
        summary = (
            f"Recommended {majority_stance.value}: YES_w={yes_w:.2f}, NO_w={no_w:.2f}, "
            f"corroboration={corroboration_score:.2f}, contradiction={contradiction_index:.2f}, "
            f"prior={prior:.3f} -> implied={implied:.3f} (delta={delta:.3f})."
        )

    return CorroborationReport(
        market_address=market.address,
        prior_yes_odds=round(prior, 6),
        implied_yes_probability=round(implied, 6),
        delta_from_market=round(delta, 6),
        recommended_stance=majority_stance,
        corroboration_score=round(corroboration_score, 4),
        contradiction_index=round(contradiction_index, 4),
        supporting_yes_weight=round(yes_w, 4),
        supporting_no_weight=round(no_w, 4),
        neutral_weight=round(neu_w, 4),
        distinct_domains_count=len(distinct_domains),
        total_signals=len(signals),
        passes_submission_threshold=passes,
        assessment_summary=summary,
    )
