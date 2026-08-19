"""Where patent records come from.

`PatentDataSource` is the seam. Two implementations live here:

* `FixtureSource` — what runs today. A small, committed set of real,
  hand-verified patent records. Demo data, honestly labelled as such: it is a
  fixture, never a live search.
* `EpoOpsSource` — EPO Open Patent Services (OPS) v3.2, the European Patent
  Office's free, self-service developer API. Activates the moment
  `EPO_OPS_KEY` and `EPO_OPS_SECRET` are in the environment.

Why EPO OPS and not a USPTO source — the short version (README has the long one):

* USPTO Patent Public Search is a public *web tool*, not an API. Driving it
  from a script means spoofing a browser User-Agent past a WAF
  and calling an undocumented internal session endpoint. USPTO's Terms of Use
  say their online databases "are not designed or intended to be a source for
  bulk downloads ... when accessed through the website's interfaces", and flag
  unauthorized access as potentially prosecutable under 18 U.S.C. § 1030. So it
  is gone from this codebase — not flagged off, not a fallback. Deleted.
* USPTO's Open Data Portal gated *all* access, bulk file downloads included,
  behind a signed-in USPTO.gov account on 2026-06-18.

EPO OPS is the opposite case: a documented, free, self-service API whose entire
purpose is third-party programmatic patent search. Registration is an email and
a password at https://developers.epo.org/ — no identity verification.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

CACHE_DIR = Path.home() / ".patentpulse"
SNAPSHOT_PATH = CACHE_DIR / "snapshot.json"
FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "sample_patents.json"

DEFAULT_WINDOW_DAYS = 45
DEFAULT_LIMIT = 250

logger = logging.getLogger(__name__)


class PatentDataError(Exception):
    """Raised when a data source cannot produce records."""


@dataclass
class PatentRecord:
    """One patent publication, trimmed to what a prior-art scan actually needs."""

    patent_id: str
    title: str
    abstract: str
    claim_summary: str
    assignee: str | None
    grant_date: str
    filing_date: str | None
    cpc_codes: list[str] = field(default_factory=list)
    url: str = ""

    def search_text(self) -> str:
        """The text this record is indexed on."""
        return " ".join([self.title, self.abstract, self.claim_summary, " ".join(self.cpc_codes)])

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> PatentRecord:
        """Rebuild a record from cached JSON, tolerating an older schema.

        Text fields are coerced to "" rather than left as None — a None here
        would blow up indexing with a bare TypeError much later.
        """
        if not data.get("patent_id") or not data.get("grant_date"):
            raise ValueError("cached patent record is missing patent_id or grant_date")
        known = {f: data.get(f) for f in cls.__dataclass_fields__}
        for text_field in ("title", "abstract", "claim_summary", "url"):
            known[text_field] = known.get(text_field) or ""
        known["cpc_codes"] = list(known.get("cpc_codes") or [])
        return cls(**known)


@dataclass
class Snapshot:
    """A cached slice of patent records plus the provenance to describe it."""

    records: list[PatentRecord]
    fetched_at: str
    date_range: tuple[str, str]
    #: Matches the producing source's `source_id`; surfaces as `scope.source`.
    source: str
    #: Plain-English provenance, opens `scope.note` in the payload. Travels with
    #: the cache so a stale snapshot describes where *it* came from, not where
    #: the currently configured source would fetch from.
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "fetched_at": self.fetched_at,
            "date_range": list(self.date_range),
            "source": self.source,
            "note": self.note,
            "records": [r.to_dict() for r in self.records],
        }

    @classmethod
    def from_dict(cls, data: dict) -> Snapshot:
        rng = data.get("date_range") or ["", ""]
        return cls(
            records=[PatentRecord.from_dict(r) for r in data.get("records", [])],
            fetched_at=data.get("fetched_at", ""),
            date_range=(rng[0], rng[1]),
            source=data.get("source") or "unknown",
            note=data.get("note") or "",
        )


class PatentDataSource(ABC):
    """A place real patent records can be read from."""

    #: Value reported in the scan payload's `scope.source`.
    source_id: str = "unknown"
    #: Opening sentence of the payload's `scope.note`.
    source_note: str = ""

    @abstractmethod
    def fetch(
        self,
        window_days: int = DEFAULT_WINDOW_DAYS,
        limit: int = DEFAULT_LIMIT,
        progress: Callable[[int, int], None] | None = None,
    ) -> Snapshot:
        """Return up to `limit` records published within the last `window_days`."""


def patent_url(patent_id: str) -> str:
    """A public, no-login page for a publication number.

    Google Patents is used rather than Espacenet because its URL is a plain
    function of the publication number and the page needs no session. This is a
    link for a human to click; the *data* comes from EPO OPS.
    """
    return f"https://patents.google.com/patent/{patent_id}/en"


# -- fixture source --------------------------------------------------------


class FixtureSource(PatentDataSource):
    """Real patent records, hand-verified and committed — a demo, not a search.

    Every record in `fixtures/sample_patents.json` is a genuine granted US
    patent, transcribed field-by-field from its public Google Patents page. It
    is real data about real patents. It is emphatically *not* a live search:
    the set is fixed, tiny, and chosen by hand, so a scan against it says
    nothing about how crowded any technical area actually is.

    This exists so PatentPulse runs end to end today, before the EPO OPS
    credentials exist. `scope.source` reports `fixture_sample` and `scope.note`
    says so in plain English, so no consumer of the payload can mistake it for
    a real search.
    """

    source_id = "fixture_sample"
    source_note = (
        "DEMO DATA — not a live patent search. These {count} records are real, granted "
        "US patents, each transcribed by hand from its public Google Patents page and "
        "committed to this repository as a fixture. They were picked to exercise the "
        "ranking end to end, not to represent any technical field. Live search over EPO "
        "Open Patent Services switches on automatically once EPO_OPS_KEY and "
        "EPO_OPS_SECRET are set in the environment."
    )

    def __init__(self, path: Path = FIXTURE_PATH) -> None:
        self.path = path

    def fetch(
        self,
        window_days: int = DEFAULT_WINDOW_DAYS,
        limit: int = DEFAULT_LIMIT,
        progress: Callable[[int, int], None] | None = None,
    ) -> Snapshot:
        """Load the fixture. `window_days` is ignored — the set is fixed.

        Ignoring `window_days` rather than filtering on it is deliberate: these
        patents span 1997-2016, so any honest recent window would return zero
        records and the demo would show nothing.
        """
        if limit < 1:
            raise ValueError("limit must be at least 1")
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError) as exc:
            raise PatentDataError(f"Could not read the sample fixture at {self.path}: {exc}") from exc

        records = [PatentRecord.from_dict(r) for r in data.get("records", [])][:limit]
        if not records:
            raise PatentDataError(f"The sample fixture at {self.path} contains no records.")
        if progress:
            progress(len(records), len(records))

        days = sorted(r.grant_date for r in records)
        return Snapshot(
            records=records,
            fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            date_range=(days[0], days[-1]),
            source=self.source_id,
            note=self.source_note.format(count=len(records)),
        )


# -- EPO Open Patent Services ----------------------------------------------

# Shapes below follow the OPS RESTful Web Services Reference Guide v1.3.20
# (June 2024): auth §2.3.2 p.35, JSON/BadgerFish §2.2 pp.16-17, published-data
# search §3.1.1 pp.57-60, throttling §2.3.3 pp.39-44, fault codes Table 15.
OPS_BASE = "https://ops.epo.org/3.2"
OPS_TOKEN_URL = f"{OPS_BASE}/auth/accesstoken"
# Constituents are a comma-separated path segment: `biblio` gives titles, dates,
# applicants and classifications, `abstract` adds the abstract text. Both in one
# call — OPS needs no separate per-document fetch for these.
OPS_SEARCH_URL = f"{OPS_BASE}/rest-services/published-data/search/abstract,biblio"

# "The maximum range is 100. The maximum total number of results is 2000."
_OPS_PAGE = 100
_OPS_MAX_RESULTS = 2000
# The search throttle bucket allows 30 requests/minute when OPS is idle, 15 when
# busy and 5 when overloaded. Three seconds between pages (20/min) sits under the
# idle ceiling; a busy-state 403 is reported rather than retried around.
# ponytail: fixed pace. Read X-Throttling-Control and adapt if this trips often.
_OPS_PAGE_INTERVAL = 3.0
# Refresh the token this many seconds before OPS says it expires. OPS tokens live
# ~20 minutes and an expired one comes back as HTTP 400 `invalid_access_token`,
# not a 401, so the cheap fix is to never let one get that old.
_TOKEN_SKEW = 60.0


def _as_list(value) -> list:
    """OPS JSON collapses single-element arrays into bare objects. Undo that."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _text(node) -> str:
    """Pull the text out of an OPS JSON node, which may be `{"$": "..."}`."""
    if isinstance(node, dict):
        return str(node.get("$", "")).strip()
    if isinstance(node, str):
        return node.strip()
    return ""


