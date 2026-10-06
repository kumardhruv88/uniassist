"""LLM gateway: the single door every LLM call goes through.

- Provider chain: primary Ollama model -> optional second local model -> optional OpenAI-compatible endpoint
  (off by default; when enabled it is disclosed in /health and every audit record names the model that answered).
  If every provider fails, callers fall back to deterministic templates, so rule and eligibility answers still work.
- Error kinds decide what happens next:
    unavailable (connection refused, 5xx, model missing)  retry once with backoff + jitter, then next provider
    timeout                                                no retry on the same provider (it would double the wait)
    bad_output / bad_request                               returned to the caller, which retries with feedback
- Per-provider circuit breaker: only availability failures count. After 3 in a row the provider is skipped for 30 s,
  then one probe request is let through (half-open).
- Concurrency limit, exact-prompt response cache, metrics per provider and task.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
from dataclasses import dataclass, field

import httpx

from app import cache, metrics
from app.config import Settings
from app.llm.client import LLMError, LLMResult, OllamaClient

log = logging.getLogger("uniassist.llm")
AVAILABILITY = ("unavailable", "timeout")


@dataclass
class Breaker:
    threshold: int = 3
    cooldown_s: float = 30.0
    failures: int = 0
    opened_at: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def state(self) -> str:
        if self.failures < self.threshold:
            return "closed"
        return "half_open" if time.monotonic() - self.opened_at >= self.cooldown_s else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record(self, ok: bool) -> None:
        with self._lock:
            if ok:
                self.failures = 0
            else:
                self.failures += 1
                if self.failures >= self.threshold:
                    self.opened_at = time.monotonic()


class OpenAICompatProvider:
    """Optional cloud fallback behind CLOUD_FALLBACK=true. Never used unless explicitly configured."""
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float):
        self.model = model
        self.http = httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout,
                                 headers={"Authorization": f"Bearer {api_key}"})

    def chat_json(self, task, system, user, schema, context=None, temperature=0.0) -> LLMResult:
        t0 = time.perf_counter()
        try:
            r = self.http.post("/chat/completions", json={
                "model": self.model, "temperature": temperature,
                "response_format": {"type": "json_schema", "json_schema": {"name": task, "schema": schema}},
                "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
            r.raise_for_status()
            body = r.json()
            data = json.loads(body["choices"][0]["message"]["content"])
        except httpx.TimeoutException as e:
            raise LLMError(f"{task}: timed out", "timeout") from e
        except httpx.HTTPStatusError as e:
            raise LLMError(f"{task}: HTTP {e.response.status_code}",
                           "unavailable" if e.response.status_code >= 500 or e.response.status_code == 429 else "bad_request") from e
        except httpx.HTTPError as e:
            raise LLMError(f"{task}: {e.__class__.__name__}", "unavailable") from e
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise LLMError(f"{task}: {e.__class__.__name__}", "bad_output") from e
        u = body.get("usage", {})
        return LLMResult(data, u.get("prompt_tokens", 0), u.get("completion_tokens", 0),
                         int((time.perf_counter() - t0) * 1000), self.model, task)

    def health(self) -> dict:
        return {"status": "ok", "provider": "openai-compatible", "model": self.model}


class LLMGateway:
    def __init__(self, s: Settings, providers: list | None = None):
        self.s = s
        if providers is None:
            providers = [OllamaClient(s)]
            if s.llm_fallback_model and s.llm_fallback_model != s.llm_model:
                fb = OllamaClient(s)
                fb.model = s.llm_fallback_model
                providers.append(fb)
            if s.cloud_fallback and s.cloud_base_url and s.cloud_api_key:
                providers.append(OpenAICompatProvider(s.cloud_base_url, s.cloud_api_key, s.cloud_model, s.llm_timeout_s))
        self.providers = providers
        self.breakers = [Breaker() for _ in providers]
        self.model = providers[0].model
        self._sem = threading.Semaphore(max(1, s.llm_concurrency))

    def chat_json(self, task: str, system: str, user: str, schema: dict, context: dict | None = None,
                  temperature: float = 0.0) -> LLMResult:
        ck = cache.key(task, system, user, json.dumps(schema, sort_keys=True), temperature)
        if self.s.llm_cache and (hit := cache.llm.get(ck)) is not None:
            metrics.inc("llm_calls", provider="cache", task=task)
            return LLMResult(hit.data, 0, 0, 0, hit.model, task, cached=True)
        errors: list[str] = []
        last_kind = "unavailable"
        for i, (p, br) in enumerate(zip(self.providers, self.breakers)):
            if not br.allow():
                errors.append(f"{p.model}: circuit open")
                continue
            for attempt in range(1 + max(0, self.s.llm_retries)):
                try:
                    with self._sem:
                        res = p.chat_json(task, system, user, schema, context, temperature)
                except LLMError as e:
                    last_kind = e.kind
                    metrics.inc("llm_errors", provider=p.model, task=task, kind=e.kind)
                    errors.append(f"{p.model}#{attempt + 1} {e.kind}: {str(e)[:100]}")
                    if e.kind not in AVAILABILITY:
                        raise                                    # the caller retries with feedback or falls back
                    br.record(False)
                    if e.kind == "timeout" or not br.allow():
                        break                                    # next provider
                    time.sleep(min(2.0, 0.3 * 2 ** attempt) + random.uniform(0, 0.2))
                    continue
                br.record(True)
                metrics.inc("llm_calls", provider=p.model, task=task)
                metrics.inc("llm_tokens", res.prompt_tokens + res.completion_tokens, provider=p.model, task=task)
                metrics.observe("llm_latency_ms", res.ms, provider=p.model, task=task)
                if i > 0:
                    metrics.inc("llm_fallbacks", to=p.model, task=task)
                    log.warning("LLM fallback: %s answered %s after %s", p.model, task, errors)
                if self.s.llm_cache:
                    cache.llm.set(ck, res)
                return res
        raise LLMError("all LLM providers failed: " + "; ".join(errors), last_kind)

    def health(self) -> dict:
        primary = self.providers[0].health()
        primary["breaker"] = self.breakers[0].state
        primary["fallbacks"] = [p.model for p in self.providers[1:]]
        primary["cloud_fallback_enabled"] = any(isinstance(p, OpenAICompatProvider) for p in self.providers)
        if self.breakers[0].state == "open" and primary.get("status") == "ok":
            primary["status"] = "degraded"
        return primary

    def warmup(self) -> None:
        """Load the model into memory at startup so the first real question is not slow."""
        t0 = time.perf_counter()
        try:
            self.providers[0].chat_json("warmup", "Reply with JSON.", "Say ok.",
                                        {"type": "object", "properties": {"ok": {"type": "string"}}, "required": ["ok"]})
            log.info("LLM warm-up done: %s in %.1fs", self.model, time.perf_counter() - t0)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM warm-up failed (%s); answers fall back to templates until the LLM is reachable", e)
