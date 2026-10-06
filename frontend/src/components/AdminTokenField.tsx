import { useId, useState } from 'react'
import { useAdminToken } from '../lib/adminToken'

/**
 * Most setups need no token, so the field stays folded away. It opens on its own when a token
 * is already set, or when the server has just refused for want of one (`forceOpen`).
 */
export function AdminTokenField({ forceOpen = false }: { forceOpen?: boolean }) {
  const id = useId()
  const [token, setToken] = useAdminToken()
  const [open, setOpen] = useState(() => token.length > 0)

  return (
    <details className="disclosure" open={open || forceOpen} onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>
        Admin token
        <span className="font-normal text-ink-muted">{token ? 'set for this tab' : 'only if the server asks for one'}</span>
      </summary>
      <div className="field mt-1 max-w-sm">
        <label htmlFor={id} className="field-label">
          Token
        </label>
        <input
          id={id}
          type="password"
          autoComplete="off"
          spellCheck={false}
          className="control"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          aria-describedby={`${id}-hint`}
        />
        <p id={`${id}-hint`} className="field-hint">
          Needed when the backend was started with ADMIN_TOKEN. It is sent as X-Admin-Token and kept in this tab only.
        </p>
      </div>
    </details>
  )
}
