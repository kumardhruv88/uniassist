# UniAssist: build TODO (live, A to Z against the Participant Guide)

LLM: **Ollama `llama3.1:8b`** (already pulled on this machine). `[x]` = built, `[~]` = in progress, `[ ]` = not started.

## Guide §2: questions the assistant must handle
- [x] Policy fact: rule in force today, with source, section, version, effective date
- [x] Procedure: steps quoted from documents, nothing invented
- [x] Personal data: computed by tools, logged-in student only
- [x] Personal eligibility: deterministic check against the cited rule
- [x] Multi-step / what-if: several sources and tools, assumptions stated
- [x] Not answerable: "I could not find this information in the authorised university sources."

## Guide §3: requirements
- [x] R1–R2 Grounded answers with citations; never fabricate (evidence-ID citations, numeric grounding, τ gate)
- [x] R3 Versions and effective dates, `as_of_date` (register applicability, append-only rules)
- [x] R4 Annex A precedence: applicability → supersession → authority → recency → conflict_flagged
- [x] R5–R6 Personal answers only through tools, only for the `X-Student-Id` student
- [x] R7 Multi-step and what-if
- [x] R8 Document text is data, never instructions (planner never sees docs, composer has no tools, redaction)
- [x] R9 `answer_type`: retrieved_fact, calculated, not_found, clarification_needed, refused, conflict_flagged
- [x] R10 `trace_id` and audit record (sources, tools with I/O, rules, conflicts, model, latency, tokens; no chain-of-thought)
- [x] R11 Live ingestion via `POST /ingest`, used on the next request, no restart
- [ ] R12 Evaluation set and measured results (see §7 below)

## Guide §4: data
- [x] 4.1 Demo documents (team decision: the 7 sample documents in `data/corpus/documents/` are the demo corpus): multiple versions, tables (fees, grading), a scanned page (OCR), cross-references (7.3 → 7.2, 8.3 → 5.2), five authority levels, plus the live-demo circular `data/corpus/demo/ACAD-2026-08`
- [x] 4.1 `source_register.csv` (Annex B) for every document
- [x] 4.2 Synthetic students generated with `llama3.1:8b`: 40 students, 2 programmes, 2 batches, 12 courses (8 calls, 0 repairs)
- [x] 4.2 Edge cases: exactly at threshold, one class below, fail by one mark, absent, detained, multiple backlogs, CGPA at cut-off (14 students, exact overlay, check V15)
- [x] 4.2 Validation script `data/synthetic/validate.py` (16 checks; thresholds read from the rule registry)
- [x] 4.2 Loader endpoint `POST /admin/students/load`
- [x] 4.2 Loader CLI `python scripts/load_students.py --dir test_students/` (stdlib client of the endpoint)
- [x] 4.2 Submit prompts verbatim, model, generator script, validation output, data card (Annex E): `data/synthetic/DATA_CARD.md`

## Guide §5: technology stack
- [x] React UI (registry-desk design; mock mode; nginx in Docker), verified against the live API: follow-ups shown as "Read as", cache hits, guardrail refusals, groundedness, what-if rows, degraded notice, 429 countdown, audit fields, security events, status panel configuration (44 screenshots)
- [x] What-if detector reads negation ("without a medical certificate")
- [x] FastAPI + Uvicorn + Pydantic v2
- [x] LangGraph orchestration (8 nodes, 2 LLM calls)
- [x] ChromaDB persisted to disk; no re-ingest on restart (SHA idempotency + startup self-check)
- [x] SQLite for students, rules, register, audit (STRICT tables)
- [x] sentence-transformers `BAAI/bge-small-en-v1.5`
- [x] Ollama local (`llama3.1:8b`), JSON-schema structured output
- [x] Docker + docker compose (Ollama on host; optional `ollama` profile)
- [x] MOCK_LLM mode for tests only

## Guide §6: API contract
- [x] `POST /ask` (X-Student-Id, question, as_of_date) with the §6.1 response format
- [x] `POST /ingest` (multipart file + Annex B metadata) → doc_id, chunks_indexed, status
- [x] `GET /health` (API, vector store, SQLite, LLM)
- [x] `GET /audit/{trace_id}`
- [x] `GET /sources`
- [x] Test-student loader (endpoint + stdlib CLI `scripts/load_students.py`)
- [x] Extras for the UI: `GET /students`, `GET /audit`, `GET /rules`, `POST /admin/rules/load`

