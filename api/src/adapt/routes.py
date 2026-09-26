"""HTTP API for the screens.

Deliberately absent: any publish, schedule, post or share route. Approved copy
leaves only through the handoff screen's copy and download buttons.
"""

from __future__ import annotations

import os
import threading

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .db import connect, tx
from .pipeline import brief_step, create_campaign, execute_run, finish_run, retry_market, start_run
from .seeds import SEEDS
from .views import campaign_view, current_text, variant_view

router = APIRouter(prefix="/api")

# Form limits, shared with the brief screen. Enforced here too, never only in the browser.
LIMITS = {"proposition": 60, "audience": 200, "mandatories": 300, "tone": 300,
          "headline": 60, "body": 300, "cta": 24, "legal": 80}
REQUIRED = ("proposition", "headline", "body", "cta", "legal")
LABELS = {"proposition": "Proposition", "audience": "Audience", "mandatories": "Mandatories", "tone": "Tone notes",
          "headline": "Headline", "body": "Body", "cta": "Call to action", "legal": "Legal line"}

MARKET_ORDER = {"za": 0, "ng": 1, "uk": 2}

EXAMPLES = {
    "baseline": "The clean example. Drafted for three markets.",
    "us_reference": "Built on Thanksgiving, a US-only reference.",
    "pun": "A baseball pun that doesn't travel.",
    "sensitivity": "A claim that Kin reads your inbox and texts.",
}


def runs_per_day() -> int:
    return int(os.environ.get("ADAPT_RUNS_PER_DAY", "5"))


def _cfg(request: Request):
    return request.app.state.config


def _sid(request: Request) -> str:
    return request.state.session_id


