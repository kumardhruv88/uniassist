/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" serves every endpoint from local fixtures (no backend needed). */
  readonly VITE_MOCK?: string
  /** Base path for API calls. Defaults to /api (proxied to the backend). */
  readonly VITE_API_BASE?: string
  /** Simulated latency for mock responses, in milliseconds. Defaults to 600. */
  readonly VITE_MOCK_LATENCY?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
