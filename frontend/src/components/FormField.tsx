import { useId, type ReactNode } from 'react'

export interface ControlProps {
  id: string
  'aria-describedby'?: string
  'aria-invalid'?: true
  'aria-required'?: true
}

interface FormFieldProps {
  label: string
  hint?: ReactNode
  error?: string
  optional?: boolean
  required?: boolean
  className?: string
  children: (control: ControlProps) => ReactNode
}

/** Label, control, hint and error, wired together for assistive technology. */
export function FormField({ label, hint, error, optional, required, className, children }: FormFieldProps) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const errorId = error ? `${id}-error` : undefined
  const describedBy = [errorId, hintId].filter(Boolean).join(' ') || undefined
  return (
    <div className={`field ${className ?? ''}`}>
      <label htmlFor={id} className="field-label">
        {label}
        {optional ? <span className="font-normal text-ink-muted"> (optional)</span> : null}
      </label>
      {children({
        id,
        'aria-describedby': describedBy,
        'aria-invalid': error ? true : undefined,
        'aria-required': required ? true : undefined,
      })}
      {error ? (
        <p id={errorId} className="field-error">
          {error}
        </p>
      ) : null}
      {hint ? (
        <p id={hintId} className="field-hint">
          {hint}
        </p>
      ) : null}
    </div>
  )
}
