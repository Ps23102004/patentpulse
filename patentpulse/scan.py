"""The scan itself: product idea in, prior-art landscape out.

Flow: idea text -> key concepts (local model) -> BM25 over the cached snapshot
-> ranked matches -> plain-English landscape summary (local model, over the
records actually matched).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from datetime import datetime, timezone

from patentpulse import ollama_client
from patentpulse.index import BM25Index
from patentpulse.sources import (
    DEFAULT_LIMIT,
    DEFAULT_WINDOW_DAYS,
    EpoOpsSource,
    FixtureSource,
    PatentDataError,
    PatentDataSource,
    Snapshot,
    load_snapshot,
    save_snapshot,
)

DEFAULT_MODEL = "gemma4:e4b-mlx"
DEFAULT_TOP_K = 10

# BM25 here uses the always-positive IDF variant, so a single shared token gives
# a record a non-zero score — counting every one of those would call more than
# half the snapshot "related". Only records scoring at least this fraction of
# the best match count toward `recent_filing_count`.
#
# 0.4 is a heuristic, tuned against a real 250-patent snapshot: a deliberately
# broad query ("machine learning, neural network, data processing, control
# system") scores non-zero on 141/250 records, which 0.4 cuts to 30. Lower
# values leave the count overstating how crowded an area is.
_RELATED_SCORE_FLOOR = 0.4

DISCLAIMER = (
    "Informational only, not legal advice. PatentPulse is a keyword scan over a "
    "bounded, recent snapshot of granted US patents — it is not a freedom-to-operate "
    "opinion and it does not prove your idea is novel. If your situation is "
    "complicated (you are about to file, raise, or ship), a real patent attorney is "
    "worth paying for."
)

# `{provenance}` is the source's own plain-English description of where these
# records came from — see `PatentDataSource.source_note`.
_SCOPE_NOTE = (
    "{provenance} Publication dates run {start} to {end}; fetched {fetched}. This is a "
    "bounded set, not the full patent record — a patent outside it will not appear here "
    "no matter how relevant it is. recent_filing_count counts patents across the whole "
    "snapshot; top_cpc_codes are drawn only from the returned results. relevance_score "
    "is relative to the best match in this scan, not an absolute confidence.{caveat}"
)
_ABSTRACT_CAVEAT = (
    " {missing} of these {count} records have no abstract text, so they are ranked on "
    "title and classification codes alone."
)


class ScanError(Exception):
    """Raised when a scan cannot be completed honestly."""


def get_source() -> PatentDataSource:
    """The active data source: live EPO OPS if credentials exist, else the fixture.

    No flag, no config file. Export `EPO_OPS_KEY` and `EPO_OPS_SECRET` and the
    next fetch is a real search; unset them and it is honestly-labelled demo
    data. Nothing else in the codebase knows which one it got.
    """
    return EpoOpsSource() if EpoOpsSource.credentials_present() else FixtureSource()


def ensure_snapshot(
    refresh: bool = False,
    window_days: int = DEFAULT_WINDOW_DAYS,
    limit: int = DEFAULT_LIMIT,
    progress: Callable[[int, int], None] | None = None,
) -> Snapshot:
    """Return the cached snapshot, fetching a fresh one if needed."""
    source = get_source()
    if not refresh:
        cached = load_snapshot()
        # A cache built by a *different* source is stale by definition: adding
        # EPO credentials must not keep serving yesterday's fixture demo.
        if cached and cached.records and cached.source == source.source_id:
            return cached
    snapshot = source.fetch(window_days=window_days, limit=limit, progress=progress)
    save_snapshot(snapshot)
    return snapshot


# -- local model steps -----------------------------------------------------

_CONCEPTS_PROMPT = """You are helping search a database of US patents.

Below is a product idea. List the 5-10 technical search terms that would find
prior art for it. Prefer the vocabulary a patent attorney would use over the
vocabulary a marketer would use. Single words or short noun phrases.

Reply with ONLY a comma-separated list. No preamble, no numbering, no explanation.

Product idea:
{idea}"""


def extract_concepts(idea: str, model: str = DEFAULT_MODEL, host: str | None = None) -> list[str]:
    """Idea text -> key technical concepts, via the local model."""
    raw = ollama_client.ask(model, _CONCEPTS_PROMPT.format(idea=idea.strip()), host)
    concepts: list[str] = []
    # Models sometimes ignore "comma-separated" and emit a bulleted list anyway,
    # or lead with "Here are the search terms:" despite being told not to.
    for piece in re.split(r"[,\n]", raw):
        term = re.sub(r"^[\s\-*\d.)]+", "", piece).strip().strip('."')
        term = term.rsplit(":", 1)[-1].strip()  # drop any "preamble:" prefix
        if not term or len(term) >= 60 or len(term.split()) > 4:
            continue  # a sentence, not a search term
        if term.lower() not in {c.lower() for c in concepts}:
            concepts.append(term)
    if not concepts:
        raise ScanError(
            f"Local model '{model}' returned no usable concepts for that idea. "
            "Try rephrasing the idea, or a different --model."
        )
    return concepts[:10]


_SUMMARY_PROMPT = """You are writing a short prior-art landscape briefing for a founder.

