"""Tests for claim decomposition engine."""

from delphi_miner.decomposer import (
    decompose_market,
    extract_entities,
    extract_thresholds,
    extract_time_window,
)
from delphi_miner.models import MarketSpec


def test_extract_thresholds_various_formats() -> None:
    text = (
        "Will Destatis's preliminary German CPI (VPI) annual rate be 2.8% or higher? "
        "Also ≥60 of 350 seats and at least 50.0% turnout, or below 1.6%."
    )
    thresholds = extract_thresholds(text)
    ops_vals = {(t.operator, t.value, t.unit.lower()) for t in thresholds}
    assert (">=", 2.8, "%") in ops_vals
    assert (">=", 60.0, "") in ops_vals or any(t.value == 60.0 and t.operator == ">=" for t in thresholds)
    assert (">=", 50.0, "%") in ops_vals
    assert ("<", 1.6, "%") in ops_vals


def test_extract_time_window_strictly_after_and_before() -> None:
    criteria = (
        "published strictly after 2026-11-24T09:06:27.769Z "
        "and at or before 2026-11-29T19:00:00.000Z."
    )
    start, end = extract_time_window(criteria, "2026-11-28T06:00:00.000Z")
    assert start == "2026-11-24T09:06:27.769Z"
    assert end == "2026-11-29T19:00:00.000Z"


def test_decompose_ietf_market_produces_subclaims_and_queries() -> None:
    market = MarketSpec(
        address="0xabc123",
        question="Will any IESG member record a DISCUSS on draft-ietf-mpls-mna-ioam in the IETF Datatracker by 2026-12-03 23:59 UTC?",
        category="releases_standards",
        evidence_cutoff_at="2026-12-02T12:00:00.000Z",
        resolution_criteria="Clarifications: Ballot record on IETF Datatracker.",
        data_sources=["https://datatracker.ietf.org/doc/draft-ietf-mpls-mna-ioam/"],
    )
    decomp = decompose_market(market)
    assert "draft-ietf-mpls-mna-ioam" in decomp.entities
    assert "datatracker.ietf.org" in decomp.target_domains
    assert len(decomp.sub_claims) == 4
    assert any("draft-ietf-mpls-mna-ioam" in q for q in decomp.suggested_queries)
    entities = extract_entities(market.question, market.resolution_criteria)
    assert "IESG" in entities
