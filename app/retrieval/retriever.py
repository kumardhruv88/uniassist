"""Retrieval with Annex A applied to text.

1. Step 1 in SQL/Python: only documents in force on as_of and in the student's scope are searched.
2. Hybrid search restricted to those documents: dense (Chroma, one batched call for the planner's rewrites and the
   glossary expansion) + BM25, fused with reciprocal rank fusion. The abstention gate still uses cosine similarity,
   so the calibrated threshold keeps its meaning.
3. Supersession refs from the register drop replaced clauses, then one query fetches the replacing document.
4. Rule anchors: the clause behind every applied rule is always in evidence.
5. Labels: precedence ranks chunks only within a topic (rule parameter); other chunks are neutral SOURCE.
6. Optional cross-encoder rerank of the top candidates.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

import math
import re

from app.db import rows
from app.models import SourceRef
from app.policy.precedence import (Candidate, StudentScope, effective, in_scope, resolve, superseded_by,
                                   supersession_map)
from app.policy.rules import load_register
from app.retrieval.lexical import get_index, rrf
from app.retrieval.query import expand
from app.retrieval.rerank import get_reranker
from app.retrieval.store import Hit
from app.services import Services

LABEL_WEIGHT = {"PRIMARY": 1.0, "SOURCE": 1.0, "TIED": 1.0, "LOWER_PRECEDENCE": 0.9, "INFORMATIONAL": 0.8}
MONEY = re.compile(r"(?:rs\.?|inr|₹)\s*([\d,]+(?:\.\d+)?)", re.I)


def amounts(text: str) -> set[str]:
    return {v.replace(",", "").rstrip(".") for v in MONEY.findall(text)}


@dataclass
class Evidence:
    eid: str
    chunk_id: str
    doc_id: str
    title: str
    section: str | None
    page: int | None
    text: str
    score: float | None
    label: str
    authority: int
    effective_from: str
    version: str | None
    anchor: bool = False
    flagged: bool = False
    is_table: bool = False
    issuer: str = ""


@dataclass
class RetrievalResult:
    evidence: list[Evidence] = field(default_factory=list)
    max_score: float = 0.0
    sources_retrieved: list[dict] = field(default_factory=list)
    upcoming: list[SourceRef] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    no_applicable_docs: bool = False
    expansion: list[str] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    mode: str = "dense"
    ties: list[tuple[str, str]] = field(default_factory=list)   # equal-rank sources stating different amounts


def retrieve(con: sqlite3.Connection, svc: Services, queries: list[str], student: StudentScope | None, as_of: date,
             anchors: list[tuple[str, str]] | None = None, question: str | None = None) -> RetrievalResult:
    s = svc.settings
    out = RetrievalResult()
    register, docs = load_register(con)
    applicable = [d.doc_id for d in register if effective(d.effective_from, d.effective_to, as_of)
                  and in_scope(d.scope_programmes, d.scope_batches, student)]
    upcoming_ids = [d.doc_id for d in register if d.effective_from > as_of and d.authority <= 4
                    and in_scope(d.scope_programmes, d.scope_batches, student)]
    if not applicable:
        out.no_applicable_docs = True
        return out

    question = (question or (queries[-1] if queries else "")).strip()
    rewrites = [q for q in dict.fromkeys(q.strip() for q in queries) if q and q != question][:2]
    queries = rewrites + ([question] if question else [])     # the question as asked is always searched
    expanded = question
    if s.query_expansion and question:
        expanded, out.expansion = expand(question)
        if out.expansion:
            queries.append(expanded)
    out.queries, out.mode = queries, s.retrieval_mode
    q_vecs = svc.embedder.embed_queries(queries) if queries else []
    hits: dict[str, Hit] = {}
    for hs in svc.store.query_many(q_vecs, s.k_fetch, applicable):
        for h in hs:
            if h.chunk_id not in hits or (h.score or 0) > (hits[h.chunk_id].score or 0):
                hits[h.chunk_id] = h
    bm25_rank: list[str] = []
    if s.retrieval_mode == "hybrid" and expanded:
        bm25_rank = [cid for cid, _ in get_index(svc.store).search(expanded, s.k_fetch, set(applicable))]
        for h, vec in svc.store.get_with_vectors([c for c in bm25_rank if c not in hits]):
            h.score = max(sum(a * b for a, b in zip(qv, vec)) for qv in q_vecs)   # cosine, for the abstention gate
            hits[h.chunk_id] = h
    out.max_score = max((h.score or 0 for h in hits.values()), default=0.0)

    # Step 2 on text: drop replaced clauses, then fetch the replacing document's best chunk
    refs = supersession_map(register, student, as_of)
    superseding: set[str] = set()
    for cid, h in list(hits.items()):
        by = superseded_by(h.doc_id, h.section, refs)
        if by:
            out.notes.append(f"{by} supersedes {h.doc_id}#{h.section} (step 2)")
            out.sources_retrieved.append({"doc_id": h.doc_id, "section": h.section, "score": round(h.score or 0, 3), "label": "SUPERSEDED"})
            del hits[cid]
            superseding.add(by)
    for by in superseding:
        if by in applicable and q_vecs:
            for h in svc.store.query(q_vecs[0], 2, [by]):
                hits.setdefault(h.chunk_id, h)

    # Rule anchors: the governing clause is always citable
    anchored: set[str] = set()
    for doc_id, section in anchors or []:
        if doc_id in applicable:
            for h in svc.store.get_section(doc_id, section):
                if h.chunk_id in hits:
                    anchored.add(h.chunk_id)
                elif superseded_by(h.doc_id, h.section, refs) is None:
                    hits[h.chunk_id] = h
                    anchored.add(h.chunk_id)

    # Labels: rank only within topics (rule parameters anchored at a chunk's clause)
    topic_of: dict[tuple[str, str], set[str]] = {}
    for r in rows(con, "SELECT parameter, source_doc_id, source_section, value FROM rule_registry WHERE status='active'"):
        topic_of.setdefault((r["source_doc_id"], r["source_section"]), set()).add(r["parameter"])
    labels: dict[str, str] = {}
    by_topic: dict[str, list[Hit]] = {}
    for h in hits.values():
        if docs[h.doc_id]["authority_level"] >= 5:
            labels[h.chunk_id] = "INFORMATIONAL"
            continue
        for t in topic_of.get((h.doc_id, h.section or ""), ()):
            by_topic.setdefault(t, []).append(h)
    for topic, hs in by_topic.items():
        if len({h.doc_id for h in hs}) < 2:
            continue
        cands = [Candidate(key=h.chunk_id, doc_id=h.doc_id, section=h.section, authority=docs[h.doc_id]["authority_level"],
                           effective_from=date.fromisoformat(docs[h.doc_id]["effective_from"]),
                           title=docs[h.doc_id]["title"]) for h in hs]
        res = resolve(cands, None, as_of, [], with_upcoming=False)
        if res.winner:
            labels.setdefault(res.winner.key, "PRIMARY")
        for c in res.tied:
            labels[c.key] = "TIED"
        for c, _ in res.losers:
            labels.setdefault(c.key, "LOWER_PRECEDENCE")

    dense_rank = sorted((c for c in hits if hits[c].score is not None), key=lambda c: -(hits[c].score or 0))
    fused = rrf(dense_rank, [c for c in bm25_rank if c in hits]) if bm25_rank else {}
    out.notes = list(dict.fromkeys(out.notes))

    def base(h: Hit) -> float:
        if fused:
            return fused.get(h.chunk_id, 0.0)
        return h.score if h.score is not None else 1.0

    ranked = sorted(hits.values(), key=lambda h: (h.chunk_id not in anchored,
                    -(base(h) * LABEL_WEIGHT[labels.get(h.chunk_id, "SOURCE")])))
    rerank_scores: dict[str, float] = {}
    reranker = get_reranker(s.reranker)
    if reranker and queries:
        pool = [h for h in ranked if h.chunk_id not in anchored][:12]
        for h, sc in zip(pool, reranker.scores(question, [h.text for h in pool])):
            rerank_scores[h.chunk_id] = 1 / (1 + math.exp(-sc))
        ranked = sorted(hits.values(), key=lambda h: (h.chunk_id not in anchored, h.chunk_id not in rerank_scores,
                        -(rerank_scores.get(h.chunk_id, 0.0) * LABEL_WEIGHT[labels.get(h.chunk_id, "SOURCE")]),
                        -(base(h) * LABEL_WEIGHT[labels.get(h.chunk_id, "SOURCE")])))
    chosen = [h for h in ranked if h.chunk_id in anchored]
    chosen += [h for h in ranked if h.chunk_id not in anchored][: max(0, s.top_k - len(chosen))]
    for i, h in enumerate(chosen, 1):
        d = docs[h.doc_id]
        out.evidence.append(Evidence(eid=f"E{i}", chunk_id=h.chunk_id, doc_id=h.doc_id, title=d["title"], section=h.section,
                                     page=h.page, text=h.text, score=h.score, label=labels.get(h.chunk_id, "SOURCE"),
                                     authority=d["authority_level"], effective_from=d["effective_from"], version=d["version"],
                                     anchor=h.chunk_id in anchored, flagged=h.flagged, is_table=h.is_table,
                                     issuer=d["issuer"]))

    # Annex A step 5 for facts that are not rule parameters (fees, charges): two sources with the same authority and
    # the same effective date, both relevant to the question, that state different amounts for the same thing
    from app.retrieval.query import terms as _terms
    for i, a in enumerate(out.evidence):
        for b in out.evidence[i + 1:]:
            if (a.doc_id == b.doc_id or a.authority != b.authority or a.authority >= 5 or a.effective_from != b.effective_from
                    or {a.label, b.label} & {"LOWER_PRECEDENCE", "INFORMATIONAL"}
                    or (a.score or 0) < s.tau or (b.score or 0) < s.tau):
                continue
            va, vb = amounts(a.text), amounts(b.text)
            ta, tb = set(_terms(a.text)), set(_terms(b.text))
            if va and vb and not va & vb and len(ta & tb) / max(1, len(ta | tb)) >= 0.5:
                a.label = b.label = "TIED"
                out.ties.append((a.eid, b.eid))
                out.notes.append(f"{a.doc_id} section {a.section} and {b.doc_id} section {b.section} have the same authority and date "
                                 f"but state different amounts (step 5: unresolved)")
    bm_pos = {c: i for i, c in enumerate(bm25_rank, 1)}
    dn_pos = {c: i for i, c in enumerate(dense_rank, 1)}
    out.sources_retrieved += [{"doc_id": h.doc_id, "section": h.section, "score": round(h.score or 0, 3),
                               "label": labels.get(h.chunk_id, "SOURCE"), "anchor": h.chunk_id in anchored,
                               "dense_rank": dn_pos.get(h.chunk_id), "bm25_rank": bm_pos.get(h.chunk_id),
                               **({"rerank": round(rerank_scores[h.chunk_id], 3)} if h.chunk_id in rerank_scores else {})}
                              for h in ranked[:10]]

    # Documents not yet in force: mentioned as upcoming changes when relevant
    if upcoming_ids and q_vecs:
        seen = set()
        for h in svc.store.query(q_vecs[0], 3, upcoming_ids):
            if (h.score or 0) >= s.tau and h.doc_id not in seen:
                seen.add(h.doc_id)
                d = docs[h.doc_id]
                out.upcoming.append(SourceRef(doc_id=h.doc_id, section=h.section, title=d["title"],
                                              effective_from=date.fromisoformat(d["effective_from"])))
    return out
