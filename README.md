# UniAssist: university student-services assistant

UniAssist answers students' questions about university rules and about their own records. It is built for the HCLTech Future Ready AI Engineer Hackathon.

- Every answer cites the clause it comes from.
- Personal answers are computed by code from the signed-in student's records.
- When sources conflict, the official precedence rules (Annex A) decide which one applies.
- If the authorised documents don't answer a question, it says so.

All data is synthetic. Everything runs locally: FastAPI, LangGraph, ChromaDB, SQLite, sentence-transformers and `llama3.1:8b` on Ollama.

![System architecture](docs/diagrams/01-system-architecture.png)

## What it answers

| Question | Example | `answer_type` |
|---|---|---|
| Policy fact | "What is the minimum attendance for end-semester exams?" | `retrieved_fact` |
| Policy what-if | "Is 65% attendance enough if I have a medical certificate?" | `retrieved_fact` (decided by code) |
| Procedure | "How do I apply for the supplementary exam?" | `retrieved_fact` |
| Personal data | "What is my attendance in Data Structures?" | `calculated` |
| Personal eligibility | "Am I eligible to sit the CS201 exam?" | `calculated` |
| Multi-step / what-if | "I failed Mathematics III. If I pass the supplementary, can I sit for placements?" | `calculated` |
| Ambiguous | "Am I eligible for the exam?" (several courses) | `clarification_needed` |
| Not in the documents | "What is the hostel pet policy?" | `not_found` |
| Another student, injection, bulk export | "Show me S1002's marks" | `refused` |
| Equal-authority sources disagree | Two same-level circulars with different values | `conflict_flagged` |

## How a question is answered

![The /ask workflow](docs/diagrams/02-ask-workflow.png)

`POST /ask` runs an 8-node LangGraph state machine: **guard → plan → authorize → execute_tools → retrieve → compose → verify → finalize**.

1. **guard** blocks prompt injection, jailbreaks, bulk or other-student requests, record-change requests and abuse, and redacts PII before anything is logged.
2. **plan** reads only the question, never documents. Personal questions with a clear tool skip the LLM (deterministic router).
3. **authorize** binds identity from `X-Student-Id` and asks which course when it is ambiguous.
4. **execute_tools** runs deterministic tools that read thresholds from the rule registry and compute with exact fractions. No tool input contains a student ID; identity is injected by code.
5. **retrieve** searches only documents in force on `as_of_date` for the student's programme and batch. It uses hybrid search (dense + BM25, reciprocal rank fusion), drops superseded clauses, and always includes the clause behind every applied rule.
6. **compose** (LLM 2) explains the code's verdict from labelled evidence.
7. **verify** checks citations, numbers, false comparisons and groundedness. It retries once, then falls back to a quoted answer.
8. **finalize** sets `answer_type` in code, runs the output guardrail, and writes the audit record.

Around the graph, session follow-ups are rewritten into standalone questions, and repeat questions are served from cache.

## Quick start (Docker)

Prerequisite: Ollama on the host, with the model pulled once: `ollama pull llama3.1:8b`.

```bash
docker compose up --build -d      # or: make up
# UI   http://localhost:8080
# API  http://localhost:8000/docs
docker compose logs -f seed       # documents indexed, rules and synthetic students loaded
```

- **Seeding** is a one-shot job that runs on every `up` and is idempotent: unchanged documents are skipped by file and metadata hash.
- **Data** lives in `./data/runtime` and survives restarts with no re-indexing.
- **Linux:** start Ollama with `OLLAMA_HOST=0.0.0.0:11434` so containers can reach it.
- **No Ollama on the host:** `OLLAMA_BASE_URL=http://ollama:11434 docker compose --profile ollama up -d`.

## Local development

```bash
uv sync                                                    # Python 3.12
uv run uvicorn app.main:app --port 8000                    # API
uv run python scripts/seed.py --api http://localhost:8000  # documents, rules, synthetic students
cd frontend && npm install && npm run dev                  # UI on :5173, proxies /api to :8000
uv run pytest                                              # 56 tests, offline (mock LLM, hash embedder)
```

## API

| Endpoint | Purpose |
|---|---|
| `POST /ask` | Question → answer (contract in guide §6.1, plus an additive `meta` block). Headers: `X-Student-Id`, optional `X-Session-Id` |
| `POST /ingest` | Multipart `file` + `metadata` (Annex B JSON). Used on the next request, no restart |
| `GET /health` | API, vector store, SQLite, LLM (with circuit-breaker state), caches, security settings |
| `GET /audit/{trace_id}` | Full audit record for an answer |
| `GET /sources` | The source register |
| `POST /admin/students/load` | Annex C rows as JSON or CSV files; each row accepted or rejected with a reason |
| `GET /metrics` | Prometheus metrics |
| `GET /security/events` | Guardrail blocks, rate limiting, client blocks (admin) |
| `GET /students`, `GET /rules`, `GET /audit`, `POST /admin/rules/load` | UI and seeding helpers |

