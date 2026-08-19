"""CLI surface."""

from __future__ import annotations

import json
from unittest.mock import patch

from typer.testing import CliRunner

from patentpulse.cli import app
from patentpulse.ollama_client import OllamaConnectionError
from patentpulse.sources import PatentDataError

runner = CliRunner()

PAYLOAD = {
    "query": "solar panel cleaning robot",
    "generated_at": "2026-08-19T00:00:00Z",
    "scope": {
        "source": "fixture_sample",
        "snapshot_date_range": ["2026-08-18", "2026-08-18"],
        "note": "A recent slice, not the full record.",
    },
    "results": [
        {
            "patent_id": "US-1",
            "title": "Robotic cleaner for solar panels",
            "abstract": "A robot traverses solar panels.",
            "assignee": "Acme Corp",
            "grant_date": "2026-08-18",
            "filing_date": "2024-02-02",
            "cpc_codes": ["H02S40/10"],
            "relevance_score": 3.1416,
            "url": "https://patents.google.com/patent/US1/en",
        }
    ],
    "landscape_summary": {
        "text": "Robotic solar cleaning is already crowded.",
        "recent_filing_count": 2,
        "top_cpc_codes": ["H02S40/10"],
    },
    "disclaimer": "Informational only, not legal advice.",
}


def test_scan_json_prints_exactly_the_payload():
    with patch("patentpulse.cli.scan_mod.scan", return_value=PAYLOAD):
        result = runner.invoke(app, ["scan", "solar panel cleaning robot", "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout) == PAYLOAD


def test_scan_json_emits_no_rich_decoration():
    with patch("patentpulse.cli.scan_mod.scan", return_value=PAYLOAD):
        result = runner.invoke(app, ["scan", "idea", "--json"])
    assert "╭" not in result.stdout and "│" not in result.stdout


def test_scan_human_output_shows_results_and_disclaimer():
    with patch("patentpulse.cli.scan_mod.scan", return_value=PAYLOAD):
        result = runner.invoke(app, ["scan", "solar panel cleaning robot"])
    assert result.exit_code == 0
    assert "US-1" in result.stdout
    assert "not legal advice" in result.stdout
    assert "Robotic solar cleaning is already crowded." in result.stdout


def test_scan_human_output_handles_zero_results():
    empty = {**PAYLOAD, "results": []}
    with patch("patentpulse.cli.scan_mod.scan", return_value=empty):
        result = runner.invoke(app, ["scan", "something obscure"])
    assert result.exit_code == 0
    assert "No patents in the snapshot matched" in result.stdout


def test_scan_passes_options_through():
    with patch("patentpulse.cli.scan_mod.scan", return_value=PAYLOAD) as mock:
        runner.invoke(app, ["scan", "idea", "--json", "--top", "3", "--model", "m", "--refresh"])
    assert mock.call_args.kwargs["top_k"] == 3
    assert mock.call_args.kwargs["model"] == "m"
    assert mock.call_args.kwargs["refresh"] is True


def test_data_errors_exit_nonzero_and_go_to_stderr():
    runner_split = CliRunner()
    with patch("patentpulse.cli.scan_mod.scan", side_effect=PatentDataError("EPO OPS is down")):
        result = runner_split.invoke(app, ["scan", "idea", "--json"])
    assert result.exit_code == 1
    assert "EPO OPS is down" in result.output
    assert result.stdout.strip() == "" or "EPO OPS is down" not in result.stdout


def test_ollama_errors_exit_nonzero():
    with patch("patentpulse.cli.scan_mod.scan", side_effect=OllamaConnectionError("no ollama")):
        result = runner.invoke(app, ["scan", "idea"])
    assert result.exit_code == 1
    assert "no ollama" in result.output


def test_fetch_reports_what_it_cached():
    from patentpulse.sources import Snapshot

    snap = Snapshot(
        records=[object()] * 7,
        fetched_at="x",
        date_range=("2026-08-01", "2026-08-18"),
        source="s",
    )
    # --limit differs from the cached count so a passing assert can't be
    # satisfied by the "Pulling up to N patents" header line.
    with patch("patentpulse.cli.scan_mod.ensure_snapshot", return_value=snap):
        result = runner.invoke(app, ["fetch", "--limit", "99"])
    assert result.exit_code == 0
    cached_line = [ln for ln in result.stdout.splitlines() if "Cached" in ln]
    assert cached_line, result.stdout
    assert "7" in cached_line[0]
    assert "patent records" in result.stdout


def test_fetch_says_out_loud_when_it_is_serving_the_demo_fixture(monkeypatch):
    """The user must never be left thinking a fixture load was a live search."""
    from patentpulse.sources import Snapshot

    monkeypatch.delenv("EPO_OPS_KEY", raising=False)
    monkeypatch.delenv("EPO_OPS_SECRET", raising=False)
    snap = Snapshot(records=[object()], fetched_at="x", date_range=("a", "b"),
                    source="fixture_sample")
    with patch("patentpulse.cli.scan_mod.ensure_snapshot", return_value=snap):
        result = runner.invoke(app, ["fetch"])
    assert result.exit_code == 0
    assert "No EPO OPS credentials" in result.stdout
    assert "not a search" in result.stdout


def test_fetch_surfaces_rate_limit_failures():
    with patch(
        "patentpulse.cli.scan_mod.ensure_snapshot",
        side_effect=PatentDataError("Only 61 of 500 patent detail fetches succeeded."),
    ):
        result = runner.invoke(app, ["fetch"])
    assert result.exit_code == 1
    # Rich colourises the numerals, so match on the prose either side of them.
    assert "patent detail fetches succeeded" in result.output
