import type { AnswerType } from './types'

export type InkTone = 'ledger' | 'stamp' | 'registrar' | 'ballpoint' | 'muted'

export interface AnswerTypeInfo {
  /** Big line of the rubber stamp. Written in sentence case; CSS sets it in capitals. */
  stamp: string
  /** Small line of the stamp, if any. */
  stampNote: string | null
  /** Stamp-pad ink colour. */
  tone: InkTone
  /** Name used in lists and tables. */
  label: string
  /** One sentence on what the stamp means. */
  meaning: string
}

export const ANSWER_TYPES: Record<AnswerType, AnswerTypeInfo> = {
  calculated: {
    stamp: 'Calculated',
    stampNote: 'from your records',
    tone: 'ledger',
    label: 'Calculated',
    meaning: 'Worked out from your records under the rules in force.',
  },
  retrieved_fact: {
    stamp: 'As per rules',
    stampNote: 'from official documents',
    tone: 'stamp',
    label: 'As per rules',
    meaning: 'Taken from official documents in force on that date.',
  },
  conflict_flagged: {
    stamp: 'Sources disagree',
    stampNote: 'ask the issuing office',
    tone: 'registrar',
    label: 'Sources disagree',
    meaning: 'Two sources of equal standing say different things.',
  },
  clarification_needed: {
    stamp: 'Need one detail',
    stampNote: null,
    tone: 'ballpoint',
    label: 'Needs one detail',
    meaning: 'One detail is missing before the question can be answered.',
  },
  refused: {
    stamp: 'Not permitted',
    stampNote: null,
    tone: 'registrar',
    label: 'Not permitted',
    meaning: 'The request is outside what this desk may answer.',
  },
  not_found: {
    stamp: 'Not on record',
    stampNote: 'no authorised source covers this',
    tone: 'muted',
    label: 'Not on record',
    meaning: 'No authorised source in force covers the question.',
  },
}

export function answerTypeInfo(type: string): AnswerTypeInfo {
  return (ANSWER_TYPES as Record<string, AnswerTypeInfo | undefined>)[type] ?? {
    stamp: type,
    stampNote: null,
    tone: 'muted',
    label: type,
    meaning: '',
  }
}
