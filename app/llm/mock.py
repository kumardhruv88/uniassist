"""Deterministic handlers for MockLLM. Tests only: the real system always uses Ollama."""
from __future__ import annotations


def mock_handlers() -> dict:
    from app.llm.planner import route

    def plan(ctx: dict) -> dict:
        return route(ctx.get("question", "")).model_dump()

    def compose(ctx: dict) -> dict:
        ev = ctx.get("evidence") or []
        verdict = ctx.get("verdict")
        if not ev and not verdict:
            return {"answer": "", "explanation": "", "evidence_ids": [], "insufficient_evidence": True, "unanswered_parts": [], "disagreeing_pairs": []}
        first = ev[0] if ev else None
        answer = verdict or (first["text"].split(". ")[0].strip() + "." if first else "")
        explanation = f"This comes from {first['title']}." if first else "This was computed from your records."
        return {"answer": answer, "explanation": explanation, "evidence_ids": [e["eid"] for e in ev[:2]],
                "insufficient_evidence": False, "unanswered_parts": [], "disagreeing_pairs": []}

    return {"plan": plan, "compose": compose}
