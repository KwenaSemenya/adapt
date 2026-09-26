"""SQLite access. One file on the Fly volume; WAL so pipeline threads and requests don't block each other."""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from typing import Iterator

from .config import ROOT

TABLES = ("sessions", "campaigns", "runs", "variants", "changes", "flags", "decisions", "log", "metrics")


def db_path() -> Path:
    return Path(os.environ.get("ADAPT_DB_PATH") or ROOT / "data" / "adapt.db")


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def init_db() -> list[str]:
    schema = resources.files("adapt").joinpath("schema.sql").read_text(encoding="utf-8")
    conn = connect()
    try:
        conn.executescript(schema)
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    finally:
        conn.close()
    return sorted(r["name"] for r in rows if r["name"] in TABLES)


def log_event(
    conn: sqlite3.Connection,
    event: str,
    *,
    level: str = "info",
    run_id: str | None = None,
    variant_id: str | None = None,
    market: str | None = None,
    step: str | None = None,
    **detail: object,
) -> None:
    conn.execute(
        "INSERT INTO log (at, level, event, run_id, variant_id, market, step, detail_json) VALUES (?,?,?,?,?,?,?,?)",
        (now(), level, event, run_id, variant_id, market, step, json.dumps(detail, ensure_ascii=False)),
    )
