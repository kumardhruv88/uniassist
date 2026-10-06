import { useId, type ReactNode } from 'react'
import { Link } from 'react-router'
import {
  describeConflict,
  describeRule,
  describeTool,
  describeUpcoming,
  plannerText,
  retrievalText,
  rulesFromTools,
  type ToolLine,
} from '../lib/explain'
import { formatDate, formatInt, plural } from '../lib/format'
import { useDocTypeLookup } from '../lib/queries'
import type { AskEntry } from '../lib/session'
import type { AskMeta } from '../lib/types'
import { JsonBlock } from './JsonBlock'

function Row({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="ledger-row">
      <dt className="ledger-term">{term}</dt>
      <dd className="ledger-value">{children}</dd>
    </div>
  )
}

const TONE_CLASS = {
  good: 'font-semibold text-ledger',
  caution: 'font-semibold text-caution',
  bad: 'font-semibold text-registrar',
} as const

function Line({ main, note, tone }: { main: string; note?: string; tone?: ToolLine['tone'] }) {
  return (
    <div>
      <p className={tone ? TONE_CLASS[tone] : undefined}>{main}</p>
      {note ? <p className="ledger-note">{note}</p> : null}
    </div>
  )
}

/** Plain sentences about how the answer was produced, from AskResponse.meta. */
function processLines(meta: AskMeta): ToolLine[] {
  const lines: ToolLine[] = []
  const planned = plannerText(meta.planner)
  if (planned) lines.push({ text: planned })
  if (meta.cache_hit) {
    lines.push({ text: `Served from the answer cache (${meta.cache === 'semantic' ? 'a question with the same meaning' : 'the same question'}); no language model call.` })
  } else if (meta.llm_calls === 0) {
    lines.push({ text: 'No language model call.' })
  } else {
    lines.push({ text: `${plural(meta.llm_calls, 'language model call')}, ${formatInt(meta.tokens)} tokens.` })
  }
  const searched = retrievalText(meta.retrieval)
  if (searched) lines.push({ text: searched })
  if (meta.tokens_saved > 0) {
    lines.push({ text: `${formatInt(meta.tokens_saved)} evidence tokens trimmed from the context.` })
  }
  if (meta.degraded) {
    lines.push({ text: 'The language model was unavailable; a fixed template worded the answer.', tone: 'caution' })
  }
  return lines
}

/** "How this was decided": a ruled ledger under the answer record. */
export function Ledger({ entry }: { entry: AskEntry }) {
  const headingId = useId()
  const r = entry.response
  const docType = useDocTypeLookup()

  const rules = r.applied_rules.length > 0 ? r.applied_rules.map(describeRule) : rulesFromTools(r.tools_invoked)
  const summaries = r.tools_invoked.map(describeTool)
  const personal = summaries.filter((t) => t.personal)
  // Decisive checks that read no personal data, such as the attendance what-if.
  const checks = summaries.filter((t) => !t.personal && t.tool.startsWith('check_'))
  const process = r.meta ? processLines(r.meta) : []
  const conflicts = r.conflicts_detected.map((c) => describeConflict(c, docType))
  const resolved = conflicts.filter((c) => c.resolved && c.losers.length > 0)
  const unresolved = conflicts.filter((c) => !c.resolved)
  const upcoming = r.upcoming_changes.map(describeUpcoming)
  const ruleBased = rules.length > 0 || r.citations.length > 0

  let recordRow: ReactNode = null
  if (personal.length > 0) {
    recordRow = (
      <Row term="Your record">
        {personal.flatMap((t) => t.lines.map((line, i) => <Line key={`${t.tool}-${i}`} main={line.text} tone={line.tone} />))}
      </Row>
    )
  } else if (r.answer_type === 'refused') {
    recordRow = (
      <Row term="Records read">
        <Line main="None. The request was stopped before any record was opened." />
      </Row>
    )
  } else if (r.answer_type === 'clarification_needed') {
    recordRow = (
      <Row term="Records read">
        <Line main="None yet. The check runs once you choose an option above." />
      </Row>
    )
  }

  return (
    <section className="mt-10" aria-labelledby={headingId}>
      <h2 id={headingId} className="section-title ledger-title">
        How this was decided
      </h2>
      <dl>
        <Row term="Rules as of">
          <p>{formatDate(r.as_of_date)}</p>
        </Row>

        {rules.length > 0 ? (
          <Row term={rules.length > 1 ? 'Rules applied' : 'Rule applied'}>
            {rules.map((rule) => (
              <Line key={rule.statement + rule.source} main={rule.statement} note={rule.source} />
            ))}
          </Row>
        ) : r.answer_type === 'not_found' ? (
          <Row term="Rule applied">
            <Line main="None. No rule or document in force covers this question." />
          </Row>
        ) : null}

        {checks.length > 0 ? (
          <Row term="What-if check">
            {checks.flatMap((t) => t.lines.map((line, i) => <Line key={`${t.tool}-${i}`} main={line.text} tone={line.tone} />))}
          </Row>
        ) : null}

        {recordRow}

        {unresolved.length > 0 ? (
          <Row term="Sources that disagree">
            {unresolved.map((c) => (
              <div key={c.topic} className="grid gap-2.5">
                {c.losers.map((l) => (
                  <Line key={l.source} main={l.source} />
                ))}
                <p className="ledger-note">
                  Same authority and the same date, so neither can be ranked above the other. Ask the issuing office.
                </p>
              </div>
            ))}
          </Row>
        ) : null}

        {resolved.length > 0 ? (
          <Row term="Sources that lost">
            {resolved.flatMap((c) => c.losers.map((l) => <Line key={c.topic + l.source} main={l.source} note={l.reason} />))}
          </Row>
        ) : ruleBased && unresolved.length === 0 ? (
          <Row term="Sources that lost">
            <Line main="None. No other source in force disagreed." />
          </Row>
        ) : null}

        {upcoming.length > 0 ? (
          <Row term="Upcoming changes">
            {upcoming.map((u) => (
              <Line key={u.source} main={u.source} note={u.detail} />
            ))}
          </Row>
        ) : ruleBased ? (
          <Row term="Upcoming changes">
            <Line main={`None announced after ${formatDate(r.as_of_date)}.`} />
          </Row>
        ) : null}

        {process.length > 0 ? (
          <Row term="How it was answered">
            {process.map((line) => (
              <Line key={line.text} main={line.text} tone={line.tone} />
            ))}
          </Row>
        ) : null}
      </dl>

      <div className="mt-3 flex flex-wrap items-center justify-between gap-x-6 gap-y-1">
        {r.tools_invoked.length > 0 ? (
          <details className="disclosure w-full">
            <summary>Show the raw tool calls ({r.tools_invoked.length})</summary>
            <div className="mt-1 mb-2">
              <JsonBlock value={r.tools_invoked} label="Raw tool calls as JSON" />
            </div>
          </details>
        ) : null}
        <p className="py-2">
          <Link to={`/audit/${encodeURIComponent(r.trace_id)}`} className="link">
            Open the audit record for file {r.trace_id}
          </Link>
        </p>
      </div>
    </section>
  )
}
