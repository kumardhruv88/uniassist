// Formatting for dates, numbers and durations. en-IN gives day-month-year dates and
// Indian digit grouping (1,23,456), which is what students and staff expect.

const LOCALE = 'en-IN'

const dateLongFmt = new Intl.DateTimeFormat(LOCALE, {
  day: 'numeric',
  month: 'long',
  year: 'numeric',
  timeZone: 'UTC',
})
const dateShortFmt = new Intl.DateTimeFormat(LOCALE, {
  day: 'numeric',
  month: 'short',
  year: 'numeric',
  timeZone: 'UTC',
})
const dayMonthFmt = new Intl.DateTimeFormat(LOCALE, { day: 'numeric', month: 'short' })
const timeFmt = new Intl.DateTimeFormat(LOCALE, { hour: '2-digit', minute: '2-digit', hour12: false })
const timeSecondsFmt = new Intl.DateTimeFormat(LOCALE, {
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hour12: false,
})
const localLongFmt = new Intl.DateTimeFormat(LOCALE, { day: 'numeric', month: 'long', year: 'numeric' })
const intFmt = new Intl.NumberFormat(LOCALE, { maximumFractionDigits: 0 })

const DATE_ONLY = /^(\d{4})-(\d{2})-(\d{2})$/

/** Parses a YYYY-MM-DD string as a calendar date (UTC midnight), so it never shifts by a day. */
export function parseDateOnly(value: string | null | undefined): Date | null {
  if (!value) return null
  const match = DATE_ONLY.exec(value.slice(0, 10))
  if (!match) return null
  const date = new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3])))
  return Number.isNaN(date.getTime()) ? null : date
}

/** "6 October 2026" (long) or "6 Oct 2026" (short). Falls back to the raw string. */
export function formatDate(value: string | null | undefined, style: 'long' | 'short' = 'long'): string {
  if (!value) return ''
  const date = parseDateOnly(value)
  if (!date) return value
  return (style === 'long' ? dateLongFmt : dateShortFmt).format(date)
}

function toDate(iso: string): Date | null {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? null : date
}

/** Local date of a timestamp: "6 Oct". */
export function formatDayMonth(iso: string): string {
  const date = toDate(iso)
  return date ? dayMonthFmt.format(date) : iso
}

/** Local time of a timestamp: "14:02". */
export function formatTime(iso: string | number, withSeconds = false): string {
  const date = typeof iso === 'number' ? new Date(iso) : toDate(iso)
  if (!date) return String(iso)
  return (withSeconds ? timeSecondsFmt : timeFmt).format(date)
}

/** Local long date of a timestamp: "6 October 2026". */
export function formatLocalDate(iso: string): string {
  const date = toDate(iso)
  return date ? localLongFmt.format(date) : iso
}

/** Today's local date as YYYY-MM-DD. */
export function todayISO(): string {
  const now = new Date()
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

export function formatInt(value: number): string {
  return intFmt.format(value)
}

/** 77.5 -> "77.50%". Computed figures keep two decimals so they can be checked by hand. */
export function formatPct(value: number, digits = 2): string {
  return `${value.toFixed(digits)}%`
}

/** Thresholds read better without trailing zeros: 80 -> "80%", 6.5 -> "6.5". */
export function trimNumber(value: number): string {
  return Number.isInteger(value) ? String(value) : String(Number(value.toFixed(2)))
}

/** 1900 -> "1,900 ms". Used where step timings are compared. */
export function formatMs(ms: number): string {
  return `${formatInt(Math.round(ms))} ms`
}

/** 5800 -> "5.8 s", 60 -> "60 ms". Used for a single total. */
export function formatDuration(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`
  const seconds = ms / 1000
  return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} s`
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} bytes`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** "7.2" -> "Section 7.2". Leaves labels such as "Annex B" or "Page 3" alone. */
export function sectionLabel(section: string | null | undefined): string {
  if (!section) return ''
  const trimmed = section.trim().replace(/^§+\s*/, '')
  return /^[\dA-Za-z]{1,4}(\.[\dA-Za-z]+)*$/.test(trimmed) ? `Section ${trimmed}` : trimmed
}

/** "ACAD-REG-2024#7.2" -> "ACAD-REG-2024, section 7.2". */
export function formatDocRef(ref: string): string {
  const [doc, section] = ref.split('#')
  return section ? `${doc ?? ''}, ${sectionLabel(section).replace(/^Section /, 'section ')}` : (doc ?? ref)
}

/** "personal_eligibility" -> "Personal eligibility". */
export function humanize(value: string): string {
  const spaced = value.replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim()
  if (!spaced) return value
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase()
}

export function capitalize(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value
}

export function plural(count: number, one: string, many = `${one}s`): string {
  return `${formatInt(count)} ${count === 1 ? one : many}`
}
