"""POST /ingest: validate → dedupe → parse → chunk → flag → embed + upsert → register row (commit point) → claims → rules.

Retrieval only searches doc_ids present in source_register, so the register row is the commit point:
chunks without a register row are unreachable, and a failed SQLite write deletes them again.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from app.db import one, session
from app.ingestion.chunker import chunk_document
from app.ingestion.claims import extract_claims
from app.ingestion.injection import flagged_sentences
from app.ingestion.parsers import SUPPORTED, parse
from app.models import IngestMetadata, IngestResponse
from app.services import Services

log = logging.getLogger("uniassist.ingest")
SHORT = {"min_attendance_pct": "ATT", "max_condonation_pct": "COND", "pass_min_total_pct": "PASS", "supplementary_allowed_results": "SUPP",
         "min_cgpa_placement": "CGPA", "max_active_backlogs_placement": "BKLG"}


def _meta_hash(meta: IngestMetadata) -> str:
    payload = meta.model_dump(mode="json", exclude={"warnings"})
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def ingest(data: bytes, filename: str, meta: IngestMetadata, svc: Services) -> IngestResponse:
    s = svc.settings
    warnings = list(meta.warnings)
    ext = Path(filename).suffix.lower() or ".txt"
    if ext not in SUPPORTED:
        return IngestResponse(doc_id=meta.doc_id, chunks_indexed=0, status="failed",
                              warnings=[f"unsupported file type {ext}; use one of {', '.join(sorted(SUPPORTED))}"])
    file_sha, meta_sha = hashlib.sha256(data).hexdigest(), _meta_hash(meta)

    with session() as con:
        existing = one(con, "SELECT file_sha256, meta_sha256, chunks_indexed FROM source_register WHERE doc_id = ?", (meta.doc_id,))
    if existing and existing["file_sha256"] == file_sha and existing["meta_sha256"] == meta_sha \
            and svc.store.count_doc(meta.doc_id) == existing["chunks_indexed"]:
        return IngestResponse(doc_id=meta.doc_id, chunks_indexed=existing["chunks_indexed"], status="unchanged")

    parsed = parse(data, ext)
    warnings += parsed.warnings
    chunks = chunk_document(meta.doc_id, parsed, s.chunker)
    if not chunks:
        return IngestResponse(doc_id=meta.doc_id, chunks_indexed=0, status="failed",
                              warnings=warnings + ["no text could be extracted from the file"])
    for c in chunks:
        if flagged_sentences(c.text):
            c.flagged = True
    n_flagged = sum(c.flagged for c in chunks)
    if n_flagged:
        warnings.append(f"{n_flagged} chunk(s) contain instruction-like text; kept as data and redacted before the LLM sees them")

    s.files_dir.mkdir(parents=True, exist_ok=True)
    path = s.files_dir / f"{meta.doc_id}{ext}"
    path.write_bytes(data)

    # 1) vectors
    if existing:
        svc.store.delete_doc(meta.doc_id)
    vectors = svc.embedder.embed_documents([c.embed_text(meta.title) for c in chunks])
    ids = [c.chunk_id for c in chunks]
    svc.store.upsert(ids, vectors, [c.text for c in chunks],
                     [{"doc_id": c.doc_id, "section": c.section, "section_title": c.section_title, "page": c.page_start,
                       "page_end": c.page_end, "is_table": c.is_table, "ocr": c.ocr, "flagged": c.flagged} for c in chunks])

    # 2) register row (commit point) + rules, in one transaction
    claims, claim_warnings = extract_claims(chunks)
    warnings += claim_warnings
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    rules_added: list[str] = []
    try:
        with session() as con:
            con.execute("""INSERT INTO source_register (doc_id, title, issuer, authority_level, doc_type, version, effective_from,
                    effective_to, supersedes, scope_programmes, scope_batches, provenance, retrieved_on, synthetic, file_path,
                    file_sha256, meta_sha256, chunks_indexed, ingest_warnings, ingested_at)
                VALUES (:doc_id,:title,:issuer,:authority_level,:doc_type,:version,:effective_from,:effective_to,:supersedes,
                    :scope_programmes,:scope_batches,:provenance,:retrieved_on,:synthetic,:file_path,:file_sha,:meta_sha,:n,:w,:now)
                ON CONFLICT(doc_id) DO UPDATE SET title=excluded.title, issuer=excluded.issuer, authority_level=excluded.authority_level,
                    doc_type=excluded.doc_type, version=excluded.version, effective_from=excluded.effective_from,
                    effective_to=excluded.effective_to, supersedes=excluded.supersedes, scope_programmes=excluded.scope_programmes,
                    scope_batches=excluded.scope_batches, provenance=excluded.provenance, retrieved_on=excluded.retrieved_on,
                    synthetic=excluded.synthetic, file_path=excluded.file_path, file_sha256=excluded.file_sha256,
                    meta_sha256=excluded.meta_sha256, chunks_indexed=excluded.chunks_indexed,
                    ingest_warnings=excluded.ingest_warnings, ingested_at=excluded.ingested_at""",
                        {**meta.model_dump(mode="json", exclude={"rules", "warnings"}), "file_path": str(path),
                         "file_sha": file_sha, "meta_sha": meta_sha, "n": len(chunks), "w": json.dumps(warnings), "now": now})
            # derived rules are regenerated from the new content; curated rows are never touched
            con.execute("UPDATE rule_registry SET status='rejected' WHERE source_doc_id=? AND origin IN ('auto_extracted','ingest_metadata')",
                        (meta.doc_id,))
            for r in meta.rules:
                _upsert_rule(con, rule_id=r.rule_id, description=r.description, parameter=r.parameter, operator=r.operator,
                             value=r.value, scope_programmes=r.scope_programmes, scope_batches=r.scope_batches,
                             effective_from=str(r.effective_from or meta.effective_from),
                             effective_to=str(r.effective_to or meta.effective_to) if (r.effective_to or meta.effective_to) else None,
                             source_doc_id=meta.doc_id, source_section=r.source_section, unit=r.unit, origin="ingest_metadata", now=now)
                rules_added.append(r.rule_id)
            for c in claims:
                taken = one(con, """SELECT rule_id FROM rule_registry WHERE parameter=? AND source_doc_id=? AND source_section=?
                                    AND status='active' AND origin IN ('curated','ingest_metadata')""",
                            (c.parameter, meta.doc_id, c.section))
                if taken:
                    continue
                rid = f"AUTO-{meta.doc_id}-{SHORT[c.parameter]}-{c.section}"
                _upsert_rule(con, rule_id=rid, description=f"Extracted from section {c.section}: \"{c.sentence[:160]}\"",
                             parameter=c.parameter, operator=c.operator, value=c.value, scope_programmes=meta.scope_programmes,
                             scope_batches=meta.scope_batches, effective_from=str(meta.effective_from),
                             effective_to=str(meta.effective_to) if meta.effective_to else None, source_doc_id=meta.doc_id,
                             source_section=c.section, unit=c.unit, origin="auto_extracted", now=now)
                rules_added.append(rid)
    except Exception:
        log.exception("register write failed for %s; removing its chunks", meta.doc_id)
        svc.store.delete_ids(ids)
        raise

    return IngestResponse(doc_id=meta.doc_id, chunks_indexed=len(chunks),
                          status="replaced" if existing else "indexed", rules_added=rules_added, warnings=warnings)


def _upsert_rule(con, *, now: str, **r) -> None:
    con.execute("""INSERT INTO rule_registry (rule_id, description, parameter, operator, value, scope_programmes, scope_batches,
                       effective_from, effective_to, source_doc_id, source_section, unit, origin, status, created_at)
                   VALUES (:rule_id,:description,:parameter,:operator,:value,:scope_programmes,:scope_batches,:effective_from,
                       :effective_to,:source_doc_id,:source_section,:unit,:origin,'active',:now)
                   ON CONFLICT(rule_id) DO UPDATE SET description=excluded.description, parameter=excluded.parameter,
                       operator=excluded.operator, value=excluded.value, scope_programmes=excluded.scope_programmes,
                       scope_batches=excluded.scope_batches, effective_from=excluded.effective_from,
                       effective_to=excluded.effective_to, source_section=excluded.source_section, unit=excluded.unit,
                       origin=excluded.origin, status='active'""", {**r, "now": now})
