import { NavLink } from 'react-router'
import { formatDate, formatInt } from '../lib/format'
import { useHealth, useStudents } from '../lib/queries'
import { useIdentity } from '../lib/session'
import { preloadRoute, type RouteModuleName } from '../routes/modules'
import { HealthPanel } from './HealthPanel'
import { Wordmark } from './Wordmark'

interface NavItem {
  to: string
  label: string
  preload?: RouteModuleName
  count?: 'documents' | 'students'
}

const NAV: NavItem[] = [
  { to: '/', label: 'Ask' },
  { to: '/documents', label: 'Documents', preload: 'documents', count: 'documents' },
  { to: '/students', label: 'Students', preload: 'students', count: 'students' },
  { to: '/audit', label: 'Audit', preload: 'audit' },
]

interface RailContentProps {
  /** Prefix for element ids; the rail and the mobile sheet each render one copy. */
  idPrefix: string
  /** Called after a navigation, so the mobile sheet can close. */
  onNavigate?: () => void
  showWordmark?: boolean
}

export function RailContent({ idPrefix, onNavigate, showWordmark = true }: RailContentProps) {
  const { studentId, setStudentId, asOfDate, setAsOfDate, today } = useIdentity()
  const students = useStudents()
  const health = useHealth()
  const selected = students.data?.find((s) => s.student_id === studentId)
  const unknownSelected = Boolean(studentId && students.data && !selected)
  const counts = {
    documents: health.data?.components.sqlite.documents,
    students: health.data?.components.sqlite.students ?? students.data?.length,
  }

  // The native select does the work (keyboard, screen readers, the phone's picker); the card on
  // top of it shows the choice on two lines, which a 200px select cannot.
  const cardName = selected ? selected.full_name : studentId ? studentId : 'No one'
  const cardNote = selected
    ? `${selected.student_id}, ${selected.programme}`
    : studentId
      ? 'Not in the records'
      : 'General questions only'

  return (
    <div className="rail-body">
      {showWordmark ? <Wordmark onNavigate={onNavigate} /> : null}

      <div className="field">
        <label htmlFor={`${idPrefix}-student`} className="field-label-quiet">
          Signed in as
        </label>
        <div className="identity">
          <select
            id={`${idPrefix}-student`}
            className="identity-select"
            value={studentId ?? ''}
            onChange={(e) => setStudentId(e.target.value || null)}
          >
            <option value="">No one — general questions</option>
            {students.data?.map((s) => (
              <option key={s.student_id} value={s.student_id}>
                {s.student_id}, {s.full_name}
              </option>
            ))}
            {unknownSelected && studentId ? <option value={studentId}>{studentId} (not in the records)</option> : null}
          </select>
          <div className="identity-card" aria-hidden="true">
            <span className="identity-name">{cardName}</span>
            <span className="identity-note">{cardNote}</span>
          </div>
        </div>
        {students.isError ? <p className="field-error">Could not load the student list. Check the API.</p> : null}
      </div>

      <div className="field">
        <label htmlFor={`${idPrefix}-asof`} className="field-label-quiet">
          Rules as of
        </label>
        <input
          id={`${idPrefix}-asof`}
          type="date"
          className="control"
          value={asOfDate}
          onChange={(e) => setAsOfDate(e.target.value)}
          aria-describedby={`${idPrefix}-asof-hint`}
        />
        <p id={`${idPrefix}-asof-hint`} className="field-hint">
          {asOfDate === today ? (
            'Today. Pick another date to see the rules in force then.'
          ) : (
            <>
              Answers use the rules of {formatDate(asOfDate)}.{' '}
              <button type="button" className="text-button" onClick={() => setAsOfDate(today)}>
                Back to today
              </button>
            </>
          )}
        </p>
      </div>

      <nav aria-label="Main">
        <ul className="grid gap-0.5">
          {NAV.map((item) => {
            const count = item.count ? counts[item.count] : undefined
            const warm = item.preload ? () => preloadRoute(item.preload as RouteModuleName) : undefined
            return (
              <li key={item.to}>
                <NavLink
                  to={item.to}
                  end={item.to === '/'}
                  className="nav-link"
                  onClick={onNavigate}
                  onMouseEnter={warm}
                  onFocus={warm}
                >
                  <span>{item.label}</span>
                  {count !== undefined ? <span className="nav-count">{formatInt(count)}</span> : null}
                </NavLink>
              </li>
            )
          })}
        </ul>
      </nav>

      <div className="mt-auto pt-2">
        <HealthPanel />
      </div>
    </div>
  )
}
