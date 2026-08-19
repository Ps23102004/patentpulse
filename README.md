# PatentPulse

Point it at a product idea. Get back a prior-art landscape scan: the closest
patents, ranked, plus a plain-English read on what territory is already
occupied.

Real patent data. A local model does the reading. Nothing leaves your machine
except the patent fetch itself.

> **Informational only, not legal advice.** PatentPulse is a keyword scan over a
> bounded set of patents. It is not a freedom-to-operate opinion and it does not
> prove your idea is novel. If your situation is complicated (you are about to
> file, raise, or ship), a real patent attorney is worth paying for.

> **⚠️ Shipped state: demo data.** Out of the box PatentPulse ranks against a
> committed fixture of **eight real patents** — genuine records, transcribed by
> hand, but a fixed hand-picked set, not a search. Live search over EPO Open
> Patent Services switches on the moment `EPO_OPS_KEY` and `EPO_OPS_SECRET` are
> set. See [Going live](#going-live). Every payload says which mode produced it,
> in `scope.source` and in plain English in `scope.note`.

## What it actually does

```
product idea ──▶ local model ──▶ key concepts ──▶ BM25 over snapshot ──▶ ranked patents
                                                                              │
                                                          local model ◀───────┘
                                                                │
                                                       landscape summary
```

Every number and every sentence in the output comes from real patent records.
There is no template summary and no synthetic data — if the data source or the
local model is unavailable, PatentPulse fails loudly instead of inventing
results.

## Setup

Requires Python 3.10+ and [Ollama](https://ollama.com) running locally.

```bash
pip install -e .
ollama pull gemma4:e4b-mlx      # or any model you already have
```

Point at a non-default Ollama with `OLLAMA_HOST`, or pass `--model` to pick a
different one (`qwen3.8:27b-mlx` and `ornith:35b-q4_K_M` both work well; bigger
models write better summaries and are slower).

## Usage

```bash
# Build the snapshot (first run does this automatically)
patentpulse fetch

# Scan an idea — a few seconds once the snapshot is cached
patentpulse scan "a robot that cleans dust off rooftop solar panels"

# Machine-readable
patentpulse scan "self-cleaning solar panel" --json

# Local web app + JSON endpoint
patentpulse serve            # http://127.0.0.1:8470/api/scan?q=<idea>
```

`serve` only reads an existing snapshot — run `patentpulse fetch` first, or the
endpoint returns 503 saying so. It will not start a network fetch inside an HTTP
request.

The snapshot is cached at `~/.patentpulse/snapshot.json` and reused until you
pass `--refresh`. It also records which source built it: add EPO credentials and
the next run refetches rather than quietly serving yesterday's demo data.

## Going live

The fixture exists so the tool works today. Replacing it with a real search
takes two environment variables.

1. **Register**, free, at <https://developers.epo.org/> — email and password.
   No ID.me, no identity verification, no approval queue.
2. **Create an app** in the developer portal and copy its **consumer key** and
   **consumer secret**.
3. **Export them.** They are read from the environment only — never hardcoded,
   never prompted for, never written to the snapshot cache.

   ```bash
   export EPO_OPS_KEY="<consumer key>"
   export EPO_OPS_SECRET="<consumer secret>"
   ```

4. **Rebuild the snapshot.**

   ```bash
   patentpulse fetch --window 45 --limit 250
   ```

   `scope.source` flips from `fixture_sample` to `epo_ops`, and `scope.note`
   changes with it. Nothing else in the codebase — or in the JSON contract —
   changes.

5. **Verify against the live service.**

   ```bash
   pytest tests/test_sources_live.py -v
   ```

   That file is skipped, cleanly, whenever the two variables are unset. With
   them set it authenticates for real, runs a real search, and asserts the
   parsed records are well-formed.

**The EPO OPS client has never been run against a live account.** It is written
to EPO's *OPS RESTful Web Services Reference Guide* v1.3.20 (June 2024) and
cross-checked against captured real responses and several long-lived open-source
OPS clients — but documentation is not a live 200. `EpoOpsSource`'s docstring
names exactly which parts are documented-and-cross-checked and which one part is
a genuine guess (the `pn=US` country clause), with the fallbacks to try. The live
test above is what turns that into evidence.

Free-tier limits worth knowing: 30 searches/minute, 4 GB/week. Over-quota comes
back as HTTP 403 with an `X-Rejection-Reason` header, which PatentPulse surfaces
verbatim rather than swallowing.

## JSON contract

`patentpulse scan --json` and `GET /api/scan?q=<idea>` both return exactly this
— unchanged from previous versions except for the *value* of `scope.source`:

```json
{
  "query": "string",
  "generated_at": "ISO8601",
  "scope": {
    "source": "fixture_sample",
    "snapshot_date_range": ["YYYY-MM-DD", "YYYY-MM-DD"],
    "note": "string"
  },
  "results": [
    {
      "patent_id": "string",
      "title": "string",
      "abstract": "string",
      "assignee": "string|null",
      "grant_date": "YYYY-MM-DD",
      "filing_date": "YYYY-MM-DD|null",
      "cpc_codes": ["string"],
      "relevance_score": 0.0,
      "url": "string"
    }
  ],
  "landscape_summary": {
    "text": "string",
    "recent_filing_count": 0,
    "top_cpc_codes": ["string"]
  },
  "disclaimer": "string"
}
```

Notes on three fields that are easy to misread:

- **`relevance_score` is 0-1, relative to the top result in *that response*.**
  It is `score / best_score_in_this_scan`, so the best match is always exactly
  `1.0` — by construction, not by merit. A `1.0` means "the closest thing this
  scan found", which on an unrelated idea can still be a bad match. It is not a
  confidence, not a probability, and not comparable between scans. **Do not
  render it as "100% match".** A relative bar is honest; a percentage is not.
  (The underlying Okapi BM25 score is unbounded, which is why it is normalized
  before it reaches the contract rather than exposed raw.)
- **`recent_filing_count`** is how many patents *in the whole snapshot* rank as
  genuinely related to the idea — scoring at least 40% of the best match — not
  the length of `results`, and not every patent sharing a single word. It is a
  rough crowding signal, and the 40% cutoff is a tuned heuristic, not a
  measurement. These are also granted patents, not pending filings.
- **`scope.source`** names what actually produced the records:
  - `fixture_sample` — the committed eight-patent demo fixture.
  - `epo_ops` — a live EPO Open Patent Services search.

  `scope.note` opens with a plain-English sentence saying the same thing, so an
  end user reading the output is never misled by a field name. The fixture's
  note begins `DEMO DATA — not a live patent search.`

On failure the HTTP endpoint returns `{"error": "..."}` with a 4xx/5xx status.
It never returns a partial or fabricated payload.

## Data sources

`PatentDataSource` is the seam between PatentPulse and wherever patents come
from. `get_source()` picks one from the environment: EPO OPS if credentials are
present, the fixture otherwise. Nothing downstream knows which it got.

### `EpoOpsSource` — the real one

**EPO Open Patent Services (OPS) v3.2**, the European Patent Office's free
developer API. OAuth2 client credentials in, bearer token out, then a paged CQL
search against `published-data/search/abstract,biblio`.

Records carry: title, abstract, CPC codes, filing date, publication date,
applicant, and a public patent page URL. They do **not** carry claim text — OPS
search results have none, so `claim_summary` is left empty rather than faked,
and records are ranked on title, abstract and CPC codes.

### `FixtureSource` — the demo that ships today

Eight real granted US patents in `patentpulse/fixtures/sample_patents.json`,
each transcribed by hand from its public Google Patents page on 2026-08-19 —
four in solar-panel cleaning (so the demo query above finds genuine hits) and
four widely-known software/hardware patents (so the keyword index has realistic
contrast). Every field is real; nothing is generated or paraphrased. The file's
`_meta` block records where each field came from and the two places Google
Patents' data needs a caveat (current-vs-original assignee, filing-vs-priority
date).

It is real data about real patents. It is **not** a search, and a scan against
eight hand-picked records says nothing about how crowded any technical area
actually is.

### Why EPO OPS, and not USPTO

Both originally-scoped USPTO paths are closed. Not inconvenient — closed.

**Patent Public Search (PPUBS) — removed on compliance grounds.** An earlier
build of this project fetched 250 real patents from USPTO's Patent Public Search
by spoofing a browser `User-Agent` past USPTO's WAF and calling an undocumented
internal anonymous-session endpoint. It worked and it was well tested. It also
could not ship:

- USPTO's Terms of Use state their online databases "are not designed or
  intended to be a source for bulk downloads ... when accessed through the
  website's interfaces", and flag unauthorized access as potentially
  prosecutable under **18 U.S.C. § 1030** (the CFAA).
- Spoofing a User-Agent to get past bot detection *is* the evasion, regardless
  of intent or how politely the client paces itself.

So it is gone from the repository entirely — not behind a flag, not documented
as a fallback, not commented out. `tests/test_sources.py` carries a guard test
that fails if any of its machinery reappears.

**The Open Data Portal — gated.** The other original plan was USPTO's bulk
data files. As of **2026-06-18** the ODP requires a signed-in USPTO.gov account
for *all* access, bulk file downloads included, and the query API needs a key
issued only to an ID.me-verified identity.

| Host | State as of 2026-08-19 |
| --- | --- |
| `bulkdata.uspto.gov` | DNS NXDOMAIN — gone |
| `developer.uspto.gov` | non-routable, all connections hang |
| `search.patentsview.org`, `data.patentsview.org` | DNS NXDOMAIN — shut down 2026-03-20 |
| `api.uspto.gov` | HTTP 401, needs an ID.me-verified key |
| `data.uspto.gov` (Open Data Portal) | signed-in USPTO.gov account required for **all** access since 2026-06-18 |

**EPO OPS is the opposite case.** It is a documented, free, self-service API
whose stated purpose is third-party programmatic patent search — not a public
web tool being driven by a script. Registration is an email and a password.
There is nothing to evade, because access is the point. Its coverage is
worldwide (DOCDB), so US patents are reachable through it too.

## Known limitations

Worth knowing before you trust the output:

- **The shipped default is eight patents.** Until credentials are set, a quiet
  result means "none of these eight matched", which is almost no information.
  This is the single biggest caveat about the current state.
- **Even live, it is a bounded slice.** OPS returns at most 2000 results per
  query and PatentPulse takes the most recent `--limit` of a date window. A
  2011 patent that reads directly on your idea will not appear. A quiet result
  means "nothing in this window matched", never "this idea is clear".
- **No claim text from OPS.** Ranking uses title, abstract and CPC codes.
  Claims often carry the most specific language in a patent, so the live path
  ranks on less text than the fixture path does (the fixture includes real
  claim-1 excerpts).
- **Granted patents and published applications, not everything pending.** The
  most recent filings in your space may still be invisible.
- **Keyword matching, not semantic.** BM25 over title, abstract, claims where
  present, and CPC codes. It will miss prior art that describes the same
  invention in different words. Embeddings would help and are not implemented.
- **The EPO OPS client is unproven against a live account.** See
  [Going live](#going-live). Treat the first real run as the verification step
  it is.
- **Free-tier quotas are real.** 30 searches/minute and 4 GB/week. A large
  `--limit` pages through the API at three seconds a page.
- **Summary quality tracks model size.** A small local model writes a blander
  landscape summary than a large one.

## Tests

```bash
pytest                    # everything
pytest -m "not network"   # skip the live-API test explicitly
```

`tests/test_sources_live.py` is marked `network` **and** skips itself cleanly
when `EPO_OPS_KEY` / `EPO_OPS_SECRET` are unset — it never fails or errors for a
missing key. With credentials set it authenticates for real, runs a real search,
and asserts the parsed records are well-formed, so a silent upstream change
surfaces as a failing test rather than as empty results.

Everything else runs offline against fixtures.

## License

MIT.
