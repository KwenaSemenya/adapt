"""Create tests/human_scores.yaml and tests/offvoice.yaml for a person to fill in.

Scores are never pre-filled: the scorer test compares the AI with human judgement.
Usage: uv run --project api python scripts/make_scoring_templates.py
"""

import json
import textwrap
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
NAMES = {"za": "South Africa", "ng": "Nigeria", "uk": "United Kingdom"}
TITLES = {"baseline": "Baseline", "us_reference": "US-only reference", "pun": "Pun", "sensitivity": "Sensitivity clash"}


def wrap(t: str, prefix: str = "#   ") -> str:
    return "\n".join(textwrap.wrap(t, 96, initial_indent=prefix, subsequent_indent=prefix))


def rubric_block(crit: list) -> str:
    out = ["# RUBRIC (score each criterion 0, 1 or 2)", "#"]
    for c in crit:
        out.append(f"# {c['id']} = {c['name']}: {c['question']}")
        for lvl in (0, 1, 2):
            out.append(wrap(f"{lvl}: {c['levels'][lvl]['description']}", "#     "))
        out.append("#")
    return "\n".join(out)


def main() -> None:
    crit = yaml.safe_load((ROOT / "config/brands/kin.yaml").read_text())["rubric"]
    rubric = rubric_block(crit)
    out = [
        "# Your scores for the seed variants. The scorer test compares the AI's scores with these.",
        "# Fill in each number with 0, 1 or 2. Leave a whole variant blank to skip it; the report",
        "# says how many you scored. Judge only the copy shown, as a Kin creative director would.",
        '# The legal line is the same on every variant: "Kin can make mistakes. Check important details."',
        "",
        rubric,
        "",
    ]
    for key in TITLES:
        d = json.loads((ROOT / f"fixtures/seed/{key}.json").read_text())
        out.append(f"# {'=' * 90}\n# {TITLES[key].upper()}\n# {'=' * 90}\n")
        for v in d["variants"]:
            dr = json.loads(v["draft_json"])
            out.append(f"# --- {TITLES[key]} · {NAMES[v['market']]} ---")
            out += [wrap(f"Headline: {dr['headline']}"), wrap(f"Body: {dr['body']}"), wrap(f"CTA: {dr['cta']}")]
            out.append(f"{key}-{v['market']}:")
            out += [f"  {c['id']}:        # {c['name']}" for c in crit]
            out.append("")
    (ROOT / "tests/human_scores.yaml").write_text("\n".join(out))

    slots = "\n".join(
        f'- id: offvoice-{i}\n  breaks:\n  headline: ""\n  body: ""\n  cta: ""\n' for i in (1, 2, 3)
    )
    (ROOT / "tests/offvoice.yaml").write_text(f"""# Three deliberately off-voice Kin variants. The scorer test checks the AI catches them.
# Write each one to break the voice in a different way, and name the criterion it breaks
# in `breaks` (one of: proposition, tone, humour, signatures, mandatories).
# The scorer "catches" it if it scores that criterion 0 or 1.
#
# Kin's voice: warm, plainspoken, dry, calm. Humour aimed at the chore, never at the user.
# No exclamation marks, no hype words. Names specific chores, talks to "you", ends on relief.
#
# Ideas: hype and exclamation marks (tone); a joke at the user's expense (humour);
# generic corporate wording with no specific chores (signatures); a CTA that says
# "free" without the 14-day length (mandatories); copy about something other than
# handling life admin (proposition).

{rubric}

{slots}""")
    for f in ("tests/human_scores.yaml", "tests/offvoice.yaml"):
        yaml.safe_load((ROOT / f).read_text())
        print(f, "written, valid YAML")


if __name__ == "__main__":
    main()
