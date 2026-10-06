"""Embedded, persisted Chroma. Stores chunk text, vectors and locators only — never governance metadata
(dates, scope, authority live in SQLite, so a register correction applies without re-embedding)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")


@dataclass
class Hit:
    chunk_id: str
    doc_id: str
    section: str | None
    page: int | None
    text: str
    score: float | None
    is_table: bool = False
    flagged: bool = False
    section_title: str = ""


class VectorStore:
    def __init__(self, path: Path, name: str):
        import chromadb
        from chromadb.config import Settings as ChromaSettings

        path.mkdir(parents=True, exist_ok=True)
        self.version = 0                      # bumped on every write; the BM25 index rebuilds when it changes
        self.client = chromadb.PersistentClient(path=str(path), settings=ChromaSettings(anonymized_telemetry=False))
        try:
            self.col = self.client.get_or_create_collection(name, configuration={"hnsw": {"space": "cosine"}})
        except TypeError:  # older chromadb
            self.col = self.client.get_or_create_collection(name, metadata={"hnsw:space": "cosine"})

    @staticmethod
    def _hit(cid: str, doc: str, meta: dict, dist: float | None) -> Hit:
        return Hit(chunk_id=cid, doc_id=meta["doc_id"], section=meta.get("section") or None,
                   page=meta.get("page") or None, text=doc, score=None if dist is None else 1.0 - float(dist),
                   is_table=bool(meta.get("is_table")), flagged=bool(meta.get("flagged")),
                   section_title=meta.get("section_title", ""))

    def upsert(self, ids: list[str], embeddings: list[list[float]], documents: list[str], metadatas: list[dict]) -> None:
        clean = [{k: ("" if v is None else v) for k, v in m.items()} for m in metadatas]
        for i in range(0, len(ids), 256):
            self.col.upsert(ids=ids[i:i + 256], embeddings=embeddings[i:i + 256],
                            documents=documents[i:i + 256], metadatas=clean[i:i + 256])
        self.version += 1

    def delete_doc(self, doc_id: str) -> None:
        self.col.delete(where={"doc_id": doc_id})
        self.version += 1

    def delete_ids(self, ids: list[str]) -> None:
        if ids:
            self.col.delete(ids=ids)
            self.version += 1

    def query(self, embedding: list[float], k: int, doc_ids: list[str]) -> list[Hit]:
        if not doc_ids:                       # Chroma rejects {"$in": []}
            return []
        res = self.col.query(query_embeddings=[embedding], n_results=k, where={"doc_id": {"$in": doc_ids}},
                             include=["documents", "metadatas", "distances"])
        return [self._hit(c, d, m, dist) for c, d, m, dist in
                zip(res["ids"][0], res["documents"][0], res["metadatas"][0], res["distances"][0])]

    def query_many(self, embeddings: list[list[float]], k: int, doc_ids: list[str]) -> list[list[Hit]]:
        """Several query vectors in one round trip (the planner's rewrites + the glossary expansion)."""
        if not doc_ids or not embeddings:
            return [[] for _ in embeddings]
        res = self.col.query(query_embeddings=embeddings, n_results=k, where={"doc_id": {"$in": doc_ids}},
                             include=["documents", "metadatas", "distances"])
        return [[self._hit(c, d, m, dist) for c, d, m, dist in zip(res["ids"][i], res["documents"][i],
                                                                     res["metadatas"][i], res["distances"][i])]
                for i in range(len(embeddings))]

    def get_with_vectors(self, ids: list[str]) -> list[tuple[Hit, list[float]]]:
        if not ids:
            return []
        res = self.col.get(ids=ids, include=["documents", "metadatas", "embeddings"])
        return [(self._hit(c, d, m, None), list(v)) for c, d, m, v in
                zip(res["ids"], res["documents"], res["metadatas"], res["embeddings"])]

    def all_texts(self) -> list[tuple[str, str, str]]:
        """(chunk_id, doc_id, section title + text) for the keyword index."""
        res = self.col.get(include=["documents", "metadatas"])
        return [(c, m["doc_id"], f"{m.get('section_title', '')} {d}") for c, d, m in
                zip(res["ids"], res["documents"], res["metadatas"])]

    def get_section(self, doc_id: str, section: str) -> list[Hit]:
        res = self.col.get(where={"$and": [{"doc_id": doc_id}, {"section": section}]}, include=["documents", "metadatas"])
        return [self._hit(c, d, m, None) for c, d, m in zip(res["ids"], res["documents"], res["metadatas"])]

    def get_ids(self, ids: list[str]) -> list[Hit]:
        if not ids:
            return []
        res = self.col.get(ids=ids, include=["documents", "metadatas"])
        return [self._hit(c, d, m, None) for c, d, m in zip(res["ids"], res["documents"], res["metadatas"])]

    def count(self) -> int:
        return self.col.count()

    def count_doc(self, doc_id: str) -> int:
        return len(self.col.get(where={"doc_id": doc_id}, include=[])["ids"])