def _ops_date(raw: str | None) -> str | None:
    """OPS dates are bare `YYYYMMDD`. Returns None rather than a malformed date.

    The JSON contract promises YYYY-MM-DD, so an upstream format change must
    surface as a missing date, never as garbage that looks like one.
    """
    if not raw or not isinstance(raw, str):
        return None
    raw = raw.strip()
    try:
        return datetime.strptime(raw, "%Y%m%d").strftime("%Y-%m-%d")
    except ValueError:
        return None


def _cpc_code(classification: dict) -> str | None:
    """Reassemble one CPC symbol from OPS's exploded parts -> `H02S40/10`."""
    scheme = classification.get("classification-scheme") or {}
    if isinstance(scheme, list):
        scheme = scheme[0] if scheme else {}
    # Inventive and additional CPC entries are tagged CPCI and CPCA, so this is
    # a prefix test, not equality. The same block also carries IPC entries.
    if not str(scheme.get("@scheme", "")).startswith("CPC"):
        return None
    parts = [_text(classification.get(k)) for k in ("section", "class", "subclass", "main-group")]
    subgroup = _text(classification.get("subgroup"))
    if not all(parts) or not subgroup:
        return None
    return f"{''.join(parts)}/{subgroup}"


def _document_id(reference: dict, kind: str = "docdb") -> dict:
    """Pick one `document-id` block out of a publication/application reference."""
    for doc in _as_list((reference or {}).get("document-id")):
        if doc.get("@document-id-type") == kind:
            return doc
    return {}


