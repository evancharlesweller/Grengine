from __future__ import annotations
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple
from .spell_resolution import normalized_effects_from_spell, parse_casting_time_meta
DEFAULT_SLOT_REFRESH = "long_rest"
_DEFAULT_SCHOOL = ""
_DEFAULT_CASTING_TIME = "1 action"
_DEFAULT_DURATION = "instantaneous"
def _as_int(value: Any, default: int = 0, minimum: int | None = None) -> int:
    try:
        out = int(value)
    except Exception:
        out = int(default)
    if minimum is not None:
        out = max(int(minimum), out)
    return out
def _as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        low = value.strip().lower()
        if low in {"1", "true", "yes", "y", "on"}:
            return True
        if low in {"0", "false", "no", "n", "off"}:
            return False
    return bool(value) if value is not None else bool(default)
def _as_list_of_str(value: Any) -> List[str]:
    if isinstance(value, list):
        out = []
        for item in value:
            s = str(item or "").strip()
            if s:
                out.append(s)
        return out
    if isinstance(value, str):
        parts = [p.strip() for p in value.replace("\n", ",").split(",")]
        return [p for p in parts if p]
    return []
def _coerce_targeting(raw: Any) -> Dict[str, Any]:
    if isinstance(raw, str):
        kind = raw.strip().lower()
        return {"kind": kind} if kind else {"kind": ""}
    if isinstance(raw, dict):
        targeting = deepcopy(raw)
        targeting["kind"] = str(targeting.get("kind", "") or "").strip().lower()
        if isinstance(targeting.get("template"), dict):
            targeting["template"] = deepcopy(targeting["template"])
        return targeting
    return {"kind": ""}
