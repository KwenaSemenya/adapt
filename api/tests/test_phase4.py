"""Phase 4: human-in-the-loop rules, decision log and metrics."""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from adapt import llm
from adapt.decisions import words_kept_pct
from tests.test_phase2 import Fake


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAPT_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    monkeypatch.delenv("ADAPT_FORCE_FAIL", raising=False)
    llm.reset_forced_failures()
    llm.set_transport(Fake())
    from adapt.main import app

    with TestClient(app) as c:
        yield c
    llm.set_transport(None)


def _variant_with_high(client) -> dict:
    """A fresh (non-seed) run whose ZA variant has one open High flag."""
    from adapt.seeds import BRIEF, MASTER

    fake = Fake()
    fake.flag = lambda m: {"flags": [
        {"severity": "High", "basis": "missing", "text": "Check: privacy.", "cites": f"{m.upper()}-C1", "quote": ""},
        {"severity": "Low", "basis": "stated", "text": "Check: word.", "cites": f"{m.upper()}-V1", "quote": "sorted"},
    ]}
    llm.set_transport(fake)
    cid = client.post("/api/campaigns", json={"brief": BRIEF, "master": MASTER, "markets": ["za"]}).json()[
        "campaign_id"]
    for _ in range(100):
        camp = client.get(f"/api/campaigns/{cid}").json()
        if camp["run"]["status"] != "running":
            break
        time.sleep(0.03)
    vid = camp["variants"][0]["id"]
    client.post(f"/api/variants/{vid}/opened")
    return client.get(f"/api/variants/{vid}").json()


def _log(client, cid):
    return client.get(f"/api/campaigns/{cid}/handoff").json()["log"]


def test_approve_blocked_by_open_high_flag(client):
    v = _variant_with_high(client)
    high = next(f for f in v["flags"] if f["severity"] == "High")
    r = client.post(f"/api/variants/{v['id']}/approve", json={"who": "Thandi Mokoena"})
    assert r.status_code == 409
    assert "waiting on the high flag ZA-C1" in r.json()["message"]
    assert "score doesn't affect this" in r.json()["message"]
    assert v["blockers"] == ["ZA-C1"]

    # No name: refused even once the flag is resolved.
    client.post(f"/api/flags/{high['id']}/ack", json={"who": "Thandi Mokoena"})
    r = client.post(f"/api/variants/{v['id']}/approve", json={"who": "  "})
    assert r.status_code == 422 and "Add your name" in r.json()["message"]

    r = client.post(f"/api/variants/{v['id']}/approve", json={"who": "Thandi Mokoena"})
    assert r.status_code == 200 and r.json()["decision"] == "approved"
    handoff = client.get(f"/api/campaigns/{v['campaign_id']}/handoff").json()
    assert [a["market"] for a in handoff["approved"]] == ["za"]


def test_dismiss_and_reject_need_reasons(client):
    v = _variant_with_high(client)
    high = next(f for f in v["flags"] if f["severity"] == "High")
    r = client.post(f"/api/flags/{high['id']}/dismiss", json={"who": "A", "reason": "   "})
    assert r.status_code == 422 and "Add a reason to dismiss" in r.json()["message"]
    r = client.post(f"/api/variants/{v['id']}/reject", json={"who": "A", "reason": ""})
    assert r.status_code == 422 and "Add a reason to reject" in r.json()["message"]
    r = client.post(f"/api/variants/{v['id']}/reject", json={"who": "A", "reason": "Needs a local writer."})
    assert r.json()["decision"] == "rejected" and r.json()["decision_reason"] == "Needs a local writer."


def test_undo_everywhere(client):
    v = _variant_with_high(client)
    high = next(f for f in v["flags"] if f["severity"] == "High")
    after = client.post(f"/api/flags/{high['id']}/dismiss", json={"who": "A", "reason": "Fine locally."}).json()
    assert next(f for f in after["flags"] if f["id"] == high["id"])["state"] == "dismissed"
    after = client.post(f"/api/flags/{high['id']}/undo", json={"who": "A"}).json()
    assert next(f for f in after["flags"] if f["id"] == high["id"])["state"] == "open"

    client.post(f"/api/variants/{v['id']}/reject", json={"who": "A", "reason": "No."})
    after = client.post(f"/api/variants/{v['id']}/undo", json={"who": "A"}).json()
    assert after["decision"] == "draft"
    # Decided variants lock their flags and copy.
    client.post(f"/api/flags/{high['id']}/ack", json={"who": "A"})
    client.post(f"/api/variants/{v['id']}/approve", json={"who": "A"})
    r = client.post(f"/api/flags/{high['id']}/undo", json={"who": "A"})
    assert r.status_code == 409 and "Undo the decision first" in r.json()["message"]
    after = client.post(f"/api/variants/{v['id']}/undo", json={"who": "A"}).json()
    assert after["decision"] == "draft"

    actions = [row["action"] for row in _log(client, v["campaign_id"])][::-1]
    assert actions == ["Dismissed flag ZA-C1", "Reopened flag ZA-C1", "Rejected", "Returned to Draft",
                       "Acknowledged flag ZA-C1", "Approved", "Returned to Draft"]


