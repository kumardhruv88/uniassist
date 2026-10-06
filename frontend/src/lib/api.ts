// Typed client for the UniAssist backend. Every request goes through `request()`, which turns
// network failures, timeouts and error bodies into an ApiError with a usable message.
// With VITE_MOCK=1 the same interface is served from local fixtures (see ./mock/server.ts).

import type {
  AskRequest,
  AskResponse,
  AuditListResponse,
  AuditRecord,
  Health,
  IngestMetadata,
  IngestResponse,
  LoadStudentsRequest,
  LoadStudentsResponse,
  SecurityEventsResponse,
  SourcesResponse,
  StudentsResponse,
} from './types'

const BASE = (import.meta.env.VITE_API_BASE ?? '/api').replace(/\/+$/, '')

/** True when the UI runs on built-in fixtures instead of the backend. */
export const MOCK_MODE = import.meta.env.VITE_MOCK === '1'

/** Answers from a local model can take 5-15 s, ingestion longer; nginx allows 120 s. */
const SLOW_TIMEOUT_MS = 120_000
const DEFAULT_TIMEOUT_MS = 20_000

export type ApiErrorKind = 'network' | 'timeout' | 'aborted' | 'http' | 'parse'

export class ApiError extends Error {
  readonly kind: ApiErrorKind
  readonly status: number | null
  /** A readable message from the body (FastAPI `detail`, or the envelope's `error.message`). */
  readonly detail: string | null
  /** Stable code from the error envelope, e.g. RATE_LIMITED or LLM_UNAVAILABLE. */
  readonly code: string | null
  /** The backend's suggestion for what to do next. */
  readonly hint: string | null
  /** The backend's trace id for this failure, to quote when reporting it. */
  readonly traceId: string | null
  /** Seconds to wait, from the Retry-After header. */
  readonly retryAfter: number | null
  readonly body: unknown
  readonly path: string
  readonly timeoutMs: number | null

  constructor(init: {
    kind: ApiErrorKind
    path: string
    status?: number | null
    detail?: string | null
    code?: string | null
    hint?: string | null
    traceId?: string | null
    retryAfter?: number | null
    body?: unknown
    timeoutMs?: number | null
  }) {
    super(init.detail ?? init.kind)
    this.name = 'ApiError'
    this.kind = init.kind
    this.path = init.path
    this.status = init.status ?? null
    this.detail = init.detail ?? null
    this.code = init.code ?? null
    this.hint = init.hint ?? null
    this.traceId = init.traceId ?? null
    this.retryAfter = init.retryAfter ?? null
    this.body = init.body ?? null
    this.timeoutMs = init.timeoutMs ?? null
  }
}

function formatValidationErrors(errors: unknown[]): string {
  return errors
    .map((item) => {
      if (!item || typeof item !== 'object') return String(item)
      const { loc, msg } = item as { loc?: unknown; msg?: unknown }
      const field = Array.isArray(loc) ? loc.filter((p) => p !== 'body').join('.') : ''
      return field ? `${field}: ${String(msg)}` : String(msg)
    })
    .join('; ')
}

/**
 * Reads the error body. The backend sends {"error": {code, message, hint, trace_id}, "detail": ...},
 * where `detail` keeps FastAPI's shapes: a string, [{"loc", "msg"}], or {"message", "errors"}.
 */
function detailFrom(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null
  const detail = (body as { detail?: unknown }).detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return formatValidationErrors(detail)
  if (detail && typeof detail === 'object') {
    const { message, errors } = detail as { message?: unknown; errors?: unknown }
    const list = Array.isArray(errors) && errors.length ? formatValidationErrors(errors) : null
    if (typeof message === 'string') return list ? `${message.replace(/\.?$/, ':')} ${list}` : message
    if (list) return list
  }
  const envelope = envelopeFrom(body)
  if (envelope.message) return envelope.message
  const message = (body as { message?: unknown }).message
  return typeof message === 'string' ? message : null
}

function envelopeFrom(body: unknown): { code: string | null; message: string | null; hint: string | null; traceId: string | null } {
  const error = body && typeof body === 'object' ? (body as { error?: unknown }).error : null
  if (!error || typeof error !== 'object') return { code: null, message: null, hint: null, traceId: null }
  const e = error as Record<string, unknown>
  const text = (v: unknown) => (typeof v === 'string' && v.trim() ? v.trim() : null)
  return { code: text(e.code), message: text(e.message), hint: text(e.hint), traceId: text(e.trace_id) }
}

/** Retry-After is either seconds or an HTTP date. */
function retryAfterFrom(response: Response): number | null {
  const raw = response.headers.get('Retry-After')
  if (!raw) return null
  const seconds = Number(raw)
  if (Number.isFinite(seconds)) return Math.max(0, Math.ceil(seconds))
  const at = Date.parse(raw)
  return Number.isNaN(at) ? null : Math.max(0, Math.ceil((at - Date.now()) / 1000))
}

