"""Load Annex C rows. Each row is accepted or rejected with a reason (SAVEPOINT per row), so one bad row
never blocks the rest. Only the API process writes SQLite; the CLI is a thin HTTP client."""
from __future__ import annotations

import sqlite3

from pydantic import BaseModel, ValidationError

from app.db import session
from app.models import AttendanceIn, CourseIn, LoadResponse, RejectedRow, ResultIn, StudentIn

TABLES: list[tuple[str, type[BaseModel], str]] = [
    ("courses", CourseIn, """INSERT INTO courses (course_code, course_name, programme, semester, credits)
        VALUES (:course_code,:course_name,:programme,:semester,:credits)
        ON CONFLICT(course_code) DO UPDATE SET course_name=excluded.course_name, programme=excluded.programme,
        semester=excluded.semester, credits=excluded.credits"""),
    ("students", StudentIn, """INSERT INTO students (student_id, full_name, programme, batch_year, current_semester, cgpa, active_backlogs)
        VALUES (:student_id,:full_name,:programme,:batch_year,:current_semester,:cgpa,:active_backlogs)
        ON CONFLICT(student_id) DO UPDATE SET full_name=excluded.full_name, programme=excluded.programme,
        batch_year=excluded.batch_year, current_semester=excluded.current_semester, cgpa=excluded.cgpa,
        active_backlogs=excluded.active_backlogs"""),
    ("attendance", AttendanceIn, """INSERT INTO attendance (student_id, course_code, classes_held, classes_attended)
        VALUES (:student_id,:course_code,:classes_held,:classes_attended)
        ON CONFLICT(student_id, course_code) DO UPDATE SET classes_held=excluded.classes_held,
        classes_attended=excluded.classes_attended"""),
    ("results", ResultIn, """INSERT INTO results (student_id, course_code, exam_session, exam_type, internal_marks,
        external_marks, total_marks, max_marks, result)
        VALUES (:student_id,:course_code,:exam_session,:exam_type,:internal_marks,:external_marks,:total_marks,:max_marks,:result)
        ON CONFLICT(student_id, course_code, exam_session, exam_type) DO UPDATE SET internal_marks=excluded.internal_marks,
        external_marks=excluded.external_marks, total_marks=excluded.total_marks, max_marks=excluded.max_marks,
        result=excluded.result"""),
]


def _reason(e: Exception) -> str:
    if isinstance(e, ValidationError):
        return "; ".join(f"{'.'.join(str(x) for x in err['loc']) or 'row'}: {err['msg']}" for err in e.errors())
    return str(e)


def load(payload: dict[str, list[dict]]) -> LoadResponse:
    accepted = {name: 0 for name, _, _ in TABLES}
    rejected: list[RejectedRow] = []
    with session() as con:
        for name, model, sql in TABLES:                       # FK order: courses → students → attendance → results
            for i, raw in enumerate(payload.get(name) or [], start=1):
                row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in raw.items() if k}
                try:
                    data = model.model_validate(row).model_dump()
                    con.execute("SAVEPOINT row")
                    try:
                        con.execute(sql, data)
                        con.execute("RELEASE row")
                        accepted[name] += 1
                    except sqlite3.Error as e:
                        con.execute("ROLLBACK TO row")
                        con.execute("RELEASE row")
                        raise ValueError(str(e).replace("FOREIGN KEY constraint failed",
                                                        "unknown student_id or course_code (load students and courses first)"))
                except (ValidationError, ValueError) as e:
                    rejected.append(RejectedRow(table=name, row=i, reason=_reason(e)))
    return LoadResponse(accepted=accepted, rejected=rejected)
