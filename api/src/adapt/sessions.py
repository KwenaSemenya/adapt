"""Anonymous sessions and per-session clones of the seed campaigns.

No accounts: a random token in an HttpOnly cookie identifies a browser. Each
session works on its own copy of each example, so one visitor's decisions never
show up for another.
"""

from __future__ import annotations

import os
import secrets
import sqlite3

from .db import new_id, now, tx

COOKIE = "adapt_session"
_CLONE_TABLES = ("campaigns", "runs", "variants", "changes", "flags")


def ensure_session(token: str | None) -> tuple[str, bool]:
    """Return (session_id, created). Unknown or missing tokens get a fresh session."""
    with tx() as conn:
        if token and conn.execute("SELECT 1 FROM sessions WHERE id=?", (token,)).fetchone():
            conn.execute("UPDATE sessions SET last_seen_at=? WHERE id=?", (now(), token))
            return token, False
        sid = secrets.token_urlsafe(24)
        ts = now()
        conn.execute("INSERT INTO sessions (id, created_at, last_seen_at) VALUES (?,?,?)", (sid, ts, ts))
        return sid, True


def cookie_secure() -> bool:
    return os.environ.get("ADAPT_ENV") == "production"


def clone_seed(session_id: str, seed_key: str) -> str | None:
    """Clone the seed template into this session (once). Returns the session's campaign id."""
    with tx() as conn:
        existing = conn.execute(
            "SELECT id FROM campaigns WHERE session_id=? AND seed_key=? AND is_seed=1", (session_id, seed_key)
        ).fetchone()
        if existing:
            return existing["id"]
        tpl = conn.execute(
            "SELECT * FROM campaigns WHERE seed_key=? AND session_id IS NULL", (seed_key,)
        ).fetchone()
        if not tpl:
            return None
        return _clone(conn, session_id, tpl)


def _clone(conn: sqlite3.Connection, session_id: str, tpl: sqlite3.Row) -> str:
    ids: dict[str, str] = {}

    def remap(old: str | None) -> str | None:
        if old is None:
            return None
        if old not in ids:
            ids[old] = new_id()
        return ids[old]

    def insert(table: str, row: dict) -> None:
        cols = ", ".join(row)
        conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({', '.join('?' for _ in row)})", tuple(row.values()))

    camp = dict(tpl)
    camp.update(id=remap(tpl["id"]), session_id=session_id, created_at=now())
    insert("campaigns", camp)
    for r in conn.execute("SELECT * FROM runs WHERE campaign_id=?", (tpl["id"],)).fetchall():
        run = dict(r)
        run.update(id=remap(r["id"]), campaign_id=camp["id"], session_id=session_id)
        insert("runs", run)
        variants = conn.execute("SELECT * FROM variants WHERE run_id=?", (r["id"],)).fetchall()
        for v in variants:
            row = dict(v)
            row.update(id=remap(v["id"]), run_id=run["id"], campaign_id=camp["id"])
            insert("variants", row)
            for ch in conn.execute("SELECT * FROM changes WHERE variant_id=?", (v["id"],)).fetchall():
                crow = dict(ch)
                crow.update(id=remap(ch["id"]), variant_id=row["id"])
                insert("changes", crow)
        # Segments inside draft_json reference change ids: rewrite them to the clone's ids.
        for v in variants:
            if not v["draft_json"]:
                continue
            draft = v["draft_json"]
            for old, new in ids.items():
                draft = draft.replace(f'"{old}"', f'"{new}"')
            conn.execute("UPDATE variants SET draft_json=? WHERE id=?", (draft, ids[v["id"]]))
        for f in conn.execute("SELECT * FROM flags WHERE run_id=?", (r["id"],)).fetchall():
            frow = dict(f)
            frow.update(id=remap(f["id"]), run_id=run["id"], variant_id=remap(f["variant_id"]),
                        change_id=remap(f["change_id"]))
            insert("flags", frow)
    return camp["id"]
