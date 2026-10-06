"""The /ask pipeline as a LangGraph state machine.

guard -> plan (LLM 1; skipped when the router is sure) -> authorize -> execute_tools -> retrieve
      -> compose (LLM 2) -> verify -> finalize
Early exits go straight to finalize with a templated answer and no further LLM call.

Around the graph, ask() rewrites session follow-ups into standalone questions and serves repeat questions from the
exact and semantic answer caches (keys include the data version, so an ingest or a records load invalidates them).
"""
from __future__ import annotations

import json
import operator
import re
import time
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Any, TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app import audit, cache, metrics
from app.conversation import Turn, contextualize, sessions
from app.db import data_version, one, rows, session
from app.graph.verify import (check_draft, evidence_coverage, groundedness, infer_citations, numbers, says_not_covered,
                              sources_for_grounding, strip_markers)
from app.ingestion.injection import redact
from app.llm.client import LLMError
from app.llm.composer import ComposerOutput, build_prompt, facts_from_tools
from app.llm.context import build_context
from app.llm.planner import PERSONAL, Plan, attendance_whatif, plan_question, route
from app.models import AppliedRule, AskMeta, AskResponse, Citation, ConflictRecord, SourceRef, ToolInvocation
from app.policy.precedence import StudentScope
from app.policy.rules import PARAMETERS
from app.retrieval.query import canonical, question_date, question_scope
from app.retrieval.retriever import Evidence, RetrievalResult, amounts, retrieve
from app.security.guardrails import REFUSALS, check_input, check_output, redact_pii
from app.services import Services
from app.tools.core import TOOLS, ToolContext, resolve_course, run_tool, student_courses

NOT_FOUND = "I could not find this information in the authorised university sources."
OTHER_STUDENT = ("I can only share your own records, so I can't answer questions about another student.",
                 "Personal records are available only to the student who is signed in.")
AVAILABILITY = ("unavailable", "timeout")
THRESHOLD_Q = re.compile(r"\b(?:minimum|maximum|min|max|threshold|cut-?off|required|requirement|at least|"
                         r"what percentage|how much attendance|which rule|what rule|limit)\b")
CACHEABLE = ("retrieved_fact", "calculated", "conflict_flagged", "not_found")


def _merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class AskState(TypedDict, total=False):
    trace_id: str
    question: str                    # standalone question (a rewritten follow-up, or the question as asked)
    original_question: str | None    # the question as asked, when it was rewritten
    rewrite: str | None
    session_id: str | None
    header_student_id: str | None
    as_of: date
    as_of_source: str
    started: float
    student: dict | None
    unknown_identity: bool
    q_redacted: str
    guardrail: dict | None
    plan: Plan | None
    plan_source: str
    whatif: dict | None              # policy what-if decided by code ("Is 65% attendance enough?")
    block: dict | None
    course_code: str | None
    pass_codes: list[str]
    tool_results: Annotated[list, operator.add]
    rule_results: Annotated[list, operator.add]
    retrieval: RetrievalResult | None
    prompt_ids: list[str]
    context_report: dict
    draft: ComposerOutput | None
    feedback: list[str]
    retries: int
    retry: bool
    verification: dict
    groundedness: float | None
    unsupported: list[str]
    llm_calls: Annotated[list, operator.add]
    llm_errors: Annotated[list, operator.add]
    timings: Annotated[dict, _merge]
    notes: Annotated[list, operator.add]
    response: AskResponse | None


def _scope(student: dict | None) -> StudentScope | None:
    return StudentScope(student["programme"], student["batch_year"]) if student else None


def _refuse(answer: str, explanation: str) -> dict:
    return {"type": "refused", "answer": answer, "explanation": explanation, "options": []}


def _name_in(full_name: str, q: str) -> bool:
    toks = [t for t in re.findall(r"[A-Za-z]+", full_name) if len(t) > 1]
    if len(toks) < 2:
        return False
    ql = q.lower()
    return all(re.search(rf"\b{re.escape(t.lower())}\b", ql) for t in (toks[0], toks[-1]))


def _policy_scope(state: "AskState") -> tuple[StudentScope | None, str]:
    """Rules and documents are read for the programme/batch the question names ("for B.Arch batch 2025"), unless the
    question is about the student's own records: personal tools always use the signed-in student's scope."""
    p = state.get("plan")
    if p is None or p.category not in PERSONAL:
        qs = question_scope(state["question"])
        if qs:
            return qs, "question"
    student = state.get("student")
    return _scope(student), ("student" if student else "none")


def _find_courses(question: str, courses: list[dict]) -> list[str]:
    """Courses named in the question by code ("CS201", "cs 201") or by full name."""
    q = question.lower()
    return [c["course_code"] for c in courses
            if re.search(rf"\b{re.escape(c['course_code'].lower())}\b|{re.escape(c['course_code'][:-3].lower())}\s+{c['course_code'][-3:]}\b", q)
            or c["course_name"].lower() in q]