interface RequestOptions extends RequestInit {
  timeoutMs?: number
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, signal: outer, headers, ...init } = options
  const controller = new AbortController()
  let timedOut = false
  const timer = setTimeout(() => {
    timedOut = true
    controller.abort()
  }, timeoutMs)
  const forwardAbort = () => controller.abort()
  if (outer?.aborted) controller.abort()
  outer?.addEventListener('abort', forwardAbort, { once: true })

  try {
    const response = await fetch(`${BASE}${path}`, {
      ...init,
      signal: controller.signal,
      headers: { Accept: 'application/json', ...headers },
    })
    const text = await response.text()
    let body: unknown = null
    if (text) {
      try {
        body = JSON.parse(text)
      } catch {
        if (response.ok) throw new ApiError({ kind: 'parse', path, status: response.status })
        body = text
      }
    }
    if (!response.ok) {
      const detail = detailFrom(body) ?? (typeof body === 'string' ? body.slice(0, 240) : null)
      const envelope = envelopeFrom(body)
      throw new ApiError({
        kind: 'http',
        path,
        status: response.status,
        detail,
        code: envelope.code,
        hint: envelope.hint,
        traceId: envelope.traceId,
        retryAfter: retryAfterFrom(response),
        body,
      })
    }
    return body as T
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (controller.signal.aborted) {
      throw new ApiError({ kind: timedOut ? 'timeout' : 'aborted', path, timeoutMs })
    }
    throw new ApiError({ kind: 'network', path, detail: error instanceof Error ? error.message : null })
  } finally {
    clearTimeout(timer)
    outer?.removeEventListener('abort', forwardAbort)
  }
}

/** True when the backend could not be reached at all (dev proxy or nginx answer for it). */
export function isUnreachable(error: unknown): boolean {
  if (!(error instanceof ApiError)) return false
  if (error.kind === 'network') return true
  if (error.kind !== 'http') return false
  // nginx answers 502/504 when the api container is down; Vite's dev proxy answers an empty 500.
  return error.status === 502 || error.status === 504 || (error.status === 500 && !error.detail)
}

/** True when the backend refused because the admin token was missing or wrong. */
export function isAuthError(error: unknown): boolean {
  return error instanceof ApiError && error.kind === 'http' && (error.status === 401 || error.status === 403)
}

/** Queries retry once on network trouble or a 5xx, never on a 4xx: a 429 must wait, not hammer. */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (failureCount >= 1) return false
  if (!(error instanceof ApiError)) return true
  if (error.kind === 'aborted') return false
  return !(error.kind === 'http' && error.status !== null && error.status < 500)
}

export interface ErrorExplanation {
  /** What happened and what to do, in one or two sentences. */
  message: string
  /** The backend's own hint, when it adds something. */
  hint: string | null
  code: string | null
  traceId: string | null
  /** Seconds to wait before trying again (429). */
  retryAfter: number | null
  unreachable: boolean
}

function seconds(n: number): string {
  return `${n} ${n === 1 ? 'second' : 'seconds'}`
}

/** Turns any failure into words: what happened, what to do, and the backend's hint and trace id. */
export function explainError(error: unknown, action = 'the request'): ErrorExplanation {
  const base = { hint: null, code: null, traceId: null, retryAfter: null, unreachable: false }
  if (!(error instanceof ApiError)) {
    return { ...base, message: `Something went wrong with ${action}: ${error instanceof Error ? error.message : String(error)}.` }
  }
  const extra = { code: error.code, traceId: error.traceId, retryAfter: error.retryAfter }
  if (isUnreachable(error)) {
    return {
      ...base,
      unreachable: true,
      message: 'The UniAssist API did not respond. Start the backend on port 8000 (or run the UI with VITE_MOCK=1), then try again.',
    }
  }
  switch (error.kind) {
    case 'timeout':
      return {
        ...base,
        message: `The API took longer than ${Math.round((error.timeoutMs ?? 0) / 1000)} seconds. The language model may still be loading; try again in a minute.`,
      }
    case 'aborted':
      return { ...base, message: 'The request was cancelled.' }
    case 'parse':
      return { ...base, message: 'The API sent a reply the UI could not read. Check that /api points at the UniAssist backend.' }
    case 'network':
      return { ...base, unreachable: true, message: 'The UniAssist API did not respond. Check your connection and that the backend is running.' }
    case 'http':
      break
  }

  const detail = error.detail ? ` ${error.detail.replace(/\.?$/, '.')}` : ''
  const wait = error.retryAfter !== null ? seconds(error.retryAfter) : 'a minute'
  let message: string
  let hint = error.hint
  switch (error.status) {
    case 429:
      // Our sentence already says how long to wait, so the backend's generic hint is left out.
      hint = null
      message =
        error.code === 'CLIENT_BLOCKED'
          ? `This browser is blocked for now after several blocked questions. Wait ${wait}, then try again.`
          : `Too many requests from this browser in a short time. Wait ${wait}, then try again.`
      break
    case 400:
    case 422:
      message = `The API rejected ${action}.${detail} Correct it and try again.`
      break
    case 401:
    case 403:
      hint = null
      message = 'This server only accepts that from an administrator. Enter the admin token the backend was started with, then try again.'
      break
    case 404:
      message = `The API has no record of that.${detail}`
      break
    case 413:
      message = 'The file is too large for the server. Upload a file under 25 MB.'
      break
    case 503:
      message = `The API is running but not ready.${detail}`
      hint ??= 'Check the system status panel, then try again.'
      break
    default:
      message = `The API failed with error ${error.status ?? 'unknown'}.${detail}`
      hint ??= 'Try again; if it repeats, check the API logs.'
  }
  return { ...base, ...extra, hint, message }
}

