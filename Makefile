# Docker shortcuts. Ports and the data folder come from API_PORT, UI_PORT and DATA_DIR (see docker-compose.yml).
#   make up            build if needed, start API + seed job + UI in the background
#   make logs          follow all logs; one service: make logs SERVICE=seed
#   make down          stop and remove the containers (data in DATA_DIR is kept)
COMPOSE ?= docker compose
SERVICE ?=

.PHONY: build up down logs

build:
	$(COMPOSE) build

up:
	$(COMPOSE) up -d --build
	@echo "UI  http://localhost:$${UI_PORT:-8080}    API http://localhost:$${API_PORT:-8000}/docs    seed progress: make logs SERVICE=seed"

down:
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f $(SERVICE)

# ---------------------------------------------------------------------------------------------------------------
# Tests and evaluation (guide §7). Method, metrics and results: eval/REPORT.md.
#   make test          pytest
#   make eval          golden set on the API at $(API) as label $(LABEL), then eval/REPORT.md
#   make eval-all      pytest + dataset check + configs A and B on private instances + LLM judge + report with gates
#   make judge         LLM-as-judge on the latest full run of $(LABEL) (needs Ollama; not while an eval runs)
#   make report        rebuild eval/REPORT.md from eval/runs/ (no API needed)
UV ?= $(shell command -v uv 2>/dev/null || echo $(HOME)/.local/bin/uv)
API ?= http://localhost:8000
LABEL ?= B
PRIMARY ?= B

.PHONY: test verify-golden eval eval-all eval-a eval-b eval-c judge report

test:
	$(UV) run pytest -q

verify-golden:
	$(UV) run python eval/verify_golden.py

eval:
	$(UV) run python eval/run_eval.py --label $(LABEL) --api $(API) --cold
	$(UV) run python eval/report.py --primary $(PRIMARY)

eval-a:
	$(UV) run python eval/run_config.py A

eval-b:
	$(UV) run python eval/run_config.py B

eval-c:
	$(UV) run python eval/run_config.py C

eval-all:
	$(UV) run pytest -q
	$(UV) run python eval/verify_golden.py
	$(UV) run python eval/run_config.py A
	$(UV) run python eval/run_config.py B
	$(UV) run python eval/judge.py --label $(PRIMARY)
	$(UV) run python eval/report.py --primary $(PRIMARY) --gate

judge:
	$(UV) run python eval/judge.py --label $(LABEL)

report:
	$(UV) run python eval/report.py --primary $(PRIMARY)
