"""Caches. Every key includes the data version, which changes on any ingest, rule load or student load,
so a cached answer can never outlive the documents and records it was built from.

answers   exact: normalised question + student + as_of + data version (+ planner/session state)
semantic  general (non-personal) questions only, same scope (programme + batch), as_of and data version:
          cosine >= 0.95 on the question embedding AND the same content terms after stemming and glossary mapping.
          The second condition stops near-misses such as "B.Tech" vs "M.Tech" that embed almost identically.
llm       exact prompt hash -> structured output (identical context gives an identical answer at temperature 0)
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from collections import OrderedDict

from app import metrics


class TTLCache:
    def __init__(self, name: str, maxsize: int, ttl_s: float):
        self.name, self.maxsize, self.ttl = name, maxsize, ttl_s
        self._d: OrderedDict[str, tuple[float, object]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            hit = self._d.get(key)
            if hit and time.monotonic() - hit[0] < self.ttl:
                self._d.move_to_end(key)
                metrics.inc("cache_requests", cache=self.name, result="hit")
                return hit[1]
            if hit:
                del self._d[key]
        metrics.inc("cache_requests", cache=self.name, result="miss")
        return None

    def set(self, key: str, value) -> None:
        with self._lock:
            self._d[key] = (time.monotonic(), value)
            self._d.move_to_end(key)
            while len(self._d) > self.maxsize:
                self._d.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    def __len__(self) -> int:
        return len(self._d)


class SemanticCache:
    def __init__(self, maxsize: int = 300, threshold: float = 0.95, ttl_s: float = 3600):
        self.maxsize, self.threshold, self.ttl = maxsize, threshold, ttl_s
        self._items: list[tuple[float, list[float], str, object]] = []   # (ts, vec, scope_key, value)
        self._lock = threading.Lock()

    def get(self, vec: list[float], scope_key: str, threshold: float | None = None):
        """(value, similarity) for the most similar entry in the same scope, or (None, best similarity)."""
        threshold = self.threshold if threshold is None else threshold
        now = time.monotonic()
        with self._lock:
            self._items = [it for it in self._items if now - it[0] < self.ttl]
            best, best_sim = None, 0.0
            for ts, v, k, value in self._items:
                if k == scope_key:
                    sim = sum(a * b for a, b in zip(vec, v))
                    if sim > best_sim:
                        best, best_sim = value, sim
        if best is not None and best_sim >= threshold:
            metrics.inc("cache_requests", cache="semantic", result="hit")
            return best, best_sim
        metrics.inc("cache_requests", cache="semantic", result="miss")
        return None, best_sim

    def set(self, vec: list[float], scope_key: str, value) -> None:
        with self._lock:
            self._items.append((time.monotonic(), vec, scope_key, value))
            self._items = self._items[-self.maxsize:]

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


def normalise(question: str) -> str:
    return re.sub(r"[\s?.!]+$", "", " ".join(question.lower().split()))


def key(*parts: object) -> str:
    return hashlib.sha256("\x1f".join(str(p) for p in parts).encode()).hexdigest()


answers = TTLCache("answers", maxsize=2000, ttl_s=3600)
llm = TTLCache("llm", maxsize=2000, ttl_s=6 * 3600)
semantic = SemanticCache()


def clear_all() -> None:
    answers.clear()
    llm.clear()
    semantic.clear()
