"""Load and validate everything under config/.

Content lives in files, not code: a brand is one YAML file, a market is one YAML
file. Validation runs at startup and fails loudly, naming the file and the
entry, because a silently skipped snapshot entry would let the model cite an ID
the reviewer can't look up.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CONFIG_DIR = ROOT / "config"

# Brief fields are citable like snapshot entries, so they get fixed IDs.
BRIEF_IDS = {
    "BRIEF-PROPOSITION": "proposition",
    "BRIEF-AUDIENCE": "audience",
    "BRIEF-MANDATORIES": "mandatories",
    "BRIEF-TONE": "tone",
}

Severity = Literal["High", "Medium", "Low"]


class ConfigError(Exception):
    """Raised with every problem found, so one restart shows the whole list."""

    def __init__(self, problems: list[str]):
        self.problems = problems
        super().__init__(
            "ADAPT config is invalid. Fix these and restart:\n"
            + "\n".join(f"  - {p}" for p in problems)
        )


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


# ---------- brand ----------


class RubricLevel(Strict):
    description: str = Field(min_length=1)
    example: str = Field(min_length=1)


class Criterion(Strict):
    id: str = Field(pattern=r"^[a-z][a-z_]*$")
    name: str = Field(min_length=1)
    question: str = Field(min_length=1)
    levels: dict[int, RubricLevel]

    @field_validator("levels")
    @classmethod
    def _levels_0_1_2(cls, v: dict[int, RubricLevel]) -> dict[int, RubricLevel]:
        if sorted(v) != [0, 1, 2]:
            raise ValueError(f"levels must be exactly 0, 1 and 2 (got {sorted(v)})")
        return v


class Voice(Strict):
    traits: list[str] = Field(min_length=1)
    humour: str
    signatures: list[str] = Field(min_length=1)
    never: list[str] = Field(default_factory=list)


class Brand(Strict):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    name: str
    proposition: str
    voice: Voice
    rubric: list[Criterion] = Field(min_length=1)


# ---------- market snapshot ----------


class Entry(Strict):
    id: str
    text: str = Field(min_length=1)


class SensitivityEntry(Entry):
    severity: Severity


class ComplianceEntry(Entry):
    source: str = Field(min_length=1)
    verified: bool


class References(Strict):
    use: list[Entry] = Field(default_factory=list)
    avoid: list[Entry] = Field(default_factory=list)


class Market(Strict):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, populate_by_name=True)

    code: str = Field(pattern=r"^[a-z]{2}$")
    name: str
    language: str = ""
    updated: str = ""
    voice_norms: list[Entry] = Field(default_factory=list)
    register_: list[Entry] = Field(default_factory=list, alias="register")
    references: References = Field(default_factory=References)
    sensitivities: list[SensitivityEntry] = Field(default_factory=list)
    compliance: list[ComplianceEntry] = Field(default_factory=list)
    known_gaps: list[Entry] = Field(default_factory=list)

    def entries(self) -> list[tuple[str, Entry]]:
        """Every citable entry with its section name."""
        out: list[tuple[str, Entry]] = []
        for section in ("voice_norms", "register", "sensitivities", "compliance", "known_gaps"):
            out += [(section, e) for e in getattr(self, "register_" if section == "register" else section)]
        out += [("references.use", e) for e in self.references.use]
        out += [("references.avoid", e) for e in self.references.avoid]
        return out

    def index(self) -> dict[str, tuple[str, Entry]]:
        return {e.id: (section, e) for section, e in self.entries()}

    def gap_ids(self) -> set[str]:
        return {e.id for e in self.known_gaps}


class Baseline(Strict):
    hours_per_market: float | None = Field(default=None, gt=0)
    note: str = ""


class Config(BaseModel):
    brands: dict[str, Brand]
    markets: dict[str, Market]
    baseline: Baseline
    source_dir: str


# ---------- loading ----------


def _read_yaml(path: Path, problems: list[str]) -> object | None:
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        problems.append(f"{path.name}: not valid YAML ({e})")
    return None


def _pydantic_problems(path: Path, err: ValidationError) -> list[str]:
    out = []
    for e in err.errors():
        where = ".".join(str(p) for p in e["loc"]) or "(top level)"
        out.append(f"{path.name}: {where}: {e['msg']}")
    return out


def load_config(config_dir: Path | str | None = None) -> Config:
    base = Path(config_dir or os.environ.get("ADAPT_CONFIG_DIR") or DEFAULT_CONFIG_DIR)
    problems: list[str] = []
    brands: dict[str, Brand] = {}
    markets: dict[str, Market] = {}
    seen_ids: dict[str, str] = {}  # ID -> file it was first seen in

    def claim(id_: str, where: str) -> None:
        if id_ in seen_ids:
            problems.append(f"{where}: duplicate ID {id_} (already used in {seen_ids[id_]})")
        else:
            seen_ids[id_] = where

    for bid in BRIEF_IDS:
        claim(bid, "brief fields")

    # Files starting with "_" are templates and never loaded.
    for path in sorted((base / "brands").glob("[!_]*.yaml")):
        raw = _read_yaml(path, problems)
        if raw is None:
            continue
        try:
            brand = Brand.model_validate(raw)
        except ValidationError as e:
            problems += _pydantic_problems(path, e)
            continue
        if brand.id != path.stem:
            problems.append(f"{path.name}: id '{brand.id}' must match the file name '{path.stem}'")
        crit_ids = [c.id for c in brand.rubric]
        if len(set(crit_ids)) != len(crit_ids):
            problems.append(f"{path.name}: rubric criterion IDs must be unique ({crit_ids})")
        brands[brand.id] = brand

    for path in sorted((base / "markets").glob("[!_]*.yaml")):
        raw = _read_yaml(path, problems)
        if raw is None:
            continue
        try:
            market = Market.model_validate(raw)
        except ValidationError as e:
            problems += _pydantic_problems(path, e)
            continue
        if market.code != path.stem:
            problems.append(f"{path.name}: code '{market.code}' must match the file name '{path.stem}'")
        id_re = re.compile(rf"^{market.code.upper()}-[A-Z]{{1,3}}\d{{1,3}}$")
        for section, entry in market.entries():
            where = f"{path.name} {section}"
            if not id_re.match(entry.id):
                problems.append(
                    f"{where}: malformed ID '{entry.id}'. Use {market.code.upper()}-<letters><number>, "
                    f"e.g. {market.code.upper()}-C1"
                )
            claim(entry.id, path.name)
        if not market.entries():
            problems.append(f"{path.name}: snapshot has no entries")
        markets[market.code] = market

    baseline = Baseline()
    bpath = base / "baseline.yaml"
    if bpath.exists():
        raw = _read_yaml(bpath, problems) or {}
        try:
            baseline = Baseline.model_validate(raw)
        except ValidationError as e:
            problems += _pydantic_problems(bpath, e)

    if not brands:
        problems.append(f"no brand files found in {base / 'brands'}")

    if problems:
        raise ConfigError(problems)
    return Config(brands=brands, markets=markets, baseline=baseline, source_dir=str(base))
