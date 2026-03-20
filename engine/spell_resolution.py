from __future__ import annotations

import re
from copy import deepcopy
from typing import Any, Dict, List, Tuple

_EFFECT_ALIASES = {
    "condition_apply": "condition_apply",
    "apply_condition": "condition_apply",
    "condition": "condition_apply",
    "condition_remove": "condition_remove",
    "remove_condition": "condition_remove",
    "temporary_hp": "temp_hp",
    "temp_hp": "temp_hp",
    "heal": "heal",
    "healing": "heal",
    "damage": "damage",
    "advantage": "advantage",
    "disadvantage": "disadvantage",
    "bonus": "bonus",
    "modifier": "bonus",
    "stat_bonus": "bonus",
    "buff": "bonus",
    "move": "move",
    "forced_movement": "forced_movement",
    "teleport": "move",
    "light": "light",
}

_DICE_RE = re.compile(r"^\s*(\d+)d(\d+)([+-]\d+)?\s*$", re.I)
_DICE_TOKEN_RE = re.compile(r"^(\d+)d(\d+)$", re.I)
_REPEAT_RE = re.compile(r"^\s*(\d+)\s*\*\s*\((.+)\)\s*$", re.I)




def normalize_effect_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    return _EFFECT_ALIASES.get(raw, raw)


def normalized_effect_payload(effect: Dict[str, Any] | None, *, spell_row: Dict[str, Any] | None = None) -> Dict[str, Any]:
    fx = deepcopy(effect or {}) if isinstance(effect, dict) else {}
    fx_type = normalize_effect_type(fx.get("type"))
    fx["type"] = fx_type
    if fx_type in {"damage", "heal", "temp_hp"}:
        expr = str(fx.get("expr") or "").strip()
        if not expr and isinstance(spell_row, dict):
            damage = spell_row.get("damage") if isinstance(spell_row.get("damage"), dict) else {}
            expr = str(damage.get("expr") or spell_row.get("damage_expr") or "").strip()
        if expr:
            fx["expr"] = expr
    if fx_type == "damage":
        if "damage_type" not in fx and isinstance(spell_row, dict):
            damage = spell_row.get("damage") if isinstance(spell_row.get("damage"), dict) else {}
            fx["damage_type"] = str(fx.get("type_name") or damage.get("type") or spell_row.get("damage_type") or "").strip().lower()
    if fx_type in {"condition_apply", "condition_remove"}:
        if fx.get("name") in (None, "") and fx.get("condition") not in (None, ""):
            fx["name"] = fx.get("condition")
        if isinstance(fx.get("names"), str):
            fx["names"] = [part.strip() for part in str(fx.get("names") or "").replace("\n", ",").split(",") if part.strip()]
    return fx



def _split_top_level_sum(expr: str) -> List[str]:
    parts: List[str] = []
    buf: List[str] = []
    depth = 0
    for idx, ch in enumerate(str(expr or "").strip()):
        if ch == "(":
            depth += 1
            buf.append(ch)
            continue
        if ch == ")":
            depth = max(0, depth - 1)
            buf.append(ch)
            continue
        if depth == 0 and ch in "+-" and idx > 0:
            parts.append("".join(buf).strip())
            buf = [ch]
            continue
        buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())
    return [p for p in parts if p]


