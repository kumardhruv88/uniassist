"""Run the golden set against a private API instance in a named configuration (guide §7: compare configurations).

    uv run python eval/run_config.py A          # baseline: MiniLM + fixed 800-char chunks, dense only (tau calibrated)
    uv run python eval/run_config.py B          # shipped defaults: bge-small + clause chunks + hybrid BM25/dense
    uv run python eval/run_config.py C          # B + cross-encoder reranker
    uv run python eval/run_config.py B --keep   # leave data/runtime-B in place afterwards

Steps: fresh RUNTIME_DIR -> start uvicorn on the preset's port -> seed through the API (scripts/seed.py) ->
[calibration pass on the answerable/unanswerable items, pick tau, restart with TAU=<tau>] -> full golden run
(eval/run_eval.py --label <name>) -> stop the server -> delete the runtime dir.

Every preset runs under the same operational flags, so differences come from the configuration alone:
rate limiting off (the harness sends ~140 requests from one IP, including ~20 that a guardrail blocks on purpose),
LLM-response and semantic caches off (each question pays for its own LLM calls), exact answer cache on (the cache
items need it). Only one evaluation should talk to the shared Ollama at a time.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

from evallib import HERE, ROOT, calibrate, calibration_points, latest_run, load_rows, wait_healthy

COMMON = {"RATE_LIMIT_ENABLED": "false", "LLM_CACHE": "false", "SEMANTIC_CACHE": "false", "ANSWER_CACHE": "true"}
PRESETS = {
    "A": {"port": 8101, "calibrate": True,
          "description": "baseline: all-MiniLM-L6-v2 + fixed 800-char chunks, dense retrieval only, no glossary expansion",
          "env": {"EMBED_MODEL": "sentence-transformers/all-MiniLM-L6-v2", "CHUNKER": "fixed-800",
                  "RETRIEVAL_MODE": "dense", "RERANKER": "none", "QUERY_EXPANSION": "false"}},
    "B": {"port": 8102, "calibrate": False,
          "description": "shipped defaults: bge-small-en-v1.5 + clause chunks, hybrid dense + BM25 (RRF), glossary expansion",
          "env": {}},
    "C": {"port": 8103, "calibrate": False,
          "description": "B + cross-encoder reranker (ms-marco-MiniLM-L-6-v2)",
          "env": {"RETRIEVAL_MODE": "hybrid", "RERANKER": "cross-encoder/ms-marco-MiniLM-L-6-v2"}},
}


def port_free(port: int) -> bool:
    with socket.socket() as s:
        return s.connect_ex(("127.0.0.1", port)) != 0


class Server:
    def __init__(self, env: dict, port: int, log: Path):
        self.env, self.port, self.log = env, port, log
        self.proc: subprocess.Popen | None = None

    @property
    def api(self) -> str:
        return f"http://localhost:{self.port}"

    def start(self) -> dict:
        if not port_free(self.port):
            raise SystemExit(f"port {self.port} is busy; stop whatever runs there or pass --port")
        self.log.parent.mkdir(parents=True, exist_ok=True)
        uvicorn = ROOT / ".venv" / "bin" / "uvicorn"
        cmd = [str(uvicorn) if uvicorn.exists() else "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(self.port)]
        self._fh = open(self.log, "ab")
        self.proc = subprocess.Popen(cmd, cwd=ROOT, env={**os.environ, **self.env}, stdout=self._fh, stderr=subprocess.STDOUT,
                                     start_new_session=True)
        try:
            return wait_healthy(self.api, timeout_s=300)
        except RuntimeError:
            self.tail()
            self.stop()
            raise

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait(timeout=10)
        self.proc = None

    def tail(self, n: int = 40) -> None:
        if self.log.exists():
            print("".join(self.log.read_text(errors="replace").splitlines(True)[-n:]))


def run(cmd: list[str]) -> int:
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT).returncode


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("preset", choices=sorted(PRESETS))
    ap.add_argument("--label", default=None, help="run label (default: the preset name)")
    ap.add_argument("--port", type=int, default=None)
    ap.add_argument("--tau", type=float, default=None, help="fixed tau (skips calibration)")
    ap.add_argument("--no-calibrate", action="store_true")
    ap.add_argument("--env", action="append", default=[], help="extra KEY=VALUE for the server")
    ap.add_argument("--dataset", default=str(HERE / "golden.yaml"))
    ap.add_argument("--keep", action="store_true", help="keep the runtime dir")
    a = ap.parse_args()

    p = PRESETS[a.preset]
    label = a.label or a.preset
    port = a.port or p["port"]
    runtime = ROOT / "data" / f"runtime-{label}"
    if runtime.exists():
        shutil.rmtree(runtime)
    env = {**COMMON, **p["env"], "RUNTIME_DIR": str(runtime.relative_to(ROOT))}
    env.update(dict(kv.split("=", 1) for kv in a.env))
    if a.tau is not None:
        env["TAU"] = str(a.tau)
    server = Server(env, port, runtime / "server.log")
    py = sys.executable
    t0 = time.time()
    try:
        health = server.start()
        print(f"[{label}] up on {server.api}: {health.get('config')}")
        rc = run([py, "scripts/seed.py", "--api", server.api])
        if rc:
            print(f"[{label}] seed exited {rc} (a document that needs OCR fails without tesseract; continuing)")

        tau_note = f"tau={health['config'].get('tau')} (default)"
        if p["calibrate"] and a.tau is None and not a.no_calibrate:
            if run([py, "eval/run_eval.py", "--label", label, "--api", server.api, "--dataset", a.dataset, "--calibration",
                    "--note", f"tau calibration pass; {p['description']}"]):
                raise SystemExit("calibration pass failed")
            pts = [(s, ans) for s, ans, _ in calibration_points(load_rows(latest_run(label, ("calibration",))))]
            fit = calibrate(pts)
            if fit is None:
                raise SystemExit("not enough calibration points")
            current = float(health["config"].get("tau") or 0)
            print(f"[{label}] calibration: tau*={fit['tau']} (optimal interval {fit['interval']}, abstention accuracy "
                  f"{fit['accuracy']:.3f} on {len(pts)} items); configured {current}")
            if abs(fit["tau"] - current) > 0.005:
                server.stop()
                env["TAU"] = str(fit["tau"])
                server = Server(env, port, runtime / "server.log")
                health = server.start()
                print(f"[{label}] restarted with TAU={fit['tau']}: {health.get('config')}")
            tau_note = f"tau={fit['tau']} calibrated on {len(pts)} items (accuracy {fit['accuracy']:.3f})"

        note = json.dumps({"preset": a.preset, "description": p["description"], "env": env, "tau": tau_note})
        if run([py, "eval/run_eval.py", "--label", label, "--api", server.api, "--dataset", a.dataset, "--note", note]):
            raise SystemExit("evaluation run failed")
    except BaseException:
        server.tail()
        raise
    finally:
        server.stop()
        if not a.keep and runtime.exists():
            shutil.rmtree(runtime)
            print(f"[{label}] removed {runtime.relative_to(ROOT)}")
    print(f"[{label}] done in {time.time() - t0:.0f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