Their product idea:
{idea}

These are the most similar recently granted US patents found in our snapshot:
{records}

Write 3-5 sentences in plain English covering: what technical territory these
patents already occupy, which companies show up, and where the idea might still
have room. Be concrete and reference actual patents above. Do not give legal
advice, do not say whether the idea is patentable, and do not invent any patent
that is not listed. If the matches look only loosely related to the idea, say so
plainly."""


def _format_for_summary(results: list[dict]) -> str:
    lines = []
    for r in results:
        assignee = r["assignee"] or "unassigned"
        cpc = ", ".join(r["cpc_codes"][:3]) or "no CPC listed"
        abstract = (r["abstract"] or "")[:300]
        lines.append(
            f"- {r['patent_id']} ({r['grant_date']}, {assignee}) [{cpc}]\n"
            f"  {r['title']}\n  {abstract}"
        )
    return "\n".join(lines)


def summarize_landscape(
    idea: str, results: list[dict], model: str = DEFAULT_MODEL, host: str | None = None
) -> str:
    """Real model output over the records actually matched. No templates."""
    if not results:
        return (
            "No patents in the current snapshot matched this idea's key concepts. "
            "That is a statement about this bounded snapshot, not evidence that the "
            "idea is unclaimed — widen the window or rephrase the idea."
        )
    prompt = _SUMMARY_PROMPT.format(idea=idea.strip(), records=_format_for_summary(results))
    text = ollama_client.ask(model, prompt, host)
    if not text:
        raise ScanError(f"Local model '{model}' returned an empty landscape summary.")
    return text


# -- the payload -----------------------------------------------------------


def scan(
    idea: str,
    snapshot: Snapshot | None = None,
    top_k: int = DEFAULT_TOP_K,
    model: str = DEFAULT_MODEL,
    host: str | None = None,
    refresh: bool = False,
) -> dict:
    """Run a full scan and return the pinned JSON payload."""
    idea = (idea or "").strip()
    if not idea:
        raise ScanError("Give me a product idea to scan for.")
    if top_k < 1:
        # Otherwise an empty results list would be reported as "nothing matched",
        # which is a different and untrue statement.
        raise ScanError("top_k must be at least 1.")

    if snapshot is None:
        snapshot = ensure_snapshot(refresh=refresh)
    if not snapshot.records:
        raise PatentDataError("The cached snapshot is empty. Run `patentpulse fetch --refresh`.")

    concepts = extract_concepts(idea, model=model, host=host)
    index = BM25Index(snapshot.records)
    query = " ".join(concepts)

    all_scores = index.score(query)
    hits = index.search(query, top_k=top_k, scores=all_scores)
    best = max(all_scores, default=0.0)
    related_count = sum(1 for s in all_scores if s >= best * _RELATED_SCORE_FLOOR and s > 0)

    # relevance_score is normalized to 0-1 against the top-scoring result *in
    # this response* — the best match is always 1.0, by construction. It ranks
    # these results against each other and says nothing in absolute terms: a
    # 1.0 is not "a 100% match", it is "the closest thing this scan found",
    # which on an unrelated idea can still be a bad match. Scores are also not
    # comparable between scans. The raw Okapi BM25 score it is derived from is
    # unbounded, which is why it is not exposed directly.
    top_score = hits[0][1] if hits else 0.0
    results = [
        {
            "patent_id": rec.patent_id,
            "title": rec.title,
            "abstract": rec.abstract,
            "assignee": rec.assignee,
            "grant_date": rec.grant_date,
            "filing_date": rec.filing_date,
            "cpc_codes": rec.cpc_codes,
            "relevance_score": round(score / top_score, 4) if top_score else 0.0,
            "url": rec.url,
        }
        for rec, score in hits
    ]

    cpc_counts = Counter(code for r in results for code in r["cpc_codes"])
    start, end = snapshot.date_range
    total = len(snapshot.records)
    missing_abstracts = sum(1 for r in snapshot.records if not r.abstract)
    caveat = (
        _ABSTRACT_CAVEAT.format(missing=missing_abstracts, count=total)
        if missing_abstracts
        else ""
    )

    return {
        "query": idea,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "scope": {
            "source": snapshot.source,
            "snapshot_date_range": [start, end],
            "note": _SCOPE_NOTE.format(
                provenance=snapshot.note or f"Ranked against {total} cached patent records.",
                start=start,
                end=end,
                fetched=snapshot.fetched_at or "at an unrecorded time",
                caveat=caveat,
            ),
        },
        "results": results,
        "landscape_summary": {
            "text": summarize_landscape(idea, results, model=model, host=host),
            "recent_filing_count": related_count,
            "top_cpc_codes": [code for code, _ in cpc_counts.most_common(5)],
        },
        "disclaimer": DISCLAIMER,
    }
