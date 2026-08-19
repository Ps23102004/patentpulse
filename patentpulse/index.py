"""A small BM25 keyword index over patent records.

Okapi BM25 in ~60 lines of stdlib. No embeddings, no vector store, no extra
dependency — for a few hundred short documents this is fast, explainable, and
good enough. Swap in embeddings only if ranking quality measurably falls short.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from patentpulse.sources import PatentRecord

K1 = 1.5
B = 0.75

# Words that carry no signal in patent prose — every patent is "a method for a
# system comprising an apparatus".
_STOPWORDS = frozenset(
    """a an and are as at be by for from has have in into is it its of on or that the to
    with which such said method system apparatus device means comprising configured
    thereof herein wherein one more least also may can use used using""".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:/[a-z0-9]+)?")


def tokenize(text: str) -> list[str]:
    """Lowercase word tokens, stopwords dropped. Keeps CPC codes like g06n3/084."""
    return [t for t in _TOKEN_RE.findall(text.lower()) if t not in _STOPWORDS and len(t) > 1]


class BM25Index:
    """Okapi BM25 over a fixed list of `PatentRecord`s."""

    def __init__(self, records: list[PatentRecord]) -> None:
        self.records = records
        self._docs = [Counter(tokenize(r.search_text())) for r in records]
        self._lengths = [sum(d.values()) for d in self._docs]
        n = len(records)
        self._avg_len = (sum(self._lengths) / n) if n else 0.0

        doc_freq: Counter[str] = Counter()
        for doc in self._docs:
            doc_freq.update(doc.keys())
        # Standard BM25 IDF with the +1 that keeps common terms non-negative.
        self._idf = {
            term: math.log(1 + (n - df + 0.5) / (df + 0.5)) for term, df in doc_freq.items()
        }

    def __len__(self) -> int:
        return len(self.records)

    def score(self, query: str) -> list[float]:
        """BM25 score for every record, in the order records were given."""
        terms = tokenize(query)
        scores = [0.0] * len(self.records)
        if not terms or not self._avg_len:
            return scores
        # Full scan per term: O(terms x docs). At snapshot sizes (hundreds to a
        # few thousand records) this is microseconds. Build an inverted index if
        # the corpus ever gets large enough to notice.
        for term in terms:
            idf = self._idf.get(term)
            if idf is None:
                continue
            for i, doc in enumerate(self._docs):
                tf = doc.get(term, 0)
                if not tf:
                    continue
                norm = 1 - B + B * (self._lengths[i] / self._avg_len)
                scores[i] += idf * (tf * (K1 + 1)) / (tf + K1 * norm)
        return scores

    def search(
        self, query: str, top_k: int = 10, scores: list[float] | None = None
    ) -> list[tuple[PatentRecord, float]]:
        """Top `top_k` records with a non-zero score, best first.

        Pass `scores` from a previous `score()` call to avoid scoring twice.
        """
        if scores is None:
            scores = self.score(query)
        scored = [(rec, s) for rec, s in zip(self.records, scores) if s > 0]
        scored.sort(key=lambda pair: (-pair[1], pair[0].patent_id))
        return scored[:top_k]
