import type { ReactNode } from 'react'
import type { ErrorExplanation } from '../lib/api'
import { Notice } from './Notice'

/**
 * A failure in words: what happened and what to do, the backend's hint when it adds something,
 * and the error code and trace id to quote when reporting it. Waiting out a rate limit is a
 * caution, not an error.
 */
export function ErrorNotice({ title, explanation, action }: { title: string; explanation: ErrorExplanation; action?: ReactNode }) {
  const reference = [
    explanation.code ? `Error ${explanation.code}` : null,
    explanation.traceId ? `trace ${explanation.traceId}` : null,
  ].filter(Boolean)
  return (
    <Notice title={title} action={action} tone={explanation.retryAfter !== null ? 'caution' : 'registrar'}>
      <p>{explanation.message}</p>
      {explanation.hint ? <p className="mt-1">{explanation.hint}</p> : null}
      {reference.length ? <p className="mt-2 text-meta">{reference.join(', ')}</p> : null}
    </Notice>
  )
}
