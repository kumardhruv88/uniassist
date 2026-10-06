import { useId, useState, type FormEvent } from 'react'
import { Link, useNavigate } from 'react-router'
import { AdminTokenField } from '../components/AdminTokenField'
import { Cell, EmptyCell } from '../components/Cell'
import { ErrorNotice } from '../components/ErrorNotice'
import { TypeMark } from '../components/TypeMark'
import { useAdminToken } from '../lib/adminToken'
import { explainError, isAuthError } from '../lib/api'
import { findingLabel, guardrailReason } from '../lib/explain'
import { formatDayMonth, formatDuration, formatTime, humanize } from '../lib/format'
import { useAuditList, useSecurityEvents } from '../lib/queries'
import type { SecurityEvent } from '../lib/types'

const EVENT_NAMES: Record<string, string> = {
  guardrail_block: 'Blocked by the input guardrail',
  output_redaction: 'Removed from an answer',
  rate_limited: 'Rate limited',
  client_blocked: 'Client blocked for a while',
}

/** The event's detail object, in words. */
function eventDetail(event: SecurityEvent): string {
  const d = event.detail
  if (typeof d === 'string') return d
  if (!d || typeof d !== 'object') return ''
  const o = d as Record<string, unknown>
  if (typeof o.reason === 'string') return guardrailReason(o.reason)
  if (Array.isArray(o.removed)) return o.removed.map((r) => findingLabel(String(r))).join(', ')
  if (typeof o.retry_after_s === 'number') return `told to wait ${Math.ceil(o.retry_after_s)} s`
  if (typeof o.after === 'number' || typeof o.for_s === 'number') {
    return [typeof o.after === 'number' ? `after ${o.after} blocked questions` : null, typeof o.for_s === 'number' ? `for ${o.for_s} s` : null]
      .filter(Boolean)
      .join(', ')
  }
  return Object.entries(o)
    .map(([k, v]) => `${humanize(k).toLowerCase()} ${String(v)}`)
    .join(', ')
}

