"""Deterministic, identity-bound tools.

Identity is injected through ToolContext. No tool input has a student_id field, so a plan cannot
even express "fetch another student's records". Every query below filters by ctx.student_id.
Decisive tools produce the verdict sentence themselves; the LLM only explains it.
"""
from __future__ import annotations

import difflib
import re
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction
from typing import Callable

from app.db import one, rows
from app.policy.precedence import StudentScope
from app.policy.rules import PARAMETERS, RuleResult, resolve_rule
from app.tools.arithmetic import as_fraction, classes_needed, compare, floor_2dp, fmt_pct, max_absences, pct

MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


@dataclass
class ToolContext:
    con: sqlite3.Connection
    student: dict | None
    as_of: date
    scope_override: StudentScope | None = None     # policy questions that name a programme/batch ("for B.Arch 2025")

    @property
    def student_id(self) -> str | None:
        return self.student["student_id"] if self.student else None

    @property
    def scope(self) -> StudentScope | None:
        if self.scope_override is not None:
            return self.scope_override
        return StudentScope(self.student["programme"], self.student["batch_year"]) if self.student else None


@dataclass
class ToolResult:
    tool: str
    input: dict
    output: dict
    status: str = "ok"
    ms: int = 0
    decisive: bool = False
    verdict: str | None = None
    rules: list[RuleResult] = field(default_factory=list)


class ToolError(Exception):
    pass


# ----------------------------------------------------------------------------- helpers
def _session_key(session: str, exam_type: str) -> tuple:
    m = re.match(r"(\d{4})\W*([A-Za-z]{3})", session or "")
    year, mon = (int(m.group(1)), MONTHS.get(m.group(2).upper(), 0)) if m else (0, 0)
    return (year, mon, 1 if exam_type == "SUPPLEMENTARY" else 0, session)


def _course(ctx: ToolContext, code: str) -> dict:
    c = one(ctx.con, "SELECT * FROM courses WHERE course_code = ?", (code,))
    if not c:
        raise ToolError(f"unknown course {code}")
    return c


def _attempts(ctx: ToolContext, code: str | None = None) -> list[dict]:
    sql = "SELECT * FROM results WHERE student_id = ?" + (" AND course_code = ?" if code else "")
    out = rows(ctx.con, sql, (ctx.student_id, code) if code else (ctx.student_id,))
    return sorted(out, key=lambda r: _session_key(r["exam_session"], r["exam_type"]))


def current_backlogs(ctx: ToolContext) -> list[str]:
    latest: dict[str, dict] = {}
    for r in _attempts(ctx):
        latest[r["course_code"]] = r
    return sorted(c for c, r in latest.items() if r["result"] != "PASS")


def _rule(ctx: ToolContext, parameter: str) -> RuleResult:
    return resolve_rule(ctx.con, parameter, ctx.scope, ctx.as_of)[0]


def _name(c: dict) -> str:
    return f"{c['course_name']} ({c['course_code']})"


def _classes(n: int) -> str:
    return "class" if n == 1 else f"{n} classes"


# ----------------------------------------------------------------------------- course resolution (used by authorize)
def student_courses(ctx: ToolContext) -> list[dict]:
    return rows(ctx.con, """SELECT DISTINCT c.* FROM courses c
        LEFT JOIN attendance a ON a.course_code = c.course_code AND a.student_id = :s
        LEFT JOIN results r ON r.course_code = c.course_code AND r.student_id = :s
        WHERE a.student_id IS NOT NULL OR r.student_id IS NOT NULL
        ORDER BY c.semester DESC, c.course_code""", {"s": ctx.student_id})


def resolve_course(ctx: ToolContext, mention: str) -> tuple[str | None, list[dict]]:
    """Return (course_code, []) on a unique match, else (None, candidates)."""
    courses = student_courses(ctx) or rows(ctx.con, "SELECT * FROM courses")
    m = mention.strip().lower()
    exact = [c for c in courses if c["course_code"].lower() == m or c["course_name"].lower() == m]
    if len(exact) == 1:
        return exact[0]["course_code"], []
    contains = [c for c in courses if m in c["course_name"].lower() or c["course_code"].lower() in m]
    if len(contains) == 1:
        return contains[0]["course_code"], []
    if len(contains) > 1:
        return None, contains
    scored = sorted(((difflib.SequenceMatcher(None, m, c["course_name"].lower()).ratio(), c) for c in courses),
                    key=lambda x: -x[0])
    if scored and scored[0][0] >= 0.72 and (len(scored) == 1 or scored[0][0] - scored[1][0] > 0.08):
        return scored[0][1]["course_code"], []
    return None, [c for s, c in scored[:4] if s >= 0.45] or courses


