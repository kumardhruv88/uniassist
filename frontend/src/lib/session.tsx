// App-level state: who is asking, the "rules as of" date, the current conversation and the
// questions asked this session. The ask flow lives here rather than in the Ask page, so an answer
// that is still on its way is not lost when someone opens Documents or Audit while waiting.

import { createContext, use, useCallback, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { ApiError, api, explainError, type ErrorExplanation } from './api'
import { todayISO } from './format'
import type { AskResponse } from './types'

const IDENTITY_KEY = 'uniassist.identity.v1'
const HISTORY_LIMIT = 12

function readStoredStudent(): string | null {
  try {
    const raw = localStorage.getItem(IDENTITY_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as { studentId?: unknown }
    return typeof parsed.studentId === 'string' && parsed.studentId ? parsed.studentId : null
  } catch {
    return null
  }
}

function storeStudent(studentId: string | null): void {
  try {
    localStorage.setItem(IDENTITY_KEY, JSON.stringify({ studentId }))
  } catch {
    // Storage can be unavailable (private windows, blocked site data); identity then lasts for the tab.
  }
}

/**
 * A random conversation id for X-Session-Id. getRandomValues works on plain http too, where
 * crypto.randomUUID is unavailable (for example a Docker host opened by IP address).
 */
function newConversationId(): string {
  const bytes = new Uint8Array(12)
  crypto.getRandomValues(bytes)
  return `ui-${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`
}

// ---------------------------------------------------------------------------------------------
// Identity

interface IdentityValue {
  studentId: string | null
  setStudentId: (studentId: string | null) => void
  asOfDate: string
  setAsOfDate: (date: string) => void
  today: string
}

const IdentityContext = createContext<IdentityValue | null>(null)

export function useIdentity(): IdentityValue {
  const value = use(IdentityContext)
  if (!value) throw new Error('useIdentity must be used inside <SessionProvider>')
  return value
}

// ---------------------------------------------------------------------------------------------
// Asking

export interface AskEntry {
  id: string
  question: string
  askedAt: number
  studentId: string | null
  asOfDate: string
  sessionId: string
  response: AskResponse
}

export interface PendingAsk {
  question: string
  startedAt: number
  studentId: string | null
  asOfDate: string
}

export interface AskFailure extends ErrorExplanation {
  question: string
  /** When a 429 said to wait, the time (ms) after which asking again makes sense. */
  retryAt: number | null
}

interface AskValue {
  entries: AskEntry[]
  current: AskEntry | null
  pending: PendingAsk | null
  failure: AskFailure | null
  /** Questions answered in the current conversation; follow-ups are read against them. */
  conversationTurns: number
  ask: (question: string) => void
  cancel: () => void
  show: (id: string) => void
  /** Starts a fresh conversation: earlier questions no longer shape how follow-ups are read. */
  newConversation: () => void
}

const AskContext = createContext<AskValue | null>(null)

export function useAsk(): AskValue {
  const value = use(AskContext)
  if (!value) throw new Error('useAsk must be used inside <SessionProvider>')
  return value
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient()
  const today = useMemo(() => todayISO(), [])
  const [studentId, setStudentIdState] = useState<string | null>(readStoredStudent)
  const [asOfDate, setAsOfDateState] = useState(today)
  const [conversationId, setConversationId] = useState(newConversationId)

  const [entries, setEntries] = useState<AskEntry[]>([])
  const [currentId, setCurrentId] = useState<string | null>(null)
  const [pending, setPending] = useState<PendingAsk | null>(null)
  const [failure, setFailure] = useState<AskFailure | null>(null)
  const inFlight = useRef<AbortController | null>(null)

  const setStudentId = useCallback(
    (id: string | null) => {
      if (id !== studentId) {
        // A conversation belongs to one student: switching identity starts a new one.
        setConversationId(newConversationId())
        setCurrentId(null)
        setFailure(null)
      }
      setStudentIdState(id)
      storeStudent(id)
    },
    [studentId],
  )

  const setAsOfDate = useCallback(
    (date: string) => {
      // An emptied date input falls back to today rather than sending no date.
      setAsOfDateState(date || today)
    },
    [today],
  )

  const ask = useCallback(
    (raw: string) => {
      const question = raw.trim()
      if (!question) return
      inFlight.current?.abort()
      const controller = new AbortController()
      inFlight.current = controller
      const request: PendingAsk = { question, startedAt: Date.now(), studentId, asOfDate }
      const sessionId = conversationId
      setPending(request)
      setFailure(null)

      api
        .ask({ question, as_of_date: asOfDate }, { studentId, sessionId, signal: controller.signal })
        .then((response) => {
          if (inFlight.current !== controller) return
          inFlight.current = null
          const entry: AskEntry = {
            id: response.trace_id,
            question,
            askedAt: request.startedAt,
            studentId,
            asOfDate: response.as_of_date || asOfDate,
            sessionId,
            response,
          }
          setEntries((list) => [entry, ...list.filter((e) => e.id !== entry.id)].slice(0, HISTORY_LIMIT))
          setCurrentId(entry.id)
          setPending(null)
          void queryClient.invalidateQueries({ queryKey: ['audit'] })
        })
        .catch((error: unknown) => {
          if (inFlight.current !== controller) return
          inFlight.current = null
          setPending(null)
          if (error instanceof ApiError && error.kind === 'aborted') return
          const explained = explainError(error, 'the question')
          setFailure({
            ...explained,
            question,
            retryAt: explained.retryAfter !== null ? Date.now() + explained.retryAfter * 1000 : null,
          })
        })
    },
    [asOfDate, conversationId, queryClient, studentId],
  )

  const cancel = useCallback(() => {
    inFlight.current?.abort()
    inFlight.current = null
    setPending(null)
  }, [])

  const show = useCallback((id: string) => {
    setCurrentId(id)
    setFailure(null)
  }, [])

  const newConversation = useCallback(() => {
    setConversationId(newConversationId())
    setCurrentId(null)
    setFailure(null)
  }, [])

  const identity = useMemo<IdentityValue>(
    () => ({ studentId, setStudentId, asOfDate, setAsOfDate, today }),
    [studentId, setStudentId, asOfDate, setAsOfDate, today],
  )

  const current = useMemo(() => entries.find((e) => e.id === currentId) ?? null, [entries, currentId])
  const conversationTurns = useMemo(() => entries.filter((e) => e.sessionId === conversationId).length, [entries, conversationId])
  const askValue = useMemo<AskValue>(
    () => ({ entries, current, pending, failure, conversationTurns, ask, cancel, show, newConversation }),
    [entries, current, pending, failure, conversationTurns, ask, cancel, show, newConversation],
  )

  return (
    <IdentityContext value={identity}>
      <AskContext value={askValue}>{children}</AskContext>
    </IdentityContext>
  )
}
