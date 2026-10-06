"""Rule resolver: thresholds come ONLY from rule_registry, resolved with Annex A at question time."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date

from app.db import rows
from app.models import ConflictRecord, SourceRef
from app.policy.precedence import Candidate, RegisterDoc, Ref, Resolution, StudentScope, resolve, scope_partitions


@dataclass(frozen=True)
class ParamSpec:
    name: str
    label: str
    unit: str
    operator: str
    keywords: tuple[str, ...]


PARAMETERS: dict[str, ParamSpec] = {p.name: p for p in [
    ParamSpec("min_attendance_pct", "Minimum attendance to sit end-semester exams", "pct", ">=", ("attendance",)),
    ParamSpec("max_condonation_pct", "Largest attendance shortage that can be condoned on medical grounds", "pct", "<=",
              ("condonation", "medical")),
    ParamSpec("pass_min_total_pct", "Minimum total marks to pass a course", "pct", ">=", ("pass",)),
    ParamSpec("supplementary_allowed_results", "Results that may register for a supplementary exam", "list", "in", ("supplementary",)),
    ParamSpec("min_cgpa_placement", "Minimum CGPA to register for placements", "cgpa", ">=", ("cgpa", "placement")),
    ParamSpec("max_active_backlogs_placement", "Maximum active backlogs to register for placements", "count", "<=", ("backlog", "placement")),
]}


def _d(v: str | None) -> date | None:
    return date.fromisoformat(v) if v else None


def load_register(con: sqlite3.Connection) -> tuple[list[RegisterDoc], dict[str, dict]]:
    docs = rows(con, "SELECT * FROM source_register")
    reg = [RegisterDoc(doc_id=d["doc_id"], authority=d["authority_level"], effective_from=_d(d["effective_from"]),
                       effective_to=_d(d["effective_to"]), scope_programmes=d["scope_programmes"],
                       scope_batches=d["scope_batches"], supersedes=Ref.parse_many(d["supersedes"]), title=d["title"])
           for d in docs]
    return reg, {d["doc_id"]: d for d in docs}


@dataclass
class RuleResult:
    parameter: str
    scope_label: str | None
    resolution: Resolution
    rows: dict[str, dict] = field(default_factory=dict)
    docs: dict[str, dict] = field(default_factory=dict)

    @property
    def winner(self) -> dict | None:
        w = self.resolution.winner
        return self.rows[w.key] if w and not self.resolution.unresolved else None

    def display_value(self, row: dict | None = None) -> str:
        row = row or self.winner
        if not row:
            return ""
        unit = row.get("unit") or PARAMETERS.get(self.parameter, ParamSpec("", "", "", "", ())).unit
        return f"{row['operator']}{row['value']}{'%' if unit == 'pct' else ''}"

    def ref(self, c: Candidate, reason: str | None = None) -> SourceRef:
        d = self.docs.get(c.doc_id, {})
        return SourceRef(doc_id=c.doc_id, section=c.section, value=c.value, title=d.get("title"),
                         reason=reason, effective_from=c.effective_from)

    def conflict(self) -> ConflictRecord | None:
        """Resolved or unresolved disagreement for this parameter; None if every source agrees."""
        r = self.resolution
        if not r.winner:
            return None
        w = r.winner
        others: list[SourceRef] = []
        steps: list[str] = []
        for c, by in r.superseded:
            if c.value != w.value:
                others.append(self.ref(c, f"replaced by {by}"))
                steps.append("step2_supersession")
        for c, step in r.losers:
            if c.value != w.value:
                others.append(self.ref(c, "lower authority" if step == "step3_authority" else "older at the same authority"))
                steps.append(step)
        for c in r.informational:
            if c.value != w.value:
                others.append(self.ref(c, "unofficial source, informational only"))
        if r.unresolved:
            return ConflictRecord(topic=self.parameter, winner=None,
                                  others=[self.ref(w, "tied")] + [self.ref(t, "tied") for t in r.tied], resolved_by=None)
        if not others:
            return None
        resolved_by = next((s for s in ("step2_supersession", "step3_authority", "step4_recency") if s in steps), None)
        return ConflictRecord(topic=self.parameter, winner=self.ref(w), others=others, resolved_by=resolved_by)

    def precedence_note(self) -> str:
        r = self.resolution
        if not r.winner:
            return f"{self.parameter}: no applicable rule"
        parts = [f"{self.parameter}: {r.winner.doc_id}#{r.winner.section} applies"]
        parts += [f"{by} supersedes {c.doc_id}#{c.section} (step 2)" for c, by in r.superseded]
        parts += [f"{c.doc_id} loses on {'authority (step 3)' if s == 'step3_authority' else 'recency (step 4)'}" for c, s in r.losers]
        if r.unresolved:
            parts.append("unresolved tie (step 5)")
        return "; ".join(parts)


def resolve_rule(con: sqlite3.Connection, parameter: str, student: StudentScope | None, as_of: date) -> list[RuleResult]:
    """One result for a known student; one per programme scope for general questions."""
    reg, docs = load_register(con)
    rule_rows = rows(con, """SELECT r.* , s.authority_level FROM rule_registry r
                             JOIN source_register s ON s.doc_id = r.source_doc_id
                             WHERE r.parameter = ? AND r.status = 'active'""", (parameter,))
    by_key = {r["rule_id"]: r for r in rule_rows}
    cands = [Candidate(key=r["rule_id"], doc_id=r["source_doc_id"], section=r["source_section"],
                       authority=r["authority_level"], effective_from=_d(r["effective_from"]),
                       effective_to=_d(r["effective_to"]), scope_programmes=r["scope_programmes"],
                       scope_batches=r["scope_batches"], value=r["value"],
                       title=docs.get(r["source_doc_id"], {}).get("title", "")) for r in rule_rows]
    if student is not None:
        return [RuleResult(parameter, None, resolve(cands, student, as_of, reg), by_key, docs)]
    return [RuleResult(parameter, label, resolve(cands, scope, as_of, reg), by_key, docs)
            for label, scope in scope_partitions(cands)]
