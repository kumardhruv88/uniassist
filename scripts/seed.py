#!/usr/bin/env python3
"""Seed a running UniAssist through its own API (the same path judges use): documents via POST /ingest,
curated rules via /admin/rules/load, synthetic students via /admin/students/load. Idempotent.

    python scripts/seed.py [--api http://localhost:8000] [--with-demo-circular]
"""
from __future__ import annotations

import argparse
import csv
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CORPUS = ROOT / "data" / "corpus"
SYNTH = ROOT / "data" / "synthetic" / "out"
REGISTER_FIELDS = ["doc_id", "title", "issuer", "authority_level", "doc_type", "version", "effective_from", "effective_to",
                   "supersedes", "scope_programmes", "scope_batches", "provenance", "retrieved_on", "synthetic"]


def _req(api: str, path: str, data: bytes | None = None, ctype: str | None = None, token: str | None = None, timeout=300):
    headers = {"Content-Type": ctype} if ctype else {}
    if token:
        headers["X-Admin-Token"] = token
    req = urllib.request.Request(f"{api}{path}", data=data, headers=headers, method="POST" if data is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def ingest(api: str, file: Path, meta: dict, token: str | None) -> dict:
    boundary = uuid.uuid4().hex
    ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"metadata\"\r\n\r\n{json.dumps(meta)}\r\n"
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{file.name}\"\r\n"
            f"Content-Type: {ctype}\r\n\r\n").encode() + file.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return _req(api, "/ingest", body, f"multipart/form-data; boundary={boundary}", token)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default=os.environ.get("API_URL", "http://localhost:8000"))
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN"))
    ap.add_argument("--with-demo-circular", action="store_true", help="also ingest data/corpus/demo/ACAD-2026-08 (normally done live)")
    a = ap.parse_args()
    api = a.api.rstrip("/")

    for i in range(90):
        try:
            if _req(api, "/health", timeout=5)["components"]["sqlite"]["status"] == "ok":
                break
        except Exception:  # noqa: BLE001
            pass
        time.sleep(2)
    else:
        print(f"API at {api} did not become healthy", file=sys.stderr)
        return 2

    with open(CORPUS / "source_register.csv", newline="") as f:
        docs = list(csv.DictReader(f))
    if a.with_demo_circular:
        demo = json.loads((CORPUS / "demo" / "ACAD-2026-08.meta.json").read_text())
        docs.append({**demo, "file": "../demo/ACAD-2026-08.pdf"})
    failures = 0
    for d in docs:
        meta = {k: d.get(k, "") for k in REGISTER_FIELDS}
        meta["authority_level"] = int(meta["authority_level"])
        try:
            r = ingest(api, (CORPUS / "documents" / d["file"]).resolve(), meta, a.token)
            print(f"{r['status']:9} {r['doc_id']:20} chunks={r['chunks_indexed']:3} rules={r['rules_added']}"
                  + (f" warnings={r['warnings']}" if r["warnings"] else ""))
            failures += r["status"] == "failed"
        except urllib.error.HTTPError as e:
            print(f"failed    {d['doc_id']:20} {e.code} {e.read().decode()[:300]}")
            failures += 1

    with open(CORPUS / "rules_seed.csv", newline="") as f:
        rules = list(csv.DictReader(f))
    r = _req(api, "/admin/rules/load", json.dumps({"rules": rules}).encode(), "application/json", a.token)
    print(f"rules     curated added={len(r['added'])} rejected={r['rejected']}")

    if SYNTH.exists() and (SYNTH / "students.csv").exists():
        payload = {}
        for t in ("courses", "students", "attendance", "results"):
            with open(SYNTH / f"{t}.csv", newline="") as f:
                payload[t] = list(csv.DictReader(f))
        r = _req(api, "/admin/students/load", json.dumps(payload).encode(), "application/json", a.token)
        print(f"students  accepted={r['accepted']} rejected={len(r['rejected'])}")
    else:
        print("students  skipped: run data/synthetic/generate.py first")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
