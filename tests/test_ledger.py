"""Tests for SQLite verifiable citation provenance ledger."""

import sqlite3
from pathlib import Path

from delphi_miner.ledger import ProvenanceLedger, compute_sha256
from delphi_miner.models import EvidenceSignal, SourceDocument, Stance


def _sample_doc_and_signal() -> tuple[SourceDocument, EvidenceSignal]:
    body = "Official Swiss Federal Chancellery bulletin: turnout was 51.8%."
    sha = compute_sha256(body)
    doc = SourceDocument(
        url="https://www.bk.admin.ch/vote-2026",
        normalized_url="https://www.bk.admin.ch/vote-2026",
        domain="bk.admin.ch",
        source_type="web_html",
        title="Swiss Turnout Bulletin",
        published_at="2026-11-26T10:00:00Z",
        fetched_at="2026-11-26T12:00:00Z",
        http_status=200,
        content_sha256=sha,
        raw_byte_len=len(body),
        text_content=body,
    )
    sig = EvidenceSignal(
        claim_id="C2-official-source",
        source_url=doc.url,
        domain=doc.domain,
        source_type=doc.source_type,
        title=doc.title,
        published_at=doc.published_at,
        fetched_at=doc.fetched_at,
        content_sha256=sha,
        stance=Stance.SUPPORT_YES,
        relevance_score=0.90,
        authority_weight=1.0,
        recency_score=1.0,
        confidence=0.92,
        excerpt="turnout was 51.8%",
        rationale="Observed 51.8 >= 50.0%",
    )
    return doc, sig


def test_ledger_records_verifies_and_detects_tampering(tmp_path: Path) -> None:
    db_path = tmp_path / "ledger.db"
    ledger = ProvenanceLedger(db_path)
    doc, sig = _sample_doc_and_signal()

    rec1 = ledger.record_signal("0xMarketA", doc, sig)
    rec2 = ledger.record_signal("0xMarketA", doc, sig)  # idempotent duplicate
    assert rec1.record_hash == rec2.record_hash

    report = ledger.verify_integrity()
    assert report["verified"] is True
    assert report["total_sources"] == 1
    assert report["total_claims"] == 1
    assert report["anomalies"] == []

    # Tamper with excerpt directly in SQLite and verify detection
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("UPDATE evidence_claims SET excerpt = 'tampered text' WHERE id = ?", (rec1.record_id,))

    tampered_report = ledger.verify_integrity()
    assert tampered_report["verified"] is False
    assert len(tampered_report["anomalies"]) == 1
    assert "hash mismatch" in tampered_report["anomalies"][0]


def test_ledger_submission_fingerprint_dedup(tmp_path: Path) -> None:
    ledger = ProvenanceLedger(tmp_path / "ledger.db")
    note = "Market: Test\nFact: 51.8% turnout."
    assert ledger.has_submitted_fingerprint("0xMarketA", note) is False
    ledger.record_submission(
        idempotency_key="idem-1",
        market_address="0xMarketA",
        note=note,
        links=["https://www.bk.admin.ch/vote-2026"],
        status="queued",
        response={"id": "sub-1", "status": "queued"},
        submission_id="sub-1",
    )
    assert ledger.has_submitted_fingerprint("0xMarketA", note) is True
    assert ledger.has_submitted_fingerprint("0xMarketB", note) is False
