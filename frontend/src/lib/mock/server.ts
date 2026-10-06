// In-memory stand-in for the backend, used when VITE_MOCK=1. It keeps state for the session:
// new answers appear in the audit list, repeated questions come from the answer cache, follow-ups
// are rewritten within a conversation, guardrail blocks appear under security events, ingested
// documents join the register and loaded students join the identity picker.
//
// Test hook: asking "rate limit test" answers 429 RATE_LIMITED with Retry-After 12.

import { ApiError, type Api, type AskOptions } from '../api'
import type {
  AskMeta,
  AskRequest,
  AskResponse,
  AuditListItem,
  AuditRecord,
  CsvRow,
  Health,
  IngestMetadata,
  IngestResponse,
  LoadStudentsRequest,
  LoadStudentsResponse,
  RejectedRow,
  SecurityEvent,
  SourceDoc,
  StudentSummary,
} from '../types'
import { todayISO } from '../format'
import {
  CLARIFICATION_OPTIONS,
  COURSES,
  MOCK_MODEL,
  SEEDED_ASKS,
  clarificationScenario,
  conflictScenario,
  eligibilityScenario,
  guardrailScenario,
  minimumAttendanceScenario,
  notFoundScenario,
  placementScenario,
  refusedNoIdentityScenario,
  refusedOtherStudentScenario,
  sourcesFixture,
  studentsFixture,
  supplementaryScenario,
  whatIfScenario,
  type Scenario,
} from './fixtures'

const LATENCY = Number(import.meta.env.VITE_MOCK_LATENCY ?? 600)
/** Answers take longer than lookups, so the waiting state is visible in mock mode too. */
const ASK_LATENCY = Math.round(LATENCY * 2.4)
const CACHE_LATENCY = 120

function wait(ms: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new ApiError({ kind: 'aborted', path: '/ask' }))
      return
    }
    const timer = setTimeout(resolve, ms)
    signal?.addEventListener(
      'abort',
      () => {
        clearTimeout(timer)
        reject(new ApiError({ kind: 'aborted', path: '/ask' }))
      },
      { once: true },
    )
  })
}

function clone<T>(value: T): T {
  return structuredClone(value)
}