# ----------------------------------------------------------------------------- tools
def get_student_profile(ctx: ToolContext) -> ToolResult:
    s = ctx.student
    out = {k: s[k] for k in ("programme", "batch_year", "current_semester", "cgpa", "active_backlogs")}
    verdict = f"Your CGPA is {s['cgpa']:.2f} and you have {s['active_backlogs']} active backlog{'s' if s['active_backlogs'] != 1 else ''}."
    return ToolResult("get_student_profile", {}, out, decisive=True, verdict=verdict)


def get_attendance(ctx: ToolContext, course_code: str) -> ToolResult:
    a = one(ctx.con, "SELECT * FROM attendance WHERE student_id = ? AND course_code = ?", (ctx.student_id, course_code))
    c = _course(ctx, course_code)
    if not a:
        return ToolResult("get_attendance", {"course_code": course_code}, {"found": False}, decisive=True,
                          verdict=f"There is no attendance record for you in {_name(c)}.")
    p = pct(a["classes_attended"], a["classes_held"])
    out = {"course_code": course_code, "classes_held": a["classes_held"], "classes_attended": a["classes_attended"],
           "attendance_pct": float(floor_2dp(p))}
    verdict = f"Your attendance in {_name(c)} is {a['classes_attended']} of {a['classes_held']} classes, {fmt_pct(p)}%."
    rr, rule, problem = _threshold(ctx, "min_attendance_pct")
    if rule:
        t = as_fraction(rule["value"])
        meets = compare(p, rule["operator"], t)
        out.update(minimum_pct=float(t), meets_minimum=meets, rule_id=rule["rule_id"])
        verdict += (f" That meets the current minimum of {float(t):g}%." if meets else
                    f" That is below the current minimum of {float(t):g}%.")
    return ToolResult("get_attendance", {"course_code": course_code}, out, decisive=True, verdict=verdict, rules=[rr])


def get_results(ctx: ToolContext, course_code: str) -> ToolResult:
    c = _course(ctx, course_code)
    att = _attempts(ctx, course_code)
    if not att:
        return ToolResult("get_results", {"course_code": course_code}, {"attempts": []}, decisive=True,
                          verdict=f"There is no exam result for you in {_name(c)} yet.")
    last = att[-1]
    out = {"course_code": course_code, "attempts": [{k: r[k] for k in ("exam_session", "exam_type", "internal_marks",
           "external_marks", "total_marks", "max_marks", "result")} for r in att], "latest_result": last["result"]}
    marks = f" with {last['total_marks']}/{last['max_marks']}" if last["total_marks"] is not None and last["max_marks"] else ""
    verdict = (f"Your latest result in {_name(c)} is {last['result']}{marks} "
               f"({last['exam_session']}, {last['exam_type'].lower()}).")
    return ToolResult("get_results", {"course_code": course_code}, out, decisive=True, verdict=verdict)


def get_rule(ctx: ToolContext, parameter: str) -> ToolResult:
    if parameter not in PARAMETERS:
        raise ToolError(f"unknown parameter {parameter}")
    results = resolve_rule(ctx.con, parameter, ctx.scope, ctx.as_of)
    outs = []
    for rr in results:
        w = rr.winner
        o = {"scope": rr.scope_label} if rr.scope_label else {}
        if w:
            o.update(rule_id=w["rule_id"], operator=w["operator"], value=w["value"],
                     source_doc_id=w["source_doc_id"], source_section=w["source_section"])
        else:
            o.update(result="RULE_CONFLICT" if rr.resolution.unresolved else "RULE_UNAVAILABLE")
        outs.append(o)
    output = outs[0] if len(outs) == 1 else {"by_scope": outs}
    return ToolResult("get_rule", {"parameter": parameter}, output, rules=results)


def _threshold(ctx: ToolContext, parameter: str) -> tuple[RuleResult, dict | None, str | None]:
    rr = _rule(ctx, parameter)
    if rr.resolution.unresolved:
        return rr, None, "RULE_CONFLICT"
    if not rr.winner:
        return rr, None, "RULE_UNAVAILABLE"
    return rr, rr.winner, None


