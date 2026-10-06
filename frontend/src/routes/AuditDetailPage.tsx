import { useId, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'
import { Cell } from '../components/Cell'
import { ErrorNotice } from '../components/ErrorNotice'
import { JsonBlock } from '../components/JsonBlock'
import { LatencyWaterfall } from '../components/LatencyWaterfall'
import { Notice } from '../components/Notice'
import { Stamp } from '../components/Stamp'
import { ApiError, explainError } from '../lib/api'
import {
  describeConflict,
  describeRule,
  describeTool,
  evidenceLabel,
  findingLabel,
  guardrailReason,
  plannerText,
  plannerUsedModel,
  retrievalText,
  rewriteLead,
} from '../lib/explain'
import {
  formatDate,
  formatDocRef,
  formatDuration,
  formatInt,
  formatLocalDate,
  formatMs,
  formatTime,
  humanize,
  plural,
  sectionLabel,
} from '../lib/format'
import { useAuditRecord, useDocTypeLookup, useSources } from '../lib/queries'
import type { AuditRecord } from '../lib/types'

const TONE_TEXT = { good: 'font-semibold text-ledger', caution: 'font-semibold text-caution', bad: 'font-semibold text-registrar' } as const

function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  const id = useId()
  return (
    <section className="mt-12" aria-labelledby={id}>
      <div className="ledger-title flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
        <h2 id={id} className="section-title">
          {title}
        </h2>
        {aside ? <div className="text-ink-muted">{aside}</div> : null}
      </div>
      <div className="mt-1">{children}</div>
    </section>
  )
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="ledger-row">
      <dt className="ledger-term">{term}</dt>
      <dd className="ledger-value">{children}</dd>
    </div>
  )
}

const CHECK_TONES = {
  ok: 'var(--color-ledger)',
  warn: 'var(--color-caution)',
  bad: 'var(--color-registrar)',
} as const

function Check({ tone, children }: { tone: keyof typeof CHECK_TONES; children: ReactNode }) {
  return (
    <li className="flex items-baseline gap-2.5">
      <span className="dot translate-y-[-1px]" style={{ ['--tone' as string]: CHECK_TONES[tone] }} />
      <span>{children}</span>
    </li>
  )
}

/** The audit's cache field: "miss" | "exact" | "semantic", or a boolean on older records. */
function cacheKind(record: AuditRecord): 'exact' | 'semantic' | null {
  const hit = record.cache?.hit
  if (hit === 'exact' || hit === 'semantic') return hit
  if (hit === true) return 'exact'
  return null
}

function ranksText(s: AuditRecord['sources_retrieved'][number]): string | null {
  const parts = [
    s.dense_rank != null ? `meaning #${s.dense_rank}` : null,
    s.bm25_rank != null ? `keyword #${s.bm25_rank}` : null,
    s.rerank != null ? `reranked #${s.rerank}` : null,
  ].filter(Boolean)
  return parts.length ? `Search rank: ${parts.join(', ')}` : null
}

