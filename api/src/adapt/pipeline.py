"""The per-market pipeline: draft -> score -> flag, plus one brief-level flag call per run.

Each step persists its result before the next starts, so a retry resumes from
the step that failed. Markets run in parallel threads and never block each other.
Confidence comes from signals computed here, never from the model's self-report.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel, Field

from . import prompts
from .config import Config
from .db import connect, log_event, new_id, now, tx
from .grounding import FIELDS, MASTER_ID, citable, locate, overlaps, ranges, unmarked_edits
from .llm import CallContext, StepError, call_json, model_id

log = logging.getLogger("adapt.pipeline")

STEPS = ("draft", "score", "flag")
SEVERITIES = ("High", "Medium", "Low", "Low confidence")


# ---------- model output shapes ----------


class ChangeOut(BaseModel):
    field: Literal["headline", "body", "cta"]
    was: str
    now: str
    cites: str
    why: str = Field(description="One short sentence: why this entry justifies the change.")


class DraftOut(BaseModel):
    headline: str = Field(min_length=1)
    body: str = Field(min_length=1)
    cta: str = Field(min_length=1)
    changes: list[ChangeOut]


class ScoreItem(BaseModel):
    criterion: str
    score: Literal[0, 1, 2]
    reason: str = Field(min_length=1)


class ScoreOut(BaseModel):
    scores: list[ScoreItem]


class FlagItem(BaseModel):
    severity: Literal["High", "Medium", "Low", "Low confidence"]
    basis: Literal["stated", "implied", "missing"] = Field(
        description="stated: the quoted words say it; implied: it follows only from how the product might work; "
                    "missing: something required is absent.")
    text: str = Field(min_length=1)
    cites: str
    quote: str


class FlagOut(BaseModel):
    flags: list[FlagItem]


class BriefFlagItem(BaseModel):
    kind: Literal["legal", "mandatory", "contradiction"] = Field(
        description="legal: a likely legal or compliance problem in the wording; mandatory: a brief mandatory is "
                    "missed; contradiction: the master contradicts the proposition.")
    text: str = Field(min_length=1)
    cites: str
    quote: str


# Brief-level severity is set by code from the kind of problem, not by the model's own judgement.
BRIEF_SEVERITY = {"legal": "High", "mandatory": "Medium", "contradiction": "Medium"}


class BriefFlagOut(BaseModel):
    flags: list[BriefFlagItem]


def check_text(text: str) -> str:
    """Normalise flag wording to a single leading "Check:"."""
    body = re.sub(r"^\s*check\s*:\s*", "", text.strip(), flags=re.I)
    body = re.sub(r"^check\s+(whether|that|if)\s+", "", body, flags=re.I)
    return "Check: " + body[:1].upper() + body[1:] if body else "Check: this line."


# ---------- campaigns and runs ----------


def create_campaign(
    *,
    brand: str,
    title: str,
    brief: dict,
    master: dict,
    markets: list[str],
    session_id: str | None = None,
    is_seed: bool = False,
    seed_key: str | None = None,
) -> str:
    cid = new_id()
    with tx() as conn:
        conn.execute(
            "INSERT INTO campaigns (id, session_id, seed_key, is_seed, brand, title, brief_json, master_json, "
            "markets_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (cid, session_id, seed_key, int(is_seed), brand, title, json.dumps(brief), json.dumps(master),
             json.dumps(markets), now()),
        )
    return cid


def start_run(campaign_id: str, *, session_id: str | None = None, is_seed: bool = False,
              ip_hash: str | None = None) -> str:
    rid = new_id()
    ts = now()
    with tx() as conn:
        camp = conn.execute("SELECT markets_json FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
        conn.execute(
            "INSERT INTO runs (id, campaign_id, session_id, is_seed, status, model, started_at, day, ip_hash) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (rid, campaign_id, session_id, int(is_seed), "running", model_id(), ts, ts[:10], ip_hash),
        )
        for m in json.loads(camp["markets_json"]):
            conn.execute(
                "INSERT INTO variants (id, run_id, campaign_id, market, started_at, updated_at) VALUES (?,?,?,?,?,?)",
                (new_id(), rid, campaign_id, m, ts, ts),
            )
    return rid


def execute_run(cfg: Config, run_id: str) -> str:
    """Run the brief-level step and every market in parallel. Returns the final run status."""
    with tx() as conn:
        vids = [r["id"] for r in conn.execute("SELECT id FROM variants WHERE run_id=? ORDER BY market", (run_id,))]
    with ThreadPoolExecutor(max_workers=len(vids) + 1) as pool:
        futures = [pool.submit(brief_step, cfg, run_id)] + [pool.submit(run_market, cfg, v) for v in vids]
        for f in futures:
            f.exception()  # errors are persisted per step; nothing escapes
    return finish_run(run_id)


def finish_run(run_id: str) -> str:
    with tx() as conn:
        rows = conn.execute("SELECT step_status FROM variants WHERE run_id=?", (run_id,)).fetchall()
        states = [r["step_status"] for r in rows]
        if any(s in ("waiting", "working") for s in states):
            status = "running"
        elif all(s == "done" for s in states):
            status = "done"
        elif any(s == "done" for s in states):
            status = "partial"
        else:
            status = "failed"
        conn.execute(
            "UPDATE runs SET status=?, finished_at=CASE WHEN ?='running' THEN NULL ELSE ? END WHERE id=?",
            (status, status, now(), run_id),
        )
    return status


# ---------- loading context ----------


def _context(conn, variant_id: str) -> dict:
    v = conn.execute("SELECT * FROM variants WHERE id=?", (variant_id,)).fetchone()
    c = conn.execute("SELECT * FROM campaigns WHERE id=?", (v["campaign_id"],)).fetchone()
    return {
        "v": dict(v),
        "brand": c["brand"],
        "brief": json.loads(c["brief_json"]),
        "master": json.loads(c["master_json"]),
    }


def _set(conn, variant_id: str, **cols) -> None:
    cols["updated_at"] = now()
    keys = ", ".join(f"{k}=?" for k in cols)
    conn.execute(f"UPDATE variants SET {keys} WHERE id=?", (*cols.values(), variant_id))


# ---------- per-market steps ----------


def run_market(cfg: Config, variant_id: str) -> None:
    """Run from the variant's current step to done. A failure stops at that step and is persisted."""
    while True:
        with tx() as conn:
            v = conn.execute("SELECT step, run_id, market FROM variants WHERE id=?", (variant_id,)).fetchone()
            if v["step"] == "done":
                _set(conn, variant_id, step_status="done", error=None)
                return
            _set(conn, variant_id, step_status="working", error=None)
        step = v["step"]
        try:
            {"draft": draft_step, "score": score_step, "flag": flag_step}[step](cfg, variant_id)
        except StepError as e:
            with tx() as conn:
                _set(conn, variant_id, step_status="failed", error=e.message)
                log_event(conn, e.event, level="error", run_id=v["run_id"], variant_id=variant_id,
                          market=v["market"], step=step, message=e.message, detail=e.detail)
            return
        except Exception as e:  # a bug, not a model problem: still fail only this market
            log.exception("step %s crashed for %s", step, variant_id)
            with tx() as conn:
                _set(conn, variant_id, step_status="failed",
                     error=f"{step.capitalize()} stopped because of an internal error. Retry, and if it repeats, report it.")
                log_event(conn, "step_crashed", level="error", run_id=v["run_id"], variant_id=variant_id,
                          market=v["market"], step=step, detail=repr(e)[:500])
            return


