import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
// Fonts are bundled, so the UI works offline: Public Sans for the interface and the assistant's
// words, Source Serif 4 only for text quoted from university documents, JetBrains Mono for raw JSON.
import '@fontsource-variable/public-sans/wght.css'
import '@fontsource-variable/source-serif-4/opsz.css'
import '@fontsource/jetbrains-mono/400.css'
import './styles.css'
import { App } from './App'

const root = document.getElementById('root')
if (!root) throw new Error('Missing #root element in index.html')

createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
