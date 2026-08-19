"""The one test that talks to a real patent API.

Marked `network`, and skipped — not failed, not errored — unless `EPO_OPS_KEY`
and `EPO_OPS_SECRET` are set. `pytest -m "not network"` skips it regardless.

It exists because every other test in this suite runs against fixtures, so the
EPO OPS client is otherwise only ever checked against the shape we *believe* OPS
returns. Nothing in `EpoOpsSource` has been run against a live account yet; this
file is what turns that belief into evidence the moment credentials exist.

    export EPO_OPS_KEY=...      # consumer key from developers.epo.org
    export EPO_OPS_SECRET=...   # its secret
    pytest tests/test_sources_live.py -v

If it fails on first contact, the likely culprits are the three things flagged
as unverified in `EpoOpsSource`'s docstring: the JSON Accept header, the key
path through the response envelope, and the `pn=US` country clause.
"""

from __future__ import annotations

import pytest

from patentpulse.index import BM25Index
from patentpulse.sources import EpoOpsSource

pytestmark = [
    pytest.mark.network,
    pytest.mark.skipif(
        not EpoOpsSource.credentials_present(),
        reason="EPO_OPS_KEY / EPO_OPS_SECRET not set — register free at developers.epo.org",
    ),
]


@pytest.fixture(scope="module")
def live_snapshot():
    """A tiny real search: a handful of genuinely published patents."""
    try:
        return EpoOpsSource().fetch(window_days=60, limit=5)
    except Exception as exc:  # noqa: BLE001 - report, don't mask
        pytest.fail(
            f"Live EPO OPS fetch failed: {exc}\n"
            "See EpoOpsSource's docstring for the three shapes that were written "
            "from documentation rather than verified against a live key."
        )


def test_authentication_returns_a_usable_bearer_token():
    """Isolates the OAuth2 half: a credential problem should not look like a parse bug."""
    token = EpoOpsSource()._authenticate()
    assert isinstance(token, str) and len(token) > 10


def test_live_fetch_returns_real_records(live_snapshot):
    assert live_snapshot.records
    assert live_snapshot.source == "epo_ops"
    assert live_snapshot.fetched_at.endswith("Z")
    assert "Open Patent Services" in live_snapshot.note


def test_live_records_have_the_fields_the_contract_promises(live_snapshot):
    for record in live_snapshot.records:
        assert record.patent_id
        assert record.title
        assert len(record.grant_date) == 10 and record.grant_date[4] == "-"
        assert record.filing_date is None or len(record.filing_date) == 10
        assert record.assignee is None or isinstance(record.assignee, str)
        assert isinstance(record.cpc_codes, list)
        assert record.url.startswith("https://")


def test_the_country_filter_actually_filters(live_snapshot):
    """`pn=US` is the least-verified clause in the CQL — prove it does something."""
    assert all(r.patent_id.startswith("US") for r in live_snapshot.records), [
        r.patent_id for r in live_snapshot.records
    ]


def test_live_records_carry_real_abstract_text(live_snapshot):
    """The `abstract` constituent is the fragile half — assert it actually landed."""
    with_abstracts = [r for r in live_snapshot.records if r.abstract]
    assert with_abstracts, "no abstracts came back — check the search constituents"
    for record in with_abstracts:
        assert len(record.abstract) > 40
        assert "<" not in record.abstract


def test_live_records_carry_cpc_codes(live_snapshot):
    """CPC symbols are reassembled from OPS's exploded parts — verify the join."""
    coded = [r for r in live_snapshot.records if r.cpc_codes]
    assert coded, "no CPC codes parsed — check _cpc_code against the live shape"
    for code in coded[0].cpc_codes:
        assert "/" in code and code[0].isalpha()


def test_live_records_are_indexable(live_snapshot):
    index = BM25Index(live_snapshot.records)
    assert len(index) == len(live_snapshot.records)
    # Every real patent should score against a word from its own title.
    first = live_snapshot.records[0]
    term = max(first.title.split(), key=len)
    assert index.search(term, top_k=5), f"no hit for {term!r} from a record's own title"


def test_live_snapshot_date_range_is_ordered_and_recent(live_snapshot):
    start, end = live_snapshot.date_range
    assert start <= end
    assert start >= "2020-01-01"