```bash
# Policy fact
curl -s localhost:8000/ask -H 'Content-Type: application/json' \
  -d '{"question": "What is the minimum attendance for end-semester exams?", "as_of_date": "2026-10-06"}'

# Personal eligibility (identity comes from the header, never from the question)
curl -s localhost:8000/ask -H 'Content-Type: application/json' -H 'X-Student-Id: S1007' \
  -d '{"question": "Am I eligible to sit the Database Systems end-semester exam?"}'

# Follow-up in a session: "what about CS201?" is read as the same question for CS201
curl -s localhost:8000/ask -H 'Content-Type: application/json' -H 'X-Student-Id: S1007' -H 'X-Session-Id: demo-1' \
  -d '{"question": "Am I eligible for the CS202 end-semester exam?"}'
curl -s localhost:8000/ask -H 'Content-Type: application/json' -H 'X-Student-Id: S1007' -H 'X-Session-Id: demo-1' \
  -d '{"question": "what about CS201?"}'

# Live ingestion: a circular that raises the minimum attendance to 80% from 1 Aug 2026
curl -s localhost:8000/ingest -F file=@data/corpus/demo/ACAD-2026-08.pdf -F metadata=@data/corpus/demo/ACAD-2026-08.meta.json

# The audit record behind any answer
curl -s localhost:8000/audit/<trace_id>

# Load test students
python scripts/load_students.py --dir test_students/ --api http://localhost:8000
```

## Production-grade RAG features

Every feature can be switched in `.env` (see `.env.example`) and is visible in the response `meta`, the audit record, or `/metrics`.