/** Guardrail blocks, redactions and rate limiting, for administrators. Loaded only on request. */
function SecurityEvents() {
  const headingId = useId()
  const [adminToken] = useAdminToken()
  const [requested, setRequested] = useState(false)
  const events = useSecurityEvents(adminToken, requested)

  return (
    <section className="mt-14" aria-labelledby={headingId}>
      <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h2 id={headingId} className="section-title">
          Security events
        </h2>
        {requested && events.data ? <p className="text-ink-muted">Latest {events.data.length}</p> : null}
      </div>
      <p className="mt-1 max-w-[62ch] text-ink-muted">
        Questions stopped by the guardrails, text removed from answers, and clients that were rate limited. Needs the
        admin token when the backend has one.
      </p>
      {!requested ? (
        <div className="mt-4">
          <button type="button" className="btn btn-secondary" onClick={() => setRequested(true)}>
            Show security events
          </button>
        </div>
      ) : events.isPending ? (
        <p className="mt-4 text-ink-muted" role="status">
          Loading security events…
        </p>
      ) : events.isError ? (
        <div className="mt-4 grid gap-4">
          <ErrorNotice
            title="Could not load the security events"
            explanation={explainError(events.error, 'loading security events')}
            action={
              <button type="button" className="btn btn-secondary" onClick={() => void events.refetch()}>
                Try again
              </button>
            }
          />
          {isAuthError(events.error) ? <AdminTokenField forceOpen /> : null}
        </div>
      ) : events.data.length === 0 ? (
        <p className="mt-4 text-ink-muted">No security events recorded.</p>
      ) : (
        <table className="register register-stack mt-4">
          <thead>
            <tr>
              <th scope="col">When</th>
              <th scope="col">Event</th>
              <th scope="col">Detail</th>
              <th scope="col">Student</th>
              <th scope="col">File</th>
            </tr>
          </thead>
          <tbody>
            {events.data.map((e) => (
              <tr key={e.id}>
                <Cell label="When" className="num whitespace-nowrap">
                  {formatDayMonth(e.ts)}, {formatTime(e.ts)}
                </Cell>
                <Cell label="Event">{EVENT_NAMES[e.kind] ?? humanize(e.kind)}</Cell>
                <Cell label="Detail">{eventDetail(e) || <EmptyCell word="None" />}</Cell>
                <Cell label="Student" className="num">
                  {e.student_id ?? <span className="text-ink-muted">General</span>}
                </Cell>
                <Cell label="File" className="num">
                  {e.trace_id ? (
                    <Link to={`/audit/${encodeURIComponent(e.trace_id)}`} className="link">
                      {e.trace_id}
                    </Link>
                  ) : (
                    <EmptyCell word="None" />
                  )}
                </Cell>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  )
}

export default function AuditListPage() {
  const list = useAuditList(30)
  const navigate = useNavigate()
  const [lookup, setLookup] = useState('')
  const [lookupError, setLookupError] = useState(false)
  const lookupId = useId()
  const listId = useId()

  function onLookup(event: FormEvent) {
    event.preventDefault()
    const id = lookup.trim()
    if (!id) {
      setLookupError(true)
      return
    }
    void navigate(`/audit/${encodeURIComponent(id)}`)
  }

  return (
    <div className="page-wide">
      <h1 className="page-title">Audit</h1>
      <p className="page-intro">
        Every answer is filed with the question, the sources it found, the tools it ran and how long each step took.
        Open a file to see how an answer was decided.
      </p>

      <form className="mt-6 flex max-w-md flex-wrap items-end gap-3" onSubmit={onLookup} noValidate>
        <div className="field min-w-0 flex-1">
          <label htmlFor={lookupId} className="field-label">
            File number
          </label>
          <input
            id={lookupId}
            className="control num"
            value={lookup}
            placeholder="a91c03fe"
            autoComplete="off"
            spellCheck={false}
            aria-invalid={lookupError || undefined}
            aria-describedby={lookupError ? `${lookupId}-error` : undefined}
            onChange={(e) => {
              setLookup(e.target.value)
              setLookupError(false)
            }}
          />
        </div>
        <button type="submit" className="btn btn-secondary">
          Open file
        </button>
        {lookupError ? (
          <p id={`${lookupId}-error`} className="field-error w-full">
            Enter a file number. It is the trace ID on the answer’s folder tab.
          </p>
        ) : null}
      </form>

      <section className="mt-10" aria-labelledby={listId}>
        <h2 id={listId} className="section-title">
          Recent answers
        </h2>
        {list.isPending ? (
          <p className="mt-4 text-ink-muted" role="status">
            Loading recent answers…
          </p>
        ) : list.isError ? (
          <div className="mt-4">
            <ErrorNotice
              title="Could not load the audit log"
              explanation={explainError(list.error, 'loading the audit log')}
              action={
                <button type="button" className="btn btn-secondary" onClick={() => void list.refetch()}>
                  Try again
                </button>
              }
            />
          </div>
        ) : list.data.length === 0 ? (
          <p className="mt-4 max-w-[60ch] text-ink-muted">
            Nothing filed yet. <Link to="/" className="link">Ask a question</Link> and its record appears here.
          </p>
        ) : (
          <table className="register register-stack mt-4">
            <thead>
              <tr>
                <th scope="col">Filed</th>
                <th scope="col">File</th>
                <th scope="col">Student</th>
                <th scope="col">Question</th>
                <th scope="col">Answer</th>
                <th scope="col" className="cell-num">
                  Took
                </th>
              </tr>
            </thead>
            <tbody>
              {list.data.map((item) => (
                <tr
                  key={item.trace_id}
                  className="is-link"
                  onClick={(e) => {
                    if ((e.target as HTMLElement).closest('a')) return
                    void navigate(`/audit/${encodeURIComponent(item.trace_id)}`)
                  }}
                >
                  <Cell label="Filed" className="num whitespace-nowrap">
                    {formatDayMonth(item.timestamp)}, {formatTime(item.timestamp)}
                  </Cell>
                  <Cell label="File" className="num">
                    {item.trace_id}
                  </Cell>
                  <Cell label="Student" className="num whitespace-nowrap">
                    {item.student_id ?? <span className="text-ink-muted">General</span>}
                  </Cell>
                  <Cell label="Question" className="min-w-64">
                    <Link to={`/audit/${encodeURIComponent(item.trace_id)}`} className="link">
                      {item.question}
                    </Link>
                  </Cell>
                  <Cell label="Answer">
                    <TypeMark type={item.answer_type} />
                  </Cell>
                  <Cell label="Took" className="cell-num whitespace-nowrap">
                    {formatDuration(item.latency_ms)}
                  </Cell>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <SecurityEvents />
    </div>
  )
}
