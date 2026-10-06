// The backend asks for X-Admin-Token on ingestion and student loading when it was started with
// ADMIN_TOKEN. The token is kept for this tab only (sessionStorage), never in localStorage.

import { useSyncExternalStore } from 'react'

const KEY = 'uniassist.adminToken.v1'
const listeners = new Set<() => void>()

function read(): string {
  try {
    return sessionStorage.getItem(KEY) ?? ''
  } catch {
    return ''
  }
}

let token = read()

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  return () => listeners.delete(listener)
}

export function setAdminToken(value: string): void {
  token = value
  try {
    if (value) sessionStorage.setItem(KEY, value)
    else sessionStorage.removeItem(KEY)
  } catch {
    // Storage unavailable: the token still works until the page reloads.
  }
  listeners.forEach((listener) => listener())
}

export function useAdminToken(): [string, (value: string) => void] {
  const value = useSyncExternalStore(subscribe, () => token)
  return [value, setAdminToken]
}
