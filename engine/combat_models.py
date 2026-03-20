from __future__ import annotations

from dataclasses import dataclass

from typing import Literal

RollMode = Literal["normal", "adv", "dis"]


@dataclass(frozen=True)
class SheetCombatView:
    """Normalized combat-relevant slice of a character sheet."""

    current_hp: int
    max_hp: int
    ac: int
    attack_modifier: int
    weapon_ref: str
    damage_expr: str

@dataclass(frozen=True)
class NpcAttackOutcome:
    """Result of resolving an NPC attack."""

    d20: int
    total_to_hit: int
    target_ac: int
    is_hit: bool
    damage_roll_expr: str
    damage_total: int

@dataclass(frozen=True)
class PendingAttack:
    player_id: str
    attacker_token_id: str
    target_token_id: str
    weapon_name: str
    roll_mode: RollMode = "normal"