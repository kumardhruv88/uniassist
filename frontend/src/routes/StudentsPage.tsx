import { useId, useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AdminTokenField } from '../components/AdminTokenField'
import { Cell } from '../components/Cell'
import { ErrorNotice } from '../components/ErrorNotice'
import { Notice } from '../components/Notice'
import { STUDENT_FILES, type StudentFileSpec } from '../lib/annex'
import { useAdminToken } from '../lib/adminToken'
import { api, explainError, isAuthError } from '../lib/api'
import { parseCsv, toCsvLine, type CsvParseResult } from '../lib/csv'
import { formatInt, plural } from '../lib/format'
import { queryKeys, useStudents } from '../lib/queries'
import { useIdentity } from '../lib/session'
import type { LoadStudentsRequest, LoadStudentsResponse, StudentTable } from '../lib/types'

interface ParsedFile {
  name: string
  result: CsvParseResult
  missing: string[]
  extra: string[]
}

function templateHref(spec: StudentFileSpec): string {
  return `data:text/csv;charset=utf-8,${encodeURIComponent(`${toCsvLine(spec.columns)}\n`)}`
}

function FileRow({
  spec,
  parsed,
  readError,
  inputKey,
  onFile,
  onRemove,
}: {
  spec: StudentFileSpec
  parsed?: ParsedFile
  readError?: string
  inputKey: number
  onFile: (spec: StudentFileSpec, file: File | null) => void
  onRemove: (table: StudentTable) => void
}) {
  const id = useId()
  return (
    <li className="grid gap-x-8 gap-y-3 border-b border-rule-strong py-5 md:grid-cols-[minmax(0,1fr)_minmax(0,22rem)]">
      <div className="min-w-0">
        <label htmlFor={id} className="field-label">
          {spec.file}
        </label>
        <p className="mt-0.5 text-ink-muted">{spec.description}</p>
        <p className="mt-1.5 text-meta text-ink-muted" id={`${id}-columns`}>
          Columns: {spec.columns.join(', ')}.{' '}
          <a className="link" href={templateHref(spec)} download={spec.file}>
            Download an empty template
          </a>
        </p>
      </div>
      <div className="grid content-start gap-2">
        <input
          key={inputKey}
          id={id}
          type="file"
          accept=".csv,text/csv"
          className="file-input"
          aria-describedby={`${id}-columns ${id}-status`}
          onChange={(e) => onFile(spec, e.target.files?.[0] ?? null)}
        />
        <div id={`${id}-status`} className="grid gap-1 text-meta">
          {readError ? <p className="field-error">{readError}</p> : null}
          {parsed ? (
            <>
              <p className="flex flex-wrap items-baseline gap-x-3">
                <span className="text-ink">
                  {plural(parsed.result.rows.length, 'row')} ready from {parsed.name}
                </span>
                <button type="button" className="text-button" onClick={() => onRemove(spec.table)}>
                  Remove
                </button>
              </p>
              {parsed.missing.length ? (
                <p className="text-caution">
                  Missing {parsed.missing.length === 1 ? 'column' : 'columns'}: {parsed.missing.join(', ')}. Rows without
                  required values will be rejected.
                </p>
              ) : null}
              {parsed.extra.length ? (
                <p className="text-ink-muted">Extra columns are ignored: {parsed.extra.join(', ')}.</p>
              ) : null}
              {parsed.result.problems.map((p) => (
                <p key={p} className="text-caution">
                  {p}
                </p>
              ))}
            </>
          ) : null}
        </div>
      </div>
    </li>
  )
}

