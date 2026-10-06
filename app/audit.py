"""Audit trail: one row per answer, Annex D fields plus extras. No chain-of-thought is stored."""
from __future__ import annotations

import json

from app.db import one, rows, session


def write(record: dict) -> None:
    with session() as con:
        con.execute("""INSERT OR REPLACE INTO audit_log (trace_id, ts, student_id, question, question_category, as_of_date,
                           answer_type, model, llm_calls, tokens, latency_ms, record_json)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (record["trace_id"], record["timestamp"], record.get("student_id"), record["question"],
                     record.get("question_category"), record["as_of_date"], record["answer_type"], record.get("model"),
                     record.get("llm_calls", 0), record.get("tokens", 0), record.get("latency_ms", 0),
                     json.dumps(record, default=str)))


def get(trace_id: str) -> dict | None:
    with session() as con:
        r = one(con, "SELECT record_json FROM audit_log WHERE trace_id = ?", (trace_id,))
    return json.loads(r["record_json"]) if r else None


def recent(limit: int = 30) -> list[dict]:
    with session() as con:
        return rows(con, """SELECT trace_id, ts AS timestamp, student_id, question, answer_type, latency_ms
                            FROM audit_log ORDER BY ts DESC LIMIT ?""", (limit,))
