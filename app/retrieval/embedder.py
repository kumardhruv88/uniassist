"""Sentence embeddings. 'hash' is a deterministic offline embedder for tests (no model download)."""
from __future__ import annotations

import hashlib
import math
import re
import threading
from collections import OrderedDict

BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


class HashEmbedder:
    dim = 384

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        toks = re.findall(r"[a-z0-9]+", text.lower())
        for tok in toks + [a + "_" + b for a, b in zip(toks, toks[1:])]:
            h = int.from_bytes(hashlib.blake2b(tok.encode(), digest_size=8).digest(), "big")
            v[h % self.dim] += 1.0 if (h >> 32) & 1 else -1.0
        n = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / n for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class STEmbedder:
    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer
        self.model_name = model_name
        self.model = SentenceTransformer(model_name)
        self.prefix = BGE_QUERY_PREFIX if "bge" in model_name.lower() else ""
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, list[float]] = OrderedDict()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        with self._lock:
            return self.model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False).tolist()

    def embed_query(self, text: str) -> list[float]:
        return self.embed_queries([text])[0]

    def embed_queries(self, texts: list[str]) -> list[list[float]]:
        """One batched forward pass for all query variants; repeated queries come from an LRU cache."""
        with self._lock:
            todo = list(dict.fromkeys(t for t in texts if t not in self._cache))
            if todo:
                vecs = self.model.encode([self.prefix + t for t in todo], normalize_embeddings=True,
                                         batch_size=16, show_progress_bar=False).tolist()
                self._cache.update(zip(todo, vecs))
            out = []
            for t in texts:
                self._cache.move_to_end(t)
                out.append(self._cache[t])
            while len(self._cache) > 2048:
                self._cache.popitem(last=False)
            return out


_cache: dict[str, object] = {}
_lock = threading.Lock()


def get_embedder(model_name: str):
    with _lock:
        if model_name not in _cache:
            _cache[model_name] = HashEmbedder() if model_name == "hash" else STEmbedder(model_name)
        return _cache[model_name]
