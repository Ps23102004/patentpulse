"""Command-line interface for PatentPulse."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeRemainingColumn
from rich.table import Table

from patentpulse import scan as scan_mod
from patentpulse.ollama_client import OllamaConnectionError
from patentpulse.server import DEFAULT_PORT
from patentpulse.sources import (
    DEFAULT_LIMIT,
    DEFAULT_WINDOW_DAYS,
    SNAPSHOT_PATH,
    FixtureSource,
    PatentDataError,
)

app = typer.Typer(help="Prior-art landscape scans over real patent data.")
console = Console()
error_console = Console(stderr=True)


def _fail(exc: Exception) -> None:
    error_console.print(f"[bold red]Error:[/bold red] {exc}")
    raise typer.Exit(1)


def _results_table(results: list[dict]) -> Table:
    table = Table(title="Closest prior art in the snapshot")
    table.add_column("Patent", style="bold")
    table.add_column("Title", max_width=52)
    table.add_column("Assignee", max_width=24)
    table.add_column("Granted", justify="right")
    table.add_column("CPC", max_width=18)
    table.add_column("Score", justify="right")
    for row in results:
        table.add_row(
            row["patent_id"],
            row["title"],
            row["assignee"] or "—",
            row["grant_date"],
            ", ".join(row["cpc_codes"][:2]) or "—",
            f"{row['relevance_score']:.2f}",
        )
    return table


@app.command()
def scan(
    idea: str = typer.Argument(..., help="Plain-English description of your product idea"),
    json_out: bool = typer.Option(False, "--json", help="Output machine-readable JSON"),
    top: int = typer.Option(scan_mod.DEFAULT_TOP_K, "--top", help="How many patents to return"),
    model: str = typer.Option(scan_mod.DEFAULT_MODEL, "--model", help="Local Ollama model"),
    refresh: bool = typer.Option(False, "--refresh", help="Re-fetch the snapshot first"),
) -> None:
    """Scan a product idea against recent granted US patents."""
    try:
        payload = scan_mod.scan(idea, top_k=top, model=model, refresh=refresh)
    except (scan_mod.ScanError, PatentDataError, OllamaConnectionError) as exc:
        _fail(exc)
        return

    if json_out:
        print(json.dumps(payload, indent=2))
        return

    scope = payload["scope"]
    summary = payload["landscape_summary"]
    console.print(Panel(scope["note"], title="Scope"))
    if payload["results"]:
        console.print(_results_table(payload["results"]))
    else:
        console.print("[yellow]No patents in the snapshot matched this idea.[/yellow]")
    console.print(Panel(summary["text"], title="Landscape"))
    console.print(
        f"[dim]{summary['recent_filing_count']} of the snapshot's patents touch these "
        f"concepts · top CPC: {', '.join(summary['top_cpc_codes']) or '—'}[/dim]"
    )
    console.print(Panel(payload["disclaimer"], title="Disclaimer", border_style="yellow"))


@app.command()
def fetch(
    window: int = typer.Option(DEFAULT_WINDOW_DAYS, "--window", help="Days of grants to cover"),
    limit: int = typer.Option(DEFAULT_LIMIT, "--limit", help="Max patents to pull"),
) -> None:
    """Build a fresh snapshot from the active data source."""
    source = scan_mod.get_source()
    if source.source_id == FixtureSource.source_id:
        console.print(
            "[yellow]No EPO OPS credentials found[/yellow] — loading the committed "
            "sample fixture instead. It is real patent data, but a hand-picked demo "
            "set, not a search. Set EPO_OPS_KEY and EPO_OPS_SECRET for the real thing."
        )
    else:
        console.print(f"Searching EPO Open Patent Services for up to {limit} publications.")
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TextColumn("{task.completed}/{task.total}"),
            TimeRemainingColumn(),
            console=console,
        ) as bar:
            task = bar.add_task("Fetching records", total=limit)
            snapshot = scan_mod.ensure_snapshot(
                refresh=True,
                window_days=window,
                limit=limit,
                progress=lambda done, total: bar.update(task, completed=done, total=total),
            )
    except (PatentDataError, ValueError) as exc:
        _fail(exc)
        return
    start, end = snapshot.date_range
    console.print(
        f"Cached [bold]{len(snapshot.records)}[/bold] patent records "
        f"({start} → {end}, source: {snapshot.source}) to {SNAPSHOT_PATH}"
    )


@app.command()
def serve(
    port: int = typer.Option(DEFAULT_PORT, "--port", help="Port to listen on"),
) -> None:
    """Serve the local web app and GET /api/scan?q=<idea>."""
    from patentpulse.server import main as serve_main

    serve_main(port=port)


if __name__ == "__main__":
    app()
