import { Link } from 'react-router'

/** A small stamp impression: the same double rule the answers are stamped with. */
const mark = (
  <svg width="26" height="20" viewBox="0 0 26 20" aria-hidden="true" focusable="false">
    <g transform="rotate(-4 13 10)" fill="none" stroke="#5b3e9e">
      <rect x="1.5" y="2.5" width="23" height="15" rx="1.6" strokeWidth="2" />
      <rect x="4.6" y="5.6" width="16.8" height="8.8" rx="0.6" strokeWidth="1" />
      <path d="M8.5 10h9" strokeWidth="2" strokeLinecap="round" />
    </g>
  </svg>
)

export function Wordmark({ onNavigate }: { onNavigate?: () => void }) {
  return (
    <Link to="/" className="wordmark" onClick={onNavigate} aria-label="UniAssist, ask a question">
      {mark}
      <span className="wordmark-name">UniAssist</span>
    </Link>
  )
}
