"""Local stdlib HTTP server for the PatentPulse web app."""

from __future__ import annotations

import json
import mimetypes
import sys
import traceback
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from patentpulse import scan as scan_mod
from patentpulse.ollama_client import OllamaConnectionError
from patentpulse.sources import PatentDataError, load_snapshot

DEFAULT_PORT = 8470
WEB_DIR = (Path(__file__).resolve().parent.parent / "web").resolve()
_STATIC_ROUTES = {"/": "app.html", "/app.html": "app.html"}


class Handler(BaseHTTPRequestHandler):
    server_version = "patentpulse/0.1"

    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: str) -> None:
        relative = _STATIC_ROUTES.get(path, unquote(path).lstrip("/"))
        target = (WEB_DIR / relative).resolve()
        if WEB_DIR not in target.parents or not target.is_file():
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            return
        body = target.read_bytes()
        content_type, _ = mimetypes.guess_type(str(target))
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type or "application/octet-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        try:
            self._route_get()
        except Exception:
            traceback.print_exc(file=sys.stderr)
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR, {"error": "internal server error"}
            )

    def _route_get(self) -> None:
        parsed = urlsplit(self.path)
        if parsed.path == "/api/scan":
            self._handle_scan(parse_qs(parsed.query))
        elif parsed.path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not found"})
        else:
            self._serve_static(parsed.path)

    def _handle_scan(self, params: dict[str, list[str]]) -> None:
        query = (params.get("q", [""])[0] or "").strip()
        if not query:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": "missing ?q=<product idea>"})
            return
        try:
            # Building a snapshot pages through a quota-limited external API.
            # Doing that inside a request would hang the caller, and concurrent
            # requests would each start their own fetch and trip each other's
            # rate limit. Serving is read-only: the cache must exist.
            snapshot = load_snapshot()
            if snapshot is None or not snapshot.records:
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"error": "No patent snapshot cached yet. Run `patentpulse fetch` first."},
                )
                return
            expected = scan_mod.get_source().source_id
            if snapshot.source != expected:
                # A cache built by a different source is stale by definition —
                # serving it would label the payload with the wrong provenance.
                self._send_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {
                        "error": f"The cached snapshot came from `{snapshot.source}` but the "
                        f"configured source is `{expected}`. Run `patentpulse fetch` to rebuild."
                    },
                )
                return
            payload = scan_mod.scan(query, snapshot=snapshot)
        except (scan_mod.ScanError, PatentDataError, OllamaConnectionError) as exc:
            # These are expected, explainable failures — say what went wrong
            # rather than pretending we have results.
            self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, payload)

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main(port: int = DEFAULT_PORT) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Serving on http://127.0.0.1:{port}/  (GET /api/scan?q=<idea>)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
