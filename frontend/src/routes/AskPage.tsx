import { useEffect, useId, useState, type FormEvent, type KeyboardEvent } from 'react'
import { AnswerRecord } from '../components/AnswerRecord'
import { ErrorNotice } from '../components/ErrorNotice'
import { Ledger } from '../components/Ledger'
import { PendingRecord } from '../components/PendingRecord'
import { useShell } from '../components/Shell'
import { TypeMark } from '../components/TypeMark'
import { formatDate, formatTime } from '../lib/format'
import { useStudents } from '../lib/queries'
import { useAsk, useIdentity, type AskEntry, type AskFailure } from '../lib/session'

interface Suggestion {
  short: string
  question: string
  personal: boolean
}

const SUGGESTIONS: Suggestion[] = [
  { short: 'Minimum attendance', question: 'What is the minimum attendance for end-semester exams?', personal: false },
  { short: 'Supplementary exam', question: 'How do I apply for the supplementary exam?', personal: false },
  { short: 'My CS201 eligibility', question: 'Am I eligible for the CS201 end-semester exam?', personal: true },
  {
    short: 'Placements after a supplementary',
    question: 'If I pass the Data Structures supplementary, can I sit for placements?',
    personal: true,
  },
  {
    short: '65% with a medical certificate',
    question: 'Is 65% attendance enough if I have a medical certificate?',
    personal: false,
  },
  { short: 'Antarctica scholarship', question: 'What is the scholarship for studying in Antarctica?', personal: false },
]

function SuggestionList({ onPick }: { onPick: (question: string) => void }) {
  const headingId = useId()
  return (
    <section className="mt-12" aria-labelledby={headingId}>
      <h2 id={headingId} className="section-title">
        Try one of these
      </h2>
      <ul className="mt-3 grid gap-2.5">
        {SUGGESTIONS.map((s) => (
          <li key={s.question} className="flex flex-wrap items-baseline gap-x-3 gap-y-0.5">
            <button type="button" className="text-button text-button-quiet text-read" onClick={() => onPick(s.question)}>
              {s.question}
            </button>
            {s.personal ? <span className="text-meta text-ink-muted">uses your record</span> : null}
          </li>
        ))}
      </ul>
      <p className="mt-10 max-w-[60ch] text-ink-muted">
        Every answer is filed with its sources and stamped with how it was reached: calculated from your records, taken
        from the rules, or not on record at all.
      </p>
    </section>
  )
}

function SuggestionRow({ onPick, disabled }: { onPick: (question: string) => void; disabled: boolean }) {
  return (
    <div className="mt-5 flex flex-wrap items-baseline gap-x-4 gap-y-1.5">
      <span className="text-ink-muted">Try</span>
      {SUGGESTIONS.map((s) => (
        <button
          key={s.question}
          type="button"
          className="text-button text-button-quiet"
          title={s.question}
          disabled={disabled}
          onClick={() => onPick(s.question)}
        >
          {s.short}
        </button>
      ))}
    </div>
  )
}

/** Seconds left until `at` (ms since epoch), ticking once a second; 0 when there is nothing to wait for. */
function useSecondsUntil(at: number | null): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (at === null || at <= Date.now()) return
    const timer = setInterval(() => {
      const t = Date.now()
      setNow(t)
      if (t >= at) clearInterval(timer)
    }, 1000)
    return () => clearInterval(timer)
  }, [at])
  return at === null ? 0 : Math.max(0, Math.ceil((at - now) / 1000))
}

function AskFailureNotice({ failure, onRetry }: { failure: AskFailure; onRetry: () => void }) {
  const wait = useSecondsUntil(failure.retryAt)
  return (
    <ErrorNotice
      title="Could not get an answer"
      explanation={failure}
      action={
        <button type="button" className="btn btn-secondary" onClick={onRetry} disabled={wait > 0}>
          {wait > 0 ? `Try again in ${wait} s` : 'Try again'}
        </button>
      }
    />
  )
}

