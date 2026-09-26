"""Human-in-the-loop actions. The AI drafts and flags; these are the only ways a variant changes state.

Every action is checked here, never only in the browser, and appends one row to
the decision log. The log is append-only (enforced by database triggers).
"""

from __future__ import annotations

import difflib
import json
import re
import sqlite3
from datetime import datetime

from .db import now, tx


class Refused(Exception):
    """An action that isn't allowed right now. `message` says why and what to do."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.message = message
        self.status = status


# Looser than the master form: a variant often grows to meet a mandatory ("free for 14 days").
EDIT_LIMITS = {"headline": 90, "body": 450, "cta": 40}


def who_or_default(who: str | None) -> str:
    """Every log entry names a person. No name, no action."""
    who = (who or "").strip()
    if not who:
        raise Refused("Add your name first. It goes in the decision log with every action.", 422)
    return who[:80]


def _log(conn: sqlite3.Connection, v: sqlite3.Row, session_id: str | None, who: str, action: str,
         reason: str = "") -> None:
    conn.execute(
        "INSERT INTO decisions (campaign_id, variant_id, session_id, at, who, market, action, reason) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (v["campaign_id"], v["id"], session_id, now(), who, v["market"], action, reason),
    )


def _variant(conn: sqlite3.Connection, variant_id: str) -> sqlite3.Row:
    v = conn.execute("SELECT * FROM variants WHERE id=?", (variant_id,)).fetchone()
    if not v:
        raise Refused("That variant doesn't exist.", 404)
    return v


def _require_ready(v: sqlite3.Row) -> None:
    if v["step"] != "done":
        raise Refused("This market isn't ready for review yet. Wait for it to finish, or retry it.")


def _require_undecided(v: sqlite3.Row) -> None:
    if v["decision"] != "draft":
        raise Refused(f"This variant is already {v['decision']}. Undo the decision first to change it.")


# ---------- review timing ----------


def open_review(variant_id: str) -> None:
    """Stamp when a person first opened the review screen for this variant (for review time)."""
    with tx() as conn:
        conn.execute(
            "UPDATE variants SET review_opened_at=COALESCE(review_opened_at, ?) WHERE id=? AND step='done'",
            (now(), variant_id),
        )


# ---------- flags ----------


def _flag(conn: sqlite3.Connection, flag_id: str) -> tuple[sqlite3.Row, sqlite3.Row]:
    f = conn.execute("SELECT * FROM flags WHERE id=?", (flag_id,)).fetchone()
    if not f or not f["variant_id"]:
        raise Refused("That flag doesn't exist.", 404)
    return f, _variant(conn, f["variant_id"])


def acknowledge(flag_id: str, who: str | None, session_id: str | None) -> None:
    who = who_or_default(who)
    with tx() as conn:
        f, v = _flag(conn, flag_id)
        _require_undecided(v)
        if f["state"] != "open":
            raise Refused("This flag is already resolved. Undo it first to change it.")
        conn.execute("UPDATE flags SET state='ack', state_by=?, state_at=?, state_reason=NULL WHERE id=?",
                     (who, now(), flag_id))
        _log(conn, v, session_id, who, f"Acknowledged flag {f['cites']}", f["text"])


def dismiss(flag_id: str, who: str | None, reason: str | None, session_id: str | None) -> None:
    who = who_or_default(who)
    reason = (reason or "").strip()
    if not reason:
        raise Refused("Add a reason to dismiss. It goes in the decision log.", 422)
    with tx() as conn:
        f, v = _flag(conn, flag_id)
        _require_undecided(v)
        if f["state"] != "open":
            raise Refused("This flag is already resolved. Undo it first to change it.")
        conn.execute("UPDATE flags SET state='dismissed', state_by=?, state_at=?, state_reason=? WHERE id=?",
                     (who, now(), reason[:500], flag_id))
        _log(conn, v, session_id, who, f"Dismissed flag {f['cites']}", reason[:500])


def reopen(flag_id: str, who: str | None, session_id: str | None) -> None:
    who = who_or_default(who)
    with tx() as conn:
        f, v = _flag(conn, flag_id)
        _require_undecided(v)
        if f["state"] == "open":
            raise Refused("This flag is already open.")
        conn.execute("UPDATE flags SET state='open', state_by=NULL, state_at=NULL, state_reason=NULL WHERE id=?",
                     (flag_id,))
        _log(conn, v, session_id, who, f"Reopened flag {f['cites']}",
             f"Undid {'acknowledgement' if f['state'] == 'ack' else 'dismissal'}")


# ---------- edit and rescore ----------


def edit(variant_id: str, who: str | None, text: dict, session_id: str | None) -> None:
    who = who_or_default(who)
    fields = {k: (text.get(k) or "").strip() for k in EDIT_LIMITS}
    problems = {}
    for k, limit in EDIT_LIMITS.items():
        if not fields[k]:
            problems[k] = f"The {'call to action' if k == 'cta' else k} can't be empty."
        elif len(fields[k]) > limit:
            problems[k] = f"This is {len(fields[k])} characters. Cut {len(fields[k]) - limit} to fit {limit}."
    if problems:
        err = Refused("Some fields need fixing. Your edits are kept.", 422)
        err.fields = problems  # type: ignore[attr-defined]
        raise err
    with tx() as conn:
        v = _variant(conn, variant_id)
        _require_ready(v)
        _require_undecided(v)
        before = json.loads(v["edited_json"]) if v["edited_json"] else json.loads(v["draft_json"])
        if all(fields[k] == before[k] for k in EDIT_LIMITS):
            raise Refused("Nothing changed, so there was nothing to save.", 422)
        conn.execute("UPDATE variants SET edited_json=?, scores_stale=1, updated_at=? WHERE id=?",
                     (json.dumps(fields), now(), variant_id))
        original = "" if v["edited_json"] else " Original AI draft: " + " / ".join(before[k] for k in EDIT_LIMITS)
        _log(conn, v, session_id, who, "Edited copy", "Scores marked out of date." + original)


MAX_RESCORES = 3


def check_rescore(variant_id: str) -> sqlite3.Row:
    with tx() as conn:
        v = _variant(conn, variant_id)
    _require_ready(v)
    _require_undecided(v)
    if not v["scores_stale"]:
        raise Refused("The scores already match this copy. Edit the copy first, then rescore.")
    if v["rescore_count"] >= MAX_RESCORES:
        raise Refused("You've used all 3 rescores for this variant. The scores stay advisory, so you can still approve.")
    return v


def record_rescore(variant_id: str, who: str | None, session_id: str | None) -> int:
    who = who_or_default(who)
    with tx() as conn:
        v = _variant(conn, variant_id)
        n = v["rescore_count"] + 1
        conn.execute("UPDATE variants SET rescore_count=? WHERE id=?", (n, variant_id))
        _log(conn, v, session_id, who, "Rescored", f"{n} of {MAX_RESCORES} used")
    return n


# ---------- approve, reject, undo ----------


def approval_blockers(conn: sqlite3.Connection, variant_id: str) -> list[str]:
    rows = conn.execute(
        "SELECT cites FROM flags WHERE variant_id=? AND severity='High' AND state='open' ORDER BY position",
        (variant_id,),
    ).fetchall()
    return [r["cites"] for r in rows]


def approve(variant_id: str, who: str | None, session_id: str | None) -> None:
    name = (who or "").strip()
    if not name:
        raise Refused("Add your name to approve. It goes in the decision log with the time.", 422)
    with tx() as conn:
        v = _variant(conn, variant_id)
        _require_ready(v)
        _require_undecided(v)
        blockers = approval_blockers(conn, variant_id)
        if blockers:
            raise Refused(
                f"Approval is waiting on the high flag{'s' if len(blockers) > 1 else ''} {', '.join(blockers)}. "
                "Acknowledge or dismiss it in Flags. The score doesn't affect this."
            )
        ts = now()
        conn.execute("UPDATE variants SET decision='approved', decided_by=?, decided_at=?, decision_reason=NULL, "
                     "updated_at=? WHERE id=?", (name[:80], ts, ts, variant_id))
        _log(conn, v, session_id, name[:80], "Approved", "Approved with edits" if v["edited_json"] else "")
        record_metrics(conn, variant_id)


def reject(variant_id: str, who: str | None, reason: str | None, session_id: str | None) -> None:
    who = who_or_default(who)
    reason = (reason or "").strip()
    if not reason:
        raise Refused("Add a reason to reject. It goes in the decision log.", 422)
    with tx() as conn:
        v = _variant(conn, variant_id)
        _require_ready(v)
        _require_undecided(v)
        ts = now()
        conn.execute("UPDATE variants SET decision='rejected', decided_by=?, decided_at=?, decision_reason=?, "
                     "updated_at=? WHERE id=?", (who, ts, reason[:500], ts, variant_id))
        _log(conn, v, session_id, who, "Rejected", reason[:500])
        record_metrics(conn, variant_id)


def undo_decision(variant_id: str, who: str | None, session_id: str | None) -> None:
    who = who_or_default(who)
    with tx() as conn:
        v = _variant(conn, variant_id)
        if v["decision"] == "draft":
            raise Refused("This variant is already a draft.")
        conn.execute("UPDATE variants SET decision='draft', decided_by=NULL, decided_at=NULL, decision_reason=NULL, "
                     "updated_at=? WHERE id=?", (now(), variant_id))
        conn.execute("DELETE FROM metrics WHERE variant_id=?", (variant_id,))
        _log(conn, v, session_id, who, "Returned to Draft", f"Undid {'approval' if v['decision'] == 'approved' else 'rejection'}")


# ---------- metrics ----------

_WORD = re.compile(r"[\w'’]+")


def words_kept_pct(draft: dict, final: dict) -> float | None:
    """Share of the final approved text's words that came from the AI draft."""
    a = [w.lower() for k in ("headline", "body", "cta") for w in _WORD.findall(draft[k])]
    b = [w.lower() for k in ("headline", "body", "cta") for w in _WORD.findall(final[k])]
    if not b:
        return None
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    kept = sum(block.size for block in sm.get_matching_blocks())
    return round(100 * kept / len(b), 1)


