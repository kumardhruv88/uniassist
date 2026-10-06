"""FastAPI app: the fixed contract (guide §6) plus a few additive read endpoints for the UI."""
from __future__ import annotations

import csv
import io
import json
import logging
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from pydantic import ValidationError

from app import audit
from app.db import init_db, one, rows, session
from app.graph.pipeline import ask as run_ask
from app.ingestion.pipeline import ingest as run_ingest
from app.loader import load as run_load
from app.models import AskRequest, AskResponse, IngestMetadata, IngestResponse, LoadRequest, LoadResponse, RuleIn
from app.services import get_services

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("uniassist")


def _self_check() -> None:
    """Re-ingest any document whose index drifted from the register (e.g. a lost vector volume)."""
    svc = get_services()
    with session() as con:
        docs = rows(con, "SELECT * FROM source_register")
    for d in docs:
        if svc.store.count_doc(d["doc_id"]) != d["chunks_indexed"] and Path(d["file_path"]).exists():
            log.warning("index drift for %s; re-ingesting from %s", d["doc_id"], d["file_path"])
            meta = IngestMetadata.model_validate({k: d[k] for k in IngestMetadata.model_fields if k in d and k not in ("rules", "warnings")})
            run_ingest(Path(d["file_path"]).read_bytes(), d["file_path"], meta, svc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    svc = get_services()
    await run_in_threadpool(lambda: svc.embedder)        # warm the embedding model once
    await run_in_threadpool(_self_check)
    log.info("ready: llm=%s/%s embed=%s collection=%s", svc.settings.llm_provider, svc.settings.llm_model,
             svc.settings.embed_model, svc.settings.collection_name)
    yield


app = FastAPI(title="UniAssist API", version="1.0.0", lifespan=lifespan,
              description="AI-Powered University Student Services Assistant — HCLTech Future Ready AI Engineer Hackathon")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def admin(x_admin_token: str | None = Header(default=None)) -> None:
    token = get_services().settings.admin_token
    if token and x_admin_token != token:
        raise HTTPException(401, "missing or wrong X-Admin-Token")


# ----------------------------------------------------------------------------- contract
@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, x_student_id: str | None = Header(default=None)) -> AskResponse:
    return await run_in_threadpool(run_ask, get_services(), req.question, x_student_id, req.as_of_date)


@app.post("/ingest", response_model=IngestResponse, status_code=201, dependencies=[Depends(admin)])
async def ingest(request: Request, file: UploadFile = File(...), metadata: str | None = Form(default=None)) -> IngestResponse:
    raw = metadata
    if raw is None:                                       # accept metadata sent as a file part too (-F metadata=@meta.json)
        form = await request.form()
        part = form.get("metadata")
        raw = (await part.read()).decode() if hasattr(part, "read") else None
    if not raw:
        raise HTTPException(400, "metadata is required: a JSON object with the Annex B fields")
    try:
        meta = IngestMetadata.model_validate(json.loads(raw))
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"metadata is not valid JSON: {e}")
    except ValidationError as e:
        raise HTTPException(400, {"message": "metadata failed validation", "errors": json.loads(e.json())})
    data = await file.read()
    svc = get_services()
    if len(data) > svc.settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"file is larger than {svc.settings.max_upload_mb} MB")
    return await run_in_threadpool(run_ingest, data, file.filename or "upload.txt", meta, svc)


@app.get("/health")
async def health() -> dict:
    svc = get_services()
    comp: dict = {"api": {"status": "ok"}}
    try:
        comp["vector_store"] = {"status": "ok", "collection": svc.settings.collection_name, "chunks": svc.store.count()}
    except Exception as e:  # noqa: BLE001
        comp["vector_store"] = {"status": "down", "detail": str(e)}
    try:
        with session() as con:
            comp["sqlite"] = {"status": "ok", **{k: one(con, f"SELECT COUNT(*) AS n FROM {t}")["n"] for k, t in
                              [("students", "students"), ("rules", "rule_registry"), ("documents", "source_register")]}}
    except Exception as e:  # noqa: BLE001
        comp["sqlite"] = {"status": "down", "detail": str(e)}
    comp["llm"] = await run_in_threadpool(svc.llm.health)
    down = [k for k in ("vector_store", "sqlite") if comp[k]["status"] != "ok"]
    status = "down" if down else ("ok" if comp["llm"]["status"] == "ok" else "degraded")
    return {"status": status, "components": comp,
            "config": {"embedder": svc.settings.embed_model, "top_k": svc.settings.top_k, "tau": svc.settings.tau,
                       "llm_provider": svc.settings.llm_provider, "llm_model": svc.settings.llm_model}}


