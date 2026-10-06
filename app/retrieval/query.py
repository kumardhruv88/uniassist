"""Query understanding shared by retrieval, caching and grounding checks.

- terms(): lowercase word tokens, stopwords removed, light stemming (plural strip + 6-character truncation),
  so "examinations"/"examination"/"exam" and "backlogs"/"backlog" meet.
- expand(): a university glossary that maps the words students use to the words regulations use
  ("bunk" -> attendance, "supply"/"KT" -> supplementary/backlog, "hall ticket" -> admit card).
  The expansion is an extra search query; the original question is always searched as written.
"""
from __future__ import annotations

import re

WORD = re.compile(r"[a-z]\.[a-z]+|[a-z]+|\d+(?:\.\d+)?")     # keeps b.tech / m.tech / ph.d apart
STOP = set("""a an the and or of to in on at for by with from as is are was were be been being am do does did done
have has had i me my mine we our you your he she it its they them their this that these those there here what which
who whom whose when where why how can could may might must shall should will would not no yes if then than so too
very just also about into over under per any all each some such only own same other more most much many please tell
know get got give let want need""".split())

GLOSSARY: list[tuple[re.Pattern, str]] = [(re.compile(p, re.I), t) for p, t in [
    (r"\b(?:bunk\w*|skip(?:ped|ping)?|miss(?:ed|ing)? (?:classes|lectures)|shortage|short of attendance)\b", "attendance"),
    (r"\b(?:supply|supple|supplementary|re-?exam\w*|re-?appear\w*|repeat exam\w*|make-?up exam\w*|backlog exam\w*)\b",
     "supplementary examination"),
    (r"\b(?:kt|arrears?|backs)\b", "backlog"),
    (r"\b(?:hall ?ticket|admit card)\b", "admit card examination"),
    (r"\b(?:debarred|detained|detention)\b", "detained attendance"),
    (r"\b(?:medical (?:certificate|leave|grounds)|sick|illness|hospitali[sz]ed)\b", "condonation medical"),
    (r"\b(?:campus (?:drive|recruitment|placement)s?|recruit\w*|jobs?|companies|company)\b", "placement"),
    (r"\b(?:pointer|gpa)\b", "CGPA"),
    (r"\b(?:pass(?:ing)? marks?|minimum marks|cut-?off)\b", "pass marks"),
    (r"\b(?:mess|dining)\b", "mess charges hostel"),
    (r"\b(?:room rent|accommodation|dorm\w*)\b", "hostel"),
    (r"\b(?:tuition|charges|cost|how much (?:do i|to) pay)\b", "fee"),
    (r"\b(?:end ?sem\w*|finals?|semester exams?|term exams?)\b", "end-semester examination"),
    (r"\b(?:curfew|gate timings?|in-?time|visitors?|guests?)\b", "hostel timings visitors"),
]]


def stem(w: str) -> str:
    if w[0].isdigit() or len(w) <= 3:
        return w
    if w.endswith(("sses", "ches", "shes", "xes")):
        w = w[:-2]
    elif w.endswith("s") and not w.endswith("ss"):
        w = w[:-1]
    return w[:6]


def terms(text: str) -> list[str]:
    return [stem(w) for w in WORD.findall(text.lower()) if w not in STOP and (len(w) > 1 or w.isdigit())]


def expand(question: str) -> tuple[str, list[str]]:
    """(expanded query, added glossary terms). Only terms not already in the question are added."""
    have = set(terms(question))
    added: list[str] = []
    for rx, t in GLOSSARY:
        if rx.search(question) and not set(terms(t)) <= have:
            added.append(t)
            have |= set(terms(t))
    return (f"{question} ({'; '.join(added)})" if added else question), added


def canonical(question: str) -> frozenset[str]:
    """Order-free content signature after stemming and glossary mapping (semantic-cache safety check)."""
    q, _ = expand(question)
    return frozenset(terms(q))