/** A sentence that says what happened and what to do about it. */
export function describeError(error: unknown, action = 'the request'): string {
  const { message, hint } = explainError(error, action)
  return hint ? `${message} ${hint}` : message
}

export interface AskOptions {
  studentId?: string | null
  /** Conversation id, so follow-up questions are read with the earlier ones. */
  sessionId?: string | null
  signal?: AbortSignal
}

export interface Api {
  ask(body: AskRequest, options?: AskOptions): Promise<AskResponse>
  health(): Promise<Health>
  sources(): Promise<SourcesResponse>
  /** `adminToken` is sent as X-Admin-Token when the backend was started with one. */
  ingest(file: File, metadata: IngestMetadata, adminToken?: string): Promise<IngestResponse>
  students(): Promise<StudentsResponse>
  loadStudents(body: LoadStudentsRequest, adminToken?: string): Promise<LoadStudentsResponse>
  auditList(limit?: number): Promise<AuditListResponse>
  auditRecord(traceId: string): Promise<AuditRecord>
  securityEvents(limit?: number, adminToken?: string): Promise<SecurityEventsResponse>
}

function adminHeaders(adminToken?: string): Record<string, string> {
  const token = adminToken?.trim()
  return token ? { 'X-Admin-Token': token } : {}
}

function looksLikeHealth(body: unknown): body is Health {
  return Boolean(body && typeof body === 'object' && 'components' in body)
}

const httpApi: Api = {
  ask(body, { studentId, sessionId, signal } = {}) {
    return request<AskResponse>('/ask', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(studentId ? { 'X-Student-Id': studentId } : {}),
        ...(sessionId ? { 'X-Session-Id': sessionId } : {}),
      },
      body: JSON.stringify(body),
      signal,
      timeoutMs: SLOW_TIMEOUT_MS,
    })
  },
  async health() {
    try {
      return await request<Health>('/health', { timeoutMs: 8_000 })
    } catch (error) {
      // A degraded backend may answer 503 with the same body; it is still a status report.
      if (error instanceof ApiError && error.kind === 'http' && looksLikeHealth(error.body)) return error.body
      throw error
    }
  },
  sources() {
    return request<SourcesResponse>('/sources')
  },
  ingest(file, metadata, adminToken) {
    const form = new FormData()
    form.append('file', file, file.name)
    form.append('metadata', JSON.stringify(metadata))
    return request<IngestResponse>('/ingest', {
      method: 'POST',
      headers: adminHeaders(adminToken),
      body: form,
      timeoutMs: SLOW_TIMEOUT_MS,
    })
  },
  students() {
    return request<StudentsResponse>('/students')
  },
  loadStudents(body, adminToken) {
    return request<LoadStudentsResponse>('/admin/students/load', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...adminHeaders(adminToken) },
      body: JSON.stringify(body),
      timeoutMs: SLOW_TIMEOUT_MS,
    })
  },
  auditList(limit = 30) {
    return request<AuditListResponse>(`/audit?limit=${limit}`)
  },
  auditRecord(traceId) {
    return request<AuditRecord>(`/audit/${encodeURIComponent(traceId)}`)
  },
  securityEvents(limit = 50, adminToken) {
    return request<SecurityEventsResponse>(`/security/events?limit=${limit}`, { headers: adminHeaders(adminToken) })
  },
}

// The mock module is only imported in mock mode, so it never ships in a normal build.
let mockApi: Promise<Api> | null = null
function loadMock(): Promise<Api> {
  mockApi ??= import('./mock/server').then((m) => m.createMockApi())
  return mockApi
}

const mockedApi: Api = {
  ask: (body, options) => loadMock().then((m) => m.ask(body, options)),
  health: () => loadMock().then((m) => m.health()),
  sources: () => loadMock().then((m) => m.sources()),
  ingest: (file, metadata, adminToken) => loadMock().then((m) => m.ingest(file, metadata, adminToken)),
  students: () => loadMock().then((m) => m.students()),
  loadStudents: (body, adminToken) => loadMock().then((m) => m.loadStudents(body, adminToken)),
  auditList: (limit) => loadMock().then((m) => m.auditList(limit)),
  auditRecord: (traceId) => loadMock().then((m) => m.auditRecord(traceId)),
  securityEvents: (limit, adminToken) => loadMock().then((m) => m.securityEvents(limit, adminToken)),
}

export const api: Api = MOCK_MODE ? mockedApi : httpApi
