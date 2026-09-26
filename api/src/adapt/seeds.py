"""Seed campaigns: produced by the real pipeline (scripts/seed.py), reviewed, then cached as fixtures.

Fixtures are loaded at startup as read-only templates (session_id NULL). Each
session later gets its own clone. Seed data is never hand-written.
"""

from __future__ import annotations

import json
from pathlib import Path

from .config import ROOT
from .db import tx

FIXTURE_DIR = ROOT / "fixtures" / "seed"

BRIEF = {
    "proposition": "Life admin, handled.",
    "audience": "Busy adults 25 to 40 who run a job, a household and at least one group chat.",
    "mandatories": "Legal line on every variant, word for word. "
    "Free trial is 14 days: state the length wherever 'free' appears.",
    "tone": "Warm, plainspoken, dry, calm.",
}

MASTER = {
    "headline": "The admin is handled. The weekend is yours.",
    "body": "Kin rebooks the dentist you've dodged since March, chases the refund, and finds a time that works "
    "for the whole group chat. You just say what needs doing. Then go do something better.",
    "cta": "Try Kin free.",
    "legal": "Kin can make mistakes. Check important details.",
}

MARKETS = ["za", "ng", "uk"]

# Order matters: the first is the default example.
SEEDS: dict[str, dict] = {
    "baseline": {"title": "Kin campaign", "master": MASTER},
    "us_reference": {
        "title": "Kin campaign · US-only reference",
        "master": {
            **MASTER,
            "body": "Kin plans Thanksgiving so you don't have to. Who's bringing pie, who's picking up Grandma, "
            "when the turkey goes in. Handled.",
        },
    },
    "pun": {
        "title": "Kin campaign · Pun",
        "master": {**MASTER, "headline": "Your to-do list just struck out."},
    },
    "sensitivity": {
        "title": "Kin campaign · Sensitivity clash",
        "master": {
            **MASTER,
            "body": MASTER["body"] + " Kin reads your inbox, your texts and your calendar, so nothing slips.",
        },
    },
}

_TABLES = ("campaigns", "runs", "variants", "changes", "flags")


def export_fixture(seed_key: str, campaign_id: str, run_id: str) -> Path:
    """Write the template campaign and its run output to fixtures/seed/<key>.json."""
    with tx() as conn:
        data = {
            "seed_key": seed_key,
            "campaigns": [dict(r) for r in conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,))],
            "runs": [dict(r) for r in conn.execute("SELECT * FROM runs WHERE id=?", (run_id,))],
            "variants": [dict(r) for r in conn.execute("SELECT * FROM variants WHERE run_id=? ORDER BY market",
                                                        (run_id,))],
            "changes": [dict(r) for r in conn.execute(
                "SELECT ch.* FROM changes ch JOIN variants v ON v.id=ch.variant_id WHERE v.run_id=? "
                "ORDER BY v.market, ch.position", (run_id,))],
            "flags": [dict(r) for r in conn.execute("SELECT * FROM flags WHERE run_id=? ORDER BY scope, variant_id, "
                                                     "position", (run_id,))],
        }
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    path = FIXTURE_DIR / f"{seed_key}.json"
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_fixtures() -> list[str]:
    """Load seed templates from fixtures. A changed fixture replaces its template.

    Templates are keyed by a fingerprint of the fixture file, so regenerated seeds
    reach the live DB on the next deploy. Visitors' existing clones are untouched.
    """
    import hashlib

    loaded = []
    if not FIXTURE_DIR.exists():
        return loaded
    for key in SEEDS:
        path = FIXTURE_DIR / f"{key}.json"
        if not path.exists():
            continue
        raw = path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        data = json.loads(raw)
        with tx() as conn:
            meta = conn.execute("SELECT value FROM meta WHERE key=?", (f"seed:{key}",)).fetchone()
            tpl = conn.execute("SELECT id FROM campaigns WHERE seed_key=? AND session_id IS NULL", (key,)).fetchone()
            if tpl and meta and meta["value"] == digest:
                continue
            if tpl:
                _delete_template(conn, tpl["id"])
            for table in _TABLES:
                for row in data[table]:
                    cols = ", ".join(row)
                    marks = ", ".join("?" for _ in row)
                    conn.execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", tuple(row.values()))
            conn.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                         (f"seed:{key}", digest))
        loaded.append(key)
    return loaded


def _delete_template(conn, campaign_id: str) -> None:
    runs = [r["id"] for r in conn.execute("SELECT id FROM runs WHERE campaign_id=?", (campaign_id,))]
    for rid in runs:
        conn.execute("DELETE FROM flags WHERE run_id=?", (rid,))
        conn.execute("DELETE FROM changes WHERE variant_id IN (SELECT id FROM variants WHERE run_id=?)", (rid,))
        conn.execute("DELETE FROM variants WHERE run_id=?", (rid,))
        conn.execute("DELETE FROM runs WHERE id=?", (rid,))
    conn.execute("DELETE FROM campaigns WHERE id=?", (campaign_id,))
