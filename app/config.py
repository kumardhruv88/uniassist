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

    # LLM
    llm_provider: str = "ollama"                   # ollama | mock (tests only)
    llm_model: str = "llama3.1:8b"
    ollama_base_url: str = "http://localhost:11434"
    llm_timeout_s: float = 120.0
    num_ctx: int = 8192
    llm_seed: int = 7

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
