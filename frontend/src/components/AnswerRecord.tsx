import { useId } from 'react'
import { Link } from 'react-router'
import { findingLabel, guardrailReason, rewriteLead } from '../lib/explain'
import { formatDate, formatDuration, plural, sectionLabel } from '../lib/format'
import type { AskEntry } from '../lib/session'
import type { AnswerType, AskMeta, Citation } from '../lib/types'
import { Stamp } from './Stamp'

export function recordMeta(studentId: string | null, studentName: string | undefined, asOfDate: string): string {
  const who = studentId ? `For ${studentId}${studentName ? `, ${studentName}` : ''}.` : 'General question.'
  return `${who} Rules as of ${formatDate(asOfDate)}.`
}

function citationMeta(c: Citation): string {
  return [
    c.doc_id,
    sectionLabel(c.section) || null,
    c.page !== null && c.page !== undefined ? `page ${c.page}` : null,
    c.version ? `version ${c.version}` : null,
    c.effective_from ? `in force from ${formatDate(c.effective_from)}` : null,
  ]
    .filter(Boolean)
    .join(', ')
}

function SourcesList({ citations, answerType, asOfDate }: { citations: Citation[]; answerType: AnswerType; asOfDate: string }) {
  const headingId = useId()
  if (citations.length === 0 && answerType !== 'not_found') return null
  return (
    <section className="record-section" aria-labelledby={headingId}>
      <h3 id={headingId} className="font-semibold">
        Sources
      </h3>
      {citations.length === 0 ? (
        <p className="mt-2 max-w-[62ch] text-ink-muted">
          None. Nothing in force on {formatDate(asOfDate)} covers this question. If an official document does, add it
          under{' '}
          <Link to="/documents" className="link">
            Documents
          </Link>{' '}
          and ask again.
        </p>
      ) : (
        <ol>
          {citations.map((c, i) => (
            <li key={`${c.doc_id}-${c.section ?? ''}-${i}`} className="citation">
              <span className="citation-number" aria-hidden="true">
                {i + 1}
              </span>
              <div className="min-w-0">
                <p className="citation-title">{c.title}</p>
                <p className="citation-meta">{citationMeta(c)}</p>
                {c.quote ? (
                  <blockquote className="quote" cite={c.doc_id}>
                    <span className="quote-open">“</span>
                    {c.quote}”
                  </blockquote>
                ) : null}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}

/**
 * The docket line at the foot of the file: how the answer was produced, in the record's own
 * voice. Each fact is its own sentence; nothing is shown that the backend did not report.
 */
function Docket({ meta, refused }: { meta: AskMeta; refused: boolean }) {
  const facts: string[] = []
  if (refused && meta.guardrail) facts.push(`Stopped by the input guardrail: ${guardrailReason(meta.guardrail)}.`)
  if (meta.cache_hit) {
    facts.push(
      meta.cache === 'semantic'
        ? `Answered from the cache in ${formatDuration(meta.latency_ms)}: an earlier question asked the same thing in other words.`
        : `Answered from the cache in ${formatDuration(meta.latency_ms)}, without calling the language model.`,
    )
  } else {
    const calls = meta.llm_calls === 0 ? 'without a language model call' : `with ${plural(meta.llm_calls, 'language model call')}`
    facts.push(`Answered in ${formatDuration(meta.latency_ms)}, ${calls}.`)
  }
  if (meta.groundedness !== null && meta.groundedness !== undefined) {
    const share = Math.round(meta.groundedness * 100)
    facts.push(
      share >= 100
        ? 'Every sentence of the answer is supported by the cited sources.'
        : `${share}% of the answer’s sentences are supported by the cited sources.`,
    )
  }
  if (meta.output_redactions.length > 0) {
    facts.push(`Removed before showing: ${[...new Set(meta.output_redactions)].map(findingLabel).join(', ')}.`)
  }
  return (
    <p className="record-docket">
      {facts.map((fact) => (
        <span key={fact}>{fact}</span>
      ))}
    </p>
  )
}

interface AnswerRecordProps {
  entry: AskEntry
  studentName?: string
  onChooseOption: (option: string) => void
}

export function AnswerRecord({ entry, studentName, onChooseOption }: AnswerRecordProps) {
  const r = entry.response
  const answerId = useId()
  const optionsId = useId()
  const meta = r.meta
  const rewritten = meta?.rewrite && meta.standalone_question ? meta.standalone_question : null
  return (
    <article className="file" aria-labelledby={answerId}>
      <Link to={`/audit/${encodeURIComponent(r.trace_id)}`} className="file-tab" title="Open the audit record for this answer">
        File <span className="file-number">{r.trace_id}</span>
      </Link>
      <div className="file-sheet">
        <div className="record-head">
          <div className="min-w-0">
            <p className="record-question">{entry.question}</p>
            {rewritten ? (
              <p className="record-rewrite">
                {rewriteLead(meta?.rewrite)} <q className="text-ink">{rewritten}</q>
              </p>
            ) : null}
            <p className="record-meta">{recordMeta(entry.studentId, studentName, entry.asOfDate)}</p>
          </div>
          <div className="record-stamp">
            <Stamp key={r.trace_id} type={r.answer_type} lands />
          </div>
        </div>

        {meta?.degraded ? (
          <p className="record-degraded" role="note">
            The language model was unavailable, so this answer comes from the rules and records only. The decision is
            the same; the wording is a fixed template.
          </p>
        ) : null}

        <p id={answerId} className="record-answer">
          {r.answer}
        </p>
        {r.explanation ? <p className="record-explanation">{r.explanation}</p> : null}

        {r.clarification_options.length > 0 ? (
          <div className="mt-6" role="group" aria-labelledby={optionsId}>
            <p id={optionsId} className="text-ink-muted">
              Choose one; it is read together with your question:
            </p>
            <div className="mt-2.5 flex flex-wrap gap-2.5">
              {r.clarification_options.map((option) => (
                <button key={option} type="button" className="btn btn-secondary" onClick={() => onChooseOption(option)}>
                  {option}
                </button>
              ))}
            </div>
          </div>
        ) : null}

        <SourcesList citations={r.citations} answerType={r.answer_type} asOfDate={r.as_of_date} />
        {meta ? <Docket meta={meta} refused={r.answer_type === 'refused'} /> : null}
      </div>
    </article>
  )
}
