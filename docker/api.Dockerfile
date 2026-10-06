# syntax=docker/dockerfile:1
#
# UniAssist API: FastAPI + LangGraph, embedded ChromaDB and SQLite, Tesseract OCR, and the embedding model
# baked into the image so the container needs no internet at runtime (only Ollama, usually on the host).
#
#   docker compose build api                                         # from the repository root
#   docker build -f docker/api.Dockerfile -t uniassist-api .         # same, without compose

ARG PYTHON_VERSION=3.12
ARG UV_VERSION=0.11.28

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# ------------------------------------------------------------------ build: virtualenv + embedding model
FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv
WORKDIR /app

# Dependencies only, so this layer is rebuilt only when pyproject.toml or uv.lock change.
# On Linux, uv.lock takes torch from the PyTorch CPU index, so no CUDA libraries are installed.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Download the models once, at build time; the runtime sets HF_HUB_OFFLINE=1. RERANKER is the optional
# cross-encoder (app/retrieval/rerank.py), e.g. cross-encoder/ms-marco-MiniLM-L-6-v2; "none" skips it.
ARG EMBED_MODEL=BAAI/bge-small-en-v1.5
ARG RERANKER=none
ENV HF_HOME=/models
RUN mkdir -p /models && /app/.venv/bin/python - "$EMBED_MODEL" "$RERANKER" <<'EOF'
import sys
from sentence_transformers import CrossEncoder, SentenceTransformer
embed, rerank = sys.argv[1], sys.argv[2]
if embed != "hash":                      # "hash" is the offline test embedder: nothing to download
    print(embed, "dim", SentenceTransformer(embed).encode(["ok"]).shape[1])
if rerank.lower() != "none":
    print(rerank, "score", CrossEncoder(rerank).predict([("ok", "ok")]))
EOF

# ------------------------------------------------------------------ runtime
FROM python:${PYTHON_VERSION}-slim

# Tesseract reads scanned PDF pages and images (pytesseract calls the binary). English data comes with it.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tesseract-ocr \
 && rm -rf /var/lib/apt/lists/*

# The API runs as this unprivileged user. The entrypoint starts as root only to make a bind-mounted
# runtime folder writable for it, then drops privileges (see docker/api-entrypoint.sh).
RUN groupadd --gid 10001 app \
 && useradd --uid 10001 --gid app --create-home --home-dir /home/app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=build /models /models
COPY --chmod=755 docker/api-entrypoint.sh /usr/local/bin/uniassist-entrypoint

# Seed inputs (POSTed through the API by scripts/seed.py), then the code, which changes most often.
COPY data/corpus/ data/corpus/
COPY data/synthetic/ data/synthetic/
COPY scripts/ scripts/
COPY app/ app/
RUN mkdir -p data/runtime && chown app:app data/runtime

# The app uses exactly the models baked above.
ARG EMBED_MODEL=BAAI/bge-small-en-v1.5
ARG RERANKER=none
ENV PATH=/app/.venv/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HF_HOME=/models \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1 \
    EMBED_MODEL=${EMBED_MODEL} \
    RERANKER=${RERANKER} \
    RUNTIME_DIR=/app/data/runtime

# SQLite, Chroma and uploaded files live in /app/data/runtime: mount a host folder or volume there
# (docker-compose.yml does) so they survive container rebuilds.
EXPOSE 8000

# Slim images have no curl. Healthy = the API answers and its stores are up (an unreachable LLM only degrades it).
HEALTHCHECK --interval=10s --timeout=10s --start-period=120s --retries=5 \
  CMD ["python", "-c", "import json, sys, urllib.request; h = json.load(urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=8)); sys.exit(h['status'] == 'down')"]

ENTRYPOINT ["uniassist-entrypoint"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
