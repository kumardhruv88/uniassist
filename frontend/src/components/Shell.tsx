import { createContext, use, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Outlet, ScrollRestoration, useLocation } from 'react-router'
import * as Dialog from '@radix-ui/react-dialog'
import { RailContent } from './RailContent'
import { Wordmark } from './Wordmark'

interface ShellValue {
  /** Moves the person to the "Signed in as" control: the rail on wide screens, the menu sheet on narrow ones. */
  focusIdentity: () => void
}

const ShellContext = createContext<ShellValue | null>(null)

export function useShell(): ShellValue {
  const value = use(ShellContext)
  if (!value) throw new Error('useShell must be used inside <Shell>')
  return value
}

export function Shell() {
  const [menuOpen, setMenuOpen] = useState(false)
  const mainRef = useRef<HTMLElement>(null)
  const { pathname } = useLocation()
  const lastPath = useRef(pathname)

  // After a client-side navigation, move focus to the new page so screen readers announce it.
  useEffect(() => {
    if (lastPath.current === pathname) return
    lastPath.current = pathname
    mainRef.current?.focus({ preventScroll: true })
  }, [pathname])

  const focusIdentity = useCallback(() => {
    const railSelect = document.getElementById('rail-student')
    if (railSelect && railSelect.offsetParent !== null) {
      railSelect.focus()
      return
    }
    setMenuOpen(true)
  }, [])

  const closeMenu = useCallback(() => setMenuOpen(false), [])
  const shell = useMemo(() => ({ focusIdentity }), [focusIdentity])

  return (
    <ShellContext value={shell}>
      <a href="#main" className="skip-link sr-only-focusable">
        Skip to main content
      </a>
      <div className="app">
        <header className="topbar">
          <Wordmark />
          <Dialog.Root open={menuOpen} onOpenChange={setMenuOpen}>
            <Dialog.Trigger asChild>
              <button type="button" className="btn btn-secondary">
                Menu
              </button>
            </Dialog.Trigger>
            <Dialog.Portal>
              <Dialog.Overlay className="sheet-overlay" />
              <Dialog.Content className="sheet" aria-describedby={undefined}>
                <div className="flex items-center justify-between gap-3 px-6 pt-4">
                  <Dialog.Title className="field-label-quiet">Menu</Dialog.Title>
                  <Dialog.Close asChild>
                    <button type="button" className="btn btn-secondary">
                      Close
                    </button>
                  </Dialog.Close>
                </div>
                <RailContent idPrefix="sheet" onNavigate={closeMenu} showWordmark={false} />
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        </header>

        <aside className="rail" aria-label="Identity, date and navigation">
          <div className="rail-sticky">
            <RailContent idPrefix="rail" />
          </div>
        </aside>

        <main id="main" ref={mainRef} tabIndex={-1} className="main outline-none">
          <Outlet />
        </main>
      </div>
      <ScrollRestoration />
    </ShellContext>
  )
}