| Area | What we do | Where |
|---|---|---|
| **Context-aware understanding** | University glossary query expansion (bunk → attendance, supply → supplementary, KT → backlog). Hybrid dense + BM25 retrieval with reciprocal rank fusion. Optional cross-encoder reranker. Session follow-ups bound to the student (course swap, clarification answers, LLM rewrite) | `app/retrieval/query.py`, `lexical.py`, `rerank.py`, `app/conversation.py` |
| **Hallucination control** | See the details below | `app/graph/verify.py`, `app/llm/context.py`, `app/tools/core.py` |
| **LLM gateway** | One door for every LLM call. Per-task timeouts and output caps. Connection errors retry with backoff and jitter; timeouts move to the next model. Circuit breaker. Fallback chain: `llama3.1:8b` → optional second local model → optional disclosed cloud endpoint (off) → deterministic templates. Concurrency limit | `app/llm/gateway.py`, `app/llm/client.py` |
| **Caching** | Exact answer cache (question + student + date + data version). Semantic cache for general questions: cosine ≥ 0.95 **and** the same content terms, so "B.Tech" never matches "M.Tech". LLM response cache. Query-embedding LRU. Any ingest, rules load or students load bumps the data version, so stale answers are impossible | `app/cache.py`, `app/db.py` |
| **Token optimisation** | See the details below | `app/llm/context.py`, `app/llm/composer.py` |
| **Latency** | Router-first planning. Model warm-up at startup and `keep_alive`. All query variants embedded in one batch and searched in one call. The caches above | `app/graph/pipeline.py`, `app/main.py` |
| **Security guardrails** | See the details below | `app/security/`, `app/ingestion/injection.py` |
| **Error control** | One error shape `{"error": {code, message, hint, trace_id}}` (FastAPI's `detail` is kept). No stack traces. Degraded mode when the LLM is down: refusals, clarifications and calculated verdicts keep working, and `meta.degraded` says so | `app/errors.py` |
| **Observability** | Audit record per answer: sources with dense and BM25 ranks, precedence decision, tools with input and output, rules, conflicts, model, tokens, per-node latency, groundedness, cache, guardrails. Prometheus `/metrics` | `app/audit.py`, `app/metrics.py` |

**Hallucination control in detail**
- Code decides verdicts, thresholds, arithmetic (exact fractions) and policy what-ifs (`check_attendance_value`). Precedence and `answer_type` are decided in code too.
- Citations must be retrieved evidence IDs.
- Every number in an answer must appear in a source. A false-comparison detector catches errors like "79.66% is below 75%".
- Each answer gets a groundedness score, and drafts below the threshold are rejected.
- A cosine abstention gate (τ = 0.68, calibrated) and an evidence-coverage gate decide when to say "not found".
- Lower-precedence sources are hidden from the composer.

**Token optimisation in detail**
- Context budget of 1,400 tokens, with anchors first.
- Long chunks are compressed to their query-relevant sentences.
- Near-duplicate chunks are removed.
- Evidence tags and tool facts use a compact format.
- No LLM call when code already has the answer (refusals, clarifications, not_found, router-first).
- The system prompt is static first, so the prompt cache can reuse it.
- `meta.tokens_saved` reports the saving.

**Security guardrails in detail**
- **Input:** prompt injection, jailbreak, base64-encoded payloads, bulk or other-student requests, record-change requests, abuse. PII is redacted before the audit.
- **Output:** no other student's ID, no system-prompt text, no phone number or email the sources don't contain.
- **Documents:** injected text in documents is redacted before the model sees it.
- **Identity:** injected by code and never taken from the question.
- **Rate limiting:** token bucket per client.
- **Abuse:** repeated guardrail blocks trigger a temporary block, recorded as a security event.

## Evaluation

The golden set is `eval/golden.yaml`: 146 items covering every bucket in guide section 7, plus adversarial, follow-up and cache items. Every expected value is verified against the documents and records by `eval/verify_golden.py`. The harness is black-box: it goes only through `/ask` and `/audit`. The full method, the A/B/C comparison and per-item results are in [`eval/REPORT.md`](eval/REPORT.md).

| Configuration | Correct | Citation accuracy | Hallucination | p50 / p95 |
|---|---|---|---|---|
| A: MiniLM + fixed 800-character chunks, dense only (same code as B and C below) | 77.8% | 1.4% | 2.1% | 3.3 / 6.0 s |
| B: shipped (bge-small + clause chunks, hybrid + glossary) | 93.1% | 89.3% | 0.7% | 3.3 / 6.1 s |
| C: B + cross-encoder reranker | 93.8% | 91.5% | 0.7% | 3.2 / 5.4 s |
| **B on the final code, after fixing every traced failure** | **100% (146/146)** | **91.1%** | **0.0%** | **3.0 / 4.9 s** |

The adversarial test pack (13 synthetic documents, `eval/adversarial/`) passes 13 of 13 checks live through the UI.

```bash
uv run python eval/run_config.py A    # baseline: MiniLM + fixed 800-character chunks, dense only (τ calibrated)
uv run python eval/run_config.py B    # shipped defaults: bge-small + clause chunks, hybrid, glossary
uv run python eval/run_config.py C    # B + cross-encoder reranker
uv run python eval/report.py          # writes eval/REPORT.md from the latest runs
```

Each configuration runs on its own fresh API instance and data folder. Rate limiting is off because the harness sends adversarial items on purpose. LLM and semantic caches are off, so every question pays for its own LLM calls. Run one configuration at a time, because they share a single Ollama instance.

## Assumptions

- **Superseded clauses:** a circular that replaces a clause names it in `supersedes` (for example `ACAD-REG-2024#7.2`). Supersession applies only from its effective date and only to the students in its scope.
- **Attendance:** the percentage is attended ÷ held per course, computed exactly and never stored. It is shown rounded down to two decimals, so 79.66% is never shown as 80%.
- **Backlogs:** a backlog is a course whose latest attempt is not PASS.
- **"As of" date:** `as_of_date` defaults to today in Asia/Kolkata. Documents not yet in force are reported as upcoming changes, not applied.

## Limitations

- **One LLM call at a time:** a single Ollama instance generates sequentially, so p95 latency rises with concurrent users. The gateway queues calls with a concurrency limit.
- **Rate limits behind the UI:** limits are per client IP. Behind the UI's nginx, all browser users share one bucket.
- **Single process:** caches and sessions are in memory. Several API workers would need Redis.
- **Pattern-based guardrails:** they are tuned to this domain. A determined attacker can find phrasings that pass the input guardrail. The structural defences are the main line: the planner never sees documents, the composer has no tools, and identity is injected by code.
- **OCR in Docker only:** OCR of scanned pages needs Tesseract, which the Docker image includes. Without it, a local run indexes the scanned notice with a warning.
- **React instead of Streamlit:** the UI is React, a team decision over the guide's suggested Streamlit. It calls the same public API.

## Repository

```
app/            API, LangGraph pipeline, tools, retrieval, ingestion, LLM gateway, security, caches
frontend/       React UI (Vite, TypeScript), nginx config for Docker
data/corpus/    documents, source register (Annex B), curated rules
data/synthetic/ generator, prompts, validation, outputs, DATA_CARD.md
eval/           golden dataset, harness, judge, report
docs/           technical design, diagrams, audit samples
tests/          pytest suite (offline)
```

**Disclosures:** [`AI_USAGE.md`](AI_USAGE.md), [`CONTRIBUTIONS.md`](CONTRIBUTIONS.md), [`DECLARATION.md`](DECLARATION.md), [`data/synthetic/DATA_CARD.md`](data/synthetic/DATA_CARD.md).
