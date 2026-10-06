"""Deterministic checks on the composer's draft. Every number must be traceable; citations must be evidence."""
from __future__ import annotations

import json
import re
from decimal import Decimal, InvalidOperation

DOC_ID = re.compile(r"\b[A-Z][A-Z0-9]*(?:[-_][A-Z0-9]+)+\b")
ISO_DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
WORD_DATE = re.compile(r"\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}\b"
                       r"|\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2}(?:st|nd|rd|th)?,?\s+\d{4}\b", re.I)
COURSE = re.compile(r"\b[A-Z]{2,4}\s?\d{3}\b")
EVID = re.compile(r"\[?\bE\d{1,2}\b\]?")
SECTION = re.compile(r"(?:§|clause|section|rule|para(?:graph)?)\s*\d+(?:\.\d+)*", re.I)
NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w])")
ELIG = re.compile(r"\byou (?:are|'re|would be|will be|are not|aren't|would not be|won't be)\s+(?:not\s+)?(?:yet\s+)?eligible\b", re.I)
SID = re.compile(r"\bS\d{4}\b")


def _strip(text: str) -> str:
    for rx in (EVID, WORD_DATE, ISO_DATE, DOC_ID, COURSE, SECTION):
        text = rx.sub(" ", text)
    return text


def _norm(n: str) -> str:
    try:
        return format(Decimal(n).normalize(), "f")
    except InvalidOperation:
        return n


def numbers(text: str) -> set[str]:
    return {_norm(n) for n in NUM.findall(_strip(text))}


def strip_markers(text: str) -> str:
    text = re.sub(r"§+\s*", "section ", text)            # plain words for students, not the section sign
    return re.sub(r"\s{2,}", " ", re.sub(r"\s*\[(?:E\d{1,2}(?:\s*,\s*)?)+\]", "", text)).strip()


def check_draft(draft, *, evidence_ids: set[str], allowed_sources: list[str], verdict: str | None,
                self_id: str | None, needs_citation: bool, personal: bool = True) -> list[str]:
    problems: list[str] = []
    cited = [e for e in draft.evidence_ids if e in evidence_ids]
    bogus = [e for e in draft.evidence_ids if e not in evidence_ids]
    if bogus:
        problems.append(f"evidence_ids {bogus} were not provided; cite only the given ids")
    if needs_citation and not draft.insufficient_evidence and not cited:
        problems.append("cite at least one evidence id that supports the answer")
    allowed = set().union(*(numbers(s) for s in allowed_sources)) if allowed_sources else set()
    text = f"{draft.answer} {draft.explanation}"
    unknown = sorted(n for n in numbers(text) if n not in allowed and not (n.isdigit() and int(n) <= 10))
    if unknown:
        problems.append(f"these numbers are not in the verdict, records, rules or evidence: {', '.join(unknown)}")
    if verdict is None and personal and ELIG.search(text):     # a policy answer may quote "not eligible" from a clause
        problems.append("do not state eligibility: no verdict was computed for this question")
    problems += [f"false comparison: '{c}'" for c in false_comparisons(text)]
    others = {s for s in SID.findall(text) if s != self_id}
    if others:
        problems.append(f"do not mention other student IDs ({', '.join(sorted(others))})")
    return problems


def sources_for_grounding(question: str, verdict: str | None, tool_results, rule_texts: list[str], evidence) -> list[str]:
    out = [question, verdict or ""] + rule_texts
    out += [json.dumps(t.output, default=str) for t in tool_results]
    out += [e.text for e in evidence]
    return out


WORD = re.compile(r"[a-z]+|\d+(?:\.\d+)?")
STOP = set("the and for that with this from are was were you your have has not can may must will shall into each any its "
           "which who what when where how there their them they our out all but per also been being than then".split())


def infer_citations(text: str, evidence, k: int = 2, min_overlap: float = 0.35) -> list[str]:
    """When the model omits evidence_ids, cite the retrieved evidence that the answer's own words come from."""
    words = {w for w in WORD.findall(text.lower()) if (len(w) > 2 and w not in STOP) or w[0].isdigit()}
    if not words:
        return []
    scored = []
    for e in evidence:
        if e.label == "INFORMATIONAL":
            continue
        overlap = len(words & set(WORD.findall(e.text.lower()))) / len(words)
        scored.append((overlap, e.eid))
    return [eid for ov, eid in sorted(scored, reverse=True)[:k] if ov >= min_overlap]


CMP = re.compile(r"(\d+(?:\.\d+)?)\s*%[^.;]{0,60}?\b(below|less than|under|lower than|short of|above|more than|"
                 r"exceeds|exceeding|over|higher than|meets|meeting|at least|equal to)\b[^.;\d]{0,40}?(\d+(?:\.\d+)?)\s*%", re.I)