@app.get("/audit/{trace_id}")
async def audit_record(trace_id: str) -> dict:
    rec = audit.get(trace_id)
    if not rec:
        raise HTTPException(404, f"no audit record for trace_id {trace_id}")
    return rec


@app.get("/sources")
async def sources() -> dict:
    with session() as con:
        docs = rows(con, "SELECT * FROM source_register ORDER BY authority_level, effective_from DESC")
    for d in docs:
        d["warnings"] = json.loads(d.pop("ingest_warnings") or "[]")
        for k in ("file_path", "file_sha256", "meta_sha256"):
            d.pop(k, None)
    return {"documents": docs}


@app.post("/admin/students/load", response_model=LoadResponse, dependencies=[Depends(admin)])
async def load_students(request: Request) -> LoadResponse:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/form-data"):
        form = await request.form()
        payload: dict = {}
        for name in ("students", "courses", "attendance", "results"):
            part = form.get(name)
            if part is not None and hasattr(part, "read"):
                text = (await part.read()).decode("utf-8-sig")
                payload[name] = list(csv.DictReader(io.StringIO(text)))
    else:
        try:
            payload = LoadRequest.model_validate(await request.json()).model_dump()
        except (ValidationError, json.JSONDecodeError) as e:
            raise HTTPException(400, f"send JSON {{students, courses, attendance, results}} or CSV files: {e}")
    return await run_in_threadpool(run_load, payload)


# ----------------------------------------------------------------------------- additive (UI + seeding)
@app.get("/students")
async def students() -> dict:
    with session() as con:
        return {"students": rows(con, """SELECT student_id, full_name, programme, batch_year, current_semester
                                         FROM students ORDER BY student_id""")}


@app.get("/audit")
async def audit_list(limit: int = 30) -> dict:
    return {"items": audit.recent(max(1, min(limit, 200)))}


@app.get("/rules")
async def rules() -> dict:
    with session() as con:
        return {"rules": rows(con, "SELECT * FROM rule_registry ORDER BY parameter, effective_from")}


@app.post("/admin/rules/load", dependencies=[Depends(admin)])
async def load_rules(request: Request) -> dict:
    """Curated rules (rules_seed.csv). Each row must cite a document already in the register."""
    body = await request.json()
    added, rejected = [], []
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    with session() as con:
        for i, raw in enumerate(body.get("rules", []), start=1):
            try:
                r = RuleIn.model_validate(raw)
                doc = one(con, "SELECT * FROM source_register WHERE doc_id = ?", (raw.get("source_doc_id"),))
                if not doc:
                    raise ValueError(f"source_doc_id {raw.get('source_doc_id')} is not in the source register")
                con.execute("""INSERT INTO rule_registry (rule_id, description, parameter, operator, value, scope_programmes,
                        scope_batches, effective_from, effective_to, source_doc_id, source_section, unit, origin, status, created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,'curated','active',?)
                    ON CONFLICT(rule_id) DO UPDATE SET description=excluded.description, parameter=excluded.parameter,
                        operator=excluded.operator, value=excluded.value, scope_programmes=excluded.scope_programmes,
                        scope_batches=excluded.scope_batches, effective_from=excluded.effective_from,
                        effective_to=excluded.effective_to, source_doc_id=excluded.source_doc_id,
                        source_section=excluded.source_section, unit=excluded.unit, origin='curated', status='active'""",
                            (r.rule_id, r.description, r.parameter, r.operator, r.value, r.scope_programmes, r.scope_batches,
                             str(r.effective_from or doc["effective_from"]),
                             str(r.effective_to) if r.effective_to else doc["effective_to"],
                             doc["doc_id"], r.source_section, r.unit, now))
                # a curated rule replaces an auto-extracted one for the same clause
                con.execute("""UPDATE rule_registry SET status='rejected' WHERE origin='auto_extracted' AND parameter=?
                               AND source_doc_id=? AND source_section=?""", (r.parameter, doc["doc_id"], r.source_section))
                added.append(r.rule_id)
            except (ValidationError, ValueError) as e:
                rejected.append({"row": i, "reason": str(e)[:300]})
    return {"added": added, "rejected": rejected}


@app.get("/")
async def root() -> dict:
    return {"name": "UniAssist API", "docs": "/docs", "health": "/health"}