function SourcesRetrieved({ record }: { record: AuditRecord }) {
  const sources = useSources()
  const titleOf = (docId: string) => sources.data?.find((d) => d.doc_id === docId)?.title
  const retrieval = record.retrieval
  const queries = retrieval?.queries ?? record.plan?.search_queries ?? []
  const summary = queries.length > 0 || (retrieval?.glossary_expansion.length ?? 0) > 0
  return (
    <>
      {summary ? (
        <div className="mt-3 mb-4 grid max-w-[68ch] gap-1">
          {queries.length ? <p>Searched for {queries.map((q) => `“${q}”`).join(', ')}.</p> : null}
          {retrieval?.glossary_expansion.length ? (
            <p className="text-ink-muted">Added from the glossary: {retrieval.glossary_expansion.join(', ')}.</p>
          ) : null}
        </div>
      ) : null}
      {record.sources_retrieved.length === 0 ? (
        <p className="text-ink-muted">No documents were searched for this answer.</p>
      ) : (
        <ul className={summary ? 'border-t border-rule-strong' : undefined}>
          {record.sources_retrieved.toSorted((a, b) => b.score - a.score).map((s, i) => {
            const score = Math.max(0, Math.min(1, s.score))
            const ranks = ranksText(s)
            return (
              <li
                key={`${s.doc_id}-${s.section ?? ''}-${i}`}
                className="grid grid-cols-[minmax(0,1fr)_3rem] items-center gap-x-4 gap-y-2 border-b border-rule-strong py-3 sm:grid-cols-[minmax(0,1fr)_8rem_3rem_9.5rem]"
              >
                <div className="min-w-0">
                  <p className="flex flex-wrap items-baseline gap-x-2">
                    <span>
                      <span className="font-semibold">{s.doc_id}</span>
                      {s.section ? <span className="text-ink-muted"> {sectionLabel(s.section)}</span> : null}
                    </span>
                    {s.anchor ? <span className="tag">Cited by a rule</span> : null}
                  </p>
                  {titleOf(s.doc_id) ? <p className="cell-sub truncate">{titleOf(s.doc_id)}</p> : null}
                  {ranks ? <p className="cell-sub">{ranks}</p> : null}
                </div>
                <div className="meter order-3 col-span-2 sm:order-none sm:col-span-1" aria-hidden="true">
                  <div className="meter-fill" style={{ width: `${score * 100}%` }} />
                </div>
                <span className="num text-right" aria-label={`Similarity ${s.score.toFixed(2)}`}>
                  {s.score.toFixed(2)}
                </span>
                <span
                  className={`order-4 col-span-2 sm:order-none sm:col-span-1 ${s.label?.toUpperCase() === 'PRIMARY' ? 'font-semibold text-ink' : 'text-ink-muted'}`}
                >
                  {evidenceLabel(s.label)}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </>
  )
}

function ContextSent({ ctx }: { ctx: NonNullable<AuditRecord['context_optimisation']> }) {
  const lists: { term: string; items: string[]; note: string }[] = [
    { term: 'Set aside', items: ctx.set_aside, note: 'Not needed once precedence was applied.' },
    { term: 'Duplicates', items: ctx.duplicates, note: 'The same text appeared twice.' },
    { term: 'Shortened', items: ctx.compressed, note: 'Trimmed to the relevant sentences.' },
    { term: 'Over budget', items: ctx.over_budget, note: 'Left out to stay within the token budget.' },
  ]
  return (
    <dl>
      <Fact term="Passages">
        <p className="num">
          {formatInt(ctx.evidence_in)} found, {formatInt(ctx.evidence_out)} sent to the model
        </p>
      </Fact>
      <Fact term="Evidence tokens">
        <p className="num">
          {formatInt(ctx.evidence_tokens_in)} before, {formatInt(ctx.evidence_tokens_out)} after
          {ctx.tokens_saved > 0 ? `, ${formatInt(ctx.tokens_saved)} saved` : ', nothing to trim'}
        </p>
      </Fact>
      {lists
        .filter((l) => l.items.length > 0)
        .map((l) => (
          <Fact key={l.term} term={l.term}>
            <p>{l.items.map(formatDocRef).join(', ')}</p>
            <p className="ledger-note">{l.note}</p>
          </Fact>
        ))}
    </dl>
  )
}

function AuditBody({ record }: { record: AuditRecord }) {
  const docType = useDocTypeLookup()
  const tools = record.tools_invoked.map((t) => ({ raw: t, summary: describeTool(t) }))
  const rules = record.applied_rules.map(describeRule)
  const conflicts = record.conflicts_detected.map((c) => describeConflict(c, docType))
  const steps = record.latency_breakdown_ms
  const v = record.verification
  const cached = cacheKind(record)
  const composed = Boolean(steps && Object.keys(steps).some((k) => k === 'compose' || k.startsWith('compose_')))
  const groundedness = record.groundedness ?? v?.groundedness ?? null
  const guard = record.guardrails
  const reasonLabel = guard?.input.reason ? guardrailReason(guard.input.reason) : null
  const extraFindings = [...new Set((guard?.input.findings ?? []).map(findingLabel))].filter((f) => f !== reasonLabel)
  const session = record.session
  const typed = session?.rewrite && session.original_question ? session.original_question : null
  const precedence = (record.precedence_decision || '')
    .split(/;\s*/)
    .map((p) => p.trim())
    .filter(Boolean)
  const showPrecedence = record.sources_retrieved.length > 0 || !/^no conflicting sources$/i.test(record.precedence_decision.trim())
  const ctx = record.context_optimisation
  const showContext = Boolean(ctx && (ctx.evidence_in > 0 || ctx.evidence_tokens_in > 0))
  const planner = record.plan?.source ?? null

  return (
    <>
      <p className="mt-6 flex flex-wrap gap-x-6 gap-y-1 text-ink-muted">
        <span>
          Filed {formatLocalDate(record.timestamp)} at <span className="num">{formatTime(record.timestamp, true)}</span>
        </span>
        <span>{record.student_id ? `Asked for ${record.student_id}` : 'General question'}</span>
        <span>Rules as of {formatDate(record.as_of_date)}</span>
        {record.question_category ? <span>Category: {humanize(record.question_category).toLowerCase()}</span> : null}
      </p>

      <div className="file mt-6 pt-0">
        <div className="file-sheet rounded-sheet">
          <div className="record-head min-h-0">
            <div className="min-w-0">
              <p className="record-question">{typed ?? record.question}</p>
              {typed ? (
                <p className="record-rewrite">
                  {rewriteLead(session?.rewrite)} <q className="text-ink">{record.question}</q>
                </p>
              ) : null}
            </div>
            <div className="record-stamp">
              <Stamp type={record.answer_type} size="small" />
            </div>
          </div>
          {record.degraded ? (
            <p className="record-degraded" role="note">
              The language model was unavailable, so this answer came from the rules and records only, worded by a
              fixed template.
            </p>
          ) : null}
          <p className="record-answer">{record.answer}</p>
          {record.explanation ? <p className="record-explanation">{record.explanation}</p> : null}
          {guard?.input.blocked && guard.input.reason ? (
            <p className="record-docket">
              <span>Stopped by the input guardrail: {guardrailReason(guard.input.reason)}.</span>
            </p>
          ) : null}
          {cached ? (
            <p className="record-docket">
              <span>
                Answered from the cache
                {record.cache?.source_trace_id ? (
                  <>
                    {': a copy of '}
                    <Link to={`/audit/${encodeURIComponent(record.cache.source_trace_id)}`} className="link">
                      file {record.cache.source_trace_id}
                    </Link>
                  </>
                ) : null}
                {cached === 'semantic'
                  ? `, matched by meaning${record.cache?.similarity != null ? ` (similarity ${record.cache.similarity.toFixed(2)})` : ''}`
                  : ''}
                .
              </span>
            </p>
          ) : null}
        </div>
      </div>

      <Section title="Where the time went" aside={<>Total {formatDuration(record.latency_ms)}</>}>
        {steps && Object.keys(steps).length > 0 ? (
          <LatencyWaterfall breakdown={steps} total={record.latency_ms} planUsedModel={plannerUsedModel(planner)} />
        ) : (
          <p className="text-ink-muted">This record has no step timings. Total time: {formatMs(record.latency_ms)}.</p>
        )}
      </Section>

      <Section title="How it was answered">
        <dl>
          <Fact term="Planning">
            <p>{plannerText(planner) ?? 'No plan was made; the question stopped before planning.'}</p>
            {record.plan?.source ? (
              <p className="ledger-note">
                {record.plan.category ? `Category ${humanize(record.plan.category).toLowerCase()}. ` : ''}
                Tools planned: {record.plan.tools.length ? record.plan.tools.join(', ') : 'none'}.
                {record.plan.rule_parameters?.length ? ` Rules looked up: ${record.plan.rule_parameters.join(', ')}.` : ''}
              </p>
            ) : null}
          </Fact>
          <Fact term="Language model">
            <p>
              {record.llm_calls === 0
                ? 'No call.'
                : `${record.model ?? 'Model not recorded'}, ${plural(record.llm_calls, 'call')}.`}
            </p>
            {record.llm_cache_hits ? (
              <p className="ledger-note">{plural(record.llm_cache_hits, 'call was', 'calls were')} answered from the model cache.</p>
            ) : null}
          </Fact>
          <Fact term="Tokens">
            <p className="num">
              {formatInt(record.tokens)}
              {record.token_breakdown
                ? ` (${formatInt(record.token_breakdown.prompt)} prompt, ${formatInt(record.token_breakdown.completion)} completion)`
                : ''}
            </p>
          </Fact>
          <Fact term="Total time">
            <p className="num">{formatMs(record.latency_ms)}</p>
          </Fact>
          {cached ? (
            <Fact term="Cache">
              <p>
                {cached === 'semantic' ? 'Semantic match' : 'Exact match'}
                {record.cache?.source_trace_id ? `, copied from file ${record.cache.source_trace_id}` : ''}.
              </p>
            </Fact>
          ) : null}
          {session?.id ? (
            <Fact term="Conversation">
              <p className="num">{session.id}</p>
              {session.rewrite ? (
                <p className="ledger-note">
                  Follow-up rewritten ({humanize(session.rewrite).toLowerCase()}) from “{session.original_question}”.
                </p>
              ) : (
                <p className="ledger-note">Read as a question on its own.</p>
              )}
            </Fact>
          ) : null}
          {record.degraded ? (
            <Fact term="Degraded">
              <p className={TONE_TEXT.caution}>Yes: the language model was unavailable and a template answered.</p>
            </Fact>
          ) : null}
        </dl>
      </Section>

      <Section
        title="Sources retrieved"
        aside={
          record.sources_retrieved.length
            ? [
                retrievalText(record.retrieval?.mode)?.replace(/\.$/, ''),
                record.retrieval?.max_score != null ? `best score ${record.retrieval.max_score.toFixed(2)}` : null,
              ]
                .filter(Boolean)
                .join('; ') || 'Similarity, 0 to 1'
            : undefined
        }
      >
        <SourcesRetrieved record={record} />
        {showPrecedence ? (
          <div className="mt-5 max-w-[68ch]">
            <h3 className="font-semibold">Precedence decision</h3>
            {precedence.length > 1 ? (
              <ul className="mt-1 grid gap-1">
                {precedence.map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            ) : (
              <p className="mt-1">{precedence[0] ?? 'None recorded.'}</p>
            )}
          </div>
        ) : null}
      </Section>

      {showContext && ctx ? (
        <Section title="Context sent to the model">
          <ContextSent ctx={ctx} />
        </Section>
      ) : null}

      <Section title="Tools invoked" aside={tools.length ? plural(tools.length, 'call') : undefined}>
        {tools.length === 0 ? (
          <p className="text-ink-muted">No tools ran. Nothing was read from student records.</p>
        ) : (
          <table className="register register-stack">
            <thead>
              <tr>
                <th scope="col">Tool</th>
                <th scope="col">What it found</th>
                <th scope="col">Status</th>
                <th scope="col" className="cell-num">
                  Time
                </th>
              </tr>
            </thead>
            <tbody>
              {tools.map(({ raw, summary }, i) => (
                <tr key={`${raw.tool}-${i}`}>
                  <Cell label="Tool" className="whitespace-nowrap">
                    {humanize(raw.tool)}
                    <p className="cell-sub">{raw.tool}</p>
                  </Cell>
                  <Cell label="What it found">
                    {summary.lines.map((line) => (
                      <p key={line.text} className={line.tone ? TONE_TEXT[line.tone] : undefined}>
                        {line.text}
                      </p>
                    ))}
                  </Cell>
                  <Cell label="Status">
                    {raw.status === 'error' ? <span className="font-semibold text-registrar">Failed</span> : 'OK'}
                  </Cell>
                  <Cell label="Time" className="cell-num whitespace-nowrap">
                    {raw.ms !== null && raw.ms !== undefined ? formatMs(raw.ms) : 'Not timed'}
                  </Cell>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      {rules.length > 0 || conflicts.length > 0 ? (
        <Section title="Rules and conflicts">
          <dl>
            {rules.length > 0 ? (
              <Fact term={rules.length > 1 ? 'Rules applied' : 'Rule applied'}>
                {rules.map((r) => (
                  <div key={r.statement + r.source}>
                    <p>{r.statement}</p>
                    <p className="ledger-note">{r.source}</p>
                  </div>
                ))}
              </Fact>
            ) : null}
            {conflicts.map((c) => (
              <Fact key={c.topic} term={c.resolved ? `Conflict on ${c.topic.toLowerCase()}` : 'Unresolved conflict'}>
                {c.winner ? (
                  <div>
                    <p className="font-semibold">{c.winner}</p>
                    <p className="ledger-note">Applies</p>
                  </div>
                ) : null}
                {c.losers.map((l) => (
                  <div key={l.source}>
                    <p>{l.source}</p>
                    <p className="ledger-note">{l.reason}</p>
                  </div>
                ))}
              </Fact>
            ))}
          </dl>
        </Section>
      ) : null}

      <Section title="Checks">
        <dl>
          {guard ? (
            <Fact term="Input guardrail">
              {guard.input.blocked ? (
                <>
                  <p className={TONE_TEXT.bad}>
                    Blocked: {guard.input.reason ? guardrailReason(guard.input.reason) : 'reason not recorded'}.
                  </p>
                  {extraFindings.length ? <p className="ledger-note">Also found: {extraFindings.join(', ')}.</p> : null}
                </>
              ) : (
                <p>Passed{guard.input.findings.length ? `, noting ${guard.input.findings.map(findingLabel).join(', ')}` : ''}.</p>
              )}
            </Fact>
          ) : null}
          {guard ? (
            <Fact term="Output guardrail">
              <p>
                {guard.output.length
                  ? `Removed before showing: ${[...new Set(guard.output)].map(findingLabel).join(', ')}.`
                  : 'Nothing removed.'}
              </p>
            </Fact>
          ) : null}
          {groundedness !== null ? (
            <Fact term="Groundedness">
              <p className="num">
                {groundedness.toFixed(2)}
                <span className="text-ink-muted">
                  {' '}
                  {groundedness >= 1
                    ? '(every sentence is supported by the cited sources)'
                    : `(${Math.round(groundedness * 100)}% of the sentences are supported by the cited sources)`}
                </span>
              </p>
              {record.unsupported_sentences?.length ? (
                <ul className="grid gap-1">
                  {record.unsupported_sentences.map((sentence) => (
                    <li key={sentence} className="ledger-note">
                      Not supported: “{sentence}”
                    </li>
                  ))}
                </ul>
              ) : null}
            </Fact>
          ) : null}
          <Fact term="Verification">
            {!composed ? (
              <p className="text-ink-muted">
                {cached
                  ? 'Not run again: this answer is a copy, checked when it was first answered.'
                  : 'Not run; no answer text was composed for this question.'}
              </p>
            ) : v ? (
              <>
                <ul className="grid gap-1.5">
                  <Check tone={v.citations_valid ? 'ok' : 'bad'}>
                    {v.citations_valid ? 'Every citation points at retrieved evidence' : 'A citation did not match the evidence'}
                  </Check>
                  <Check tone={v.numbers_grounded ? 'ok' : 'bad'}>
                    {v.numbers_grounded ? 'Every number matches tool output or cited text' : 'A number could not be traced to a source'}
                  </Check>
                  <Check tone={v.retries === 0 ? 'ok' : 'warn'}>
                    {v.retries === 0
                      ? 'Passed on the first attempt'
                      : `Rewritten ${v.retries === 1 ? 'once' : `${v.retries} times`} after a failed check`}
                  </Check>
                  <Check tone={v.fallback ? 'warn' : 'ok'}>
                    {v.fallback ? 'The safe template answer was used' : 'No fallback answer was needed'}
                  </Check>
                </ul>
                {v.problems?.length ? (
                  <ul className="mt-1 grid gap-1">
                    {v.problems.map((problem) => (
                      <li key={problem} className="ledger-note">
                        Problem found: {problem}
                      </li>
                    ))}
                  </ul>
                ) : null}
              </>
            ) : (
              <p className="text-ink-muted">Not recorded.</p>
            )}
          </Fact>
        </dl>
      </Section>

      <details className="disclosure mt-10">
        <summary>Show the raw audit record</summary>
        <div className="mt-1">
          <JsonBlock value={record} label={`Audit record ${record.trace_id} as JSON`} />
        </div>
      </details>
    </>
  )
}

export default function AuditDetailPage() {
  const { traceId = '' } = useParams()
  const record = useAuditRecord(traceId)
  const notFound = record.error instanceof ApiError && record.error.status === 404

  return (
    <div className="max-w-[880px]">
      <p>
        <Link to="/audit" className="link">
          All files
        </Link>
      </p>
      <h1 className="page-title mt-3">
        File <span className="num">{traceId}</span>
      </h1>

      {record.isPending ? (
        <p className="mt-6 text-ink-muted" role="status">
          Opening the file…
        </p>
      ) : record.isError ? (
        <div className="mt-6">
          {notFound ? (
            <Notice title={`No file numbered ${traceId}`}>
              <p>
                Check the number on the answer’s folder tab, or pick one from{' '}
                <Link to="/audit" className="link">
                  the list of recent answers
                </Link>
                .
              </p>
            </Notice>
          ) : (
            <ErrorNotice
              title="Could not open the file"
              explanation={explainError(record.error, 'opening the file')}
              action={
                <button type="button" className="btn btn-secondary" onClick={() => void record.refetch()}>
                  Try again
                </button>
              }
            />
          )}
        </div>
      ) : (
        <AuditBody record={record.data} />
      )}
    </div>
  )
}
