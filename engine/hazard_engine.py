from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

_DICE_RE = re.compile(r"^\s*(\d+)\s*d\s*(\d+)\s*([+-]\s*\d+)?\s*$", re.IGNORECASE)

VALID_TRIGGERS = ("enter", "turn_start", "turn_end")


@dataclass(frozen=True)
class HazardTrigger:
    hazard_type: str
    trigger: str
    damage_expr: str
    source: Dict[str, Any]  # original hazard dict


def normalize_trigger(trigger: str) -> str:
    t = str(trigger or "enter").strip().lower()
    # common aliases
    if t in ("on_enter", "enter", "move", "step"):
        return "enter"
    if t in ("turnstart", "turn_start", "start", "start_turn", "turn start"):
        return "turn_start"
    if t in ("turnend", "turn_end", "end", "end_turn", "turn end"):
        return "turn_end"
    return "enter"


def roll_dice(expr: str) -> Tuple[int, List[int], int]:
    """Return (total, rolls, mod). Supports 'NdS(+/-)M' or integer strings."""
    s = str(expr or "").strip()
    m = _DICE_RE.match(s)
    if not m:
        try:
            v = int(s)
            return v, [], 0
        except Exception:
            # safest default
            return 1, [], 0

    n = max(1, int(m.group(1)))
    sides = max(1, int(m.group(2)))
    mod = int(m.group(3).replace(" ", "")) if m.group(3) else 0
    rolls = [random.randint(1, sides) for _ in range(n)]
    return sum(rolls) + mod, rolls, mod


def _hazard_matches_trigger(h: Dict[str, Any], want_trigger: str) -> bool:
    """
    Supports both:
      - legacy: h["trigger"] = "enter"
      - multi:  h["triggers"] = ["enter","turn_start","turn_end"]
    """
    want = normalize_trigger(want_trigger)

    trigs = h.get("triggers", None)
    if isinstance(trigs, list):
        norm = set()
        for t in trigs:
            nt = normalize_trigger(t)
            if nt in VALID_TRIGGERS:
                norm.add(nt)
        return want in norm

    # legacy
    ht = normalize_trigger(h.get("trigger", "enter"))
    return ht == want


def hazards_for_cell(meta: Dict[str, Any], gx: int, gy: int, *, trigger: str) -> List[HazardTrigger]:
    want = normalize_trigger(trigger)
    hazards = (meta or {}).get("hazards", []) or []
    if not isinstance(hazards, list):
        return []

    out: List[HazardTrigger] = []
    for h in hazards:
        if not isinstance(h, dict):
            continue
        try:
            hx = int(h.get("x"))
            hy = int(h.get("y"))
        except Exception:
            continue
        if hx != int(gx) or hy != int(gy):
            continue

        if not _hazard_matches_trigger(h, want):
            continue

        hazard_type = str(h.get("hazard_type", h.get("type", "fire")) or "fire").strip().lower()
        dmg = str(h.get("damage", h.get("damage_expr", "1")) or "1").strip()

        # Important: we emit a HazardTrigger for the specific trigger we are resolving now
        out.append(HazardTrigger(hazard_type=hazard_type, trigger=want, damage_expr=dmg, source=h))

    return out


def resolve_hazards(meta: Dict[str, Any], gx: int, gy: int, *, trigger: str) -> List[Dict[str, Any]]:
    """
    Resolve hazard triggers into concrete damage rolls.
    Returns list of dicts:
      {hazard_type, trigger, damage_expr, damage_total, rolls, mod}
    """
    out: List[Dict[str, Any]] = []
    for hz in hazards_for_cell(meta, gx, gy, trigger=trigger):
        total, rolls, mod = roll_dice(hz.damage_expr)
        out.append(
            {
                "hazard_type": hz.hazard_type,
                "trigger": hz.trigger,          # <- correct key
                "damage_expr": hz.damage_expr,
                "damage_total": int(total),
                "rolls": list(rolls),
                "mod": int(mod),
            }
        )
    return out
