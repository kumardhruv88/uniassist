"""Validate Annex C CSVs: schema, logical constraints, rule consistency, coverage and the edge-case manifest.

    uv run python data/synthetic/validate.py                      # our generated data (strict: reserved ranges, manifest)
    uv run python data/synthetic/validate.py --dir test_students --no-manifest --allow-reserved   # judges' data

Thresholds (pass mark, minimum attendance) are read from data/corpus/rules_seed.csv, never hard-coded.
Writes validation_report.json and exits 1 if any error-level check fails.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import Counter, defaultdict
from fractions import Fraction
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
COLS = {
    "students": ["student_id", "full_name", "programme", "batch_year", "current_semester", "cgpa", "active_backlogs"],
    "courses": ["course_code", "course_name", "programme", "semester", "credits"],
    "attendance": ["student_id", "course_code", "classes_held", "classes_attended"],
    "results": ["student_id", "course_code", "exam_session", "exam_type", "internal_marks", "external_marks",
                "total_marks", "max_marks", "result"],
}


def _int(v):
    return None if v in (None, "") else int(float(v))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(HERE / "out"))
    ap.add_argument("--no-manifest", action="store_true")
    ap.add_argument("--allow-reserved", action="store_true")
    ap.add_argument("--report", default=str(HERE / "validation_report.json"))
    a = ap.parse_args()
    d = Path(a.dir)
    rules = {r["parameter"]: r["value"] for r in csv.DictReader(open(ROOT / "data/corpus/rules_seed.csv"))}
    pass_mark = Fraction(rules["pass_min_total_pct"])
    min_att = Fraction(rules["min_attendance_pct"])

    data = {t: list(csv.DictReader(open(d / f"{t}.csv", newline=""))) if (d / f"{t}.csv").exists() else [] for t in COLS}
    checks: list[dict] = []

    def check(cid: str, name: str, violations: list[str], severity: str = "error") -> None:
        checks.append({"id": cid, "check": name, "severity": severity, "passed": not violations,
                       "violations": violations[:50], "violation_count": len(violations)})

    # V01 columns and types
    v = []
    for t, cols in COLS.items():
        if data[t] and set(cols) - set(data[t][0].keys()):
            v.append(f"{t}.csv missing columns {sorted(set(cols) - set(data[t][0].keys()))}")
    for s in data["students"]:
        try:
            int(s["batch_year"]); int(s["current_semester"]); float(s["cgpa"]); int(s["active_backlogs"])
        except (ValueError, KeyError):
            v.append(f"{s.get('student_id')}: non-numeric batch_year/current_semester/cgpa/active_backlogs")
    check("V01", "Columns and types match Annex C", v)
    # V02 / V03 id formats and reserved ranges
    v2 = [s["student_id"] for s in data["students"] if not re.fullmatch(r"S\d{4}", s["student_id"])]
    if not a.allow_reserved:
        v2 += [f"{s['student_id']} is in the judges' reserved range S9000-S9999" for s in data["students"]
               if re.fullmatch(r"S9\d{3}", s["student_id"])]
    check("V02", "student_id is S + 4 digits and outside S9000-S9999", v2)
    v3 = [] if a.allow_reserved else [c["course_code"] for c in data["courses"] if c["course_code"].upper().startswith("JDG")]
    check("V03", "No course code starts with JDG (reserved for judges)", v3)
    # V04 ranges
    v = []
    for s in data["students"]:
        if not 1 <= int(s["current_semester"]) <= 10: v.append(f"{s['student_id']}: current_semester out of 1-10")
        if not 0 <= float(s["cgpa"]) <= 10: v.append(f"{s['student_id']}: cgpa out of 0-10")
        if int(s["active_backlogs"]) < 0: v.append(f"{s['student_id']}: negative active_backlogs")
    for r in data["attendance"]:
        h, at = int(r["classes_held"]), int(r["classes_attended"])
        if h <= 0: v.append(f"{r['student_id']} {r['course_code']}: classes_held must be > 0")
        if not 0 <= at <= h: v.append(f"{r['student_id']} {r['course_code']}: attended {at} not in 0..{h}")
    for r in data["results"]:
        i, e, mx = _int(r["internal_marks"]), _int(r["external_marks"]), _int(r["max_marks"])
        if i is not None and i < 0 or e is not None and e < 0: v.append(f"{r['student_id']} {r['course_code']}: negative marks")
        if mx and _int(r["total_marks"]) is not None and _int(r["total_marks"]) > mx: v.append(f"{r['student_id']} {r['course_code']}: total exceeds max")
    check("V04", "Value ranges (semester, CGPA, backlogs, attendance, marks)", v)
    # V05 totals
    v = [f"{r['student_id']} {r['course_code']}: {r['internal_marks']}+{r['external_marks']} != {r['total_marks']}"
         for r in data["results"] if None not in (_int(r["internal_marks"]), _int(r["external_marks"]), _int(r["total_marks"]))
         and _int(r["internal_marks"]) + _int(r["external_marks"]) != _int(r["total_marks"])]
    check("V05", "total_marks = internal_marks + external_marks", v)
    # V06 foreign keys
    sids, codes = {s["student_id"] for s in data["students"]}, {c["course_code"] for c in data["courses"]}
    v = [f"{t}: {r['student_id']} {r['course_code']} references an unknown student or course"
         for t in ("attendance", "results") for r in data[t] if r["student_id"] not in sids or r["course_code"] not in codes]
    check("V06", "Attendance and results reference existing students and courses", v)
    # V07 programme consistency
    prog = {s["student_id"]: s["programme"] for s in data["students"]}
    cprog = {c["course_code"]: c["programme"] for c in data["courses"]}
    v = [f"course {c['course_code']} programme '{c['programme']}' matches no student" for c in data["courses"]
         if c["programme"] not in set(prog.values())]
    v += [f"{r['student_id']} takes {r['course_code']} from another programme" for t in ("attendance", "results")
          for r in data[t] if r["student_id"] in prog and r["course_code"] in cprog and cprog[r["course_code"]] != prog[r["student_id"]]]
    check("V07", "Course programmes match student programmes", v)
    # V08 semester order (warning)
    sem = {s["student_id"]: int(s["current_semester"]) for s in data["students"]}
    csem = {c["course_code"]: _int(c["semester"]) for c in data["courses"]}
    v = [f"{r['student_id']} {r['course_code']}: course semester {csem.get(r['course_code'])} > current {sem.get(r['student_id'])}"
         for r in data["attendance"] if csem.get(r["course_code"]) and sem.get(r["student_id"]) and csem[r["course_code"]] > sem[r["student_id"]]]
    check("V08", "Course semester <= student's current semester", v, "warning")
    # V09 result vs pass mark (from the rule registry)
    v = []
    for r in data["results"]:
        tot, mx = _int(r["total_marks"]), _int(r["max_marks"]) or 100
        if r["result"] in ("PASS", "FAIL") and tot is not None:
            should = "PASS" if Fraction(tot * 100, mx) >= pass_mark else "FAIL"
            if should != r["result"]:
                v.append(f"{r['student_id']} {r['course_code']}: {tot}/{mx} should be {should}, not {r['result']}")
    check("V09", f"Result consistent with the pass mark ({pass_mark}% from rule_registry)", v)
    # V10 absent
    v = [f"{r['student_id']} {r['course_code']}: ABSENT but external_marks = {r['external_marks']}" for r in data["results"]
         if r["result"] == "ABSENT" and _int(r["external_marks"]) not in (None, 0)]
    check("V10", "ABSENT results have no external marks", v)
    # V11 detained ⇒ attendance below the minimum
    att = {(r["student_id"], r["course_code"]): (int(r["classes_attended"]), int(r["classes_held"])) for r in data["attendance"]}
    v = []
    for r in data["results"]:
        if r["result"] == "DETAINED":
            k = (r["student_id"], r["course_code"])
            if k not in att:
                v.append(f"{k[0]} {k[1]}: DETAINED but no attendance record")
            elif Fraction(att[k][0] * 100, att[k][1]) >= min_att:
                v.append(f"{k[0]} {k[1]}: DETAINED but attendance {att[k][0]}/{att[k][1]} meets {min_att}%")
    check("V11", f"DETAINED only when attendance is below {min_att}% (rule_registry)", v)
    # V12 backlogs consistent with results
    latest: dict[tuple, dict] = {}
    for r in sorted(data["results"], key=lambda r: (r["exam_session"][:4], r["exam_type"] == "SUPPLEMENTARY")):
        latest[(r["student_id"], r["course_code"])] = r
    implied = Counter(sid for (sid, _), r in latest.items() if r["result"] != "PASS")
    v = [f"{s['student_id']}: active_backlogs {s['active_backlogs']} but results imply {implied.get(s['student_id'], 0)}"
         for s in data["students"] if int(s["active_backlogs"]) != implied.get(s["student_id"], 0)]
    check("V12", "active_backlogs equals courses whose latest attempt is not PASS", v)
    # V13 supplementary only after a non-PASS regular attempt
    by = defaultdict(list)
    for r in data["results"]:
        by[(r["student_id"], r["course_code"])].append(r)
    v = [f"{k[0]} {k[1]}: SUPPLEMENTARY without a failed REGULAR attempt" for k, rs in by.items()
         if any(r["exam_type"] == "SUPPLEMENTARY" for r in rs) and not any(r["exam_type"] == "REGULAR" and r["result"] != "PASS" for r in rs)]
    check("V13", "Supplementary attempts follow a non-PASS regular attempt", v)
    # V14 coverage
    v = []
    if len(data["students"]) < 30: v.append(f"only {len(data['students'])} students (need >= 30)")
    if len({s['programme'] for s in data['students']}) < 2: v.append("fewer than 2 programmes")
    if len({s['batch_year'] for s in data['students']}) < 2: v.append("fewer than 2 batches")
    if len(data["courses"]) < 6: v.append(f"only {len(data['courses'])} courses (need >= 6)")
    check("V14", "Coverage: >= 30 students, 2 programmes, 2 batches, 6 courses", v)
    # V15 edge-case manifest
    v = []
    if not a.no_manifest:
        st = {s["student_id"]: s for s in data["students"]}
        for e in yaml.safe_load((HERE / "edge_cases.yaml").read_text()):
            sid, exp = e["id"], e.get("expect", {})
            if sid not in st:
                v.append(f"{sid}: missing ({e['case']})"); continue
            for code, pct in (exp.get("attendance_pct") or {}).items():
                at, h = att.get((sid, code), (None, None))
                got = None if at is None else float(int(Fraction(at * 100, h) * 100) / 100)
                if got is None or abs(got - pct) > 1e-9: v.append(f"{sid} {code}: attendance {got}% != {pct}% ({e['case']})")
            for code, res in (exp.get("result") or {}).items():
                if latest.get((sid, code), {}).get("result") != res: v.append(f"{sid} {code}: latest result != {res} ({e['case']})")
            for code, tot in (exp.get("total") or {}).items():
                if _int(latest.get((sid, code), {}).get("total_marks")) != tot: v.append(f"{sid} {code}: total != {tot}")
            if "active_backlogs" in exp and int(st[sid]["active_backlogs"]) != exp["active_backlogs"]:
                v.append(f"{sid}: active_backlogs {st[sid]['active_backlogs']} != {exp['active_backlogs']} ({e['case']})")
            if "cgpa" in exp and abs(float(st[sid]["cgpa"]) - exp["cgpa"]) > 1e-9:
                v.append(f"{sid}: cgpa {st[sid]['cgpa']} != {exp['cgpa']} ({e['case']})")
    check("V15", "Every edge case in the manifest holds", v)
    # V16 distributions (info)
    pcts = [float(Fraction(int(r["classes_attended"]) * 100, int(r["classes_held"]))) for r in data["attendance"]]
    buckets = Counter("<65" if p < 65 else "65-74.99" if p < 75 else "75-79.99" if p < 80 else "80-89.99" if p < 90 else "90+" for p in pcts)
    dist = {"students_per_programme_batch": dict(Counter(f"{s['programme']} {s['batch_year']}" for s in data["students"])),
            "attendance_buckets_pct": dict(sorted(buckets.items())),
            "results": dict(Counter(r["result"] for r in data["results"])),
            "cgpa_min_max": [min(float(s["cgpa"]) for s in data["students"]), max(float(s["cgpa"]) for s in data["students"])] if data["students"] else []}
    check("V16", "Distribution report", [], "info")

    errors = [c for c in checks if c["severity"] == "error" and not c["passed"]]
    report = {"directory": str(d), "pass_mark_pct": float(pass_mark), "min_attendance_pct": float(min_att),
              "row_counts": {t: len(rows) for t, rows in data.items()}, "distributions": dist,
              "checks": checks, "errors": len(errors)}
    Path(a.report).write_text(json.dumps(report, indent=2))
    for c in checks:
        mark = "PASS" if c["passed"] else ("WARN" if c["severity"] == "warning" else "FAIL")
        print(f"{c['id']} {mark:4} {c['check']}" + (f"  ({c['violation_count']} violations)" if not c["passed"] else ""))
        for x in c["violations"][:5]:
            print(f"        - {x}")
    print(f"\n{len(errors)} failing check(s). Report: {a.report}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
