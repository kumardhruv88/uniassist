// Reference data from the hackathon annexes: Annex B (document metadata) and Annex C (student CSVs).

import type { DocType, StudentTable } from './types'

export interface AuthorityLevel {
  level: number
  name: string
  docType: DocType
  explanation: string
}

/** Annex A precedence: a lower number wins. */
export const AUTHORITY_LEVELS: AuthorityLevel[] = [
  {
    level: 1,
    name: 'Regulation',
    docType: 'regulation',
    explanation: 'Approved by the Academic Council. The highest authority.',
  },
  {
    level: 2,
    name: 'Circular',
    docType: 'circular',
    explanation: 'Issued by a university office. Can replace a regulation clause it names.',
  },
  {
    level: 3,
    name: 'Department notice',
    docType: 'notice',
    explanation: 'Applies within one department. Ranks below circulars.',
  },
  {
    level: 4,
    name: 'FAQ or handbook',
    docType: 'faq',
    explanation: 'Guidance for students. Loses to any regulation, circular or notice.',
  },
  {
    level: 5,
    name: 'Unofficial',
    docType: 'unofficial',
    explanation: 'Informational only. Never sets a rule.',
  },
]

export function authorityName(level: number): string {
  return AUTHORITY_LEVELS.find((a) => a.level === level)?.name ?? `Level ${level}`
}

export const DOC_TYPES: { value: DocType; label: string }[] = [
  { value: 'regulation', label: 'Regulation' },
  { value: 'circular', label: 'Circular' },
  { value: 'notice', label: 'Notice' },
  { value: 'faq', label: 'FAQ' },
  { value: 'handbook', label: 'Handbook' },
  { value: 'unofficial', label: 'Unofficial' },
]

export const ACCEPTED_FILE_TYPES = '.pdf,.docx,.txt,.md,.html,.htm,.png,.jpg,.jpeg'
export const MAX_UPLOAD_BYTES = 25 * 1024 * 1024

export interface StudentFileSpec {
  table: StudentTable
  file: string
  description: string
  columns: string[]
}

/** Annex C files, in the order the backend loads them. */
export const STUDENT_FILES: StudentFileSpec[] = [
  {
    table: 'courses',
    file: 'courses.csv',
    description: 'One row per course.',
    columns: ['course_code', 'course_name', 'programme', 'semester', 'credits'],
  },
  {
    table: 'students',
    file: 'students.csv',
    description: 'One row per student.',
    columns: ['student_id', 'full_name', 'programme', 'batch_year', 'current_semester', 'cgpa', 'active_backlogs'],
  },
  {
    table: 'attendance',
    file: 'attendance.csv',
    description: 'Classes held and attended, per student and course.',
    columns: ['student_id', 'course_code', 'classes_held', 'classes_attended'],
  },
  {
    table: 'results',
    file: 'results.csv',
    description: 'Exam marks, per student, course and exam session.',
    columns: [
      'student_id',
      'course_code',
      'exam_session',
      'exam_type',
      'internal_marks',
      'external_marks',
      'total_marks',
      'max_marks',
      'result',
    ],
  },
]
