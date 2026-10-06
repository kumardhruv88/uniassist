import { useCallback } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from './api'
import type { DocTypeLookup } from './explain'

export const queryKeys = {
  health: ['health'] as const,
  students: ['students'] as const,
  sources: ['sources'] as const,
  audit: ['audit'] as const,
  auditList: (limit: number) => ['audit', 'list', limit] as const,
  auditRecord: (traceId: string) => ['audit', 'record', traceId] as const,
  securityEvents: ['security-events'] as const,
}

export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health,
    queryFn: () => api.health(),
    refetchInterval: 30_000,
    retry: false,
  })
}

export function useStudents() {
  return useQuery({
    queryKey: queryKeys.students,
    queryFn: () => api.students(),
    select: (data) => data.students,
  })
}

export function useSources() {
  return useQuery({
    queryKey: queryKeys.sources,
    queryFn: () => api.sources(),
    select: (data) => data.documents,
  })
}

export function useAuditList(limit = 30) {
  return useQuery({
    queryKey: queryKeys.auditList(limit),
    queryFn: () => api.auditList(limit),
    select: (data) => data.items,
  })
}

export function useAuditRecord(traceId: string) {
  return useQuery({
    queryKey: queryKeys.auditRecord(traceId),
    queryFn: () => api.auditRecord(traceId),
    enabled: traceId.length > 0,
    // Audit records never change once written.
    staleTime: Infinity,
  })
}

/** Security events need an admin token when the backend has one; fetched only when asked for. */
export function useSecurityEvents(adminToken: string, enabled: boolean) {
  return useQuery({
    queryKey: [...queryKeys.securityEvents, adminToken] as const,
    queryFn: () => api.securityEvents(50, adminToken),
    enabled,
    select: (data) => data.events,
  })
}

/** Looks up a document's type in the register, so the ledger can say "circular ACAD-2026-08". */
export function useDocTypeLookup(): DocTypeLookup {
  const { data } = useSources()
  return useCallback((docId: string) => data?.find((d) => d.doc_id === docId)?.doc_type, [data])
}