def check_exam_eligibility(ctx: ToolContext, course_code: str) -> ToolResult:
    c = _course(ctx, course_code)
    inp = {"course_code": course_code}
    a = one(ctx.con, "SELECT * FROM attendance WHERE student_id = ? AND course_code = ?", (ctx.student_id, course_code))
    rr, rule, problem = _threshold(ctx, "min_attendance_pct")
    if not a:
        return ToolResult("check_exam_eligibility", inp, {"result": "NO_ATTENDANCE_RECORD"}, rules=[rr],
                          decisive=True, verdict=f"There is no attendance record for you in {_name(c)}, so eligibility cannot be checked.")
    p = pct(a["classes_attended"], a["classes_held"])
    base = {"classes_held": a["classes_held"], "classes_attended": a["classes_attended"], "attendance_pct": float(floor_2dp(p))}
    if problem:
        return ToolResult("check_exam_eligibility", inp, {"result": problem, **base}, rules=[rr])
    t = as_fraction(rule["value"])
    ok = compare(p, rule["operator"], t)
    need = 0 if ok else classes_needed(a["classes_attended"], a["classes_held"], t)
    out = {"result": "ELIGIBLE" if ok else "NOT_ELIGIBLE", **base, "threshold": float(t), "rule_id": rule["rule_id"],
           "classes_needed": need, "projected_attended": a["classes_attended"] + need, "projected_held": a["classes_held"] + need}
    if ok:
        verdict = (f"You are eligible to sit the {_name(c)} end-semester exam: your attendance is {fmt_pct(p)}%, "
                   f"which meets the required {float(t):g}%.")
    else:
        verdict = (f"You are not eligible to sit the {_name(c)} end-semester exam: your attendance is {fmt_pct(p)}%, "
                   f"below the required {float(t):g}%. Attending the next {_classes(need)} would bring you to "
                   f"{a['classes_attended'] + need} of {a['classes_held'] + need}.")
    return ToolResult("check_exam_eligibility", inp, out, decisive=True, verdict=verdict, rules=[rr])


def check_supplementary_eligibility(ctx: ToolContext, course_code: str) -> ToolResult:
    c = _course(ctx, course_code)
    inp = {"course_code": course_code}
    att = _attempts(ctx, course_code)
    rr, rule, problem = _threshold(ctx, "supplementary_allowed_results")
    if not att:
        return ToolResult("check_supplementary_eligibility", inp, {"result": "NO_RESULT"}, decisive=True, rules=[rr],
                          verdict=f"There is no exam result for you in {_name(c)}, so there is nothing to clear in a supplementary exam.")
    last = att[-1]["result"]
    if problem:
        return ToolResult("check_supplementary_eligibility", inp, {"result": problem, "latest_result": last}, rules=[rr])
    allowed = [v.strip().upper() for v in re.split(r"[;,|]", rule["value"]) if v.strip()]
    if last == "PASS":
        out, verdict = ({"result": "NOT_NEEDED", "latest_result": last, "rule_id": rule["rule_id"]},
                        f"You have already passed {_name(c)}, so no supplementary exam is needed.")
    elif last in allowed:
        out, verdict = ({"result": "ELIGIBLE", "latest_result": last, "allowed_results": allowed, "rule_id": rule["rule_id"]},
                        f"You can register for the {_name(c)} supplementary exam: your latest result is {last}.")
    else:
        out, verdict = ({"result": "NOT_ELIGIBLE", "latest_result": last, "allowed_results": allowed, "rule_id": rule["rule_id"]},
                        f"You cannot register for the {_name(c)} supplementary exam: your latest result is {last}, "
                        f"and only {' or '.join(allowed)} results may register.")
    return ToolResult("check_supplementary_eligibility", inp, out, decisive=True, verdict=verdict, rules=[rr])