function LoadResult({ result }: { result: LoadStudentsResponse }) {
  const tables = STUDENT_FILES.filter((s) => s.table in result.accepted)
  const rejectedBy = (table: string) => result.rejected.filter((r) => r.table === table).length
  const total = Object.values(result.accepted).reduce((sum, n) => sum + n, 0)
  const rejected = result.rejected.length
  return (
    <div className="grid gap-6">
      <Notice
        title={rejected ? `Loaded ${plural(total, 'row')}; ${plural(rejected, 'row')} rejected` : `Loaded ${plural(total, 'row')}`}
        tone={rejected ? 'caution' : 'ledger'}
        role="status"
      >
        <p>
          {rejected
            ? 'The rejected rows are listed below with the reason. Fix them in the CSV and load that file again; accepted rows are updated in place.'
            : 'Every row was accepted. The students below can be chosen under “Signed in as”.'}
        </p>
      </Notice>

      <table className="register max-w-xl">
        <thead>
          <tr>
            <th scope="col">File</th>
            <th scope="col" className="cell-num">
              Accepted
            </th>
            <th scope="col" className="cell-num">
              Rejected
            </th>
          </tr>
        </thead>
        <tbody>
          {tables.map((s) => (
            <tr key={s.table}>
              <td>{s.file}</td>
              <td className="cell-num">{formatInt(result.accepted[s.table] ?? 0)}</td>
              <td className={`cell-num ${rejectedBy(s.table) ? 'font-semibold text-registrar' : ''}`}>
                {formatInt(rejectedBy(s.table))}
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      {rejected ? (
        <div>
          <h3 className="section-title">Rejected rows</h3>
          <table className="register register-stack mt-3">
            <thead>
              <tr>
                <th scope="col">File</th>
                <th scope="col" className="cell-num">
                  Row
                </th>
                <th scope="col">Reason</th>
              </tr>
            </thead>
            <tbody>
              {result.rejected.map((r, i) => (
                <tr key={`${r.table}-${r.row}-${i}`}>
                  <Cell label="File" className="whitespace-nowrap">
                    {r.table}.csv
                  </Cell>
                  <Cell label="Row" className="cell-num">
                    {formatInt(r.row)}
                  </Cell>
                  <Cell label="Reason">{r.reason}</Cell>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-meta text-ink-muted">Row 1 is the first row after the header.</p>
        </div>
      ) : null}

      {result.warnings?.length ? (
        <ul className="grid gap-1 text-caution">
          {result.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}

export default function StudentsPage() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const students = useStudents()
  const { studentId, setStudentId } = useIdentity()
  const [adminToken] = useAdminToken()
  const [files, setFiles] = useState<Partial<Record<StudentTable, ParsedFile>>>({})
  const [readErrors, setReadErrors] = useState<Partial<Record<StudentTable, string>>>({})
  const [inputKey, setInputKey] = useState(0)
  const loadId = useId()
  const listId = useId()

  const load = useMutation({
    mutationFn: (body: LoadStudentsRequest) => api.loadStudents(body, adminToken),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.students })
      void queryClient.invalidateQueries({ queryKey: queryKeys.health })
    },
  })

  async function onFile(spec: StudentFileSpec, file: File | null) {
    setReadErrors((e) => ({ ...e, [spec.table]: undefined }))
    if (!file) {
      setFiles((f) => ({ ...f, [spec.table]: undefined }))
      return
    }
    try {
      const result = parseCsv(await file.text())
      const missing = spec.columns.filter((c) => !result.headers.includes(c))
      const extra = result.headers.filter((h) => h && !spec.columns.includes(h))
      setFiles((f) => ({ ...f, [spec.table]: { name: file.name, result, missing, extra } }))
    } catch {
      setFiles((f) => ({ ...f, [spec.table]: undefined }))
      setReadErrors((e) => ({ ...e, [spec.table]: `Could not read ${file.name}. Save it as UTF-8 CSV and choose it again.` }))
    }
  }

  function onRemove(table: StudentTable) {
    setFiles((f) => ({ ...f, [table]: undefined }))
    setInputKey((k) => k + 1)
  }

  const chosen = STUDENT_FILES.filter((s) => files[s.table])
  const rowCount = chosen.reduce((sum, s) => sum + (files[s.table]?.result.rows.length ?? 0), 0)

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    const body: LoadStudentsRequest = {}
    for (const spec of chosen) {
      const parsed = files[spec.table]
      if (parsed) body[spec.table] = parsed.result.rows
    }
    load.mutate(body)
  }

  function askAs(id: string) {
    setStudentId(id)
    void navigate('/')
  }

  return (
    <div className="page-wide">
      <h1 className="page-title">Students</h1>
      <p className="page-intro">
        Load test students from the Annex C CSV files, then ask questions as any of them. Each row is checked on its
        own, so a bad row is reported without blocking the rest.
      </p>

      <section className="mt-10" aria-labelledby={loadId}>
        <h2 id={loadId} className="section-title">
          Load test students
        </h2>
        <p className="mt-1 max-w-[66ch] text-ink-muted">
          Choose any of the four files, with the column names below as the header row. They load in this order, so
          attendance and results can refer to students and courses in the same upload. IDs S9000 to S9999 and course
          codes starting with JDG are kept free for the judges’ own data.
        </p>

        <form className="mt-4" onSubmit={onSubmit}>
          <ul className="border-t border-rule-strong">
            {STUDENT_FILES.map((spec) => (
              <FileRow
                key={spec.table}
                spec={spec}
                parsed={files[spec.table]}
                readError={readErrors[spec.table]}
                inputKey={inputKey}
                onFile={onFile}
                onRemove={onRemove}
              />
            ))}
          </ul>

          <div className="mt-4">
            <AdminTokenField forceOpen={isAuthError(load.error)} />
          </div>

          <div className="mt-6 flex flex-wrap items-center gap-4">
            <button type="submit" className="btn btn-primary" disabled={chosen.length === 0 || load.isPending}>
              {load.isPending ? 'Loading students…' : 'Load students'}
            </button>
            <span className="text-ink-muted" role="status">
              {chosen.length === 0
                ? 'Choose at least one file.'
                : `${plural(chosen.length, 'file')}, ${plural(rowCount, 'row')} ready to send.`}
            </span>
          </div>
        </form>

        {load.isError ? (
          <div className="mt-6">
            <ErrorNotice title="Nothing was loaded" explanation={explainError(load.error, 'the student files')} />
          </div>
        ) : null}
        {load.isSuccess ? (
          <div className="mt-6">
            <LoadResult result={load.data} />
          </div>
        ) : null}
      </section>

      <section className="mt-14" aria-labelledby={listId}>
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
          <h2 id={listId} className="section-title">
            Students on record
          </h2>
          {students.data?.length ? <p className="text-ink-muted">{plural(students.data.length, 'student')}</p> : null}
        </div>
        {students.isPending ? (
          <p className="mt-4 text-ink-muted" role="status">
            Loading students…
          </p>
        ) : students.isError ? (
          <div className="mt-4">
            <ErrorNotice
              title="Could not load the students"
              explanation={explainError(students.error, 'loading students')}
              action={
                <button type="button" className="btn btn-secondary" onClick={() => void students.refetch()}>
                  Try again
                </button>
              }
            />
          </div>
        ) : students.data.length === 0 ? (
          <p className="mt-4 max-w-[60ch] text-ink-muted">
            No students yet. Load students.csv above, with courses.csv and attendance.csv if you want to ask about
            eligibility.
          </p>
        ) : (
          <table className="register register-stack mt-4">
            <thead>
              <tr>
                <th scope="col">Student</th>
                <th scope="col">Programme</th>
                <th scope="col" className="cell-num">
                  Batch
                </th>
                <th scope="col" className="cell-num">
                  Semester
                </th>
                <th scope="col">
                  <span className="sr-only">Action</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {students.data.map((s) => (
                <tr key={s.student_id}>
                  <Cell label="Student" className="cell-lead">
                    <p className="font-semibold">{s.full_name}</p>
                    <p className="cell-sub num">{s.student_id}</p>
                  </Cell>
                  <Cell label="Programme">{s.programme}</Cell>
                  <Cell label="Batch" className="cell-num">
                    {s.batch_year}
                  </Cell>
                  <Cell label="Semester" className="cell-num">
                    {s.current_semester}
                  </Cell>
                  <Cell label="" className="text-right">
                    {studentId === s.student_id ? (
                      <span className="text-ink-muted">Signed in</span>
                    ) : (
                      <button
                        type="button"
                        className="text-button whitespace-nowrap"
                        aria-label={`Ask as ${s.student_id}, ${s.full_name}`}
                        onClick={() => askAs(s.student_id)}
                      >
                        Ask as {s.student_id}
                      </button>
                    )}
                  </Cell>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
