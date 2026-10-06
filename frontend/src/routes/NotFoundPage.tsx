import { Link } from 'react-router'

export default function NotFoundPage() {
  return (
    <div className="page">
      <h1 className="page-title">No page here</h1>
      <p className="page-intro">
        The address does not match anything in UniAssist. <Link to="/" className="link">Ask a question</Link>, or open
        the <Link to="/audit" className="link">audit log</Link> to find an earlier answer.
      </p>
    </div>
  )
}
