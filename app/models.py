"""API contract (guide section 6) plus additive fields. Pydantic v2."""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

AnswerType = Literal["retrieved_fact", "calculated", "not_found", "clarification_needed", "refused", "conflict_flagged"]
ResolvedBy = Literal["step2_supersession", "step3_authority", "step4_recency"]


# ----------------------------------------------------------------------------- /ask
class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1000)
    as_of_date: date | None = None


class Citation(BaseModel):
    doc_id: str
    title: str
    section: str | None = None
    page: int | None = None
    version: str | None = None
    effective_from: date
    quote: str | None = None            # additive: excerpt of the cited clause


class ToolInvocation(BaseModel):
    tool: str
    input: dict[str, Any] = {}
    output: dict[str, Any] = {}
    status: Literal["ok", "error"] = "ok"
    ms: int | None = None


class AppliedRule(BaseModel):
    rule_id: str
    value: str
    source_doc_id: str
    source_section: str | None = None
    parameter: str | None = None


class SourceRef(BaseModel):
    doc_id: str
    section: str | None = None
    value: str | None = None
    title: str | None = None
    reason: str | None = None
    effective_from: date | None = None


class ConflictRecord(BaseModel):
    topic: str
    winner: SourceRef | None
    others: list[SourceRef]
    resolved_by: ResolvedBy | None      # None = unresolved (conflict_flagged)


class AskMeta(BaseModel):
    """Additive: how this answer was produced. For the UI, the eval harness and operators."""
    cache_hit: bool = False
    cache: Literal["miss", "exact", "semantic"] = "miss"
    latency_ms: int = 0
    guardrail: str | None = None                 # the input guardrail that blocked the question
    output_redactions: list[str] = []            # what the output guardrail removed
    groundedness: float | None = None            # share of answer sentences supported by the sources
    session_id: str | None = None
    standalone_question: str | None = None       # set when a follow-up was rewritten
    rewrite: str | None = None                   # clarification | course_swap | llm | concat
    degraded: bool = False                       # the LLM was unavailable and a deterministic template answered
    planner: str | None = None                   # llm | router | router_first | router_fallback
    retrieval: str | None = None                 # dense | hybrid, + rerank
    llm_calls: int = 0
    tokens: int = 0
    tokens_saved: int = 0                        # evidence tokens removed by context optimisation
    as_of_source: str | None = None              # request | question ("As of 2026-12-10, …") | today
    scope: str | None = None                     # the programme/batch rules were resolved for, and where it came from


class AskResponse(BaseModel):
    trace_id: str
    answer: str
    answer_type: AnswerType
    citations: list[Citation]
    tools_invoked: list[ToolInvocation]
    applied_rules: list[AppliedRule]
    conflicts_detected: list[ConflictRecord]
    explanation: str
    as_of_date: date
    upcoming_changes: list[SourceRef] = []      # additive
    clarification_options: list[str] = []      # additive
    student_id: str | None = None              # additive
    meta: AskMeta = Field(default_factory=AskMeta)   # additive


# ----------------------------------------------------------------------------- /ingest
DOC_TYPE_SYNONYMS = {
    "regulation": "regulation", "regulations": "regulation", "ordinance": "regulation", "statute": "regulation",
    "act": "regulation", "rules": "regulation",
    "circular": "circular", "notification": "circular", "office order": "circular",
    "notice": "notice", "department notice": "notice",
    "faq": "faq", "faqs": "faq", "handbook": "handbook", "guide": "handbook",
    "unofficial": "unofficial", "post": "unofficial", "forum": "unofficial",
}
DOC_TYPE_LEVEL = {"regulation": 1, "circular": 2, "notice": 3, "faq": 4, "handbook": 4, "unofficial": 5}
DOC_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$")


def _blank_to_none(v: Any) -> Any:
    return None if isinstance(v, str) and not v.strip() else v


class RuleIn(BaseModel):
    rule_id: str
    description: str
    parameter: str
    operator: Literal[">=", "<=", ">", "<", "=", "between", "in"]
    value: str
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    effective_from: date | None = None          # defaults to the document's dates
    effective_to: date | None = None
    source_section: str
    unit: str | None = None

    _blank = field_validator("effective_from", "effective_to", "unit", mode="before")(_blank_to_none)


