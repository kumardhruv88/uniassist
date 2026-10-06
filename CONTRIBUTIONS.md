# Team contribution statement

The project was built with Claude Code (see `AI_USAGE.md`). Each member directed, prompted and reviewed the parts listed under their name. Commits in the repository are authored by the member who directed that part.

| Member | GitHub | Parts directed |
|---|---|---|
| Garv Bahl | [garvbahl37-gif](https://github.com/garvbahl37-gif) | Frontend UI/UX: React app, registry-desk design, answer record, ledger, audit and document views (`frontend/`) |
| Manish Kumar | [m4nish-dev](https://github.com/m4nish-dev) | LangGraph orchestration (the 8-node `/ask` state machine) and the core end-to-end build it orchestrates: ingestion, retrieval, Annex A precedence engine, rule resolver, tools, API, schema, Docker, core tests |
| Dhruv Kumar | [kumardhruv88](https://github.com/kumardhruv88) | Production-grade RAG layer (LLM gateway, caches, guardrails, rate limiting, structured errors, metrics, context optimisation, hybrid retrieval, session follow-ups, what-if tool); synthetic dataset kit (`data/synthetic/`); golden dataset and evaluation (`eval/`); design docs, README and disclosures |

Each member works on their own branch (`garv`, `dhruv`, `manish`) and merges into `main` through pull requests.

**To be completed by the team:** the demo and presentation.
