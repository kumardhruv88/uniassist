import { useId, type ReactNode } from 'react'
import { MOCK_MODE } from '../lib/api'
import { formatInt, humanize } from '../lib/format'
import { useHealth } from '../lib/queries'
import type { Health } from '../lib/types'

type ComponentKey = keyof Health['components']

const COMPONENTS: { key: ComponentKey; name: string }[] = [
  { key: 'api', name: 'API' },
  { key: 'vector_store', name: 'Search index' },
  { key: 'sqlite', name: 'Records database' },
  { key: 'llm', name: 'Language model' },
]

const STATUS: Record<string, { word: string; tone: string }> = {
  ok: { word: 'OK', tone: 'var(--color-ledger)' },
  degraded: { word: 'Degraded', tone: 'var(--color-caution)' },
  down: { word: 'Down', tone: 'var(--color-registrar)' },
}

function detailFor(key: ComponentKey, health: Health): string | undefined {
  const c = health.components
  switch (key) {
    case 'vector_store':
      return c.vector_store.chunks !== undefined ? `${formatInt(c.vector_store.chunks)} passages indexed` : undefined
    case 'sqlite':
      return c.sqlite.students !== undefined
        ? `${formatInt(c.sqlite.students)} students, ${formatInt(c.sqlite.rules ?? 0)} rules, ${formatInt(c.sqlite.documents ?? 0)} documents`
        : undefined
    case 'llm':
      return c.llm.model ? `${c.llm.model}${c.llm.provider ? ` via ${c.llm.provider}` : ''}` : undefined
    default:
      return undefined
  }
}

/** What to say under the language model line: the circuit breaker, then the backend's own detail. */
function llmProblem(llm: Health['components']['llm']): string | undefined {
  if (llm.breaker === 'open') return 'Circuit breaker open: the model is skipped and answers come from rules and records only.'
  if (llm.breaker === 'half_open') return 'Circuit breaker half-open: trying the model again.'
  if (llm.status !== 'ok') return llm.detail
  return undefined
}

function StatusLine({ name, status, detail, problem }: { name: string; status: string; detail?: string; problem?: string }) {
  const known = STATUS[status]
  return (
    <li className="text-meta" title={detail}>
      <span className="flex items-center gap-2">
        <span className={known ? 'dot' : 'dot dot-hollow'} style={known ? { ['--tone' as string]: known.tone } : undefined} />
        <span className="text-ink">{name}</span>
        <span className="ml-auto text-ink-muted">{known?.word ?? status}</span>
      </span>
      {problem ? <span className="mt-0.5 block pl-4 text-ink-muted [overflow-wrap:anywhere]">{problem}</span> : null}
    </li>
  )
}

const onOff = (value: boolean | undefined) => (value ? 'on' : 'off')

/** The settings that explain behaviour: how it searches, plans, caches and guards. */
function configRows(health: Health): { term: string; value: string }[] {
  const { config, caches, security } = health
  const llm = health.components.llm
  const rows: { term: string; value: string }[] = []
  if (llm.model) rows.push({ term: 'Model', value: llm.model })
  if (config?.retrieval_mode) {
    const reranker = config.reranker && config.reranker !== 'none' ? `, reranker ${config.reranker}` : ', no reranker'
    rows.push({ term: 'Retrieval', value: `${humanize(config.retrieval_mode)}${reranker}` })
  }
  if (config?.planner) rows.push({ term: 'Planner', value: humanize(config.planner) })
  if (config?.context_budget_tokens) rows.push({ term: 'Context budget', value: `${formatInt(config.context_budget_tokens)} tokens` })
  if (caches) {
    const answers = caches.enabled.answers ? `${formatInt(caches.answers)} answers${caches.enabled.semantic ? ' (by meaning too)' : ''}` : 'answers off'
    const model = caches.enabled.llm ? `${formatInt(caches.llm)} model replies` : 'model replies off'
    rows.push({ term: 'Caches', value: `${answers}, ${model}` })
  }
  if (security) {
    const limit = security.rate_limit_per_min ? `${formatInt(security.rate_limit_per_min)} questions a minute` : 'no rate limit'
    rows.push({ term: 'Guardrails', value: `input ${onOff(security.input_guardrails)}, output ${onOff(security.output_guardrails)}; ${limit}` })
  }
  const fallbacks = [
    ...(llm.fallbacks ?? []),
    ...(config?.llm_fallback_model && !(llm.fallbacks ?? []).includes(config.llm_fallback_model) ? [config.llm_fallback_model] : []),
  ]
  if (config || llm.fallbacks) {
    rows.push({
      term: 'Fallback',
      value: `${fallbacks.length ? fallbacks.join(', ') : 'none'}; cloud ${onOff(llm.cloud_fallback_enabled ?? config?.cloud_fallback)}`,
    })
  }
  return rows
}

export function HealthPanel() {
  const headingId = useId()
  const health = useHealth()

  let lines: ReactNode
  let config: { term: string; value: string }[] = []
  if (health.isPending) {
    lines = <StatusLine name="API" status="Checking" />
  } else if (health.isError) {
    lines = (
      <>
        <StatusLine name="API" status="down" />
        <li className="field-hint">Not reachable. Start the backend on port 8000.</li>
      </>
    )
  } else {
    const data = health.data
    config = configRows(data)
    lines = COMPONENTS.map(({ key, name }) => {
      const component = data.components[key]
      const status = component?.status ?? 'unknown'
      // The backend explains failures ("model not pulled; run: ollama pull ..."); show that next step.
      const problem = key === 'llm' ? llmProblem(data.components.llm) : status !== 'ok' ? component?.detail : undefined
      return <StatusLine key={key} name={name} status={status} detail={detailFor(key, data)} problem={problem} />
    })
  }

  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="field-label-quiet">
        System status
      </h2>
      <ul className="mt-2.5 grid gap-1.5">{lines}</ul>
      {config.length ? (
        <details className="disclosure disclosure-small mt-1">
          <summary>Configuration</summary>
          <dl className="grid gap-1.5 pb-1 text-meta">
            {config.map((row) => (
              <div key={row.term}>
                <dt className="text-ink-muted">{row.term}</dt>
                <dd className="text-ink [overflow-wrap:anywhere]">{row.value}</dd>
              </div>
            ))}
          </dl>
        </details>
      ) : null}
      {MOCK_MODE ? (
        <p className="field-hint mt-2">Mock mode: answers come from built-in examples, not the backend.</p>
      ) : null}
    </section>
  )
}