def _own_campaign(conn, request: Request, campaign_id: str):
    row = conn.execute("SELECT session_id FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not row or row["session_id"] != _sid(request):
        raise HTTPException(404, detail={"message": "That campaign isn't in this browser session."})
    return row


def _own_variant(conn, request: Request, variant_id: str):
    row = conn.execute(
        "SELECT v.*, c.session_id AS owner FROM variants v JOIN campaigns c ON c.id=v.campaign_id WHERE v.id=?",
        (variant_id,),
    ).fetchone()
    if not row or row["owner"] != _sid(request):
        raise HTTPException(404, detail={"message": "That variant isn't in this browser session."})
    return row


def _runs_today(conn, sid: str) -> int:
    from .db import now

    return conn.execute(
        "SELECT COUNT(*) FROM runs WHERE session_id=? AND is_seed=0 AND day=?", (sid, now()[:10])
    ).fetchone()[0]


# ---------- bootstrap ----------


@router.get("/bootstrap")
def bootstrap(request: Request) -> dict:
    cfg = _cfg(request)
    sid = _sid(request)
    conn = connect()
    try:
        own = [dict(r) for r in conn.execute(
            "SELECT c.id, c.title, c.created_at, r.status FROM campaigns c "
            "LEFT JOIN runs r ON r.campaign_id=c.id WHERE c.session_id=? AND c.is_seed=0 "
            "ORDER BY c.created_at DESC LIMIT 20", (sid,))]
        seeds_ready = {r["seed_key"] for r in conn.execute(
            "SELECT seed_key FROM campaigns WHERE session_id IS NULL AND is_seed=1")}
        used = _runs_today(conn, sid)
    finally:
        conn.close()
    brand = cfg.brands["kin"]
    return {
        "app": "ADAPT",
        "brand": {"id": brand.id, "name": brand.name, "proposition": brand.proposition,
                  "traits": brand.voice.traits, "humour": brand.voice.humour,
                  "signatures": brand.voice.signatures, "never": brand.voice.never},
        "markets": [{"code": m.code, "name": m.name, "status": m.status}
                    for m in sorted(cfg.markets.values(), key=lambda m: MARKET_ORDER.get(m.code, 99))],
        "limits": LIMITS,
        "runs_per_day": runs_per_day(),
        "runs_used_today": used,
        "examples": [{"key": k, "title": SEEDS[k]["title"], "description": EXAMPLES[k]}
                     for k in SEEDS if k in seeds_ready],
        "campaigns": own,
        "criteria": [{"id": c.id, "name": c.name} for c in brand.rubric],
    }


@router.get("/metrics")
def metrics(request: Request) -> dict:
    from .decisions import metrics_summary

    cfg = _cfg(request)
    conn = connect()
    try:
        m = metrics_summary(conn)
    finally:
        conn.close()
    return {**m, "baseline_hours_per_market": cfg.baseline.hours_per_market}


@router.get("/seed-brief")
def seed_brief() -> dict:
    from .seeds import BRIEF, MASTER

    return {"brief": BRIEF, "master": MASTER}


@router.post("/examples/{key}")
def open_example(key: str, request: Request) -> dict:
    from .sessions import clone_seed

    if key not in SEEDS:
        raise HTTPException(404, detail={"message": "There's no example with that name."})
    cid = clone_seed(_sid(request), key)
    if not cid:
        raise HTTPException(503, detail={"message": "The examples haven't been generated yet. "
                                                    "Run scripts/seed.py, then restart."})
    return {"campaign_id": cid}


# ---------- campaigns ----------


class BriefIn(BaseModel):
    brief: dict[str, str]
    master: dict[str, str]
    markets: list[str]


def validate_brief(body: BriefIn, known_markets: set[str]) -> dict[str, str]:
    errors: dict[str, str] = {}
    values = {**{k: body.brief.get(k, "") for k in ("proposition", "audience", "mandatories", "tone")},
              **{k: body.master.get(k, "") for k in ("headline", "body", "cta", "legal")}}
    for k, limit in LIMITS.items():
        n = len(values[k])
        if n > limit:
            errors[k] = f"{LABELS[k]} is {n} characters. Cut {n - limit} to fit {limit}."
        elif k in REQUIRED and not values[k].strip():
            errors[k] = f"Add the {LABELS[k].lower()}."
    chosen = [m for m in body.markets if m in known_markets]
    if not chosen:
        errors["markets"] = "Pick at least one market to adapt for."
    return errors


@router.post("/campaigns", status_code=201)
def create(body: BriefIn, request: Request) -> dict:
    cfg = _cfg(request)
    errors = validate_brief(body, set(cfg.markets))
    if errors:
        raise HTTPException(422, detail={"message": "Some fields need fixing.", "fields": errors})
    brief = {k: body.brief.get(k, "").strip() for k in ("proposition", "audience", "mandatories", "tone")}
    master = {k: body.master.get(k, "").strip() for k in ("headline", "body", "cta")}
    master["legal"] = body.master.get("legal", "")  # kept exactly as typed: it is carried word for word
    markets = [m for m in ("za", "ng", "uk") if m in body.markets and m in cfg.markets]
    cid = create_campaign(brand="kin", title=f"{cfg.brands['kin'].name} campaign", brief=brief, master=master,
                          markets=markets, session_id=_sid(request))
    rid = start_run(cid, session_id=_sid(request))
    threading.Thread(target=execute_run, args=(cfg, rid), daemon=True, name=f"run-{rid}").start()
    return {"campaign_id": cid, "run_id": rid}


@router.get("/campaigns/{campaign_id}")
def get_campaign(campaign_id: str, request: Request) -> dict:
    conn = connect()
    try:
        _own_campaign(conn, request, campaign_id)
        return campaign_view(conn, _cfg(request), campaign_id)
    finally:
        conn.close()


@router.post("/campaigns/{campaign_id}/retry-brief")
def retry_brief(campaign_id: str, request: Request) -> dict:
    conn = connect()
    try:
        _own_campaign(conn, request, campaign_id)
        run = conn.execute("SELECT id, brief_step FROM runs WHERE campaign_id=? ORDER BY started_at DESC LIMIT 1",
                           (campaign_id,)).fetchone()
    finally:
        conn.close()
    if not run or run["brief_step"] != "failed":
        raise HTTPException(409, detail={"message": "The brief check isn't in a failed state."})
    threading.Thread(target=brief_step, args=(_cfg(request), run["id"]), daemon=True).start()
    return {"ok": True}


# ---------- variants ----------


@router.get("/variants/{variant_id}")
def get_variant(variant_id: str, request: Request) -> dict:
    conn = connect()
    try:
        _own_variant(conn, request, variant_id)
        return variant_view(conn, _cfg(request), variant_id)
    finally:
        conn.close()


@router.post("/variants/{variant_id}/retry")
def retry(variant_id: str, request: Request) -> dict:
    conn = connect()
    try:
        v = _own_variant(conn, request, variant_id)
    finally:
        conn.close()
    if v["step_status"] != "failed":
        raise HTTPException(409, detail={"message": "This market isn't stopped, so there's nothing to retry."})
    with tx() as c:
        c.execute("UPDATE variants SET step_status='waiting', error=NULL WHERE id=?", (variant_id,))
        c.execute("UPDATE runs SET status='running', finished_at=NULL WHERE id=?", (v["run_id"],))
    threading.Thread(target=retry_market, args=(_cfg(request), variant_id), daemon=True).start()
    return {"ok": True, "step": v["step"]}


# ---------- human decisions ----------


class Who(BaseModel):
    who: str | None = None


class WithReason(Who):
    reason: str | None = None


class EditIn(Who):
    headline: str = ""
    body: str = ""
    cta: str = ""


def _act(fn, *args):
    from .decisions import Refused

    try:
        fn(*args)
    except Refused as e:
        detail = {"message": e.message}
        if getattr(e, "fields", None):
            detail["fields"] = e.fields
        raise HTTPException(e.status, detail=detail)


def _own_flag(conn, request: Request, flag_id: str) -> None:
    row = conn.execute(
        "SELECT c.session_id FROM flags f JOIN runs r ON r.id=f.run_id JOIN campaigns c ON c.id=r.campaign_id "
        "WHERE f.id=?", (flag_id,)).fetchone()
    if not row or row["session_id"] != _sid(request):
        raise HTTPException(404, detail={"message": "That flag isn't in this browser session."})


def _variant_after(request: Request, variant_id: str) -> dict:
    conn = connect()
    try:
        return variant_view(conn, _cfg(request), variant_id)
    finally:
        conn.close()


def _flag_variant(flag_id: str) -> str:
    conn = connect()
    try:
        return conn.execute("SELECT variant_id FROM flags WHERE id=?", (flag_id,)).fetchone()["variant_id"]
    finally:
        conn.close()


@router.post("/variants/{variant_id}/opened")
def opened(variant_id: str, request: Request) -> dict:
    from .decisions import open_review

    conn = connect()
    try:
        _own_variant(conn, request, variant_id)
    finally:
        conn.close()
    open_review(variant_id)
    return {"ok": True}


@router.post("/flags/{flag_id}/ack")
def ack_flag(flag_id: str, body: Who, request: Request) -> dict:
    from .decisions import acknowledge

    conn = connect()
    try:
        _own_flag(conn, request, flag_id)
    finally:
        conn.close()
    _act(acknowledge, flag_id, body.who, _sid(request))
    return _variant_after(request, _flag_variant(flag_id))


@router.post("/flags/{flag_id}/dismiss")
def dismiss_flag(flag_id: str, body: WithReason, request: Request) -> dict:
    from .decisions import dismiss

    conn = connect()
    try:
        _own_flag(conn, request, flag_id)
    finally:
        conn.close()
    _act(dismiss, flag_id, body.who, body.reason, _sid(request))
    return _variant_after(request, _flag_variant(flag_id))


@router.post("/flags/{flag_id}/undo")
def undo_flag(flag_id: str, body: Who, request: Request) -> dict:
    from .decisions import reopen

    conn = connect()
    try:
        _own_flag(conn, request, flag_id)
    finally:
        conn.close()
    _act(reopen, flag_id, body.who, _sid(request))
    return _variant_after(request, _flag_variant(flag_id))


def _owned(request: Request, variant_id: str) -> None:
    conn = connect()
    try:
        _own_variant(conn, request, variant_id)
    finally:
        conn.close()


@router.post("/variants/{variant_id}/edit")
def edit_variant(variant_id: str, body: EditIn, request: Request) -> dict:
    from .decisions import edit

    _owned(request, variant_id)
    _act(edit, variant_id, body.who, body.model_dump(), _sid(request))
    return _variant_after(request, variant_id)


@router.post("/variants/{variant_id}/rescore")
def rescore(variant_id: str, body: Who, request: Request) -> dict:
    from .decisions import check_rescore, record_rescore
    from .llm import StepError
    from .pipeline import score_variant

    _owned(request, variant_id)
    _act(check_rescore, variant_id)
    try:
        score_variant(_cfg(request), variant_id, step_name="rescore")
    except StepError as e:
        raise HTTPException(502, detail={"message": e.message + " No rescore was used. Try again."})
    record_rescore(variant_id, body.who, _sid(request))
    return _variant_after(request, variant_id)


@router.post("/variants/{variant_id}/approve")
def approve_variant(variant_id: str, body: Who, request: Request) -> dict:
    from .decisions import approve

    _owned(request, variant_id)
    _act(approve, variant_id, body.who, _sid(request))
    return _variant_after(request, variant_id)


@router.post("/variants/{variant_id}/reject")
def reject_variant(variant_id: str, body: WithReason, request: Request) -> dict:
    from .decisions import reject

    _owned(request, variant_id)
    _act(reject, variant_id, body.who, body.reason, _sid(request))
    return _variant_after(request, variant_id)


@router.post("/variants/{variant_id}/undo")
def undo_variant(variant_id: str, body: Who, request: Request) -> dict:
    from .decisions import undo_decision

    _owned(request, variant_id)
    _act(undo_decision, variant_id, body.who, _sid(request))
    return _variant_after(request, variant_id)


# ---------- handoff ----------


@router.get("/campaigns/{campaign_id}/handoff")
def handoff(campaign_id: str, request: Request) -> dict:
    cfg = _cfg(request)
    conn = connect()
    try:
        _own_campaign(conn, request, campaign_id)
        view = campaign_view(conn, cfg, campaign_id)
        approved, others = [], []
        for vs in view["variants"]:
            v = conn.execute("SELECT * FROM variants WHERE id=?", (vs["id"],)).fetchone()
            if v["decision"] == "approved":
                approved.append({"market": v["market"], "name": vs["name"], "by": v["decided_by"],
                                 "at": v["decided_at"], **current_text(v)})
            else:
                others.append({"market": v["market"], "name": vs["name"], "decision": v["decision"],
                               "by": v["decided_by"], "ready": vs["ready"]})
        log = [dict(r) for r in conn.execute(
            "SELECT at, who, market, action, reason FROM decisions WHERE campaign_id=? ORDER BY id DESC",
            (campaign_id,))]
    finally:
        conn.close()
    return {"campaign": {"id": view["id"], "title": view["title"]}, "approved": approved, "others": others,
            "log": log}


def recover_interrupted() -> int:
    """Runs cut off by a restart are marked failed at the step they reached, so a retry resumes there."""
    with tx() as conn:
        rows = conn.execute("SELECT id, run_id FROM variants WHERE step_status IN ('waiting','working')").fetchall()
        for r in rows:
            conn.execute("UPDATE variants SET step_status='failed', error=? WHERE id=?",
                         ("This market stopped when the server restarted. Its progress is kept. Retry to carry on.",
                          r["id"]))
        conn.execute("UPDATE runs SET brief_step='failed' WHERE brief_step IN ('pending','working')")
        run_ids = {r["run_id"] for r in rows}
    for rid in run_ids:
        finish_run(rid)
    return len(rows)

