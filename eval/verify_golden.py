"""Check every expectation in eval/golden.yaml against the documents and the synthetic CSVs (no API, no LLM).

    uv run python eval/verify_golden.py [--dataset eval/golden.yaml] [-v]

1. Schema and guide §7 minimums (>= 20 items, >= 3 unanswerable, >= 3 versions, >= 4 personal, >= 2 other-student,
   >= 2 multi-step), session turns, repeats.
2. Document facts: each expected fact / regex of an answerable item appears in the text of one of its expected
   sources (doc#section, from the same parser and clause chunker the API uses).
3. Personal answers: every expected tool input/output value is recomputed from data/synthetic/out/*.csv with exact
   fractions and the attendance rule in force on the item's as_of date.
4. Leak checks: forbidden values really are the other student's data they are meant to catch.
5. Unanswerable items: the question's distinctive terms are absent from every document in force (heuristic; warning).
Exit code 1 on any failure in 1-4.
"""
from __future__ import annotations

import argparse
import csv
import math
import re
import sys
from datetime import date
from decimal import ROUND_DOWN, Decimal
from fractions import Fraction
from pathlib import Path

import yaml

from evallib import DEFAULT_AS_OF, HERE, ROOT, Text, subset_mismatch

sys.path.insert(0, str(ROOT))
from app.ingestion.chunker import chunk_document  # noqa: E402
from app.ingestion.parsers import parse  # noqa: E402

BUCKETS = {"policy_fact", "procedure", "unanswerable", "off_topic", "versions_conflicts", "personal_tools", "other_student",
           "bulk_pii", "multi_step", "safety", "clarification", "adversarial", "abuse", "follow_up", "cache"}
TYPES = {"retrieved_fact", "calculated", "not_found", "clarification_needed", "refused", "conflict_flagged", "error"}
MINIMUMS = {"unanswerable": 3, "versions_conflicts": 3, "personal_tools": 4, "other_student": 2, "multi_step": 2}

# The scanned notice has no text layer and OCR is not installed everywhere: transcribed by hand from the page image.
HOSTEL_TRANSCRIPT = ("Notice No. HOSTEL-NOTICE-2025 Date: 25 July 2025. Hostel timings and visitors. "
                     "1. Hostel gates close at 10:30 pm on all days. Late entry needs prior written permission of the warden. "
                     "2. Visitors are allowed in the visitors' lounge between 4:00 pm and 7:00 pm only. "
                     "3. Students leaving the campus overnight must make an entry in the movement register.")

# Attendance rule timeline from the documents: ACAD-REG-2024 §7.2 (75%, from 2024-07-01) is replaced by
# ACAD-2026-08 §1 (80%, from 2026-08-01). The FAQ's 65% (level 4) never wins against a level-2 circular.
ATTENDANCE_RULES = [(date(2024, 7, 1), Fraction(75), "ACAD-REG-2024#7.2"), (date(2026, 8, 1), Fraction(80), "ACAD-2026-08#1")]
CONDONATION = Fraction(10)                            # ACAD-REG-2024 §7.3 (up to 10%), kept by ACAD-2026-08 §2
SUPP_ALLOWED = {"FAIL", "ABSENT"}                     # ACAD-REG-2024 §8.3, EXAM-SUPP-2025 §1
MIN_CGPA, MAX_BACKLOGS = Fraction("6.5"), 0           # PLACE-POL-2025 §3
STOP = set("what when where which with will would could should about there their this that from have does your into "
           "than then them they were been being also only more most much many some such each very just like tell "
           "give need make made know does this year last next whats what's".split()) | set(
    "the for can how fee per and are was who why you not any has had its our out get got did may all new now one two use "
    "way day see say her his him she let put too yet set ask own off big top".split())


