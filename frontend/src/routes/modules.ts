// Route modules loaded on demand. Calling one early (on hover or focus of a nav link) starts the
// download before the click, so the page is usually ready when the person gets there.

export const routeModules = {
  documents: () => import('./DocumentsPage'),
  students: () => import('./StudentsPage'),
  audit: () => import('./AuditListPage'),
  auditRecord: () => import('./AuditDetailPage'),
}

export type RouteModuleName = keyof typeof routeModules

export function preloadRoute(name: RouteModuleName): void {
  void routeModules[name]()
}
