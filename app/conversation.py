"""Conversation memory for follow-up questions (X-Session-Id).

A session is bound to the student who started it: the history is stored under (session id, student id), so a
different X-Student-Id never sees another student's turns. Six turns, thirty minutes idle, in memory.

A follow-up is rewritten into a standalone question before anything else runs, so the guardrails, cache, router,
retrieval and audit all see a complete question:
  1. answer to a clarification   "Am I eligible to sit the exam?" -> options -> "Data Structures"
                                 => "Am I eligible to sit the exam for Data Structures?"
  2. course swap                 "Am I eligible for the CS201 exam?" -> "what about CS202?"
                                 => "Am I eligible for the CS202 exam?"
  3. anything else that reads as a follow-up ("and for placements?", "how many more classes then?") is rewritten by
     the LLM, which sees only this student's previous questions (never documents or records). The rewrite is checked:
     it may not introduce student IDs, and it falls back to "previous question + follow-up" if the LLM is down.
Questions that stand on their own are never rewritten.
"""
from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

from pydantic import BaseModel, ValidationError

from app.llm.client import LLMError, LLMResult

FOLLOWUP_START = re.compile(r"^\s*(?:and|also|what about|how about|same (?:for|with)|then|so|but|for|in|ok(?:ay)?,?)\b", re.I)
PRONOUN = re.compile(r"\b(?:it|that|this|those|these|them|there|the same|then)\b", re.I)
SID = re.compile(r"\bS\d{4}\b", re.I)


@dataclass
class Turn:
    question: str               # standalone form
    answer_type: str
    course_code: str | None = None
    ts: float = field(default_factory=time.monotonic)


class SessionStore:
    def __init__(self, max_turns: int = 6, ttl_s: float = 1800, max_sessions: int = 5000):
        self.max_turns, self.ttl, self.max_sessions = max_turns, ttl_s, max_sessions
        self._d: dict[tuple[str, str], list[Turn]] = {}
        self._lock = threading.Lock()

    def history(self, session_id: str, student_id: str | None) -> list[Turn]:
        now = time.monotonic()
        with self._lock:
            turns = self._d.get((session_id, student_id or ""), [])
            return [t for t in turns if now - t.ts < self.ttl]

    def add(self, session_id: str, student_id: str | None, turn: Turn) -> None:
        with self._lock:
            key = (session_id, student_id or "")
            turns = (self._d.get(key) or [])[-(self.max_turns - 1):] + [turn]
            self._d[key] = turns
            if len(self._d) > self.max_sessions:            # drop the stalest sessions
                for k in sorted(self._d, key=lambda k: self._d[k][-1].ts)[: len(self._d) - self.max_sessions]:
                    del self._d[k]

    def clear(self) -> None:
        with self._lock:
            self._d.clear()


sessions = SessionStore()


class Rewrite(BaseModel):
    standalone: str


SYSTEM = """You rewrite a student's follow-up message into one standalone question for a university assistant.
Use the previous questions only to fill in what the follow-up leaves out (the course, the topic, the rule).
Keep the student's wording and first person ("I", "my"). Do not answer. Do not add facts, numbers or names.
If the follow-up is already a complete question on its own, return it unchanged. Return ONLY JSON."""


def _mentions(text: str, courses: list[dict]) -> list[tuple[str, str]]:
    """(course_code, matched text) for every course named in the text, by code or full name."""
    out = []
    low = text.lower()
    for c in courses:
        code, name = c["course_code"], c["course_name"]
        m = re.search(rf"\b{re.escape(code[:-3])}\s?{re.escape(code[-3:])}\b", text, re.I)
        if m:
            out.append((code, m.group(0)))
        elif name.lower() in low:
            i = low.index(name.lower())
            out.append((code, text[i:i + len(name)]))
    return out


def looks_like_followup(q: str, courses: list[dict]) -> bool:
    words = q.split()
    if FOLLOWUP_START.search(q):
        return True
    if len(words) <= 6 and PRONOUN.search(q):
        return True
    return len(words) <= 4 and bool(_mentions(q, courses))          # a bare course name, e.g. after a clarification


def contextualize(question: str, history: list[Turn], courses: list[dict], llm) -> tuple[str, str | None, LLMResult | None]:
    """Returns (standalone question, method or None if unchanged, llm result if the LLM was called)."""
    if not history or not looks_like_followup(question, courses):
        return question, None, None
    prev = history[-1]
    now_m = _mentions(question, courses)
    prev_m = _mentions(prev.question, courses)
    short = len(question.split()) <= 8

    if prev.answer_type == "clarification_needed" and len(now_m) == 1 and len(question.split()) <= 6:
        code = now_m[0][0]
        name = next(c["course_name"] for c in courses if c["course_code"] == code)
        return f"{prev.question.rstrip(' ?.')} for {name} ({code})?", "clarification", None
    if short and len(now_m) == 1 and len(prev_m) == 1 and now_m[0][0] != prev_m[0][0]:
        return prev.question.replace(prev_m[0][1], now_m[0][1], 1), "course_swap", None

    user = ("Previous questions (oldest first):\n" + "\n".join(f"{i}. {t.question}" for i, t in enumerate(history[-3:], 1))
            + f"\n\nFollow-up: <<<{question}>>>")
    try:
        res = llm.chat_json("condense", SYSTEM, user, Rewrite.model_json_schema(), context={"question": question})
        out = Rewrite.model_validate(res.data).standalone.strip()
        allowed_ids = {m.upper() for m in SID.findall(question + " " + " ".join(t.question for t in history))}
        if out and len(out) <= 400 and {m.upper() for m in SID.findall(out)} <= allowed_ids:
            return out, "llm", res
    except (LLMError, ValidationError):
        pass
    return f"{prev.question.rstrip(' ?.')}; {question}", "concat", None
