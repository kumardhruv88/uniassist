import { useId, useMemo, useRef, useState, type FormEvent } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { AdminTokenField } from '../components/AdminTokenField'
import { Cell, EmptyCell } from '../components/Cell'
import { FormField } from '../components/FormField'
import { ErrorNotice } from '../components/ErrorNotice'
import { Notice } from '../components/Notice'
import { ACCEPTED_FILE_TYPES, AUTHORITY_LEVELS, DOC_TYPES, MAX_UPLOAD_BYTES, authorityName } from '../lib/annex'
import { useAdminToken } from '../lib/adminToken'
import { api, explainError, isAuthError } from '../lib/api'
import { formatBytes, formatDate, formatDocRef, formatInt, plural, todayISO } from '../lib/format'
import { queryKeys, useSources } from '../lib/queries'
import { useIdentity } from '../lib/session'
import type { DocType, IngestMetadata, IngestResponse, SourceDoc } from '../lib/types'

// ---------------------------------------------------------------------------------------------
// Register

/** "ALL" / "2023+" -> "Batches from 2023"; "B.Tech CSE" / "ALL" -> "B.Tech CSE". */
function scopeText(programmes: string, batches: string): string {
  const allProgrammes = programmes.trim().toUpperCase() === 'ALL'
  const allBatches = batches.trim().toUpperCase() === 'ALL'
  if (allProgrammes && allBatches) return 'Everyone'
  const who = allProgrammes ? null : programmes.split(';').map((p) => p.trim()).filter(Boolean).join(', ')
  let when: string | null = null
  if (!allBatches) {
    const from = /^(\d{4})\+$/.exec(batches.trim())
    const range = /^(\d{4})\s*-\s*(\d{4})$/.exec(batches.trim())
    when = from ? `batches from ${from[1]}` : range ? `batches ${range[1]} to ${range[2]}` : `batch ${batches.trim()}`
  }
  const text = [who, when].filter(Boolean).join(', ')
  return text.charAt(0).toUpperCase() + text.slice(1)
}

function forceStatus(doc: SourceDoc, asOf: string): { word: string; tone: string } {
  if (doc.effective_from > asOf) return { word: 'Not yet in force', tone: 'var(--color-caution)' }
  if (doc.effective_to && doc.effective_to < asOf) return { word: 'No longer in force', tone: 'var(--color-graphite)' }
  return { word: 'In force', tone: 'var(--color-ledger)' }
}