def normalize_spell_entry(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    spell_id = str(raw.get("spell_id") or raw.get("id") or "").strip()
    if not spell_id:
        return {}
    target_mode = _coerce_targeting(raw.get("targeting"))
    range_ft = None
    if isinstance(raw.get("cast"), dict):
        range_ft = raw.get("cast", {}).get("range_ft")
    if range_ft is None:
        range_ft = raw.get("range_ft", raw.get("range", 0))
    try:
        range_ft_int = int(range_ft or 0)
    except Exception:
        range_ft_int = 0
    damage = raw.get("damage")
    if isinstance(damage, dict):
        damage_block = {
            "expr": str(damage.get("expr", raw.get("damage_expr", "")) or "").strip(),
            "type": str(damage.get("type", raw.get("damage_type", "")) or "").strip().lower(),
            "heal": _as_bool(damage.get("heal"), False),
            "upcast": deepcopy(damage.get("upcast", {})) if isinstance(damage.get("upcast"), dict) else {},
        }
    else:
        damage_block = {
            "expr": str(raw.get("damage_expr", "") or "").strip(),
            "type": str(raw.get("damage_type", "") or "").strip().lower(),
            "heal": False,
            "upcast": {},
        }
    normalized = deepcopy(raw)
    normalized["spell_id"] = spell_id
    normalized.setdefault("name", spell_id.replace("_", " ").title())
    normalized["name"] = str(normalized.get("name") or spell_id).strip()
    normalized["school"] = str(raw.get("school", _DEFAULT_SCHOOL) or "").strip()
    normalized["level"] = _as_int(raw.get("level", 0), 0, 0)
    normalized["casting_time"] = str(raw.get("casting_time") or raw.get("cast_time") or _DEFAULT_CASTING_TIME).strip()
    cast_meta = parse_casting_time_meta(normalized["casting_time"])
    normalized["reaction"] = bool(_as_bool(raw.get("reaction"), cast_meta.get("is_reaction", False)))
    normalized["reaction_trigger"] = str(raw.get("reaction_trigger") or cast_meta.get("trigger") or "").strip()
    normalized["range_ft"] = max(0, range_ft_int)
    normalized["range"] = raw.get("range", normalized["range_ft"])
    normalized["targeting"] = target_mode
    normalized["target_mode"] = str(target_mode.get("kind", "") or "").strip().lower()
    normalized["save_type"] = str(raw.get("save_type") or raw.get("save") or "").strip().lower()
    normalized["attack_roll"] = _as_bool(raw.get("attack_roll"), normalized["target_mode"] in {"attack", "attack_roll"})
    normalized["duration"] = str(raw.get("duration") or _DEFAULT_DURATION).strip()
    normalized["concentration"] = _as_bool(raw.get("concentration"), False)
    normalized["ritual"] = _as_bool(raw.get("ritual"), False)
    normalized["tags"] = _as_list_of_str(raw.get("tags"))
    normalized["effects"] = deepcopy(raw.get("effects", [])) if isinstance(raw.get("effects"), list) else []
    normalized["damage"] = damage_block
    normalized.setdefault("cast", {})
    if not isinstance(normalized["cast"], dict):
        normalized["cast"] = {}
    normalized["cast"]["range_ft"] = normalized["range_ft"]
    if normalized.get("description") is None:
        normalized["description"] = ""
    if normalized.get("reaction_window") is None:
        normalized["reaction_window"] = "incoming" if normalized.get("reaction") else ""
    return normalized
def normalize_spells_payload(data: Any) -> Dict[str, Dict[str, Any]]:
    spells: Iterable[Any]
    if isinstance(data, dict):
        spells = data.get("spells", [])
    elif isinstance(data, list):
        spells = data
    else:
        spells = []
    out: Dict[str, Dict[str, Any]] = {}
    for entry in spells or []:
        normalized = normalize_spell_entry(entry)
        if normalized:
            out[normalized["spell_id"]] = normalized
    return out
def get_spell_effects(spell_row: Dict[str, Any], *, cast_level: int | None = None) -> List[Dict[str, Any]]:
    return normalized_effects_from_spell(spell_row, cast_level=cast_level)

def start_concentration(sheet: Dict[str, Any], *, spell_id: str, source: str = "", rounds_remaining: int | None = None) -> Dict[str, Any]:
    sc = ensure_spellcasting_foundation(sheet).get("spellcasting", {})
    conc = sc.setdefault("concentration", {}) if isinstance(sc.get("concentration"), dict) else {}
    sc["concentration"] = conc
    conc["active"] = bool(spell_id)
    conc["spell_id"] = str(spell_id or "")
    conc["source"] = str(source or "")
    conc["rounds_remaining"] = rounds_remaining
    return conc

def clear_concentration(sheet: Dict[str, Any], *, reason: str = "") -> Dict[str, Any]:
    sc = ensure_spellcasting_foundation(sheet).get("spellcasting", {})
    conc = sc.setdefault("concentration", {}) if isinstance(sc.get("concentration"), dict) else {}
    sc["concentration"] = conc
    previous = dict(conc)
    conc["active"] = False
    conc["spell_id"] = ""
    conc["source"] = str(reason or "")
    conc["rounds_remaining"] = None
    return previous

def load_spells_db(path: str | Path) -> Dict[str, Dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return normalize_spells_payload(data)
def ensure_spellcasting_foundation(sheet: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(sheet, dict):
        sheet = {}
    sc = sheet.setdefault("spellcasting", {})
    if not isinstance(sc, dict):
        sc = {}
        sheet["spellcasting"] = sc
    sc.setdefault("class", "")
    sc.setdefault("ability", "")
    sc.setdefault("save_dc", 0)
    sc.setdefault("attack_bonus", 0)
    sc.setdefault("cantrips", "")
    sc.setdefault("slot_refresh", DEFAULT_SLOT_REFRESH)
    sc.setdefault("known_mode", "")
    sc.setdefault("concentration", {"active": False, "spell_id": "", "source": ""})
    if not isinstance(sc.get("concentration"), dict):
        sc["concentration"] = {"active": False, "spell_id": "", "source": ""}
    sc.setdefault("known_spells", [])
    if not isinstance(sc.get("known_spells"), list):
        sc["known_spells"] = _as_list_of_str(sc.get("known_spells"))
    sc.setdefault("prepared_spells", [])
    if not isinstance(sc.get("prepared_spells"), list):
        sc["prepared_spells"] = _as_list_of_str(sc.get("prepared_spells"))
    sc.setdefault("spellbook_spells", [])
    if not isinstance(sc.get("spellbook_spells"), list):
        sc["spellbook_spells"] = _as_list_of_str(sc.get("spellbook_spells"))
    sc.setdefault("spells", {})
    if not isinstance(sc.get("spells"), dict):
        sc["spells"] = {}
    for lvl in range(1, 10):
        key = str(lvl)
        row = sc["spells"].setdefault(key, {})
        if not isinstance(row, dict):
            row = {}
            sc["spells"][key] = row
        total = _as_int(row.get("total", 0), 0, 0)
        used = _as_int(row.get("used", 0), 0, 0)
        row["total"] = total
        row["used"] = min(max(used, 0), total if total >= 0 else used)
        row.setdefault("list", "")
        row["remaining"] = max(0, total - row["used"])
        row.setdefault("refresh", sc.get("slot_refresh", DEFAULT_SLOT_REFRESH))
    # root mirrors for compatibility with older DM code
    sheet.setdefault("known_spells", list(sc.get("known_spells") or []))
    if not isinstance(sheet.get("known_spells"), list):
        sheet["known_spells"] = list(sc.get("known_spells") or [])
    sheet.setdefault("prepared_spells", list(sc.get("prepared_spells") or []))
    if not isinstance(sheet.get("prepared_spells"), list):
        sheet["prepared_spells"] = list(sc.get("prepared_spells") or [])
    sheet.setdefault("spellbook_spells", list(sc.get("spellbook_spells") or []))
    if not isinstance(sheet.get("spellbook_spells"), list):
        sheet["spellbook_spells"] = list(sc.get("spellbook_spells") or [])
    return sheet
def refresh_spell_slots(sheet: Dict[str, Any], refresh_type: str = DEFAULT_SLOT_REFRESH) -> Dict[str, Dict[str, Any]]:
    sc = ensure_spellcasting_foundation(sheet).get("spellcasting", {})
    refresh_type = str(refresh_type or DEFAULT_SLOT_REFRESH).strip().lower()
    if refresh_type not in {"short_rest", "long_rest"}:
        return sc.get("spells", {})
    for key, row in (sc.get("spells", {}) or {}).items():
        if not isinstance(row, dict):
            continue
        row_refresh = str(row.get("refresh") or sc.get("slot_refresh") or DEFAULT_SLOT_REFRESH).strip().lower()
        if row_refresh == "short_rest" and refresh_type in {"short_rest", "long_rest"}:
            row["used"] = 0
        elif row_refresh == "long_rest" and refresh_type == "long_rest":
            row["used"] = 0
        row["remaining"] = max(0, _as_int(row.get("total", 0), 0, 0) - _as_int(row.get("used", 0), 0, 0))
    return sc.get("spells", {})
def can_consume_spell_slot(sheet: Dict[str, Any], slot_level: int, count: int = 1) -> Tuple[bool, str, Dict[str, Any]]:
    sc = ensure_spellcasting_foundation(sheet).get("spellcasting", {})
    level_key = str(_as_int(slot_level, 0, 0))
    count = max(1, _as_int(count, 1, 1))
    row = (sc.get("spells", {}) or {}).get(level_key)
    if level_key == "0":
        return True, "cantrip", {"level": 0, "total": 0, "used": 0, "remaining": 999}
    if not isinstance(row, dict):
        return False, f"No slot row for level {level_key}", {}
    total = _as_int(row.get("total", 0), 0, 0)
    used = _as_int(row.get("used", 0), 0, 0)
    remaining = max(0, total - used)
    if total <= 0:
        return False, f"No level {level_key} spell slots available", {"level": int(level_key), "total": total, "used": used, "remaining": remaining}
    if remaining < count:
        return False, f"Not enough level {level_key} spell slots", {"level": int(level_key), "total": total, "used": used, "remaining": remaining}
    return True, "ok", {"level": int(level_key), "total": total, "used": used, "remaining": remaining}
def consume_spell_slot(sheet: Dict[str, Any], slot_level: int, count: int = 1) -> Tuple[bool, str, Dict[str, Any]]:
    ok, msg, state = can_consume_spell_slot(sheet, slot_level, count=count)
    if not ok:
        return False, msg, state
    if int(slot_level or 0) <= 0:
        return True, msg, state
    sc = ensure_spellcasting_foundation(sheet).get("spellcasting", {})
    row = sc.get("spells", {}).get(str(int(slot_level))) or {}
    row["used"] = min(_as_int(row.get("total", 0), 0, 0), _as_int(row.get("used", 0), 0, 0) + max(1, int(count or 1)))
    row["remaining"] = max(0, _as_int(row.get("total", 0), 0, 0) - _as_int(row.get("used", 0), 0, 0))
    return True, "consumed", {
        "level": int(slot_level),
        "total": _as_int(row.get("total", 0), 0, 0),
        "used": _as_int(row.get("used", 0), 0, 0),
        "remaining": _as_int(row.get("remaining", 0), 0, 0),
    }