def retry_market(cfg: Config, variant_id: str) -> None:
    run_market(cfg, variant_id)
    with tx() as conn:
        run_id = conn.execute("SELECT run_id FROM variants WHERE id=?", (variant_id,)).fetchone()["run_id"]
    finish_run(run_id)


def draft_step(cfg: Config, variant_id: str) -> None:
    with tx() as conn:
        ctx = _context(conn, variant_id)
    v, brief, master = ctx["v"], ctx["brief"], ctx["master"]
    market = cfg.markets.get(v["market"])
    if market is None:
        raise StepError(f"There's no market snapshot for '{v['market']}'. Add config/markets/{v['market']}.yaml.",
                        event="snapshot_missing")
    brand = cfg.brands[ctx["brand"]]
    sources = citable(brief, market)
    cctx = CallContext("draft", v["run_id"], variant_id, v["market"])
    out = call_json(prompts.DRAFT_SYSTEM, prompts.draft_user(brand, market, brief, master, sources), DraftOut, cctx)

    text = {"headline": out.headline.strip(), "body": out.body.strip(), "cta": out.cta.strip()}
    grounded, ungrounded = [], []
    for i, ch in enumerate(out.changes):
        item = {"id": new_id(), "position": i, **ch.model_dump()}
        item["cites"] = item["cites"].strip()
        if not item["now"].strip() and not item["was"].strip():
            continue  # an empty change says nothing
        (grounded if item["cites"] in sources else ungrounded).append(item)

    # Locate each grounded change in its field; a change we can't find in the text isn't shown as grounded.
    # A removal (now == "") has nothing to locate: it stays grounded and is listed, not highlighted.
    segments, not_found = {}, []
    for f in FIELDS:
        segs, missing = locate(text[f], [(c["id"], c["now"]) for c in grounded
                                         if c["field"] == f and c["now"].strip()])
        segments[f] = segs
        not_found += [c for c in grounded if c["id"] in missing]
    for c in not_found:
        grounded.remove(c)
        ungrounded.append({**c, "problem": "change text not found in the variant"})

    # Anything that differs from the master but no located change accounts for is an uncited claim.
    unmarked = {f: unmarked_edits(master[f], text[f], ranges(segments[f])) for f in FIELDS}

    why: list[str] = []
    for c in grounded:
        if sources[c["cites"]].is_gap:
            why.append(f"A change relies on {c['cites']}, a known gap in the snapshot: \"{c['now']}\".")
    for c in ungrounded:
        why.append(f"A change has no valid citation, so it isn't shown as grounded: \"{c['now']}\".")
    for f, spans in unmarked.items():
        for s in spans:
            why.append(f"The {f} has an edit with no citation: \"{s}\".")

    draft = {**text, "legal": master["legal"], "segments": segments,
             "ungrounded": [{k: c.get(k) for k in ("field", "was", "now", "cites", "problem")} for c in ungrounded],
             "unmarked": unmarked}
    with tx() as conn:
        conn.execute("DELETE FROM flags WHERE variant_id=?", (variant_id,))
        conn.execute("DELETE FROM changes WHERE variant_id=?", (variant_id,))
        for c in grounded:
            conn.execute(
                "INSERT INTO changes (id, variant_id, position, field, was, now, cites, why) VALUES (?,?,?,?,?,?,?,?)",
                (c["id"], variant_id, c["position"], c["field"], c["was"], c["now"], c["cites"], c["why"]),
            )
        for c in ungrounded:
            log_event(conn, "ungrounded", level="warn", run_id=v["run_id"], variant_id=variant_id,
                      market=v["market"], step="draft", kind="change", cites=c["cites"], now=c["now"],
                      problem=c.get("problem", "citation does not resolve"))
        for f, spans in unmarked.items():
            for s in spans:
                log_event(conn, "unmarked_edit", level="warn", run_id=v["run_id"], variant_id=variant_id,
                          market=v["market"], step="draft", field=f, text=s)
        _set(conn, variant_id, draft_json=json.dumps(draft), step="score",
             low_confidence=int(bool(why)), confidence_why=json.dumps(why))


