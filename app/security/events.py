"""Security events: guardrail blocks, rate limiting, temporary client blocks, output redactions.
Stored in SQLite (security_events) and counted in /metrics; listed at GET /security/events (admin)."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from app import metrics
from app.db import rows, session

log = logging.getLogger("uniassist.security")


def record(kind: str, client: str, detail: dict | str, student_id: str | None = None, trace_id: str | None = None) -> None:
    metrics.inc("security_events", kind=kind)
    log.warning("security event kind=%s client=%s student=%s trace=%s", kind, client, student_id, trace_id)
    try:
        with session() as con:
            con.execute("INSERT INTO security_events (ts, client, student_id, kind, detail, trace_id) VALUES (?,?,?,?,?,?)",
                        (datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), client, student_id, kind,
                         detail if isinstance(detail, str) else json.dumps(detail), trace_id))
    except Exception:  # noqa: BLE001  never fail a request because the event log is busy
        log.exception("could not store security event")


def recent(limit: int = 50) -> list[dict]:
    with session() as con:
        out = rows(con, "SELECT * FROM security_events ORDER BY id DESC LIMIT ?", (limit,))
    for r in out:
        try:
            r["detail"] = json.loads(r["detail"])
        except (TypeError, ValueError):
            pass
    return out
