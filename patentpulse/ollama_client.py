"""Minimal Ollama chat client.

Vendored from llm-ladder's `llm_ladder.ollama_client` (same API surface) so that
PatentPulse installs standalone instead of depending on a sibling repo that was
never published to PyPI.
"""

from __future__ import annotations

import os

import requests

DEFAULT_HOST = "http://127.0.0.1:11434"


class OllamaConnectionError(Exception):
    """Raised when communication with the Ollama endpoint fails."""


class OllamaModelNotFoundError(OllamaConnectionError):
    """Raised when Ollama reports the requested model isn't pulled (HTTP 404)."""


def resolve_host(host: str | None = None) -> str:
    return host or os.environ.get("OLLAMA_HOST", DEFAULT_HOST)


def chat(model: str, prompt: str, host: str | None = None) -> dict:
    """Send a single-turn chat request. Returns the raw Ollama JSON response."""
    host = resolve_host(host)
    url = f"{host}/api/chat"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    try:
        response = requests.post(url, json=payload, timeout=180)
        response.raise_for_status()
        return response.json()
    except (requests.ConnectionError, requests.Timeout) as exc:
        raise OllamaConnectionError(
            f"Could not reach Ollama at {host}: {exc}. Is `ollama serve` running?"
        ) from exc
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            raise OllamaModelNotFoundError(
                f"Model '{model}' isn't available at {host}. Run `ollama pull {model}` first."
            ) from exc
        status = exc.response.status_code if exc.response is not None else "?"
        raise OllamaConnectionError(f"Ollama at {host} returned status {status}: {exc}") from exc
    except ValueError as exc:
        raise OllamaConnectionError(f"Ollama at {host} returned invalid JSON: {exc}") from exc
    except requests.RequestException as exc:
        # e.g. a malformed OLLAMA_HOST raises MissingSchema — surface it as a
        # connection error rather than a raw traceback out of the CLI.
        raise OllamaConnectionError(f"Could not talk to Ollama at {host}: {exc}") from exc


def ask(model: str, prompt: str, host: str | None = None) -> str:
    """chat() but returns just the assistant's message text."""
    return chat(model, prompt, host).get("message", {}).get("content", "").strip()
