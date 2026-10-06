"""Shared helpers for the evaluation scripts: answer matching, resilient HTTP, run loading, statistics, tau calibration.

Only the standard library and PyYAML, so the harness runs anywhere the API is reachable.
"""
from __future__ import annotations

import json
import math
import re
import socket
import time
import urllib.error
import urllib.request
from datetime import date
from decimal import Decimal, InvalidOperation
from http.client import HTTPException
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNS = HERE / "runs"
DEFAULT_AS_OF = "2026-10-06"
ANSWERED = {"retrieved_fact", "calculated", "conflict_flagged"}

# ----------------------------------------------------------------------------- text normalisation and matching
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{2,3}\b)")          # 55,000 and 1,00,000 -> 55000, 100000
_NUM = re.compile(r"(?<!\d)(?<!\d\.)(\d+(?:\.\d+)?)(?!\d)")
_NUMERIC_FACT = re.compile(r"[+-]?\d+(?:\.\d+)?")
_MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DATE_DMY = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+(?:of\s+)?{_MON},?\s+(\d{{4}})\b")
_DATE_MDY = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(\d{{4}})\b")
_DATE_NUM = re.compile(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})\b")    # DD/MM/YYYY (Indian convention)


def _iso(y: int, m: int, d: int) -> str | None:
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def iso_dates(text: str) -> list[str]:
    """Every calendar date written in the text, as ISO strings ('1 August 2026' -> '2026-08-01')."""
    t = text.lower()
    out = [m.group(0) for m in re.finditer(r"\b\d{4}-\d{2}-\d{2}\b", t)]
    out += [_iso(int(y), _MONTHS[mon[:3]], int(d)) for d, mon, y in _DATE_DMY.findall(t)]
    out += [_iso(int(y), _MONTHS[mon[:3]], int(d)) for mon, d, y in _DATE_MDY.findall(t)]
    out += [_iso(int(y), int(mo), int(d)) for d, mo, y in _DATE_NUM.findall(t)]
    return [d for d in dict.fromkeys(out) if d]


def norm(s: str) -> str:
    return " ".join(_THOUSANDS.sub("", (s or "").lower()).split())


def numbers(text_norm: str) -> set[Decimal]:
    out = set()
    for n in _NUM.findall(text_norm):
        try:
            out.add(Decimal(n))
        except InvalidOperation:
            pass
    return out


class Text:
    """Normalised answer text with its numbers and dates, for exact matching."""

    def __init__(self, *parts: str):
        raw = " ".join(p for p in parts if p)
        self.base = norm(raw)
        self.nums = numbers(self.base)
        self.full = self.base + " " + " ".join(iso_dates(raw))

    def has(self, fact) -> bool:
        f = norm(str(fact))
        if _NUMERIC_FACT.fullmatch(f):                       # numbers: exact value, any formatting (80 == 80.00)
            return Decimal(f) in self.nums
        return f in self.full

    def matches(self, pattern: str) -> bool:
        return re.search(pattern, self.full, re.I) is not None


def value_matches(expected, actual) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return abs(float(actual) - float(expected)) <= 1e-6
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(value_matches(v, actual.get(k)) for k, v in expected.items())
    return expected == actual


def subset_mismatch(expected: dict, actual: dict | None) -> list[str]:
    """Keys of `expected` whose value differs in `actual` (nested dicts compared as subsets)."""
    actual = actual or {}
    return [f"{k}={actual.get(k)!r}≠{v!r}" for k, v in expected.items() if not value_matches(v, actual.get(k))]


def source_matches(cited: str, expected: list[str]) -> bool:
    """'DOC#7.2' against expected entries; 'DOC#*' accepts any section of DOC."""
    doc = cited.split("#", 1)[0]
    return any(cited == e or (e.endswith("#*") and e[:-2] == doc) for e in expected)


# ----------------------------------------------------------------------------- HTTP with retries
class HttpResult:
    def __init__(self, status: int, body: object, wall_ms: int, waited_s: float = 0.0, attempts: int = 1):
        self.status, self.body, self.wall_ms, self.waited_s, self.attempts = status, body, wall_ms, waited_s, attempts


def http(api: str, path: str, body: dict | None = None, headers: dict | None = None, raw: bytes | None = None,
         ctype: str = "application/json", timeout: float = 600, max_conn_wait_s: float = 300,
         max_429_wait_s: float = 900, quiet: bool = False) -> HttpResult:
    """POST when a body is given, else GET. Retries connection errors (the dev API restarts while people work on it)
    and honours Retry-After on 429. Other HTTP errors are returned, not raised, so the caller can score them."""
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    hdrs = {"Content-Type": ctype, **(headers or {})}
    waited, attempts, conn_wait, wait_429 = 0.0, 0, 0.0, 0.0
    while True:
        attempts += 1
        req = urllib.request.Request(api.rstrip("/") + path, data=data, headers=hdrs,
                                     method="POST" if data is not None else "GET")
        t0 = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
                wall = int((time.perf_counter() - t0) * 1000)
                return HttpResult(r.status, json.loads(payload) if payload else None, wall, waited, attempts)
        except urllib.error.HTTPError as e:
            wall = int((time.perf_counter() - t0) * 1000)
            text = e.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = {"raw": text[:500]}
            if e.code == 429 and wait_429 < max_429_wait_s:
                ra = e.headers.get("Retry-After")
                pause = min(max(float(ra) if ra else 5.0, 1.0), 400.0)
                if not quiet:
                    code = (parsed.get("error") or {}).get("code") if isinstance(parsed, dict) else None
                    print(f"    429 {code or ''}: waiting {pause:.0f} s (Retry-After)", flush=True)
                time.sleep(pause)
                waited += pause
                wait_429 += pause
                continue
            if e.code in (502, 503, 504) and conn_wait < max_conn_wait_s:
                time.sleep(3)
                waited += 3
                conn_wait += 3
                continue
            return HttpResult(e.code, parsed, wall, waited, attempts)
        except (urllib.error.URLError, ConnectionError, socket.timeout, TimeoutError, HTTPException) as e:
            if conn_wait >= max_conn_wait_s:
                raise RuntimeError(f"{api}{path} unreachable for {conn_wait:.0f} s: {e}") from e
            if not quiet and attempts in (1, 5, 20):
                print(f"    connection problem ({e.__class__.__name__}); retrying", flush=True)
            time.sleep(3)
            waited += 3
            conn_wait += 3


