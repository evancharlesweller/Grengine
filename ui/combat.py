# ui/combat.py
"""
Compatibility shim.

Combat resolution helpers were migrated to engine/ for Phase 5.
Keep this module so older imports (from ui.combat import ...) continue working.
"""

from engine.combat_models import PendingAttack, RollMode
from engine.combat_engine import choose_d20, roll_damage, roll_damage_crit, resolve_attack

__all__ = [
    "PendingAttack",
    "RollMode",
    "choose_d20",
    "roll_damage",
    "roll_damage_crit",
    "resolve_attack",
]