## Guide §7: evaluation (mandatory)
- [ ] ≥20 questions with expected answer and source (target 36): ≥3 unanswerable, ≥3 versions/conflicts, ≥4 personal via tools, ≥2 other-student attempts, ≥2 multi-step
- [ ] Metrics: answer correctness, citation accuracy, abstention accuracy, tool-result correctness, retrieval hit rate, p50/p95 latency, LLM calls and tokens
- [ ] Method stated; compare two configurations (MiniLM + fixed chunks vs bge-small + clause chunks)

## Guide §8: deliverables
- [x] README: architecture diagram, setup and run steps, curl commands, production features, assumptions, limitations (eval numbers to add from REPORT.md)
- [x] `docker compose up` starts API, seed job and UI (verified: OCR in the container, restart keeps the index); rebuild after today's backend changes
- [x] Source register CSV + documents
- [x] Rule registry in SQLite, every rule linked to a cited clause (`rules_seed.csv` + auto-extracted claims)
- [x] Synthetic data kit: prompts, generator, validation script and output, data card
- [ ] Evaluation set and report
- [x] Four sample audit records in `docs/audit_samples/` (calculated, retrieved_fact what-if, refused by guardrail, not_found)
- [x] AI-usage disclosure (`AI_USAGE.md`: development tools, runtime models, generated data; team review section to fill)
- [x] Team contribution statement (`CONTRIBUTIONS.md`: Garv = frontend, Manish = LangGraph orchestration, Dhruv = eval + production RAG; rest to fill)
- [x] Signed declaration of original work (`DECLARATION.md`, to sign)
- [~] GitHub: https://github.com/kumardhruv88/uniassist (public). History authored per member with GitHub no-reply addresses and the Claude co-author trailer; dates not backdated
  - Manish Kumar (`m4nish-dev`): 13 commits, the core end-to-end build including LangGraph orchestration
  - Dhruv Kumar (`kumardhruv88`): production-RAG layer, synthetic data, golden dataset + eval, docs
  - Garv Bahl (`garvbahl37-gif`): frontend UI/UX
  - Branches `garv`, `dhruv`, `manish` created from `main` for each member's work; merge back through pull requests
- [ ] Rotate the GitHub tokens shared during setup

## Guide §9: judging readiness
- [ ] Demo: cited policy answer, tool-based eligibility, not_found, conflict resolved
- [ ] Live test rehearsal: ingest an unseen circular, load test students, unseen questions
- [x] pytest suite green: 55 tests (precedence T1–T13, arithmetic traps, scope, answer types, live ingestion, refusals, audit, plus 30 production-feature tests)

## Production-grade RAG (extended scope, requested 6 Oct)
Each item names the technique used. `[x]` built and tested, `[~]` in progress, `[ ]` next.

### LLM gateway and fallbacks
- [x] `app/llm/gateway.py`: one interface for every LLM call (plan, condense, compose); per-task timeouts and output caps (`num_predict`)
- [x] Error kinds decide the action: connection errors retry with backoff + jitter, timeouts move to the next model, bad output goes back to the caller; circuit breaker counts availability failures only (open after 3, half-open probe after 30 s)
- [x] Fallback chain: `llama3.1:8b` → optional `LLM_FALLBACK_MODEL` → optional disclosed cloud endpoint (off) → deterministic templates; `meta.degraded` tells the UI
- [x] Concurrency limit (semaphore); per-provider calls, tokens, errors, fallbacks and latency histograms in `/metrics`; breaker state in `/health`

### Caching (input/output)
- [x] Answer cache: key = normalised question + student + as_of + data version + config; any ingest, rules load or students load bumps the version
- [x] LLM response cache (exact prompt hash) and query-embedding LRU cache (batched encode)
- [x] Semantic cache for general questions, per programme + batch scope: cosine ≥ 0.95 AND the same content terms after stemming + glossary mapping (stops B.Tech/M.Tech near-misses)
- [x] Cache hits get their own trace_id and audit record (`cache.hit`, `source_trace_id`); personal answers never shared; degraded or redacted answers never cached