def _secs(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return max(0.0, (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds())


def record_metrics(conn: sqlite3.Connection, variant_id: str) -> None:
    v = conn.execute("SELECT * FROM variants WHERE id=?", (variant_id,)).fetchone()
    run = conn.execute("SELECT * FROM runs WHERE id=?", (v["run_id"],)).fetchone()
    flags = conn.execute("SELECT state FROM flags WHERE variant_id=?", (variant_id,)).fetchall()
    acked = sum(1 for f in flags if f["state"] == "ack")
    resolved = sum(1 for f in flags if f["state"] in ("ack", "dismissed"))
    draft = json.loads(v["draft_json"])
    final = json.loads(v["edited_json"]) if v["edited_json"] else draft
    kept = words_kept_pct(draft, final) if v["decision"] == "approved" else None
    conn.execute("DELETE FROM metrics WHERE variant_id=?", (variant_id,))
    conn.execute(
        "INSERT INTO metrics (run_id, variant_id, market, time_to_draft_s, review_time_s, words_kept_pct, "
        "flags_acked, flags_resolved, is_seed, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (v["run_id"], variant_id, v["market"], _secs(run["started_at"], v["draft_ready_at"]),
         _secs(v["review_opened_at"], v["decided_at"]), kept, acked, resolved, run["is_seed"], now()),
    )


def metrics_summary(conn: sqlite3.Connection) -> dict:
    r = conn.execute(
        "SELECT COUNT(DISTINCT run_id) AS runs, AVG(time_to_draft_s) AS ttd, AVG(review_time_s) AS rt, "
        "AVG(words_kept_pct) AS kept, SUM(flags_acked) AS acked, SUM(flags_resolved) AS resolved, "
        "COUNT(*) AS variants FROM metrics WHERE is_seed=0"
    ).fetchone()
    return {
        "runs": r["runs"] or 0,
        "variants": r["variants"] or 0,
        "time_to_draft_s": r["ttd"],
        "review_time_s": r["rt"],
        "words_kept_pct": r["kept"],
        "flag_ack_rate": (100 * r["acked"] / r["resolved"]) if r["resolved"] else None,
    }
