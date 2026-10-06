"""Regex-first claim extraction: (parameter, operator, value, section) -> rule rows.

Grounded by construction: a value is accepted only if it is literally in the clause, and only when
a section states exactly one value for a parameter. Anything ambiguous becomes a warning, never a rule.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.ingestion.chunker import Chunk

PCT = r"(\d{1,3}(?:\.\d+)?)\s*(?:%|per\s?cent\b|percent\b)"
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+")

ATT_TRIGGER = re.compile(r"\b(minimum|at least|not less than|required|requirement|must|shall|enough|sufficient|eligible|need)\b", re.I)
PASS_TRIGGER = re.compile(r"\bpass(?:es|ed|ing)?\b", re.I)


@dataclass
class Claim:
    parameter: str
    operator: str
    value: str
    unit: str
    section: str
    sentence: str


def _sentences(text: str) -> list[str]:
    return [x.strip() for x in SENT_SPLIT.split(" ".join(text.split())) if x.strip()]


def _attendance(s: str) -> str | None:
    if not re.search(r"\battendance\b", s, re.I) or re.search(r"condon", s, re.I) or not ATT_TRIGGER.search(s):
        return None
    vals = {v for v in re.findall(PCT, s, re.I) if 30 <= float(v) <= 100}
    return vals.pop() if len(vals) == 1 else ("AMBIGUOUS" if vals else None)


def _pass_mark(s: str) -> str | None:
    if not PASS_TRIGGER.search(s) or re.search(r"\b(attendance|cgpa|external)\b", s, re.I):
        return None
    if not re.search(r"\b(minimum|at least|not less than|secure|obtain|aggregate|total)\b", s, re.I):
        return None
    vals = {v for v in re.findall(PCT, s, re.I) if 20 <= float(v) <= 60}
    return vals.pop() if len(vals) == 1 else ("AMBIGUOUS" if vals else None)


def _cgpa(s: str) -> str | None:
    if not re.search(r"\b(cgpa|cumulative grade point)\b", s, re.I) or not re.search(r"\b(placement|recruitment|campus)\b", s, re.I):
        return None
    vals = {v for v in re.findall(r"(?<![\d.])(\d{1,2}(?:\.\d{1,2})?)(?![\d.%])", s) if 4 <= float(v) <= 10}
    return vals.pop() if len(vals) == 1 else ("AMBIGUOUS" if vals else None)


def _backlogs(s: str) -> str | None:
    if not re.search(r"\bbacklogs?\b", s, re.I) or not re.search(r"\b(placement|recruitment|campus|eligible|register)\b", s, re.I):
        return None
    if re.search(r"\bno\s+(?:active\s+|standing\s+|live\s+|pending\s+)?backlogs?\b", s, re.I):
        return "0"
    m = re.search(r"\b(?:not more than|at most|maximum of|up to|upto)\s+(\d{1,2})\s+(?:active\s+)?backlogs?", s, re.I)
    return m.group(1) if m else None


def _condonation(s: str) -> str | None:
    if not re.search(r"\bcondon", s, re.I) or not re.search(r"\b(shortage|attendance)\b", s, re.I):
        return None
    vals = {v for v in re.findall(r"up to\s+" + PCT, s, re.I) if 1 <= float(v) <= 30}
    return vals.pop() if len(vals) == 1 else ("AMBIGUOUS" if vals else None)


def _supplementary(s: str) -> str | None:
    if not re.search(r"\bsupplementary\b", s, re.I) or not re.search(r"\b(eligible|may|can|allowed|permitted)\b", s, re.I):
        return None
    if re.search(r"\b(not eligible|cannot|not allowed|not permitted)\b", s, re.I):
        return None
    found = []
    if re.search(r"\bfail(?:ed|s|ure)?\b", s, re.I):
        found.append("FAIL")
    if re.search(r"\babsent\b", s, re.I):
        found.append("ABSENT")
    return ";".join(found) if found else None


EXTRACTORS = [
    ("min_attendance_pct", ">=", "pct", _attendance, re.compile(r"\battendance\b.*\d", re.I)),
    ("max_condonation_pct", "<=", "pct", _condonation, None),
    ("pass_min_total_pct", ">=", "pct", _pass_mark, None),
    ("min_cgpa_placement", ">=", "cgpa", _cgpa, None),
    ("max_active_backlogs_placement", "<=", "count", _backlogs, None),
    ("supplementary_allowed_results", "in", "list", _supplementary, None),
]


def extract_claims(chunks: list[Chunk]) -> tuple[list[Claim], list[str]]:
    by_section: dict[str, list[Chunk]] = {}
    for c in chunks:
        if c.section and not c.is_table:
            by_section.setdefault(c.section, []).append(c)
    claims: list[Claim] = []
    warnings: list[str] = []
    for section, cs in by_section.items():
        text = " ".join(c.text for c in cs)
        for param, op, unit, fn, mention in EXTRACTORS:
            found: dict[str, str] = {}
            ambiguous = False
            for s in _sentences(text):
                v = fn(s)
                if v == "AMBIGUOUS":
                    ambiguous = True
                elif v is not None:
                    found.setdefault(v, s)
            if len(found) == 1 and not ambiguous:
                (v, s), = found.items()
                claims.append(Claim(param, op, v, unit, section, s))
            elif found or ambiguous:
                warnings.append(f"section {section}: {param} stated ambiguously ({', '.join(found) or 'several values'}); no rule added")
            elif mention is not None and mention.search(text) and re.search(PCT, text, re.I) and param == "min_attendance_pct":
                if not re.search(r"condon", text, re.I):
                    warnings.append(f"section {section}: mentions {param} but no rule could be extracted (parameter_mentioned_without_rule)")
    return claims, warnings
