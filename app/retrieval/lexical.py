"""BM25 over the indexed chunks (Okapi BM25, k1=1.5, b=0.75). Catches what embeddings miss: clause numbers,
course codes, fee figures and exact terms like "condonation". Rebuilt in memory whenever the vector store changes;
54 chunks take a few milliseconds."""
from __future__ import annotations

import math
import threading
from collections import Counter

from app.retrieval.query import terms


class BM25:
    def __init__(self, docs: list[tuple[str, str, str]], k1: float = 1.5, b: float = 0.75):
        """docs: (chunk_id, doc_id, text)."""
        self.k1, self.b = k1, b
        self.ids = [d[0] for d in docs]
        self.doc_of = {d[0]: d[1] for d in docs}
        self.tf = [Counter(terms(d[2])) for d in docs]
        self.len = [sum(c.values()) for c in self.tf]
        self.avg = (sum(self.len) / len(self.len)) if self.len else 1.0
        df: Counter = Counter()
        for c in self.tf:
            df.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int, allowed_docs: set[str] | None = None) -> list[tuple[str, float]]:
        q = [t for t in dict.fromkeys(terms(query)) if t in self.idf]
        scored = []
        for i, cid in enumerate(self.ids):
            if allowed_docs is not None and self.doc_of[cid] not in allowed_docs:
                continue
            tf, dl = self.tf[i], self.len[i]
            s = sum(self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + self.k1 * (1 - self.b + self.b * dl / self.avg))
                    for t in q if t in tf)
            if s > 0:
                scored.append((cid, s))
        return sorted(scored, key=lambda x: -x[1])[:k]


_index: tuple[int, BM25] | None = None
_lock = threading.Lock()


def get_index(store) -> BM25:
    global _index
    with _lock:
        if _index is None or _index[0] != store.version:
            version = store.version
            _index = (version, BM25(store.all_texts()))
        return _index[1]


def rrf(*rankings: list[str], k: int = 60) -> dict[str, float]:
    """Reciprocal rank fusion: score = sum over rankings of 1 / (k + rank)."""
    out: dict[str, float] = {}
    for ranking in rankings:
        for r, cid in enumerate(ranking, 1):
            out[cid] = out.get(cid, 0.0) + 1.0 / (k + r)
    return out