class IngestMetadata(BaseModel):
    """Annex B fields. Lenient on case and separators; strict on dates and authority."""
    doc_id: str
    title: str
    issuer: str
    authority_level: int = Field(ge=1, le=5)
    doc_type: str
    version: str | None = None
    effective_from: date
    effective_to: date | None = None
    supersedes: str | None = None
    scope_programmes: str = "ALL"
    scope_batches: str = "ALL"
    provenance: str = "uploaded"
    retrieved_on: date | None = None
    synthetic: Literal["Y", "N"] = "N"
    rules: list[RuleIn] = []                     # optional extension: explicit rule rows
    warnings: list[str] = []                     # filled by validators, not by clients

    _blank = field_validator("version", "effective_to", "supersedes", "retrieved_on", mode="before")(_blank_to_none)

    @field_validator("doc_id")
    @classmethod
    def _doc_id(cls, v: str) -> str:
        v = v.strip()
        if not DOC_ID_RE.match(v):
            raise ValueError("doc_id must be 2-64 characters: letters, digits, '.', '_' or '-'")
        return v

    @field_validator("synthetic", mode="before")
    @classmethod
    def _synthetic(cls, v: Any) -> str:
        s = str(v).strip().upper()
        return {"YES": "Y", "TRUE": "Y", "1": "Y", "NO": "N", "FALSE": "N", "0": "N", "": "N"}.get(s, s)

    @field_validator("scope_programmes", "scope_batches", mode="before")
    @classmethod
    def _scope(cls, v: Any) -> str:
        s = str(v or "").strip()
        if not s or s.upper() == "ALL":
            return "ALL"
        return ";".join(p.strip() for p in re.split(r"[;,|]", s) if p.strip())

    @model_validator(mode="after")
    def _normalise(self) -> "IngestMetadata":
        raw = self.doc_type.strip().lower()
        norm = DOC_TYPE_SYNONYMS.get(raw)
        if norm is None:
            self.warnings.append(f"unknown doc_type '{self.doc_type}': kept as given; authority_level decides precedence")
            norm = raw
        self.doc_type = norm
        expected = DOC_TYPE_LEVEL.get(norm)
        if expected and expected != self.authority_level:
            self.warnings.append(f"doc_type '{norm}' usually has authority level {expected}; using the declared level {self.authority_level}")
        if self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        if self.supersedes:
            self.supersedes = ";".join(p.strip() for p in re.split(r"[;,|]", self.supersedes) if p.strip())
        return self


class IngestResponse(BaseModel):
    doc_id: str
    chunks_indexed: int
    status: Literal["indexed", "replaced", "unchanged", "failed"]
    rules_added: list[str] = []
    warnings: list[str] = []


# ----------------------------------------------------------------------------- students loader
class StudentIn(BaseModel):
    student_id: str = Field(pattern=r"^S\d{4}$")
    full_name: str
    programme: str
    batch_year: int
    current_semester: int = Field(ge=1, le=10)
    cgpa: float = Field(ge=0, le=10)
    active_backlogs: int = Field(ge=0)


class CourseIn(BaseModel):
    course_code: str
    course_name: str
    programme: str
    semester: int | None = None
    credits: int | None = None

    _blank = field_validator("semester", "credits", mode="before")(_blank_to_none)


class AttendanceIn(BaseModel):
    student_id: str
    course_code: str
    classes_held: int = Field(gt=0)
    classes_attended: int = Field(ge=0)

    @model_validator(mode="after")
    def _bounds(self) -> "AttendanceIn":
        if self.classes_attended > self.classes_held:
            raise ValueError("classes_attended cannot exceed classes_held")
        return self


class ResultIn(BaseModel):
    student_id: str
    course_code: str
    exam_session: str
    exam_type: Literal["REGULAR", "SUPPLEMENTARY"]
    internal_marks: int | None = None
    external_marks: int | None = None
    total_marks: int | None = None
    max_marks: int | None = None
    result: Literal["PASS", "FAIL", "ABSENT", "DETAINED"]

    _blank = field_validator("internal_marks", "external_marks", "total_marks", "max_marks", mode="before")(_blank_to_none)

    @field_validator("exam_type", "result", mode="before")
    @classmethod
    def _upper(cls, v: Any) -> Any:
        return v.strip().upper() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _total(self) -> "ResultIn":
        if None not in (self.internal_marks, self.external_marks, self.total_marks):
            if self.total_marks != self.internal_marks + self.external_marks:
                raise ValueError("total_marks must equal internal_marks + external_marks")
        return self


class LoadRequest(BaseModel):
    students: list[dict] = []
    courses: list[dict] = []
    attendance: list[dict] = []
    results: list[dict] = []


class RejectedRow(BaseModel):
    table: str
    row: int
    reason: str


class LoadResponse(BaseModel):
    accepted: dict[str, int]
    rejected: list[RejectedRow]
    warnings: list[str] = []
