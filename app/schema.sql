-- UniAssist SQLite schema. Annex C columns are unchanged; additions are marked "added".
-- STRICT tables reject wrong types (e.g. '' or 'forty' in an INTEGER column).
-- Constraints encode Annex C rules only; cross-row business checks live in the validator.
-- Date checks use IS (null-safe): date() returns NULL for malformed input, and a CHECK that is NULL passes.

CREATE TABLE IF NOT EXISTS students (
  student_id        TEXT PRIMARY KEY CHECK (student_id GLOB 'S[0-9][0-9][0-9][0-9]'),
  full_name         TEXT NOT NULL,
  programme         TEXT NOT NULL,
  batch_year        INTEGER NOT NULL,
  current_semester  INTEGER NOT NULL CHECK (current_semester BETWEEN 1 AND 10),
  cgpa              REAL NOT NULL CHECK (cgpa BETWEEN 0.0 AND 10.0),
  active_backlogs   INTEGER NOT NULL DEFAULT 0 CHECK (active_backlogs >= 0)
) STRICT;

CREATE TABLE IF NOT EXISTS courses (
  course_code  TEXT PRIMARY KEY,
  course_name  TEXT NOT NULL,
  programme    TEXT NOT NULL,
  semester     INTEGER,
  credits      INTEGER
) STRICT;

CREATE TABLE IF NOT EXISTS attendance (
  student_id        TEXT NOT NULL REFERENCES students(student_id),
  course_code       TEXT NOT NULL REFERENCES courses(course_code),
  classes_held      INTEGER NOT NULL CHECK (classes_held > 0),
  classes_attended  INTEGER NOT NULL CHECK (classes_attended >= 0),
  PRIMARY KEY (student_id, course_code),
  CHECK (classes_attended <= classes_held)
) STRICT;  -- attendance % is computed by tools, never stored

CREATE TABLE IF NOT EXISTS results (
  student_id      TEXT NOT NULL REFERENCES students(student_id),
  course_code     TEXT NOT NULL REFERENCES courses(course_code),
  exam_session    TEXT NOT NULL,
  exam_type       TEXT NOT NULL CHECK (exam_type IN ('REGULAR','SUPPLEMENTARY')),
  internal_marks  INTEGER,
  external_marks  INTEGER,
  total_marks     INTEGER,
  max_marks       INTEGER,
  result          TEXT NOT NULL CHECK (result IN ('PASS','FAIL','ABSENT','DETAINED')),
  PRIMARY KEY (student_id, course_code, exam_session, exam_type),     -- added: Annex C names no key
  CHECK (total_marks = internal_marks + external_marks)               -- a NULL operand skips the check
) STRICT;

CREATE TABLE IF NOT EXISTS source_register (                         -- added: Annex B as a table
  doc_id            TEXT PRIMARY KEY,
  title             TEXT NOT NULL,
  issuer            TEXT NOT NULL,
  authority_level   INTEGER NOT NULL CHECK (authority_level BETWEEN 1 AND 5),
  doc_type          TEXT NOT NULL,
  version           TEXT,
  effective_from    TEXT NOT NULL CHECK (date(effective_from) IS effective_from),
  effective_to      TEXT CHECK (effective_to IS NULL OR date(effective_to) IS effective_to),
  supersedes        TEXT,
  scope_programmes  TEXT NOT NULL DEFAULT 'ALL',
  scope_batches     TEXT NOT NULL DEFAULT 'ALL',
  provenance        TEXT NOT NULL,
  retrieved_on      TEXT,
  synthetic         TEXT NOT NULL CHECK (synthetic IN ('Y','N')),
  file_path         TEXT NOT NULL,
  file_sha256       TEXT NOT NULL,
  meta_sha256       TEXT NOT NULL,
  chunks_indexed    INTEGER NOT NULL DEFAULT 0,
  ingest_warnings   TEXT NOT NULL DEFAULT '[]',
  ingested_at       TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS rule_registry (
  rule_id           TEXT PRIMARY KEY,
  description       TEXT NOT NULL,
  parameter         TEXT NOT NULL,
  operator          TEXT NOT NULL CHECK (operator IN ('>=','<=','>','<','=','between','in')),
  value             TEXT NOT NULL,
  scope_programmes  TEXT NOT NULL DEFAULT 'ALL',
  scope_batches     TEXT NOT NULL DEFAULT 'ALL',
  effective_from    TEXT NOT NULL CHECK (date(effective_from) IS effective_from),
  effective_to      TEXT CHECK (effective_to IS NULL OR date(effective_to) IS effective_to),
  source_doc_id     TEXT NOT NULL REFERENCES source_register(doc_id),
  source_section    TEXT NOT NULL,
  unit              TEXT,                                                       -- added
  origin            TEXT NOT NULL DEFAULT 'curated'
                      CHECK (origin IN ('curated','ingest_metadata','auto_extracted')),  -- added
  status            TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','rejected')),  -- added
  created_at        TEXT NOT NULL                                               -- added
) STRICT;
CREATE INDEX IF NOT EXISTS ix_rule_param ON rule_registry(parameter, status);

CREATE TABLE IF NOT EXISTS audit_log (                               -- added
  trace_id           TEXT PRIMARY KEY,
  ts                 TEXT NOT NULL,
  student_id         TEXT,
  question           TEXT NOT NULL,
  question_category  TEXT,
  as_of_date         TEXT NOT NULL,
  answer_type        TEXT NOT NULL,
  model              TEXT,
  llm_calls          INTEGER NOT NULL DEFAULT 0,
  tokens             INTEGER NOT NULL DEFAULT 0,
  latency_ms         INTEGER NOT NULL,
  record_json        TEXT NOT NULL
) STRICT;
CREATE INDEX IF NOT EXISTS ix_audit_ts ON audit_log(ts);

CREATE TABLE IF NOT EXISTS kv (                                       -- added: data version for cache invalidation
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
) STRICT;
INSERT OR IGNORE INTO kv (key, value) VALUES ('data_version', '1');

CREATE TABLE IF NOT EXISTS security_events (                          -- added: guardrail blocks, rate limits, abuse
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  ts          TEXT NOT NULL,
  client      TEXT NOT NULL,
  student_id  TEXT,
  kind        TEXT NOT NULL,
  detail      TEXT NOT NULL,
  trace_id    TEXT
) STRICT;
CREATE INDEX IF NOT EXISTS ix_sec_ts ON security_events(ts);
