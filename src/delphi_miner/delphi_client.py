"""Delphi Evidence API v1 client matching https://delphi.fyi/v1/openapi.json."""

from __future__ import annotations

import mimetypes
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

DELPHI_API_BASE_URL = "https://delphi.fyi/v1"
DEFAULT_CLIENT_USER_AGENT = "DelphiEvidenceMiner/0.1.0 (+https://github.com/random-solver/delphi-evidence-miner)"
MAX_NOTE_CHARS = 8000
MAX_TOTAL_ITEMS = 3  # note (1 if present) + len(links) + len(files) <= 3
MAX_TOTAL_FILE_BYTES = 10 * 1024 * 1024  # 10 MB total across attached files


class DelphiAPIError(RuntimeError):
    """Structured error raised for Delphi Evidence API failures."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        retryable: bool = False,
        retry_after_seconds: int | None = None,
        field: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(f"HTTP {status_code} ({code}): {message}")
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_after_seconds = retry_after_seconds
        self.field = field
        self.request_id = request_id


@dataclass(frozen=True)
class AgentIdentity:
    agent_id: str = "delphi-evidence-miner"
    agent_harness: str = "python-httpx"
    agent_model: str = "deterministic-corroboration-v1"

    def headers(self) -> dict[str, str]:
        return {
            "X-Agent-Id": self.agent_id[:64],
            "X-Agent-Harness": self.agent_harness[:64],
            "X-Agent-Model": self.agent_model[:64],
        }


def build_submission_packet(
    market_address: str,
    note: str | None = None,
    links: list[str] | None = None,
    file_paths: list[str | Path] | None = None,
) -> dict[str, Any]:
    """Validate and construct a Delphi-compliant submission packet (<=3 items, <=8000 note chars)."""
    links_list = [u.strip() for u in (links or []) if u and u.strip()]
    files_list = [str(p) for p in (file_paths or [])]
    note_clean = note.strip() if note and note.strip() else None

    if note_clean and len(note_clean) > MAX_NOTE_CHARS:
        raise ValueError(f"Note exceeds Delphi limit of {MAX_NOTE_CHARS} characters (got {len(note_clean)}).")

    item_count = (1 if note_clean else 0) + len(links_list) + len(files_list)
    if item_count == 0:
        raise ValueError("Submission must contain at least 1 item (note, link, or file).")
    if item_count > MAX_TOTAL_ITEMS:
        raise ValueError(
            f"Delphi allows at most {MAX_TOTAL_ITEMS} items total (note + links + files); got {item_count}."
        )

    return {
        "marketAddress": market_address,
        "note": note_clean,
        "links": links_list,
        "files": files_list,
        "item_count": item_count,
    }


class DelphiEvidenceClient:
    """HTTP client for Delphi's Evidence API v1 (with secret-safe handling)."""

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str = DELPHI_API_BASE_URL,
        identity: AgentIdentity | None = None,
        timeout_seconds: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._api_key = api_key if api_key is not None else os.environ.get("DELPHI_API_ACCESS_KEY", "").strip()
        self.base_url = base_url.rstrip("/")
        self.identity = identity or AgentIdentity()
        self._owns_client = client is None
        self.client = client or httpx.Client(timeout=timeout_seconds)

    @property
    def has_api_key(self) -> bool:
        return bool(self._api_key)

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def __enter__(self) -> DelphiEvidenceClient:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def _auth_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        if not self._api_key:
            raise DelphiAPIError(
                401,
                "missing_api_key",
                "DELPHI_API_ACCESS_KEY is not set. Create a Mainnet API key at https://api-access.delphi.fyi.",
            )
        hdrs = {
            "Authorization": f"Bearer {self._api_key}",
            "User-Agent": DEFAULT_CLIENT_USER_AGENT,
            **self.identity.headers(),
        }
        if extra:
            hdrs.update(extra)
        return hdrs

    def _request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: list[tuple[str, tuple[Any, ...]]] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        hdrs = self._auth_headers(headers)
        resp = self.client.request(method, url, headers=hdrs, json=json_body, data=data, files=files)
        if resp.status_code >= 400:
            retry_after: int | None = None
            field: str | None = None
            request_id: str | None = None
            retryable = resp.status_code in {429, 500, 502, 503, 504}
            try:
                err = resp.json()
                request_id = err.get("requestId") if isinstance(err, dict) else None
                nested = err.get("error") if isinstance(err, dict) and isinstance(err.get("error"), dict) else None
                if nested is not None:
                    code = str(nested.get("code") or "api_error")
                    msg = str(nested.get("message") or resp.text)
                    if "retryable" in nested:
                        retryable = bool(nested["retryable"])
                    retry_after = nested.get("retryAfterSeconds")
                    field = nested.get("field")
                elif isinstance(err, dict):
                    code = str(err.get("code") or err.get("error") or "api_error")
                    msg = str(err.get("message") or resp.text)
                else:
                    code = "api_error"
                    msg = resp.text
            except ValueError:
                code = "api_error"
                msg = resp.text
            raise DelphiAPIError(
                resp.status_code,
                code,
                msg,
                retryable=retryable,
                retry_after_seconds=retry_after,
                field=field,
                request_id=request_id,
            )
        if not resp.content:
            return {"status_code": resp.status_code}
        payload = resp.json()
        if isinstance(payload, dict):
            payload.setdefault("_http_status", resp.status_code)
            if "Location" in resp.headers:
                payload.setdefault("_location", resp.headers["Location"])
        return payload

    def get_limits(self) -> dict[str, Any]:
        return self._request("GET", "/limits")

    def get_terms(self) -> dict[str, Any]:
        return self._request("GET", "/terms")

    def accept_terms(self, terms_version: str) -> dict[str, Any]:
        return self._request("POST", "/terms/acceptance", json_body={"termsVersion": terms_version})

    def get_entry_state(self, market_address: str) -> dict[str, Any]:
        return self._request("GET", f"/markets/{market_address}/entry-state")

    def get_submission(self, submission_id: str) -> dict[str, Any]:
        return self._request("GET", f"/submissions/{submission_id}")

    def list_submissions(self) -> dict[str, Any]:
        return self._request("GET", "/submissions")

    def retry_submission(self, submission_id: str) -> dict[str, Any]:
        return self._request("POST", f"/submissions/{submission_id}/retry")

    def submit_evidence(
        self,
        market_address: str,
        note: str | None = None,
        links: list[str] | None = None,
        file_paths: list[str | Path] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Submit evidence multipart/form-data payload to POST /v1/submissions."""
        packet = build_submission_packet(
            market_address=market_address,
            note=note,
            links=links,
            file_paths=file_paths,
        )
        idem = idempotency_key or str(uuid.uuid4())

        # Always encode as multipart/form-data using httpx `files=` tuples so scalar
        # fields, repeated `links[]`, and binary `files` share one RFC-7578 payload.
        multipart_parts: list[tuple[str, tuple[Any, ...]]] = [
            ("marketAddress", (None, packet["marketAddress"])),
        ]
        if packet["note"]:
            multipart_parts.append(("note", (None, packet["note"])))
        for link in packet["links"]:
            multipart_parts.append(("links[]", (None, link)))

        total_file_bytes = 0
        for fp in packet["files"]:
            p = Path(fp)
            raw_bytes = p.read_bytes()
            total_file_bytes += len(raw_bytes)
            if total_file_bytes > MAX_TOTAL_FILE_BYTES:
                raise ValueError(
                    f"Total attached file size exceeds Delphi 10 MB limit ({total_file_bytes} bytes)."
                )
            guessed, _ = mimetypes.guess_type(p.name)
            if p.suffix.lower() == ".pdf":
                ctype = "application/pdf"
            elif guessed:
                ctype = guessed
            else:
                ctype = "text/plain"
            multipart_parts.append(("files", (p.name, raw_bytes, ctype)))

        return self._request(
            "POST",
            "/submissions",
            headers={"Idempotency-Key": idem},
            files=multipart_parts,
        )
