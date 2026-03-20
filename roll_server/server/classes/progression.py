from typing import Any, Dict, Set

SUBCLASS_UNLOCK_LEVELS = {
    "barbarian": 3,
    "bard": 3,
    "cleric": 1,
    "druid": 2,
    "fighter": 3,
    "monk": 3,
    "paladin": 3,
    "ranger": 3,
    "rogue": 3,
    "sorcerer": 1,
    "warlock": 1,
    "wizard": 2,
}

ASI_LEVELS = {
    "fighter": {4, 6, 8, 12, 14, 16, 19},
    "rogue": {4, 8, 10, 12, 16, 19},
}
DEFAULT_ASI_LEVELS = {4, 8, 12, 16, 19}

def asi_levels_for_class(class_key: str) -> Set[int]:
    return set(ASI_LEVELS.get(str(class_key or '').strip().lower(), DEFAULT_ASI_LEVELS))

def subclass_unlock_level(class_key: str) -> int:
    return int(SUBCLASS_UNLOCK_LEVELS.get(str(class_key or '').strip().lower(), 0) or 0)

def class_needs_subclass_choice(class_key: str, target_level: int, sheet: Dict[str, Any]) -> bool:
    ck = str(class_key or '').strip().lower()
    unlock = subclass_unlock_level(ck)
    if unlock <= 0 or int(target_level or 0) < unlock:
        return False
    subclasses = sheet.get('subclasses') if isinstance(sheet.get('subclasses'), dict) else {}
    return not bool(str(subclasses.get(ck) or '').strip())
