"""Phase 3 API: sessions, example clones, campaign/variant views, validation, retry, recovery."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from adapt import llm
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


def test_session_cookie_and_bootstrap(client):
    r = client.get("/api/bootstrap")
    assert r.status_code == 200
    assert "adapt_session" in r.cookies or client.cookies.get("adapt_session")
    body = r.json()
    assert body["app"] == "ADAPT"
    assert [e["key"] for e in body["examples"]] == ["baseline", "us_reference", "pun", "sensitivity"]
    assert body["runs_per_day"] == 5 and body["runs_used_today"] == 0
    assert {m["code"] for m in body["markets"]} == {"za", "ng", "uk"}


def test_example_is_cloned_per_session(client, tmp_path):
    cid = client.post("/api/examples/baseline").json()["campaign_id"]
    assert client.post("/api/examples/baseline").json()["campaign_id"] == cid  # same clone for this session
    camp = client.get(f"/api/campaigns/{cid}").json()
    assert camp["is_seed"] and len(camp["variants"]) == 3
    assert all(v["ready"] for v in camp["variants"])
    vid = camp["variants"][0]["id"]
    v = client.get(f"/api/variants/{vid}").json()
    assert v["text"]["legal"] == "Kin can make mistakes. Check important details."
    # Change ids in segments were remapped to this clone's change rows.
    seg_ids = {s["change"] for f in ("headline", "body", "cta") for s in v["segments"][f] if s.get("change")}
    assert seg_ids <= {c["id"] for c in v["changes"]}
    assert all(f["cite"]["label"] != "Unknown source" for f in v["flags"])

    other = TestClient(client.app)
    other_cid = other.post("/api/examples/baseline").json()["campaign_id"]
    assert other_cid != cid
    assert other.get(f"/api/campaigns/{cid}").status_code == 404  # can't read another session's clone


def test_brief_validation_keeps_field_messages(client):
    r = client.post("/api/campaigns", json={
        "brief": {"proposition": ""},
        "master": {"headline": "x" * 61, "body": "b", "cta": "c", "legal": "l"},
        "markets": [],
    })
    assert r.status_code == 422
    fields = r.json()["fields"]
    assert fields["proposition"] == "Add the proposition."
    assert fields["headline"] == "Headline is 61 characters. Cut 1 to fit 60."
    assert fields["markets"] == "Pick at least one market to adapt for."


def test_create_run_and_poll_to_done(client):
    from adapt.seeds import BRIEF, MASTER

    r = client.post("/api/campaigns", json={"brief": BRIEF, "master": MASTER, "markets": ["za", "uk"]})
    assert r.status_code == 201
    cid = r.json()["campaign_id"]
    import time

    for _ in range(50):
        camp = client.get(f"/api/campaigns/{cid}").json()
        if camp["run"]["status"] != "running":
            break
        time.sleep(0.05)
    assert camp["run"]["status"] == "done"
    assert [v["market"] for v in camp["variants"]] == ["za", "uk"]
    assert client.get("/api/bootstrap").json()["runs_used_today"] == 1


def test_recover_interrupted_marks_failed(client):
    from adapt.db import tx
    from adapt.routes import recover_interrupted

    cid = client.post("/api/examples/pun").json()["campaign_id"]
    with tx() as conn:
        conn.execute("UPDATE variants SET step='score', step_status='working' WHERE campaign_id=? AND market='uk'",
                     (cid,))
    assert recover_interrupted() == 1
    uk = next(v for v in client.get(f"/api/campaigns/{cid}").json()["variants"] if v["market"] == "uk")
    assert uk["step_status"] == "failed" and "server restarted" in uk["error"]
    assert client.post(f"/api/variants/{uk['id']}/retry").status_code == 200
    import time

    for _ in range(50):  # let the retry thread finish before the test DB goes away
        uk = next(v for v in client.get(f"/api/campaigns/{cid}").json()["variants"] if v["market"] == "uk")
        if uk["step_status"] == "done":
            break
        time.sleep(0.05)
    assert uk["step_status"] == "done"


def test_no_publish_route_exists(client):
    paths = [getattr(r, "path", "") for r in client.app.routes]
    assert not [p for p in paths if any(w in p for w in ("publish", "schedule", "post", "share"))]
    assert json.dumps(paths)


def test_changed_fixture_replaces_template_but_not_clones(client):
    from adapt.db import connect
    from adapt.seeds import load_fixtures

    cid = client.post("/api/examples/pun").json()["campaign_id"]
    assert load_fixtures() == []  # unchanged fixtures: nothing reloaded
    conn = connect()
    conn.execute("UPDATE meta SET value='stale' WHERE key='seed:pun'")
    assert load_fixtures() == ["pun"]
    assert conn.execute("SELECT COUNT(*) FROM campaigns WHERE seed_key='pun' AND session_id IS NULL").fetchone()[0] == 1
    assert client.get(f"/api/campaigns/{cid}").status_code == 200  # the visitor's clone survives
