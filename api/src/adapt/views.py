"""Read models: turn DB rows into the JSON the screens render.

Kept separate from the routes so the shapes are easy to test and reuse.
"""

from __future__ import annotations

import json
import sqlite3

from .config import Config
from .grounding import FIELDS, Source, citable

SEV_ORDER = {"High": 0, "Medium": 1, "Low": 2, "Low confidence": 3}


def _cite(sources: dict[str, Source], cid: str) -> dict:
    s = sources.get(cid)
    if not s:  # never shown as grounded; should not happen for stored rows
        return {"id": cid, "label": "Unknown source", "text": "", "illustrative": False, "gap": False}
    return {"id": cid, "label": s.label, "text": s.text, "illustrative": s.illustrative, "gap": s.is_gap}


def _flag(row: sqlite3.Row, sources: dict[str, Source]) -> dict:
    return {
        "id": row["id"],
        "severity": row["severity"],
        "text": row["text"],
        "cite": _cite(sources, row["cites"]),
        "change_id": row["change_id"],
        "state": row["state"],
        "by": row["state_by"],
        "at": row["state_at"],
        "reason": row["state_reason"],
    }


def current_text(v: sqlite3.Row | dict) -> dict | None:
    if not v["draft_json"]:
        return None
    draft = json.loads(v["draft_json"])
    edited = json.loads(v["edited_json"]) if v["edited_json"] else None
    src = edited or draft
    return {"headline": src["headline"], "body": src["body"], "cta": src["cta"], "legal": draft["legal"]}


def variant_summary(conn: sqlite3.Connection, cfg: Config, v: sqlite3.Row) -> dict:
    flags = conn.execute("SELECT severity, state FROM flags WHERE variant_id=?", (v["id"],)).fetchall()
    counts = {k: 0 for k in SEV_ORDER}
    for f in flags:
        counts[f["severity"]] += 1
    market = cfg.markets.get(v["market"])
    text = current_text(v)
    return {
        "id": v["id"],
        "market": v["market"],
        "name": market.name if market else v["market"].upper(),
        "step": v["step"],
        "step_status": v["step_status"],
        "error": v["error"],
        "updated_at": v["updated_at"],
        "ready": v["step"] == "done",
        "headline": text["headline"] if text else None,
        "flag_counts": counts,
        "flags_total": len(flags),
        "high_open": sum(1 for f in flags if f["severity"] == "High" and f["state"] == "open"),
        "scores": json.loads(v["scores_json"]) if v["scores_json"] else None,
        "scores_stale": bool(v["scores_stale"]),
        "low_confidence": bool(v["low_confidence"]),
        "decision": v["decision"],
        "decided_by": v["decided_by"],
        "decided_at": v["decided_at"],
    }


def campaign_view(conn: sqlite3.Connection, cfg: Config, campaign_id: str) -> dict | None:
    c = conn.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not c:
        return None
    brief = json.loads(c["brief_json"])
    run = conn.execute("SELECT * FROM runs WHERE campaign_id=? ORDER BY started_at DESC LIMIT 1",
                       (campaign_id,)).fetchone()
    sources = citable(brief, None)
    variants, brief_flags = [], []
    if run:
        variants = [variant_summary(conn, cfg, v) for v in conn.execute(
            "SELECT * FROM variants WHERE run_id=? ORDER BY CASE market WHEN 'za' THEN 0 WHEN 'ng' THEN 1 "
            "WHEN 'uk' THEN 2 ELSE 3 END, market", (run["id"],))]
        brief_flags = [_flag(f, sources) for f in conn.execute(
            "SELECT * FROM flags WHERE run_id=? AND scope='brief' ORDER BY position", (run["id"],))]
    return {
        "id": c["id"],
        "title": c["title"],
        "brand": c["brand"],
        "is_seed": bool(c["is_seed"]),
        "seed_key": c["seed_key"],
        "brief": brief,
        "master": json.loads(c["master_json"]),
        "markets": json.loads(c["markets_json"]),
        "run": dict(run) if run else None,
        "brief_flags": brief_flags,
        "variants": variants,
    }


def variant_view(conn: sqlite3.Connection, cfg: Config, variant_id: str) -> dict | None:
    v = conn.execute("SELECT * FROM variants WHERE id=?", (variant_id,)).fetchone()
    if not v:
        return None
    c = conn.execute("SELECT * FROM campaigns WHERE id=?", (v["campaign_id"],)).fetchone()
    brief = json.loads(c["brief_json"])
    market = cfg.markets.get(v["market"])
    sources = citable(brief, market)
    summary = variant_summary(conn, cfg, v)
    draft = json.loads(v["draft_json"]) if v["draft_json"] else None
    edited = json.loads(v["edited_json"]) if v["edited_json"] else None
    changes = []
    located: set[str] = set()
    if draft:
        for f in FIELDS:
            located |= {s["change"] for s in draft["segments"][f] if s.get("change")}
    for ch in conn.execute("SELECT * FROM changes WHERE variant_id=? ORDER BY position", (variant_id,)):
        changes.append({
            "id": ch["id"], "field": ch["field"], "was": ch["was"], "now": ch["now"], "why": ch["why"],
            "cite": _cite(sources, ch["cites"]),
            "kind": "removal" if not ch["now"].strip() else ("marked" if ch["id"] in located else "nested"),
        })
    flags = sorted(
        (_flag(f, sources) for f in conn.execute("SELECT * FROM flags WHERE variant_id=? ORDER BY position",
                                                  (variant_id,))),
        key=lambda f: SEV_ORDER[f["severity"]],
    )
    return {
        **summary,
        "campaign_id": c["id"],
        "master": json.loads(c["master_json"]),
        "text": current_text(v),
        "segments": draft["segments"] if draft and not edited else None,
        "edited": bool(edited),
        "changes": changes,
        "flags": flags,
        "confidence_why": json.loads(v["confidence_why"]),
        "rescore_count": v["rescore_count"],
        "blockers": [f["cite"]["id"] for f in flags if f["severity"] == "High" and f["state"] == "open"],
        "decision_reason": v["decision_reason"],
        "snapshot_status": market.status if market else None,
    }
