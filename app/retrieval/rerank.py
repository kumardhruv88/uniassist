"""Optional cross-encoder reranking (RERANKER=cross-encoder/ms-marco-MiniLM-L-6-v2). It reads the question and
each candidate together, so it orders candidates better than either vector or keyword scores alone.
Off by default: it adds about 100-300 ms on CPU for 12 candidates."""
from __future__ import annotations

import threading

_models: dict[str, object] = {}
_lock = threading.Lock()


class CrossEncoderReranker:
    def __init__(self, name: str):
        from sentence_transformers import CrossEncoder
        self.name = name
        self.model = CrossEncoder(name, max_length=512)
        self._lock = threading.Lock()

    def scores(self, query: str, texts: list[str]) -> list[float]:
        if not texts:
            return []
        with self._lock:
            return [float(s) for s in self.model.predict([(query, t) for t in texts], show_progress_bar=False)]


def get_reranker(name: str | None):
    if not name or name.lower() == "none":
        return None
    with _lock:
        if name not in _models:
            _models[name] = CrossEncoderReranker(name)
        return _models[name]