def build_graph(svc: Services):
    s = svc.settings

    def timed(name, fn):
        def run(state: AskState) -> dict:
            t0 = time.perf_counter()
            out = dict(fn(state) or {})
            ms = int((time.perf_counter() - t0) * 1000)
            key = name if name not in (state.get("timings") or {}) else f"{name}_retry"
            out["timings"] = {key: ms}
            metrics.observe("node_latency_ms", ms, node=name)
            return out
        return run

    # ------------------------------------------------------------------ 1 guard
    def guard(state: AskState) -> dict:
        q = state["question"].strip()
        g = check_input(q)
        sid = (state.get("header_student_id") or "").strip().upper() or None
        with session() as con:
            student = one(con, "SELECT * FROM students WHERE student_id = ?", (sid,)) if sid else None
            others = rows(con, "SELECT student_id, full_name FROM students WHERE student_id != ?", (sid or "",))
        out: dict[str, Any] = {"student": student, "unknown_identity": bool(sid and not student), "q_redacted": g.redacted,
                               "guardrail": {"blocked": g.blocked, "reason": g.reason, "findings": g.findings}}
        if g.blocked:
            out["block"] = _refuse(*REFUSALS[g.reason])
            return out
        other_ids = {m.upper() for m in re.findall(r"\bS\d{4}\b", q, re.I)} - ({sid} if sid else set())
        if other_ids or any(_name_in(o["full_name"], q) for o in others):
            out["block"] = _refuse(*OTHER_STUDENT)
            out["guardrail"] = {"blocked": True, "reason": "other_student", "findings": g.findings + ["other_student_reference"]}
        return out

    # ------------------------------------------------------------------ 2 plan (LLM 1)
    def router_is_sure(router: Plan, state: AskState) -> bool:
        """Personal questions with a clear tool: the router's plan is final anyway (it overrides the LLM on personal
        intent and tools), so the planner call is skipped. Saves one LLM call, about 1-1.5 s."""
        if router.category not in PERSONAL or not router.tools or router.mentions_other_student:
            return False
        student = state.get("student")
        if not student or not any(TOOLS[t].needs_course for t in router.tools):
            return True
        with session() as con:
            courses = student_courses(ToolContext(con, student, state["as_of"]))
        return bool(_find_courses(state["question"], courses))

    def plan(state: AskState) -> dict:
        router = route(state["question"])
        whatif = attendance_whatif(state["question"])
        if s.planner == "router" or (s.planner == "auto" and (whatif or router_is_sure(router, state))):
            p, source, result, notes = router, "router" if s.planner == "router" else "router_first", None, []
        else:
            p, source, result, notes = plan_question(svc.llm, state["question"])
        if whatif:                       # a stated percentage is checked against the rules, not the student's records
            p.category, p.tools = "policy_fact", []
        out: dict[str, Any] = {"plan": p, "plan_source": source, "notes": notes, "llm_calls": [result] if result else [],
                               "llm_errors": ["unavailable"] if source == "router_fallback" else [], "whatif": whatif}
        if p.mentions_other_student:
            out["block"] = _refuse(*OTHER_STUDENT)
            out["guardrail"] = {**(state.get("guardrail") or {}), "blocked": True, "reason": "other_student"}
        return out

    # ------------------------------------------------------------------ 3 authorize
    def authorize(state: AskState) -> dict:
        p: Plan = state["plan"]
        student = state.get("student")
        if (p.category in PERSONAL or any(TOOLS[t].personal for t in p.tools)) and not student:
            if state.get("unknown_identity"):
                return {"block": _refuse(f"Student ID {state.get('header_student_id')} is not registered, so I can't look up personal records.",
                                         "Check the student ID and ask again, or ask a general question about the rules.")}
            return {"block": _refuse("Sign in to ask about your own records.",
                                     "Personal questions need your student ID. Choose it and ask again, or ask a general question about the rules.")}
        if not student:
            return {}
        needs_course = any(TOOLS[t].needs_course for t in p.tools)
        q = state["question"].lower()
        with session() as con:
            ctx = ToolContext(con, student, state["as_of"])
            courses = student_courses(ctx)
            found = _find_courses(state["question"], courses)
            for m in p.course_mentions:
                code, cands = resolve_course(ctx, m)
                if code:
                    found.append(code)
            found = list(dict.fromkeys(found))
        pass_codes = found if (p.category == "multi_step" and re.search(r"\bpass", q)) else []
        if needs_course:
            if len(found) == 1:
                return {"course_code": found[0], "pass_codes": pass_codes}
            opts = [c for c in courses if c["course_code"] in found] if found else \
                   [c for c in courses if c["semester"] == max((x["semester"] or 0) for x in courses)] or courses
            return {"block": {"type": "clarification_needed", "answer": "Which course do you mean?",
                              "explanation": "Your question could apply to more than one of your courses. Pick one and I'll check it.",
                              "options": [f"{c['course_code']} {c['course_name']}" for c in opts[:6]]}}
        return {"pass_codes": pass_codes}

    # ------------------------------------------------------------------ 4 execute_tools
    def execute_tools(state: AskState) -> dict:
        p: Plan = state["plan"]
        results = []
        with session() as con:
            ctx = ToolContext(con, state.get("student"), state["as_of"])
            pctx = ToolContext(con, state.get("student"), state["as_of"], scope_override=_policy_scope(state)[0])
            for t in p.tools:
                kwargs: dict[str, Any] = {}
                if TOOLS[t].needs_course:
                    kwargs["course_code"] = state.get("course_code")
                if t == "check_placement_eligibility":
                    kwargs["assume_cleared"] = state.get("pass_codes") or []
                if t == "attendance_projection":
                    kwargs["future_classes"] = p.future_classes or 10
                results.append(run_tool(t, ctx, **kwargs))
            if state.get("whatif"):
                results.append(run_tool("check_attendance_value", pctx, **state["whatif"]))
            consulted = {rr.parameter for r in results for rr in r.rules}
            for param in p.rule_parameters:
                if param not in consulted:
                    results.append(run_tool("get_rule", pctx, parameter=param))
        return {"tool_results": results, "rule_results": [rr for r in results for rr in r.rules]}

    # ------------------------------------------------------------------ 5 retrieve
    def do_retrieve(state: AskState) -> dict:
        anchors: list[tuple[str, str]] = []
        for rr in state.get("rule_results", []):
            res = rr.resolution
            for c in ([res.winner] if res.winner else []) + (res.tied if res.unresolved else []):
                anchors.append((c.doc_id, c.section))
        p: Plan = state["plan"]
        with session() as con:
            r = retrieve(con, svc, p.search_queries or [state["question"]], _policy_scope(state)[0], state["as_of"],
                         list(dict.fromkeys(anchors)), question=state["question"])
        return {"retrieval": r}

    def after_retrieve(state: AskState) -> str:
        decisive = any(t.decisive and t.status == "ok" for t in state.get("tool_results", []))
        r = state["retrieval"]
        grounded = bool(r.evidence) and (r.max_score >= s.tau or any(e.anchor for e in r.evidence))
        if grounded and not decisive and r.max_score < s.tau_confident:
            grounded = evidence_coverage(state["question"], r.evidence) >= s.min_coverage
        tie = any(rr.resolution.unresolved for rr in state.get("rule_results", []))
        return "compose" if (decisive or grounded or tie) else "finalize"

    # ------------------------------------------------------------------ 6 compose (LLM 2)
    def _verdict(state: AskState) -> str | None:
        v = [t.verdict for t in state.get("tool_results", []) if t.decisive and t.status == "ok" and t.verdict]
        return " ".join(v) or None

    def _rule_lines(state: AskState) -> list[str]:
        out = []
        for rr in state.get("rule_results", []):
            w = rr.winner
            scope = f" ({rr.scope_label})" if rr.scope_label else ""
            if w:
                out.append(f"{rr.parameter}{scope}: {rr.display_value()} — {rr.docs[w['source_doc_id']]['title']} "
                           f"({w['source_doc_id']} section {w['source_section']})")
            elif rr.resolution.unresolved:
                vals = [f"{c.doc_id} section {c.section} says {c.value}" for c in [rr.resolution.winner] + rr.resolution.tied]
                out.append(f"{rr.parameter}{scope}: unresolved tie — " + "; ".join(vals))
        return out

    def _rule_sentence(rr) -> str:
        """'Minimum attendance to sit end-semester exams: at least 80% (Circular: Revised Minimum Attendance
        Requirement, section 1).'"""
        w = rr.winner
        spec = PARAMETERS.get(rr.parameter)
        unit = "%" if (w.get("unit") or (spec.unit if spec else "")) == "pct" else ""
        words = {">=": "at least", "<=": "at most", ">": "more than", "<": "less than", "in": "one of"}.get(w["operator"], "")
        return (f"{spec.label if spec else rr.parameter}: {words} {w['value'].replace(';', ' or ')}{unit} "
                f"({rr.docs[w['source_doc_id']]['title']}, section {w['source_section']}).").replace(":  ", ": ")

    def _rule_sentences(state: AskState) -> str:
        return " ".join(dict.fromkeys(_rule_sentence(rr) for rr in state.get("rule_results", []) if rr.winner))

    def _notes(state: AskState) -> list[str]:
        notes = [rr.precedence_note() for rr in state.get("rule_results", []) if rr.resolution.winner]
        r = state.get("retrieval")
        return list(dict.fromkeys(notes + (r.notes if r else [])))

    def _upcoming(state: AskState) -> list[SourceRef]:
        out: dict[str, SourceRef] = {}
        for rr in state.get("rule_results", []):
            for c in rr.resolution.upcoming:
                out.setdefault(c.doc_id, SourceRef(doc_id=c.doc_id, section=c.section, value=c.value, title=c.title,
                                                   effective_from=c.effective_from))
        r = state.get("retrieval")
        for u in (r.upcoming if r else []):
            out.setdefault(u.doc_id, u)
        return list(out.values())

    def compose(state: AskState) -> dict:
        r: RetrievalResult = state["retrieval"]
        evidence, set_aside, report = build_context(state["question"], r.evidence, s.context_budget_tokens,
                                                    s.compress_context, r.expansion)
        system, user = build_prompt(state["question"], _verdict(state), facts_from_tools(state.get("tool_results", [])),
                                    _rule_lines(state), _notes(state) + set_aside,
                                    [f"{u.title or u.doc_id} ({u.doc_id}) takes effect on {u.effective_from}" for u in _upcoming(state)],
                                    evidence, state.get("feedback"))
        ctx = {"evidence": [vars(e) for e in evidence], "verdict": _verdict(state), "question": state["question"]}
        out: dict[str, Any] = {"prompt_ids": [e.eid for e in evidence], "context_report": report.as_dict()}
        try:
            res = svc.llm.chat_json("compose", system, user, ComposerOutput.model_json_schema(), context=ctx)
            return {**out, "draft": ComposerOutput.model_validate(res.data), "llm_calls": [res]}
        except LLMError as e:
            return {**out, "draft": None, "notes": [f"compose failed ({e.kind}): {str(e)[:160]}"], "llm_errors": [e.kind]}
        except ValidationError as e:
            return {**out, "draft": None, "notes": [f"compose output invalid: {str(e)[:160]}"]}

    # ------------------------------------------------------------------ 7 verify
    def verify(state: AskState) -> dict:
        r: RetrievalResult = state["retrieval"]
        draft = state.get("draft")
        verdict = _verdict(state)
        retries = state.get("retries", 0)
        shown = set(state.get("prompt_ids") or [e.eid for e in r.evidence])
        shown_ev = [e for e in r.evidence if e.eid in shown]
        notes = []
        if draft is not None and not draft.insufficient_evidence and not [i for i in draft.evidence_ids if i in shown]:
            inferred = infer_citations(f"{draft.answer} {draft.explanation}", shown_ev)
            if inferred:
                draft.evidence_ids = inferred
                notes.append(f"evidence_ids inferred by overlap: {inferred}")
        score, unsupported = None, []
        if draft is None:
            problems = ["the composer returned no valid JSON"]
        else:
            sources = sources_for_grounding(state["question"], verdict, state.get("tool_results", []), _rule_lines(state), shown_ev)
            p: Plan | None = state.get("plan")
            problems = check_draft(draft, evidence_ids=shown, allowed_sources=sources, verdict=verdict,
                                   self_id=(state.get("student") or {}).get("student_id"), needs_citation=verdict is None,
                                   personal=bool(p and p.category in PERSONAL))
            if not draft.insufficient_evidence:
                score, unsupported = groundedness(f"{draft.answer} {draft.explanation}",
                                                  sources + [e.title for e in shown_ev] + _notes(state))
                if verdict is None and score < s.min_groundedness:
                    problems.append("most of the answer is not supported by the evidence; use only what the evidence says. "
                                    "Unsupported: " + " | ".join(u[:90] for u in unsupported[:2]))
        unavailable = any(k in AVAILABILITY for k in state.get("llm_errors", []))
        if problems and retries < 1 and not unavailable:
            return {"retry": True, "retries": retries + 1, "feedback": problems, "notes": notes}
        return {"retry": False, "notes": notes, "groundedness": score, "unsupported": unsupported,
                "verification": {"citations_valid": not any("evidence" in p or "cite" in p for p in problems),
                                 "numbers_grounded": not any("numbers" in p for p in problems),
                                 "groundedness": score, "retries": retries, "fallback": bool(problems), "problems": problems}}

    # ------------------------------------------------------------------ 8 finalize
    def finalize(state: AskState) -> dict:
        p: Plan | None = state.get("plan")
        r: RetrievalResult | None = state.get("retrieval")
        evidence: list[Evidence] = r.evidence if r else []
        by_id = {e.eid: e for e in evidence}
        tool_results = state.get("tool_results", [])
        rule_results = state.get("rule_results", [])
        draft = state.get("draft")
        ver = state.get("verification") or {}
        fallback = bool(ver.get("fallback")) or (draft is None and r is not None and state.get("timings", {}).get("compose") is not None)
        block = state.get("block")
        verdict = _verdict(state)
        decisive = any(t.decisive and t.status == "ok" for t in tool_results)
        conflicts: list[ConflictRecord] = []
        seen_topics = set()
        for rr in rule_results:
            c = rr.conflict()
            if c and (c.topic, rr.scope_label) not in seen_topics:
                seen_topics.add((c.topic, rr.scope_label))
                conflicts.append(c)
        rule_tie = [rr for rr in rule_results if rr.resolution.unresolved]

        # text-level disagreements reported by the composer are resolved by code with Annex A
        text_tie: list[tuple[Evidence, Evidence]] = []
        if draft and not fallback:
            for pair in draft.disagreeing_pairs:
                if len(pair) != 2 or pair[0] not in by_id or pair[1] not in by_id:
                    continue
                a, b = by_id[pair[0]], by_id[pair[1]]
                if a.doc_id == b.doc_id:
                    continue
                ka, kb = (a.authority, -date.fromisoformat(a.effective_from).toordinal()), (b.authority, -date.fromisoformat(b.effective_from).toordinal())
                if ka == kb:
                    text_tie.append((a, b))
                else:
                    win, lose = (a, b) if ka < kb else (b, a)
                    step = "step3_authority" if win.authority != lose.authority else "step4_recency"
                    conflicts.append(ConflictRecord(topic="text", winner=SourceRef(doc_id=win.doc_id, section=win.section, title=win.title),
                                                    others=[SourceRef(doc_id=lose.doc_id, section=lose.section, title=lose.title,
                                                                      reason="lower authority" if step == "step3_authority" else "older at the same authority")],
                                                    resolved_by=step))

        cited_text = " ".join(by_id[i].text for i in (draft.evidence_ids if draft else []) if i in by_id)
        if r and r.ties and not decisive:          # equal authority, same date, different amounts: step 5, unresolved
            for x, y in r.ties:
                if x in by_id and y in by_id and (by_id[x], by_id[y]) not in text_tie:
                    text_tie.append((by_id[x], by_id[y]))
        if draft and not fallback and not decisive and not draft.insufficient_evidence and says_not_covered(draft.answer, cited_text):
            draft.insufficient_evidence = True          # "there is no scholarship mentioned …" is not a retrieved fact

        cite_ids: list[str] = []
        options: list[str] = []
        if block:
            answer_type, answer, explanation, options = block["type"], block["answer"], block["explanation"], block.get("options", [])
        elif rule_tie or text_tie:
            answer_type = "conflict_flagged"
            if rule_tie:
                rr = rule_tie[0]
                srcs = [rr.resolution.winner] + rr.resolution.tied
                said = " and ".join(f"{rr.docs[c.doc_id]['title']} (section {c.section}) says {c.value}" for c in srcs)
                issuer = rr.docs[srcs[0].doc_id]["issuer"]
                anchors = {(c.doc_id, c.section) for c in srcs}
                cite_ids = [e.eid for e in evidence if (e.doc_id, e.section) in anchors]
            else:
                a, b = text_tie[0]
                va, vb = amounts(a.text), amounts(b.text)
                if va and vb:
                    said = (f"{a.title} (section {a.section}) says Rs {', '.join(sorted(va))} and {b.title} (section {b.section}) "
                            f"says Rs {', '.join(sorted(vb))}")
                else:
                    said = f"{a.title} (section {a.section}) and {b.title} (section {b.section}) say different things"
                issuer = a.issuer if a.issuer == b.issuer else (f"{a.issuer} or {b.issuer}" if a.issuer and b.issuer else "the issuing office")
                cite_ids = [a.eid, b.eid]
                conflicts.append(ConflictRecord(topic="text", winner=None, resolved_by=None,
                                                others=[SourceRef(doc_id=e.doc_id, section=e.section, title=e.title,
                                                                  value=", ".join(sorted(amounts(e.text))) or None) for e in (a, b)]))
            answer = f"The sources disagree: {said}. Both have the same authority and date, so please confirm with {issuer}."
            explanation = (strip_markers(draft.explanation) if draft and not fallback else
                           "Annex A cannot break this tie, so both sources are cited.")
        elif decisive:                   # calculated = from the student's records; a policy what-if is a retrieved fact
            personal = any(t.decisive and t.status == "ok" and TOOLS[t.tool].personal for t in tool_results)
            answer_type, answer = ("calculated" if personal else "retrieved_fact"), verdict or ""
            anchors = {(w["source_doc_id"], w["source_section"]) for rr in rule_results if (w := rr.winner)}
            cite_ids = [e.eid for e in evidence if (e.doc_id, e.section) in anchors]
            if draft and not fallback:
                explanation = strip_markers(draft.explanation)
                cite_ids += [i for i in draft.evidence_ids if i in by_id]
            else:
                explanation = _rule_sentences(state) or "Computed from your records."
        elif r and evidence and draft and not fallback and not draft.insufficient_evidence:
            answer_type, answer, explanation = "retrieved_fact", strip_markers(draft.answer), strip_markers(draft.explanation)
            cite_ids = [i for i in draft.evidence_ids if i in by_id]
            ql = state["question"].lower()
            asks_threshold = bool(THRESHOLD_Q.search(ql))
            for rr in rule_results:                  # the question asks for this threshold but the draft never states it
                spec = PARAMETERS.get(rr.parameter)
                if (asks_threshold and rr.winner and spec and any(k in ql for k in spec.keywords)
                        and not numbers(rr.display_value(rr.winner)) <= numbers(f"{answer} {explanation}")):
                    explanation += " Rule in force: " + _rule_sentence(rr)
            stated = numbers(f"{answer} {explanation}")
            for rr in rule_results:                  # an answer that states a rule's value cites the clause that sets it
                if (w := rr.winner) and numbers(str(rr.display_value(w))) & stated:
                    cite_ids += [e.eid for e in evidence if (e.doc_id, e.section) == (w["source_doc_id"], w["source_section"])]
            if draft.unanswered_parts:
                explanation += " Not covered by the authorised sources: " + "; ".join(draft.unanswered_parts) + "."
        elif r and evidence and fallback and (r.max_score >= s.tau or any(e.anchor for e in evidence)):
            usable = [e for e in evidence if e.label not in ("INFORMATIONAL", "LOWER_PRECEDENCE")]
            cited = [by_id[i] for i in (draft.evidence_ids if draft else []) if i in by_id and by_id[i] in usable]
            top = cited[0] if cited else (max(usable, key=lambda e: e.score if e.score is not None else -1.0) if usable else None)
            if top:
                body = re.sub(r"^\s*(?:\d{1,2}(?:\.\d{1,2})*[.)]?\s+)?(?:[A-Z][\w ,'()-]{0,60}?\.\s+)?", "", redact(top.text).strip(), count=1)
                sents = re.split(r"(?<=[.!?])\s+", body or top.text)
                answer_type, answer = "retrieved_fact", " ".join(sents[:2])
                explanation = f"Quoted from {top.title}" + (f", section {top.section}." if top.section else ".")
                cite_ids = [top.eid]
            else:
                answer_type, answer, explanation = "not_found", NOT_FOUND, "No authorised source in force covers this question."
        else:
            answer_type, answer = "not_found", NOT_FOUND
            explanation = ("No document is in force for this date and programme." if r and r.no_applicable_docs
                           else f"No authorised document in force on {state['as_of'].isoformat()} answers this question.")

        ups = _upcoming(state) if answer_type in ("retrieved_fact", "calculated", "conflict_flagged") else []
        if ups and answer_type != "conflict_flagged" and (fallback or not draft):
            explanation += " Upcoming change: " + "; ".join(f"{u.title or u.doc_id} takes effect on {u.effective_from}" for u in ups) + "."

        # output guardrail: no other student's ID, no prompt text, no contact details the sources don't contain
        output_findings: list[str] = []
        if not block:
            self_id = (state.get("student") or {}).get("student_id")
            source_text = " ".join(e.text for e in evidence) + " " + json.dumps([t.output for t in tool_results], default=str)
            answer, f1 = check_output(answer, self_id, source_text)
            explanation, f2 = check_output(explanation, self_id, source_text)
            output_findings = sorted(set(f1 + f2))

        citations: list[Citation] = []
        cited_refs: set[tuple] = set()
        for eid in dict.fromkeys(cite_ids):
            e = by_id.get(eid)
            if not e or e.label in ("INFORMATIONAL",) or (e.label == "LOWER_PRECEDENCE" and answer_type != "conflict_flagged"):
                continue
            ref = (e.doc_id, e.section or f"p{e.page}")
            if ref in cited_refs:                # two chunks of one clause (text + table) are one citation
                continue
            cited_refs.add(ref)
            quote = redact(e.text) if e.flagged else e.text
            citations.append(Citation(doc_id=e.doc_id, title=e.title, section=e.section, page=e.page, version=e.version,
                                      effective_from=date.fromisoformat(e.effective_from),
                                      quote=(quote[:320].rsplit(" ", 1)[0] + "…") if len(quote) > 320 else quote))
        applied: dict[str, AppliedRule] = {}
        for rr in rule_results:
            if (w := rr.winner):
                applied.setdefault(w["rule_id"], AppliedRule(rule_id=w["rule_id"], value=rr.display_value(w),
                                                             source_doc_id=w["source_doc_id"], source_section=w["source_section"],
                                                             parameter=rr.parameter))
        if answer_type in ("refused", "clarification_needed"):
            conflicts, applied = [], {}

        calls = state.get("llm_calls", [])
        live = [c for c in calls if not c.cached]
        prompt_toks = sum(c.prompt_tokens for c in live)
        completion_toks = sum(c.completion_tokens for c in live)
        timings = dict(state.get("timings") or {})
        total_ms = int((time.perf_counter() - state["started"]) * 1000)
        g = state.get("guardrail") or {}
        degraded = any(k in AVAILABILITY for k in state.get("llm_errors", [])) or state.get("plan_source") == "router_fallback"
        report = state.get("context_report") or {}
        pscope, scope_src = _policy_scope(state)
        scope_label = (" ".join(x for x in (pscope.programme, str(pscope.batch_year) if pscope.batch_year else None) if x)
                       + f" (from the {scope_src})") if pscope else None
        retrieval_mode = (s.retrieval_mode + ("+rerank" if s.reranker.lower() != "none" else "")) if r else None
        meta = AskMeta(latency_ms=total_ms, guardrail=g.get("reason") if g.get("blocked") else None,
                       output_redactions=output_findings, groundedness=state.get("groundedness"),
                       session_id=state.get("session_id"),
                       standalone_question=state["question"] if state.get("rewrite") else None, rewrite=state.get("rewrite"),
                       degraded=degraded, planner=state.get("plan_source"), retrieval=retrieval_mode,
                       llm_calls=len(live), tokens=prompt_toks + completion_toks, tokens_saved=report.get("tokens_saved", 0),
                       as_of_source=state.get("as_of_source"), scope=scope_label)
        response = AskResponse(
            trace_id=state["trace_id"], answer=answer, answer_type=answer_type, citations=citations,
            tools_invoked=[ToolInvocation(tool=t.tool, input=t.input, output=t.output, status=t.status, ms=t.ms) for t in tool_results],
            applied_rules=list(applied.values()), conflicts_detected=conflicts, explanation=explanation,
            as_of_date=state["as_of"], upcoming_changes=ups, clarification_options=options,
            student_id=(state.get("student") or {}).get("student_id"), meta=meta)

        record = {
            "trace_id": state["trace_id"], "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "student_id": response.student_id, "question": state.get("q_redacted") or state["question"],
            "question_category": p.category if p else None, "as_of_date": state["as_of"].isoformat(),
            "sources_retrieved": r.sources_retrieved if r else [],
            "precedence_decision": "; ".join(_notes(state)) or "no conflicting sources",
            "tools_invoked": [t.model_dump() for t in response.tools_invoked],
            "applied_rules": [a.model_dump() for a in response.applied_rules],
            "conflicts_detected": [c.model_dump(mode="json") for c in conflicts],
            "answer_type": answer_type, "answer": answer, "explanation": explanation,
            "citations": [c.model_dump(mode="json") for c in citations],
            "model": ", ".join(sorted({c.model for c in calls})) or None, "llm_calls": len(live),
            "llm_cache_hits": len(calls) - len(live),
            "tokens": prompt_toks + completion_toks, "latency_ms": total_ms,
            "plan": {"source": state.get("plan_source"), "category": p.category if p else None,
                     "tools": list(p.tools) if p else [], "rule_parameters": list(p.rule_parameters) if p else [],
                     "search_queries": list(p.search_queries) if p else []},
            "retrieval": {"mode": retrieval_mode, "queries": r.queries, "glossary_expansion": r.expansion,
                          "max_score": round(r.max_score, 3)} if r else None,
            "context_optimisation": report or None,
            "verification": ver or {"citations_valid": True, "numbers_grounded": True, "retries": 0, "fallback": False},
            "groundedness": state.get("groundedness"), "unsupported_sentences": state.get("unsupported", []),
            "guardrails": {"input": g, "output": output_findings},
            "session": {"id": state.get("session_id"), "rewrite": state.get("rewrite"),
                        "original_question": redact_pii(state["original_question"])[0] if state.get("rewrite") else None},
            "cache": {"hit": "miss"}, "degraded": degraded,
            "scope": {"source": scope_src, "programme": pscope.programme if pscope else None,
                      "batch_year": pscope.batch_year if pscope else None},
            "as_of_source": state.get("as_of_source"),
            "token_breakdown": {"prompt": prompt_toks, "completion": completion_toks},
            "latency_breakdown_ms": timings, "notes": state.get("notes", []),
            "config": {"embedder": s.embed_model, "chunker": s.chunker, "top_k": s.top_k, "tau": s.tau,
                       "retrieval_mode": s.retrieval_mode, "reranker": s.reranker, "planner": s.planner,
                       "context_budget_tokens": s.context_budget_tokens,
                       "llm_provider": s.llm_provider, "llm_model": s.llm_model},
        }
        audit.write(record)
        return {"response": response}

    g = StateGraph(AskState)
    for name, fn in [("guard", guard), ("plan", plan), ("authorize", authorize), ("execute_tools", execute_tools),
                     ("retrieve", do_retrieve), ("compose", compose), ("verify", verify), ("finalize", finalize)]:
        g.add_node(name, timed(name, fn))
    g.add_edge(START, "guard")
    g.add_conditional_edges("guard", lambda st: "finalize" if st.get("block") else "plan", {"plan": "plan", "finalize": "finalize"})
    g.add_conditional_edges("plan", lambda st: "finalize" if st.get("block") else "authorize", {"authorize": "authorize", "finalize": "finalize"})
    g.add_conditional_edges("authorize", lambda st: "finalize" if st.get("block") else "execute_tools",
                            {"execute_tools": "execute_tools", "finalize": "finalize"})
    g.add_edge("execute_tools", "retrieve")
    g.add_conditional_edges("retrieve", after_retrieve, {"compose": "compose", "finalize": "finalize"})
    g.add_edge("compose", "verify")
    g.add_conditional_edges("verify", lambda st: "compose" if st.get("retry") else "finalize", {"compose": "compose", "finalize": "finalize"})
    g.add_edge("finalize", END)
    return g.compile()