function RegisterTable({ docs, asOf, highlight }: { docs: SourceDoc[]; asOf: string; highlight: string | null }) {
  return (
    <table className="register register-stack mt-4">
      <thead>
        <tr>
          <th scope="col">Document</th>
          <th scope="col">Authority and issuer</th>
          <th scope="col">In force</th>
          <th scope="col">Applies to</th>
          <th scope="col">Replaces</th>
          <th scope="col" className="cell-num">
            Passages
          </th>
        </tr>
      </thead>
      <tbody>
        {docs.map((doc) => {
          const status = forceStatus(doc, asOf)
          const replaces = doc.supersedes ? doc.supersedes.split(';').map((s) => s.trim()).filter(Boolean) : []
          const isNew = doc.doc_id === highlight
          return (
            <tr key={doc.doc_id} className={isNew ? 'is-new' : undefined}>
              <Cell label="Document" className="cell-lead min-w-60">
                <p className="font-semibold">{doc.title}</p>
                <p className="cell-sub flex flex-wrap items-center gap-x-2.5 gap-y-1">
                  <span>{doc.doc_id}</span>
                  {doc.version ? <span>version {doc.version}</span> : null}
                  {doc.synthetic === 'Y' ? <span className="tag">Synthetic</span> : null}
                  {isNew ? <span className="tag">Just added</span> : null}
                </p>
                {doc.warnings.map((w) => (
                  <p key={w} className="cell-sub text-caution">
                    {w}
                  </p>
                ))}
              </Cell>
              <Cell label="Authority">
                <p className="whitespace-nowrap">
                  <span className="num mr-2 text-ink-muted">{doc.authority_level}</span>
                  {authorityName(doc.authority_level)}
                </p>
                <p className="cell-sub">{doc.issuer}</p>
              </Cell>
              <Cell label="In force">
                <p className="num whitespace-nowrap">
                  {doc.effective_to
                    ? `${formatDate(doc.effective_from, 'short')} to ${formatDate(doc.effective_to, 'short')}`
                    : `From ${formatDate(doc.effective_from, 'short')}`}
                </p>
                <p className="cell-sub flex items-center gap-1.5">
                  <span className="dot" style={{ ['--tone' as string]: status.tone }} />
                  {status.word}
                </p>
              </Cell>
              <Cell label="Applies to">{scopeText(doc.scope_programmes, doc.scope_batches)}</Cell>
              <Cell label="Replaces">
                {replaces.length ? (
                  replaces.map((ref) => (
                    <p key={ref} className="whitespace-nowrap">
                      {formatDocRef(ref)}
                    </p>
                  ))
                ) : (
                  <EmptyCell word="Nothing" />
                )}
              </Cell>
              <Cell label="Passages" className="cell-num">
                {formatInt(doc.chunks_indexed)}
              </Cell>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ---------------------------------------------------------------------------------------------
// Add a document

interface DocForm {
  doc_id: string
  title: string
  issuer: string
  authority_level: string
  doc_type: DocType
  version: string
  effective_from: string
  effective_to: string
  supersedes: string
  scope_programmes: string
  scope_batches: string
  provenance: string
  retrieved_on: string
  synthetic: 'Y' | 'N'
}

type FormErrors = Partial<Record<keyof DocForm | 'file', string>>

function emptyForm(): DocForm {
  return {
    doc_id: '',
    title: '',
    issuer: '',
    authority_level: '2',
    doc_type: 'circular',
    version: '',
    effective_from: '',
    effective_to: '',
    supersedes: '',
    scope_programmes: 'ALL',
    scope_batches: 'ALL',
    provenance: '',
    retrieved_on: todayISO(),
    synthetic: 'N',
  }
}

const DOC_ID = /^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$/
const ACCEPTED_EXTENSIONS = ACCEPTED_FILE_TYPES.split(',')

function validate(form: DocForm, file: File | null): FormErrors {
  const errors: FormErrors = {}
  if (!file) errors.file = 'Choose the document file.'
  else if (file.size > MAX_UPLOAD_BYTES) errors.file = `The file is ${formatBytes(file.size)}; the limit is 25 MB.`
  else if (!ACCEPTED_EXTENSIONS.some((ext) => file.name.toLowerCase().endsWith(ext))) {
    errors.file = 'Use a PDF, Word (.docx), text, Markdown, HTML, PNG or JPG file.'
  }
  if (!form.doc_id.trim()) errors.doc_id = 'Enter a document ID.'
  else if (!DOC_ID.test(form.doc_id.trim())) errors.doc_id = 'Use 2 to 64 letters, digits, dots, dashes or underscores, starting with a letter or digit.'
  if (!form.title.trim()) errors.title = 'Enter the title.'
  if (!form.issuer.trim()) errors.issuer = 'Enter the office that issued it.'
  if (!form.effective_from) errors.effective_from = 'Enter the date it takes effect.'
  if (form.effective_to && form.effective_from && form.effective_to < form.effective_from) {
    errors.effective_to = 'The end date is before the start date.'
  }
  if (!form.provenance.trim()) errors.provenance = 'Say where the file came from.'
  return errors
}

function toMetadata(form: DocForm): IngestMetadata {
  const orNull = (v: string) => (v.trim() ? v.trim() : null)
  return {
    doc_id: form.doc_id.trim(),
    title: form.title.trim(),
    issuer: form.issuer.trim(),
    authority_level: Number(form.authority_level),
    doc_type: form.doc_type,
    version: orNull(form.version),
    effective_from: form.effective_from,
    effective_to: orNull(form.effective_to),
    supersedes: orNull(form.supersedes),
    scope_programmes: form.scope_programmes.trim() || 'ALL',
    scope_batches: form.scope_batches.trim() || 'ALL',
    provenance: form.provenance.trim(),
    retrieved_on: orNull(form.retrieved_on),
    synthetic: form.synthetic,
  }
}

const RESULT_WORDS: Record<IngestResponse['status'], { title: string; tone: 'ledger' | 'ballpoint' | 'registrar' }> = {
  indexed: { title: 'Indexed', tone: 'ledger' },
  replaced: { title: 'Replaced the earlier version', tone: 'ledger' },
  unchanged: { title: 'Already indexed, nothing changed', tone: 'ballpoint' },
  failed: { title: 'Could not index the document', tone: 'registrar' },
}

function IngestResult({ result }: { result: IngestResponse }) {
  const words = RESULT_WORDS[result.status] ?? RESULT_WORDS.failed
  const usable = result.status === 'indexed' || result.status === 'replaced'
  return (
    <Notice title={`${result.doc_id}: ${words.title}`} tone={words.tone} role="status">
      <p>
        {plural(result.chunks_indexed, 'passage')} indexed.
        {usable ? ' The next question can use it.' : ''}
      </p>
      <p className="mt-1">
        {result.rules_added.length
          ? `Rules added: ${result.rules_added.join(', ')}.`
          : 'No rules were extracted from it.'}
      </p>
      {result.warnings.length ? (
        <ul className="mt-2 grid gap-1 text-caution">
          {result.warnings.map((w) => (
            <li key={w}>{w}</li>
          ))}
        </ul>
      ) : null}
    </Notice>
  )
}

function AddDocumentPanel({ onClose, onAdded, canClose }: { onClose: () => void; onAdded: (docId: string) => void; canClose: boolean }) {
  const queryClient = useQueryClient()
  const [adminToken] = useAdminToken()
  const [form, setForm] = useState<DocForm>(emptyForm)
  const [file, setFile] = useState<File | null>(null)
  const [fileKey, setFileKey] = useState(0)
  const [errors, setErrors] = useState<FormErrors>({})
  const [dragging, setDragging] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  const headingId = useId()
  const fileHintId = useId()

  const ingest = useMutation({
    mutationFn: (input: { file: File; metadata: IngestMetadata }) => api.ingest(input.file, input.metadata, adminToken),
    onSuccess: (result) => {
      void queryClient.invalidateQueries({ queryKey: queryKeys.sources })
      void queryClient.invalidateQueries({ queryKey: queryKeys.health })
      if (result.status === 'indexed' || result.status === 'replaced') {
        onAdded(result.doc_id)
        setForm(emptyForm())
        setFile(null)
        setFileKey((k) => k + 1)
      }
    },
  })

  const level = AUTHORITY_LEVELS.find((a) => String(a.level) === form.authority_level)

  function set<K extends keyof DocForm>(key: K, value: DocForm[K]) {
    setForm((f) => ({ ...f, [key]: value }))
    if (errors[key]) setErrors((e) => ({ ...e, [key]: undefined }))
  }

  function chooseFile(next: File | null) {
    setFile(next)
    if (errors.file) setErrors((e) => ({ ...e, file: undefined }))
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    const found = validate(form, file)
    setErrors(found)
    const first = Object.keys(found)[0]
    if (first) {
      const target = formRef.current?.querySelector<HTMLElement>(`[name="${first}"]`)
      target?.focus()
      return
    }
    if (file) ingest.mutate({ file, metadata: toMetadata(form) })
  }

  return (
    <section className="mt-8 border-y border-rule-strong py-8" aria-labelledby={headingId}>
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 id={headingId} className="section-title">
          Add a document
        </h2>
        {canClose ? (
          <button type="button" className="text-button" onClick={onClose}>
            Close
          </button>
        ) : null}
      </div>
      <p className="mt-1 max-w-[62ch] text-ink-muted">
        Upload the file and describe it as the register does (Annex B). It is searchable as soon as it is indexed; the
        next question can use it.
      </p>

      {ingest.isSuccess ? (
        <div className="mt-5">
          <IngestResult result={ingest.data} />
        </div>
      ) : null}
      {ingest.isError ? (
        <div className="mt-5">
          <ErrorNotice title="The document was not added" explanation={explainError(ingest.error, 'the document')} />
        </div>
      ) : null}

      <form ref={formRef} className="mt-6 grid gap-x-6 gap-y-5 sm:grid-cols-2" onSubmit={onSubmit} noValidate>
        <div className="field sm:col-span-2">
          <span className="field-label" id={`${fileHintId}-label`}>
            File
          </span>
          <label
            className={`dropzone ${dragging ? 'is-over' : ''}`}
            onDragOver={(e) => {
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              chooseFile(e.dataTransfer.files[0] ?? null)
            }}
          >
            <input
              key={fileKey}
              name="file"
              type="file"
              accept={ACCEPTED_FILE_TYPES}
              className="sr-only"
              aria-labelledby={`${fileHintId}-label`}
              aria-describedby={fileHintId}
              aria-invalid={errors.file ? true : undefined}
              onChange={(e) => chooseFile(e.target.files?.[0] ?? null)}
            />
            <span className="font-semibold text-ballpoint">{file ? file.name : 'Choose a file'}</span>
            <span id={fileHintId} className="text-meta text-ink-muted">
              {file
                ? `${formatBytes(file.size)}. Choose or drop another file to replace it.`
                : 'Or drop it here. PDF, Word (.docx), text, Markdown, HTML, PNG or JPG, up to 25 MB. Images are read with OCR.'}
            </span>
          </label>
          {errors.file ? <p className="field-error">{errors.file}</p> : null}
        </div>

        <FormField label="Document ID" error={errors.doc_id} required hint="For example ACAD-2026-09. Reusing an ID replaces that document.">
          {(p) => (
            <input {...p} name="doc_id" className="control" value={form.doc_id} autoComplete="off" spellCheck={false} onChange={(e) => set('doc_id', e.target.value)} />
          )}
        </FormField>
        <FormField label="Title" error={errors.title} required hint="As printed on the document.">
          {(p) => <input {...p} name="title" className="control" value={form.title} onChange={(e) => set('title', e.target.value)} />}
        </FormField>

        <FormField label="Issuer" error={errors.issuer} required hint="The office that issued it, such as the Dean of Academic Affairs.">
          {(p) => <input {...p} name="issuer" className="control" value={form.issuer} onChange={(e) => set('issuer', e.target.value)} />}
        </FormField>
        <FormField label="Version" optional>
          {(p) => <input {...p} name="version" className="control" value={form.version} onChange={(e) => set('version', e.target.value)} />}
        </FormField>

        <FormField label="Authority" hint={level?.explanation}>
          {(p) => (
            <select
              {...p}
              name="authority_level"
              className="control"
              value={form.authority_level}
              onChange={(e) => {
                const next = AUTHORITY_LEVELS.find((a) => String(a.level) === e.target.value)
                setForm((f) => ({ ...f, authority_level: e.target.value, doc_type: next?.docType ?? f.doc_type }))
              }}
            >
              {AUTHORITY_LEVELS.map((a) => (
                <option key={a.level} value={a.level}>
                  {a.level} {a.name}
                </option>
              ))}
            </select>
          )}
        </FormField>
        <FormField label="Document type" hint="Set from the authority; change it if the document says otherwise.">
          {(p) => (
            <select {...p} name="doc_type" className="control" value={form.doc_type} onChange={(e) => set('doc_type', e.target.value as DocType)}>
              {DOC_TYPES.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          )}
        </FormField>

        <FormField label="In force from" error={errors.effective_from} required>
          {(p) => <input {...p} name="effective_from" type="date" className="control" value={form.effective_from} onChange={(e) => set('effective_from', e.target.value)} />}
        </FormField>
        <FormField label="In force until" optional error={errors.effective_to} hint="Leave empty if it has no end date.">
          {(p) => <input {...p} name="effective_to" type="date" className="control" value={form.effective_to} onChange={(e) => set('effective_to', e.target.value)} />}
        </FormField>

        <FormField
          className="sm:col-span-2"
          label="Replaces"
          optional
          hint="Document ID, with # and a clause to replace one clause, such as ACAD-REG-2024#7.2. Separate several with semicolons."
        >
          {(p) => <input {...p} name="supersedes" className="control" value={form.supersedes} spellCheck={false} onChange={(e) => set('supersedes', e.target.value)} />}
        </FormField>

        <FormField label="Programmes it applies to" hint="ALL, or programmes such as B.Tech CSE; B.Tech ECE.">
          {(p) => <input {...p} name="scope_programmes" className="control" value={form.scope_programmes} onChange={(e) => set('scope_programmes', e.target.value)} />}
        </FormField>
        <FormField label="Batches it applies to" hint="ALL, 2023+, 2023 or 2022-2024.">
          {(p) => <input {...p} name="scope_batches" className="control" value={form.scope_batches} onChange={(e) => set('scope_batches', e.target.value)} />}
        </FormField>

        <FormField className="sm:col-span-2" label="Provenance" error={errors.provenance} required hint="Where the file came from: a URL, or the office and notice board.">
          {(p) => <input {...p} name="provenance" className="control" value={form.provenance} onChange={(e) => set('provenance', e.target.value)} />}
        </FormField>

        <FormField label="Retrieved on" optional>
          {(p) => <input {...p} name="retrieved_on" type="date" className="control" value={form.retrieved_on} onChange={(e) => set('retrieved_on', e.target.value)} />}
        </FormField>
        <fieldset className="field">
          <legend className="field-label mb-1.5">Synthetic</legend>
          <div className="flex min-h-11 flex-wrap items-center gap-x-6 gap-y-2">
            {(
              [
                ['N', 'No, an official document'],
                ['Y', 'Yes, written for testing'],
              ] as const
            ).map(([value, text]) => (
              <label key={value} className="inline-flex cursor-pointer items-center gap-2">
                <input
                  type="radio"
                  name="synthetic"
                  value={value}
                  checked={form.synthetic === value}
                  onChange={() => set('synthetic', value)}
                  className="size-4 accent-ballpoint"
                />
                {text}
              </label>
            ))}
          </div>
        </fieldset>

        <div className="sm:col-span-2">
          <AdminTokenField forceOpen={isAuthError(ingest.error)} />
        </div>

        <div className="flex flex-wrap items-center gap-4 sm:col-span-2">
          <button type="submit" className="btn btn-primary" disabled={ingest.isPending}>
            {ingest.isPending ? 'Adding document…' : 'Add document'}
          </button>
          {ingest.isPending ? (
            <span className="text-ink-muted" role="status">
              Reading, splitting and indexing the file. Large PDFs and scans take longer.
            </span>
          ) : null}
        </div>
      </form>
    </section>
  )
}

// ---------------------------------------------------------------------------------------------

export default function DocumentsPage() {
  const sources = useSources()
  const { asOfDate } = useIdentity()
  const [formOpen, setFormOpen] = useState(false)
  const [lastAdded, setLastAdded] = useState<string | null>(null)
  const registerId = useId()

  const docs = useMemo(
    () =>
      (sources.data ?? []).toSorted(
        (a, b) => a.authority_level - b.authority_level || b.effective_from.localeCompare(a.effective_from),
      ),
    [sources.data],
  )
  const passages = docs.reduce((sum, d) => sum + d.chunks_indexed, 0)
  const registerEmpty = sources.isSuccess && docs.length === 0
  const open = formOpen || registerEmpty

  return (
    <div className="page-wide">
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
        <div>
          <h1 className="page-title">Documents</h1>
          <p className="page-intro">
            The official sources UniAssist can cite. When two disagree, the higher authority wins; at equal authority,
            the one in force more recently.
          </p>
        </div>
        {!open ? (
          <button type="button" className="btn btn-primary" onClick={() => setFormOpen(true)}>
            Add a document
          </button>
        ) : null}
      </div>

      {open ? (
        <AddDocumentPanel onClose={() => setFormOpen(false)} onAdded={setLastAdded} canClose={!registerEmpty} />
      ) : null}

      <section className="mt-10" aria-labelledby={registerId}>
        <div className="flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1">
          <h2 id={registerId} className="section-title">
            Source register
          </h2>
          {sources.isSuccess && docs.length > 0 ? (
            <p className="text-ink-muted">
              {plural(docs.length, 'document')}, {plural(passages, 'passage')} indexed. Status as of {formatDate(asOfDate)}.
            </p>
          ) : null}
        </div>

        {sources.isPending ? (
          <p className="mt-4 text-ink-muted" role="status">
            Loading the register…
          </p>
        ) : sources.isError ? (
          <div className="mt-4">
            <ErrorNotice
              title="Could not load the register"
              explanation={explainError(sources.error, 'loading the register')}
              action={
                <button type="button" className="btn btn-secondary" onClick={() => void sources.refetch()}>
                  Try again
                </button>
              }
            />
          </div>
        ) : registerEmpty ? (
          <p className="mt-4 max-w-[60ch] text-ink-muted">
            No documents yet. Add the regulations first, then circulars and notices; answers can only cite what is here.
          </p>
        ) : (
          <RegisterTable docs={docs} asOf={asOfDate} highlight={lastAdded} />
        )}
      </section>
    </div>
  )
}
