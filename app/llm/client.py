"""LLM access. Ollama with JSON-schema structured outputs; MockLLM exists only for tests (never for demos)."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from app.config import Settings


class LLMError(Exception):
    """kind: unavailable (connection, 5xx, model missing) | timeout | bad_output (invalid JSON) | bad_request.
    Only unavailable and timeout count against a provider's circuit breaker."""
    def __init__(self, message: str, kind: str = "bad_output"):
        super().__init__(message)
        self.kind = kind


# Per-task limits: a timeout bounds the wait, num_predict bounds the answer length (and so the latency).
TASK_LIMITS = {"plan": (30.0, 320), "condense": (20.0, 120), "compose": (90.0, 600), "warmup": (60.0, 8)}


@dataclass
class LLMResult:
    data: dict
    prompt_tokens: int
    completion_tokens: int
    ms: int
    model: str
    task: str
    cached: bool = False


class OllamaClient:
    def __init__(self, s: Settings):
        self.s = s
        self.model = s.llm_model
        self.http = httpx.Client(base_url=s.ollama_base_url, timeout=s.llm_timeout_s)

    def chat_json(self, task: str, system: str, user: str, schema: dict, context: dict | None = None,
                  temperature: float = 0.0) -> LLMResult:
        timeout, num_predict = TASK_LIMITS.get(task, (self.s.llm_timeout_s, 600))
        payload = {"model": self.model, "stream": False, "format": schema, "keep_alive": "30m",
                   "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
                   "options": {"temperature": temperature, "num_ctx": self.s.num_ctx, "seed": self.s.llm_seed,
                               "num_predict": num_predict}}
        t0 = time.perf_counter()
        try:
            r = self.http.post("/api/chat", json=payload, timeout=min(timeout, self.s.llm_timeout_s))
            r.raise_for_status()
            body = r.json()
            data = json.loads(body["message"]["content"])
        except httpx.TimeoutException as e:
            raise LLMError(f"{task}: timed out after {timeout:.0f}s", "timeout") from e
        except httpx.HTTPStatusError as e:
            kind = "unavailable" if e.response.status_code >= 500 or e.response.status_code == 404 else "bad_request"
            raise LLMError(f"{task}: HTTP {e.response.status_code}: {e.response.text[:120]}", kind) from e
        except httpx.HTTPError as e:
            raise LLMError(f"{task}: {e.__class__.__name__}: {e}", "unavailable") from e
        except (KeyError, json.JSONDecodeError) as e:
            raise LLMError(f"{task}: {e.__class__.__name__}: {e}", "bad_output") from e
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
            raise LLMError(f"mock has no handler for {task}", "bad_request")
        return LLMResult(self.handlers[task](context or {}), len(user) // 4, 60, 1, "mock", task)

    def health(self) -> dict:
        return {"status": "ok", "provider": "mock", "model": "mock"}


def make_llm(s: Settings) -> Any:
    """Every caller gets the gateway (retries, breaker, fallback chain, cache, metrics) around the provider."""
    from app.llm.gateway import LLMGateway
    if s.llm_provider == "mock":
        from app.llm.mock import mock_handlers
        return LLMGateway(s, providers=[MockLLM(mock_handlers())])
    return LLMGateway(s)