def test_edit_marks_stale_and_rescore_cap(client):
    v = _variant_with_high(client)
    assert not v["scores_stale"] and v["segments"]
    r = client.post(f"/api/variants/{v['id']}/rescore", json={"who": "A"})
    assert r.status_code == 409 and "Edit the copy first" in r.json()["message"]

    bad = client.post(f"/api/variants/{v['id']}/edit", json={"who": "A", "headline": "x" * 91, "body": "b",
                                                             "cta": "c"})
    assert bad.status_code == 422 and "Cut 1 to fit 90" in bad.json()["fields"]["headline"]

    for i in range(3):
        e = client.post(f"/api/variants/{v['id']}/edit", json={
            "who": "A", "headline": f"Admin, sorted {i}. FOR:za", "body": v["text"]["body"], "cta": v["text"]["cta"]})
        assert e.status_code == 200
        ev = e.json()
        assert ev["scores_stale"] and ev["edited"] and ev["segments"] is None  # marks cleared
        assert ev["text"]["legal"] == v["text"]["legal"]
        r = client.post(f"/api/variants/{v['id']}/rescore", json={"who": "A"})
        assert r.status_code == 200 and not r.json()["scores_stale"] and r.json()["rescore_count"] == i + 1

    client.post(f"/api/variants/{v['id']}/edit", json={"who": "A", "headline": "Once more. FOR:za",
                                                       "body": v["text"]["body"], "cta": v["text"]["cta"]})
    r = client.post(f"/api/variants/{v['id']}/rescore", json={"who": "A"})
    assert r.status_code == 409 and "all 3 rescores" in r.json()["message"]
    log = _log(client, v["campaign_id"])
    assert any(row["action"] == "Edited copy" and "Original AI draft" in row["reason"] for row in log)
    assert sum(row["action"] == "Rescored" for row in log) == 3


def test_rescore_drift_of_two_marks_low_confidence(client):
    v = _variant_with_high(client)
    fake = Fake()
    fake.score = lambda m: {"scores": [{"criterion": c, "score": 0 if c == "tone" else 2, "reason": "r"}
                                       for c in ("proposition", "tone", "humour", "signatures", "mandatories")]}
    llm.set_transport(fake)
    client.post(f"/api/variants/{v['id']}/edit", json={"who": "A", "headline": "Admin! FOR:za",
                                                       "body": v["text"]["body"], "cta": v["text"]["cta"]})
    r = client.post(f"/api/variants/{v['id']}/rescore", json={"who": "A"}).json()
    assert r["low_confidence"] and any("moved Tone from 2 to 0" in w for w in r["confidence_why"])


def test_metrics_update_after_reviewed_run(client):
    assert client.get("/api/metrics").json()["runs"] == 0
    v = _variant_with_high(client)
    high = next(f for f in v["flags"] if f["severity"] == "High")
    low = next(f for f in v["flags"] if f["severity"] == "Low")
    client.post(f"/api/flags/{high['id']}/ack", json={"who": "A"})
    client.post(f"/api/flags/{low['id']}/dismiss", json={"who": "A", "reason": "Reads fine."})
    client.post(f"/api/variants/{v['id']}/approve", json={"who": "A"})
    m = client.get("/api/metrics").json()
    assert m["runs"] == 1
    assert m["flag_ack_rate"] == 50.0
    assert m["words_kept_pct"] == 100.0
    assert m["time_to_draft_s"] is not None and m["review_time_s"] is not None

    # Seed clones never count.
    cid = client.post("/api/examples/baseline").json()["campaign_id"]
    za = client.get(f"/api/campaigns/{cid}").json()["variants"][0]
    zv = client.get(f"/api/variants/{za['id']}").json()
    for f in zv["flags"]:
        if f["severity"] == "High":
            client.post(f"/api/flags/{f['id']}/ack", json={"who": "A"})
    assert client.post(f"/api/variants/{za['id']}/approve", json={"who": "A"}).status_code == 200
    assert client.get("/api/metrics").json()["runs"] == 1


def test_words_kept():
    d = {"headline": "The admin is sorted.", "body": "Kin rebooks the dentist.", "cta": "Try Kin free."}
    f = {"headline": "The admin is handled.", "body": "Kin rebooks the dentist.", "cta": "Try Kin free."}
    assert words_kept_pct(d, f) == pytest.approx(100 * 10 / 11, abs=0.1)


def test_decision_log_is_append_only(client):
    from adapt.db import connect

    v = _variant_with_high(client)
    client.post(f"/api/variants/{v['id']}/reject", json={"who": "A", "reason": "No."})
    conn = connect()
    with pytest.raises(Exception, match="append-only"):
        conn.execute("DELETE FROM decisions")
    assert json.dumps(_log(client, v["campaign_id"]))


def test_every_logged_action_needs_a_name(client):
    v = _variant_with_high(client)
    f = v["flags"][0]
    r = client.post(f"/api/flags/{f['id']}/ack", json={"who": ""})
    assert r.status_code == 422 and "Add your name first" in r.json()["message"]
    r = client.post(f"/api/variants/{v['id']}/reject", json={"reason": "No."})
    assert r.status_code == 422


def test_review_and_draft_timing():
    from adapt.decisions import _secs

    assert _secs("2026-09-26T10:00:00+00:00", "2026-09-26T10:06:40+00:00") == 400
    assert _secs(None, "2026-09-26T10:00:00+00:00") is None


def test_humour_swing_on_rescore_does_not_mark_low_confidence(client):
    v = _variant_with_high(client)
    fake = Fake()
    fake.score = lambda m: {"scores": [{"criterion": c, "score": 0 if c == "humour" else 2, "reason": "r"}
                                       for c in ("proposition", "tone", "humour", "signatures", "mandatories")]}
    llm.set_transport(fake)
    client.post(f"/api/variants/{v['id']}/edit", json={"who": "A", "headline": "Admin, sorted. FOR:za",
                                                       "body": v["text"]["body"], "cta": v["text"]["cta"]})
    r = client.post(f"/api/variants/{v['id']}/rescore", json={"who": "A"}).json()
    assert not any("Humour" in w for w in r["confidence_why"])
