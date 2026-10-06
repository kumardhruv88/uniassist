import { useEffect, useState } from 'react'
import type { PendingAsk } from '../lib/session'
import { recordMeta } from './AnswerRecord'

function useElapsedSeconds(since: number): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])
  return Math.max(0, Math.floor((now - since) / 1000))
}

/**
 * Holds the shape of the answer record while the backend works, so nothing jumps when the
 * answer arrives. The backend does not report its stage, so the text says what the whole
 * pipeline is doing and how long it has taken, not a made-up percentage.
 */
export function PendingRecord({
  pending,
  studentName,
  onCancel,
}: {
  pending: PendingAsk
  studentName?: string
  onCancel: () => void
}) {
  const seconds = useElapsedSeconds(pending.startedAt)
  const status = pending.studentId ? 'Checking your record and the rules…' : 'Finding the rules that apply…'
  const note =
    seconds >= 25
      ? 'This is taking longer than usual. The model may still be loading; keep waiting or cancel.'
      : seconds >= 5
        ? 'Answers from the local model usually take 5 to 15 seconds.'
        : 'Reading the question, then the records and documents in force.'

  return (
    <article className="file" aria-busy="true" aria-label="Answer in preparation">
      <span className="file-tab">New file</span>
      <div className="file-sheet">
        <div className="record-head">
          <div className="min-w-0">
            <p className="record-question">{pending.question}</p>
            <p className="record-meta">{recordMeta(pending.studentId, studentName, pending.asOfDate)}</p>
          </div>
          <div className="record-stamp" aria-hidden="true">
            <div className="stamp-slot" />
          </div>
        </div>
        <p className="record-answer text-ink-muted" role="status">
          {status}
        </p>
        <p className="record-explanation flex flex-wrap items-baseline gap-x-3 text-ink-muted">
          <span className="num font-semibold text-ink">{seconds} s</span>
          <span>{note}</span>
        </p>
        <div className="mt-6">
          <button type="button" className="btn btn-secondary" onClick={onCancel}>
            Cancel
          </button>
        </div>
      </div>
    </article>
  )
}
