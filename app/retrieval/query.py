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
    (r"\b(?:tuition|charges?|costs?|price|how much (?:do i|to) pay|how much (?:is|does))\b", "fee"),
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


# ----------------------------------------------------------------------------- dates and scope named in the question
MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), 1)}
_MON = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
ISO_DATE = re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b")
DAY_MON_YEAR = re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{_MON},?\s+(20\d\d)\b", re.I)
MON_DAY_YEAR = re.compile(rf"\b{_MON}\s+(\d{{1,2}})(?:st|nd|rd|th)?,?\s+(20\d\d)\b", re.I)
MON_YEAR = re.compile(rf"\b{_MON}\s+(20\d\d)\b", re.I)


def question_date(question: str):
    """The single date a question is asked about ("As of 2026-12-10", "on 10 December 2026", "in October 2026" ->
    the 15th). None when there is no date or more than one, so a comparison question keeps the request's date."""
    from datetime import date
    found: set = set()
    spans: list[tuple[int, int]] = []

    def add(y: int, m: int, d: int, span: tuple[int, int]) -> None:
        try:
            found.add(date(y, m, d))
            spans.append(span)
        except ValueError:
            pass
    for m in ISO_DATE.finditer(question):
        add(int(m.group(1)), int(m.group(2)), int(m.group(3)), m.span())
    for m in DAY_MON_YEAR.finditer(question):
        add(int(m.group(3)), MONTHS[m.group(2).lower()[:3]], int(m.group(1)), m.span())
    for m in MON_DAY_YEAR.finditer(question):
        add(int(m.group(3)), MONTHS[m.group(1).lower()[:3]], int(m.group(2)), m.span())
    for m in MON_YEAR.finditer(question):
        if not any(a <= m.start() < b for a, b in spans):            # "October 2026", not part of a full date
            add(int(m.group(2)), MONTHS[m.group(1).lower()[:3]], 15, m.span())
    return found.pop() if len(found) == 1 else None


PROGRAMME = re.compile(r"\b(B\.?\s?Tech|B\.?\s?Arch|B\.?\s?Sc|B\.?\s?Com|BBA|BCA|M\.?\s?Tech|M\.?\s?Sc|MBA|MCA|Ph\.?\s?D|B\.E\.?)"
                       r"(?:\s*\(?\s*(CSE|ECE|EEE|ME|CE|IT|Civil|Mechanical|Electrical))?\b", re.I)
CANON = {"btech": "B.Tech", "barch": "B.Arch", "bsc": "B.Sc", "bcom": "B.Com", "bba": "BBA", "bca": "BCA",
         "mtech": "M.Tech", "msc": "M.Sc", "mba": "MBA", "mca": "MCA", "phd": "Ph.D", "be": "B.E"}
BATCH = re.compile(r"\b(?:batch|cohort|admitted in|class of)\s*(?:of\s*)?(20\d\d)\b|\b(20\d\d)\s*(?:batch|cohort)\b", re.I)


def question_scope(question: str):
    """Programme and batch named in the question ("for B.Arch batch 2025") as a StudentScope, else None."""
    from app.policy.precedence import StudentScope
    m = PROGRAMME.search(question)
    programme = None
    if m:
        base = CANON.get(re.sub(r"[^a-z]", "", m.group(1).lower()))
        programme = f"{base} {m.group(2).upper()}" if base and m.group(2) else base
    b = BATCH.search(question)
    batch = int(b.group(1) or b.group(2)) if b else None
    if batch is None and m:                                      # "MBA 2025 Industry Immersion"
        y = re.match(r"\s*(20\d\d)\b", question[m.end():])
        batch = int(y.group(1)) if y else None
    return StudentScope(programme=programme, batch_year=batch) if (programme or batch) else None
