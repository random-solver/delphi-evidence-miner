"""End-to-end integration tests for EvidenceMiningPipeline and CLI."""

from pathlib import Path

import httpx
from typer.testing import CliRunner

from delphi_miner.cli import app
from delphi_miner.delphi_client import DelphiEvidenceClient
from delphi_miner.fetchers import PublicSourceFetcher
from delphi_miner.ledger import ProvenanceLedger
from delphi_miner.models import MarketOdds, MarketPots, MarketSpec
from delphi_miner.pipeline import EvidenceMiningPipeline, rank_markets


def test_rank_markets_filters_closed_and_prioritizes_uncertainty() -> None:
    markets = [
        MarketSpec(
            address="0xclosed",
            question="Closed market",
            status="closed",
            odds=MarketOdds(yes=0.5, no=0.5),
            pots=MarketPots(judgement="20000000", settlement="30000000"),
        ),
        MarketSpec(
            address="0xskewed",
            question="Skewed market",
            status="open",
            odds=MarketOdds(yes=0.95, no=0.05),
            evidence_cutoff_at="2099-01-01T00:00:00Z",
            pots=MarketPots(judgement="20000000", settlement="30000000"),
        ),
        MarketSpec(
            address="0xbalanced",
            question="Balanced market",
            status="open",
            odds=MarketOdds(yes=0.50, no=0.50),
            evidence_cutoff_at="2099-01-01T00:00:00Z",
            pots=MarketPots(judgement="20000000", settlement="30000000"),
        ),
    ]
    ranked = rank_markets(markets, limit=5)
    assert [m.address for m in ranked] == ["0xbalanced", "0xskewed"]


def test_end_to_end_pipeline_with_mocked_sources_and_live_submit(tmp_path: Path) -> None:
    def source_handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "datatracker.ietf.org" in url:
            html = """
            <html>
              <head>
                <title>draft-ietf-mpls-mna-ioam Ballot Status</title>
                <meta property="article:published_time" content="2026-11-26T11:00:00Z"/>
              </head>
              <body>
                <p>IESG ballot for draft-ietf-mpls-mna-ioam: DISCUSS recorded by Area Director on 2026-11-26.</p>
              </body>
            </html>
            """
            return httpx.Response(200, text=html, headers={"content-type": "text/html"})
        if "api.github.com" in url:
            return httpx.Response(
                200,
                json={"items": [{"full_name": "ietf/draft-ietf-mpls-mna-ioam", "html_url": "https://github.com/ietf/draft-ietf-mpls-mna-ioam", "description": "IESG DISCUSS confirmed in tracker", "stargazers_count": 5, "updated_at": "2026-11-26T09:00:00Z"}]},
            )
        return httpx.Response(404, text="not found")

    def delphi_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/submissions" and request.method == "POST":
            return httpx.Response(202, json={"id": "sub_e2e_1", "status": "queued"})
        return httpx.Response(404, json={"code": "not_found"})

    market = MarketSpec(
        address="0xietf00000001",
        question="Will any IESG member record a DISCUSS on draft-ietf-mpls-mna-ioam in the IETF Datatracker by 2026-12-03 23:59 UTC?",
        category="releases_standards",
        odds=MarketOdds(yes=0.32, no=0.68),
        evidence_cutoff_at="2026-12-02T12:00:00.000Z",
        data_sources=["https://datatracker.ietf.org/doc/draft-ietf-mpls-mna-ioam/"],
    )

    ledger = ProvenanceLedger(tmp_path / "e2e.db")
    fetcher = PublicSourceFetcher(client=httpx.Client(transport=httpx.MockTransport(source_handler)))
    dclient = DelphiEvidenceClient(api_key="dummy-key", client=httpx.Client(transport=httpx.MockTransport(delphi_handler)))

    with EvidenceMiningPipeline(fetcher=fetcher, ledger=ledger, delphi_client=dclient) as pipe:
        res = pipe.mine_market(
            market,
            include_external_apis=True,
            output_dir=tmp_path / "dossiers",
            submit_live=True,
        )

    dossier = res["dossier"]
    assert dossier.submission_payload_valid is True
    assert dossier.corroboration.passes_submission_threshold is True
    assert res["submission"]["submitted"] is True
    assert res["submission"]["response"]["id"] == "sub_e2e_1"
    assert len(res["written_files"]) == 2
    for fp in res["written_files"]:
        assert Path(fp).exists()

    # Second run should deduplicate submission via ledger fingerprint
    with EvidenceMiningPipeline(fetcher=fetcher, ledger=ledger, delphi_client=dclient) as pipe:
        res2 = pipe.mine_market(market, include_external_apis=True, submit_live=True)
    assert res2["submission"]["submitted"] is False
    assert "Duplicate note fingerprint" in res2["submission"]["reason"]

    integrity = ledger.verify_integrity()
    assert integrity["verified"] is True
    assert integrity["total_submissions"] == 1


def test_cli_verify_ledger_and_status(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("DELPHI_API_ACCESS_KEY", raising=False)
    runner = CliRunner()
    res_status = runner.invoke(app, ["status"])
    assert res_status.exit_code == 0
    assert "offline/dry-run mode" in res_status.output

    db_path = tmp_path / "cli_ledger.db"
    ProvenanceLedger(db_path)
    res_verify = runner.invoke(app, ["verify-ledger", "--ledger", str(db_path)])
    assert res_verify.exit_code == 0
    assert '"verified": true' in res_verify.output
