import { answerTypeInfo } from '../lib/answerTypes'

/** Answer type as a word with a small square in its stamp ink. */
export function TypeMark({ type }: { type: string }) {
  const info = answerTypeInfo(type)
  return <span className={`type-mark tone-${info.tone}`}>{info.label}</span>
}
