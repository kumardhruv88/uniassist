"""Token-bucket rate limiting per client, plus abuse tracking: repeated guardrail blocks lead to a temporary block.
In-process (single API worker); swap for Redis when running several workers."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass


@dataclass
class Bucket:
    tokens: float
    updated: float


class RateLimiter:
    def __init__(self, per_minute: int = 30, burst: int = 10, block_after: int = 3, window_s: int = 600, block_s: int = 900):
        self.rate = per_minute / 60.0
        self.burst = burst
        self.block_after, self.window_s, self.block_s = block_after, window_s, block_s
        self._buckets: dict[str, Bucket] = {}
        self._strikes: dict[str, deque] = defaultdict(deque)
        self._blocked_until: dict[str, float] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> tuple[bool, str | None, float]:
        """(allowed, error_code, retry_after_s)."""
        now = time.monotonic()
        with self._lock:
            until = self._blocked_until.get(key, 0)
            if until > now:
                return False, "CLIENT_BLOCKED", until - now
            b = self._buckets.get(key) or Bucket(self.burst, now)
            b.tokens = min(self.burst, b.tokens + (now - b.updated) * self.rate)
            b.updated = now
            if b.tokens < 1:
                self._buckets[key] = b
                return False, "RATE_LIMITED", (1 - b.tokens) / self.rate
            b.tokens -= 1
            self._buckets[key] = b
            return True, None, 0.0

    def strike(self, key: str) -> bool:
        """Record a guardrail block; returns True if the client is now temporarily blocked."""
        now = time.monotonic()
        with self._lock:
            q = self._strikes[key]
            q.append(now)
            while q and now - q[0] > self.window_s:
                q.popleft()
            if len(q) >= self.block_after:
                self._blocked_until[key] = now + self.block_s
                q.clear()
                return True
            return False
