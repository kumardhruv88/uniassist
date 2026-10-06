"""Instruction-like text inside documents is data, never instructions (guide R8).

At ingest we flag chunks; at compose time those sentences are redacted before the LLM sees them.
The structural defences matter more: the planner never sees documents and the composer has no tools.
"""
from __future__ import annotations

import re

PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"\bignore\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier|other)\s+(?:instructions|rules|prompts?|guidelines)",
    r"\bdisregard\s+(?:all\s+|any\s+|the\s+|your\s+)?(?:previous|prior|above|earlier)?\s*(?:instructions|rules|prompts?|guidelines)",
    r"\byou\s+are\s+now\b",
    r"\bsystem\s+prompt\b",
    r"\b(?:ai|a\.i\.|chat\s?bot|chatbot|assistant|language\s+model|llm)s?\b[^.!?\n]{0,80}\b(?:must|should|always|never|ignore|tell|say|answer|reply|respond)\b",
    r"\bact\s+as\b[^.!?\n]{0,40}\b(?:admin|administrator|registrar|dean|system)\b",
    r"\breveal\b[^.!?\n]{0,40}\b(?:prompt|instructions|password|records)\b",
    r"\bdo\s+not\s+(?:cite|mention|reveal)\b",
]]
SENT = re.compile(r"[^.!?\n]+[.!?]?")
REDACTED = "[instruction-like text removed]"


def flagged_sentences(text: str) -> list[str]:
    return [s.strip() for s in SENT.findall(text) if any(p.search(s) for p in PATTERNS)]


def redact(text: str) -> str:
    out = text
    for s in flagged_sentences(text):
        out = out.replace(s, REDACTED)
    return out