def roll_spell_expr(expr: str, *, rng=None) -> Tuple[int, List[int], int]:
    import random

    rng = rng or random
    text = str(expr or "").strip()
    if not text:
        return 0, [], 0

    repeat_match = _REPEAT_RE.match(text)
    if repeat_match:
        count = max(0, int(repeat_match.group(1) or 0))
        inner = str(repeat_match.group(2) or "").strip()
        total = 0
        rolls: List[int] = []
        modifier = 0
        for _ in range(count):
            sub_total, sub_rolls, sub_mod = roll_spell_expr(inner, rng=rng)
            total += int(sub_total or 0)
            rolls.extend(list(sub_rolls or []))
            modifier += int(sub_mod or 0)
        return total, rolls, modifier

    parts = _split_top_level_sum(text)
    if len(parts) > 1:
        total = 0
        rolls: List[int] = []
        modifier = 0
        for part in parts:
            sign = -1 if part.startswith("-") else 1
            atom = part[1:].strip() if part[:1] in "+-" else part
            sub_total, sub_rolls, sub_mod = roll_spell_expr(atom, rng=rng)
            total += sign * int(sub_total or 0)
            if sign >= 0:
                rolls.extend(list(sub_rolls or []))
                modifier += int(sub_mod or 0)
            else:
                rolls.extend([-int(v) for v in list(sub_rolls or [])])
                modifier -= int(sub_mod or 0)
        return total, rolls, modifier

    if text.startswith("(") and text.endswith(")"):
        return roll_spell_expr(text[1:-1], rng=rng)

    dice_match = _DICE_TOKEN_RE.match(text.lstrip("+"))
    if dice_match:
        dice_count = max(0, int(dice_match.group(1) or 0))
        dice_size = max(1, int(dice_match.group(2) or 1))
        rolled = [rng.randint(1, dice_size) for _ in range(dice_count)]
        return sum(rolled), rolled, 0

    full_match = _DICE_RE.match(text)
    if full_match:
        dice_count = max(0, int(full_match.group(1) or 0))
        dice_size = max(1, int(full_match.group(2) or 1))
        mod = int(full_match.group(3) or 0)
        rolled = [rng.randint(1, dice_size) for _ in range(dice_count)]
        return sum(rolled) + mod, rolled, mod

    try:
        value = int(text)
        return value, [], value
    except Exception:
        return 0, [], 0

def parse_casting_time_meta(value: Any) -> Dict[str, Any]:
    raw = str(value or "").strip()
    low = raw.lower()
    is_reaction = low.startswith("1 reaction") or low == "reaction" or " as a reaction" in low
    trigger = ""
    if "when" in low:
        idx = low.find("when")
        trigger = raw[idx:].strip(" ,.;")
    elif ", which" in low:
        idx = low.find(",")
        trigger = raw[idx+1:].strip(" ,.;")
    return {"raw": raw, "is_reaction": bool(is_reaction), "trigger": trigger}

def resolve_spell_attack(attack_roll: int, attack_modifier: int, target_ac: int) -> Dict[str, int | bool]:
    roll = int(attack_roll or 0)
    mod = int(attack_modifier or 0)
    ac = int(target_ac or 0)
    total = roll + mod
    return {"hit": bool(total >= ac), "total": total, "roll": roll, "modifier": mod, "target_ac": ac}


def resolve_spell_save(save_roll: int, save_modifier: int, spell_dc: int) -> Dict[str, int | bool]:
    roll = int(save_roll or 0)
    mod = int(save_modifier or 0)
    dc = int(spell_dc or 0)
    total = roll + mod
    return {"success": bool(total >= dc), "total": total, "roll": roll, "modifier": mod, "spell_dc": dc}


def parse_damage_expr(expr: str) -> Tuple[int, int, int]:
    m = _DICE_RE.match(str(expr or "").strip())
    if not m:
        return 0, 0, 0
    return int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)


def format_damage_expr(dice_count: int, dice_size: int, modifier: int = 0) -> str:
    dice_count = max(0, int(dice_count or 0))
    dice_size = max(0, int(dice_size or 0))
    modifier = int(modifier or 0)
    if dice_count <= 0 or dice_size <= 0:
        return str(modifier) if modifier else ""
    if modifier > 0:
        return f"{dice_count}d{dice_size}+{modifier}"
    if modifier < 0:
        return f"{dice_count}d{dice_size}{modifier}"
    return f"{dice_count}d{dice_size}"