### Token optimisation
- [x] Token budget for the composer context (`CONTEXT_BUDGET_TOKENS`=1400); anchors first, then by rank
- [x] Extractive compression: long non-anchor, non-table chunks keep the opening + query-relevant sentences; tokens saved in `meta` and the audit
- [x] Near-duplicate chunk removal (Jaccard ≥ 0.8 on stemmed terms); compact evidence tags and tool facts
- [x] Skip LLM calls when code already knows the answer (templates for refused / clarification / not_found; router-first planning)
- [ ] Tokens per question tracked in the eval report

### Latency optimisation
- [x] Router-first planning: personal questions with a clear tool skip the planner LLM (measured: 2.5 s instead of ~3.8 s, 1 LLM call)
- [x] Model warm-up at startup (background thread) and `keep_alive`; all query variants embedded in one batch and searched in one Chroma call
- [~] Per-node latency histograms in `/metrics` (done); p50 / p95 per stage in the eval report (next)

### Hallucination control
- [x] Citations restricted to retrieved evidence IDs; numeric grounding; false-comparison detector; τ gate (0.68, calibrated); evidence-coverage gate
- [x] Lower-precedence evidence hidden from the composer, replaced by a one-line "set aside" note (fixes VC3's "65% is enough")
- [x] Policy what-if calculator `check_attendance_value`: "Is 65% enough with a medical certificate?" decided by code (80% minimum, up to 10% condonable → 70% floor); new rule parameter `max_condonation_pct` (ATT-COND-01, §7.3)
- [x] False-comparison detector ignores clause boundaries ("at most 10%, so attendance must be at least 70%"); citations de-duplicated per clause; a stated rule value cites its clause
- [x] Groundedness score per answer (share of sentences supported by verdict, records, rules or evidence); below 0.34 the draft is retried, then replaced by a quote
- [x] Hallucination rate in the eval report (B: 0.7%; mean groundedness 0.94)

### Security guardrails
- [x] Input guardrails: prompt injection, jailbreak, base64-encoded payloads, bulk / other-student requests, record-change requests, abuse; PII redacted before audit
- [x] Output guardrails: no other student's ID, no system-prompt leakage, no phone or email the sources don't contain
- [x] Document-level injection scanning and redaction; identity injected by code; refusals by code
- [x] Rate limiting (token bucket per client, 60/min, burst 20) and abuse detection (5 guardrail blocks in 10 min → 5-minute block); 429 with Retry-After
- [x] Security event log (SQLite) + `GET /security/events` (admin) + counters in `/metrics`

### Error control
- [x] Structured errors (`{"error": {code, message, hint, trace_id}}` + FastAPI's `detail` kept for clients), no stack traces leaked
- [x] Degraded mode when the LLM is down (refusals, clarifications and calculated verdicts keep working; `meta.degraded`)

### Context-aware understanding
- [x] Domain glossary query expansion (bunk → attendance, supply/re-exam → supplementary, KT/arrears → backlog, hall ticket → admit card)
- [x] Hybrid retrieval: BM25 + dense with reciprocal rank fusion (default); cosine still drives the abstention gate
- [~] Optional cross-encoder reranker (`RERANKER=cross-encoder/ms-marco-MiniLM-L-6-v2`): built, to be measured as config C
- [x] Session follow-ups (`X-Session-Id`, bound to the student): course swap, answer to a clarification, LLM rewrite for the rest; `meta.standalone_question`

### Evaluation suite and pipeline
- [x] Black-box harness over `/ask` + `/audit` with all §7 metrics (run 3: 97.3% correct, 100% retrieval hit, p50 3.3 s)
- [x] Golden dataset expanded to 146 items (eval agent): paraphrases, adversarial injection and jailbreak, PII and bulk exfiltration, follow-ups, cache checks
- [~] Config comparison A vs B vs C (fixed chunks + MiniLM / clause + bge-small + hybrid / + rerank), τ calibrated for A. B done: 93.1% correct, 89.3% citation accuracy, 97.4% abstention, 100% retrieval hit, 100% injection resistance, 0.7% hallucination, p50 3.0 s / p95 5.4 s, 1.42 LLM calls and ~1,300 tokens per question; C running
- [ ] LLM-as-judge (prompt disclosed) calibrated against exact-match grades
- [ ] `make eval` pipeline: pytest → golden eval → report → regression gates