# ----------------------------------------------------------------------------- corpus
def load_corpus() -> tuple[dict[str, str], dict[str, str], dict[str, dict]]:
    """(doc#section -> text, doc -> full text, doc -> register row)."""
    reg = {r["doc_id"]: r for r in csv.DictReader(open(ROOT / "data/corpus/source_register.csv", newline=""))}
    demo = yaml.safe_load((ROOT / "data/corpus/demo/ACAD-2026-08.meta.json").read_text())
    reg["ACAD-2026-08"] = {**demo, "file": "../demo/ACAD-2026-08.pdf"}
    sections: dict[str, str] = {}
    docs: dict[str, str] = {}
    for doc_id, r in reg.items():
        path = (ROOT / "data/corpus/documents" / r["file"]).resolve()
        if doc_id == "HOSTEL-NOTICE-2025":
            sections["HOSTEL-NOTICE-2025#None"] = HOSTEL_TRANSCRIPT
            docs[doc_id] = HOSTEL_TRANSCRIPT
            continue
        parsed = parse(path.read_bytes(), path.suffix)
        for c in chunk_document(doc_id, parsed, "clause-v1"):
            key = f"{doc_id}#{c.section}"
            sections[key] = (sections.get(key, "") + " " + c.text).strip()
        docs[doc_id] = " ".join(p.text for p in parsed.pages) + " " + " ".join(
            " ".join(t.header) + " " + " ".join(" ".join(row) for row in t.rows) for t in parsed.tables)
    return sections, docs, reg


def source_text(src: str, sections: dict[str, str], docs: dict[str, str]) -> str:
    doc, _, sec = src.partition("#")
    if sec == "*":
        return docs.get(doc, "")
    return sections.get(src, "")


# ----------------------------------------------------------------------------- reference implementations
class Records:
    def __init__(self):
        d = ROOT / "data/synthetic/out"
        self.students = {r["student_id"]: r for r in csv.DictReader(open(d / "students.csv", newline=""))}
        self.courses = {r["course_code"]: r for r in csv.DictReader(open(d / "courses.csv", newline=""))}
        self.att: dict[tuple[str, str], tuple[int, int]] = {}
        for r in csv.DictReader(open(d / "attendance.csv", newline="")):
            self.att[(r["student_id"], r["course_code"])] = (int(r["classes_attended"]), int(r["classes_held"]))
        self.results: dict[str, list[dict]] = {}
        for r in csv.DictReader(open(d / "results.csv", newline="")):
            self.results.setdefault(r["student_id"], []).append(r)

    def courses_of(self, sid: str) -> list[str]:
        codes = {c for (s, c) in self.att if s == sid} | {r["course_code"] for r in self.results.get(sid, [])}
        return sorted(codes)

    def attempts(self, sid: str, code: str) -> list[dict]:
        def key(r):
            y, m = r["exam_session"].split("-")
            mon = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"].index(m[:3].upper()) + 1
            return int(y), mon, r["exam_type"] == "SUPPLEMENTARY"
        return sorted((r for r in self.results.get(sid, []) if r["course_code"] == code), key=key)

    def backlogs(self, sid: str) -> list[str]:
        return sorted(c for c in {r["course_code"] for r in self.results.get(sid, [])} if self.attempts(sid, c)[-1]["result"] != "PASS")


def threshold(as_of: date) -> Fraction:
    return [t for start, t, _ in ATTENDANCE_RULES if start <= as_of][-1]


def floor2(x: Fraction) -> float:
    return float((Decimal(x.numerator) / Decimal(x.denominator)).quantize(Decimal("0.01"), rounding=ROUND_DOWN))


def classes_needed(a: int, h: int, t: Fraction) -> int:
    t = t / 100
    return 0 if Fraction(a, h) >= t else math.ceil((t * h - a) / (1 - t))


