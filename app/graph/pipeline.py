"""The /ask pipeline as a LangGraph state machine.

guard -> plan (LLM 1) -> authorize -> execute_tools -> retrieve -> compose (LLM 2) -> verify -> finalize
Early exits go straight to finalize with a templated answer and no further LLM call.
"""
from __future__ import annotations

import operator
import re
import time
import uuid
from datetime import date, datetime, timezone
from typing import Annotated, Any, TypedDict
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from app import audit
from app.db import one, rows, session
from app.graph.verify import check_draft, evidence_coverage, infer_citations, sources_for_grounding, strip_markers
from app.ingestion.injection import redact
from app.llm.client import LLMError
from app.llm.composer import ComposerOutput, build_prompt, facts_from_tools
from app.llm.planner import PERSONAL, Plan, plan_question
from app.models import AppliedRule, AskResponse, Citation, ConflictRecord, SourceRef, ToolInvocation
from app.policy.precedence import StudentScope
from app.retrieval.retriever import Evidence, RetrievalResult, retrieve
from app.services import Services
from app.tools.core import TOOLS, ToolContext, resolve_course, run_tool, student_courses

NOT_FOUND = "I could not find this information in the authorised university sources."


def _merge(a: dict | None, b: dict | None) -> dict:
    return {**(a or {}), **(b or {})}


class AskState(TypedDict, total=False):
    trace_id: str
    question: str
    header_student_id: str | None
    as_of: date
    started: float
    student: dict | None
    unknown_identity: bool
    plan: Plan | None
    plan_source: str
    block: dict | None
    course_code: str | None
    pass_codes: list[str]
    tool_results: Annotated[list, operator.add]
    rule_results: Annotated[list, operator.add]
    retrieval: RetrievalResult | None
    draft: ComposerOutput | None
    feedback: list[str]
    retries: int
    retry: bool
    verification: dict
    llm_calls: Annotated[list, operator.add]
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


