"""Parsing and snapshot plumbing — no network."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
import requests

from patentpulse.sources import (
    FIXTURE_PATH,
    EpoOpsSource,
    FixtureSource,
    PatentDataError,
    PatentRecord,
    Snapshot,
    _as_list,
    _cpc_code,
    _ops_date,
    _text,
    load_snapshot,
    patent_url,
    save_snapshot,
)

# One `exchange-document` in the shape EPO OPS's JSON rendering produces:
# `@`-prefixed attributes, `$` for text, single-element arrays collapsed to
# bare objects. Shape is documented in EpoOpsSource's docstring as unverified
# against a live key — this fixture is what the parser is written to.
EXCHANGE_DOC = {
    "@system": "ops.epo.org",
    "@family-id": "54262186",
    "@country": "US",
    "@doc-number": "9455665",
    "@kind": "B2",
    "bibliographic-data": {
        "publication-reference": {
            "document-id": [
                {
                    "@document-id-type": "docdb",
                    "country": {"$": "US"},
                    "doc-number": {"$": "9455665"},
                    "kind": {"$": "B2"},
                    "date": {"$": "20160927"},
                },
                {"@document-id-type": "epodoc", "doc-number": {"$": "US9455665"}},
            ]
        },
        "application-reference": {
            "document-id": {
                "@document-id-type": "docdb",
                "country": {"$": "US"},
                "doc-number": {"$": "201414419793"},
                "kind": {"$": "A"},
                "date": {"$": "20140807"},
            }
        },
        "invention-title": [
            {"@lang": "de", "$": "REINIGUNGSVORRICHTUNG FUER SOLARMODULE"},
            {"@lang": "en", "$": "Solar panel cleaning system"},
        ],
        "parties": {
            "applicants": {
                "applicant": [
                    {
                        "@sequence": "1",
                        "@data-format": "docdb",
                        "applicant-name": {"name": {"$": "ECOPPIA SCIENTIFIC LTD"}},
                    }
                ]
            }
        },
        "patent-classifications": {
            "patent-classification": [
                {
                    "classification-scheme": {"@office": "", "@scheme": "CPCI"},
                    "section": {"$": "H"},
                    "class": {"$": "02"},
                    "subclass": {"$": "S"},
                    "main-group": {"$": "40"},
                    "subgroup": {"$": "10"},
                },
                {
                    "classification-scheme": {"@office": "", "@scheme": "CPCA"},
                    "section": {"$": "B"},
                    "class": {"$": "08"},
                    "subclass": {"$": "B"},
                    "main-group": {"$": "1"},
                    "subgroup": {"$": "002"},
                },
                # IPC entries share the block and must not become CPC codes.
                {
                    "classification-scheme": {"@office": "", "@scheme": "IPC"},
                    "section": {"$": "H"},
                    "class": {"$": "02"},
                    "subclass": {"$": "S"},
                    "main-group": {"$": "40"},
                    "subgroup": {"$": "10"},
                },
            ]
        },
    },
    "abstract": {"@lang": "en", "p": {"$": "A cleaning device for photovoltaic panels."}},
}


def _envelope(*docs) -> dict:
    return {
        "ops:world-patent-data": {
            "ops:biblio-search": {
                "@total-result-count": str(len(docs)),
                "ops:search-result": {
                    "exchange-documents": [{"exchange-document": d} for d in docs]
                },
            }
        }
    }


# -- OPS JSON helpers ------------------------------------------------------


def test_as_list_undoes_ops_single_element_collapsing():
    assert _as_list([1, 2]) == [1, 2]
    assert _as_list({"a": 1}) == [{"a": 1}]
    assert _as_list(None) == []


def test_text_reads_the_dollar_convention():
    assert _text({"$": " hi "}) == "hi"
    assert _text("hi") == "hi"
    assert _text(None) == ""


@pytest.mark.parametrize(
    "value,expected",
    [
        ("20160927", "2016-09-27"),
        ("  20160927 ", "2016-09-27"),
        # A changed upstream format must yield None, never a plausible-looking
        # string that violates the YYYY-MM-DD contract.
        ("2016-09-27", None),
        ("20161345", None),
        ("201609", None),
        ("", None),
        (None, None),
        (20160927, None),
    ],
)
def test_ops_date(value, expected):
    assert _ops_date(value) == expected


def test_cpc_code_reassembles_the_exploded_symbol():
    """Inventive (CPCI) and additional (CPCA) entries both count as CPC."""
    parts = EXCHANGE_DOC["bibliographic-data"]["patent-classifications"]["patent-classification"]
    assert _cpc_code(parts[0]) == "H02S40/10"
    assert _cpc_code(parts[1]) == "B08B1/002"


def test_cpc_code_ignores_non_cpc_schemes():
    parts = EXCHANGE_DOC["bibliographic-data"]["patent-classifications"]["patent-classification"]
    assert _cpc_code(parts[2]) is None  # IPC


def test_cpc_code_drops_incomplete_symbols():
    assert _cpc_code({"classification-scheme": {"@scheme": "CPC"}, "section": {"$": "H"}}) is None


# -- OPS record parsing ----------------------------------------------------


def test_to_record_maps_an_ops_exchange_document():
    record = EpoOpsSource(key="k", secret="s")._to_record(EXCHANGE_DOC)
    assert record is not None
    assert record.patent_id == "US9455665B2"
    assert record.title == "Solar panel cleaning system"  # English preferred over German
    assert record.abstract == "A cleaning device for photovoltaic panels."
    assert record.assignee == "ECOPPIA SCIENTIFIC LTD"
    assert record.grant_date == "2016-09-27"
    assert record.filing_date == "2014-08-07"
    assert record.cpc_codes == ["H02S40/10", "B08B1/002"]
    assert record.url == "https://patents.google.com/patent/US9455665B2/en"


def test_to_record_leaves_claim_summary_empty_rather_than_faking_it():
    """OPS search results carry no claim text. Empty is honest; invented is not."""
    assert EpoOpsSource(key="k", secret="s")._to_record(EXCHANGE_DOC).claim_summary == ""


def test_to_record_rejects_documents_without_an_id_or_date():
    source = EpoOpsSource(key="k", secret="s")
    no_date = {"bibliographic-data": {"publication-reference": {"document-id": {
        "@document-id-type": "docdb", "country": {"$": "US"}, "doc-number": {"$": "1"}}}}}
    assert source._to_record(no_date) is None
    assert source._to_record({"bibliographic-data": {}}) is None


def test_to_record_survives_a_document_with_no_abstract_or_applicant():
    thin = json.loads(json.dumps(EXCHANGE_DOC))
    del thin["abstract"]
    del thin["bibliographic-data"]["parties"]
    record = EpoOpsSource(key="k", secret="s")._to_record(thin)
    assert record.abstract == ""
    assert record.assignee is None
    assert record.title


def test_records_from_walks_the_ops_envelope():
    records = EpoOpsSource(key="k", secret="s")._records_from(_envelope(EXCHANGE_DOC))
    assert [r.patent_id for r in records] == ["US9455665B2"]


def test_records_from_tolerates_an_empty_or_unexpected_envelope():
    source = EpoOpsSource(key="k", secret="s")
    assert source._records_from({}) == []
    assert source._records_from({"ops:world-patent-data": {}}) == []


def test_records_from_counts_documents_it_could_not_parse():
    source = EpoOpsSource(key="k", secret="s")
    unparsable = {"bibliographic-data": {}}  # no id/date; _to_record returns None
    records = source._records_from(_envelope(EXCHANGE_DOC, unparsable))
    assert [r.patent_id for r in records] == ["US9455665B2"]
    assert source._dropped_count == 1


def test_search_text_covers_every_indexed_field():
    text = EpoOpsSource(key="k", secret="s")._to_record(EXCHANGE_DOC).search_text()
    assert "Solar panel cleaning" in text
    assert "photovoltaic" in text
    assert "H02S40/10" in text


# -- OPS credentials and auth ---------------------------------------------


def test_credentials_come_from_the_environment_and_are_never_hardcoded(monkeypatch):
    monkeypatch.setenv("EPO_OPS_KEY", "env-key")
    monkeypatch.setenv("EPO_OPS_SECRET", "env-secret")
    source = EpoOpsSource()
    assert (source.key, source.secret) == ("env-key", "env-secret")
    assert EpoOpsSource.credentials_present() is True


def test_credentials_present_is_false_when_either_var_is_missing(monkeypatch):
    monkeypatch.delenv("EPO_OPS_KEY", raising=False)
    monkeypatch.delenv("EPO_OPS_SECRET", raising=False)
    assert EpoOpsSource.credentials_present() is False
    monkeypatch.setenv("EPO_OPS_KEY", "only-the-key")
    assert EpoOpsSource.credentials_present() is False


def test_missing_credentials_fail_with_a_pointer_to_the_signup(monkeypatch):
    monkeypatch.delenv("EPO_OPS_KEY", raising=False)
    monkeypatch.delenv("EPO_OPS_SECRET", raising=False)
    with pytest.raises(PatentDataError, match="developers.epo.org"):
        EpoOpsSource()._authenticate()


class _Resp:
    def __init__(self, status=200, body=None, headers=None):
        self.status_code = status
        self._body = body if body is not None else {}
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._body


def _session(post=None, get=None):
    return type("S", (), {"post": lambda self, *a, **k: post, "get": lambda self, *a, **k: get})()


def test_authenticate_sends_basic_auth_and_the_client_credentials_grant():
    captured = {}

    class _S:
        def post(self, url, data=None, headers=None, timeout=None):
            captured.update(url=url, data=data, headers=headers)
            return _Resp(body={"access_token": "tok", "expires_in": "1200"})

    source = EpoOpsSource(key="abc", secret="xyz", session=_S())
    assert source._authenticate() == "tok"
    assert captured["url"].endswith("/auth/accesstoken")
    assert captured["data"] == {"grant_type": "client_credentials"}
    # base64("abc:xyz")
    assert captured["headers"]["Authorization"] == "Basic YWJjOnh5eg=="


def test_authenticate_reuses_a_live_token_instead_of_re_authing():
    calls = []

    class _S:
        def post(self, *a, **k):
            calls.append(1)
            return _Resp(body={"access_token": "tok", "expires_in": "1200"})

    source = EpoOpsSource(key="a", secret="b", session=_S())
    source._authenticate()
    source._authenticate()
    assert len(calls) == 1


def test_bad_credentials_say_so_rather_than_raising_a_bare_http_error():
    source = EpoOpsSource(key="a", secret="b", session=_session(post=_Resp(401)))
    with pytest.raises(PatentDataError, match="rejected the credentials"):
        source._authenticate()


def test_a_token_response_without_a_token_is_an_error():
    source = EpoOpsSource(key="a", secret="b", session=_session(post=_Resp(body={"ok": True})))
    with pytest.raises(PatentDataError, match="no access_token"):
        source._authenticate()


# -- OPS search transport --------------------------------------------------


def _authed(get_response):
    source = EpoOpsSource(key="a", secret="b", session=_session(get=get_response))
    source._token = "tok"
    source._token_expires_at = float("inf")
    return source


def test_search_page_sends_the_bearer_token_the_query_and_the_ops_range_header():
    captured = {}

    class _S:
        def get(self, url, params=None, headers=None, timeout=None):
            captured.update(url=url, params=params, headers=headers)
            return _Resp(body=_envelope(EXCHANGE_DOC))

    source = EpoOpsSource(key="a", secret="b", session=_S())
    source._token, source._token_expires_at = "tok", float("inf")
    source._search_page('pd within "1 2"', 1, 100)
    assert captured["params"] == {"q": 'pd within "1 2"'}
    assert captured["headers"]["Authorization"] == "Bearer tok"
    assert captured["headers"]["X-OPS-Range"] == "1-100"
    assert captured["headers"]["Accept"] == "application/json"


def test_a_404_is_no_results_not_a_failure():
    assert _authed(_Resp(404))._search_page("q", 1, 100) == {}


@pytest.mark.parametrize("status", [403, 429])
def test_quota_rejections_surface_the_reason_ops_gives(status):
    source = _authed(_Resp(status, headers={"X-Rejection-Reason": "IndividualQuotaPerHour"}))
    with pytest.raises(PatentDataError, match="IndividualQuotaPerHour"):
        source._search_page("q", 1, 100)


def test_cql_windows_on_publication_date_and_country():
    from datetime import date

    cql = EpoOpsSource(key="a", secret="b")._cql(date(2026, 7, 1), date(2026, 8, 19))
    assert cql == 'pn=US and pd within "20260701 20260819"'
    unfiltered = EpoOpsSource(key="a", secret="b", country=None)
    assert unfiltered._cql(date(2026, 7, 1), date(2026, 8, 19)) == 'pd within "20260701 20260819"'


# -- OPS fetch orchestration, network faked out ---------------------------


def _fake_ops(pages):
    source = EpoOpsSource(key="a", secret="b")
    source._token, source._token_expires_at = "tok", float("inf")
    calls = iter(pages)
    source._search_page = lambda cql, begin, end: next(calls, {})
    return source


def _doc(number):
    doc = json.loads(json.dumps(EXCHANGE_DOC))
    doc["bibliographic-data"]["publication-reference"]["document-id"][0]["doc-number"] = {
        "$": number
    }
    return doc


@pytest.mark.parametrize("kwargs", [{"window_days": 0}, {"limit": 0}])
def test_fetch_rejects_nonsense_bounds(kwargs):
    with pytest.raises(ValueError):
        EpoOpsSource(key="a", secret="b").fetch(**kwargs)


def test_fetch_errors_when_the_window_holds_no_publications():
    with pytest.raises(PatentDataError, match="no publications"):
        _fake_ops([{}]).fetch(window_days=30, limit=5)


def test_fetch_deduplicates_and_never_exceeds_the_limit():
    page = _envelope(*[_doc(str(i)) for i in range(5)])
    repeat = _envelope(_doc("0"), _doc("1"))  # a server that ignores Range
    snapshot = _fake_ops([page, repeat, {}]).fetch(window_days=30, limit=8)
    ids = [r.patent_id for r in snapshot.records]
    assert len(ids) == len(set(ids)) == 5


def test_fetch_truncates_to_the_requested_limit():
    page = _envelope(*[_doc(str(i)) for i in range(10)])
    assert len(_fake_ops([page]).fetch(window_days=30, limit=3).records) == 3


def test_fetch_reports_the_date_range_it_actually_got():
    early, late = _doc("A"), _doc("B")
    early["bibliographic-data"]["publication-reference"]["document-id"][0]["date"] = {
        "$": "20260804"
    }
    late["bibliographic-data"]["publication-reference"]["document-id"][0]["date"] = {"$": "20260818"}
    snapshot = _fake_ops([_envelope(early, late)]).fetch(window_days=30, limit=2)
    assert snapshot.date_range == ("2026-08-04", "2026-08-18")
    assert snapshot.source == "epo_ops"
    assert "Open Patent Services" in snapshot.note


def test_fetch_notes_and_logs_records_it_had_to_drop(caplog):
    page = _envelope(_doc("0"), {"bibliographic-data": {}})  # one good, one unparsable
    with caplog.at_level("WARNING"):
        snapshot = _fake_ops([page, {}]).fetch(window_days=30, limit=5)
    assert len(snapshot.records) == 1
    assert "1 record(s)" in snapshot.note and "dropped" in snapshot.note
    assert any("dropped" in r.message for r in caplog.records)


# -- the committed fixture -------------------------------------------------


def test_the_shipped_fixture_parses_into_real_looking_records():
    snapshot = FixtureSource().fetch()
    assert 5 <= len(snapshot.records) <= 10
    assert snapshot.source == "fixture_sample"
    for record in snapshot.records:
        assert record.patent_id.startswith("US")
        assert record.title and len(record.abstract) > 40
        assert len(record.grant_date) == 10 and record.grant_date[4] == "-"
        assert record.filing_date is None or len(record.filing_date) == 10
        assert record.cpc_codes
        assert record.url == patent_url(record.patent_id)


def test_the_fixture_never_pretends_to_be_a_live_search():
    """The whole point: a payload built from this must read as demo data."""
    note = FixtureSource().fetch().note
    assert "DEMO DATA" in note
    assert "not a live patent search" in note


def test_fixture_source_honours_the_limit():
    assert len(FixtureSource().fetch(limit=2).records) == 2


def test_a_missing_fixture_file_is_a_clear_error(tmp_path):
    with pytest.raises(PatentDataError, match="sample fixture"):
        FixtureSource(path=tmp_path / "gone.json").fetch()


def test_an_empty_fixture_file_is_refused(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"records": []}))
    with pytest.raises(PatentDataError, match="no records"):
        FixtureSource(path=path).fetch()


def test_the_fixture_ships_inside_the_package():
    assert FIXTURE_PATH.is_file()


# -- snapshot cache --------------------------------------------------------


def test_snapshot_round_trips_through_disk(tmp_path):
    snapshot = Snapshot(
        records=[EpoOpsSource(key="a", secret="b")._to_record(EXCHANGE_DOC)],
        fetched_at="2026-08-19T00:00:00Z",
        date_range=("2016-09-27", "2016-09-27"),
        source="epo_ops",
        note="from EPO OPS",
    )
    path = save_snapshot(snapshot, tmp_path / "snap.json")
    reloaded = load_snapshot(path)
    assert reloaded is not None
    assert reloaded.date_range == ("2016-09-27", "2016-09-27")
    assert reloaded.source == "epo_ops"
    assert reloaded.note == "from EPO OPS"
    assert reloaded.records[0].patent_id == "US9455665B2"
    assert reloaded.records[0].cpc_codes == ["H02S40/10", "B08B1/002"]


def test_load_snapshot_returns_none_only_when_there_is_no_cache(tmp_path):
    assert load_snapshot(tmp_path / "nope.json") is None


def test_corrupt_snapshot_raises_instead_of_looking_like_no_snapshot(tmp_path):
    """A broken cache must not quietly trigger a refetch."""
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(PatentDataError, match="unreadable"):
        load_snapshot(bad)


def test_a_snapshot_with_a_one_element_date_range_raises_cleanly(tmp_path):
    """A hand-mangled cache with a short date_range must not raise a raw IndexError."""
    bad = tmp_path / "short_range.json"
    bad.write_text(json.dumps({"date_range": ["2016-09-27"], "records": []}))
    with pytest.raises(PatentDataError, match="unreadable"):
        load_snapshot(bad)


def test_a_snapshot_with_no_source_key_reports_unknown_not_a_guess(tmp_path):
    """An old cache must not inherit a source label it never had."""
    path = tmp_path / "old.json"
    path.write_text(
        json.dumps(
            {
                "fetched_at": "2026-08-19T00:00:00Z",
                "date_range": ["2016-09-27", "2016-09-27"],
                "records": [
                    EpoOpsSource(key="a", secret="b")._to_record(EXCHANGE_DOC).to_dict()
                ],
            }
        )
    )
    assert load_snapshot(path).source == "unknown"


def test_record_from_dict_coerces_missing_text_fields_to_strings():
    """A record from an older schema must stay indexable, not blow up later."""
    record = PatentRecord.from_dict({"patent_id": "US-1", "grant_date": "2026-08-18"})
    assert record.title == "" and record.abstract == "" and record.url == ""
    assert record.search_text().strip() == ""  # would TypeError if any were None


def test_record_from_dict_rejects_records_with_no_identity():
    with pytest.raises(ValueError, match="patent_id or grant_date"):
        PatentRecord.from_dict({"title": "orphan"})


def test_record_round_trips_through_json():
    original = EpoOpsSource(key="a", secret="b")._to_record(EXCHANGE_DOC)
    clone = PatentRecord.from_dict(json.loads(json.dumps(original.to_dict())))
    assert clone == original


# -- no trace of the removed scraper --------------------------------------


def test_the_ppubs_scraper_is_gone_from_the_package():
    """Compliance guard: the reverse-engineered PPUBS path must not come back.

    It spoofed a browser User-Agent past USPTO's WAF to reach an undocumented
    internal endpoint, which USPTO's Terms of Use prohibit. This test fails if
    any of its machinery is reintroduced, anywhere in the repo (not just the
    Python package) — a reintroduction in web/, a shell script, etc. is just
    as much a violation. (Prose *about* why it was removed is fine — what is
    banned is the mechanism. README.md legitimately discusses it, so it and
    this test file are excluded.)
    """
    from pathlib import Path

    repo_root = Path(__file__).resolve().parent.parent
    this_file = Path(__file__).resolve()
    excluded_dirs = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "node_modules"}
    banned = (
        "ppubs.uspto.gov",
        "X-Access-Token",
        "ANONYMOUS_USER",
        "searchWithBeFamily",
        "api/users/me/session",
        "Mozilla/5.0",
        "playwright",
    )
    offences = [
        f"{path.relative_to(repo_root)}: {word}"
        for ext in ("*.py", "*.js", "*.sh")
        for path in repo_root.rglob(ext)
        if not excluded_dirs & set(path.relative_to(repo_root).parts)
        if path.resolve() != this_file
        for word in banned
        if word.lower() in path.read_text().lower()
    ]
    assert not offences, offences


def test_no_credentials_are_baked_into_the_source():
    from pathlib import Path

    import patentpulse.sources as mod

    text = Path(mod.__file__).read_text()
    assert 'os.environ.get("EPO_OPS_KEY"' in text
    assert 'os.environ.get("EPO_OPS_SECRET"' in text


def test_scan_picks_the_source_from_the_environment(monkeypatch):
    from patentpulse import scan as scan_mod

    monkeypatch.delenv("EPO_OPS_KEY", raising=False)
    monkeypatch.delenv("EPO_OPS_SECRET", raising=False)
    assert isinstance(scan_mod.get_source(), FixtureSource)
    monkeypatch.setenv("EPO_OPS_KEY", "k")
    monkeypatch.setenv("EPO_OPS_SECRET", "s")
    assert isinstance(scan_mod.get_source(), EpoOpsSource)


def test_a_cache_from_another_source_is_not_served_as_the_current_one(monkeypatch, tmp_path):
    """Adding EPO credentials must not keep serving yesterday's fixture demo."""
    from patentpulse import scan as scan_mod

    monkeypatch.setenv("EPO_OPS_KEY", "k")
    monkeypatch.setenv("EPO_OPS_SECRET", "s")
    stale = Snapshot(
        records=[PatentRecord("US-1", "t", "a", "", None, "2026-08-18", None, [], "")],
        fetched_at="x",
        date_range=("2026-08-18", "2026-08-18"),
        source="fixture_sample",
    )
    fresh = Snapshot(records=stale.records, fetched_at="y", date_range=("a", "b"), source="epo_ops")
    with patch("patentpulse.scan.load_snapshot", return_value=stale), patch(
        "patentpulse.scan.save_snapshot"
    ), patch.object(EpoOpsSource, "fetch", return_value=fresh) as fetched:
        assert scan_mod.ensure_snapshot().source == "epo_ops"
    fetched.assert_called_once()
