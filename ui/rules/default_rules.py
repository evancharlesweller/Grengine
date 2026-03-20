# ui/rules/default_rules.py
from __future__ import annotations

from .base import BaseRules


class DefaultRules(BaseRules):
    ruleset_id = "default"

    def ability_mod(self, score: int) -> int:
        # Generic “(score-10)//2” style modifier (5e-shaped, but kept inside rules).
        try:
            s = int(score)
        except Exception:
            s = 10
        return (s - 10) // 2
    
    def save_mod(self, actor, stat_key: str) -> int:
        key = str(stat_key or "").upper().strip()
        abilities = getattr(actor, "abilities", {}) or {}
        score = int(abilities.get(key, 10) or 10)
        base = self.ability_mod(score)

        prof_bonus = int(getattr(actor, "proficiency_bonus", 0) or 0)
        save_profs = set((getattr(actor, "save_proficiencies", None) or []))
        if key in save_profs:
            base += prof_bonus
        return int(base)
