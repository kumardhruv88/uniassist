"""Annex A source precedence as a pure function.

Used twice: over rule rows (one winner per parameter, fully deterministic) and over retrieved
chunks grouped by topic. Supersession refs are always read from the source register, so a
superseding document counts even when it has no rule row and none of its chunks were retrieved.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

SEP = re.compile(r"[;,|]")


# ----------------------------------------------------------------------------- scope
def _norm(s: str) -> str:
    return re.sub(r"[\s.]+", " ", s.casefold()).strip()


def programme_matches(spec: str, programme: str | None) -> bool:
    """'ALL', 'B.Tech' (covers 'B.Tech CSE'), or 'B.Tech CSE;B.Tech ECE'."""
    if not spec or spec.strip().upper() == "ALL" or programme is None:
        return True
    p = _norm(programme)
    return any(p == _norm(s) or p.startswith(_norm(s) + " ") for s in SEP.split(spec) if s.strip())


def batch_matches(spec: str, year: int | None) -> bool:
    """'ALL', '2023+', '2023', '2022-2024', '2023;2024'."""
    if not spec or spec.strip().upper() == "ALL" or year is None:
        return True
    for part in (p.strip() for p in SEP.split(spec) if p.strip()):
        try:
            if part.endswith("+") and year >= int(part[:-1]):
                return True
            if "-" in part:
                lo, hi = (int(x) for x in part.split("-", 1))
                if lo <= year <= hi:
                    return True
            elif part.isdigit() and int(part) == year:
                return True
        except ValueError:
            continue
    return False


@dataclass(frozen=True)
class StudentScope:
    programme: str | None
    batch_year: int | None


def in_scope(scope_programmes: str, scope_batches: str, student: StudentScope | None) -> bool:
    if student is None:
        return True
    return programme_matches(scope_programmes, student.programme) and batch_matches(scope_batches, student.batch_year)


# ----------------------------------------------------------------------------- references
@dataclass(frozen=True)
class Ref:
    doc_id: str
    clause: str | None = None

    @staticmethod
    def parse_many(spec: str | None) -> tuple["Ref", ...]:
        out = []
        for part in (p.strip() for p in SEP.split(spec or "") if p.strip()):
            doc, _, clause = part.partition("#")
            out.append(Ref(doc.strip(), clause.strip() or None))
        return tuple(out)

    def covers(self, doc_id: str, section: str | None) -> bool:
        if self.doc_id != doc_id:
            return False
        if self.clause is None:
            return True
        s = section or ""
        return s == self.clause or s.startswith(self.clause + ".") or s.startswith(self.clause + "(")

    def __str__(self) -> str:
        return f"{self.doc_id}#{self.clause}" if self.clause else self.doc_id


# ----------------------------------------------------------------------------- inputs / outputs
@dataclass(frozen=True)
class RegisterDoc:
    doc_id: str
    authority: int
    effective_from: date
    effective_to: date | None = None
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    supersedes: tuple[Ref, ...] = ()
    title: str = ""


@dataclass(frozen=True)
class Candidate:
    key: str                      # rule_id or chunk_id
    doc_id: str
    section: str | None
    authority: int                # always from the source register
    effective_from: date
    effective_to: date | None = None
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    value: str | None = None      # rule rows only
    title: str = ""


@dataclass
class Resolution:
    winner: Candidate | None = None
    tied: list[Candidate] = field(default_factory=list)
    losers: list[tuple[Candidate, str]] = field(default_factory=list)          # (candidate, "step3_authority"|"step4_recency")
    superseded: list[tuple[Candidate, str]] = field(default_factory=list)      # (candidate, superseding doc_id)
    informational: list[Candidate] = field(default_factory=list)              # level 5
    upcoming: list[Candidate] = field(default_factory=list)
    excluded: list[Candidate] = field(default_factory=list)                   # expired or out of scope

    @property
    def unresolved(self) -> bool:
        """Step 5: an exact tie whose values differ."""
        if not self.winner or not self.tied:
            return False
        return any(t.value is not None and t.value != self.winner.value for t in self.tied)


def effective(c_from: date, c_to: date | None, as_of: date) -> bool:
    return c_from <= as_of and (c_to is None or c_to >= as_of)


def supersession_map(register: list[RegisterDoc], student: StudentScope | None, as_of: date) -> dict[Ref, str]:
    """Refs replaced by applicable level 1–2 documents -> the superseding doc_id (step 2)."""
    out: dict[Ref, str] = {}
    for d in register:
        if d.authority <= 2 and effective(d.effective_from, d.effective_to, as_of) and in_scope(d.scope_programmes, d.scope_batches, student):
            for r in d.supersedes:
                out.setdefault(r, d.doc_id)
    return out


def superseded_by(doc_id: str, section: str | None, refs: dict[Ref, str]) -> str | None:
    for r, by in refs.items():
        if by != doc_id and r.covers(doc_id, section):
            return by
    return None


# ----------------------------------------------------------------------------- the five steps
def resolve(cands: list[Candidate], student: StudentScope | None, as_of: date,
            register: list[RegisterDoc], *, with_upcoming: bool = True) -> Resolution:
    res = Resolution()
    applicable: list[Candidate] = []
    for c in cands:                                                   # Step 1: applicability
        scoped = in_scope(c.scope_programmes, c.scope_batches, student)
        if scoped and effective(c.effective_from, c.effective_to, as_of):
            applicable.append(c)
        elif scoped and c.effective_from > as_of:
            res.upcoming.append(c)
        else:
            res.excluded.append(c)

    refs = supersession_map(register, student, as_of)                 # Step 2: explicit supersession
    live: list[Candidate] = []
    for c in applicable:
        by = superseded_by(c.doc_id, c.section, refs)
        if by:
            res.superseded.append((c, by))
        else:
            live.append(c)

    res.informational = [c for c in live if c.authority >= 5]         # level 5 never overrides
    ranked = sorted((c for c in live if c.authority < 5),
                    key=lambda c: (c.authority, -c.effective_from.toordinal()))
    if ranked:                                                        # Steps 3–5
        top = res.winner = ranked[0]
        for c in ranked[1:]:
            if c.doc_id != top.doc_id and (c.authority, c.effective_from) == (top.authority, top.effective_from):
                res.tied.append(c)
            else:
                res.losers.append((c, "step3_authority" if c.authority > top.authority else "step4_recency"))

    if with_upcoming and res.upcoming:                                # only future sources that would win on their date
        keep = []
        for c in res.upcoming:
            if c.authority >= 5:
                continue
            future = resolve(cands, student, c.effective_from, register, with_upcoming=False)
            if future.winner and future.winner.key == c.key:
                keep.append(c)
        res.upcoming = keep
    return res


def scope_partitions(cands: list[Candidate]) -> list[tuple[str, StudentScope | None]]:
    """Without a student identity, resolve once per programme scope (ALL-scope rules apply to every partition)."""
    specs = sorted({c.scope_programmes for c in cands if c.scope_programmes.upper() != "ALL"})
    if not specs:
        return [("all programmes", None)]
    parts: list[tuple[str, StudentScope | None]] = []
    for spec in specs:
        first = SEP.split(spec)[0].strip()
        parts.append((spec.replace(";", ", "), StudentScope(programme=first, batch_year=None)))
    if any(c.scope_programmes.upper() == "ALL" for c in cands):
        parts.append(("other programmes", StudentScope(programme="__other__", batch_year=None)))
    return parts
