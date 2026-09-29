"""Scorer reliability test: can the blind scorer judge Kin's voice consistently and like a human would?

Three checks, all against the real scoring prompt used in the product:
  1. Consistency: score each of the 12 seed variants 5 times; report variance per criterion.
  2. Off-voice: score the user's 3 deliberately off-voice variants (tests/offvoice.yaml) 5 times each;
     a variant is caught when the criterion it breaks scores 0 or 1.
  3. Human agreement: compare the scorer's most common score with tests/human_scores.yaml.

Reports the scorer's raw output, and separately the product's one deterministic adjustment
(Humour raised from 0 to 1 when a grounded change dropped the joke for a stated reason).

Usage (repo root):
    uv run --project api python scripts/scorer_test.py            # live, about $0.75
    uv run --project api python scripts/scorer_test.py --fake     # free dry run with a canned scorer
Writes docs/scorer-test-results.json and docs/scorer-test-data.md.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import statistics
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "api" / "src"))

from adapt.env import load_env  # noqa: E402

load_env()
os.environ["ADAPT_DB_PATH"] = str(Path(tempfile.mkdtemp()) / "scorer-test.db")

import yaml  # noqa: E402

from adapt import llm, prompts  # noqa: E402
from adapt.config import load_config  # noqa: E402
from adapt.db import connect, init_db  # noqa: E402
from adapt.pipeline import ScoreOut  # noqa: E402
from adapt.seeds import BRIEF, SEEDS  # noqa: E402

REPEATS = 5
NAMES = {"za": "South Africa", "ng": "Nigeria", "uk": "United Kingdom"}


def fake_transport(system: str, user: str, schema: dict):
    """Canned scorer for dry runs: mostly 2s with some noise, and low scores for obvious flaws."""
    text = user.split("<variant>")[1]
    ids = ["proposition", "tone", "humour", "signatures", "mandatories"]
    scores = []
    for c in ids:
        s = 2 if random.random() > 0.15 else 1
        if c == "mandatories" and "free." in text.lower() and "14" not in text:
            s = 1
        scores.append({"criterion": c, "score": s, "reason": "Dry run."})
    return json.dumps({"scores": scores}), {"model": "fake", "input_tokens": 0, "output_tokens": 0}


def load_seed_variants(cfg) -> list[dict]:
    out = []
    for key in SEEDS:
        d = json.loads((ROOT / "fixtures" / "seed" / f"{key}.json").read_text())
        brief = json.loads(d["campaigns"][0]["brief_json"])
        changes = d["changes"]
        for v in d["variants"]:
            draft = json.loads(v["draft_json"])
            market = cfg.markets[v["market"]]
            avoid = {e.id for e in market.references.avoid}
            dropped_joke_reason = next((c["cites"] for c in changes if c["variant_id"] == v["id"]
                                        and c["cites"] in avoid), None)
            out.append({
                "id": f"{key}-{v['market']}",
                "label": f"{key} · {NAMES[v['market']]}",
                "brief": brief,
                "text": {k: draft[k] for k in ("headline", "body", "cta", "legal")},
                "stated_reason": dropped_joke_reason,
            })
    return out


def load_offvoice() -> list[dict]:
    legal = json.loads((ROOT / "fixtures" / "seed" / "baseline.json").read_text())["campaigns"][0]["master_json"]
    legal = json.loads(legal)["legal"]
    items = yaml.safe_load((ROOT / "tests" / "offvoice.yaml").read_text())
    return [{"id": i["id"], "breaks": i["breaks"], "brief": BRIEF,
             "text": {"headline": i["headline"], "body": i["body"], "cta": i["cta"], "legal": legal}} for i in items]


def score_once(brand, item: dict, n: int) -> dict[str, int]:
    crit = [c.id for c in brand.rubric]

    def check(o: ScoreOut):
        got = sorted(s.criterion for s in o.scores)
        return None if got == sorted(crit) else f"expected {crit}, got {got}"

    ctx = llm.CallContext(f"scorer-test-{n}", None, None, item["id"])
    out = llm.call_json(prompts.SCORE_SYSTEM, prompts.score_user(brand, item["brief"], item["text"]),
                        ScoreOut, ctx, check=check)
    return {s.criterion: s.score for s in out.scores}, {s.criterion: s.reason for s in out.scores}


def run_many(brand, items: list[dict]) -> dict[str, list]:
    jobs = [(it, n) for it in items for n in range(REPEATS)]
    results: dict[str, list] = {it["id"]: [] for it in items}
    failures: list[str] = []

    def work(job):
        it, n = job
        try:
            return it["id"], score_once(brand, it, n)
        except llm.StepError as e:
            return it["id"], e.message

    with ThreadPoolExecutor(max_workers=6) as pool:
        for vid, res in pool.map(work, jobs):
            if isinstance(res, str):
                failures.append(f"{vid}: {res}")
            else:
                results[vid].append(res)
    return results, failures


def mode(values: list[int]) -> int:
    c = Counter(values).most_common()
    top = c[0][1]
    return min(v for v, k in c if k == top)  # ties resolve to the stricter (lower) score


def main(fake: bool) -> None:
    random.seed(7)
    cfg = load_config()
    brand = cfg.brands["kin"]
    crit = [(c.id, c.name) for c in brand.rubric]
    init_db()
    if fake:
        llm.set_transport(fake_transport)

    seeds = load_seed_variants(cfg)
    offvoice = load_offvoice()
    human = yaml.safe_load((ROOT / "tests" / "human_scores.yaml").read_text()) or {}

    seed_runs, seed_fail = run_many(brand, seeds)
    off_runs, off_fail = run_many(brand, offvoice)

    # ---- 1. consistency ----
    consistency = {}
    for cid, name in crit:
        per_variant = []
        for it in seeds:
            vals = [r[0][cid] for r in seed_runs[it["id"]]]
            if vals:
                per_variant.append({"id": it["id"], "scores": vals, "range": max(vals) - min(vals),
                                    "sd": statistics.pstdev(vals)})
        consistency[cid] = {
            "name": name,
            "variants": per_variant,
            "unstable": sum(1 for p in per_variant if p["range"] > 0),
            "swing_2": sum(1 for p in per_variant if p["range"] >= 2),
            "mean_sd": round(statistics.mean(p["sd"] for p in per_variant), 3) if per_variant else None,
        }

    # ---- 2. off-voice ----
    offv = []
    for it in offvoice:
        runs = off_runs[it["id"]]
        target = [r[0][it["breaks"]] for r in runs]
        offv.append({
            "id": it["id"], "breaks": it["breaks"], "text": it["text"],
            "target_scores": target,
            "caught_runs": sum(1 for s in target if s <= 1),
            "runs": len(runs),
            "all_scores": [r[0] for r in runs],
            "reasons": [r[1][it["breaks"]] for r in runs],
        })

    # ---- 3. human agreement (scorer's most common score vs the user's) ----
    rows = []
    for it in seeds:
        h = human.get(it["id"]) or {}
        runs = seed_runs[it["id"]]
        if not runs:
            continue
        for cid, name in crit:
            if h.get(cid) is None:
                continue
            raw = mode([r[0][cid] for r in runs])
            adj = 1 if (cid == "humour" and raw == 0 and it["stated_reason"]) else raw
            # Quote only reasons from repeats that gave the reported score, so score and reason match.
            reasons = sorted({r[1][cid] for r in runs if r[0][cid] == raw})
            rows.append({"variant": it["id"], "criterion": cid, "name": name, "human": h[cid],
                         "ai_raw": raw, "ai_adjusted": adj, "ai_reasons": reasons[:2]})

    def agree(key: str, rs: list[dict]) -> dict:
        n = len(rs)
        exact = sum(1 for r in rs if r[key] == r["human"])
        within1 = sum(1 for r in rs if abs(r[key] - r["human"]) <= 1)
        return {"n": n, "exact": exact, "within_1": within1,
                "exact_pct": round(100 * exact / n, 1) if n else None}

    agreement = {
        "overall_raw": agree("ai_raw", rows),
        "overall_adjusted": agree("ai_adjusted", rows),
        "by_criterion": {cid: agree("ai_adjusted", [r for r in rows if r["criterion"] == cid]) for cid, _ in crit},
        "disagreements": [r for r in rows if r["ai_adjusted"] != r["human"]],
        "variants_scored_by_human": sum(1 for it in seeds if human.get(it["id"])),
    }

    # ---- cost ----
    conn = connect()
    calls = [json.loads(r["detail_json"]) for r in conn.execute("SELECT detail_json FROM log WHERE event='llm_call'")]
    conn.close()
    tin = sum(c.get("input_tokens", 0) for c in calls)
    tout = sum(c.get("output_tokens", 0) for c in calls)
    cost = {"calls": len(calls), "input_tokens": tin, "output_tokens": tout,
            "usd": round(llm.cost_usd(calls[0]["model"] if calls else "", tin, tout), 3),
            "model": calls[0]["model"] if calls else None}

    results = {"fake": fake, "repeats": REPEATS, "consistency": consistency, "offvoice": offv,
               "agreement": agreement, "failures": seed_fail + off_fail, "cost": cost,
               "seed_runs": {k: [r[0] for r in v] for k, v in seed_runs.items()},
               "seed_reasons": {k: [r[1] for r in v] for k, v in seed_runs.items()}}
    (ROOT / "docs").mkdir(exist_ok=True)
    suffix = "-fake" if fake else ""
    (ROOT / "docs" / f"scorer-test-results{suffix}.json").write_text(json.dumps(results, indent=2) + "\n")
    (ROOT / "docs" / f"scorer-test-data{suffix}.md").write_text(render(results, crit, seeds))
    print(render(results, crit, seeds))


def render(r: dict, crit, seeds) -> str:
    out = [f"# Scorer test data{' (DRY RUN, fake scorer)' if r['fake'] else ''}", "",
           f"Model: {r['cost']['model']}. {r['cost']['calls']} scoring calls, {r['repeats']} repeats each, "
           f"about ${r['cost']['usd']}.", ""]
    if r["failures"]:
        out += ["**Failed calls:** " + "; ".join(r["failures"]), ""]
    out += ["## 1. Consistency across 5 repeats (12 seed variants)", "",
            "| Criterion | Variants whose score changed between repeats | Swung by 2 | Mean SD |", "|---|---|---|---|"]
    for cid, name in crit:
        c = r["consistency"][cid]
        out.append(f"| {name} | {c['unstable']} of {len(c['variants'])} | {c['swing_2']} | {c['mean_sd']} |")
    out += ["", "Scores per variant (5 repeats):", "", "| Variant | " + " | ".join(n for _, n in crit) + " |",
            "|---|" + "---|" * len(crit)]
    for it in seeds:
        runs = r["seed_runs"].get(it["id"], [])
        cells = [" ".join(str(x[cid]) for x in runs) for cid, _ in crit]
        out.append(f"| {it['label']} | " + " | ".join(cells) + " |")
    out += ["", "## 2. Off-voice variants (caught = the broken criterion scores 0 or 1)", "",
            "| Variant | Breaks | Scores on that criterion | Caught |", "|---|---|---|---|"]
    for o in r["offvoice"]:
        out.append(f"| {o['id']} | {o['breaks']} | {' '.join(map(str, o['target_scores']))} | "
                   f"{o['caught_runs']} of {o['runs']} runs |")
    a = r["agreement"]
    out += ["", f"## 3. Agreement with human scores ({a['variants_scored_by_human']} variants scored)", "",
            f"- Exact agreement, raw scorer: {a['overall_raw']['exact']} of {a['overall_raw']['n']} "
            f"({a['overall_raw']['exact_pct']}%)",
            f"- Exact agreement, with the product's humour rule: {a['overall_adjusted']['exact']} of "
            f"{a['overall_adjusted']['n']} ({a['overall_adjusted']['exact_pct']}%)",
            f"- Within one point: {a['overall_adjusted']['within_1']} of {a['overall_adjusted']['n']}", "",
            "| Criterion | Exact agreement |", "|---|---|"]
    for cid, name in crit:
        b = a["by_criterion"][cid]
        out.append(f"| {name} | {b['exact']} of {b['n']} |")
    out += ["", "Disagreements (scorer's most common score vs human):", "",
            "| Variant | Criterion | Human | Scorer | Scorer's reason |", "|---|---|---|---|---|"]
    for d in a["disagreements"]:
        out.append(f"| {d['variant']} | {d['name']} | {d['human']} | {d['ai_adjusted']} | "
                   f"{d['ai_reasons'][0] if d['ai_reasons'] else ''} |")
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--fake", action="store_true", help="dry run with a canned scorer, no API spend")
    main(ap.parse_args().fake)