function History({ entries, onShow }: { entries: AskEntry[]; onShow: (id: string) => void }) {
  const headingId = useId()
  if (entries.length === 0) return null
  return (
    <section className="mt-14" aria-labelledby={headingId}>
      <h2 id={headingId} className="section-title">
        Earlier in this session
      </h2>
      <ul className="mt-2 border-t border-rule-strong">
        {entries.map((e) => (
          <li key={e.id} className="border-b border-rule-strong">
            <button
              type="button"
              className="grid w-full grid-cols-[3.25rem_minmax(0,1fr)] gap-x-3 gap-y-1 py-3 text-left hover:bg-desk-shade sm:grid-cols-[3.25rem_minmax(0,1fr)_auto] sm:items-baseline"
              onClick={() => onShow(e.id)}
            >
              <span className="num text-meta text-ink-muted">{formatTime(e.askedAt)}</span>
              <span className="min-w-0 truncate" title={e.response.meta?.standalone_question ? `Typed as “${e.question}”` : undefined}>
                {e.response.meta?.standalone_question ?? e.question}
              </span>
              <span className="col-start-2 sm:col-start-auto">
                <TypeMark type={e.response.answer_type} />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default function AskPage() {
  const { entries, current, pending, failure, conversationTurns, ask, cancel, show, newConversation } = useAsk()
  const { studentId, asOfDate } = useIdentity()
  const { focusIdentity } = useShell()
  const students = useStudents()
  const [draft, setDraft] = useState('')
  const [emptyHint, setEmptyHint] = useState(false)
  const questionId = useId()
  const hintId = useId()

  const nameOf = (id: string | null) => (id ? students.data?.find((s) => s.student_id === id)?.full_name : undefined)
  const signedIn = studentId ? (nameOf(studentId) ? `${studentId}, ${nameOf(studentId)}` : studentId) : null
  const hasActivity = Boolean(current || pending || failure || entries.length)
  const earlier = entries.filter((e) => e.id !== current?.id)

  function submit(question: string) {
    if (!question.trim()) {
      setEmptyHint(true)
      document.getElementById(questionId)?.focus()
      return
    }
    setEmptyHint(false)
    ask(question)
  }

  function pick(question: string) {
    setDraft(question)
    submit(question)
  }

  function onSubmit(event: FormEvent) {
    event.preventDefault()
    submit(draft)
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submit(draft)
    }
  }

  return (
    <div className="page">
      <h1 className="page-title">Ask about rules, exams, attendance or your record</h1>

      <form className="mt-6" onSubmit={onSubmit}>
        <label htmlFor={questionId} className="sr-only">
          Your question
        </label>
        <div className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <textarea
            id={questionId}
            className="control min-h-[52px] max-h-56 resize-none px-4 py-3 text-read [field-sizing:content]"
            rows={1}
            value={draft}
            placeholder="Am I eligible for the CS201 end-semester exam?"
            onChange={(e) => {
              setDraft(e.target.value)
              if (emptyHint) setEmptyHint(false)
            }}
            onKeyDown={onKeyDown}
            aria-describedby={hintId}
            aria-invalid={emptyHint || undefined}
          />
          <button type="submit" className="btn btn-primary h-[52px] min-w-24 px-6" disabled={Boolean(pending)}>
            {pending ? 'Asking…' : 'Ask'}
          </button>
        </div>
        <div id={hintId} className="mt-2.5 flex flex-wrap items-baseline justify-between gap-x-6 gap-y-1 text-meta text-ink-muted">
          {emptyHint ? (
            <p className="field-error">Type a question first, or pick one of the examples.</p>
          ) : (
            <p>
              {signedIn ? (
                <>
                  Asking as <span className="font-semibold text-ink">{signedIn}</span>.
                </>
              ) : (
                'Asking as no one: general questions only.'
              )}{' '}
              Rules as of {formatDate(asOfDate)}.{' '}
              <button type="button" className="text-button" onClick={focusIdentity}>
                Change
              </button>
            </p>
          )}
          <p className="hidden rail:block">Enter to ask, Shift+Enter for a new line</p>
        </div>
        {conversationTurns > 0 ? (
          <p className="mt-1.5 text-meta text-ink-muted">
            Follow-ups such as “what about CS202?” are read with your last {conversationTurns === 1 ? 'question' : 'questions'}.{' '}
            <button
              type="button"
              className="text-button"
              onClick={() => {
                newConversation()
                setDraft('')
              }}
            >
              New conversation
            </button>
          </p>
        ) : null}
      </form>

      {hasActivity ? <SuggestionRow onPick={pick} disabled={Boolean(pending)} /> : <SuggestionList onPick={pick} />}

      <section className="mt-10" aria-live="polite" aria-label="Answer">
        {pending ? (
          <PendingRecord pending={pending} studentName={nameOf(pending.studentId)} onCancel={cancel} />
        ) : failure ? (
          <AskFailureNotice failure={failure} onRetry={() => submit(failure.question)} />
        ) : current ? (
          <AnswerRecord
            entry={current}
            studentName={nameOf(current.studentId)}
            // The backend keeps the conversation, so the option alone is enough: it is read with the question.
            onChooseOption={(option) => pick(option)}
          />
        ) : null}
      </section>

      {current && !pending && !failure ? <Ledger key={current.id} entry={current} /> : null}

      <History entries={earlier} onShow={show} />
    </div>
  )
}
