import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// The backend serves its endpoints at the root (/ask, /health, ...).
// The UI always calls /api/..., and this proxy strips the prefix, the same way nginx does in Docker.
const apiTarget = process.env.API_TARGET ?? 'http://localhost:8000'

const apiProxy = {
  '/api': {
    target: apiTarget,
    changeOrigin: true,
    rewrite: (path: string) => path.replace(/^\/api/, ''),
    // Answers from a local model can take 5-15 s; give slow requests room.
    timeout: 120_000,
    proxyTimeout: 120_000,
  },
}

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: apiProxy,
  },
  preview: {
    port: 4173,
    proxy: apiProxy,
  },
})
