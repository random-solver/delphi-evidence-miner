"""SQLite-backed verifiable citation provenance ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from delphi_miner.models import (
    EvidenceSignal,
    ProvenanceRecord,
    SourceDocument,
    Stance,
    utc_now_iso,
)


def compute_sha256(data: bytes | str) -> str:
    """Compute hex SHA-256 digest of bytes or UTF-8 string."""
    raw = data.encode("utf-8") if isinstance(data, str) else data
    return hashlib.sha256(raw).hexdigest()


def compute_record_hash(
    market_address: str,
    claim_id: str,
    content_sha256: str,
    source_url: str,
    excerpt: str,
    fetched_at: str,
) -> str:
    """Compute deterministic provenance record hash linking claim, source, and timestamp."""
    payload = "|".join(
        [
            market_address.strip().lower(),
            claim_id.strip(),
            content_sha256.strip().lower(),
            source_url.strip(),
            excerpt.strip(),
            fetched_at.strip(),
        ]
    )
    return compute_sha256(payload)


class ProvenanceLedger:
    """Immutable audit ledger for fetched sources, claim citations, and Delphi submissions."""

    def __init__(self, db_path: str | Path = "delphi_ledger.db") -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON;")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS fetched_sources (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    normalized_url TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    http_status INTEGER NOT NULL,
                    content_sha256 TEXT NOT NULL,
                    raw_byte_len INTEGER NOT NULL,
                    published_at TEXT,
                    fetched_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    excerpt TEXT NOT NULL,
                    UNIQUE(normalized_url, content_sha256)
                );

                CREATE TABLE IF NOT EXISTS evidence_claims (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    market_address TEXT NOT NULL,
                    claim_id TEXT NOT NULL,
                    source_id INTEGER NOT NULL REFERENCES fetched_sources(id),
                    stance TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    excerpt TEXT NOT NULL,
                    record_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS submissions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    market_address TEXT NOT NULL,
                    submission_id TEXT,
                    status TEXT NOT NULL,
                    note_sha256 TEXT NOT NULL,
                    links_json TEXT NOT NULL,
                    response_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def record_source(self, doc: SourceDocument) -> int:
        """Insert or retrieve an existing fetched source document ID."""
        excerpt = doc.text_content[:500].strip()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM fetched_sources
                WHERE normalized_url = ? AND content_sha256 = ?
                """,
                (doc.normalized_url, doc.content_sha256),
            ).fetchone()
            if row:
                return int(row["id"])
            cur = conn.execute(
                """
                INSERT INTO fetched_sources (
                    url, normalized_url, domain, source_type, http_status,
                    content_sha256, raw_byte_len, published_at, fetched_at, title, excerpt
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    doc.url,
                    doc.normalized_url,
                    doc.domain,
                    doc.source_type,
                    doc.http_status,
                    doc.content_sha256,
                    doc.raw_byte_len,
                    doc.published_at,
                    doc.fetched_at,
                    doc.title[:300],
                    excerpt,
                ),
            )
            return int(cur.lastrowid)

    def record_signal(
        self,
        market_address: str,
        doc: SourceDocument,
        signal: EvidenceSignal,
    ) -> ProvenanceRecord:
        """Record a verifiable citation linking a market sub-claim to a fetched source."""
        source_id = self.record_source(doc)
        rhash = compute_record_hash(
            market_address=market_address,
            claim_id=signal.claim_id,
            content_sha256=doc.content_sha256,
            source_url=doc.url,
            excerpt=signal.excerpt,
            fetched_at=doc.fetched_at,
        )
        now = utc_now_iso()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT id FROM evidence_claims WHERE record_hash = ?",
                (rhash,),
            ).fetchone()
            if existing:
                rec_id = int(existing["id"])
            else:
                cur = conn.execute(
                    """
                    INSERT INTO evidence_claims (
                        market_address, claim_id, source_id, stance,
                        confidence, excerpt, record_hash, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        market_address.lower(),
                        signal.claim_id,
                        source_id,
                        signal.stance.value,
                        float(signal.confidence),
                        signal.excerpt,
                        rhash,
                        now,
                    ),
                )
                rec_id = int(cur.lastrowid)
        return ProvenanceRecord(
            record_id=rec_id,
            market_address=market_address.lower(),
            claim_id=signal.claim_id,
            source_url=doc.url,
            domain=doc.domain,
            source_type=doc.source_type,
            http_status=doc.http_status,
            content_sha256=doc.content_sha256,
            published_at=doc.published_at,
            fetched_at=doc.fetched_at,
            stance=signal.stance,
            confidence=signal.confidence,
            excerpt=signal.excerpt,
            record_hash=rhash,
        )

    def record_submission(
        self,
        idempotency_key: str,
        market_address: str,
        note: str,
        links: list[str],
        status: str,
        response: dict[str, Any],
        submission_id: str | None = None,
    ) -> None:
        """Persist a submission attempt or dry-run packet for deduplication."""
        note_sha = compute_sha256(note)
        now = utc_now_iso()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO submissions (
                    idempotency_key, market_address, submission_id, status,
                    note_sha256, links_json, response_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_key) DO UPDATE SET
                    submission_id = COALESCE(excluded.submission_id, submissions.submission_id),
                    status = excluded.status,
                    response_json = excluded.response_json,
                    updated_at = excluded.updated_at
                """,
                (
                    idempotency_key,
                    market_address.lower(),
                    submission_id,
                    status,
                    note_sha,
                    json.dumps(links),
                    json.dumps(response),
                    now,
                    now,
                ),
            )

    def has_submitted_fingerprint(self, market_address: str, note: str) -> bool:
        """Return True if an identical note was already recorded as queued/judged for this market."""
        note_sha = compute_sha256(note)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id FROM submissions
                WHERE market_address = ? AND note_sha256 = ?
                  AND status IN ('queued', 'judging', 'judged')
                """,
                (market_address.lower(), note_sha),
            ).fetchone()
            return row is not None

    def get_market_provenance(self, market_address: str) -> list[ProvenanceRecord]:
        """Retrieve all recorded provenance entries for a market."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ec.id, ec.market_address, ec.claim_id, ec.stance, ec.confidence,
                       ec.excerpt, ec.record_hash, fs.url, fs.domain, fs.source_type,
                       fs.http_status, fs.content_sha256, fs.published_at, fs.fetched_at
                FROM evidence_claims ec
                JOIN fetched_sources fs ON ec.source_id = fs.id
                WHERE ec.market_address = ?
                ORDER BY ec.confidence DESC, ec.id ASC
                """,
                (market_address.lower(),),
            ).fetchall()
        return [
            ProvenanceRecord(
                record_id=int(r["id"]),
                market_address=r["market_address"],
                claim_id=r["claim_id"],
                source_url=r["url"],
                domain=r["domain"],
                source_type=r["source_type"],
                http_status=int(r["http_status"]),
                content_sha256=r["content_sha256"],
                published_at=r["published_at"],
                fetched_at=r["fetched_at"],
                stance=Stance(r["stance"]),
                confidence=float(r["confidence"]),
                excerpt=r["excerpt"],
                record_hash=r["record_hash"],
            )
            for r in rows
        ]

    def verify_integrity(self) -> dict[str, Any]:
        """Recompute all provenance record hashes and validate SHA-256 and URL invariants."""
        anomalies: list[str] = []
        with self._connect() as conn:
            sources_count = conn.execute("SELECT COUNT(*) FROM fetched_sources").fetchone()[0]
            submissions_count = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
            rows = conn.execute(
                """
                SELECT ec.id, ec.market_address, ec.claim_id, ec.excerpt, ec.record_hash,
                       fs.url, fs.content_sha256, fs.fetched_at, fs.http_status
                FROM evidence_claims ec
                JOIN fetched_sources fs ON ec.source_id = fs.id
                ORDER BY ec.id ASC
                """
            ).fetchall()

        for row in rows:
            expected = compute_record_hash(
                market_address=row["market_address"],
                claim_id=row["claim_id"],
                content_sha256=row["content_sha256"],
                source_url=row["url"],
                excerpt=row["excerpt"],
                fetched_at=row["fetched_at"],
            )
            if expected != row["record_hash"]:
                anomalies.append(f"claim#{row['id']}: hash mismatch (expected {expected}, got {row['record_hash']})")
            if len(row["content_sha256"]) != 64:
                anomalies.append(f"claim#{row['id']}: invalid source SHA-256 length")
            if not str(row["url"]).startswith(("http://", "https://")):
                anomalies.append(f"claim#{row['id']}: invalid source URL scheme ({row['url']})")

        return {
            "verified": len(anomalies) == 0,
            "total_sources": int(sources_count),
            "total_claims": len(rows),
            "total_submissions": int(submissions_count),
            "anomalies": anomalies,
        }