def _variant_text(v: dict) -> dict:
    draft = json.loads(v["draft_json"])
    edited = json.loads(v["edited_json"]) if v["edited_json"] else None
    src = edited or draft
    return {"headline": src["headline"], "body": src["body"], "cta": src["cta"], "legal": draft["legal"]}


def score_variant(cfg: Config, variant_id: str, *, step_name: str = "score") -> list[dict]:
    """Blind scoring call. Used by the pipeline and by rescore. Returns the stored scores."""
    with tx() as conn:
        ctx = _context(conn, variant_id)
        changes = [dict(r) for r in conn.execute("SELECT * FROM changes WHERE variant_id=?", (variant_id,))]
    v, brief = ctx["v"], ctx["brief"]
    brand = cfg.brands[ctx["brand"]]
    text = _variant_text(v)
    crit_ids = [c.id for c in brand.rubric]

    def check(o: ScoreOut) -> str | None:
        got = [s.criterion for s in o.scores]
        if sorted(got) != sorted(crit_ids):
            return f"expected one score per criterion {crit_ids}, got {got}"
        return None

    cctx = CallContext(step_name, v["run_id"], variant_id, v["market"])
    out = call_json(prompts.SCORE_SYSTEM, prompts.score_user(brand, brief, text), ScoreOut, cctx, check=check)
    by_id = {s.criterion: s for s in out.scores}
    scores = [{"criterion": c.id, "name": c.name, "score": by_id[c.id].score, "reason": by_id[c.id].reason.strip(),
               "adjusted": False} for c in brand.rubric]

    # Rubric rule: humour scores 1, not 0, when the joke was dropped with a stated reason.
    # The scorer is blind to reasons, so code applies it: a grounded change that cites a
    # "references to avoid" entry is the stated reason. Only for unedited AI drafts.
    market = cfg.markets.get(v["market"])
    avoid_ids = {e.id for e in market.references.avoid} if market else set()
    stated = [c for c in changes if c["cites"] in avoid_ids] if not v["edited_json"] else []
    for s in scores:
        if s["criterion"] == "humour" and s["score"] == 0 and stated:
            s["score"], s["adjusted"] = 1, True
            s["reason"] += f" Raised to 1: the joke was dropped with a stated reason ({stated[0]['cites']})."

    history = json.loads(v["score_history"])
    why = json.loads(v["confidence_why"])
    if history:
        prev = {p["criterion"]: p["score"] for p in history[-1]["scores"]}
        watched = {c.id for c in brand.rubric if c.drift_signal}
        for s in scores:
            if s["criterion"] in watched and abs(s["score"] - prev.get(s["criterion"], s["score"])) >= 2:
                why.append(f"A rescore moved {s['name']} from {prev[s['criterion']]} to {s['score']}.")
    history.append({"at": now(), "scores": [{"criterion": s["criterion"], "score": s["score"]} for s in scores]})
    with tx() as conn:
        _set(conn, variant_id, scores_json=json.dumps(scores), score_history=json.dumps(history),
             scores_stale=0, low_confidence=int(bool(why)), confidence_why=json.dumps(why))
    return scores


