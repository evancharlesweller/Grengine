# engine/combat_utils.py
from __future__ import annotations

import random
import re
from typing import Literal, Tuple

RollMode = Literal["normal", "adv", "dis"]

_DICE_PAT = re.compile(r"^\s*(\d+)\s*d\s*(\d+)\s*([+-]\s*\d+)?\s*$", re.IGNORECASE)


def choose_d20(rolls: list[int], mode: RollMode) -> int:
    """rolls may contain 1 or 2 values. If 2 and adv/dis, choose appropriately."""
    if not rolls:
        raise ValueError("No rolls provided")

    if len(rolls) == 1:
        return int(rolls[0])

    r1, r2 = int(rolls[0]), int(rolls[1])
    if mode == "adv":
        return max(r1, r2)
    if mode == "dis":
        return min(r1, r2)
    # normal with two rolls: default to first
    return r1


def roll_damage(damage_expr: str) -> Tuple[int, str]:
    """
    Supports: '1d8+3', '2d6', '1d4 + 1'
    Returns (total, breakdown_str).
    """
    m = _DICE_PAT.match(damage_expr or "")
    if not m:
        # If damage is stored as int, or malformed string
        try:
            val = int(damage_expr)
            return val, str(val)
        except Exception:
            return 1, "1"

    n = int(m.group(1))
    sides = int(m.group(2))
    mod = m.group(3)
    mod_val = int(mod.replace(" ", "")) if mod else 0

    rolls = [random.randint(1, sides) for _ in range(n)]
    total = sum(rolls) + mod_val

    # readable breakdown
    mod_txt = ""
    if mod_val > 0:
        mod_txt = f"+{mod_val}"
    elif mod_val < 0:
        mod_txt = f"{mod_val}"

    breakdown = f"{n}d{sides}{mod_txt} -> {rolls} {mod_txt}".strip()
    return total, breakdown


def resolve_attack(
    d20: int,
    attacker_mod: int,
    target_ac: int,
    weapon_attack_bonus: int = 0,
) -> Tuple[bool, int, bool, bool]:
    """
    Returns: (hit?, total_attack_roll, is_nat20, is_nat1)
    D&D5e: nat20 = auto-hit, nat1 = auto-miss
    """
    d20 = int(d20)
    attacker_mod = int(attacker_mod)
    target_ac = int(target_ac)
    weapon_attack_bonus = int(weapon_attack_bonus)

    is_nat20 = (d20 == 20)
    is_nat1 = (d20 == 1)

    total = d20 + attacker_mod + weapon_attack_bonus

    if is_nat20:
        return True, total, True, False
    if is_nat1:
        return False, total, False, True

    return (total >= target_ac), total, False, False


def roll_damage_crit(damage_expr: str) -> Tuple[int, str]:
    """
    Crit rule (5e): double the number of dice, keep modifier once.
    Example: 1d8+3 -> 2d8+3
    """
    m = _DICE_PAT.match(damage_expr or "")
    if not m:
        return roll_damage(damage_expr)

    n = int(m.group(1))
    sides = int(m.group(2))
    mod = m.group(3)
    mod_val = int(mod.replace(" ", "")) if mod else 0

    n2 = n * 2
    rolls = [random.randint(1, sides) for _ in range(n2)]
    total = sum(rolls) + mod_val

    mod_txt = ""
    if mod_val > 0:
        mod_txt = f"+{mod_val}"
    elif mod_val < 0:
        mod_txt = f"{mod_val}"

    breakdown = f"CRIT {n2}d{sides}{mod_txt} -> {rolls} {mod_txt}".strip()
    return total, breakdown
