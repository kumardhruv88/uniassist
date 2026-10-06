import { answerTypeInfo } from '../lib/answerTypes'
import type { AnswerType } from '../lib/types'

interface StampProps {
  type: AnswerType
  /** "small" for lists and the audit view. */
  size?: 'full' | 'small'
  /** Play the landing motion. Key the element by trace id so it lands once per answer. */
  lands?: boolean
}

/**
 * The answer-type stamp: a double-ruled rubber stamp in stamp-pad ink, set at -4 degrees.
 * The words are stored in sentence case and set in capitals by CSS, so screen readers
 * read words rather than spelling out letters.
 */
export function Stamp({ type, size = 'full', lands = false }: StampProps) {
  const info = answerTypeInfo(type)
  const classes = ['stamp', `tone-${info.tone}`, size === 'small' ? 'stamp-small' : '', lands ? 'stamp-lands' : '']
    .filter(Boolean)
    .join(' ')
  return (
    <div className={classes}>
      <div className="stamp-inner">
        <span className="stamp-word">{info.stamp}</span>
        {info.stampNote ? <span className="stamp-note">{info.stampNote}</span> : null}
      </div>
    </div>
  )
}