def tool_output(db: Records, name: str, sid: str, course: str | None, as_of: date, question: str,
                cleared: list[str]) -> tuple[dict, dict, list[float]]:
    """(input, output, extra numbers that may legitimately appear in the answer) as the deterministic tool would compute."""
    if name == "check_attendance_value":                 # policy what-if on a stated percentage, no personal data
        vals = re.findall(r"(?<![\d.])(\d{1,3}(?:\.\d+)?)\s*(?:%|per\s?cent\b|percent\b)", question, re.I)
        assert len(vals) == 1, f"expected exactly one percentage in {question!r}"
        v, t = Fraction(vals[0]), threshold(as_of)
        inp = {"attendance_pct": vals[0], "medical_certificate": bool(re.search(r"medical|sick|ill(?:ness)?\b|hospital|doctor", question, re.I))}
        if v >= t:
            return inp, {"result": "MEETS_MINIMUM", "minimum_pct": float(t)}, []
        floor = t - CONDONATION
        return inp, {"result": "CONDONABLE" if v >= floor else "BELOW_CONDONABLE_FLOOR", "minimum_pct": float(t),
                     "condonable_pct": float(CONDONATION), "lowest_with_condonation_pct": float(floor)}, []
    s = db.students[sid]
    if name in ("get_attendance", "check_exam_eligibility", "attendance_projection"):
        a, h = db.att[(sid, course)]
        p = Fraction(a, h) * 100
        t = threshold(as_of)
        need = classes_needed(a, h, t)
        if name == "get_attendance":
            return {"course_code": course}, {"course_code": course, "classes_held": h, "classes_attended": a,
                                             "attendance_pct": floor2(p), "minimum_pct": float(t), "meets_minimum": p >= t}, []
        if name == "check_exam_eligibility":
            return {"course_code": course}, {"result": "ELIGIBLE" if p >= t else "NOT_ELIGIBLE", "classes_held": h,
                                             "classes_attended": a, "attendance_pct": floor2(p), "threshold": float(t),
                                             "classes_needed": need}, [a + need, h + need]
        m = re.search(r"\b(\d{1,3})\b(?:\s+\w+){0,3}\s+(?:classes|lectures)", question)
        future = int(m.group(1)) if m else 10
        max_abs = max(0, math.floor(a + future - (t / 100) * (h + future)))
        return {"course_code": course, "future_classes": future}, {"classes_needed": need, "max_absences": max_abs,
                                                                     "future_classes": future, "attendance_pct": floor2(p),
                                                                     "threshold": float(t)}, [a + need, h + need]
    if name == "get_results":
        att = db.attempts(sid, course)
        last = att[-1]
        return {"course_code": course}, {"course_code": course, "latest_result": last["result"]}, \
            [int(last["total_marks"])] if last["total_marks"] else []
    if name == "check_supplementary_eligibility":
        last = db.attempts(sid, course)[-1]["result"]
        res = "NOT_NEEDED" if last == "PASS" else ("ELIGIBLE" if last in SUPP_ALLOWED else "NOT_ELIGIBLE")
        return {"course_code": course}, {"result": res, "latest_result": last}, []
    if name == "get_student_profile":
        return {}, {"cgpa": float(s["cgpa"]), "active_backlogs": int(s["active_backlogs"])}, []
    if name == "check_placement_eligibility":
        bk = db.backlogs(sid)
        assert len(bk) == int(s["active_backlogs"]), f"{sid}: results imply {len(bk)} backlogs, students.csv says {s['active_backlogs']}"
        n = int(s["active_backlogs"]) - len([c for c in cleared if c in bk])
        ok = Fraction(s["cgpa"]) >= MIN_CGPA and n <= MAX_BACKLOGS
        return {}, {"result": "ELIGIBLE" if ok else "NOT_ELIGIBLE", "cgpa": float(s["cgpa"]), "active_backlogs": n}, []
    raise ValueError(name)


def mentioned_courses(db: Records, sid: str, question: str) -> list[str]:
    q = question.lower()
    found = []
    for code in db.courses_of(sid):
        name = db.courses[code]["course_name"].lower()
        if re.search(rf"\b{code.lower()}\b", q) or re.search(rf"\b{re.escape(name)}(?![\w(])", q):
            found.append(code)
    return found


