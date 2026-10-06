// API contract shared with the FastAPI backend. Field names match the backend exactly.

export type AnswerType =
  | 'retrieved_fact'
  | 'calculated'
  | 'not_found'
  | 'clarification_needed'
  | 'refused'
  | 'conflict_flagged'

export interface Citation {
  doc_id: string
  title: string
  section: string | null
  page: number | null
  version: string | null
  effective_from: string
  quote?: string | null
}

export interface ToolInvocation {
  tool: string
  input: Record<string, unknown>
  output: Record<string, unknown>
  status?: 'ok' | 'error'
  ms?: number | null
}

export interface AppliedRule {
  rule_id: string
  value: string
  source_doc_id: string
  source_section?: string | null
  parameter?: string | null
}

export interface SourceRef {
  doc_id: string
  section?: string | null
  value?: string | null
  title?: string | null
  reason?: string | null
  effective_from?: string | null
}

export type ResolvedBy = 'step2_supersession' | 'step3_authority' | 'step4_recency' | null

export interface ConflictRecord {
  topic: string
  winner: SourceRef | null
  others: SourceRef[]
  resolved_by: ResolvedBy
}

export interface AskRequest {
  question: string
  /** YYYY-MM-DD */
  as_of_date?: string
}

export type CacheKind = 'miss' | 'exact' | 'semantic'

/** How a follow-up was turned into a standalone question. */
export type RewriteKind = 'clarification' | 'course_swap' | 'llm' | 'concat'

/** Additive: how the answer was produced. Older backends omit it, so it is optional here. */
export interface AskMeta {
  cache_hit: boolean
  cache: CacheKind
  latency_ms: number
  /** The input guardrail that blocked the question, e.g. prompt_injection. */
  guardrail: string | null
  /** What the output guardrail removed, e.g. other_student_id. */
  output_redactions: string[]
  /** Share of answer sentences supported by the sources, 0 to 1. */
  groundedness: number | null
  session_id: string | null
  standalone_question: string | null
  rewrite: RewriteKind | string | null
  /** The language model was unavailable; rules and records answered with a template. */
  degraded: boolean
  /** llm | router | router_first | router_fallback */
  planner: string | null
  /** dense | hybrid | hybrid+rerank */
  retrieval: string | null
  llm_calls: number
  tokens: number
  tokens_saved: number
}

export interface AskResponse {
  trace_id: string
  answer: string
  answer_type: AnswerType
  citations: Citation[]
  tools_invoked: ToolInvocation[]
  applied_rules: AppliedRule[]
  conflicts_detected: ConflictRecord[]
  explanation: string
  as_of_date: string
  upcoming_changes: SourceRef[]
  clarification_options: string[]
  student_id?: string | null
  meta?: AskMeta
}

export type IngestStatus = 'indexed' | 'replaced' | 'unchanged' | 'failed'

export interface IngestResponse {
  doc_id: string
  chunks_indexed: number
  status: IngestStatus
  rules_added: string[]
  warnings: string[]
}

export type DocType = 'regulation' | 'circular' | 'notice' | 'faq' | 'handbook' | 'unofficial'

/** Annex B metadata, sent as a JSON string in the `metadata` field of POST /ingest. */
export interface IngestMetadata {
  doc_id: string
  title: string
  issuer: string
  authority_level: number
  doc_type: DocType
  version: string | null
  effective_from: string
  effective_to: string | null
  supersedes: string | null
  scope_programmes: string
  scope_batches: string
  provenance: string
  retrieved_on: string | null
  synthetic: 'Y' | 'N'
}

export type ComponentStatus = { status: string; detail?: string }

export interface Health {
  status: 'ok' | 'degraded' | 'down'
  components: {
    api: ComponentStatus
    vector_store: ComponentStatus & { chunks?: number; collection?: string }
    sqlite: ComponentStatus & { students?: number; rules?: number; documents?: number }
    llm: ComponentStatus & {
      provider?: string
      model?: string
      /** closed = normal; open = the model is skipped; half_open = trying it again */
      breaker?: 'closed' | 'open' | 'half_open' | string
      fallbacks?: string[]
      cloud_fallback_enabled?: boolean
    }
  }
  config?: {
    retrieval_mode?: string
    reranker?: string | null
    planner?: string
    context_budget_tokens?: number
    llm_fallback_model?: string | null
    cloud_fallback?: boolean
    llm_model?: string
    embedder?: string
  }
  caches?: {
    answers: number
    llm: number
    data_version?: string
    enabled: { answers: boolean; semantic: boolean; llm: boolean }
  }
  security?: {
    rate_limit_per_min: number | null
    abuse_block_after: number | null
    input_guardrails: boolean
    output_guardrails: boolean
  }
}

