"""BM25 index behaviour."""

from __future__ import annotations

import math

import pytest

from patentpulse.index import B, K1, BM25Index, tokenize
from patentpulse.sources import PatentRecord


def _record(pid: str, title: str, abstract: str = "", cpc: list[str] | None = None):
    return PatentRecord(
        patent_id=pid,
        title=title,
        abstract=abstract,
        claim_summary="",
        assignee=None,
        grant_date="2026-08-18",
        filing_date="2024-01-01",
        cpc_codes=cpc or [],
        url="https://example.test",
    )


def test_tokenize_drops_stopwords_and_keeps_cpc_codes():
    tokens = tokenize("A method for the cleaning of G06N3/084 panels")
    assert "method" not in tokens
    assert "the" not in tokens
    assert "g06n3/084" in tokens
    assert "cleaning" in tokens


def test_tokenize_drops_single_characters():
    assert tokenize("a b robot") == ["robot"]


def test_search_ranks_the_matching_record_first():
    records = [
        _record("US-1", "Hydraulic excavator bucket linkage"),
        _record("US-2", "Robotic cleaning of photovoltaic solar panels"),
        _record("US-3", "Method of baking bread"),
    ]
    hits = BM25Index(records).search("solar panel cleaning robot", top_k=3)
    assert hits
    assert hits[0][0].patent_id == "US-2"
    assert hits[0][1] > 0


def test_search_excludes_zero_scoring_records():
    records = [
        _record("US-1", "Robotic solar panel cleaner"),
        _record("US-2", "Bread baking oven"),
    ]
    hits = BM25Index(records).search("solar", top_k=10)
    assert [r.patent_id for r, _ in hits] == ["US-1"]


def test_unknown_query_returns_no_hits_rather_than_noise():
    records = [_record("US-1", "Robotic solar panel cleaner")]
    assert BM25Index(records).search("xyzzy quixotic", top_k=5) == []


def test_abstract_and_cpc_are_searchable():
    records = [
        _record("US-1", "Untitled apparatus", abstract="ultrasonic dust removal", cpc=["H02S40/10"]),
        _record("US-2", "Untitled apparatus", abstract="hydraulic press", cpc=["B30B1/00"]),
    ]
    index = BM25Index(records)
    assert index.search("ultrasonic", top_k=1)[0][0].patent_id == "US-1"
    assert index.search("H02S40/10", top_k=1)[0][0].patent_id == "US-1"


def test_score_returns_one_entry_per_record():
    records = [_record(f"US-{i}", f"widget {i}") for i in range(5)]
    assert len(BM25Index(records).score("widget")) == 5


def test_precomputed_scores_give_the_same_ranking():
    records = [
        _record("US-1", "Robotic solar panel cleaner"),
        _record("US-2", "Solar panel mounting bracket"),
        _record("US-3", "Bread oven"),
    ]
    index = BM25Index(records)
    scores = index.score("solar panel cleaning")
    assert index.search("solar panel cleaning", scores=scores) == index.search(
        "solar panel cleaning"
    )


def test_bm25_score_matches_the_textbook_formula():
    """Pin the actual arithmetic — ordering-only tests would miss a sign flip
    in the IDF or a dropped (k1+1) in the numerator."""
    records = [
        _record("A", "solar solar panel"),
        _record("B", "bread oven"),
        _record("C", "hydraulic press"),
    ]
    scores = BM25Index(records).score("solar")

    n, df, tf = 3, 1, 2
    lengths = [3, 2, 2]
    avg = sum(lengths) / n
    idf = math.log(1 + (n - df + 0.5) / (df + 0.5))
    norm = 1 - B + B * (lengths[0] / avg)
    expected = idf * (tf * (K1 + 1)) / (tf + K1 * norm)

    assert scores[0] == pytest.approx(expected, abs=1e-12)
    assert scores[1] == 0.0 and scores[2] == 0.0


def test_idf_stays_non_negative_for_a_term_in_every_document():
    """The classic BM25 pitfall: the textbook IDF goes negative at df > N/2."""
    records = [_record(str(i), "solar panel") for i in range(5)]
    scores = BM25Index(records).score("solar")
    assert all(s > 0 for s in scores)


def test_longer_documents_are_penalised_for_the_same_term_count():
    """Both docs mention "solar" once; the padded one should score lower.

    They must share one index — length normalisation is relative to that
    index's average document length.
    """
    padded = "solar " + " ".join(f"filler{i}" for i in range(40))
    scores = BM25Index([_record("SHORT", "solar panel"), _record("LONG", padded)]).score("solar")
    assert scores[1] < scores[0]


def test_empty_index_is_harmless():
    index = BM25Index([])
    assert len(index) == 0
    assert index.search("anything") == []
