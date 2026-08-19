"""Scan orchestration and the pinned JSON contract."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from patentpulse import scan as scan_mod
from patentpulse.index import BM25Index
from patentpulse.ollama_client import OllamaConnectionError
from patentpulse.sources import PatentRecord, Snapshot

IDEA = "a robot that cleans dust off rooftop solar panels"


def _record(pid, title, abstract, cpc, assignee="Acme Corp"):
    return PatentRecord(
        patent_id=pid,
        title=title,
        abstract=abstract,
        claim_summary="A system comprising a controller.",
        assignee=assignee,
        grant_date="2026-08-18",
        filing_date="2024-02-02",
        cpc_codes=cpc,
        url=f"https://patents.google.com/patent/{pid}/en",
    )


@pytest.fixture
def snapshot():
    return Snapshot(
        records=[
            _record("US-1", "Robotic cleaner for photovoltaic solar panels",
                    "A robot traverses solar panels removing dust.", ["H02S40/10", "B08B1/00"]),
            _record("US-2", "Solar panel dust removal by electrostatic field",
                    "Electrostatic dust removal from solar panels.", ["H02S40/10"]),
            _record("US-3", "Bread proofing cabinet",
                    "A cabinet for proofing dough.", ["A21B3/00"], assignee=None),
        ],
        fetched_at="2026-08-19T00:00:00Z",
        date_range=("2026-08-18", "2026-08-18"),
        source="fixture_sample",
    )


@pytest.fixture
def fake_model():
    """Concepts call returns terms; summary call returns prose."""

    def _ask(model, prompt, host=None):
        if "search terms" in prompt:
            return "solar panel, dust removal, robotic cleaner, photovoltaic"
        return "Two of the three patents cover robotic solar panel cleaning."

    with patch.object(scan_mod.ollama_client, "ask", side_effect=_ask) as mock:
        yield mock


# -- concept extraction ----------------------------------------------------


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("solar panel, dust removal", ["solar panel", "dust removal"]),
        ("- solar panel\n- dust removal", ["solar panel", "dust removal"]),
        ("1. solar panel\n2. dust removal", ["solar panel", "dust removal"]),
        ("solar panel, Solar Panel, dust", ["solar panel", "dust"]),
        # A preamble the model was told not to emit must not become a concept.
        ("Here are the search terms: solar panel, dust", ["solar panel", "dust"]),
        ("solar panel, this is a whole explanatory sentence about panels", ["solar panel"]),
    ],
)
def test_extract_concepts_parses_whatever_the_model_emits(raw, expected):
    with patch.object(scan_mod.ollama_client, "ask", return_value=raw):
        assert scan_mod.extract_concepts(IDEA) == expected


def test_extract_concepts_raises_when_the_model_says_nothing():
    with patch.object(scan_mod.ollama_client, "ask", return_value="   "):
        with pytest.raises(scan_mod.ScanError, match="no usable concepts"):
            scan_mod.extract_concepts(IDEA)


def test_extract_concepts_is_capped():
    raw = ", ".join(f"term{i}" for i in range(40))
    with patch.object(scan_mod.ollama_client, "ask", return_value=raw):
        assert len(scan_mod.extract_concepts(IDEA)) == 10


# -- summary ---------------------------------------------------------------


def test_summary_with_no_results_does_not_claim_the_idea_is_novel():
    text = scan_mod.summarize_landscape(IDEA, [])
    assert "not evidence" in text


def test_summary_is_model_output_over_the_matched_records(snapshot, fake_model):
    payload = scan_mod.scan(IDEA, snapshot=snapshot)
    assert payload["landscape_summary"]["text"] == (
        "Two of the three patents cover robotic solar panel cleaning."
    )
    summary_prompt = fake_model.call_args_list[-1][0][1]
    # The model must be shown the actual matched patents, not a template.
    assert "US-1" in summary_prompt
    assert "Robotic cleaner for photovoltaic solar panels" in summary_prompt


def test_summary_raises_rather_than_templating_when_the_model_returns_nothing(snapshot):
    def _ask(model, prompt, host=None):
        return "solar panel, dust" if "search terms" in prompt else ""

    with patch.object(scan_mod.ollama_client, "ask", side_effect=_ask):
        with pytest.raises(scan_mod.ScanError, match="empty landscape summary"):
            scan_mod.scan(IDEA, snapshot=snapshot)


# -- the contract ----------------------------------------------------------


def test_payload_matches_the_pinned_contract(snapshot, fake_model):
    payload = scan_mod.scan(IDEA, snapshot=snapshot)

    assert set(payload) == {
        "query",
        "generated_at",
        "scope",
        "results",
        "landscape_summary",
        "disclaimer",
    }
    assert payload["query"] == IDEA
    assert payload["generated_at"].endswith("Z")

    assert set(payload["scope"]) == {"source", "snapshot_date_range", "note"}
    assert payload["scope"]["source"] == "fixture_sample"
    assert payload["scope"]["snapshot_date_range"] == ["2026-08-18", "2026-08-18"]
    assert isinstance(payload["scope"]["note"], str) and payload["scope"]["note"]

    assert set(payload["landscape_summary"]) == {
        "text",
        "recent_filing_count",
        "top_cpc_codes",
    }
    assert isinstance(payload["landscape_summary"]["recent_filing_count"], int)
    assert isinstance(payload["landscape_summary"]["top_cpc_codes"], list)

    assert payload["results"]
    for row in payload["results"]:
        assert set(row) == {
            "patent_id",
            "title",
            "abstract",
            "assignee",
            "grant_date",
            "filing_date",
            "cpc_codes",
            "relevance_score",
            "url",
        }
        assert isinstance(row["relevance_score"], float)
        assert isinstance(row["cpc_codes"], list)
        assert row["grant_date"] == "2026-08-18"
        assert row["url"].startswith("https://")


def test_results_are_ranked_and_the_irrelevant_patent_is_excluded(snapshot, fake_model):
    payload = scan_mod.scan(IDEA, snapshot=snapshot)
    ids = [r["patent_id"] for r in payload["results"]]
    assert "US-3" not in ids  # the bread cabinet
    scores = [r["relevance_score"] for r in payload["results"]]
    assert scores == sorted(scores, reverse=True)


def test_relevance_scores_are_normalized_to_the_top_hit_in_this_response(snapshot, fake_model):
    """0-1, relative to the best match in *this* scan — never a raw BM25 score."""
    payload = scan_mod.scan(IDEA, snapshot=snapshot)
    scores = [r["relevance_score"] for r in payload["results"]]
    assert scores[0] == 1.0
    assert all(0.0 < s <= 1.0 for s in scores)


def test_normalization_is_relative_so_even_a_poor_best_match_scores_1(fake_model):
    """A 1.0 means "the closest thing this scan found", not "a perfect match" —
    prove it goes to a barely-related patent too. This is exactly why the field
    must not be rendered as a confidence percentage."""
    weak = _record("US-9", "Decorative wall panel fastener",
                   "A fastener for mounting a decorative panel.", ["E04F13/08"])
    snap = Snapshot(records=[weak], fetched_at="x", date_range=("2026-08-18", "2026-08-18"),
                    source="fixture_sample")
    payload = scan_mod.scan(IDEA, snapshot=snap)
    assert payload["results"][0]["relevance_score"] == 1.0


def test_scope_note_warns_that_the_score_is_relative(snapshot, fake_model):
    note = scan_mod.scan(IDEA, snapshot=snapshot)["scope"]["note"]
    assert "relative to the best match in this scan" in note


def test_scope_note_opens_with_the_snapshots_own_provenance(snapshot, fake_model):
    """A stale cache must describe where *it* came from, not the current source."""
    snapshot.note = "DEMO DATA — not a live patent search."
    note = scan_mod.scan(IDEA, snapshot=snapshot)["scope"]["note"]
    assert note.startswith("DEMO DATA — not a live patent search.")


def test_top_cpc_codes_come_from_the_matched_results(snapshot, fake_model):
    payload = scan_mod.scan(IDEA, snapshot=snapshot)
    assert payload["landscape_summary"]["top_cpc_codes"][0] == "H02S40/10"
    assert "A21B3/00" not in payload["landscape_summary"]["top_cpc_codes"]


def test_recent_filing_count_counts_the_whole_snapshot_not_the_page(snapshot, fake_model):
    payload = scan_mod.scan(IDEA, snapshot=snapshot, top_k=1)
    assert len(payload["results"]) == 1
    assert payload["landscape_summary"]["recent_filing_count"] == 2


def test_recent_filing_count_ignores_barely_related_records(fake_model):
    """One incidental shared word must not make a patent count as related —
    otherwise the crowding signal reads as far higher than it is."""
    strong = _record("US-1", "Robotic cleaner for photovoltaic solar panels",
                     "A robot removes dust from solar panels by robotic cleaning.",
                     ["H02S40/10"])
    # Shares only the word "panel", in an unrelated sense.
    weak = _record("US-9", "Decorative wall panel fastener",
                   "A fastener for mounting a decorative panel.", ["E04F13/08"])
    # Filler so document frequencies (and therefore IDF) are not degenerate.
    filler = [
        _record(f"US-F{i}", f"Widget assembly variant {i}", f"A widget of type {i}.", ["F16B1/00"])
        for i in range(12)
    ]
    snap = Snapshot(
        records=[strong, weak, *filler],
        fetched_at="2026-08-19T00:00:00Z",
        date_range=("2026-08-18", "2026-08-18"),
        source="fixture_sample",
    )
    payload = scan_mod.scan(IDEA, snapshot=snap)
    scored = [r for r in BM25Index(snap.records).score(
        "solar panel dust removal robotic cleaner photovoltaic") if r > 0]
    assert len(scored) == 2, "both solar and wall-panel records should score above zero"
    # ...but only the genuinely related one should be counted as related.
    assert payload["landscape_summary"]["recent_filing_count"] == 1


def test_nullable_assignee_survives_as_none(snapshot, fake_model):
    snapshot.records = [snapshot.records[2]]
    with patch.object(scan_mod.ollama_client, "ask", return_value="bread, proofing, dough"):
        payload = scan_mod.scan("a dough proofing box", snapshot=snapshot)
    assert payload["results"][0]["assignee"] is None


def test_disclaimer_is_present_and_says_not_legal_advice(snapshot, fake_model):
    payload = scan_mod.scan(IDEA, snapshot=snapshot)
    assert "not legal advice" in payload["disclaimer"].lower()
    assert "attorney" in payload["disclaimer"].lower()


def test_scope_note_names_the_real_snapshot_bounds(snapshot, fake_model):
    note = scan_mod.scan(IDEA, snapshot=snapshot)["scope"]["note"]
    assert "2026-08-18" in note
    assert "not the full" in note


def test_scope_note_discloses_when_the_snapshot_was_fetched(snapshot, fake_model):
    """A six-month-old cache should be visible in the payload, not just implied."""
    note = scan_mod.scan(IDEA, snapshot=snapshot)["scope"]["note"]
    assert snapshot.fetched_at in note


def test_scope_note_discloses_records_that_are_missing_abstracts(snapshot, fake_model):
    snapshot.records[2].abstract = ""
    note = scan_mod.scan(IDEA, snapshot=snapshot)["scope"]["note"]
    assert "no abstract text" in note


def test_scope_note_stays_quiet_when_every_record_has_an_abstract(snapshot, fake_model):
    note = scan_mod.scan(IDEA, snapshot=snapshot)["scope"]["note"]
    assert "no abstract text" not in note


# -- failure modes ---------------------------------------------------------


def test_empty_idea_is_rejected(snapshot):
    with pytest.raises(scan_mod.ScanError, match="product idea"):
        scan_mod.scan("   ", snapshot=snapshot)


@pytest.mark.parametrize("top_k", [0, -1])
def test_nonsense_top_k_is_rejected_rather_than_faking_no_matches(snapshot, top_k):
    """top_k=0 previously produced an empty results list, which the summary
    then reported as 'nothing matched' — the opposite of the truth."""
    with pytest.raises(scan_mod.ScanError, match="top_k"):
        scan_mod.scan(IDEA, snapshot=snapshot, top_k=top_k)


def test_ollama_being_down_surfaces_rather_than_faking_a_result(snapshot):
    with patch.object(
        scan_mod.ollama_client, "ask", side_effect=OllamaConnectionError("connection refused")
    ):
        with pytest.raises(OllamaConnectionError):
            scan_mod.scan(IDEA, snapshot=snapshot)