_graph = None


def _student(sid: str | None) -> dict | None:
    if not sid:
        return None
    with session() as con:
        return one(con, "SELECT * FROM students WHERE student_id = ?", (sid,))


def _courses(student: dict | None, as_of: date) -> list[dict]:
    with session() as con:
        if student:
            return student_courses(ToolContext(con, student, as_of))
        return rows(con, "SELECT * FROM courses")


def _serve_cached(entry: dict, kind: str, trace_id: str, standalone: str, rewrite: str | None, session_id: str | None,
                  student: dict | None, started: float, similarity: float | None = None) -> AskResponse:
    resp: AskResponse = entry["response"].model_copy(deep=True)
    latency = int((time.perf_counter() - started) * 1000)
    resp.trace_id = trace_id
    resp.student_id = student["student_id"] if student else None
    resp.meta = resp.meta.model_copy(update={"cache_hit": True, "cache": kind, "latency_ms": latency, "session_id": session_id,
                                             "standalone_question": standalone if rewrite else None, "rewrite": rewrite,
                                             "llm_calls": 0, "tokens": 0, "tokens_saved": 0, "degraded": False})
    rec = audit.get(entry["trace_id"]) or {}
    rec.update({"trace_id": trace_id, "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "student_id": resp.student_id, "question": redact_pii(standalone)[0], "latency_ms": latency,
                "llm_calls": 0, "llm_cache_hits": 0, "tokens": 0, "token_breakdown": {"prompt": 0, "completion": 0},
                "latency_breakdown_ms": {"cache": latency},
                "cache": {"hit": kind, "source_trace_id": entry["trace_id"],
                          **({"similarity": round(similarity, 4)} if similarity is not None else {})},
                "session": {"id": session_id, "rewrite": rewrite}})
    audit.write(rec)
    return resp


