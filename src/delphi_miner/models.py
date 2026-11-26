"""Data models for Delphi Evidence Miner."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format with Z suffix."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


class Stance(str, Enum):
    """Directional stance of an evidence signal with respect to market resolution."""

    SUPPORT_YES = "SUPPORT_YES"
    SUPPORT_NO = "SUPPORT_NO"
    NEUTRAL_CONTEXT = "NEUTRAL_CONTEXT"


class MarketOdds(BaseModel):
    yes: float = Field(ge=0.0, le=1.0)
    no: float = Field(ge=0.0, le=1.0)


class MarketPots(BaseModel):
    """Pots are strings in USDC base units (6 decimals) in competition markets.json."""

    judgement: str = "0"
    settlement: str = "0"

    @property
    def total_usdc(self) -> float:
        return (int(self.judgement) + int(self.settlement)) / 1_000_000.0

    @property
    def judgement_usdc(self) -> float:
        return int(self.judgement) / 1_000_000.0

    @property
    def settlement_usdc(self) -> float:
        return int(self.settlement) / 1_000_000.0


class MarketSpec(BaseModel):
    """Normalized representation of a Delphi prediction market."""

    address: str
    question: str
    delphi_url: str = ""
    category: str = "general"
    category_label: str = "General"
    market_tier: str = "A"
    wave: int = 1
    status: str = "open"
    odds: MarketOdds = Field(default_factory=lambda: MarketOdds(yes=0.5, no=0.5))
    closes_at: str | None = None
    evidence_cutoff_at: str | None = None
    pots: MarketPots = Field(default_factory=MarketPots)
    resolution_criteria: str = ""
    data_sources: list[str] = Field(default_factory=list)
    settled: Any = None

    @classmethod
    def from_delphi_json(cls, raw: dict[str, Any]) -> MarketSpec:
        odds_raw = raw.get("odds") or {"yes": 0.5, "no": 0.5}
        pots_raw = raw.get("pots") or {"judgement": "0", "settlement": "0"}
        return cls(
            address=raw["address"],
            question=raw["question"],
            delphi_url=raw.get("delphiUrl", ""),
            category=raw.get("category", "general"),
            category_label=raw.get("categoryLabel", raw.get("category", "General")),
            market_tier=raw.get("marketTier", "A"),
            wave=raw.get("wave", 1),
            status=raw.get("status", "open"),
            odds=MarketOdds(**odds_raw),
            closes_at=raw.get("closesAt"),
            evidence_cutoff_at=raw.get("evidenceCutoffAt"),
            pots=MarketPots(**pots_raw),
            resolution_criteria=raw.get("resolutionCriteria", ""),
            data_sources=list(raw.get("dataSources") or []),
            settled=raw.get("settled"),
        )


class ThresholdSpec(BaseModel):
    """Structured numeric comparison extracted from a market question or criteria."""

    operator: str  # ">=", ">", "<=", "<", "=="
    value: float
    unit: str = ""
    raw_text: str = ""


class SubClaim(BaseModel):
    """Atomic verifiable predicate decomposed from a market."""

    claim_id: str
    predicate: str
    claim_type: str  # "primary_outcome", "official_source_check", "leading_indicator", "edge_guard"
    target_entities: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    target_domains: list[str] = Field(default_factory=list)
    expected_direction: str = "binary"


class ClaimDecomposition(BaseModel):
    """Full decomposition of a prediction market into searchable sub-claims."""

    market_address: str
    question: str
    entities: list[str] = Field(default_factory=list)
    thresholds: list[ThresholdSpec] = Field(default_factory=list)
    window_start: str | None = None
    window_end: str | None = None
    evidence_cutoff_at: str | None = None
    sub_claims: list[SubClaim] = Field(default_factory=list)
    suggested_queries: list[str] = Field(default_factory=list)
    target_domains: list[str] = Field(default_factory=list)


class SourceDocument(BaseModel):
    """Fetched public document with cryptographic provenance metadata."""

    url: str
    normalized_url: str
    domain: str
    source_type: str  # rss, atom, sec_edgar, arxiv, github, wikipedia, web_html, web_json, pdf
    title: str = ""
    published_at: str | None = None
    fetched_at: str = Field(default_factory=utc_now_iso)
    http_status: int = 200
    content_sha256: str
    raw_byte_len: int = 0
    text_content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceSignal(BaseModel):
    """Scored signal linking a fetched SourceDocument to a SubClaim."""

    claim_id: str
    source_url: str
    domain: str
    source_type: str
    title: str
    published_at: str | None = None
    fetched_at: str
    content_sha256: str
    stance: Stance
    relevance_score: float = Field(ge=0.0, le=1.0)
    authority_weight: float = Field(ge=0.0, le=1.0)
    recency_score: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    matched_terms: list[str] = Field(default_factory=list)
    extracted_numbers: list[float] = Field(default_factory=list)
    excerpt: str = ""
    rationale: str = ""


class CorroborationReport(BaseModel):
    """Aggregated multi-source corroboration & contradiction scoring for a market."""

    market_address: str
    prior_yes_odds: float = Field(ge=0.0, le=1.0)
    implied_yes_probability: float = Field(ge=0.0, le=1.0)
    delta_from_market: float = Field(ge=0.0, le=1.0)
    recommended_stance: Stance
    corroboration_score: float = Field(ge=0.0, le=1.0)
    contradiction_index: float = Field(ge=0.0, le=1.0)
    supporting_yes_weight: float = 0.0
    supporting_no_weight: float = 0.0
    neutral_weight: float = 0.0
    distinct_domains_count: int = 0
    total_signals: int = 0
    passes_submission_threshold: bool = False
    assessment_summary: str = ""


class ProvenanceRecord(BaseModel):
    """Immutable citation ledger entry verifying source fetch + claim linkage."""

    record_id: int | None = None
    market_address: str
    claim_id: str
    source_url: str
    domain: str
    source_type: str
    http_status: int
    content_sha256: str
    published_at: str | None = None
    fetched_at: str
    stance: Stance
    confidence: float
    excerpt: str
    record_hash: str


class EvidenceDossier(BaseModel):
    """Complete structured evidence dossier ready for Delphi submission or review."""

    generated_at: str = Field(default_factory=utc_now_iso)
    market: MarketSpec
    decomposition: ClaimDecomposition
    corroboration: CorroborationReport
    signals: list[EvidenceSignal] = Field(default_factory=list)
    provenance_records: list[ProvenanceRecord] = Field(default_factory=list)
    submission_note: str = ""
    submission_links: list[str] = Field(default_factory=list)
    submission_payload_valid: bool = False
    dry_run_submission: dict[str, Any] = Field(default_factory=dict)
