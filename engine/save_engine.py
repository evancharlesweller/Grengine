from __future__ import annotations

from typing import Any, Dict, Iterable, Optional

ABILITY_KEYS = ("STR", "DEX", "CON", "INT", "WIS", "CHA")


def normalize_ability_key(value: str) -> str:
    key = str(value or "").strip().upper()
    aliases = {
        "STRENGTH": "STR",
        "DEXTERITY": "DEX",
        "CONSTITUTION": "CON",
        "INTELLIGENCE": "INT",
        "WISDOM": "WIS",
        "CHARISMA": "CHA",
    }
    key = aliases.get(key, key)
    return key if key in ABILITY_KEYS else ""


def _normalized_abilities(actor: Any) -> Dict[str, int]:
    raw = getattr(actor, "abilities", {}) or {}
    out: Dict[str, int] = {}
    if isinstance(raw, dict):
        for k, v in raw.items():
            nk = normalize_ability_key(str(k))
            if not nk:
                continue
            try:
                out[nk] = int(v)
            except Exception:
                out[nk] = 10
    for k in ABILITY_KEYS:
        out.setdefault(k, 10)
    return out


def save_modifier_for_actor(actor: Any, rules: Any, ability_key: str) -> int:
    key = normalize_ability_key(ability_key)
    if not key:
        return 0

    raw_abilities = getattr(actor, "abilities", None)
    raw_save_profs = list(getattr(actor, "save_proficiencies", []) or [])
    raw_save_bonus = dict(getattr(actor, "save_bonus", {}) or {})

    class _Adapter:
        pass

    adapted = _Adapter()
    adapted.abilities = _normalized_abilities(actor)
    adapted.proficiency_bonus = int(getattr(actor, "proficiency_bonus", 0) or 0)
    adapted.save_proficiencies = [normalize_ability_key(x) for x in raw_save_profs if normalize_ability_key(x)]
    adapted.save_bonus = {normalize_ability_key(k): int(v) for k, v in raw_save_bonus.items() if normalize_ability_key(k)}

    try:
        mod = int(rules.save_mod(adapted, key))
    except Exception:
        score = int(adapted.abilities.get(key, 10) or 10)
        mod = int((score - 10) // 2)
        if key in set(adapted.save_proficiencies):
            mod += int(adapted.proficiency_bonus or 0)
    mod += int((adapted.save_bonus or {}).get(key, 0) or 0)
    return int(mod)


def resolve_save_result(
    *,
    actor: Any,
    rules: Any,
    ability_key: str,
    d20_value: int,
    dc: int,
    mode: str = "normal",
    rolls: Optional[Iterable[int]] = None,
    extras: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    label: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    key = normalize_ability_key(ability_key)
    if not key:
        raise ValueError("Invalid ability key")

    modifier = int(save_modifier_for_actor(actor, rules, key))
    chosen = int(d20_value)
    total = int(chosen + modifier)
    success = bool(total >= int(dc))
    roll_list = [int(r) for r in (list(rolls or []) or [chosen])]

    return {
        "request_id": str(request_id or ""),
        "roll_kind": "save",
        "ability": key,
        "label": str(label or "").strip(),
        "mode": str(mode or "normal"),
        "rolls": roll_list,
        "chosen": chosen,
        "modifier": modifier,
        "total": total,
        "dc": int(dc),
        "success": success,
        "context": dict(context or {}),
        "extras": dict(extras or {}),
    }



def _normalize_mode(value: str) -> str:
    mode = str(value or "normal").strip().lower()
    if mode in {"adv", "advantage"}:
        return "advantage"
    if mode in {"dis", "disadvantage"}:
        return "disadvantage"
    return "normal"


def roll_engine_save_result(
    *,
    actor: Any,
    rules: Any,
    ability_key: str,
    dc: int,
    mode: str = "normal",
    extras: Optional[Dict[str, Any]] = None,
    request_id: str = "",
    label: str = "",
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Deterministically resolve a saving throw completely inside the DM engine.
    Used for environmental / hazard / procedural saves where the portal should
    not stop combat flow.
    """
    from engine.hazard_engine import roll_dice

    norm_mode = _normalize_mode(mode)
    roll_count = 2 if norm_mode in {"advantage", "disadvantage"} else 1
    raw_rolls = []
    for _ in range(roll_count):
        total, rolls, _ = roll_dice("1d20")
        raw_rolls.append(int(rolls[0] if rolls else total))

    if norm_mode == "advantage" and len(raw_rolls) > 1:
        chosen = max(raw_rolls)
    elif norm_mode == "disadvantage" and len(raw_rolls) > 1:
        chosen = min(raw_rolls)
    else:
        chosen = int(raw_rolls[0])

    return resolve_save_result(
        actor=actor,
        rules=rules,
        ability_key=ability_key,
        d20_value=int(chosen),
        dc=int(dc),
        mode=norm_mode,
        rolls=list(raw_rolls),
        extras=extras,
        request_id=request_id,
        label=label,
        context=context,
    )


def compute_damage_after_save(base_damage: int, success: bool, on_success: str = "none") -> int:
    """
    Apply the success branch of a damaging saving throw.
    on_success:
      - 'none' : no damage on success
      - 'half' : floor(base_damage / 2) on success
      - 'full' : unchanged on success (rare, but supported)
    """
    dmg = max(0, int(base_damage or 0))
    branch = str(on_success or "none").strip().lower()
    if not success:
        return dmg
    if branch == "half":
        return int(dmg // 2)
    if branch == "full":
        return int(dmg)
    return 0
