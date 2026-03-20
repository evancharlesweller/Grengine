# ui/rules/base.py
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable, Optional, Any


RollMode = str  # "NORMAL" | "ADV" | "DIS"


class BaseRules(ABC):
    """
    Rules adapter seam.
    The engine calls this instead of hardcoding D&D 5e logic.

    For Step 5.0.1 we keep this minimal and non-invasive.
    """

    ruleset_id: str = "base"

    # --- Core numeric helpers (not yet used by game logic in this step) ---

    @abstractmethod
    def ability_mod(self, score: int) -> int:
        raise NotImplementedError

    # Optional: later you’ll implement save_mod/skill_mod here.
    # def save_mod(self, actor, stat_key: str) -> int: ...

    # --- Roll mode constraints (conditions, etc.) ---
    def roll_mode_for_attack(
        self,
        attacker_conditions: Optional[Iterable[str]] = None,
        target_conditions: Optional[Iterable[str]] = None,
    ) -> RollMode:
        """
        Default: no forced mode.
        Later: blinded, stunned, etc. influence ADV/DIS.
        """
        return "NORMAL"
    
    def save_mod(self, actor: Any, stat_key: str) -> int:
        raise NotImplementedError