def score_step(cfg: Config, variant_id: str) -> None:
    score_variant(cfg, variant_id)
    with tx() as conn:
        _set(conn, variant_id, step="flag")


_WORDS = re.compile(r"[\w'’]+")


def _new_words(quote: str, master_text: str) -> bool:
    master = {w.lower() for w in _WORDS.findall(master_text)}
    return any(w.lower() not in master for w in _WORDS.findall(quote))


def _in_copy(quote: str, text: dict) -> bool:
    q = re.sub(r"\s+", " ", quote.strip().lower())
    if len(q) < 3:
        return False
    whole = re.sub(r"\s+", " ", " ".join(text[k] for k in ("headline", "body", "cta")).lower())
    return q in whole


def _campaign_master(variant_id: str) -> str:
    conn = connect()
    try:
        return conn.execute("SELECT c.master_json FROM campaigns c JOIN variants v ON v.campaign_id=c.id "
                            "WHERE v.id=?", (variant_id,)).fetchone()["master_json"]
    finally:
        conn.close()


def flag_step(cfg: Config, variant_id: str) -> None:
    with tx() as conn:
        ctx = _context(conn, variant_id)
        changes = [dict(r) for r in conn.execute("SELECT * FROM changes WHERE variant_id=? ORDER BY position",
                                                 (variant_id,))]
    v, brief = ctx["v"], ctx["brief"]
    market = cfg.markets[v["market"]]
    draft = json.loads(v["draft_json"])
    text = _variant_text(v)
    cctx = CallContext("flag", v["run_id"], variant_id, v["market"])
    sources = citable(brief, market, master=json.loads(_campaign_master(variant_id)))
    out = call_json(prompts.FLAG_SYSTEM, prompts.flag_user(market, brief, text, sources), FlagOut, cctx)

    rows, dropped = [], []
    for f in out.flags:
        cite = f.cites.strip()
        if cite not in sources:
            dropped.append(f)
            continue
        # A "new claim" flag must quote words the master doesn't have; otherwise it's about a removal or rewording.
        if cite == MASTER_ID and not _new_words(f.quote, sources[MASTER_ID].text):
            dropped.append(f)
            continue
        body = check_text(f.text)
        change_id = None
        for fld in FIELDS:
            change_id = change_id or overlaps(f.quote, draft[fld], draft["segments"][fld])
        if not change_id:
            change_id = next((c["id"] for c in changes if c["cites"] == cite), None)
        severity = f.severity
        # "Low confidence" is a signal, not an opinion: it stands only when a change relies on that gap.
        gap_used = any(c["cites"] == cite for c in changes) and sources[cite].is_gap
        if severity == "Low confidence" and not gap_used:
            severity = "Low"
        # ...and a flag on a gap a change relies on is always Low confidence, whatever the model called it,
        # so the flag list agrees with the variant's Low confidence label.
        if gap_used:
            severity = "Low confidence"
        # A risk the copy only implies (or whose quote isn't really in the copy) is worth a look, never a blocker.
        stated = f.basis == "stated" and _in_copy(f.quote, text)
        if f.basis != "missing" and not stated and severity in ("High", "Medium"):
            severity = "Low"
        rows.append({"severity": severity, "text": body, "cites": cite, "change_id": change_id})

    # Every change that leans on a known gap gets a Low confidence flag, whether or not the model raised one.
    for c in changes:
        if sources[c["cites"]].is_gap and not any(r["cites"] == c["cites"] for r in rows):
            rows.append({"severity": "Low confidence", "cites": c["cites"], "change_id": c["id"],
                         "text": f"No curated guidance here ({sources[c['cites']].text.rstrip('.')}). "
                                 f"\"{c['now']}\" was drafted without it, so your judgement matters most on that line."})

    order = {s: i for i, s in enumerate(SEVERITIES)}
    rows.sort(key=lambda r: order[r["severity"]])
    with tx() as conn:
        conn.execute("DELETE FROM flags WHERE variant_id=?", (variant_id,))
        for i, r in enumerate(rows):
            conn.execute(
                "INSERT INTO flags (id, run_id, variant_id, scope, position, severity, text, cites, change_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (new_id(), v["run_id"], variant_id, "market", i, r["severity"], r["text"], r["cites"], r["change_id"]),
            )
        for f in dropped:
            log_event(conn, "ungrounded", level="warn", run_id=v["run_id"], variant_id=variant_id,
                      market=v["market"], step="flag", kind="flag", cites=f.cites, text=f.text)
        _set(conn, variant_id, step="done", step_status="done", draft_ready_at=now())


