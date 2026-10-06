#!/usr/bin/env python3
"""Load Annex C CSVs (students, courses, attendance, results) into UniAssist.

    python scripts/load_students.py --dir test_students/ [--api http://localhost:8000]

Standard library only: it runs on any machine with Python 3, without the project's dependencies.
It POSTs the rows to /admin/students/load, so only the API process writes SQLite (safe with Docker volumes).
Each row is accepted or rejected with a reason; one bad row never blocks the rest.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

TABLES = ("courses", "students", "attendance", "results")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, help="folder with students.csv, courses.csv, attendance.csv, results.csv")
    ap.add_argument("--api", default=os.environ.get("API_URL", "http://localhost:8000"))
    ap.add_argument("--token", default=os.environ.get("ADMIN_TOKEN"))
    a = ap.parse_args()
    d = Path(a.dir)
    payload: dict[str, list[dict]] = {}
    for t in TABLES:
        f = d / f"{t}.csv"
        if f.exists():
            with open(f, newline="", encoding="utf-8-sig") as fh:
                payload[t] = list(csv.DictReader(fh))
            print(f"read {len(payload[t]):4} rows from {f}")
    if not payload:
        print(f"no CSV files found in {d}; expected any of {', '.join(t + '.csv' for t in TABLES)}", file=sys.stderr)
        return 2
    req = urllib.request.Request(f"{a.api.rstrip('/')}/admin/students/load", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", **({"X-Admin-Token": a.token} if a.token else {})})
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            res = json.loads(r.read())
    except urllib.error.URLError as e:
        print(f"could not reach the API at {a.api}: {e}. Is `docker compose up` running?", file=sys.stderr)
        return 2
    print("\naccepted:", ", ".join(f"{k} {v}" for k, v in res["accepted"].items()))
    if res["rejected"]:
        print(f"rejected {len(res['rejected'])} row(s):")
        for x in res["rejected"]:
            print(f"  {x['table']} row {x['row']}: {x['reason']}")
    return 1 if res["rejected"] else 0


if __name__ == "__main__":
    sys.exit(main())
