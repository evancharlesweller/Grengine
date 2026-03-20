# ui/item_lookup.py
from typing import Dict
from ui.items_db import load_items, index_items

DEFAULT_UNARMED = {
    "item_id": "unarmed",
    "name": "Unarmed",
    "damage": "1d4",
    "attack_bonus": 0,
    "type": "melee",
    "range": 1,
    "range_ft": 5
}

def load_item_index(campaign_path: str) -> Dict[str, Dict[str, Dict[str, Dict]]]:
    items = load_items(campaign_path)
    return index_items(items)

def _slug_to_title(s: str) -> str:
    # "pipe_rifle" -> "Pipe Rifle"
    parts = [p for p in s.replace("-", "_").split("_") if p]
    return " ".join([p.capitalize() for p in parts])

def get_weapon(campaign_path: str, weapon_ref: str) -> dict:
    """
    weapon_ref can be:
    - item_id (preferred)
    - exact name (legacy)
    - slug (pipe_rifle -> Pipe Rifle)
    If missing/unknown, defaults to Unarmed.
    """
    weapon_ref = (weapon_ref or "").strip()
    idx = load_item_index(campaign_path).get("weapons", {})
    by_id = idx.get("by_id", {})
    by_name = idx.get("by_name", {})

    if not weapon_ref:
        return DEFAULT_UNARMED

    # 1) exact id
    w = by_id.get(weapon_ref)
    if w:
        return w

    # 2) exact name
    w = by_name.get(weapon_ref)
    if w:
        return w

    # 3) slug -> title-case name
    if "_" in weapon_ref or "-" in weapon_ref:
        title = _slug_to_title(weapon_ref)
        w = by_name.get(title)
        if w:
            return w

    return DEFAULT_UNARMED

def get_weapon_or_unarmed(campaign_path: str, weapon_ref: str) -> dict:
    """
    Same as get_weapon, but makes the fallback explicit in the name.
    Kept for compatibility with main_window imports.
    """
    return get_weapon(campaign_path, weapon_ref)