# ---------- brief-level step ----------


def brief_step(cfg: Config, run_id: str) -> None:
    with tx() as conn:
        c = conn.execute("SELECT c.* FROM campaigns c JOIN runs r ON r.campaign_id=c.id WHERE r.id=?",
                         (run_id,)).fetchone()
        conn.execute("UPDATE runs SET brief_step='working' WHERE id=?", (run_id,))
    brief, master = json.loads(c["brief_json"]), json.loads(c["master_json"])
    sources = citable(brief, None)
    cctx = CallContext("brief", run_id, None, "all")
    try:
        out = call_json(prompts.BRIEF_FLAG_SYSTEM, prompts.brief_flag_user(brief, master, sources), BriefFlagOut, cctx)
    except StepError as e:
        with tx() as conn:
            conn.execute("UPDATE runs SET brief_step='failed' WHERE id=?", (run_id,))
            log_event(conn, e.event, level="error", run_id=run_id, market="all", step="brief", message=e.message)
        return
    with tx() as conn:
        conn.execute("DELETE FROM flags WHERE run_id=? AND scope='brief'", (run_id,))
        kept = 0
        for f in out.flags:
            cite = f.cites.strip()
            if cite not in sources:
                log_event(conn, "ungrounded", level="warn", run_id=run_id, market="all", step="brief",
                          kind="flag", cites=f.cites, text=f.text)
                continue
            body = check_text(f.text)
            conn.execute(
                "INSERT INTO flags (id, run_id, variant_id, scope, position, severity, text, cites) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (new_id(), run_id, None, "brief", kept, BRIEF_SEVERITY[f.kind], body, cite),
            )
            kept += 1
        conn.execute("UPDATE runs SET brief_step='done' WHERE id=?", (run_id,))


# ---------- reporting ----------


def run_cost(run_id: str) -> dict:
    from .llm import cost_usd

    conn = connect()
    try:
        rows = conn.execute("SELECT detail_json FROM log WHERE run_id=? AND event='llm_call'", (run_id,)).fetchall()
    finally:
        conn.close()
    calls = [json.loads(r["detail_json"]) for r in rows]
    tin = sum(c.get("input_tokens", 0) for c in calls)
    tout = sum(c.get("output_tokens", 0) for c in calls)
    model = calls[0]["model"] if calls else ""
    return {"calls": len(calls), "input_tokens": tin, "output_tokens": tout,
            "usd": round(cost_usd(model, tin, tout), 4)}