def build_graph(svc: Services):
    s = svc.settings

    def timed(name, fn):
        def run(state: AskState) -> dict:
            t0 = time.perf_counter()
            out = dict(fn(state) or {})
            key = name if name not in (state.get("timings") or {}) else f"{name}_retry"
            out["timings"] = {key: int((time.perf_counter() - t0) * 1000)}
            return out
        return run

    # ------------------------------------------------------------------ 1 guard
    def guard(state: AskState) -> dict:
        q = state["question"].strip()
        sid = (state.get("header_student_id") or "").strip().upper() or None
        with session() as con:
            student = one(con, "SELECT * FROM students WHERE student_id = ?", (sid,)) if sid else None
            others = rows(con, "SELECT student_id, full_name FROM students WHERE student_id != ?", (sid or "",))
        out: dict[str, Any] = {"student": student, "unknown_identity": bool(sid and not student)}
        other_ids = {m.upper() for m in re.findall(r"\bS\d{4}\b", q, re.I)} - ({sid} if sid else set())
        if other_ids or any(_name_in(o["full_name"], q) for o in others):
            out["block"] = _refuse("I can only share your own records, so I can't answer questions about another student.",
                                   "Personal records are available only to the student who is signed in.")
        return out

    # ------------------------------------------------------------------ 2 plan (LLM 1)
    def plan(state: AskState) -> dict:
        p, source, result, notes = plan_question(svc.llm, state["question"])
        out: dict[str, Any] = {"plan": p, "plan_source": source, "notes": notes, "llm_calls": [result] if result else []}
        if p.mentions_other_student:
            out["block"] = _refuse("I can only share your own records, so I can't answer questions about another student.",
                                   "Personal records are available only to the student who is signed in.")
        return out

    # ------------------------------------------------------------------ 3 authorize
    def authorize(state: AskState) -> dict:
        p: Plan = state["plan"]
        student = state.get("student")
        if (p.category in PERSONAL or p.tools) and not student:
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
            found = [c["course_code"] for c in courses
                     if re.search(rf"\b{re.escape(c['course_code'].lower())}\b|{re.escape(c['course_code'][:-3].lower())}\s+{c['course_code'][-3:]}\b", q)
                     or c["course_name"].lower() in q]
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
            for t in p.tools:
                kwargs: dict[str, Any] = {}
                if TOOLS[t].needs_course:
                    kwargs["course_code"] = state.get("course_code")
                if t == "check_placement_eligibility":
                    kwargs["assume_cleared"] = state.get("pass_codes") or []
                if t == "attendance_projection":
                    kwargs["future_classes"] = p.future_classes or 10
                results.append(run_tool(t, ctx, **kwargs))
            consulted = {rr.parameter for r in results for rr in r.rules}
            for param in p.rule_parameters:
                if param not in consulted:
                    results.append(run_tool("get_rule", ctx, parameter=param))
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
            r = retrieve(con, svc, p.search_queries or [state["question"]], _scope(state.get("student")), state["as_of"],
                         list(dict.fromkeys(anchors)))
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
                           f"({w['source_doc_id']} §{w['source_section']})")
            elif rr.resolution.unresolved:
                vals = [f"{c.doc_id} §{c.section} says {c.value}" for c in [rr.resolution.winner] + rr.resolution.tied]
                out.append(f"{rr.parameter}{scope}: unresolved tie — " + "; ".join(vals))
        return out

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
        system, user = build_prompt(state["question"], _verdict(state), facts_from_tools(state.get("tool_results", [])),
                                    _rule_lines(state), _notes(state),
                                    [f"{u.title or u.doc_id} ({u.doc_id}) takes effect on {u.effective_from}" for u in _upcoming(state)],
                                    r.evidence, state.get("feedback"))
        ctx = {"evidence": [vars(e) for e in r.evidence], "verdict": _verdict(state), "question": state["question"]}
        try:
            res = svc.llm.chat_json("compose", system, user, ComposerOutput.model_json_schema(), context=ctx)
            return {"draft": ComposerOutput.model_validate(res.data), "llm_calls": [res]}
        except (LLMError, ValidationError) as e:
            return {"draft": None, "notes": [f"compose failed: {str(e)[:160]}"]}

    # ------------------------------------------------------------------ 7 verify
    def verify(state: AskState) -> dict:
        r: RetrievalResult = state["retrieval"]
        draft = state.get("draft")
        verdict = _verdict(state)
        retries = state.get("retries", 0)
        notes = []
        if draft is not None and not draft.insufficient_evidence and not [i for i in draft.evidence_ids if i in {e.eid for e in r.evidence}]:
            inferred = infer_citations(f"{draft.answer} {draft.explanation}", r.evidence)
            if inferred:
                draft.evidence_ids = inferred
                notes.append(f"evidence_ids inferred by overlap: {inferred}")
        if draft is None:
            problems = ["the composer returned no valid JSON"]
        else:
            problems = check_draft(draft, evidence_ids={e.eid for e in r.evidence},
                                   allowed_sources=sources_for_grounding(state["question"], verdict, state.get("tool_results", []),
                                                                         _rule_lines(state), r.evidence),
                                   verdict=verdict, self_id=(state.get("student") or {}).get("student_id"),
                                   needs_citation=verdict is None)
        if problems and retries < 1:
            return {"retry": True, "retries": retries + 1, "feedback": problems, "notes": notes}
        return {"retry": False, "notes": notes, "verification": {"citations_valid": not any("evidence" in p or "cite" in p for p in problems),
                                                  "numbers_grounded": not any("numbers" in p for p in problems),
                                                  "retries": retries, "fallback": bool(problems), "problems": problems}}

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

        cite_ids: list[str] = []
        options: list[str] = []
        if block:
            answer_type, answer, explanation, options = block["type"], block["answer"], block["explanation"], block.get("options", [])
        elif rule_tie or text_tie:
            answer_type = "conflict_flagged"
            if rule_tie:
                rr = rule_tie[0]
                srcs = [rr.resolution.winner] + rr.resolution.tied
                said = " and ".join(f"{rr.docs[c.doc_id]['title']} (§{c.section}) says {c.value}" for c in srcs)
                issuer = rr.docs[srcs[0].doc_id]["issuer"]
                anchors = {(c.doc_id, c.section) for c in srcs}
                cite_ids = [e.eid for e in evidence if (e.doc_id, e.section) in anchors]
            else:
                a, b = text_tie[0]
                said = f"{a.title} (§{a.section}) and {b.title} (§{b.section}) say different things"
                issuer = "the issuing office"
                cite_ids = [a.eid, b.eid]
            answer = f"The sources disagree: {said}. Both have the same authority and date, so please confirm with {issuer}."
            explanation = (strip_markers(draft.explanation) if draft and not fallback else
                           "Annex A cannot break this tie, so both sources are cited.")
        elif decisive:
            answer_type, answer = "calculated", verdict or ""
            anchors = {(w["source_doc_id"], w["source_section"]) for rr in rule_results if (w := rr.winner)}
            cite_ids = [e.eid for e in evidence if (e.doc_id, e.section) in anchors]
            if draft and not fallback:
                explanation = strip_markers(draft.explanation)
                cite_ids += [i for i in draft.evidence_ids if i in by_id]
            else:
                explanation = " ".join(_rule_lines(state)) or "Computed from your records."
        elif r and evidence and draft and not fallback and not draft.insufficient_evidence:
            answer_type, answer, explanation = "retrieved_fact", strip_markers(draft.answer), strip_markers(draft.explanation)
            cite_ids = [i for i in draft.evidence_ids if i in by_id]
            if draft.unanswered_parts:
                explanation += " Not covered by the authorised sources: " + "; ".join(draft.unanswered_parts) + "."
        elif r and evidence and fallback and (r.max_score >= s.tau or any(e.anchor for e in evidence)):
            top = next((e for e in evidence if e.label not in ("INFORMATIONAL", "LOWER_PRECEDENCE")), None)
            if top:
                body = re.sub(r"^\s*(?:\d{1,2}(?:\.\d{1,2})*[.)]?\s+)?(?:[A-Z][\w ,'()-]{0,60}?\.\s+)?", "", redact(top.text).strip(), count=1)
                sents = re.split(r"(?<=[.!?])\s+", body or top.text)
                answer_type, answer = "retrieved_fact", " ".join(sents[:2])
                explanation = f"Quoted from {top.title}" + (f", §{top.section}." if top.section else ".")
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

        citations: list[Citation] = []
        for eid in dict.fromkeys(cite_ids):
            e = by_id.get(eid)
            if not e or e.label in ("INFORMATIONAL",) or (e.label == "LOWER_PRECEDENCE" and answer_type != "conflict_flagged"):
                continue
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

        response = AskResponse(
            trace_id=state["trace_id"], answer=answer, answer_type=answer_type, citations=citations,
            tools_invoked=[ToolInvocation(tool=t.tool, input=t.input, output=t.output, status=t.status, ms=t.ms) for t in tool_results],
            applied_rules=list(applied.values()), conflicts_detected=conflicts, explanation=explanation,
            as_of_date=state["as_of"], upcoming_changes=ups, clarification_options=options,
            student_id=(state.get("student") or {}).get("student_id"))

        calls = state.get("llm_calls", [])
        prompt_toks = sum(c.prompt_tokens for c in calls)
        completion_toks = sum(c.completion_tokens for c in calls)
        timings = dict(state.get("timings") or {})
        total_ms = int((time.perf_counter() - state["started"]) * 1000)
        record = {
            "trace_id": state["trace_id"], "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "student_id": response.student_id, "question": state["question"],
            "question_category": p.category if p else None, "as_of_date": state["as_of"].isoformat(),
            "sources_retrieved": r.sources_retrieved if r else [],
            "precedence_decision": "; ".join(_notes(state)) or "no conflicting sources",
            "tools_invoked": [t.model_dump() for t in response.tools_invoked],
            "applied_rules": [a.model_dump() for a in response.applied_rules],
            "conflicts_detected": [c.model_dump(mode="json") for c in conflicts],
            "answer_type": answer_type, "answer": answer, "explanation": explanation,
            "citations": [c.model_dump(mode="json") for c in citations],
            "model": svc.settings.llm_model if calls else None, "llm_calls": len(calls),
            "tokens": prompt_toks + completion_toks, "latency_ms": total_ms,
            "plan": {"source": state.get("plan_source"), "category": p.category if p else None,
                     "tools": list(p.tools) if p else [], "rule_parameters": list(p.rule_parameters) if p else [],
                     "search_queries": list(p.search_queries) if p else []},
            "verification": ver or {"citations_valid": True, "numbers_grounded": True, "retries": 0, "fallback": False},
            "token_breakdown": {"prompt": prompt_toks, "completion": completion_toks},
            "latency_breakdown_ms": timings, "notes": state.get("notes", []),
            "config": {"embedder": s.embed_model, "chunker": s.chunker, "top_k": s.top_k, "tau": s.tau,
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


def ask(svc: Services, question: str, student_id: str | None, as_of: date | None) -> AskResponse:
    global _graph
    if _graph is None:
        _graph = build_graph(svc)
    today = datetime.now(ZoneInfo(svc.settings.timezone)).date()
    state: AskState = {"trace_id": uuid.uuid4().hex[:8], "question": question, "header_student_id": student_id,
                       "as_of": as_of or today, "started": time.perf_counter(), "retries": 0}
    out = _graph.invoke(state)
    return out["response"]


def reset_graph() -> None:  # tests
    global _graph
    _graph = None