NEW_CLAUSE = re.compile(r"\b(?:so|therefore|thus|hence|because|since|and|but|while|whereas|must|should|need|needs|"
                        r"has to|have to|required to|means)\b", re.I)


def false_comparisons(text: str) -> list[str]:
    """Catch arithmetic the model got wrong, e.g. '79.66%, which is below the required 75%'.
    A new clause between the two values ('up to 10%, so attendance must be at least 70%') is not a comparison."""
    bad = []
    for m in CMP.finditer(text):
        if NEW_CLAUSE.search(text[m.end(1):m.start(2)]):
            continue
        a, word, b = Decimal(m.group(1)), m.group(2).lower(), Decimal(m.group(3))
        below = word in ("below", "less than", "under", "lower than", "short of")
        above = word in ("above", "more than", "exceeds", "exceeding", "over", "higher than")
        meets = word in ("meets", "meeting", "at least", "equal to")
        if (below and not a < b) or (above and not a > b) or (meets and not a >= b):
            bad.append(m.group(0).strip())
    return bad


GENERIC = STOP | set("student students university college course courses exam exams examination semester rule rules "
                     "policy please tell know about there much many more what which minimum maximum required "
                     "need allowed get give take does did should would could now then still yet ever just "
                     "cost costs price charge charges".split())


def evidence_coverage(question: str, evidence) -> float:
    """Share of the question's distinctive terms that appear anywhere in the retrieved evidence (prefix match)."""
    terms = {w for w in WORD.findall(question.lower()) if len(w) > 2 and w not in GENERIC and not w.isdigit()}
    if not terms:
        return 1.0
    words = set(WORD.findall(" ".join(e.text for e in evidence).lower()))
    pre = {w[:4] for w in words if len(w) >= 4}
    hit = {t for t in terms if t in words or (len(t) >= 4 and t[:4] in pre)}
    return len(hit) / len(terms)


SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")


def groundedness(text: str, sources: list[str]) -> tuple[float, list[str]]:
    """Share of the draft's sentences supported by the sources (verdict, records, rules, evidence titles and text).

    A sentence is supported when at least half of its content terms occur in one source sentence, or three quarters
    occur across all sources. Terms are stemmed, so paraphrase with the same vocabulary still counts; a sentence
    built from words the sources never use does not. Returns (score, unsupported sentences)."""
    from app.retrieval.query import terms
    sents = [x for x in SENT_SPLIT.split(strip_markers(text)) if len(set(terms(x))) >= 3]
    if not sents:
        return 1.0, []
    src_sents = [set(terms(x)) for src in sources for x in SENT_SPLIT.split(src or "") if x.strip()]
    pool = set().union(*src_sents) if src_sents else set()
    unsupported = []
    for snt in sents:
        st = set(terms(snt))
        best = max((len(st & ss) / len(st) for ss in src_sents), default=0.0)
        if best < 0.5 and len(st & pool) / len(st) < 0.75:
            unsupported.append(snt)
    return round(1 - len(unsupported) / len(sents), 3), unsupported


ABSENCE = re.compile(r"\b(?:no|not)\b[^.]{0,30}?\b(?:mention(?:ed)?|information|details?|specified|stated|provided|found)\b"
                     r"|\b(?:does not|doesn't|do not|don't|did not)\s+(?:mention|say|specify|state|cover|include|provide)\b", re.I)


NO_SUCH = re.compile(r"^\s*(?:no[,.]?\s+)?there (?:is|are) no\s+(?:specific\s+|such\s+|separate\s+)?([a-z][a-z-]+)", re.I)


def says_not_covered(answer: str, evidence_text: str = "") -> bool:
    """'There is no scholarship mentioned for …' is an abstention, whatever the model labelled it. A flat
    'There is no X' counts as one too when X never appears in the cited evidence (nothing to support the claim)."""
    if ABSENCE.search(answer):
        return True
    m = NO_SUCH.search(answer)
    if not m:
        return False
    from app.retrieval.query import terms
    subject = terms(m.group(1))
    return bool(subject) and subject[0] not in set(terms(evidence_text))


NEG_CLAIM = re.compile(r"\b(?:do(?:es)? not need|don'?t need|doesn'?t need|need not|no need to|not required|not mandatory|"
                       r"not necessary|does not matter|doesn'?t matter|is optional|are optional)\b", re.I)
NEG_WORD = re.compile(r"\b(?:no|not|none|never|cannot|without|optional|exempt\w*|waived|need not)\b", re.I)


def unsupported_negative(answer: str, cited_texts: list[str]) -> bool:
    """'You do not need to attend …' when no cited clause says anything negative: a claim the model made up."""
    return bool(NEG_CLAIM.search(answer)) and not any(NEG_WORD.search(t) for t in cited_texts)
