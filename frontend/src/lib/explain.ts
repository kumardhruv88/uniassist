// Turns machine output (rules, tool calls, precedence results) into plain sentences for the
// "How this was decided" ledger. Every number shown comes straight from the backend payload.

import type { AppliedRule, ConflictRecord, SourceRef, ToolInvocation } from './types'
import { capitalize, formatDate, formatInt, formatPct, humanize, sectionLabel, trimNumber } from './format'

type Unit = 'pct' | 'cgpa' | 'count' | 'list' | 'inr' | 'none'

const PARAMETERS: Record<string, { label: string; unit: Unit }> = {
  min_attendance_pct: { label: 'Minimum attendance', unit: 'pct' },
  max_condonation_pct: { label: 'Shortage that can be condoned', unit: 'pct' },
  pass_min_total_pct: { label: 'Minimum total to pass', unit: 'pct' },
  supplementary_allowed_results: { label: 'Results that allow a supplementary exam', unit: 'list' },
  min_cgpa_placement: { label: 'Minimum CGPA for placements', unit: 'cgpa' },
  max_active_backlogs_placement: { label: 'Active backlogs allowed for placements', unit: 'count' },
  late_fee_per_day: { label: 'Late fee per day', unit: 'inr' },
}

export function parameterLabel(parameter: string | null | undefined): string | null {
  if (!parameter) return null
  return PARAMETERS[parameter]?.label ?? humanize(parameter.replace(/_pct$/, ' percent'))
}

function unitFor(parameter: string | null | undefined): Unit {
  if (!parameter) return 'none'
  const known = PARAMETERS[parameter]
  if (known) return known.unit
  if (parameter.endsWith('_pct')) return 'pct'
  if (parameter.includes('cgpa')) return 'cgpa'
  if (parameter.includes('fee') || parameter.includes('fine')) return 'inr'
  if (parameter.includes('backlog') || parameter.includes('count')) return 'count'
  return 'none'
}

function toNumber(value: unknown): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value === 'string' && value.trim() !== '' && Number.isFinite(Number(value))) return Number(value)
  return null
}

function toText(value: unknown): string | null {
  if (typeof value === 'string' && value.trim() !== '') return value.trim()
  if (typeof value === 'number' && Number.isFinite(value)) return String(value)
  return null
}

function formatAmount(raw: string, unit: Unit): string {
  const text = raw.trim()
  if (unit === 'list') {
    return text
      .split(/[;,]/)
      .map((part) => part.trim().toLowerCase())
      .filter(Boolean)
      .join(' or ')
  }
  const n = toNumber(text)
  if (n === null) return text
  switch (unit) {
    case 'pct':
      return `${trimNumber(n)}%`
    case 'inr':
      return `₹${formatInt(n)}`
    default:
      return trimNumber(n)
  }
}

const OPERATOR_WORDS: Record<string, string> = {
  '>=': 'at least',
  '<=': 'at most',
  '>': 'more than',
  '<': 'less than',
  '=': 'exactly',
}
const OPERATOR_PREFIX = /^(>=|<=|>|<|=)\s*/

/** ">=80%" for min_attendance_pct -> "at least 80%"; "<=0" for backlogs -> "none". */
export function describeRuleValue(value: string, parameter?: string | null, operator?: string | null): string {
  let rest = value.trim()
  let op = operator ?? null
  const prefix = OPERATOR_PREFIX.exec(rest)
  if (prefix) {
    op = op ?? prefix[1] ?? null
    rest = rest.slice(prefix[0].length)
  }
  const unit = unitFor(parameter)
  const amount = formatAmount(rest, unit)
  if (unit === 'list') return amount
  if (op === '<=' && toNumber(rest) === 0) return 'none'
  const words = op ? OPERATOR_WORDS[op] : undefined
  return words ? `${words} ${amount}` : amount
}

export interface RuleLine {
  statement: string
  source: string
}

export function describeRule(rule: AppliedRule): RuleLine {
  const label = parameterLabel(rule.parameter)
  const value = describeRuleValue(rule.value, rule.parameter)
  return {
    statement: label ? `${label}: ${value}` : `${rule.rule_id}: ${value}`,
    source: [`${rule.source_doc_id} ${sectionLabel(rule.source_section)}`.trim(), label ? `rule ${rule.rule_id}` : null]
      .filter(Boolean)
      .join(', '),
  }
}

