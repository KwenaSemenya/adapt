"""Cost protection for a public demo with no accounts.

Three daily run limits, all counted from the runs table so they can't drift from
what actually ran: per browser session, per network (hashed IP), and global.
The Anthropic console spend limit is the final backstop (see README).
"""

from __future__ import annotations

import hashlib
import os
import threading
from datetime import UTC, datetime, timedelta

from fastapi import Request

# Check-then-create happens under this lock, so two clicks can't both slip under a limit.
RUN_LOCK = threading.Lock()


def per_session() -> int:
    return int(os.environ.get("ADAPT_RUNS_PER_DAY", "5"))


def per_ip() -> int:
    return int(os.environ.get("ADAPT_RUNS_PER_IP_PER_DAY", "15"))


def global_cap() -> int:
    return int(os.environ.get("ADAPT_GLOBAL_RUNS_PER_DAY", "15"))


def client_ip(request: Request) -> str:
    # Fly sets Fly-Client-IP; fall back to the first forwarded hop, then the socket peer.
    ip = request.headers.get("fly-client-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
    return ip or (request.client.host if request.client else "unknown")


def ip_hash(request: Request) -> str:
    """A salted hash, so the raw address is never stored."""
    salt = os.environ.get("ADAPT_IP_SALT", "adapt-demo")
    return hashlib.blake2b(f"{salt}:{client_ip(request)}".encode(), digest_size=12).hexdigest()


def today() -> str:
    return datetime.now(UTC).date().isoformat()


def resets_at() -> str:
    tomorrow = datetime.now(UTC).date() + timedelta(days=1)
    return datetime(tomorrow.year, tomorrow.month, tomorrow.day, tzinfo=UTC).isoformat()


def usage(conn, session_id: str, iphash: str) -> dict:
    day = today()
    q = "SELECT COUNT(*) FROM runs WHERE is_seed=0 AND day=?"
    return {
        "session": conn.execute(q + " AND session_id=?", (day, session_id)).fetchone()[0],
        "ip": conn.execute(q + " AND ip_hash=?", (day, iphash)).fetchone()[0],
        "global": conn.execute(q, (day,)).fetchone()[0],
    }


CREDIT_PAUSE = timedelta(hours=1)


def credits_paused(conn) -> bool:
    """True for an hour after the API reported no credit, so visitors aren't sent into failing runs."""
    row = conn.execute("SELECT value FROM meta WHERE key='credits_exhausted_at'").fetchone()
    if not row:
        return False
    return datetime.now(UTC) - datetime.fromisoformat(row["value"]) < CREDIT_PAUSE


def blocked_reason(conn, session_id: str, iphash: str) -> str | None:
    """The most specific limit that's been reached, or None."""
    if credits_paused(conn):
        return "credits"
    u = usage(conn, session_id, iphash)
    if u["session"] >= per_session():
        return "session"
    if u["ip"] >= per_ip():
        return "network"
    if u["global"] >= global_cap():
        return "global"
    return None


MESSAGES = {
    "session": "You've used all {n} runs for today. Your brief is saved on this page. Runs reset at midnight UTC.",
    "network": "This network has used all {n} runs for today. Your brief is saved on this page. "
               "Runs reset at midnight UTC.",
    "global": "ADAPT has reached today's limit of runs for everyone. Your brief is saved on this page. "
              "Runs reset at midnight UTC.",
    "credits": "ADAPT's AI budget has run out for now, so new runs are paused. Your brief is saved on this page. "
               "The example campaigns still work.",
}


def message(reason: str) -> str:
    n = {"session": per_session(), "network": per_ip(), "global": global_cap(), "credits": 0}[reason]
    return MESSAGES[reason].format(n=n)
