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
- [~] GitHub: https://github.com/kumardhruv88/uniassist (public). History authored per member with GitHub no-reply addresses; dates not backdated. Claude co-author lines removed from every commit at the team's request (AI use stays disclosed in `AI_USAGE.md`)
  - Manish Kumar (`m4nish-dev`): 13 commits, the core end-to-end build including LangGraph orchestration
  - Dhruv Kumar (`kumardhruv88`): production-RAG layer, synthetic data, golden dataset + eval, docs
  - Garv Bahl (`garvbahl37-gif`): frontend UI/UX
  - Branches `garv`, `dhruv`, `manish` created from `main` for each member's work; merge back through pull requests
  - [x] Collaborator access granted on kumardhruv88/uniassist
  - [x] Pushed `main`, `garv`, `dhruv`, `manish` (34 commits)
  - [x] Force-pushed the rewritten history (no Claude lines); all branches at `b907e94`. GitHub credits kumardhruv88 (17), m4nish-dev (13), garvbahl37-gif (6)
  - [ ] Branch list "Updated" shows the last pusher, not the author: Dhruv and Manish each recreate (or push to) their own branch while signed in as themselves
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
- [x] `make eval` pipeline: pytest → golden eval → report → regression gates (Makefile: test, eval, eval-all, eval-a/b/c, judge, report, verify-golden)
- [x] LLM-as-judge calibrated: 90.3% agreement with exact match on B (κ 0.37); blind spot documented (rates a wrong "not found" as correct), so exact match stays primary
- [x] Fixed the 10 B failures traced in `eval/failure_analysis.yaml`: coverage stop-list (TF6, PR10), eligibility-wording check only for personal questions + fallback quotes the cited clause (PF12), "which students have a CGPA…" bulk pattern (BK4), hidden-instructions / "repeat everything above" injection patterns (ADV7), "give/attempt" as personal verbs (PT13), "not mentioned" drafts → not_found (NA9), applied rule's value always stated (VC2), every table variant listed (TF1), course carried into pronoun follow-ups (FU3b)
- [x] Re-ran B after the fixes: 99.3% correct (143/144 scored), every bucket 100% except unanswerable 90% (NA9, fixed after the run), citation accuracy 89.8%, p50 3.1 s / p95 5.0 s
- [x] Final B run on the current code: **100% correct (146/146, every bucket)**, citation accuracy 91.1%, abstention 100%, hallucination 0.0%, groundedness 0.94, p50 2.97 s / p95 4.93 s, 1.38 AI calls and ~1,330 tokens per question (`eval/runs/B/20261006-153726*`); REPORT.md, README and guides refreshed
- [x] Presenter's guide explains the upload form: every Annex B field and which precedence check it drives (authority → check 3, dates and scope → check 1, replaces → check 2), with a script and code locations
- [x] Presenter's guide for the evaluation / security / golden-dataset part: `docs/PRESENTER_GUIDE.md/.pdf` (opening, 12-step live demo, file:line code map, metrics, results, cross-questions)

### Aster University test pack (judge rehearsal)
- [x] 13 synthetic documents built from the test manifest (`eval/adversarial/pack/`, `make_pack.py`): baseline, supersession, exact duplicate, false FAQ, unofficial post, future-dated rule, out-of-scope B.Arch, visible + hidden injection, tied fee circulars, scanned OCR notice, irrelevant library hours, keyword-stuffed post
- [x] One `.meta.json` per document, plus `JUDGE_TEST_GUIDE.md/.pdf`: clean-instance setup, upload order, 11 questions with expected answers
- [x] Live test through the real UI in a visible browser (Playwright, `frontend/scripts/aster-live-test.mjs`): all 13 documents uploaded with "Fill the fields from a metadata file", 11 questions + duplicate + injection checks: **13/13 pass** (screenshots and results in `eval/adversarial/ui-run/`)
- [x] Tesseract 5.5.3 installed locally (Homebrew), so scanned pages are read outside Docker too; the demo corpus' scanned hostel notice is now indexed (4 chunks)
- [x] Parser: OCR for scanned images on pages that also have printed text; a warning when an image cannot be read; Annex B metadata header tables are not indexed as policy text
- [x] Verifier: an unsupported negative claim ("you do not need to attend …") is rejected; conflict explanations are written by code, never by the model
- [ ] Team rehearsal with the same pack before the judges (`JUDGE_TEST_GUIDE.pdf`)

### Live-use fixes
- [x] Personal questions without "my" are recognised: "how many backlogs i have", "do I have any backlogs", "what marks did I get", "am I detained", "how much attendance do i have" (was answered "not on record"); "how many classes have I attended" reads attendance instead of projecting; 11 regression tests; the 146 golden questions route as before (only ADV6 changes, and the guardrail refuses it first)

- [x] "what subjects i have backlogs in … total number of subjects" answered: the profile tool now lists backlog subjects and every course on record; informal "<subjects> i have" phrasing and subject-list questions route to it
- [x] Students page says the 40 synthetic students are already loaded, so nothing needs uploading for the demo

### Project explainer
- [x] `docs/UNIASSIST_EXPLAINED.md/.pdf`: the whole project in five plain-language pages (what it does, how a question is answered, conflicts, safety and honesty, proof and results, running it), with two diagrams and three live screenshots
- [x] Dates written in the question set `as_of` when the request has none ("As of 2026-12-10", "in October 2026")
- [x] Programme and batch written in the question scope policy answers ("for B.Arch batch 2025"); personal tools keep the student's own scope
- [x] Equal-rank, same-date sources stating different amounts give `conflict_flagged` with both cited (fees and other non-rule facts)
- [x] Unicode NFKC at parse time: ligatures (ﬁ ﬂ ﬀ) no longer break search, claims or prompts
- [x] No "§" sign in answers, notes, warnings or the UI ("section 7.2")
- [x] UI: fill the upload form from a `.meta.json`; the "Rules as of" date is sent only when changed, so a date in the question applies
