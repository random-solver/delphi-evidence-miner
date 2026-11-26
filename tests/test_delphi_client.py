"""Tests for Delphi Evidence API v1 client and submission packet validator."""

from pathlib import Path

import httpx
import pytest

from delphi_miner.delphi_client import (
    MAX_TOTAL_FILE_BYTES,
    AgentIdentity,
    DelphiAPIError,
    DelphiEvidenceClient,
    build_submission_packet,
)


def test_build_submission_packet_enforces_delphi_limits() -> None:
    pkt = build_submission_packet(
        market_address="0xabc",
        note="Dated fact",
        links=["https://example.org/1", "https://example.org/2"],
    )
    assert pkt["item_count"] == 3

    with pytest.raises(ValueError, match="at most 3 items"):
        build_submission_packet(
            market_address="0xabc",
            note="Dated fact",
            links=["https://a.com", "https://b.com", "https://c.com"],
        )

    with pytest.raises(ValueError, match="8000 characters"):
        build_submission_packet(market_address="0xabc", note="x" * 8001)

    with pytest.raises(ValueError, match="at least 1 item"):
        build_submission_packet(market_address="0xabc")


def test_delphi_client_endpoints_and_headers_with_mock_transport() -> None:
    seen_headers: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.append(dict(request.headers))
        path = request.url.path
        if path == "/v1/limits":
            return httpx.Response(200, json={"hourlyRemaining": 10, "dailyRewardRemaining": "50000000"})
        if path == "/v1/terms":
            return httpx.Response(200, json={"version": "2026-11", "url": "https://delphi.fyi/tos", "acceptedAt": None})
        if path == "/v1/terms/acceptance":
            return httpx.Response(200, json={"acceptedAt": "2026-11-26T14:00:00Z"})
        if path == "/v1/markets/0xabc/entry-state":
            return httpx.Response(200, json={"canSubmit": True, "remaining": 3})
        if path == "/v1/submissions" and request.method == "POST":
            assert "idempotency-key" in request.headers
            assert request.headers["x-agent-id"] == "test-agent"
            assert "user-agent" in request.headers
            ctype = request.headers.get("content-type", "")
            assert ctype.startswith("multipart/form-data; boundary=")
            body = request.content.decode("utf-8", errors="replace")
            assert 'name="marketAddress"' in body and "0xabc" in body
            assert 'name="note"' in body
            assert 'name="links[]"' in body and "https://www.destatis.de/EN/Press/cpi" in body
            return httpx.Response(
                202,
                json={"id": "sub_123", "status": "queued"},
                headers={"Location": "/v1/submissions/sub_123"},
            )
        if path == "/v1/submissions/sub_401":
            return httpx.Response(401, json={"code": "unauthorized", "message": "Bad key"})
        return httpx.Response(404, json={"code": "not_found", "message": "Missing"})

    mock_http = httpx.Client(transport=httpx.MockTransport(handler))
    client = DelphiEvidenceClient(
        api_key="test-secret-key",
        identity=AgentIdentity(agent_id="test-agent", agent_harness="pytest", agent_model="test-v1"),
        client=mock_http,
    )

    limits = client.get_limits()
    assert limits["hourlyRemaining"] == 10
    terms = client.get_terms()
    assert terms["acceptedAt"] is None
    acc = client.accept_terms(terms["version"])
    assert acc["acceptedAt"] is not None
    state = client.get_entry_state("0xabc")
    assert state["canSubmit"] is True

    sub = client.submit_evidence(
        market_address="0xabc",
        note="As of 2026-11-26: official CPI release is 2.9%.",
        links=["https://www.destatis.de/EN/Press/cpi"],
        idempotency_key="idem-fixed-1",
    )
    assert sub["id"] == "sub_123"
    assert sub["_http_status"] == 202
    assert sub["_location"] == "/v1/submissions/sub_123"

    with pytest.raises(DelphiAPIError) as exc_info:
        client.get_submission("sub_401")
    assert exc_info.value.status_code == 401
    assert exc_info.value.code == "unauthorized"


def test_submit_evidence_with_file_and_links_multipart(tmp_path: Path) -> None:
    captured: list[tuple[str, bytes]] = []
    evidence_pdf = tmp_path / "bulletin.pdf"
    evidence_pdf.write_bytes(b"%PDF-1.4\n%test\n")

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append((request.headers.get("content-type", ""), request.content))
        return httpx.Response(202, json={"id": "sub_file_1", "status": "queued"})

    client = DelphiEvidenceClient(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    resp = client.submit_evidence(
        market_address="0xdef",
        links=["https://example.org/a", "https://example.org/b"],
        file_paths=[evidence_pdf],
        idempotency_key="idem-file-1",
    )
    assert resp["id"] == "sub_file_1"
    assert len(captured) == 1
    ctype, raw = captured[0]
    assert ctype.startswith("multipart/form-data; boundary=")
    assert b'name="marketAddress"' in raw and b"0xdef" in raw
    assert raw.count(b'name="links[]"') == 2
    assert b'name="files"; filename="bulletin.pdf"' in raw
    assert b"Content-Type: application/pdf" in raw


def test_delphi_client_parses_openapi_nested_error_and_enforces_file_size_cap(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "terms_not_accepted",
                    "message": "Accept current terms first",
                    "retryable": False,
                    "retryAfterSeconds": None,
                    "field": "termsVersion",
                },
                "requestId": "11111111-1111-4111-8111-111111111111",
            },
        )

    client = DelphiEvidenceClient(
        api_key="test-key",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(DelphiAPIError) as exc_info:
        client.submit_evidence(market_address="0xabc", note="Dated evidence fact.")
    assert exc_info.value.status_code == 403
    assert exc_info.value.code == "terms_not_accepted"
    assert exc_info.value.message == "Accept current terms first"
    assert exc_info.value.retryable is False
    assert exc_info.value.field == "termsVersion"
    assert exc_info.value.request_id == "11111111-1111-4111-8111-111111111111"

    oversize = tmp_path / "huge.txt"
    oversize.write_bytes(b"a" * (MAX_TOTAL_FILE_BYTES + 1))
    with pytest.raises(ValueError, match="10 MB"):
        client.submit_evidence(market_address="0xabc", file_paths=[oversize])