/** Rule lookups made by get_rule, used when the response carries no applied_rules. */
export function rulesFromTools(tools: ToolInvocation[]): RuleLine[] {
  return tools.flatMap((t) => {
    if (t.tool !== 'get_rule' || t.status === 'error') return []
    const value = toText(t.output.value)
    if (!value) return []
    const parameter = toText(t.input.parameter) ?? toText(t.output.parameter)
    const label = parameterLabel(parameter)
    const docId = toText(t.output.source_doc_id)
    return [
      {
        statement: `${label ?? toText(t.output.rule_id) ?? 'Rule'}: ${describeRuleValue(value, parameter, toText(t.output.operator))}`,
        source: [docId ? `${docId} ${sectionLabel(toText(t.output.source_section))}`.trim() : null, toText(t.output.rule_id) ? `rule ${toText(t.output.rule_id)}` : null]
          .filter(Boolean)
          .join(', '),
      },
    ]
  })
}

// ---------------------------------------------------------------------------------------------
// Tool calls

export interface ToolLine {
  text: string
  /** A verdict: good (eligible), caution (only with a condition) or bad (not eligible, failed). */
  tone?: 'good' | 'caution' | 'bad'
}

export interface ToolSummary {
  tool: string
  /** True when the tool read the student's own records. */
  personal: boolean
  failed: boolean
  lines: ToolLine[]
}

const plainLines = (lines: string[]): ToolLine[] => lines.map((text) => ({ text }))

const NON_PERSONAL_TOOLS = new Set(['get_rule', 'search_documents', 'resolve_course', 'check_attendance_value'])

function isPersonalTool(name: string): boolean {
  if (NON_PERSONAL_TOOLS.has(name)) return false
  return name.startsWith('get_') || name.startsWith('check_') || name.startsWith('what_if')
}

function courseOf(t: ToolInvocation): string | null {
  return toText(t.input.course_code) ?? toText(t.output.course_code)
}

function attendanceLine(course: string | null, held: number, attended: number, pct: number | null): string {
  const share = pct ?? (held > 0 ? (attended / held) * 100 : 0)
  return `${course ? `${course}: ` : ''}${formatInt(attended)} of ${formatInt(held)} classes attended = ${formatPct(share)}`
}

function verdictWord(result: string | null): 'eligible' | 'not eligible' | null {
  if (!result) return null
  const r = result.toUpperCase()
  if (r === 'ELIGIBLE' || r === 'YES' || r === 'PASS') return 'eligible'
  if (r === 'NOT_ELIGIBLE' || r === 'NO' || r === 'INELIGIBLE') return 'not eligible'
  return null
}

