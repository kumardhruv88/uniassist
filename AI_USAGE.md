# AI usage disclosure

The team built this project with AI assistance. This file says where AI was used, which models, and what stays deterministic.

## 1. AI used to build the project (development time)

| What | Tool | How |
|---|---|---|
| Architecture docs, technical design document, diagrams | Claude Code (Anthropic, Claude Opus) | Drafted from the Participant Guide pages on the team's instructions; edited over several rounds |
| Backend code (`app/`), tests, scripts, Docker files | Claude Code | Generated and revised on each team member's prompts; run and tested locally (`pytest`, live runs against Ollama) |
| React frontend (`frontend/`) | Claude Code | Generated on the team's prompts, with a design brief and screenshot critique passes |
| Evaluation suite (`eval/`) and golden dataset | Claude Code | Items written against the guide's evaluation buckets; expected values checked by `eval/verify_golden.py` against the corpus and the synthetic records |

Commits made with Claude Code carry a `Co-Authored-By: Claude` trailer. `CONTRIBUTIONS.md` says which team member directed which part.

**To be completed by the team:** what you reviewed, changed or decided yourselves, and anything you rejected from the AI's output.

## 2. AI inside the running system (runtime)

| Use | Model | Where |
|---|---|---|
| LLM 1: planning (question → JSON plan). Skipped when deterministic routing is sure | `llama3.1:8b` via local Ollama, temperature 0, JSON-schema output | `app/llm/planner.py` |
| Follow-up rewriting (session follow-up → standalone question) | `llama3.1:8b` | `app/conversation.py` |
| LLM 2: composing the explanation from labelled evidence | `llama3.1:8b` | `app/llm/composer.py` |
| Embeddings for retrieval | `BAAI/bge-small-en-v1.5` (sentence-transformers, local) | `app/retrieval/embedder.py` |
| Optional reranker (off by default) | `cross-encoder/ms-marco-MiniLM-L-6-v2` (local) | `app/retrieval/rerank.py` |
| Optional cloud fallback (off by default) | Any OpenAI-compatible endpoint, only if `CLOUD_FALLBACK=true` is set | `app/llm/gateway.py`; `/health` and every audit record show which model answered |

**What the LLM never decides:**
- eligibility verdicts, thresholds, attendance arithmetic and policy what-ifs (tools read thresholds from the rule registry and use exact fractions);
- precedence between documents (Annex A, in code);
- `answer_type`;
- refusals;
- citations (code expands evidence IDs and checks them).

Every prompt is in the repository (`app/llm/planner.py`, `app/llm/composer.py`, `app/conversation.py`), and every LLM call is recorded in the audit log with model, tokens and latency.

## 3. AI-generated data

| Data | Model | Disclosure |
|---|---|---|
| 40 synthetic students, 220 attendance rows, 141 results | `llama3.1:8b` (temperature 0.7, seed 42) | `data/synthetic/DATA_CARD.md`; prompts and raw outputs in `data/synthetic/generation_log.jsonl` |
| Two synthetic documents (`synthetic=Y` in the source register): the ACAD-2026-08 circular and the student-council post | Written for the hackathon | `data/corpus/source_register.csv` |
| Evaluation grading (optional LLM-as-judge) | `llama3.1:8b` | Judge prompt in `eval/judge_prompt.md`; exact-match grades are the primary metric |

No real student data was used anywhere.