function traceId(): string {
  const bytes = new Uint8Array(4)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

function jitter(value: number, spread = 0.08): number {
  if (value < 20) return value
  return Math.round(value * (1 + (Math.random() * 2 - 1) * spread))
}

// ---------------------------------------------------------------------------------------------
// Question routing

const GUARDRAILS: [RegExp, string][] = [
  [/ignore (all |any )?(the )?(previous|prior|above) (instructions|rules)|system prompt|reveal your (instructions|prompt)/i, 'prompt_injection'],
  [/pretend (you are|to be)|you are now|act as (a|an) |jailbreak|developer mode/i, 'jailbreak'],
  [/(marks|records|attendance|results|data) of (every|all) (student|students)|every student'?s|all students'? (marks|records|data)|export (all|the) records/i, 'bulk_data'],
  [/(change|update|edit|increase|set|delete) my (attendance|marks|result|results|grade|cgpa)/i, 'record_change'],
  [/\b(idiot|stupid|useless|shut up)\b/i, 'abuse'],
]

function guardrailFor(question: string): string | null {
  for (const [pattern, reason] of GUARDRAILS) if (pattern.test(question)) return reason
  return null
}

const COURSE_CODE = /\b([A-Z]{2,3})\s?(\d{3})\b/i

function courseIn(text: string): string | null {
  const code = COURSE_CODE.exec(text)
  if (code) {
    const normalized = `${code[1]!.toUpperCase()}${code[2]}`
    if (COURSES[normalized]) return normalized
  }
  const lower = text.toLowerCase()
  for (const [course, name] of Object.entries(COURSES)) if (lower.includes(name.toLowerCase())) return course
  return null
}

function pickScenario(question: string, studentId: string | null, asOf: string): Scenario {
  const text = question.toLowerCase()
  const guard = guardrailFor(question)
  if (guard) return guardrailScenario(guard)
  const mentioned = /\bS\d{4}\b/i.exec(question)?.[0]?.toUpperCase()
  if (mentioned && mentioned !== studentId) return refusedOtherStudentScenario(studentId, mentioned)
  if (/antarctica|scholarship/.test(text)) return notFoundScenario(question)
  if (/late fee|late payment|fine for|fees? after/.test(text)) return conflictScenario()

  // Policy what-if: a stated percentage, decided by code.
  const stated = /(\d{1,3}(?:\.\d+)?)\s*%/.exec(question)
  if (stated && /attendance/.test(text) && /enough|sit|eligible|allowed|can i|qualif/.test(text)) {
    return whatIfScenario(Number(stated[1]), /medical|certificate|condon/.test(text), asOf)
  }

  const personal = /\b(i|me|my|am i|can i)\b/.test(text)
  if (/placement/.test(text)) {
    if (!studentId) return refusedNoIdentityScenario()
    return placementScenario(studentId)
  }
  if (/supplementary/.test(text) && (!personal || /how|apply|procedure|when/.test(text))) return supplementaryScenario()
  if (/eligib|can i (sit|write|appear|take)/.test(text)) {
    if (!studentId) return refusedNoIdentityScenario()
    const course = courseIn(question)
    if (!course) return clarificationScenario()
    // MA201 shows the degraded path: the model is treated as unavailable for it.
    return eligibilityScenario(studentId, course, asOf, course === 'MA201')
  }
  if (/attendance/.test(text)) return minimumAttendanceScenario(asOf)
  return notFoundScenario(question)
}

// ---------------------------------------------------------------------------------------------
// Conversations: follow-ups are rewritten into standalone questions, as the backend does.

interface Conversation {
  lastQuestion: string
  lastType: string
}

const FOLLOW_UP = /^\s*(and|also|what about|how about|same (for|with)|then|so|but|ok(ay)?,?)\b/i

function rewriteFollowUp(text: string, convo: Conversation | undefined): { standalone: string; rewrite: string } | null {
  if (!convo) return null
  const course = courseIn(text)
  // 1. The answer to a clarification: a bare option or course name.
  if (convo.lastType === 'clarification_needed') {
    const option = CLARIFICATION_OPTIONS.find((o) => o.toLowerCase() === text.trim().toLowerCase())
    const chosen = course ?? (option ? courseIn(option) : null)
    if (chosen) {
      const base = convo.lastQuestion.replace(/\?\s*$/, '')
      return { standalone: `${base} for ${COURSES[chosen]} (${chosen})?`, rewrite: 'clarification' }
    }
  }
  if (!FOLLOW_UP.test(text)) return null
  // 2. Course swap: "what about CS202?"
  const previous = courseIn(convo.lastQuestion)
  if (course && previous && course !== previous) {
    const swapped = convo.lastQuestion.replace(new RegExp(`\\b${previous}\\b`, 'i'), course)
    if (swapped !== convo.lastQuestion) return { standalone: swapped, rewrite: 'course_swap' }
  }
  // 3. Anything else that reads as a follow-up is read together with the last question.
  return { standalone: `${convo.lastQuestion.replace(/\?\s*$/, '')}, ${text.trim().replace(/^(and|also)\s+/i, '')}`, rewrite: 'concat' }
}

/** Lower-cased words without punctuation or filler, for cache matching "by meaning". */
function normalize(question: string): string {
  return question
    .toLowerCase()
    .replace(/[^a-z0-9% ]+/g, ' ')
    .split(/\s+/)
    .filter((w) => w && !['the', 'a', 'an', 'please', 'my', 'for', 'to', 'is', 'what', 'whats'].includes(w))
    .join(' ')
}

// ---------------------------------------------------------------------------------------------
// Audit records and meta, in the production shape

interface BuildContext {
  question: string
  timestamp: string
  vary: boolean
  session: { id: string | null; rewrite: string | null; original: string | null }
}

function buildAudit(response: AskResponse, scenario: Scenario, ctx: BuildContext): AuditRecord {
  const a = scenario.audit
  const breakdown = Object.fromEntries(
    Object.entries(a.latency_breakdown_ms).map(([step, ms]) => [step, ctx.vary ? jitter(ms) : ms]),
  )
  const stepsTotal = Object.values(breakdown).reduce((sum, ms) => sum + ms, 0)
  // Request handling and the audit write add a little on top of the steps.
  const latency = stepsTotal + (a.llm_calls === 0 ? 3 : ctx.vary ? jitter(131, 0.3) : 131)
  const evidence = a.sources_retrieved.filter((s) => s.label !== 'SUPERSEDED' && s.label !== 'UPCOMING')
  const setAside = a.set_aside ?? []
  const tokensIn = evidence.length * 47
  const saved = setAside.length * 41
  return {
    trace_id: response.trace_id,
    timestamp: ctx.timestamp,
    student_id: response.student_id ?? null,
    question: ctx.question,
    question_category: a.question_category,
    as_of_date: response.as_of_date,
    answer: response.answer,
    answer_type: response.answer_type,
    explanation: response.explanation,
    sources_retrieved: a.sources_retrieved,
    precedence_decision: a.precedence_decision,
    tools_invoked: response.tools_invoked,
    applied_rules: response.applied_rules,
    conflicts_detected: response.conflicts_detected,
    model: a.llm_calls > 0 ? MOCK_MODEL : null,
    llm_calls: a.llm_calls,
    llm_cache_hits: 0,
    tokens: a.token_breakdown ? a.token_breakdown.prompt + a.token_breakdown.completion : 0,
    latency_ms: latency,
    plan: {
      source: a.planner,
      category: a.planner ? a.question_category : null,
      tools: a.plan_tools,
      search_queries: a.search_queries,
      rule_parameters: response.applied_rules.flatMap((r) => (r.parameter ? [r.parameter] : [])),
    },
    retrieval: a.sources_retrieved.length
      ? {
          mode: 'hybrid',
          queries: [...a.search_queries, ...(a.glossary_expansion ?? []).map((g) => `${a.search_queries[0] ?? ''} (${g})`)],
          glossary_expansion: a.glossary_expansion ?? [],
          max_score: Math.max(...a.sources_retrieved.map((s) => s.score)),
        }
      : { mode: null, queries: [], glossary_expansion: [], max_score: null },
    context_optimisation: {
      evidence_in: evidence.length,
      evidence_out: evidence.length - setAside.length,
      evidence_tokens_in: tokensIn,
      evidence_tokens_out: tokensIn - saved,
      tokens_saved: saved,
      set_aside: setAside,
      duplicates: [],
      compressed: [],
      over_budget: [],
    },
    verification: {
      citations_valid: true,
      numbers_grounded: true,
      retries: a.retries ?? 0,
      fallback: Boolean(a.degraded),
      groundedness: a.groundedness ?? null,
      problems: a.problems ?? [],
    },
    groundedness: a.groundedness ?? null,
    unsupported_sentences: a.unsupported_sentences ?? [],
    guardrails: {
      input: { blocked: Boolean(a.guardrail), reason: a.guardrail?.reason ?? null, findings: a.guardrail?.findings ?? [] },
      output: a.output_redactions ?? [],
    },
    session: { id: ctx.session.id, rewrite: ctx.session.rewrite, original_question: ctx.session.original },
    cache: { hit: 'miss' },
    degraded: Boolean(a.degraded),
    token_breakdown: a.token_breakdown ?? undefined,
    latency_breakdown_ms: breakdown,
  }
}

function metaFor(record: AuditRecord, scenario: Scenario, session: string | null, rewrite: { standalone: string; rewrite: string } | null): AskMeta {
  const a = scenario.audit
  return {
    cache_hit: false,
    cache: 'miss',
    latency_ms: record.latency_ms,
    guardrail: a.guardrail?.reason ?? null,
    output_redactions: a.output_redactions ?? [],
    groundedness: a.groundedness ?? null,
    session_id: session,
    standalone_question: rewrite?.standalone ?? null,
    rewrite: rewrite?.rewrite ?? null,
    degraded: Boolean(a.degraded),
    planner: a.planner,
    retrieval: a.sources_retrieved.length ? 'hybrid' : null,
    llm_calls: a.llm_calls,
    tokens: record.tokens,
    tokens_saved: record.context_optimisation?.tokens_saved ?? 0,
  }
}

/** A cache hit: a new file that copies an earlier answer, served without the pipeline. */
function cachedCopy(source: AuditRecord, response: AskResponse, kind: 'exact' | 'semantic', ctx: BuildContext): AuditRecord {
  return {
    ...clone(source),
    trace_id: response.trace_id,
    timestamp: ctx.timestamp,
    question: ctx.question,
    llm_calls: 0,
    llm_cache_hits: 0,
    tokens: 0,
    token_breakdown: { prompt: 0, completion: 0 },
    latency_ms: kind === 'exact' ? 2 : 5,
    latency_breakdown_ms: { cache: kind === 'exact' ? 2 : 5 },
    session: { id: ctx.session.id, rewrite: ctx.session.rewrite, original_question: ctx.session.original },
    cache: { hit: kind, source_trace_id: source.trace_id, ...(kind === 'semantic' ? { similarity: 0.95 } : {}) },
  }
}

// ---------------------------------------------------------------------------------------------
// Student loader validation

const REQUIRED_COLUMNS: Record<string, string[]> = {
  courses: ['course_code', 'course_name', 'programme', 'semester', 'credits'],
  students: ['student_id', 'full_name', 'programme', 'batch_year', 'current_semester', 'cgpa'],
  attendance: ['student_id', 'course_code', 'classes_held', 'classes_attended'],
  results: ['student_id', 'course_code', 'exam_session', 'exam_type', 'max_marks', 'result'],
}

function intOf(row: CsvRow, key: string): number | null {
  const raw = row[key]
  if (raw === null || raw === undefined || !/^-?\d+$/.test(raw.trim())) return null
  return Number(raw)
}

function validateRow(table: string, row: CsvRow): string | null {
  const missing = (REQUIRED_COLUMNS[table] ?? []).filter((c) => row[c] === null || row[c] === undefined)
  if (missing.length) return `missing ${missing.join(', ')}`
  switch (table) {
    case 'students': {
      if (!/^S\d{4}$/.test(row.student_id ?? '')) return `student_id ${row.student_id} is not S followed by 4 digits`
      const sem = intOf(row, 'current_semester')
      if (sem === null || sem < 1 || sem > 10) return 'current_semester must be a whole number from 1 to 10'
      const cgpa = Number(row.cgpa)
      if (!Number.isFinite(cgpa) || cgpa < 0 || cgpa > 10) return `cgpa ${row.cgpa} is outside 0 to 10`
      return null
    }
    case 'attendance': {
      const held = intOf(row, 'classes_held')
      const attended = intOf(row, 'classes_attended')
      if (held === null || held <= 0) return 'classes_held must be a whole number above 0'
      if (attended === null || attended < 0) return 'classes_attended must be a whole number, 0 or more'
      if (attended > held) return `classes_attended ${attended} is more than classes_held ${held}`
      return null
    }
    case 'results': {
      const internal = intOf(row, 'internal_marks')
      const external = intOf(row, 'external_marks')
      const total = intOf(row, 'total_marks')
      if (!['REGULAR', 'SUPPLEMENTARY'].includes((row.exam_type ?? '').toUpperCase())) return 'exam_type must be REGULAR or SUPPLEMENTARY'
      if (internal !== null && external !== null && total !== null && internal + external !== total) {
        return `total_marks ${total} does not equal internal ${internal} + external ${external}`
      }
      return null
    }
    case 'courses': {
      const credits = intOf(row, 'credits')
      if (credits === null || credits <= 0) return 'credits must be a whole number above 0'
      return null
    }
    default:
      return null
  }
}

// ---------------------------------------------------------------------------------------------

export function createMockApi(): Api {
  const students: StudentSummary[] = clone(studentsFixture)
  const sources: SourceDoc[] = clone(sourcesFixture)
  const audit = new Map<string, AuditRecord>()
  const order: string[] = []
  const today = todayISO()
  const fileFingerprints = new Map<string, string>()
  const conversations = new Map<string, Conversation>()
  const answerCache = new Map<string, { exactQuestion: string; traceId: string; response: AskResponse }>()
  const events: SecurityEvent[] = []
  let llmCacheEntries = 37

  const recordEvent = (kind: string, detail: unknown, studentId: string | null, trace: string | null, minutesAgo = 0) => {
    events.unshift({
      id: events.length + 1,
      ts: new Date(Date.now() - minutesAgo * 60_000).toISOString(),
      client: '127.0.0.1',
      student_id: studentId,
      kind,
      detail,
      trace_id: trace,
    })
  }

  // Seed the audit log, oldest first, so cache copies can point at their source.
  for (const seed of [...SEEDED_ASKS].reverse()) {
    const scenario = seed.build(today)
    const response: AskResponse = { ...clone(scenario.response), trace_id: seed.trace_id, as_of_date: today, student_id: seed.student_id }
    const timestamp = new Date(Date.now() - seed.minutesAgo * 60_000).toISOString()
    const ctx: BuildContext = {
      question: seed.question,
      timestamp,
      vary: false,
      session: { id: seed.typed?.session ?? null, rewrite: seed.typed?.rewrite ?? null, original: seed.typed?.text ?? null },
    }
    const source = seed.copyOf ? audit.get(seed.copyOf) : undefined
    const record = source ? cachedCopy(source, response, 'exact', ctx) : buildAudit(response, scenario, ctx)
    audit.set(seed.trace_id, record)
    order.unshift(seed.trace_id)
    if (scenario.audit.guardrail && scenario.audit.guardrail.reason !== 'other_student') {
      recordEvent('guardrail_block', { reason: scenario.audit.guardrail.reason }, seed.student_id, seed.trace_id, seed.minutesAgo)
    }
  }
  recordEvent('rate_limited', { retry_after_s: 11.4 }, null, null, 44)
  recordEvent('output_redaction', { removed: ['other_student_id'] }, 'S1004', '6b0d2e91', 52)

  return {
    async ask(body: AskRequest, options: AskOptions = {}) {
      const question = body.question.trim()
      if (/^rate limit test$/i.test(question)) {
        await wait(150, options.signal)
        recordEvent('rate_limited', { retry_after_s: 11.2 }, options.studentId ?? null, null)
        throw new ApiError({
          kind: 'http',
          status: 429,
          path: '/ask',
          code: 'RATE_LIMITED',
          detail: 'Too many requests from this client. Wait and try again.',
          hint: 'Wait a moment and retry.',
          traceId: traceId(),
          retryAfter: 12,
        })
      }
      if (!question) {
        throw new ApiError({ kind: 'http', status: 422, path: '/ask', code: 'INVALID_REQUEST', detail: 'question: String should have at least 1 character' })
      }
      const asOf = body.as_of_date ?? today
      const studentId = options.studentId ?? null
      const sessionKey = options.sessionId ? `${options.sessionId}|${studentId ?? ''}` : null
      const convo = sessionKey ? conversations.get(sessionKey) : undefined

      const rewrite = guardrailFor(question) ? null : rewriteFollowUp(question, convo)
      const standalone = rewrite?.standalone ?? question
      const ctx: BuildContext = {
        question: standalone,
        timestamp: new Date().toISOString(),
        vary: true,
        session: { id: options.sessionId ?? null, rewrite: rewrite?.rewrite ?? null, original: rewrite ? question : null },
      }

      // The answer cache: the same question (or the same words, reordered or reworded slightly).
      const cacheKey = `${studentId ?? ''}|${asOf}|${normalize(standalone)}`
      const cached = guardrailFor(standalone) ? undefined : answerCache.get(cacheKey)
      if (cached) {
        await wait(CACHE_LATENCY, options.signal)
        const kind = cached.exactQuestion === standalone ? 'exact' : 'semantic'
        const response: AskResponse = { ...clone(cached.response), trace_id: traceId() }
        const source = audit.get(cached.traceId)
        if (source) {
          audit.set(response.trace_id, cachedCopy(source, response, kind, ctx))
          order.unshift(response.trace_id)
        }
        response.meta = {
          ...(response.meta as AskMeta),
          cache_hit: true,
          cache: kind,
          latency_ms: kind === 'exact' ? 2 : 5,
          llm_calls: 0,
          tokens: 0,
          tokens_saved: 0,
          session_id: options.sessionId ?? null,
          standalone_question: rewrite?.standalone ?? null,
          rewrite: rewrite?.rewrite ?? null,
        }
        if (sessionKey) conversations.set(sessionKey, { lastQuestion: standalone, lastType: response.answer_type })
        return response
      }

      await wait(ASK_LATENCY, options.signal)
      const scenario = pickScenario(standalone, studentId, asOf)
      const response: AskResponse = { ...clone(scenario.response), trace_id: traceId(), as_of_date: asOf, student_id: studentId }
      const record = buildAudit(response, scenario, ctx)
      response.meta = metaFor(record, scenario, options.sessionId ?? null, rewrite)
      audit.set(response.trace_id, record)
      order.unshift(response.trace_id)
      llmCacheEntries += scenario.audit.llm_calls

      const guard = scenario.audit.guardrail
      if (guard && guard.reason !== 'other_student') {
        recordEvent('guardrail_block', { reason: guard.reason }, studentId, response.trace_id)
      }
      if (sessionKey && !guard) conversations.set(sessionKey, { lastQuestion: standalone, lastType: response.answer_type })
      if (!guard && response.answer_type !== 'clarification_needed' && !scenario.audit.degraded) {
        answerCache.set(cacheKey, { exactQuestion: standalone, traceId: response.trace_id, response: clone(response) })
      }
      return clone(response)
    },

    async health(): Promise<Health> {
      await wait(LATENCY / 3)
      return {
        status: 'ok',
        components: {
          api: { status: 'ok' },
          vector_store: { status: 'ok', chunks: sources.reduce((n, d) => n + d.chunks_indexed, 0), collection: 'uniassist_chunks' },
          sqlite: { status: 'ok', students: students.length, rules: 14, documents: sources.length },
          llm: { status: 'ok', provider: 'ollama', model: MOCK_MODEL, breaker: 'closed', fallbacks: [], cloud_fallback_enabled: false },
        },
        config: {
          retrieval_mode: 'hybrid',
          reranker: 'none',
          planner: 'auto',
          context_budget_tokens: 1400,
          llm_fallback_model: null,
          cloud_fallback: false,
          llm_model: MOCK_MODEL,
        },
        caches: {
          answers: answerCache.size,
          llm: llmCacheEntries,
          data_version: '2',
          enabled: { answers: true, semantic: true, llm: true },
        },
        security: { rate_limit_per_min: 30, abuse_block_after: 5, input_guardrails: true, output_guardrails: true },
      }
    },

    async sources() {
      await wait(LATENCY)
      return { documents: clone(sources) }
    },

    async ingest(file: File, metadata: IngestMetadata): Promise<IngestResponse> {
      await wait(LATENCY * 2)
      if (!/^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$/.test(metadata.doc_id)) {
        throw new ApiError({
          kind: 'http',
          status: 400,
          path: '/ingest',
          code: 'INVALID_METADATA',
          detail: 'doc_id must be 2-64 characters: letters, digits, dots, dashes or underscores',
          hint: 'Send metadata as a JSON object with the Annex B fields (doc_id, title, issuer, authority_level, doc_type, effective_from, ...).',
          traceId: traceId(),
        })
      }
      const fingerprint = `${file.name}:${file.size}:${JSON.stringify(metadata)}`
      const existing = sources.findIndex((d) => d.doc_id === metadata.doc_id)
      const chunks = Math.max(1, Math.round(file.size / 2400))
      const warnings: string[] = []
      if (/\.(png|jpe?g)$/i.test(file.name)) warnings.push('Image file: the text was read with OCR. Check figures against the original.')
      const expectedType = ['regulation', 'circular', 'notice', 'faq', 'unofficial'][metadata.authority_level - 1]
      if (expectedType && expectedType !== metadata.doc_type && !(metadata.authority_level === 4 && metadata.doc_type === 'handbook')) {
        warnings.push(`doc_type ${metadata.doc_type} is unusual for authority level ${metadata.authority_level}; stored as given.`)
      }
      if (existing >= 0 && fileFingerprints.get(metadata.doc_id) === fingerprint) {
        return { doc_id: metadata.doc_id, chunks_indexed: 0, status: 'unchanged', rules_added: [], warnings }
      }
      fileFingerprints.set(metadata.doc_id, fingerprint)
      // New documents change what answers say, so the answer cache is emptied (data version bump).
      answerCache.clear()
      const rulesAdded = /attendance/i.test(metadata.title) ? [`ATT-MIN-${String(sources.length + 1).padStart(2, '0')}`] : []
      const doc: SourceDoc = { ...metadata, chunks_indexed: chunks, ingested_at: new Date().toISOString(), warnings }
      if (existing >= 0) sources.splice(existing, 1, doc)
      else sources.push(doc)
      return { doc_id: metadata.doc_id, chunks_indexed: chunks, status: existing >= 0 ? 'replaced' : 'indexed', rules_added: rulesAdded, warnings }
    },

    async students() {
      await wait(LATENCY / 2)
      return { students: clone(students) }
    },

    async loadStudents(body: LoadStudentsRequest): Promise<LoadStudentsResponse> {
      await wait(LATENCY * 1.5)
      const accepted: Record<string, number> = {}
      const rejected: RejectedRow[] = []
      for (const table of ['courses', 'students', 'attendance', 'results'] as const) {
        const rows = body[table]
        if (!rows) continue
        accepted[table] = 0
        rows.forEach((row, index) => {
          const reason = validateRow(table, row)
          if (reason) {
            rejected.push({ table, row: index + 1, reason })
            return
          }
          accepted[table] = (accepted[table] ?? 0) + 1
          if (table === 'students') {
            const summary: StudentSummary = {
              student_id: row.student_id ?? '',
              full_name: row.full_name ?? '',
              programme: row.programme ?? '',
              batch_year: Number(row.batch_year),
              current_semester: Number(row.current_semester),
            }
            const at = students.findIndex((s) => s.student_id === summary.student_id)
            if (at >= 0) students.splice(at, 1, summary)
            else students.push(summary)
          }
        })
      }
      students.sort((a, b) => a.student_id.localeCompare(b.student_id))
      answerCache.clear()
      return { accepted, rejected }
    },

    async auditList(limit = 30) {
      await wait(LATENCY)
      const items: AuditListItem[] = order.slice(0, limit).flatMap((id) => {
        const r = audit.get(id)
        return r
          ? [{ trace_id: r.trace_id, timestamp: r.timestamp, student_id: r.student_id, question: r.question, answer_type: r.answer_type, latency_ms: r.latency_ms }]
          : []
      })
      return { items }
    },

    async auditRecord(id: string) {
      await wait(LATENCY)
      const record = audit.get(id)
      if (!record) {
        throw new ApiError({
          kind: 'http',
          status: 404,
          path: `/audit/${id}`,
          code: 'NOT_FOUND',
          detail: `no audit record for trace_id ${id}`,
          hint: 'Check the identifier.',
          traceId: traceId(),
        })
      }
      return clone(record)
    },

    async securityEvents(limit = 50) {
      await wait(LATENCY)
      return { events: clone(events.slice(0, limit)) }
    },
  }
}
