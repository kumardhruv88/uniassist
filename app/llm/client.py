"""LLM access. Ollama with JSON-schema structured outputs; MockLLM exists only for tests (never for demos)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.config import Settings


class LLMError(Exception):
    pass


@dataclass
class LLMResult:
    data: dict
    prompt_tokens: int
    completion_tokens: int
    ms: int
    model: str
    task: str


class OllamaClient:
    def __init__(self, s: Settings):
        self.s = s
        self.model = s.llm_model
        self.http = httpx.Client(base_url=s.ollama_base_url, timeout=s.llm_timeout_s)

    def chat_json(self, task: str, system: str, user: str, schema: dict, context: dict | None = None,
                  temperature: float = 0.0) -> LLMResult:
        payload = {"model": self.model, "stream": False, "format": schema, "keep_alive": "30m",
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "options": {"temperature": temperature, "num_ctx": self.s.num_ctx, "seed": self.s.llm_seed}}
        t0 = time.perf_counter()
        try:
            r = self.http.post("/api/chat", json=payload)
            r.raise_for_status()
            body = r.json()
            data = json.loads(body["message"]["content"])
        except (httpx.HTTPError, KeyError, json.JSONDecodeError) as e:
            raise LLMError(f"{task}: {e.__class__.__name__}: {e}") from e
        return LLMResult(data, body.get("prompt_eval_count", 0), body.get("eval_count", 0),
                         int((time.perf_counter() - t0) * 1000), self.model, task)

    def health(self) -> dict:
        try:
            tags = self.http.get("/api/tags", timeout=3).json()
            names = [m["name"] for m in tags.get("models", [])]
            ok = self.model in names or (":" not in self.model and f"{self.model}:latest" in names)
            return {"status": "ok" if ok else "degraded", "provider": "ollama", "model": self.model,
                    **({} if ok else {"detail": f"model {self.model} not pulled; run: ollama pull {self.model}"})}
        except Exception as e:  # noqa: BLE001
            return {"status": "down", "provider": "ollama", "model": self.model, "detail": e.__class__.__name__}


class MockLLM:
    """Deterministic stand-in so the pipeline can be tested without a model. Tests only."""
    model = "mock"

    def __init__(self, handlers: dict[str, Callable[[dict], dict]] | None = None):
        self.handlers = handlers or {}

    def chat_json(self, task: str, system: str, user: str, schema: dict, context: dict | None = None,
                  temperature: float = 0.0) -> LLMResult:
        if task not in self.handlers:
            raise LLMError(f"mock has no handler for {task}")
        return LLMResult(self.handlers[task](context or {}), len(user) // 4, 60, 1, "mock", task)

    def health(self) -> dict:
        return {"status": "ok", "provider": "mock", "model": "mock"}


def make_llm(s: Settings) -> Any:
    if s.llm_provider == "mock":
        from app.llm.mock import mock_handlers
        return MockLLM(mock_handlers())
    return OllamaClient(s)
