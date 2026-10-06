import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { createBrowserRouter } from 'react-router'
import { RouterProvider } from 'react-router/dom'
import { Shell } from './components/Shell'
import { shouldRetry } from './lib/api'
import { SessionProvider } from './lib/session'
import AskPage from './routes/AskPage'
import { routeModules } from './routes/modules'
import NotFoundPage from './routes/NotFoundPage'
import RouteError from './routes/RouteError'

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      // In Docker every browser shares one rate-limit bucket: never retry a 4xx, retry a 5xx once.
      retry: shouldRetry,
      refetchOnWindowFocus: false,
    },
  },
})

// Ask is the front desk and ships in the main bundle; the other views load on demand.
const router = createBrowserRouter([
  {
    path: '/',
    Component: Shell,
    ErrorBoundary: RouteError,
    children: [
      {
        ErrorBoundary: RouteError,
        children: [
          { index: true, Component: AskPage },
          { path: 'documents', lazy: async () => ({ Component: (await routeModules.documents()).default }) },
          { path: 'students', lazy: async () => ({ Component: (await routeModules.students()).default }) },
          { path: 'audit', lazy: async () => ({ Component: (await routeModules.audit()).default }) },
          { path: 'audit/:traceId', lazy: async () => ({ Component: (await routeModules.auditRecord()).default }) },
          { path: '*', Component: NotFoundPage },
        ],
      },
    ],
  },
])

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <SessionProvider>
        <RouterProvider router={router} />
      </SessionProvider>
    </QueryClientProvider>
  )
}