def build_upcast_damage_expr(base_expr: str, cast_level: int, base_level: int, upcast: Dict[str, Any] | None = None) -> str:
    count, size, mod = parse_damage_expr(base_expr)
    if count <= 0 or size <= 0:
        return str(base_expr or "").strip()
    cast_level = int(cast_level or 0)
    base_level = int(base_level or 0)
    extra_levels = max(0, cast_level - base_level)
    if extra_levels <= 0:
        return format_damage_expr(count, size, mod)

    upcast = dict(upcast or {})
    if upcast.get("expr"):
        extra_count, extra_size, extra_mod = parse_damage_expr(str(upcast.get("expr") or ""))
        if extra_count > 0 and extra_size == size:
            count += extra_count * extra_levels
            mod += extra_mod * extra_levels
            return format_damage_expr(count, size, mod)
    if int(upcast.get("per_level_dice", 0) or 0) > 0:
        count += int(upcast.get("per_level_dice", 0) or 0) * extra_levels
        return format_damage_expr(count, size, mod)
    if str(upcast.get("mode", "")).strip().lower() in {"plus_1_die", "plus_one_die", "default"}:
        count += extra_levels
        return format_damage_expr(count, size, mod)
    return format_damage_expr(count, size, mod)


def normalized_effects_from_spell(spell_row: Dict[str, Any] | None, *, cast_level: int | None = None) -> List[Dict[str, Any]]:
    row = dict(spell_row or {})
    effects = deepcopy(row.get("effects") or []) if isinstance(row.get("effects"), list) else []
    if effects:
        return [normalized_effect_payload(fx, spell_row=row) for fx in effects if isinstance(fx, dict)]

    damage = row.get("damage") if isinstance(row.get("damage"), dict) else {}
    expr = str(damage.get("expr") or row.get("damage_expr") or "").strip()
    if expr:
        base_level = int(row.get("level", 0) or 0)
        resolved_expr = build_upcast_damage_expr(expr, int(cast_level or base_level), base_level, damage.get("upcast") if isinstance(damage.get("upcast"), dict) else {})
        fx: Dict[str, Any] = {
            "type": "heal" if bool(damage.get("heal", False)) else "damage",
            "expr": resolved_expr,
            "damage_type": str(damage.get("type") or row.get("damage_type") or "").strip().lower(),
        }
        save_type = str(row.get("save_type") or "").strip().lower()
        if save_type:
            fx["save"] = {
                "ability": save_type,
                "on_success": str(damage.get("save_on_success") or row.get("save_on_success") or "half").strip().lower() or "half",
            }
        if bool(row.get("attack_roll", False)):
            fx["attack_roll"] = True
        effects.append(fx)
    return [normalized_effect_payload(fx, spell_row=row) for fx in effects if isinstance(fx, dict)]



def normalized_targeting(spell_row: Dict[str, Any] | None) -> Dict[str, Any]:
    row = dict(spell_row or {})
    raw = row.get("targeting") if isinstance(row.get("targeting"), dict) else {}
    tgt = deepcopy(raw) if isinstance(raw, dict) else {}
    tgt["kind"] = str(tgt.get("kind") or row.get("target_mode") or "").strip().lower()
    tgt["delivery"] = str(tgt.get("delivery") or "").strip().lower()
    tgt["affects"] = str(tgt.get("affects") or tgt.get("scope") or "").strip().lower()
    try:
        tgt["count"] = max(1, int(tgt.get("count") or 1))
    except Exception:
        tgt["count"] = 1
    if isinstance(tgt.get("template"), dict):
        tgt["template"] = deepcopy(tgt.get("template"))
    else:
        tgt["template"] = {}
    return tgt


def split_target_hints(value: Any) -> List[str]:
    if isinstance(value, list):
        out: List[str] = []
        for item in value:
            s = str(item or "").strip()
            if s:
                out.append(s)
        return out
    text = str(value or "").strip()
    if not text:
        return []
    parts = re.split(r"[,;\n]+", text)
    return [str(part or "").strip() for part in parts if str(part or "").strip()]

def parse_rounds_from_duration(duration: str | None) -> int | None:
    txt = str(duration or '').strip().lower()
    if not txt:
        return None
    m = re.match(r'^\s*(\d+)\s+rounds?\s*$', txt)
    if m:
        try:
            return max(0, int(m.group(1)))
        except Exception:
            return None
    if txt in {"instant", "instantaneous", "permanent", "special"}:
        return None
    return None