def check_placement_eligibility(ctx: ToolContext, assume_cleared: list[str] | None = None,
                                assume_cgpa: float | None = None) -> ToolResult:
    assume_cleared = assume_cleared or []
    inp = {"assume_cleared": assume_cleared, "assume_cgpa": assume_cgpa}
    r_cgpa, rule_cgpa, p1 = _threshold(ctx, "min_cgpa_placement")
    r_bk, rule_bk, p2 = _threshold(ctx, "max_active_backlogs_placement")
    backlogs = current_backlogs(ctx)                    # which courses are backlogs, from results
    cleared = [c for c in assume_cleared if c in backlogs]
    remaining = [c for c in backlogs if c not in cleared]
    n_backlogs = max(0, int(ctx.student["active_backlogs"]) - len(cleared))   # Annex C field is authoritative
    cgpa = Fraction(str(assume_cgpa if assume_cgpa is not None else ctx.student["cgpa"]))
    assumptions = [f"you pass {c}" for c in cleared] + (["CGPA unchanged"] if cleared and assume_cgpa is None else [])
    base = {"cgpa": float(cgpa), "active_backlogs": n_backlogs, "assumptions_applied": assumptions, "backlog_courses": remaining}
    if p1 or p2:
        return ToolResult("check_placement_eligibility", inp, {"result": p1 or p2, **base}, rules=[r_cgpa, r_bk])
    t_cgpa, t_bk = as_fraction(rule_cgpa["value"]), as_fraction(rule_bk["value"])
    ok_cgpa = compare(cgpa, rule_cgpa["operator"], t_cgpa)
    ok_bk = compare(Fraction(n_backlogs), rule_bk["operator"], t_bk)
    ok = ok_cgpa and ok_bk
    out = {"result": "ELIGIBLE" if ok else "NOT_ELIGIBLE", **base, "min_cgpa": float(t_cgpa), "max_backlogs": int(t_bk),
           "rule_ids": [rule_cgpa["rule_id"], rule_bk["rule_id"]]}
    prefix = ("If " + " and ".join(f"you pass {c}" for c in cleared) + ", you" if cleared else "You")
    if ok:
        verdict = f"{prefix} would be eligible for placements: CGPA {float(cgpa):.2f} (minimum {float(t_cgpa):g}) and {n_backlogs} active backlogs (maximum {int(t_bk)})."
        if not cleared:
            verdict = verdict.replace("would be", "are")
    else:
        reasons = []
        if not ok_cgpa:
            reasons.append(f"CGPA {float(cgpa):.2f} is below the minimum {float(t_cgpa):g}")
        if not ok_bk:
            reasons.append(f"{n_backlogs} active backlog{'s' if n_backlogs != 1 else ''} ({', '.join(remaining)}) exceed{'s' if n_backlogs == 1 else ''} the maximum of {int(t_bk)}")
        verdict = f"{prefix} {'would still not be' if cleared else 'are not yet'} eligible for placements: " + "; ".join(reasons) + "."
    return ToolResult("check_placement_eligibility", inp, out, decisive=True, verdict=verdict, rules=[r_cgpa, r_bk])


def attendance_projection(ctx: ToolContext, course_code: str, future_classes: int) -> ToolResult:
    c = _course(ctx, course_code)
    inp = {"course_code": course_code, "future_classes": future_classes}
    a = one(ctx.con, "SELECT * FROM attendance WHERE student_id = ? AND course_code = ?", (ctx.student_id, course_code))
    rr, rule, problem = _threshold(ctx, "min_attendance_pct")
    if not a or problem:
        return ToolResult("attendance_projection", inp, {"result": problem or "NO_ATTENDANCE_RECORD"}, rules=[rr])
    t = as_fraction(rule["value"])
    m = max_absences(a["classes_attended"], a["classes_held"], future_classes, t)
    need = classes_needed(a["classes_attended"], a["classes_held"], t)
    out = {"max_absences": m, "future_classes": future_classes, "threshold": float(t), "rule_id": rule["rule_id"],
           "classes_held": a["classes_held"], "classes_attended": a["classes_attended"], "classes_needed": need,
           "attendance_pct": float(floor_2dp(pct(a["classes_attended"], a["classes_held"])))}
    if need:
        verdict = (f"You need to attend the next {_classes(need)} in {_name(c)} without a break to reach the required "
                   f"{float(t):g}% ({a['classes_attended'] + need} of {a['classes_held'] + need}).")
    else:
        verdict = (f"Of the next {future_classes} classes in {_name(c)}, you can miss at most {m} and still meet the "
                   f"required {float(t):g}% (you have attended {a['classes_attended']} of {a['classes_held']} so far).")
    return ToolResult("attendance_projection", inp, out, decisive=True, verdict=verdict, rules=[rr])


def _num(x: Fraction) -> str:
    return str(x.numerator) if x.denominator == 1 else f"{floor_2dp(x).normalize():f}"


