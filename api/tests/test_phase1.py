"""Phase 1: config validation fails loudly, tables exist, the app boots as ADAPT."""

import shutil
import sqlite3
import textwrap
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from adapt.config import DEFAULT_CONFIG_DIR, ConfigError, load_config

GOOD_MARKET = textwrap.dedent(
    """
    code: xx
    name: Testland
    voice_norms:
      - {id: XX-V1, text: Short sentences.}
    sensitivities:
      - {id: XX-S1, text: Something sensitive., severity: High}
    compliance:
      - {id: XX-C1, text: A rule., source: Test Act s1, verified: false}
    known_gaps:
      - {id: XX-G1, text: No guidance on weddings.}
    """
)


@pytest.fixture
def cfg_dir(tmp_path: Path) -> Path:
    d = tmp_path / "config"
    shutil.copytree(DEFAULT_CONFIG_DIR / "brands", d / "brands")
    (d / "markets").mkdir()
    (d / "markets" / "xx.yaml").write_text(GOOD_MARKET)
    (d / "baseline.yaml").write_text("hours_per_market: 24\n")
    return d


def problems(d: Path) -> str:
    with pytest.raises(ConfigError) as e:
        load_config(d)
    return str(e.value)


def test_valid_config_loads(cfg_dir: Path):
    cfg = load_config(cfg_dir)
    assert set(cfg.brands) == {"kin"}
    assert set(cfg.markets) == {"xx"}
    assert cfg.baseline.hours_per_market == 24
    assert cfg.markets["xx"].gap_ids() == {"XX-G1"}
    assert len(cfg.brands["kin"].rubric) == 5


def test_duplicate_id_fails(cfg_dir: Path):
    (cfg_dir / "markets" / "xx.yaml").write_text(GOOD_MARKET.replace("XX-G1", "XX-V1"))
    msg = problems(cfg_dir)
    assert "duplicate ID XX-V1" in msg


def test_duplicate_across_files_fails(cfg_dir: Path):
    (cfg_dir / "markets" / "yy.yaml").write_text(
        "code: yy\nname: Other\nvoice_norms:\n  - {id: XX-V1, text: stolen}\n"
    )
    msg = problems(cfg_dir)
    assert "duplicate ID XX-V1" in msg and "malformed ID 'XX-V1'" in msg


def test_malformed_id_fails(cfg_dir: Path):
    (cfg_dir / "markets" / "xx.yaml").write_text(GOOD_MARKET.replace("XX-C1", "xx_c_one"))
    assert "malformed ID 'xx_c_one'" in problems(cfg_dir)


def test_bad_severity_and_missing_verified_fail(cfg_dir: Path):
    bad = GOOD_MARKET.replace("severity: High", "severity: Critical").replace(", verified: false", "")
    (cfg_dir / "markets" / "xx.yaml").write_text(bad)
    msg = problems(cfg_dir)
    assert "sensitivities.0.severity" in msg
    assert "compliance.0.verified" in msg


def test_unknown_key_fails(cfg_dir: Path):
    (cfg_dir / "markets" / "xx.yaml").write_text(GOOD_MARKET + "sensitivites: []\n")
    assert "sensitivites" in problems(cfg_dir)


def test_invalid_yaml_fails(cfg_dir: Path):
    (cfg_dir / "markets" / "xx.yaml").write_text("code: xx\n  name: [unclosed\n")
    assert "not valid YAML" in problems(cfg_dir)


def test_rubric_needs_three_levels(cfg_dir: Path):
    p = cfg_dir / "brands" / "kin.yaml"
    p.write_text(p.read_text().replace("      2:\n        description: Every mandatory", "      3:\n        description: Every mandatory"))
    assert "levels must be exactly 0, 1 and 2" in problems(cfg_dir)


def test_template_is_not_loaded(cfg_dir: Path):
    shutil.copy(DEFAULT_CONFIG_DIR / "markets" / "_template.yaml", cfg_dir / "markets" / "_template.yaml")
    assert set(load_config(cfg_dir).markets) == {"xx"}


def test_empty_baseline_is_allowed(cfg_dir: Path):
    (cfg_dir / "baseline.yaml").write_text("hours_per_market:\nnote: ''\n")
    assert load_config(cfg_dir).baseline.hours_per_market is None


def test_app_boots_tables_exist_and_log_is_append_only(cfg_dir: Path, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("ADAPT_CONFIG_DIR", str(cfg_dir))
    monkeypatch.setenv("ADAPT_DB_PATH", str(tmp_path / "t.db"))
    from adapt.main import app

    with TestClient(app) as client:
        r = client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["app"] == "ADAPT"
        assert set(body["tables"]) == {
            "sessions", "campaigns", "runs", "variants", "changes", "flags", "decisions", "log", "metrics",
        }
        assert client.get("/api/nope").status_code == 404

    conn = sqlite3.connect(tmp_path / "t.db")
    conn.execute("INSERT INTO sessions VALUES ('s','t','t')")
    conn.execute(
        "INSERT INTO campaigns (id, brand, title, brief_json, master_json, markets_json, created_at) "
        "VALUES ('c','kin','t','{}','{}','[]','t')"
    )
    conn.execute("INSERT INTO decisions (campaign_id, at, who, market, action) VALUES ('c','t','A','za','Approved')")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE decisions SET who='B'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM decisions")


def test_bad_config_stops_boot(cfg_dir: Path, tmp_path: Path):
    """Start the real server process: it must exit non-zero and print the problem."""
    import os
    import subprocess
    import sys

    (cfg_dir / "markets" / "xx.yaml").write_text(GOOD_MARKET.replace("XX-G1", "XX-V1"))
    env = {**os.environ, "ADAPT_CONFIG_DIR": str(cfg_dir), "ADAPT_DB_PATH": str(tmp_path / "t.db")}
    proc = subprocess.run(
        [sys.executable, "-m", "uvicorn", "adapt.main:app", "--port", "8791"],
        env=env, capture_output=True, text=True, timeout=20,
    )
    assert proc.returncode != 0
    assert "ADAPT config is invalid" in proc.stderr
    assert "duplicate ID XX-V1" in proc.stderr
