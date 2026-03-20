# engine/perception_engine.py
"""Perception policy layer (B-X4: Vision Types).

This module is deliberately *separate* from geometry LOS and fog rendering.

Geometry answers: "is there an unobstructed line?" (B2/B5)
Perception answers: "given LOS + lighting + senses, can I perceive the target?"

Design goals:
- Deterministic (no Qt, no IO)
- Backwards compatible: if no lighting data is present, everything behaves as bright light.
- Minimal D&D 5e-aligned behavior suitable for incremental expansion.

Metadata (future lighting authoring):
  meta["lighting"] = {"x,y": "bright"|"dim"|"dark"|"magical_dark"}

If absent, light defaults to "bright".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


LIGHT_BRIGHT = "bright"
LIGHT_DIM = "dim"
LIGHT_DARK = "dark"
LIGHT_MAGICAL_DARK = "magical_dark"


def get_cell_light_level(meta: Dict[str, Any], gx: int, gy: int) -> str:
    """Return lighting level for a cell (defaults to bright)."""
    m = meta or {}
    lighting = (m.get("lighting") or m.get("light") or {})
    if not isinstance(lighting, dict):
        return LIGHT_BRIGHT
    key = f"{int(gx)},{int(gy)}"
    lvl = str(lighting.get(key, LIGHT_BRIGHT) or LIGHT_BRIGHT).strip().lower()
    if lvl in (LIGHT_BRIGHT, LIGHT_DIM, LIGHT_DARK, LIGHT_MAGICAL_DARK):
        return lvl
    return LIGHT_BRIGHT


def _distance_ft(att_gx: int, att_gy: int, tgt_gx: int, tgt_gy: int, *, feet_per_square: int = 5) -> float:
    import math

    dx = (float(tgt_gx) + 0.5) - (float(att_gx) + 0.5)
    dy = (float(tgt_gy) + 0.5) - (float(att_gy) + 0.5)
    return math.sqrt(dx * dx + dy * dy) * float(max(1, int(feet_per_square)))


def _status_has(ts: Any, name: str) -> bool:
    try:
        for s in (getattr(ts, "statuses", None) or []):
            if isinstance(s, dict) and str(s.get("id", "")).strip().lower() == str(name).strip().lower():
                return True
    except Exception:
        return False
    return False


def _is_grounded(ts: Any) -> bool:
    """v1: treat flying/airborne as not grounded."""
    try:
        if bool(getattr(ts, "is_flying", False)):
            return False
    except Exception:
        pass
    if _status_has(ts, "flying") or _status_has(ts, "airborne"):
        return False
    return True


@dataclass(frozen=True)
class PerceptionResult:
    can_perceive: bool
    method: str
    light_level: str
    reason: str = ""
    obscured: str = "none"  # none|light|heavy


def can_perceive_target(
    *,
    attacker_ts: Any,
    target_ts: Any,
    meta: Optional[Dict[str, Any]] = None,
    feet_per_square: int = 5,
    requires_sight: bool = True,
) -> PerceptionResult:
    """Return whether attacker can perceive target for targeting.

    Minimal 5e-aligned behavior:
    - Bright: normal sight works.
    - Dim: lightly obscured (not a hard block).
    - Darkness: blocks *sight* unless darkvision/truesight/devils_sight or non-visual senses apply.
    - Darkvision: darkness treated as dim (within range). Does NOT pierce magical darkness.
    - Truesight / Devil's Sight: can see in magical darkness (within range).
    - Blindsight: ignores lighting within range.
    - Tremorsense: ignores lighting within range, but requires both on the same ground (grounded).

    Engine note: we do not model disadvantage for unseen targets yet. If the attack
    requires sight and there is no valid sense, we block targeting.
    """

    m = meta or {}

    try:
        agx = int(getattr(attacker_ts, "grid_x", 0) or 0)
        agy = int(getattr(attacker_ts, "grid_y", 0) or 0)
        tgx = int(getattr(target_ts, "grid_x", 0) or 0)
        tgy = int(getattr(target_ts, "grid_y", 0) or 0)
    except Exception:
        return PerceptionResult(False, method="none", light_level=LIGHT_BRIGHT, reason="bad_coords")

    dist_ft = _distance_ft(agx, agy, tgx, tgy, feet_per_square=feet_per_square)
    light = get_cell_light_level(m, tgx, tgy)

    def _int_attr(name: str) -> int:
        try:
            return int(getattr(attacker_ts, name, 0) or 0)
        except Exception:
            return 0

    vision_type = str(getattr(attacker_ts, "vision_type", "normal") or "normal").strip().lower()
    normal_ft = _int_attr("vision_ft")
    darkvision_ft = _int_attr("darkvision_ft")
    blindsight_ft = _int_attr("blindsight_ft")
    truesight_ft = _int_attr("truesight_ft")
    tremorsense_ft = _int_attr("tremorsense_ft")
    devils_sight_ft = _int_attr("devils_sight_ft")

    # Compatibility: if a profile is set but its dedicated range is 0, fall back to vision_ft.
    if vision_type == "darkvision" and darkvision_ft <= 0:
        darkvision_ft = max(0, normal_ft)
    if vision_type == "blindsight" and blindsight_ft <= 0:
        blindsight_ft = max(0, normal_ft)
    if vision_type == "truesight" and truesight_ft <= 0:
        truesight_ft = max(0, normal_ft)
    if vision_type == "tremorsense" and tremorsense_ft <= 0:
        tremorsense_ft = max(0, normal_ft)
    if vision_type in ("devils_sight", "devilssight") and devils_sight_ft <= 0:
        devils_sight_ft = max(120, darkvision_ft, normal_ft)

    # Non-visual senses first
    if blindsight_ft > 0 and dist_ft <= float(blindsight_ft):
        return PerceptionResult(True, method="blindsight", light_level=light, obscured="none")

    if tremorsense_ft > 0 and dist_ft <= float(tremorsense_ft):
        if _is_grounded(attacker_ts) and _is_grounded(target_ts):
            return PerceptionResult(True, method="tremorsense", light_level=light, obscured="none")

    if truesight_ft > 0 and dist_ft <= float(truesight_ft):
        return PerceptionResult(True, method="truesight", light_level=light, obscured="none")

    if devils_sight_ft > 0 and dist_ft <= float(devils_sight_ft):
        return PerceptionResult(True, method="devils_sight", light_level=light, obscured="none")

    if not requires_sight:
        return PerceptionResult(True, method="not_required", light_level=light, obscured="none")

    # If lighting isn't authored yet, treat as bright.
    if light == LIGHT_BRIGHT:
        return PerceptionResult(True, method="sight", light_level=light, obscured="none")

    if light == LIGHT_DIM:
        return PerceptionResult(True, method="sight", light_level=light, obscured="light")

    if light == LIGHT_MAGICAL_DARK:
        return PerceptionResult(False, method="sight", light_level=light, reason="magical_darkness", obscured="heavy")

    # Mundane darkness
    if darkvision_ft > 0 and dist_ft <= float(darkvision_ft):
        return PerceptionResult(True, method="darkvision", light_level=light, obscured="light")

    return PerceptionResult(False, method="sight", light_level=light, reason="darkness", obscured="heavy")


def can_perceive_cell(
    *,
    attacker_ts: Any,
    gx: int,
    gy: int,
    meta: Optional[Dict[str, Any]] = None,
    feet_per_square: int = 5,
    requires_sight: bool = True,
) -> PerceptionResult:
    """Like can_perceive_target, but for a bare map cell (no target token).

    Used for fog/visibility: determines whether a cell's lighting can be perceived by the attacker.
    """
    m = meta or {}
    try:
        agx = int(getattr(attacker_ts, "grid_x", 0) or 0)
        agy = int(getattr(attacker_ts, "grid_y", 0) or 0)
        tgx = int(gx)
        tgy = int(gy)
    except Exception:
        return PerceptionResult(False, method="none", light_level=LIGHT_BRIGHT, reason="bad_coords")

    dist_ft = _distance_ft(agx, agy, tgx, tgy, feet_per_square=feet_per_square)
    light = get_cell_light_level(m, tgx, tgy)

    def _int_attr(name: str) -> int:
        try:
            return int(getattr(attacker_ts, name, 0) or 0)
        except Exception:
            return 0

    vision_type = str(getattr(attacker_ts, "vision_type", "normal") or "normal").strip().lower()
    normal_ft = _int_attr("vision_ft")
    darkvision_ft = _int_attr("darkvision_ft")
    blindsight_ft = _int_attr("blindsight_ft")
    truesight_ft = _int_attr("truesight_ft")
    tremorsense_ft = _int_attr("tremorsense_ft")
    devils_sight_ft = _int_attr("devils_sight_ft")

    if vision_type == "darkvision" and darkvision_ft <= 0:
        darkvision_ft = max(0, normal_ft)
    if vision_type == "blindsight" and blindsight_ft <= 0:
        blindsight_ft = max(0, normal_ft)
    if vision_type == "truesight" and truesight_ft <= 0:
        truesight_ft = max(0, normal_ft)
    if vision_type == "tremorsense" and tremorsense_ft <= 0:
        tremorsense_ft = max(0, normal_ft)
    if vision_type in ("devils_sight", "devilssight") and devils_sight_ft <= 0:
        devils_sight_ft = max(120, darkvision_ft, normal_ft)

    # Non-visual senses
    if blindsight_ft > 0 and dist_ft <= float(blindsight_ft):
        return PerceptionResult(True, method="blindsight", light_level=light, obscured="none")

    if tremorsense_ft > 0 and dist_ft <= float(tremorsense_ft):
        # Cells are treated as grounded; attacker must be grounded.
        if _is_grounded(attacker_ts):
            return PerceptionResult(True, method="tremorsense", light_level=light, obscured="none")

    if truesight_ft > 0 and dist_ft <= float(truesight_ft):
        return PerceptionResult(True, method="truesight", light_level=light, obscured="none")

    if devils_sight_ft > 0 and dist_ft <= float(devils_sight_ft):
        return PerceptionResult(True, method="devils_sight", light_level=light, obscured="none")

    if not requires_sight:
        return PerceptionResult(True, method="not_required", light_level=light, obscured="none")

    if light == LIGHT_BRIGHT:
        return PerceptionResult(True, method="sight", light_level=light, obscured="none")
    if light == LIGHT_DIM:
        return PerceptionResult(True, method="sight", light_level=light, obscured="light")
    if light == LIGHT_MAGICAL_DARK:
        return PerceptionResult(False, method="sight", light_level=light, reason="magical_darkness", obscured="heavy")

    # Mundane darkness
    if darkvision_ft > 0 and dist_ft <= float(darkvision_ft):
        return PerceptionResult(True, method="darkvision", light_level=light, obscured="light")

    return PerceptionResult(False, method="sight", light_level=light, reason="darkness", obscured="heavy")
