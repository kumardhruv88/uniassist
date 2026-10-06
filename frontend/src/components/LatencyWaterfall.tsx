import type { CSSProperties } from 'react'
import { formatInt, formatMs, humanize } from '../lib/format'

const STEP_ORDER = ['cache', 'guard', 'plan', 'authorize', 'tools', 'execute_tools', 'retrieve', 'compose', 'verify', 'finalize']
/** Steps that call the language model. Planning does only when the planner used it (not router_first). */
const LLM_STEPS = new Set(['compose'])

const STEP_INFO: Record<string, { name: string; does: string }> = {
  cache: { name: 'Answer cache', does: 'Serves the stored answer to the same question' },
  guard: { name: 'Guard', does: 'Checks who is asking and screens the question' },
  plan: { name: 'Plan', does: 'Works out what the question needs' },
  authorize: { name: 'Authorize', does: 'Checks identity and the course' },
  tools: { name: 'Tools', does: 'Computes from the records' },
  execute_tools: { name: 'Tools', does: 'Computes from the records' },
  retrieve: { name: 'Retrieve', does: 'Finds the documents in force' },
  compose: { name: 'Compose', does: 'Language model writes the answer' },
  verify: { name: 'Verify', does: 'Checks citations and numbers' },
  finalize: { name: 'Finalize', does: 'Sets the answer type, files the audit' },
  compose_retry: { name: 'Rewrite', does: 'Language model rewrites after a failed check' },
  verify_retry: { name: 'Verify again', does: 'Checks the rewritten answer' },
  other: { name: 'Other', does: 'Request handling and the audit write' },
}

/**
 * Pipeline order. Retries ("compose_retry") run after the first verify and before finalize;
 * unknown steps keep the order the backend sent them in, after the known ones.
 */
function stepRank(key: string, index: number): number {
  const retry = key.endsWith('_retry')
  const base = retry ? key.slice(0, -'_retry'.length) : key
  const at = STEP_ORDER.indexOf(base)
  if (at === -1) return 100 + index
  return retry ? STEP_ORDER.indexOf('verify') + 0.5 + at / 100 : at
}

const TICK_STEPS = [50, 100, 200, 250, 500, 1000, 2000, 2500, 5000, 10000, 20000, 30000, 60000]

function tickLabel(ms: number): string {
  if (ms === 0) return '0'
  if (ms < 1000) return `${formatInt(ms)} ms`
  const seconds = ms / 1000
  return `${Number.isInteger(seconds) ? seconds : seconds.toFixed(1)} s`
}

interface Row {
  key: string
  ms: number
  start: number
  llm: boolean
}

export function LatencyWaterfall({
  breakdown,
  total,
  planUsedModel,
}: {
  breakdown: Record<string, number>
  total: number
  /** False for router_first plans, which code makes without calling the model. */
  planUsedModel: boolean
}) {
  const ordered = Object.entries(breakdown)
    .filter(([, ms]) => Number.isFinite(ms) && ms >= 0)
    .map(([key, ms], index) => ({ key, ms, rank: stepRank(key, index) }))
    .toSorted((a, b) => a.rank - b.rank)

  const rows: Row[] = []
  let cursor = 0
  for (const { key, ms } of ordered) {
    const base = key.replace(/_retry$/, '')
    rows.push({ key, ms, start: cursor, llm: base === 'plan' ? planUsedModel : LLM_STEPS.has(base) })
    cursor += ms
  }
  const remainder = Math.round(total - cursor)
  if (remainder > 0) rows.push({ key: 'other', ms: remainder, start: cursor, llm: false })

  const domain = Math.max(total, cursor, 1)
  const step = TICK_STEPS.find((s) => domain / s <= 6) ?? 60000
  const axisMax = Math.ceil(domain / step) * step
  const ticks = Array.from({ length: Math.round(axisMax / step) + 1 }, (_, i) => i * step)
  const pct = (ms: number) => `${(ms / axisMax) * 100}%`
  const trackStyle = { '--tick': pct(step) } as CSSProperties

  return (
    <figure>
      <figcaption className="flex flex-wrap items-center gap-x-6 gap-y-1 text-meta text-ink-muted">
        <span className="inline-flex items-center gap-2">
          <span className="wf-swatch" style={{ background: 'var(--color-stamp)' }} />
          Language model call
        </span>
        <span className="inline-flex items-center gap-2">
          <span className="wf-swatch" />
          Code
        </span>
        <span>Steps run one after another; hover or focus a step to see when it started and ended.</span>
      </figcaption>

      <ol className="mt-3">
        {rows.map((row) => {
          const info = STEP_INFO[row.key] ?? { name: humanize(row.key), does: humanize(row.key) }
          const end = row.start + row.ms
          const share = total > 0 ? Math.round((row.ms / total) * 100) : 0
          return (
            <li
              key={row.key}
              className="wf-row outline-offset-0"
              tabIndex={0}
              aria-label={`${info.name}: ${formatMs(row.ms)}, ${share}% of the total, from ${formatInt(row.start)} to ${formatInt(end)} ms. ${info.does}.`}
            >
              <span className="truncate">{info.name}</span>
              <div className="wf-track" style={trackStyle}>
                <div
                  className={row.llm ? 'wf-bar wf-bar-llm' : 'wf-bar'}
                  style={{ left: pct(row.start), width: pct(row.ms) }}
                />
              </div>
              <span className="num text-right">{formatMs(row.ms)}</span>
              <div className="wf-tip" aria-hidden="true">
                <span className="font-semibold">{formatMs(row.ms)}</span> {info.does.toLowerCase()}. From{' '}
                {formatInt(row.start)} to {formatInt(end)} ms, {share}% of the total.
              </div>
            </li>
          )
        })}
      </ol>

      <div className="wf-row mt-1" aria-hidden="true">
        <span />
        <div className="wf-axis">
          {ticks.map((t, i) => (
            <span
              key={t}
              className={`absolute top-0 whitespace-nowrap ${i % 2 === 1 && i !== ticks.length - 1 ? 'wf-tick-minor' : ''}`}
              style={{
                left: pct(t),
                transform: i === 0 ? 'none' : i === ticks.length - 1 ? 'translateX(-100%)' : 'translateX(-50%)',
              }}
            >
              {tickLabel(t)}
            </span>
          ))}
        </div>
        <span />
      </div>
    </figure>
  )
}