def check_attendance_value(ctx: ToolContext, value_pct: str, medical: bool = False) -> ToolResult:
    """Policy what-if, no personal data: is a stated attendance percentage enough to sit end-semester exams?
    Both thresholds come from the rule registry (minimum attendance, and the shortage that can be condoned)."""
    inp = {"attendance_pct": value_pct, "medical_certificate": medical}
    v = as_fraction(value_pct)
    rr, rule, problem = _threshold(ctx, "min_attendance_pct")
    if problem:
        return ToolResult("check_attendance_value", inp, {"result": problem}, rules=[rr])
    t = as_fraction(rule["value"])
    out = {"minimum_pct": float(t), "rule_id": rule["rule_id"]}
    if v >= t:
        return ToolResult("check_attendance_value", inp, {**out, "result": "MEETS_MINIMUM"}, decisive=True, rules=[rr],
                          verdict=f"Yes: {_num(v)}% attendance meets the {_num(t)}% minimum required to sit the end-semester exam.")
    crr, crule, cproblem = _threshold(ctx, "max_condonation_pct")
    if cproblem:
        return ToolResult("check_attendance_value", inp, {**out, "result": "BELOW_MINIMUM"}, decisive=True, rules=[rr, crr],
                          verdict=f"No: {_num(v)}% attendance is below the {_num(t)}% minimum required to sit the end-semester exam.")
    c = as_fraction(crule["value"])
    floor = t - c
    out.update(condonable_pct=float(c), lowest_with_condonation_pct=float(floor), condonation_rule_id=crule["rule_id"])
    if v >= floor:
        verdict = (f"Only with condonation: {_num(v)}% is below the {_num(t)}% minimum, but a shortage of up to {_num(c)}% can be "
                   f"condoned on medical grounds, so {_num(v)}% is enough only if your condonation is approved."
                   if medical else
                   f"No, not by itself: {_num(v)}% is below the {_num(t)}% minimum. A shortage of up to {_num(c)}% can be "
                   f"condoned on medical grounds, so {_num(v)}% is enough only if a medical condonation is approved.")
        return ToolResult("check_attendance_value", inp, {**out, "result": "CONDONABLE"}, decisive=True, rules=[rr, crr],
                          verdict=verdict)
    return ToolResult("check_attendance_value", inp, {**out, "result": "BELOW_CONDONABLE_FLOOR"}, decisive=True, rules=[rr, crr],
                      verdict=f"No: {_num(v)}% is below the {_num(t)}% minimum, and condonation on medical grounds covers a "
                              f"shortage of at most {_num(c)}%, so attendance must be at least {_num(floor)}%"
                              f"{' even with a medical certificate' if medical else ''}.")


@dataclass(frozen=True)
class ToolSpec:
    fn: Callable[..., ToolResult]
    description: str
    personal: bool
    needs_course: bool = False


TOOLS: dict[str, ToolSpec] = {
    "get_student_profile": ToolSpec(get_student_profile, "the user's CGPA, backlogs, programme, semester", True),
    "get_attendance": ToolSpec(get_attendance, "the user's attendance in one course", True, True),
    "get_results": ToolSpec(get_results, "the user's exam results in one course", True, True),
    "get_rule": ToolSpec(get_rule, "the threshold that currently applies for a rule parameter", False),
    "check_exam_eligibility": ToolSpec(check_exam_eligibility, "whether the user may sit a course's end-semester exam (attendance rule)", True, True),
    "check_supplementary_eligibility": ToolSpec(check_supplementary_eligibility, "whether the user may register for a course's supplementary exam", True, True),
    "check_placement_eligibility": ToolSpec(check_placement_eligibility, "whether the user may register for placements, optionally assuming courses are cleared", True),
    "attendance_projection": ToolSpec(attendance_projection, "how many of the next N classes the user can miss", True, True),
    "check_attendance_value": ToolSpec(check_attendance_value, "whether a stated attendance percentage is enough (policy what-if)", False),
}


def run_tool(name: str, ctx: ToolContext, **kwargs) -> ToolResult:
    spec = TOOLS[name]
    if spec.personal and ctx.student is None:
        raise ToolError("personal tool called without identity")
    t0 = time.perf_counter()
    try:
        res = spec.fn(ctx, **kwargs)
    except ToolError as e:
        res = ToolResult(name, kwargs, {"error": str(e)}, status="error")
    res.ms = int((time.perf_counter() - t0) * 1000)
    return res
