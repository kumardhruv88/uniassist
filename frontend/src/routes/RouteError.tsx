import { Link, isRouteErrorResponse, useRouteError } from 'react-router'
import { Notice } from '../components/Notice'

/** Shown when a page fails to load or throws while rendering. */
export default function RouteError() {
  const error = useRouteError()
  const detail = isRouteErrorResponse(error)
    ? `${error.status} ${error.statusText}`
    : error instanceof Error
      ? error.message
      : String(error)
  const chunkFailed = /dynamically imported module|Failed to fetch|Importing a module script failed/i.test(detail)

  return (
    <div className="page">
      <h1 className="page-title">This page could not be shown</h1>
      <div className="mt-6">
        <Notice
          title={chunkFailed ? 'Part of the app failed to download' : 'Something broke while showing this page'}
          action={
            <button type="button" className="btn btn-secondary" onClick={() => window.location.reload()}>
              Reload the page
            </button>
          }
        >
          <p>
            {chunkFailed
              ? 'The app may have been updated or the connection dropped. Reloading fetches the current version.'
              : detail}
          </p>
        </Notice>
      </div>
      <p className="mt-6">
        <Link to="/" className="link">
          Back to Ask
        </Link>
      </p>
    </div>
  )
}
