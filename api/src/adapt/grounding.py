"""Deterministic grounding checks. None of this trusts the model.

- Every citation must resolve to a brief field or a snapshot entry.
- Every change must be locatable in the variant text.
- Any edit to the master that no change accounts for is an uncited claim.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from .config import BRIEF_IDS, Market

FIELDS = ("headline", "body", "cta")
BRIEF_LABELS = {"proposition": "Proposition", "audience": "Audience", "mandatories": "Mandatories", "tone": "Tone notes"}
SECTION_LABELS = {
    "voice_norms": "Voice norms",
    "register": "Register",
    "references.use": "References to use",
    "references.avoid": "References to avoid",
    "sensitivities": "Sensitivities",
    "compliance": "Compliance",
    "known_gaps": "Known gap",
}


@dataclass(frozen=True)
class Source:
    id: str
    kind: str  # brief | snapshot
    section: str  # brief field name or snapshot section
    label: str  # e.g. "Brief · Mandatories" or "Market snapshot · Known gap"
    text: str
    is_gap: bool = False
    illustrative: bool = False


MASTER_ID = "BRIEF-MASTER"


def citable(brief: dict, market: Market | None, master: dict | None = None) -> dict[str, Source]:
    """Everything a change or flag may cite for this brief and market.

    `master` is passed only for flagging: a flag may cite the US master copy to say
    "the variant claims something the master doesn't". Changes never cite it.
    """
    out: dict[str, Source] = {}
    if master:
        text = " / ".join(master[k] for k in ("headline", "body", "cta"))
        out[MASTER_ID] = Source(MASTER_ID, "brief", "master", "Brief · US master copy", text)
    for bid, field in BRIEF_IDS.items():
        value = (brief.get(field) or "").strip()
        if value:
            out[bid] = Source(bid, "brief", field, f"Brief · {BRIEF_LABELS[field]}", value)
    if market:
        for section, entry in market.entries():
            out[entry.id] = Source(
                entry.id, "snapshot", section, f"Market snapshot · {SECTION_LABELS[section]}", entry.text,
                is_gap=section == "known_gaps", illustrative=market.status == "illustrative",
            )
    return out


# ---------- locating text ----------

_TOKEN = re.compile(r"\s+|\w+(?:['’]\w+)*|[^\w\s]")


def _tokens(text: str) -> list[tuple[str, int, int]]:
    return [(m.group(), m.start(), m.end()) for m in _TOKEN.finditer(text)]


def locate(text: str, needles: list[tuple[str, str]]) -> tuple[list[dict], set[str]]:
    """Split `text` into plain and change segments.

    needles: [(change_id, now_text)]. Each is matched to its first non-overlapping
    occurrence. Longer changes are placed first, so a small change nested inside a
    larger rewrite is found (it stays grounded but gets no segment of its own).
    Returns (segments, ids whose text is not in the variant at all).
    """
    needles = sorted(needles, key=lambda n: -len(n[1].strip()))
    taken: list[tuple[int, int, str]] = []
    missing: set[str] = set()
    for cid, now in needles:
        now = now.strip()
        if not now:
            missing.add(cid)
            continue
        start, found = 0, False
        while (i := text.find(now, start)) != -1:
            j = i + len(now)
            if not any(i < b and a < j for a, b, _ in taken):
                taken.append((i, j, cid))
                found = True
                break
            start = i + 1
        if not found and now not in text:
            missing.add(cid)
    taken.sort()
    segs: list[dict] = []
    pos = 0
    for a, b, cid in taken:
        if a > pos:
            segs.append({"t": text[pos:a]})
        segs.append({"t": text[a:b], "change": cid})
        pos = b
    if pos < len(text):
        segs.append({"t": text[pos:]})
    return segs, missing


def unmarked_edits(master: str, variant: str, covered: list[tuple[int, int]]) -> list[str]:
    """Variant text that differs from the master and isn't inside any located change."""
    a, b = _tokens(master), _tokens(variant)
    sm = difflib.SequenceMatcher(a=[t for t, _, _ in a], b=[t for t, _, _ in b], autojunk=False)
    out: list[str] = []
    for op, _i1, _i2, j1, j2 in sm.get_opcodes():
        if op not in ("insert", "replace") or j1 == j2:
            continue
        span_tokens = b[j1:j2]
        words = [t for t, _, _ in span_tokens if re.match(r"\w", t)]
        if not words:
            continue  # punctuation or whitespace only
        s, e = span_tokens[0][1], span_tokens[-1][2]
        # Trim whitespace at the edges before checking coverage.
        while s < e and variant[s].isspace():
            s += 1
        while e > s and variant[e - 1].isspace():
            e -= 1
        if not any(cs <= s and e <= ce for cs, ce in covered):
            out.append(variant[s:e])
    return out


def ranges(segs: list[dict]) -> list[tuple[int, int]]:
    out, pos = [], 0
    for s in segs:
        if s.get("change"):
            out.append((pos, pos + len(s["t"])))
        pos += len(s["t"])
    return out


def overlaps(quote: str, text: str, segs: list[dict]) -> str | None:
    """The change id whose located text overlaps the quoted text, if any."""
    quote = quote.strip()
    if not quote:
        return None
    i = text.find(quote)
    pos = 0
    for s in segs:
        a, b = pos, pos + len(s["t"])
        pos = b
        cid = s.get("change")
        if not cid:
            continue
        if i != -1 and i < b and a < i + len(quote):
            return cid
        if s["t"].strip() and (s["t"].strip() in quote or quote in s["t"]):
            return cid
    return None