def wait_healthy(api: str, timeout_s: float = 180) -> dict:
    deadline = time.time() + timeout_s
    last = None
    while time.time() < deadline:
        try:
            r = http(api, "/health", timeout=10, max_conn_wait_s=0, quiet=True)
            if r.status == 200 and isinstance(r.body, dict) and r.body.get("components", {}).get("sqlite", {}).get("status") == "ok":
                return r.body
            last = r.body
        except RuntimeError as e:
            last = str(e)
        time.sleep(2)
    raise RuntimeError(f"API at {api} not healthy after {timeout_s:.0f} s: {last}")


# ----------------------------------------------------------------------------- statistics
def pct(xs) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(100 * sum(1 for x in xs if x) / len(xs), 1) if xs else None


def quantile(values: list[float], p: float) -> float | None:
    """Nearest-rank on the sorted list (index round(p*(n-1))), the method used since the first report."""
    v = sorted(values)
    return v[min(len(v) - 1, int(round(p * (len(v) - 1))))] if v else None


def mean(values) -> float | None:
    v = [x for x in values if x is not None]
    return round(sum(v) / len(v), 3) if v else None


def cohen_kappa(a: list[bool], b: list[bool]) -> float | None:
    n = len(a)
    if n == 0:
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else (1.0 if po == 1 else 0.0)


# ----------------------------------------------------------------------------- tau calibration
def calibrate(points: list[tuple[float, bool]]) -> dict | None:
    """points = (max retrieval score, answerable). The gate answers iff score >= tau.
    Returns the tau with the best abstention accuracy, taken at the midpoint of the widest optimal gap."""
    if not points or len({a for _, a in points}) < 2:
        return None
    scores = sorted({s for s, _ in points})
    edges = [scores[0] - 0.02] + scores + [scores[-1] + 0.02]
    best = None
    for lo, hi in zip(edges, edges[1:]):                 # any tau in (lo, hi] gives the same decisions
        t = (lo + hi) / 2
        acc = sum((s >= t) == a for s, a in points) / len(points)
        width = hi - lo
        if best is None or acc > best["accuracy"] + 1e-12 or (abs(acc - best["accuracy"]) < 1e-12 and width > best["width"]):
            best = {"tau": round(t, 3), "accuracy": acc, "width": width, "interval": [round(lo, 3), round(hi, 3)]}
    return best


def gate_accuracy(points: list[tuple[float, bool]], tau: float) -> float | None:
    return sum((s >= tau) == a for s, a in points) / len(points) if points else None


def loocv_accuracy(points: list[tuple[float, bool]]) -> float | None:
    """Leave-one-out: choose tau on the other items, test on the held-out one (honest estimate of the tuned gate)."""
    if len(points) < 3:
        return None
    hits = 0
    for i, (s, a) in enumerate(points):
        fit = calibrate(points[:i] + points[i + 1:])
        tau = fit["tau"] if fit else 0.0
        hits += (s >= tau) == a
    return hits / len(points)


def calibration_points(rows: list[dict]) -> list[tuple[float, bool, str]]:
    """(max_score, answerable, id) for items whose answerability is unambiguous and that went through retrieval."""
    out = []
    for r in rows:
        exp = r.get("expected_types") or []
        if r.get("skipped") or r.get("repeat_of") or (r.get("turn") or 1) > 1 or r.get("max_score") is None:
            continue
        if set(exp) == {"not_found"}:
            out.append((float(r["max_score"]), False, r["id"]))
        elif set(exp) == {"retrieved_fact"}:
            out.append((float(r["max_score"]), True, r["id"]))
    return out


# ----------------------------------------------------------------------------- runs on disk
def list_runs(label: str) -> list[dict]:
    """Summaries of every run of a label, oldest first, each with its stamp and paths."""
    d = RUNS / label
    out = []
    for p in sorted(d.glob("*.summary.json")):
        if p.name.endswith(".judge.summary.json"):
            continue
        try:
            s = json.loads(p.read_text())
        except json.JSONDecodeError:
            continue
        stamp = p.name[: -len(".summary.json")]
        s.update(_stamp=stamp, _summary=str(p), _rows=str(d / f"{stamp}.jsonl"))
        out.append(s)
    return out


def latest_run(label: str, kinds: tuple[str, ...] = ("full",)) -> dict | None:
    runs = [r for r in list_runs(label) if r.get("kind", "full") in kinds]
    return runs[-1] if runs else None


def labels() -> list[str]:
    return sorted(p.name for p in RUNS.iterdir() if p.is_dir()) if RUNS.exists() else []


def load_rows(run: dict) -> list[dict]:
    p = Path(run["_rows"])
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []


def fmt(v, digits: int = 1, suffix: str = "") -> str:
    if v is None:
        return "–"
    if isinstance(v, float):
        if math.isnan(v):
            return "–"
        return f"{v:.{digits}f}{suffix}"
    return f"{v}{suffix}"
