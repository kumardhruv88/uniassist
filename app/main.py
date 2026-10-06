"""FastAPI app: the fixed contract (guide §6) plus a few additive read endpoints for the UI."""
from __future__ import annotations

import csv
import io
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import ValidationError

from app import audit, cache, errors, metrics
from app.conversation import sessions
from app.db import bump_data_version, data_version, init_db, one, rows, session
from app.graph.pipeline import ask as run_ask
from app.ingestion.pipeline import ingest as run_ingest
from app.loader import load as run_load
from app.models import AskRequest, AskResponse, IngestMetadata, IngestResponse, LoadRequest, LoadResponse, RuleIn
from app.security import events
from app.security.ratelimit import RateLimiter
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


limiter: RateLimiter | None = None


def data_changed() -> None:
    """Every write that can change an answer (ingest, rules, students) bumps the data version, so answers cached
    under the old version are never served again. The in-memory answer caches are cleared to free the memory."""
    with session() as con:
        bump_data_version(con)
    cache.answers.clear()
    cache.semantic.clear()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global limiter
    init_db()
    svc = get_services()
    s = svc.settings
    limiter = RateLimiter(per_minute=s.rate_limit_per_min, burst=s.rate_limit_burst, block_after=s.abuse_block_after,
                          window_s=600, block_s=s.abuse_block_s)
    await run_in_threadpool(lambda: svc.embedder)        # warm the embedding model once
    await run_in_threadpool(_self_check)
    if s.llm_provider != "mock":                         # load the LLM into memory without delaying startup
        threading.Thread(target=svc.llm.warmup, daemon=True, name="llm-warmup").start()
    log.info("ready: llm=%s/%s embed=%s collection=%s retrieval=%s reranker=%s planner=%s", s.llm_provider, s.llm_model,
             s.embed_model, s.collection_name, s.retrieval_mode, s.reranker, s.planner)
    yield


app = FastAPI(title="UniAssist API", version="1.1.0", lifespan=lifespan,
              description="AI-Powered University Student Services Assistant — HCLTech Future Ready AI Engineer Hackathon")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
                   expose_headers=["Retry-After"])
errors.install(app)


def admin(x_admin_token: str | None = Header(default=None)) -> None:
    token = get_services().settings.admin_token
    if token and x_admin_token != token:
        raise HTTPException(401, "missing or wrong X-Admin-Token")


# ----------------------------------------------------------------------------- contract
@app.post("/ask", response_model=AskResponse)
async def ask(req: AskRequest, request: Request, x_student_id: str | None = Header(default=None),
              x_session_id: str | None = Header(default=None)) -> AskResponse:
    svc = get_services()
    s = svc.settings
    client = request.client.host if request.client else "unknown"
    if s.rate_limit_enabled and limiter:
        allowed, code, retry_after = limiter.check(client)
        if not allowed:
            metrics.inc("rate_limited", code=code)
            if code == "RATE_LIMITED":
                events.record("rate_limited", client, {"retry_after_s": round(retry_after, 1)}, x_student_id)
            raise errors.AppError(code, "Too many requests from this client. Wait and try again." if code == "RATE_LIMITED"
                                  else "This client is temporarily blocked after repeated blocked requests.", 429,
                                  retry_after=retry_after)
    session_id = (x_session_id or "").strip()[:64] or None
    resp = await run_in_threadpool(run_ask, svc, req.question, x_student_id, req.as_of_date, session_id)
    if resp.meta.guardrail:
        events.record("guardrail_block", client, {"reason": resp.meta.guardrail}, resp.student_id, resp.trace_id)
        if s.rate_limit_enabled and limiter and limiter.strike(client):
            events.record("client_blocked", client, {"after": s.abuse_block_after, "for_s": s.abuse_block_s},
                          resp.student_id, resp.trace_id)
    if resp.meta.output_redactions:
        events.record("output_redaction", client, {"removed": resp.meta.output_redactions}, resp.student_id, resp.trace_id)
    return resp


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
    res = await run_in_threadpool(run_ingest, data, file.filename or "upload.txt", meta, svc)
    if res.status in ("indexed", "replaced"):
        data_changed()
    return res


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
    s = svc.settings
    return {"status": status, "components": comp,
            "config": {"embedder": s.embed_model, "top_k": s.top_k, "tau": s.tau, "llm_provider": s.llm_provider,
                       "llm_model": s.llm_model, "llm_fallback_model": s.llm_fallback_model, "cloud_fallback": s.cloud_fallback,
                       "retrieval_mode": s.retrieval_mode, "reranker": s.reranker, "planner": s.planner,
                       "context_budget_tokens": s.context_budget_tokens},
            "caches": {"answers": len(cache.answers), "llm": len(cache.llm), "data_version": data_version(),
                       "enabled": {"answers": s.answer_cache, "semantic": s.semantic_cache, "llm": s.llm_cache}},
            "security": {"rate_limit_per_min": s.rate_limit_per_min if s.rate_limit_enabled else None,
                         "abuse_block_after": s.abuse_block_after, "input_guardrails": True, "output_guardrails": True}}


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
    res = await run_in_threadpool(run_load, payload)
    if any(res.accepted.values()):
        data_changed()
    return res


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
    if added:
        data_changed()
    return {"added": added, "rejected": rejected}


@app.get("/metrics", response_class=PlainTextResponse)
async def prometheus_metrics() -> str:
    """Prometheus text format: requests by answer type and cache, node and LLM latency histograms, LLM calls, tokens,
    errors and fallbacks per provider, cache hits and misses, guardrail blocks, rate limiting, degraded answers."""
    return metrics.render()


@app.get("/security/events", dependencies=[Depends(admin)])
async def security_events(limit: int = 50) -> dict:
    return {"events": events.recent(max(1, min(limit, 500)))}


@app.get("/")
async def root() -> dict:
    return {"name": "UniAssist API", "docs": "/docs", "health": "/health", "metrics": "/metrics"}
