"""In-process metrics in Prometheus text format (GET /metrics). Counters + latency histograms."""
from __future__ import annotations

import threading
from collections import defaultdict

BUCKETS = (50, 100, 250, 500, 1000, 2000, 3000, 5000, 8000, 13000, 21000, 60000)
_lock = threading.Lock()
_counters: dict[tuple[str, tuple], float] = defaultdict(float)
_hist: dict[tuple[str, tuple], list[float]] = {}


def inc(name: str, value: float = 1.0, **labels: str) -> None:
    with _lock:
        _counters[(name, tuple(sorted(labels.items())))] += value


def observe(name: str, ms: float, **labels: str) -> None:
    key = (name, tuple(sorted(labels.items())))
    with _lock:
        h = _hist.setdefault(key, [0.0] * (len(BUCKETS) + 2))   # buckets..., sum, count
        for i, b in enumerate(BUCKETS):
            if ms <= b:
                h[i] += 1
        h[-2] += ms
        h[-1] += 1


def _lbl(labels: tuple, extra: dict | None = None) -> str:
    items = list(labels) + list((extra or {}).items())
    return "{" + ",".join(f'{k}="{v}"' for k, v in items) + "}" if items else ""


def render() -> str:
    lines = []
    with _lock:
        for (name, labels), v in sorted(_counters.items()):
            lines.append(f"uniassist_{name}{_lbl(labels)} {v:g}")
        for (name, labels), h in sorted(_hist.items()):
            for i, b in enumerate(BUCKETS):
                lines.append(f"uniassist_{name}_bucket{_lbl(labels, {'le': b})} {h[i]:g}")
            lines.append(f"uniassist_{name}_bucket{_lbl(labels, {'le': '+Inf'})} {h[-1]:g}")
            lines.append(f"uniassist_{name}_sum{_lbl(labels)} {h[-2]:g}")
            lines.append(f"uniassist_{name}_count{_lbl(labels)} {h[-1]:g}")
    return "\n".join(lines) + "\n"


def snapshot() -> dict:
    with _lock:
        return {f"{n}{dict(l) if l else ''}": v for (n, l), v in _counters.items()}