class EpoOpsSource(PatentDataSource):
    """Published-data search against EPO Open Patent Services v3.2.

    OAuth2 client credentials in, bearer token out, then a paged CQL search.
    Credentials come from `EPO_OPS_KEY` and `EPO_OPS_SECRET` — they are never
    hardcoded, never prompted for, and never written to disk.

    Register for free at https://developers.epo.org/ (email and password; no
    identity verification), create an app, and copy its consumer key/secret.

    NOT YET RUN AGAINST A LIVE ACCOUNT. Every shape here is written to EPO's
    "OPS RESTful Web Services Reference Guide" v1.3.20 (June 2024) and
    cross-checked against captured real OPS responses and several long-lived
    open-source OPS clients — but no call in this class has been executed with
    real credentials. What that means per piece:

    * Documented and cross-checked: the token exchange, `Accept: application/json`
      (OPS renders any XML service as BadgerFish JSON — `$` for text, `@` for
      attributes, single elements *not* wrapped in arrays, which is why
      `_as_list` exists), the `ops:world-patent-data` envelope path, the
      exploded CPC symbol, `X-OPS-Range` paging, and 404-means-no-results.
    * Genuinely unverified: the `pn=<country>` clause in `_cql`. OPS's index
      catalogue has no country field; `pn=US` leans on publication-number
      prefix matching. If it comes back empty or as a CQL fault, try `pn=US*`
      (`*` is documented unlimited truncation), and failing that drop the
      clause and filter `patent_id` client-side.

    `tests/test_sources_live.py` exercises all of it against the real service
    the moment credentials are present — that is what it is for.
    """

    source_id = "epo_ops"
    source_note = (
        "Ranked against {count} patent records from EPO Open Patent Services (OPS) v3.2 "
        "published-data search — the European Patent Office's free developer API. OPS "
        "search results carry titles, abstracts, classifications, dates and applicants, "
        "but not claim text, so records are ranked on title, abstract and CPC codes."
    )

    def __init__(
        self,
        key: str | None = None,
        secret: str | None = None,
        country: str | None = "US",
        session: requests.Session | None = None,
    ) -> None:
        self.key = key or os.environ.get("EPO_OPS_KEY", "")
        self.secret = secret or os.environ.get("EPO_OPS_SECRET", "")
        self.country = country
        self._session = session or requests.Session()
        self._token: str | None = None
        self._token_expires_at = 0.0
        #: Records `_to_record` couldn't parse (no identity/date), counted per
        #: `fetch()` call so the drop is visible in the log and the snapshot
        #: note rather than silently shrinking the result set.
        self._dropped_count = 0

    @staticmethod
    def credentials_present() -> bool:
        return bool(os.environ.get("EPO_OPS_KEY") and os.environ.get("EPO_OPS_SECRET"))

    # -- auth --------------------------------------------------------------

    def _authenticate(self) -> str:
        """Client-credentials grant. Basic-auth the key/secret, get a bearer token."""
        if self._token and time.monotonic() < self._token_expires_at:
            return self._token
        if not self.key or not self.secret:
            raise PatentDataError(
                "EPO OPS credentials are missing. Set EPO_OPS_KEY and EPO_OPS_SECRET "
                "to the consumer key and secret of an app registered at "
                "https://developers.epo.org/ (free, email signup)."
            )
        basic = base64.b64encode(f"{self.key}:{self.secret}".encode()).decode()
        try:
            resp = self._session.post(
                OPS_TOKEN_URL,
                data={"grant_type": "client_credentials"},
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                timeout=60,
            )
        except requests.RequestException as exc:
            raise PatentDataError(f"Could not reach the EPO OPS token endpoint: {exc}") from exc
        if resp.status_code in (400, 401, 403):
            raise PatentDataError(
                f"EPO OPS rejected the credentials (HTTP {resp.status_code}). Check "
                "EPO_OPS_KEY and EPO_OPS_SECRET against the app at developers.epo.org."
            )
        try:
            resp.raise_for_status()
            body = resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise PatentDataError(f"EPO OPS token response was unusable: {exc}") from exc

        token = body.get("access_token")
        if not token:
            raise PatentDataError("EPO OPS returned no access_token — the auth API may have changed.")
        # `expires_in` is seconds, and OPS returns it as a string.
        try:
            lifetime = float(body.get("expires_in") or 0) or 1200.0
        except (TypeError, ValueError):
            lifetime = 1200.0
        self._token = token
        self._token_expires_at = time.monotonic() + max(lifetime - _TOKEN_SKEW, 0)
        return token

    # -- search ------------------------------------------------------------

    def _cql(self, start: date, end: date) -> str:
        """A CQL query for publications in a date window.

        `pd within "YYYYMMDD YYYYMMDD"` is OPS's documented publication-date
        range form. The country clause narrows to one office's publications;
        it is the least-verified part of this query — see the class docstring.
        """
        query = f'pd within "{start:%Y%m%d} {end:%Y%m%d}"'
        if self.country:
            query = f"pn={self.country} and {query}"
        return query

    def _search_page(self, cql: str, begin: int, end: int) -> dict:
        headers = {
            "Authorization": f"Bearer {self._authenticate()}",
            "Accept": "application/json",
            # OPS pages via X-OPS-Range, 1-based and inclusive. (A plain `Range`
            # header and a `Range=` query param also work, but the guide calls
            # the query-param form test-only, so use the documented header.)
            "X-OPS-Range": f"{begin}-{end}",
        }
        try:
            resp = self._session.get(
                OPS_SEARCH_URL, params={"q": cql}, headers=headers, timeout=120
            )
        except requests.RequestException as exc:
            raise PatentDataError(f"EPO OPS search request failed: {exc}") from exc

        if resp.status_code == 404:
            # OPS reports zero hits as a 404 SERVER.EntityNotFound fault, not as
            # a 200 with a zero count. An empty page is a normal end-of-results.
            return {}
        if resp.status_code in (403, 429):
            # Quota and throttle rejections both come back as 403 (OPS does not
            # use 429) and are indistinguishable without X-Rejection-Reason.
            reason = resp.headers.get("X-Rejection-Reason", "no reason given")
            raise PatentDataError(
                f"EPO OPS refused the request (HTTP {resp.status_code}, {reason}). "
                "The free tier is limited to 30 searches/minute and 4GB/week — "
                "wait and retry, or lower --limit."
            )
        try:
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise PatentDataError(f"EPO OPS search response was unusable: {exc}") from exc

    # -- parsing -----------------------------------------------------------

    def _records_from(self, body: dict) -> list[PatentRecord]:
        result = (
            (body.get("ops:world-patent-data") or {})
            .get("ops:biblio-search", {})
            .get("ops:search-result", {})
        )
        records = []
        for wrapper in _as_list(result.get("exchange-documents")):
            for doc in _as_list(wrapper.get("exchange-document") if isinstance(wrapper, dict) else None):
                record = self._to_record(doc)
                if record is not None:
                    records.append(record)
                else:
                    self._dropped_count += 1
        return records

    def _to_record(self, doc: dict) -> PatentRecord | None:
        biblio = doc.get("bibliographic-data") or {}
        publication = _document_id(biblio.get("publication-reference") or {})
        country = _text(publication.get("country")) or doc.get("@country", "")
        number = _text(publication.get("doc-number")) or doc.get("@doc-number", "")
        kind = _text(publication.get("kind")) or doc.get("@kind", "")
        grant_date = _ops_date(_text(publication.get("date")))
        if not (country and number) or not grant_date:
            # Without an identity or a date the record cannot honour the contract.
            return None

        titles = [t for t in _as_list(biblio.get("invention-title")) if isinstance(t, dict)]
        english = next((t for t in titles if t.get("@lang") == "en"), None)
        title = _text(english or (titles[0] if titles else None))

        abstracts = [a for a in _as_list(doc.get("abstract")) if isinstance(a, dict)]
        chosen = next((a for a in abstracts if a.get("@lang") == "en"), None) or (
            abstracts[0] if abstracts else {}
        )
        abstract = " ".join(_text(p) for p in _as_list(chosen.get("p"))).strip()

        applicants = ((biblio.get("parties") or {}).get("applicants") or {}).get("applicant")
        names = [
            _text((a.get("applicant-name") or {}).get("name"))
            for a in _as_list(applicants)
            if isinstance(a, dict)
        ]
        assignee = next((n for n in names if n), None)

        classifications = (biblio.get("patent-classifications") or {}).get("patent-classification")
        cpc_codes: list[str] = []
        for entry in _as_list(classifications):
            code = _cpc_code(entry) if isinstance(entry, dict) else None
            if code and code not in cpc_codes:
                cpc_codes.append(code)

        patent_id = f"{country}{number}{kind}"
        return PatentRecord(
            patent_id=patent_id,
            title=title,
            abstract=abstract,
            # OPS published-data search does not return claim text. Left empty
            # rather than faked; `source_note` tells the user.
            claim_summary="",
            assignee=assignee,
            grant_date=grant_date,
            filing_date=_ops_date(
                _text(_document_id(biblio.get("application-reference") or {}).get("date"))
            ),
            cpc_codes=cpc_codes,
            url=patent_url(patent_id),
        )

    # -- the fetch ---------------------------------------------------------

    def fetch(
        self,
        window_days: int = DEFAULT_WINDOW_DAYS,
        limit: int = DEFAULT_LIMIT,
        progress: Callable[[int, int], None] | None = None,
    ) -> Snapshot:
        if window_days < 1:
            raise ValueError("window_days must be at least 1")
        if limit < 1:
            raise ValueError("limit must be at least 1")
        limit = min(limit, _OPS_MAX_RESULTS)
        self._dropped_count = 0

        end = date.today()
        cql = self._cql(end - timedelta(days=window_days), end)

        records: list[PatentRecord] = []
        seen: set[str] = set()
        begin = 1
        while len(records) < limit and begin <= _OPS_MAX_RESULTS:
            page_end = min(begin + _OPS_PAGE - 1, _OPS_MAX_RESULTS)
            page = self._records_from(self._search_page(cql, begin, page_end))
            fresh = [r for r in page if r.patent_id not in seen]
            if not fresh:
                # Either the window is exhausted or paging is looping — more
                # requests will not produce more patents either way.
                break
            seen.update(r.patent_id for r in fresh)
            records.extend(fresh)
            if progress:
                progress(min(len(records), limit), limit)
            begin = page_end + 1
            if len(records) < limit:
                time.sleep(_OPS_PAGE_INTERVAL)
        records = records[:limit]

        if not records:
            raise PatentDataError(
                f"EPO OPS returned no publications for `{cql}`. Try a longer --window."
            )

        note = self.source_note.format(count=len(records))
        if self._dropped_count:
            # Envelope-level failures already raise (see _search_page); this is
            # the record-level case — a malformed individual document is
            # skipped rather than failing the whole fetch, so make the loss
            # visible instead of silently shrinking the snapshot.
            logger.warning(
                "EPO OPS response included %d record(s) that could not be parsed; dropped.",
                self._dropped_count,
            )
            note += f" {self._dropped_count} record(s) in the response could not be parsed and were dropped."

        days = sorted(r.grant_date for r in records)
        return Snapshot(
            records=records,
            fetched_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            date_range=(days[0], days[-1]),
            source=self.source_id,
            note=note,
        )


# -- snapshot cache --------------------------------------------------------


def load_snapshot(path: Path = SNAPSHOT_PATH) -> Snapshot | None:
    """Read the cached snapshot. None means "not fetched yet" — and only that.

    A cache that exists but cannot be read is a different problem from one that
    was never built, so it raises instead of quietly triggering a refetch.
    """
    if not path.is_file():
        return None
    try:
        return Snapshot.from_dict(json.loads(path.read_text()))
    except (ValueError, OSError, KeyError, TypeError, IndexError) as exc:
        raise PatentDataError(
            f"The snapshot at {path} is unreadable ({exc}). Delete it and run "
            "`patentpulse fetch` to rebuild."
        ) from exc


def save_snapshot(snapshot: Snapshot, path: Path = SNAPSHOT_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(snapshot.to_dict(), indent=1))
    return path
