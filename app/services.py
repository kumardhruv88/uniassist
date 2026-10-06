"""Process-wide services (created once, shared by requests)."""
from __future__ import annotations

import threading
from dataclasses import dataclass

from app.config import Settings, get_settings
from app.retrieval.embedder import get_embedder
from app.retrieval.store import VectorStore


@dataclass
class Services:
    settings: Settings
    store: VectorStore
    _embedder: object | None = None
    _llm: object | None = None

    @property
    def embedder(self):
        if self._embedder is None:
            self._embedder = get_embedder(self.settings.embed_model)
        return self._embedder

    @property
    def llm(self):
        if self._llm is None:
            from app.llm.client import make_llm
            self._llm = make_llm(self.settings)
        return self._llm


_services: Services | None = None
_lock = threading.Lock()


def get_services() -> Services:
    global _services
    with _lock:
        if _services is None:
            s = get_settings()
            _services = Services(settings=s, store=VectorStore(s.chroma_dir, s.collection_name))
        return _services


def reset_services() -> None:   # tests
    global _services
    with _lock:
        _services = None