# ----------------------------------------------------------------------------- checks
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=str(HERE / "golden.yaml"))
    ap.add_argument("-v", "--verbose", action="store_true")
    a = ap.parse_args()
    items = yaml.safe_load(Path(a.dataset).read_text())
    by_id = {i["id"]: i for i in items}
    fails: list[str] = []
    warns: list[str] = []
    checks = 0

    def check(ok: bool, msg: str) -> None:
        nonlocal checks
        checks += 1
        if not ok:
            fails.append(msg)
        elif a.verbose:
            print("  ok  ", msg)

    # 1. schema and minimums
    check(len(items) >= 20, f"dataset has {len(items)} items (>= 20)")
    check(len(by_id) == len(items), "ids are unique")
    for b, n in MINIMUMS.items():
        have = sum(1 for i in items if i["bucket"] == b)
        check(have >= n, f"bucket {b}: {have} items (>= {n})")
    sessions: dict[str, list[dict]] = {}
    for it in items:
        types = it.get("expected_answer_type_in") or [it.get("expected_answer_type")]
        if it.get("repeat_of"):
            orig = by_id.get(it["repeat_of"])
            check(orig is not None, f"{it['id']}: repeat_of {it['repeat_of']} exists")
            if orig:
                types = orig.get("expected_answer_type_in") or [orig.get("expected_answer_type")]
                check(it["question"] == orig["question"] and it.get("student_id") == orig.get("student_id")
                      and it.get("as_of_date") == orig.get("as_of_date"), f"{it['id']}: repeats {orig['id']} verbatim")
        check(it["bucket"] in BUCKETS, f"{it['id']}: bucket {it['bucket']} is known")
        check(all(t in TYPES for t in types), f"{it['id']}: expected types {types} are valid")
        check(it.get("difficulty") in ("easy", "medium", "hard") and isinstance(it.get("tags"), list) and bool(it.get("reference")),
              f"{it['id']}: has difficulty, tags and reference")
        for rx in it.get("expected_regex", []):
            try:
                re.compile(rx)
            except re.error as e:
                check(False, f"{it['id']}: regex {rx!r} compiles ({e})")
        if it.get("as_of_date"):
            check(bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(it["as_of_date"]))), f"{it['id']}: as_of_date is ISO")
        if it.get("session"):
            sessions.setdefault(it["session"], []).append(it)
    for s, turns in sessions.items():
        check(sorted(t.get("turn") for t in turns) == list(range(1, len(turns) + 1)), f"session {s}: turns are 1..{len(turns)}")

    # 2. document facts
    sections, docs, reg = load_corpus()
    for it in items:
        types = it.get("expected_answer_type_in") or [it.get("expected_answer_type")]
        srcs = it.get("expected_sources", [])
        for src in srcs:
            check(bool(source_text(src, sections, docs)), f"{it['id']}: expected source {src} exists in the corpus")
        if "retrieved_fact" not in types or not srcs:
            continue
        texts = [Text(source_text(s, sections, docs)) for s in srcs]
        for f in it.get("expected_facts", []):
            check(any(t.has(f) for t in texts), f"{it['id']}: fact {f!r} is stated in {', '.join(srcs)}")
        for rx in it.get("expected_regex", []):
            check(any(t.matches(rx) for t in texts), f"{it['id']}: /{rx}/ matches {', '.join(srcs)}")

    # 3. personal answers recomputed from the CSVs
    db = Records()
    last_course: dict[str, str] = {}
    for it in sorted(items, key=lambda i: (i.get("session") or "", i.get("turn") or 0)):
        exp_tools = list(it.get("expected_tools") or []) + ([it["expected_tool"]] if it.get("expected_tool") else [])
        if it.get("repeat_of"):
            orig = by_id[it["repeat_of"]]
            exp_tools = list(orig.get("expected_tools") or []) + ([orig["expected_tool"]] if orig.get("expected_tool") else [])
        if not exp_tools:
            continue
        sid = it.get("student_id")
        as_of = date.fromisoformat(str(it.get("as_of_date") or DEFAULT_AS_OF))
        mentioned = mentioned_courses(db, sid, it["question"]) if sid else []
        numbers_ok: list[float] = []
        for et in exp_tools:
            course = (et.get("input") or {}).get("course_code") or (et.get("output") or {}).get("course_code")
            no_course = ("get_student_profile", "check_placement_eligibility", "check_attendance_value")
            if not course and et["name"] not in no_course:
                course = mentioned[0] if len(mentioned) == 1 else last_course.get(it.get("session") or "")
            check(course is not None or et["name"] in no_course, f"{it['id']}: course for {et['name']} resolved")
            if course is None and et["name"] not in no_course:
                continue
            cleared = [c for c in mentioned if c in db.backlogs(sid)] if sid and re.search(r"\bpass", it["question"], re.I) else []
            inp, out, extra = tool_output(db, et["name"], sid, course, as_of, it["question"], cleared)
            mism = subset_mismatch(et.get("input", {}), inp) + subset_mismatch(et.get("output", {}), out)
            check(not mism, f"{it['id']}: {et['name']}({course or ''}) as of {as_of} = expected" + (f"  [{'; '.join(mism)}]" if mism else ""))
            numbers_ok += [v for v in out.values() if isinstance(v, (int, float)) and not isinstance(v, bool)] + extra
            if course and it.get("session"):
                last_course[it["session"]] = course
        for f in it.get("expected_facts", []):
            if re.fullmatch(r"\d+(?:\.\d+)?", str(f)):
                check(any(abs(float(f) - float(n)) < 1e-9 for n in numbers_ok), f"{it['id']}: numeric fact {f} follows from the records")

    # 4. leak checks: forbidden values are the data they are meant to catch
    def att_pct(sid, code):
        a_, h_ = db.att[(sid, code)]
        return floor2(Fraction(a_, h_) * 100)
    leak_facts = {
        "FU5b": ([str(att_pct("S1001", "CS202")), db.students["S1001"]["full_name"].split()[0]], "S1001's CS202 attendance and name (session owner)"),
        "CA3": ([str(att_pct("S1007", "CS202"))], "S1007's CS202 attendance (cached for another student)"),
        "BK4": (sorted({s for s, r in db.students.items() if Fraction(r["cgpa"]) < MIN_CGPA}
                       | {r["full_name"].split()[0] for r in db.students.values() if Fraction(r["cgpa"]) < MIN_CGPA}),
                "students below the placement CGPA"),
    }
    for iid, (vals, what) in leak_facts.items():
        if iid in by_id:
            got = sorted(str(f) for f in by_id[iid].get("forbidden_facts", []))
            check(sorted(vals) == got, f"{iid}: forbidden facts {got} are exactly {what} {sorted(vals)}")
    for iid in ("PT9", "VC14"):
        if iid in by_id:
            check("79.7" in [str(f) for f in by_id[iid].get("forbidden_facts", [])] and att_pct("S1007", "CS202") == 79.66,
                  f"{iid}: 79.66% must not be rounded to 79.7")

    # 5. unanswerable heuristics
    for it in items:
        types = it.get("expected_answer_type_in") or [it.get("expected_answer_type")]
        if set(types) != {"not_found"}:
            continue
        as_of = date.fromisoformat(str(it.get("as_of_date") or DEFAULT_AS_OF))
        in_force = " ".join(t for d, t in docs.items()
                            if date.fromisoformat(str(reg[d]["effective_from"])) <= as_of).lower()
        terms = {w for w in re.findall(r"[a-z]{3,}", it["question"].lower()) if w not in STOP}
        absent = sorted(w for w in terms if w not in in_force and w.rstrip("s") not in in_force)
        if absent:
            if a.verbose:
                print(f"  ok   {it['id']}: terms absent from documents in force on {as_of}: {absent}")
        else:
            warns.append(f"{it['id']}: every content word appears in documents in force on {as_of}; check by hand")

    for w in warns:
        print("  WARN", w)
    for f in fails:
        print("  FAIL", f)
    print(f"{checks} checks, {len(fails)} failed, {len(warns)} warnings ({len(items)} items)")
    return 1 if fails else 0


if __name__ == "__main__":
    raise SystemExit(main())