function genericLines(t: ToolInvocation): string[] {
  const parts = Object.entries(t.output)
    .filter(([, v]) => typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean')
    .map(([k, v]) => `${humanize(k).toLowerCase()} ${typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v)}`)
  return parts.length ? [`${humanize(t.tool)}: ${parts.join(', ')}`] : [`${humanize(t.tool)} returned no figures`]
}

function describeAttendance(t: ToolInvocation): string[] {
  const rows = Array.isArray(t.output.courses) ? (t.output.courses as Record<string, unknown>[]) : [t.output]
  const lines = rows.flatMap((row) => {
    const held = toNumber(row.classes_held)
    const attended = toNumber(row.classes_attended)
    if (held === null || attended === null) return []
    const course = toText(row.course_code) ?? courseOf(t)
    return [attendanceLine(course, held, attended, toNumber(row.attendance_pct))]
  })
  return lines.length ? lines : genericLines(t)
}

const toneOf = (verdict: 'eligible' | 'not eligible'): ToolLine['tone'] => (verdict === 'eligible' ? 'good' : 'bad')

function describeExamEligibility(t: ToolInvocation): ToolLine[] {
  const o = t.output
  const result = toText(o.result)
  const course = courseOf(t)
  const exam = course ? `the ${course} end-semester exam` : 'the end-semester exam'
  const pct = toNumber(o.attendance_pct)
  const threshold = toNumber(o.threshold)
  if (result?.toUpperCase() === 'RULE_UNAVAILABLE') {
    return [{ text: 'Eligibility was not computed: no rule in force sets the minimum attendance.' }]
  }
  const verdict = verdictWord(result)
  const lines: ToolLine[] = []
  const held = toNumber(o.classes_held)
  const attended = toNumber(o.classes_attended)
  if (held !== null && attended !== null) lines.push({ text: attendanceLine(course, held, attended, pct) })
  if (verdict && pct !== null && threshold !== null) {
    lines.push({
      text:
        verdict === 'eligible'
          ? `Eligible for ${exam}: ${formatPct(pct)} meets the ${trimNumber(threshold)}% minimum.`
          : `Not eligible for ${exam}: ${formatPct(pct)} is below the ${trimNumber(threshold)}% minimum.`,
      tone: toneOf(verdict),
    })
  } else if (verdict) {
    lines.push({ text: `${capitalize(verdict)} for ${exam}.`, tone: toneOf(verdict) })
  }
  const needed = toNumber(o.classes_needed)
  const projAttended = toNumber(o.projected_attended)
  const projHeld = toNumber(o.projected_held)
  if (needed !== null && needed > 0 && projAttended !== null && projHeld !== null && projHeld > 0) {
    lines.push({
      text: `Attending the next ${plainCount(needed, 'class', 'classes')} brings you to ${formatInt(projAttended)} of ${formatInt(projHeld)} = ${formatPct((projAttended / projHeld) * 100)}.`,
    })
  }
  return lines.length ? lines : plainLines(genericLines(t))
}

function plainCount(n: number, one: string, many: string): string {
  return `${formatInt(n)} ${n === 1 ? one : many}`
}

function describeResults(t: ToolInvocation): string[] {
  const rows = (Array.isArray(t.output.results) ? t.output.results : Array.isArray(t.output.rows) ? t.output.rows : null) as
    | Record<string, unknown>[]
    | null
  if (!rows) return genericLines(t)
  if (rows.length === 0) return ['No exam results on record for this course.']
  return rows.map((row) => {
    const course = toText(row.course_code) ?? courseOf(t) ?? 'Course'
    const session = toText(row.exam_session)
    const type = toText(row.exam_type)?.toLowerCase()
    const total = toNumber(row.total_marks)
    const max = toNumber(row.max_marks)
    const result = toText(row.result)?.toLowerCase()
    const what = [session, type ? `${type} exam` : null].filter(Boolean).join(' ')
    const marks = total !== null && max !== null ? `${formatInt(total)} of ${formatInt(max)}` : null
    return `${course}${what ? `, ${what}` : ''}: ${[marks, result].filter(Boolean).join(', ')}`
  })
}

function describeProfile(t: ToolInvocation): string[] {
  const o = t.output
  const parts = [
    toText(o.programme),
    toNumber(o.batch_year) !== null ? `batch ${toNumber(o.batch_year)}` : null,
    toNumber(o.current_semester) !== null ? `semester ${toNumber(o.current_semester)}` : null,
  ].filter(Boolean)
  const cgpa = toNumber(o.cgpa)
  const backlogs = toNumber(o.active_backlogs)
  const standing = [
    cgpa !== null ? `CGPA ${cgpa.toFixed(2)}` : null,
    backlogs !== null ? plainCount(backlogs, 'active backlog', 'active backlogs') : null,
  ].filter(Boolean)
  const lines = [parts.join(', '), standing.join(', ')].filter((s) => s.length > 0)
  return lines.length ? [lines.join('. ') + '.'] : genericLines(t)
}

function assumptionsOf(t: ToolInvocation): string | null {
  const raw = t.output.assumptions ?? t.input.assumptions
  if (Array.isArray(raw) && raw.length) return `Assuming ${raw.map(String).join('; ')}.`
  const cleared = t.input.assume_cleared
  if (Array.isArray(cleared) && cleared.length) return `Assuming you clear ${cleared.map(String).join(', ')}.`
  return null
}

function describePlacement(t: ToolInvocation): ToolLine[] {
  const o = t.output
  const verdict = verdictWord(toText(o.result))
  const cgpa = toNumber(o.cgpa)
  const minCgpa = toNumber(o.min_cgpa)
  const backlogs = toNumber(o.active_backlogs)
  const maxBacklogs = toNumber(o.max_active_backlogs)
  const facts = [
    cgpa !== null && minCgpa !== null ? `CGPA ${cgpa.toFixed(2)} against a ${trimNumber(minCgpa)} minimum` : null,
    backlogs !== null && maxBacklogs !== null
      ? `${plainCount(backlogs, 'active backlog', 'active backlogs')} against ${maxBacklogs === 0 ? 'none' : formatInt(maxBacklogs)} allowed`
      : null,
  ].filter(Boolean)
  const lines: ToolLine[] = []
  if (verdict) {
    lines.push({ text: `${capitalize(verdict)} for placements${facts.length ? `: ${facts.join(', ')}` : ''}.`, tone: toneOf(verdict) })
  }
  const assumed = assumptionsOf(t)
  if (assumed) lines.push({ text: assumed })
  return lines.length ? lines : plainLines(genericLines(t))
}

function describeSupplementary(t: ToolInvocation): ToolLine[] {
  const verdict = verdictWord(toText(t.output.result))
  const course = courseOf(t)
  const reason = toText(t.output.reason)
  if (!verdict) return plainLines(genericLines(t))
  return [
    {
      text: `${capitalize(verdict)} for the ${course ? `${course} ` : ''}supplementary exam${reason ? `: ${reason}` : ''}.`,
      tone: toneOf(verdict),
    },
  ]
}

/** Policy what-if: is a stated attendance percentage enough, with or without a medical certificate? */
function describeAttendanceValue(t: ToolInvocation): ToolLine[] {
  const o = t.output
  const value = toNumber(t.input.attendance_pct ?? t.input.value_pct)
  const medical = t.input.medical_certificate === true || t.input.medical === true
  const minimum = toNumber(o.minimum_pct)
  const condonable = toNumber(o.condonable_pct)
  const floor = toNumber(o.lowest_with_condonation_pct)
  const result = (toText(o.result) ?? '').toUpperCase()
  const pctOf = (n: number | null) => (n === null ? '?' : `${trimNumber(n)}%`)
  const asked = `${pctOf(value)} attendance, ${medical ? 'with' : 'without'} a medical certificate.`
  const lines: ToolLine[] = [{ text: `Checked ${asked}` }]
  switch (result) {
    case 'MEETS_MINIMUM':
      lines.push({ text: `Enough: ${pctOf(value)} meets the ${pctOf(minimum)} minimum.`, tone: 'good' })
      break
    case 'BELOW_MINIMUM':
      lines.push({ text: `Not enough: ${pctOf(value)} is below the ${pctOf(minimum)} minimum.`, tone: 'bad' })
      break
    case 'CONDONABLE':
      lines.push({
        text: medical
          ? `Enough only if condonation is approved: ${pctOf(value)} is below the ${pctOf(minimum)} minimum but within the ${pctOf(condonable)} shortage that can be condoned on medical grounds.`
          : `Not by itself: ${pctOf(value)} is below the ${pctOf(minimum)} minimum; an approved medical condonation of up to ${pctOf(condonable)} would cover it.`,
        tone: 'caution',
      })
      break
    case 'BELOW_CONDONABLE_FLOOR':
      lines.push({
        text: `Not enough: ${pctOf(value)} is below ${pctOf(floor)}, the lowest a medical condonation can cover (the ${pctOf(minimum)} minimum less up to ${pctOf(condonable)}).`,
        tone: 'bad',
      })
      break
    default:
      lines.push({
        text: result ? `Not decided: ${humanize(result).toLowerCase()}.` : 'Not decided: the check returned no result.',
      })
  }
  return lines
}

function describeRuleLookup(t: ToolInvocation): string[] {
  const [rule] = rulesFromTools([t])
  return rule ? [`Looked up ${rule.statement.charAt(0).toLowerCase()}${rule.statement.slice(1)} (${rule.source})`] : genericLines(t)
}

export function describeTool(t: ToolInvocation): ToolSummary {
  const personal = isPersonalTool(t.tool)
  if (t.status === 'error') {
    const why = toText(t.output.error) ?? toText(t.output.message) ?? toText(t.output.detail)
    return { tool: t.tool, personal, failed: true, lines: [{ text: `${humanize(t.tool)} failed${why ? `: ${why}` : ''}.`, tone: 'bad' }] }
  }
  let lines: ToolLine[]
  switch (t.tool) {
    case 'get_attendance':
      lines = plainLines(describeAttendance(t))
      break
    case 'check_exam_eligibility':
      lines = describeExamEligibility(t)
      break
    case 'get_results':
      lines = plainLines(describeResults(t))
      break
    case 'get_student_profile':
      lines = plainLines(describeProfile(t))
      break
    case 'check_placement_eligibility':
      lines = describePlacement(t)
      break
    case 'check_supplementary_eligibility':
      lines = describeSupplementary(t)
      break
    case 'check_attendance_value':
      lines = describeAttendanceValue(t)
      break
    case 'get_rule':
      lines = plainLines(describeRuleLookup(t))
      break
    default:
      lines = plainLines(genericLines(t))
  }
  return { tool: t.tool, personal, failed: false, lines }
}

// ---------------------------------------------------------------------------------------------
// Precedence

/** Returns the register's doc_type for a document ID, when the register is loaded. */
export type DocTypeLookup = (docId: string) => string | undefined

const DOC_TYPE_NAMES: Record<string, { name: string; withArticle: string }> = {
  regulation: { name: 'regulation', withArticle: 'a regulation' },
  circular: { name: 'circular', withArticle: 'a circular' },
  notice: { name: 'notice', withArticle: 'a notice' },
  faq: { name: 'FAQ', withArticle: 'an FAQ' },
  handbook: { name: 'handbook', withArticle: 'a handbook' },
  unofficial: { name: 'unofficial source', withArticle: 'an unofficial source' },
}

function typeName(docId: string, docType?: DocTypeLookup) {
  const raw = docType?.(docId)
  return raw ? DOC_TYPE_NAMES[raw.toLowerCase()] : undefined
}

function refName(ref: SourceRef): string {
  const name = ref.title ?? ref.doc_id
  return ref.section ? `${name} ${sectionLabel(ref.section)}` : name
}

function winnerName(ref: SourceRef | null, docType?: DocTypeLookup): string {
  if (!ref) return 'the source that applies'
  const type = typeName(ref.doc_id, docType)
  return type ? `${type.name} ${ref.doc_id}` : ref.doc_id
}

/** "replaced by ACAD-2026-08" -> "Replaced by circular ACAD-2026-08"; "lower authority" gets the two ranks. */
function explainReason(ref: SourceRef, conflict: ConflictRecord, docType?: DocTypeLookup): string {
  const reason = (ref.reason ?? '').trim()
  const winner = conflict.winner
  if (winner && /^lower authority$/i.test(reason)) {
    const loserType = typeName(ref.doc_id, docType)
    const winnerType = typeName(winner.doc_id, docType)
    if (loserType && winnerType) return `Lower authority: ${loserType.withArticle} ranks below ${winnerType.withArticle}`
  }
  let text = capitalize(reason)
  const type = winner ? typeName(winner.doc_id, docType) : undefined
  if (winner && type && text.includes(winner.doc_id) && !text.toLowerCase().includes(`${type.name.toLowerCase()} ${winner.doc_id.toLowerCase()}`)) {
    text = text.replace(winner.doc_id, `${type.name} ${winner.doc_id}`)
  }
  return text
}

export interface ConflictLine {
  source: string
  reason: string
}

export interface ConflictSummary {
  topic: string
  resolved: boolean
  winner: string | null
  losers: ConflictLine[]
}

export function describeConflict(conflict: ConflictRecord, docType?: DocTypeLookup): ConflictSummary {
  const unit = unitFor(conflict.topic)
  const said = (ref: SourceRef) => (ref.value ? `${refName(ref)} says ${formatAmount(ref.value, unit)}` : refName(ref))
  const fallbackReason = (() => {
    switch (conflict.resolved_by) {
      case 'step2_supersession':
        return `Replaced by ${winnerName(conflict.winner, docType)}`
      case 'step3_authority':
        return `Lower authority than ${winnerName(conflict.winner, docType)}`
      case 'step4_recency':
        return `Older than ${winnerName(conflict.winner, docType)}`
      default:
        return 'Same authority and date as the others, so it cannot be ranked'
    }
  })()
  return {
    topic: parameterLabel(conflict.topic) ?? conflict.topic,
    resolved: conflict.resolved_by !== null && conflict.winner !== null,
    winner: conflict.winner ? said(conflict.winner) : null,
    losers: conflict.others.map((ref) => ({
      source: said(ref),
      reason: ref.reason ? explainReason(ref, conflict, docType) : fallbackReason,
    })),
  }
}

export interface UpcomingLine {
  source: string
  detail: string
}

export function describeUpcoming(ref: SourceRef): UpcomingLine {
  const parts = [
    ref.value ? `sets ${ref.value}` : null,
    ref.effective_from ? `takes effect on ${formatDate(ref.effective_from)}` : null,
    ref.reason ?? null,
  ].filter(Boolean)
  return { source: refName(ref), detail: capitalize(parts.join(', ')) }
}

/** Short names for the precedence labels attached to retrieved evidence. */
export function evidenceLabel(label: string | undefined): string {
  if (!label) return 'Retrieved'
  const known: Record<string, string> = {
    PRIMARY: 'Primary',
    SUPERSEDED: 'Superseded',
    LOWER_PRECEDENCE: 'Lower precedence',
    TIED: 'Tied',
    INFORMATIONAL: 'Informational only',
    UPCOMING: 'Not yet in force',
    SOURCE: 'Source',
  }
  return known[label.toUpperCase()] ?? humanize(label)
}

// ---------------------------------------------------------------------------------------------
// How the answer was produced (AskResponse.meta and the audit record)

const GUARDRAIL_REASONS: Record<string, string> = {
  prompt_injection: 'instructions aimed at the assistant',
  jailbreak: 'an attempt to make the assistant drop its rules or play another role',
  bulk_data: 'a request for other students’ data or a bulk export',
  record_change: 'a request to change records, which this desk can only read',
  abuse: 'abusive language',
  other_student: 'a request for another student’s records',
}

/** "prompt_injection" -> "instructions aimed at the assistant". */
export function guardrailReason(reason: string): string {
  return GUARDRAIL_REASONS[reason] ?? humanize(reason).toLowerCase()
}

const FINDINGS: Record<string, string> = {
  injection_pattern: 'instructions aimed at the assistant',
  jailbreak_pattern: 'a role-play or bypass attempt',
  encoded_payload: 'an encoded payload',
  write_request: 'a request to change records',
  bulk_request: 'a bulk data request',
  abuse_lexicon: 'abusive language',
  other_student_id: 'another student’s ID',
  prompt_leak: 'text from the assistant’s own instructions',
}

/** Names what a guardrail found or removed: "other_student_id" -> "another student's ID". */
export function findingLabel(finding: string): string {
  if (FINDINGS[finding]) return FINDINGS[finding]
  const unsupported = /^unsupported_(.+)$/.exec(finding)
  if (unsupported?.[1]) return `${humanize(unsupported[1]).toLowerCase()} details not found in the sources`
  return humanize(finding).toLowerCase()
}

/** How the question was planned. router_first means code decided without calling the model. */
export function plannerText(planner: string | null | undefined): string | null {
  switch (planner) {
    case 'router_first':
      return 'Planned by code, without the language model: the question matched a known pattern.'
    case 'llm':
      return 'Planned by the language model.'
    case 'router_fallback':
    case 'router':
      return 'Planned by code, because the language model’s plan was not usable.'
    case null:
    case undefined:
    case '':
      return null
    default:
      return `Planned by ${humanize(planner).toLowerCase()}.`
  }
}

/** True when planning called the language model. */
export function plannerUsedModel(planner: string | null | undefined): boolean {
  return planner === 'llm' || planner === 'router_fallback' || planner === 'router'
}

export function retrievalText(mode: string | null | undefined): string | null {
  if (!mode) return null
  const known: Record<string, string> = {
    dense: 'Searched by meaning (dense vectors).',
    hybrid: 'Searched by meaning and by keyword (hybrid).',
    'hybrid+rerank': 'Searched by meaning and by keyword, then reranked.',
  }
  return known[mode] ?? `Searched with ${mode}.`
}

/** How a follow-up was read, for the note under the question. */
export function rewriteLead(rewrite: string | null | undefined): string {
  switch (rewrite) {
    case 'clarification':
      return 'Read with your earlier question as'
    case 'course_swap':
      return 'Read as a follow-up:'
    case 'llm':
      return 'Read as a follow-up by the language model:'
    case 'concat':
      return 'Read together with your last question:'
    default:
      return 'Read as'
  }
}
