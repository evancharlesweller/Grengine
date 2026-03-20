# ui/rules/registry.py
from __future__ import annotations

from typing import Dict, Type

from .base import BaseRules
from .default_rules import DefaultRules


_RULESETS: Dict[str, Type[BaseRules]] = {
    "default": DefaultRules,
    # You can alias campaign config values here without creating new classes yet:
    "post_apoc_v1": DefaultRules,
    "fantasy_v1": DefaultRules,
}


class RulesRegistry:
    @staticmethod
    def load(ruleset_id: str) -> BaseRules:
        rid = str(ruleset_id or "default").strip() or "default"
        cls = _RULESETS.get(rid, DefaultRules)
        return cls()
