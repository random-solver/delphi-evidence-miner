"""Command-line interface for Delphi Evidence Miner."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from delphi_miner.delphi_client import DelphiAPIError, DelphiEvidenceClient
from delphi_miner.fetchers import PublicSourceFetcher
from delphi_miner.ledger import ProvenanceLedger
from delphi_miner.models import MarketSpec
from delphi_miner.pipeline import EvidenceMiningPipeline, rank_markets, score_market_opportunity

app = typer.Typer(help="Verifiable evidence-mining agent for Delphi Agent Arena forecasting markets.")
console = Console()


@app.command("markets")
def cmd_markets(
    limit: int = typer.Option(10, "--limit", "-n", help="Maximum number of markets to display."),
    save_json: Optional[Path] = typer.Option(None, "--save-json", help="Optional path to save markets snapshot JSON."),
) -> None:
    """Fetch and rank live open competition markets from Delphi."""
    with PublicSourceFetcher() as fetcher:
        all_markets = fetcher.fetch_delphi_markets()
    ranked = rank_markets(all_markets, limit=limit)

    if save_json is not None:
        save_json.parent.mkdir(parents=True, exist_ok=True)
        save_json.write_text(
            json.dumps([m.model_dump() for m in ranked], indent=2),
            encoding="utf-8",
        )
        console.print(f"[green]Saved {len(ranked)} ranked markets to {save_json}[/green]")

    table = Table(title=f"Delphi Live Competition Markets (Top {len(ranked)} of {len(all_markets)})")
    table.add_column("Score", justify="right", style="cyan")
    table.add_column("Category", style="magenta")
    table.add_column("YES", justify="right", style="yellow")
    table.add_column("Pot USDC", justify="right", style="green")
    table.add_column("Cutoff", style="white")
    table.add_column("Question")

    for m in ranked:
        sc = score_market_opportunity(m)
        table.add_row(
            f"{sc:.3f}",
            m.category_label,
            f"{m.odds.yes:.1%}",
            f"${m.pots.total_usdc:.0f}",
            (m.evidence_cutoff_at or "")[:16],
            m.question[:72] + ("..." if len(m.question) > 72 else ""),
        )
    console.print(table)


@app.command("mine")
def cmd_mine(
    limit: int = typer.Option(3, "--limit", "-n", help="Number of top markets to mine."),
    market_address: Optional[str] = typer.Option(None, "--market", "-m", help="Specific Delphi market contract address."),
    question: Optional[str] = typer.Option(None, "--question", "-q", help="Custom forecasting question (offline/ad-hoc mode)."),
    source_url: list[str] = typer.Option([], "--source", "-s", help="Extra public source URL or RSS/Atom feed."),
    output_dir: Path = typer.Option(Path("outputs"), "--output-dir", "-o", help="Directory for JSON/Markdown dossiers."),
    ledger_db: Path = typer.Option(Path("delphi_ledger.db"), "--ledger", help="SQLite provenance ledger path."),
    submit_live: bool = typer.Option(False, "--submit", help="Submit passing dossiers to Delphi API (requires DELPHI_API_ACCESS_KEY)."),
) -> None:
    """Decompose claims, fetch live public sources, score corroboration/contradiction, and emit dossiers."""
    ledger = ProvenanceLedger(ledger_db)
    with EvidenceMiningPipeline(ledger=ledger) as pipeline:
        if question:
            targets = [
                MarketSpec(
                    address=market_address or "0x0000000000000000000000000000000000000001",
                    question=question,
                    data_sources=list(source_url),
                )
            ]
            extra = []
        else:
            all_markets = pipeline.fetcher.fetch_delphi_markets()
            if market_address:
                targets = [m for m in all_markets if m.address.lower() == market_address.lower()]
                if not targets:
                    console.print(f"[red]Market {market_address} not found in live markets.json.[/red]")
                    raise typer.Exit(code=1)
            else:
                targets = rank_markets(all_markets, limit=limit)
            extra = list(source_url)

        for idx, mkt in enumerate(targets, start=1):
            console.print(f"\n[bold cyan][{idx}/{len(targets)}] Mining:[/bold cyan] {mkt.question}")
            res = pipeline.mine_market(
                mkt,
                extra_urls=extra,
                include_external_apis=True,
                output_dir=output_dir,
                submit_live=submit_live,
            )
            dossier = res["dossier"]
            corr = dossier.corroboration
            console.print(
                f"  Signals: {corr.total_signals} across {corr.distinct_domains_count} domain(s) | "
                f"Stance: {corr.recommended_stance.value} | "
                f"Corrob: {corr.corroboration_score:.2f} | Contradiction: {corr.contradiction_index:.2f} | "
                f"Delta: {corr.delta_from_market:.2%}"
            )
            console.print(f"  Written: {', '.join(res['written_files'])}")
            if res["fetch_errors"]:
                console.print(f"  [yellow]Non-fatal fetch notes ({len(res['fetch_errors'])}):[/yellow] {res['fetch_errors'][0]}")


@app.command("verify-ledger")
def cmd_verify_ledger(
    ledger_db: Path = typer.Option(Path("delphi_ledger.db"), "--ledger", help="SQLite provenance ledger path."),
) -> None:
    """Verify cryptographic hashes and URL/SHA-256 invariants of the citation provenance ledger."""
    if not ledger_db.exists():
        console.print(f"[red]Ledger not found: {ledger_db}[/red]")
        raise typer.Exit(code=1)
    ledger = ProvenanceLedger(ledger_db)
    report = ledger.verify_integrity()
    console.print_json(data=report)
    if not report["verified"]:
        raise typer.Exit(code=1)


@app.command("status")
def cmd_status() -> None:
    """Check Delphi Evidence API connectivity, limits, and terms status (never prints API key)."""
    with DelphiEvidenceClient() as client:
        if not client.has_api_key:
            console.print(
                "[yellow]DELPHI_API_ACCESS_KEY is not set.[/yellow] Running in offline/dry-run mode. "
                "To enable live submission, create a Mainnet key at https://api-access.delphi.fyi."
            )
            return
        try:
            limits = client.get_limits()
            terms = client.get_terms()
            console.print_json(data={"limits": limits, "terms": terms})
        except DelphiAPIError as exc:
            console.print(f"[red]Delphi API error:[/red] {exc}")
            raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