def ask(svc: Services, question: str, student_id: str | None, as_of: date | None, session_id: str | None = None) -> AskResponse:
    global _graph
    if _graph is None:
        _graph = build_graph(svc)
    s = svc.settings
    started = time.perf_counter()
    if as_of is not None:
        as_of_source = "request"
    elif (qd := question_date(question)) is not None:
        as_of, as_of_source = qd, "question"
    else:
        as_of, as_of_source = datetime.now(ZoneInfo(s.timezone)).date(), "today"
    sid = (student_id or "").strip().upper() or None
    student = _student(sid)
    trace_id = uuid.uuid4().hex[:8]
    question = question.strip()

    # 1. follow-ups become standalone questions (only for questions that pass the input guardrail)
    standalone, rewrite, condense_call = question, None, None
    if session_id and not check_input(question).blocked:
        history = sessions.history(session_id, sid)
        if history:
            standalone, rewrite, condense_call = contextualize(question, history, _courses(student, as_of), svc.llm)

    # 2. answer caches: exact (normalised question + student) and semantic (general questions, same scope)
    fingerprint = (s.embed_model, s.chunker, s.retrieval_mode, s.reranker, s.planner, s.llm_model, s.tau, s.top_k)
    version = data_version()
    exact_key = cache.key(cache.normalise(standalone), sid or "", as_of.isoformat(), version, *fingerprint)
    sem_vec, sem_scope, sim = None, None, None
    entry, kind = (cache.answers.get(exact_key), "exact") if s.answer_cache else (None, None)
    if entry is None and s.semantic_cache and not check_input(standalone).blocked:
        rp = route(standalone)
        if rp.category not in PERSONAL and not rp.tools and not rp.mentions_other_student:
            scope = f"{student['programme']}|{student['batch_year']}" if student else ("unknown" if sid else "anon")
            sem_scope = cache.key(as_of.isoformat(), version, scope, *fingerprint)
            sem_vec = svc.embedder.embed_queries([standalone])[0]
            got, sim = cache.semantic.get(sem_vec, sem_scope, s.semantic_cache_threshold)
            if got is not None and got["canonical"] == canonical(standalone):
                entry, kind = got, "semantic"
    if entry is not None:
        resp = _serve_cached(entry, kind, trace_id, standalone, rewrite, session_id, student, started,
                             sim if kind == "semantic" else None)
        metrics.inc("ask_requests", answer_type=resp.answer_type, cache=kind)
        metrics.observe("ask_latency_ms", resp.meta.latency_ms, cache=kind)
    else:
        state: AskState = {"trace_id": trace_id, "question": standalone, "original_question": question if rewrite else None,
                           "rewrite": rewrite, "session_id": session_id, "header_student_id": student_id, "as_of": as_of,
                           "as_of_source": as_of_source,
                           "started": started, "retries": 0, "llm_calls": [condense_call] if condense_call else []}
        out = _graph.invoke(state)
        resp = out["response"]
        m = resp.meta
        if (resp.answer_type in CACHEABLE and not m.degraded and not m.guardrail and not m.output_redactions
                and not (out.get("verification") or {}).get("fallback")):
            stored = {"response": resp.model_copy(deep=True), "trace_id": resp.trace_id, "canonical": canonical(standalone)}
            if s.answer_cache:
                cache.answers.set(exact_key, stored)
            if sem_vec is not None and not any(TOOLS[t.tool].personal for t in resp.tools_invoked):
                cache.semantic.set(sem_vec, sem_scope, stored)          # general answers only (get_rule is not personal)
        metrics.inc("ask_requests", answer_type=resp.answer_type, cache="miss")
        metrics.observe("ask_latency_ms", m.latency_ms, cache="miss")
        if m.degraded:
            metrics.inc("degraded_answers")
        if m.groundedness is not None:                           # mean = groundedness_sum / groundedness_count
            metrics.inc("groundedness_sum", m.groundedness)
            metrics.inc("groundedness_count")

    # 3. remember the turn for follow-ups (never a blocked one)
    if session_id and not resp.meta.guardrail:
        course = next((t.input.get("course_code") for t in resp.tools_invoked if t.input.get("course_code")), None)
        sessions.add(session_id, sid, Turn(standalone, resp.answer_type, course))
    return resp


def reset_graph() -> None:  # tests
    global _graph
    _graph = None
