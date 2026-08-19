"""HTTP surface: GET /api/scan?q=<idea>."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

from patentpulse.ollama_client import OllamaConnectionError
from patentpulse.server import Handler
from patentpulse.sources import PatentDataError, PatentRecord, Snapshot
from tests.test_cli import PAYLOAD


@pytest.fixture(scope="module")
def base_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    server.server_close()


def _get(url: str):
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except ValueError:
            return exc.code, body


def _scan_url(base: str, idea: str) -> str:
    return f"{base}/api/scan?q={urllib.parse.quote(idea)}"


@pytest.fixture
def cached_snapshot():
    """The server only serves from an existing cache — fake one."""
    snap = Snapshot(
        records=[
            PatentRecord("US-1", "t", "a", "c", None, "2026-08-18", None, [], "https://x.test")
        ],
        fetched_at="2026-08-19T00:00:00Z",
        date_range=("2026-08-18", "2026-08-18"),
        source="fixture_sample",
    )
    with patch("patentpulse.server.load_snapshot", return_value=snap):
        yield snap


def test_scan_endpoint_returns_the_payload(base_url, cached_snapshot):
    with patch("patentpulse.server.scan_mod.scan", return_value=PAYLOAD):
        status, body = _get(_scan_url(base_url, "solar panel cleaning robot"))
    assert status == 200
    assert body == PAYLOAD


def test_scan_endpoint_passes_the_query_and_cached_snapshot_through(base_url, cached_snapshot):
    with patch("patentpulse.server.scan_mod.scan", return_value=PAYLOAD) as mock:
        _get(_scan_url(base_url, "a robot & a panel"))
    assert mock.call_args[0][0] == "a robot & a panel"
    assert mock.call_args.kwargs["snapshot"] is cached_snapshot


def test_scan_without_a_cached_snapshot_says_so_instead_of_fetching(base_url):
    """A cold cache must not trigger a live API fetch inside a request."""
    with patch("patentpulse.server.load_snapshot", return_value=None), patch(
        "patentpulse.server.scan_mod.scan"
    ) as scan_fn:
        status, body = _get(_scan_url(base_url, "idea"))
    assert status == 503
    assert "patentpulse fetch" in body["error"]
    scan_fn.assert_not_called()


def test_scan_with_an_empty_cached_snapshot_is_also_refused(base_url):
    empty = Snapshot(records=[], fetched_at="x", date_range=("a", "b"), source="fixture_sample")
    with patch("patentpulse.server.load_snapshot", return_value=empty):
        status, body = _get(_scan_url(base_url, "idea"))
    assert status == 503
    assert "error" in body


def test_a_cache_from_a_different_source_is_refused_not_relabelled(base_url):
    """Serving a snapshot built by a retired source would mislabel its provenance."""
    stale = Snapshot(
        records=[PatentRecord("US-1", "t", "a", "c", None, "2026-08-18", None, [], "https://x")],
        fetched_at="2026-08-19T00:00:00Z",
        date_range=("2026-08-18", "2026-08-18"),
        source="some_retired_source",
    )
    with patch("patentpulse.server.load_snapshot", return_value=stale), patch(
        "patentpulse.server.scan_mod.scan"
    ) as scan_fn:
        status, body = _get(_scan_url(base_url, "idea"))
    assert status == 503
    assert "some_retired_source" in body["error"]
    scan_fn.assert_not_called()


def test_corrupt_cache_surfaces_as_an_error(base_url):
    with patch(
        "patentpulse.server.load_snapshot",
        side_effect=PatentDataError("The snapshot at /x is unreadable"),
    ):
        status, body = _get(_scan_url(base_url, "idea"))
    assert status == 503
    assert "unreadable" in body["error"]


def test_missing_query_is_a_400_not_a_crash(base_url):
    status, body = _get(f"{base_url}/api/scan")
    assert status == 400
    assert "error" in body


def test_blank_query_is_rejected(base_url):
    status, body = _get(f"{base_url}/api/scan?q=%20%20")
    assert status == 400
    assert "error" in body


@pytest.mark.parametrize(
    "exc", [PatentDataError("EPO OPS unreachable"), OllamaConnectionError("no ollama")]
)
def test_expected_failures_return_an_error_not_a_fake_payload(base_url, cached_snapshot, exc):
    with patch("patentpulse.server.scan_mod.scan", side_effect=exc):
        status, body = _get(_scan_url(base_url, "idea"))
    assert status == 503
    assert "results" not in body
    assert str(exc) in body["error"]


def test_unexpected_failures_return_json_500_not_a_broken_socket(base_url, cached_snapshot):
    with patch("patentpulse.server.scan_mod.scan", side_effect=RuntimeError("boom")):
        status, body = _get(_scan_url(base_url, "idea"))
    assert status == 500
    assert body["error"] == "internal server error"


def test_unknown_api_route_is_404(base_url):
    status, body = _get(f"{base_url}/api/nope")
    assert status == 404
    assert "error" in body


def test_static_serving_refuses_path_traversal(base_url):
    status, _ = _get(f"{base_url}/../../etc/passwd")
    assert status == 404
