# ui/items_db.py
import os
import json
import uuid
from typing import Dict, Any, Optional

ITEMS_FILENAME = "items.json"

DEFAULT_ITEMS: Dict[str, list] = {
    "weapons": [],
    "armors": [],
    "health_items": [],
    "misc_items": [],
}

# For now, all categories use "item_id"
ID_FIELDS = {
    "weapons": "item_id",
    "armors": "item_id",
    "health_items": "item_id",
    "misc_items": "item_id",
}


def _items_path(campaign_path: str) -> str:
    return os.path.join(campaign_path, ITEMS_FILENAME)


def save_items(campaign_path: str, data: Dict[str, Any]) -> None:
    path = _items_path(campaign_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_items(campaign_path: str) -> Dict[str, Any]:
    """
    Loads items.json and ensures:
    - keys exist (weapons/armors/health_items/misc_items)
    - each item is a dict
    - each item has item_id (uuid hex)
    Auto-saves if it had to repair/upgrade the file.
    """
    path = _items_path(campaign_path)

    if not os.path.exists(path):
        data = dict(DEFAULT_ITEMS)
        save_items(campaign_path, data)
        return data

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        data = dict(DEFAULT_ITEMS)
        save_items(campaign_path, data)
        return data

    changed = False

    # Ensure top-level lists exist
    for k, default_list in DEFAULT_ITEMS.items():
        if k not in data or not isinstance(data.get(k), list):
            data[k] = list(default_list)
            changed = True

    # Ensure each item has item_id
    for cat, id_key in ID_FIELDS.items():
        new_list = []
        for it in data.get(cat, []):
            if not isinstance(it, dict):
                changed = True
                continue
            if not it.get(id_key):
                it[id_key] = uuid.uuid4().hex
                changed = True
            new_list.append(it)
        data[cat] = new_list

    if changed:
        save_items(campaign_path, data)

    return data


def ensure_items_db(campaign_path: str) -> None:
    """
    Ensures campaign/items.json exists and is upgraded.
    Safe to call during campaign creation.
    """
    load_items(campaign_path)


def index_items(items: Dict[str, Any]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """
    Returns:
    {
      "weapons": {"by_id": {...}, "by_name": {...}},
      ...
    }
    """
    out: Dict[str, Dict[str, Dict[str, Any]]] = {}
    for cat, id_key in ID_FIELDS.items():
        by_id: Dict[str, Dict[str, Any]] = {}
        by_name: Dict[str, Dict[str, Any]] = {}
        for it in items.get(cat, []) or []:
            if not isinstance(it, dict):
                continue
            iid = str(it.get(id_key, "")).strip()
            name = str(it.get("name", "")).strip()
            if iid:
                by_id[iid] = it
            if name:
                by_name[name] = it
        out[cat] = {"by_id": by_id, "by_name": by_name}
    return out


def resolve_item(
    campaign_path: str,
    ref: str,
    category: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    Resolve an item by item_id OR name.

    - If category is provided, only searches that category.
    - If category is None, searches all categories and returns the first match.
    """
    if not ref:
        return None

    items = load_items(campaign_path)
    idx = index_items(items)

    if category:
        cat_idx = idx.get(category, {})
        return cat_idx.get("by_id", {}).get(ref) or cat_idx.get("by_name", {}).get(ref)

    # Search all categories
    for cat in ["weapons", "armors", "health_items", "misc_items"]:
        cat_idx = idx.get(cat, {})
        hit = cat_idx.get("by_id", {}).get(ref) or cat_idx.get("by_name", {}).get(ref)
        if hit:
            return hit

    return None