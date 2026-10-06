import type { ReactNode } from 'react'

interface NoticeProps {
  title: string
  children?: ReactNode
  tone?: 'registrar' | 'caution' | 'ledger' | 'ballpoint'
  action?: ReactNode
  role?: 'alert' | 'status'
}

/** A slip of paper with a coloured edge: errors, warnings and confirmations. */
export function Notice({ title, children, tone = 'registrar', action, role = 'alert' }: NoticeProps) {
  const toneVar = {
    registrar: 'var(--color-registrar)',
    caution: 'var(--color-caution)',
    ledger: 'var(--color-ledger)',
    ballpoint: 'var(--color-ballpoint)',
  }[tone]
  return (
    <div className="notice" role={role} style={{ ['--tone' as string]: toneVar }}>
      <p className="font-semibold">{title}</p>
      {children ? <div className="mt-1 text-ink-muted">{children}</div> : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  )
}
