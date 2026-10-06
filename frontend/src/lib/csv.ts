// A small RFC 4180 CSV parser: quoted fields, "" escapes, commas and line breaks inside quotes,
// CRLF / LF / CR line endings and a leading byte-order mark. No dependencies.

import type { CsvRow } from './types'

export interface CsvParseResult {
  /** Header names, trimmed and lower-cased. */
  headers: string[]
  /** One object per data row. Empty cells become null. */
  rows: CsvRow[]
  /** Problems worth showing before the rows are sent. */
  problems: string[]
}

function tokenize(text: string): { records: string[][]; unterminated: boolean } {
  const records: string[][] = []
  let record: string[] = []
  let field = ''
  let quoted = false
  let inQuotes = false
  let i = 0

  const endField = () => {
    record.push(quoted ? field : field.trim())
    field = ''
    quoted = false
  }
  const endRecord = () => {
    endField()
    records.push(record)
    record = []
  }

  while (i < text.length) {
    const c = text[i]
    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') {
          field += '"'
          i += 2
          continue
        }
        inQuotes = false
        i += 1
        continue
      }
      field += c
      i += 1
      continue
    }
    if (c === '"' && field.trim() === '' && !quoted) {
      inQuotes = true
      quoted = true
      field = ''
      i += 1
      continue
    }
    if (c === ',') {
      endField()
      i += 1
      continue
    }
    if (c === '\r' || c === '\n') {
      endRecord()
      i += c === '\r' && text[i + 1] === '\n' ? 2 : 1
      continue
    }
    // Characters after a closing quote (e.g. "abc"x) are kept, as most spreadsheet tools do.
    field += c
    i += 1
  }
  if (field !== '' || quoted || record.length > 0) endRecord()
  return { records, unterminated: inQuotes }
}

export function parseCsv(input: string): CsvParseResult {
  const text = input.charCodeAt(0) === 0xfeff ? input.slice(1) : input
  const { records, unterminated } = tokenize(text)
  const nonEmpty = records.filter((r) => !(r.length === 1 && r[0] === ''))
  const problems: string[] = []
  if (unterminated) problems.push('A quoted value is never closed, so the end of the file may be cut off.')

  const [headerRecord, ...dataRecords] = nonEmpty
  if (!headerRecord) return { headers: [], rows: [], problems: ['The file is empty.'] }

  const headers = headerRecord.map((h) => h.trim().toLowerCase())
  const seen = new Set<string>()
  for (const h of headers) {
    if (!h) problems.push('The header row has an empty column name.')
    else if (seen.has(h)) problems.push(`The header row repeats the column ${h}.`)
    seen.add(h)
  }

  let shortRows = 0
  let longRows = 0
  const rows = dataRecords.map((record) => {
    if (record.length < headers.length) shortRows += 1
    if (record.length > headers.length) longRows += 1
    const row: CsvRow = {}
    headers.forEach((h, index) => {
      if (!h) return
      const value = record[index]
      row[h] = value === undefined || value === '' ? null : value
    })
    return row
  })
  if (shortRows) problems.push(`${shortRows} ${shortRows === 1 ? 'row has' : 'rows have'} fewer values than the header; missing cells are sent empty.`)
  if (longRows) problems.push(`${longRows} ${longRows === 1 ? 'row has' : 'rows have'} more values than the header; extra values are dropped.`)

  return { headers, rows, problems }
}

/** Builds a CSV line, quoting values that need it. Used for downloadable templates. */
export function toCsvLine(values: string[]): string {
  return values.map((v) => (/[",\r\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)).join(',')
}
