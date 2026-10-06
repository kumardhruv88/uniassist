"""Token optimisation: what the composer sees, in as few tokens as the answer needs.

1. Precedence filter. LOWER_PRECEDENCE evidence (a lower-authority source on a rule a higher one governs) is not
   shown; a one-line note says it was set aside. The model cannot repeat a claim it never sees (eval item VC3:
   the FAQ's "65% is enough" was being repeated even though the circular requires 80%).
2. De-duplication. Near-identical chunks (Jaccard >= 0.8 on stemmed terms) are shown once.
3. Extractive compression. Long chunks that are neither rule anchors nor tables keep their first sentence and the
   sentences that mention the question's terms (glossary expansions included), in document order.
4. Budget. Evidence is added in rank order until CONTEXT_BUDGET_TOKENS is reached; anchors and the top hit always fit.

Citations and quotes still use the full original text: only the prompt is compressed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, replace

from app.retrieval.query import terms

SENT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")
COMPRESS_OVER = 700          # characters; shorter chunks are sent whole
KEEP_CHARS = 520


def est_tokens(text: str) -> int:
    """About 4 characters per token for English with the Llama 3 tokenizer; used only for budgeting and reporting."""
    return max(1, round(len(text) / 4))


@dataclass
class ContextReport:
    evidence_in: int = 0
    evidence_out: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    set_aside: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)
    compressed: list[str] = field(default_factory=list)
    over_budget: list[str] = field(default_factory=list)

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_in - self.tokens_out)

    def as_dict(self) -> dict:
        return {"evidence_in": self.evidence_in, "evidence_out": self.evidence_out, "evidence_tokens_in": self.tokens_in,
                "evidence_tokens_out": self.tokens_out, "tokens_saved": self.tokens_saved, "set_aside": self.set_aside,
                "duplicates": self.duplicates, "compressed": self.compressed, "over_budget": self.over_budget}


def compress(text: str, q_terms: set[str]) -> str:
    sents = SENT.split(text.strip())
    if len(sents) <= 2:
        return text
    scored = []
    for i, snt in enumerate(sents[1:], 1):
        st = set(terms(snt))
        overlap = len(st & q_terms)
        scored.append((overlap + (0.5 if overlap and re.search(r"\d", snt) else 0.0), i))
    keep = {0}
    used = len(sents[0])
    for score, i in sorted(scored, key=lambda x: (-x[0], x[1])):
        if score <= 0 or used + len(sents[i]) > KEEP_CHARS:
            continue
        keep.add(i)
        used += len(sents[i])
    if len(keep) == 1:                       # nothing matched: keep the opening, which carries the clause's subject
        keep.add(1)
    parts, prev = [], -1
    for i in sorted(keep):
        if prev >= 0 and i != prev + 1:
            parts.append("…")
        parts.append(sents[i])
        prev = i
    if prev != len(sents) - 1:
        parts.append("…")
    return " ".join(parts)


def build_context(question: str, evidence: list, budget_tokens: int, do_compress: bool = True,
                  extra_terms: list[str] | None = None) -> tuple[list, list[str], ContextReport]:
    """Returns (evidence for the prompt, precedence notes for set-aside sources, report)."""
    rep = ContextReport(evidence_in=len(evidence), tokens_in=sum(est_tokens(e.text) for e in evidence))
    q_terms = set(terms(question + " " + " ".join(extra_terms or [])))
    notes: list[str] = []
    kept: list = []
    kept_terms: list[set[str]] = []
    used = 0
    for e in evidence:
        ref = f"{e.doc_id}#{e.section}" if e.section else e.doc_id
        if e.label == "LOWER_PRECEDENCE":
            rep.set_aside.append(ref)
            notes.append(f"{e.title} (section {e.section or '-'}) is a lower-authority source on a rule that a higher-authority "
                         f"source governs; it was set aside and does not apply.")
            continue
        et = set(terms(e.text))
        if not e.anchor and any(len(et & k) / max(1, len(et | k)) >= 0.8 for k in kept_terms):
            rep.duplicates.append(ref)
            continue
        text = e.text
        if do_compress and not e.anchor and not e.is_table and len(text) > COMPRESS_OVER:
            text = compress(text, q_terms)
            if text != e.text:
                rep.compressed.append(ref)
        cost = est_tokens(text)
        if kept and not e.anchor and used + cost > budget_tokens:
            rep.over_budget.append(ref)
            continue
        kept.append(replace(e, text=text))
        kept_terms.append(et)
        used += cost
    rep.evidence_out, rep.tokens_out = len(kept), used
    return kept, notes, rep