export interface SourceDoc {
  doc_id: string
  title: string
  issuer: string
  authority_level: number
  doc_type: string
  version: string | null
  effective_from: string
  effective_to: string | null
  supersedes: string | null
  scope_programmes: string
  scope_batches: string
  provenance: string
  retrieved_on: string | null
  synthetic: 'Y' | 'N'
  chunks_indexed: number
  ingested_at: string
  warnings: string[]
}

export interface SourcesResponse {
  documents: SourceDoc[]
}

export interface StudentSummary {
  student_id: string
  full_name: string
  programme: string
  batch_year: number
  current_semester: number
}

export interface StudentsResponse {
  students: StudentSummary[]
}

/** One CSV row, keyed by header. Empty cells are sent as null. */
export type CsvRow = Record<string, string | null>

export type StudentTable = 'students' | 'courses' | 'attendance' | 'results'

export type LoadStudentsRequest = Partial<Record<StudentTable, CsvRow[]>>

export interface RejectedRow {
  table: string
  row: number
  reason: string
}

export interface LoadStudentsResponse {
  accepted: Record<string, number>
  rejected: RejectedRow[]
  warnings?: string[]
}

export interface AuditListItem {
  trace_id: string
  timestamp: string
  student_id: string | null
  question: string
  answer_type: AnswerType
  latency_ms: number
}

export interface AuditListResponse {
  items: AuditListItem[]
}

export interface RetrievedSource {
  doc_id: string
  section: string | null
  score: number
  label?: string
  /** Fetched because a rule cites this clause, not only by search. */
  anchor?: boolean
  dense_rank?: number | null
  bm25_rank?: number | null
  rerank?: number | null
}

export interface AuditRecord {
  trace_id: string
  timestamp: string
  student_id: string | null
  question: string
  question_category: string | null
  as_of_date: string
  answer: string
  answer_type: AnswerType
  sources_retrieved: RetrievedSource[]
  precedence_decision: string
  tools_invoked: ToolInvocation[]
  applied_rules: AppliedRule[]
  conflicts_detected: ConflictRecord[]
  model: string | null
  llm_calls: number
  tokens: number
  latency_ms: number
  /** source: llm, router, router_first, router_fallback, or null when the question stopped before planning. */
  plan?: {
    source: string | null
    category: string | null
    tools: string[]
    search_queries: string[]
    rule_parameters?: string[]
  }
  verification?: {
    citations_valid: boolean
    numbers_grounded: boolean
    retries: number
    fallback: boolean
    groundedness?: number | null
    problems?: string[]
  }
  token_breakdown?: { prompt: number; completion: number }
  latency_breakdown_ms?: Record<string, number>
  // Additive fields from the production backend.
  explanation?: string
  llm_cache_hits?: number
  retrieval?: { mode: string | null; queries: string[]; glossary_expansion: string[]; max_score: number | null }
  context_optimisation?: {
    evidence_in: number
    evidence_out: number
    evidence_tokens_in: number
    evidence_tokens_out: number
    tokens_saved: number
    /** "DOC#section" references. */
    set_aside: string[]
    duplicates: string[]
    compressed: string[]
    over_budget: string[]
  }
  groundedness?: number | null
  unsupported_sentences?: string[]
  guardrails?: {
    input: { blocked: boolean; reason: string | null; findings: string[] }
    output: string[]
  }
  session?: { id: string | null; rewrite: string | null; original_question: string | null }
  /** hit is "miss" | "exact" | "semantic" (older records: a boolean). */
  cache?: { hit: boolean | string; source_trace_id?: string | null; similarity?: number | null }
  degraded?: boolean
}

export interface SecurityEvent {
  id: number
  ts: string
  client: string
  student_id: string | null
  /** guardrail_block | output_redaction | rate_limited | client_blocked */
  kind: string
  detail: unknown
  trace_id: string | null
}

export interface SecurityEventsResponse {
  events: SecurityEvent[]
}
