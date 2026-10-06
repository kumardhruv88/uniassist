import type { ReactNode } from 'react'

/**
 * A register table cell. On narrow screens rows stack and each cell shows its column name
 * (from data-label) beside one wrapped body, so multi-part content stays together.
 */
export function Cell({ label, className, children }: { label: string; className?: string; children: ReactNode }) {
  return (
    <td data-label={label} className={className}>
      <div className="min-w-0">{children}</div>
    </td>
  )
}

/** Shown in an empty cell; screen readers hear the word instead of a dash. */
export function EmptyCell({ word }: { word: string }) {
  return (
    <>
      <span aria-hidden="true" className="text-ink-muted">
        –
      </span>
      <span className="sr-only">{word}</span>
    </>
  )
}
