"""SQLite access. One short-lived connection per unit of work; WAL lets readers run during writes."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.config import get_settings

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: Path | None = None) -> sqlite3.Connection:
    path = path or get_settings().sqlite_path
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")      # per connection: SQLite does not persist it
    con.execute("PRAGMA busy_timeout = 5000")
    return con


def init_db(path: Path | None = None) -> None:
    con = connect(path)
    try:
        con.execute("PRAGMA journal_mode = WAL")  # persists in the file
        con.executescript(SCHEMA.read_text())
        con.commit()
    finally:
        con.close()


@contextmanager
def session(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    con = connect(path)
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def rows(con: sqlite3.Connection, sql: str, args: tuple | dict = ()) -> list[dict]:
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def one(con: sqlite3.Connection, sql: str, args: tuple | dict = ()) -> dict | None:
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None
