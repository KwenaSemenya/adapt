-- ADAPT schema. Times are ISO-8601 UTC strings. JSON columns hold small structured blobs.

CREATE TABLE IF NOT EXISTS sessions (
  id            TEXT PRIMARY KEY,          -- random token held in an anonymous cookie
  created_at    TEXT NOT NULL,
  last_seen_at  TEXT NOT NULL
);

-- A campaign is one brief + master copy. Seed templates have session_id NULL and
-- is_seed = 1; each session gets its own clone (is_seed = 1, session_id set).
CREATE TABLE IF NOT EXISTS campaigns (
  id            TEXT PRIMARY KEY,
  session_id    TEXT REFERENCES sessions(id),
  seed_key      TEXT,                      -- baseline | us_reference | pun | sensitivity
  is_seed       INTEGER NOT NULL DEFAULT 0,
  brand         TEXT NOT NULL,
  title         TEXT NOT NULL,
  brief_json    TEXT NOT NULL,             -- proposition, audience, mandatories, tone
  master_json   TEXT NOT NULL,             -- headline, body, cta, legal
  markets_json  TEXT NOT NULL,             -- ["za","ng","uk"]
  created_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS campaigns_session ON campaigns(session_id);

CREATE TABLE IF NOT EXISTS runs (
  id            TEXT PRIMARY KEY,
  campaign_id   TEXT NOT NULL REFERENCES campaigns(id),
  session_id    TEXT REFERENCES sessions(id),
  is_seed       INTEGER NOT NULL DEFAULT 0,
  status        TEXT NOT NULL,             -- running | done | partial | failed
  brief_step    TEXT NOT NULL DEFAULT 'pending',  -- brief-level flag step: pending | done | failed
  model         TEXT,
  started_at    TEXT NOT NULL,
  finished_at   TEXT,
  day           TEXT NOT NULL              -- UTC date, for the daily caps
);
CREATE INDEX IF NOT EXISTS runs_session_day ON runs(session_id, day);
CREATE INDEX IF NOT EXISTS runs_day ON runs(day);

-- One variant per market per run. Each pipeline step persists its output here,
-- so a retry resumes from the step that failed.
CREATE TABLE IF NOT EXISTS variants (
  id              TEXT PRIMARY KEY,
  run_id          TEXT NOT NULL REFERENCES runs(id),
  campaign_id     TEXT NOT NULL REFERENCES campaigns(id),
  market          TEXT NOT NULL,
  step            TEXT NOT NULL DEFAULT 'draft',   -- next step to run: draft | score | flag | done
  step_status     TEXT NOT NULL DEFAULT 'waiting', -- waiting | working | failed | done
  error           TEXT,                            -- plain-language cause of the last failure
  draft_json      TEXT,                            -- AI draft: headline, body, cta, legal
  edited_json     TEXT,                            -- human edit, if any
  scores_json     TEXT,                            -- latest scores: [{criterion, score, reason}]
  score_history   TEXT NOT NULL DEFAULT '[]',      -- every scoring pass, for rescore drift
  scores_stale    INTEGER NOT NULL DEFAULT 0,
  rescore_count   INTEGER NOT NULL DEFAULT 0,
  low_confidence  INTEGER NOT NULL DEFAULT 0,
  confidence_why  TEXT NOT NULL DEFAULT '[]',      -- signals that made it low confidence
  decision        TEXT NOT NULL DEFAULT 'draft',   -- draft | approved | rejected
  decided_by      TEXT,
  decided_at      TEXT,
  decision_reason TEXT,
  started_at      TEXT,
  draft_ready_at  TEXT,
  review_opened_at TEXT,
  updated_at      TEXT NOT NULL,
  UNIQUE (run_id, market)
);

-- Grounded changes only. Ungrounded ones are dropped and written to `log`.
CREATE TABLE IF NOT EXISTS changes (
  id          TEXT PRIMARY KEY,
  variant_id  TEXT NOT NULL REFERENCES variants(id),
  position    INTEGER NOT NULL,
  field       TEXT NOT NULL,               -- headline | body | cta
  was         TEXT NOT NULL,
  now         TEXT NOT NULL,
  cites       TEXT NOT NULL,
  why         TEXT NOT NULL DEFAULT ''
);

-- Brief-level flags have variant_id NULL. Market flags belong to one variant.
CREATE TABLE IF NOT EXISTS flags (
  id          TEXT PRIMARY KEY,
  run_id      TEXT NOT NULL REFERENCES runs(id),
  variant_id  TEXT REFERENCES variants(id),
  scope       TEXT NOT NULL,               -- brief | market
  position    INTEGER NOT NULL,
  severity    TEXT NOT NULL,               -- High | Medium | Low | Low confidence
  text        TEXT NOT NULL,
  cites       TEXT NOT NULL,
  change_id   TEXT REFERENCES changes(id),
  state       TEXT NOT NULL DEFAULT 'open',-- open | ack | dismissed
  state_by    TEXT,
  state_at    TEXT,
  state_reason TEXT
);

-- Decision log: who, when, market, action, reason. Append-only (triggers below).
CREATE TABLE IF NOT EXISTS decisions (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  campaign_id TEXT NOT NULL REFERENCES campaigns(id),
  variant_id  TEXT REFERENCES variants(id),
  session_id  TEXT,
  at          TEXT NOT NULL,
  who         TEXT NOT NULL,
  market      TEXT NOT NULL,               -- market code, or 'all'
  action      TEXT NOT NULL,
  reason      TEXT NOT NULL DEFAULT ''
);
CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON decisions
BEGIN SELECT RAISE(ABORT, 'decision log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
BEGIN SELECT RAISE(ABORT, 'decision log is append-only'); END;

-- System log: ungrounded citations, invalid JSON, step failures, injection notes.
CREATE TABLE IF NOT EXISTS log (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  at          TEXT NOT NULL,
  level       TEXT NOT NULL,               -- info | warn | error
  event       TEXT NOT NULL,               -- e.g. ungrounded, json_invalid, step_failed
  run_id      TEXT,
  variant_id  TEXT,
  market      TEXT,
  step        TEXT,
  detail_json TEXT NOT NULL DEFAULT '{}'
);

-- Efficiency instrumentation, one row per reviewed variant. Seeds are excluded.
CREATE TABLE IF NOT EXISTS metrics (
  id               INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id           TEXT NOT NULL REFERENCES runs(id),
  variant_id       TEXT NOT NULL UNIQUE REFERENCES variants(id),
  market           TEXT NOT NULL,
  time_to_draft_s  REAL,
  review_time_s    REAL,
  words_kept_pct   REAL,
  flags_acked      INTEGER NOT NULL DEFAULT 0,
  flags_resolved   INTEGER NOT NULL DEFAULT 0,
  is_seed          INTEGER NOT NULL DEFAULT 0,
  recorded_at      TEXT NOT NULL
);

-- Small key/value store, e.g. the fingerprint of each loaded seed fixture.
CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
