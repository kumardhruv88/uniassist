"""Runtime configuration. Every tunable lives here and can be overridden by an env var or .env."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    runtime_dir: Path = ROOT / "data" / "runtime"
    corpus_dir: Path = ROOT / "data" / "corpus"

    # Retrieval
    embed_model: str = "BAAI/bge-small-en-v1.5"   # "hash" = deterministic offline embedder (tests)
    chunker: str = "clause-v1"
    top_k: int = 5
    k_fetch: int = 20
    tau: float = 0.68                              # abstention gate on cosine similarity (calibrated for bge-small, eval/REPORT.md)
    tau_confident: float = 0.80                    # above this, skip the lexical coverage gate
    min_coverage: float = 0.5                      # share of the question's distinctive terms the evidence must mention
    retrieval_mode: str = "hybrid"                 # dense | hybrid (dense + BM25, reciprocal rank fusion)
    reranker: str = "none"                         # none | a cross-encoder, e.g. cross-encoder/ms-marco-MiniLM-L-6-v2
    query_expansion: bool = True                   # university glossary: "bunk" -> attendance, "supply" -> supplementary
    planner: str = "auto"                          # auto (skip LLM 1 when the router is sure) | llm | router

    # Context (token optimisation)
    context_budget_tokens: int = 1400              # evidence tokens sent to the composer
    compress_context: bool = True                  # keep only query-relevant sentences of long, non-anchor chunks
    min_groundedness: float = 0.34                 # below this share of supported sentences, the draft is rejected

    # LLM gateway
    llm_provider: str = "ollama"                   # ollama | mock (tests only)
    llm_model: str = "llama3.1:8b"
    llm_fallback_model: str | None = None          # optional second local model, e.g. llama3.2:3b
    ollama_base_url: str = "http://localhost:11434"
    llm_timeout_s: float = 120.0
    llm_retries: int = 1                           # retries on connection errors only (not on timeouts or bad output)
    llm_concurrency: int = 2
    num_ctx: int = 8192
    llm_seed: int = 7
    cloud_fallback: bool = False                   # off by default; if enabled it is disclosed in /health and the audit
    cloud_base_url: str | None = None              # any OpenAI-compatible endpoint
    cloud_api_key: str | None = None
    cloud_model: str = "gpt-4o-mini"

    # Caches (every key includes the data version, bumped by ingest / rules / students loads)
    answer_cache: bool = True
    semantic_cache: bool = True
    semantic_cache_threshold: float = 0.95
    llm_cache: bool = True

    # Security
    rate_limit_enabled: bool = True
    rate_limit_per_min: int = 60
    rate_limit_burst: int = 20
    abuse_block_after: int = 5                     # guardrail blocks within 10 minutes before a temporary block
    abuse_block_s: int = 300

    timezone: str = "Asia/Kolkata"
    admin_token: str | None = None
    max_upload_mb: int = 25

    @property
    def sqlite_path(self) -> Path:
        return self.runtime_dir / "app.db"

    @property
    def chroma_dir(self) -> Path:
        return self.runtime_dir / "chroma"

    @property
    def files_dir(self) -> Path:
        return self.runtime_dir / "files"

    @property
    def collection_name(self) -> str:
        slug = self.embed_model.split("/")[-1].replace(".", "-")
        return f"docs__{slug}__{self.chunker}"


@lru_cache
def get_settings() -> Settings:
    return Settings()
