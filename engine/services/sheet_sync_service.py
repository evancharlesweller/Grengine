# engine/services/sheet_sync_service.py
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
from engine.trait_engine import apply_passives_to_combat_view

# No Qt imports here. Pure orchestration + deterministic mapping.
# HTTP is performed by the provided ServerClient instance (net/server_client.py).

def _safe_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default

def _sheet_updated_at(sheet: Dict[str, Any]) -> int:
    if not isinstance(sheet, dict):
        return 0
    return _safe_int(sheet.get("updated_at", 0), 0)

def _extract_combat_view(sheet: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize a character sheet into combat-relevant fields.

    Keep this in lockstep with ui.main_window.get_sheet_combat_view() so the
    background sync timer sees the same equipped weapon/armor that manual token
    hydration sees. This avoids DM/player overlay drift after portal equipment
    swaps.
    """
    if not isinstance(sheet, dict):
        return {}

    base = sheet.get("base_stats", {}) or {}
    res = sheet.get("resources", {}) or {}
    stats = sheet.get("stats", {}) or {}
    equipment = sheet.get("equipment", {}) or {}
    combat = sheet.get("combat", {}) or {}
    eq = sheet.get("equipped", {}) or {}

    eq_weapon = str(
        eq.get("weapon_id", "")
        or eq.get("weapon", "")
        or equipment.get("primary_weapon_id", "")
        or equipment.get("primary_weapon", "")
        or equipment.get("weapon_id", "")
        or equipment.get("weapon", "")
        or ""
    ).strip()
    eq_armor = str(
        eq.get("armor_id", "")
        or eq.get("armor", "")
        or equipment.get("armor_id", "")
        or equipment.get("armor", "")
        or ""
    ).strip()

    bs_weapon = str(
        base.get("weapon_id", "")
        or base.get("weapon", "")
        or stats.get("weapon_id", "")
        or stats.get("weapon", "")
        or combat.get("weapon_ref", "")
        or ""
    ).strip()
    bs_armor = str(
        base.get("armor_id", "")
        or base.get("armor", "")
        or stats.get("armor_id", "")
        or stats.get("armor", "")
        or ""
    ).strip()

    weapon_id = eq_weapon or bs_weapon
    armor_id = eq_armor or bs_armor

    max_hp = _safe_int(stats.get("max_hp", base.get("max_hp", 10)), 10)
    current_hp = stats.get("current_hp", None)
    if current_hp is None:
        current_hp = res.get("current_hp", None)
    if current_hp is None:
        current_hp = res.get("hp", None)
    if current_hp is None:
        current_hp = max_hp

    defense = _safe_int(
        combat.get("ac", None) if isinstance(combat, dict) else None,
        _safe_int(stats.get("defense", stats.get("ac", base.get("ac", 10))), 10),
    )
    movement_ft = _safe_int(stats.get("movement_ft", stats.get("movement", base.get("movement_ft", base.get("movement", 30)))), 30)
    attack_mod = _safe_int(
        combat.get("attack_modifier", None) if isinstance(combat, dict) else None,
        _safe_int(stats.get("attack_modifier", base.get("attack_modifier", 0)), 0),
    )
    vision_ft = _safe_int(stats.get("vision_ft", base.get("vision_ft", 60)), 60)

    view = {
        "max_hp": max_hp,
        "current_hp": _safe_int(current_hp, max_hp),
        "defense": defense,
        "ac": defense,
        "movement_ft": movement_ft,
        "attack_modifier": attack_mod,
        "vision_ft": vision_ft,
        "weapon_id": weapon_id,
        "armor_id": armor_id,
    }
    return apply_passives_to_combat_view(sheet, view)

def sync_sheet_backed_tokens(
    state: Any,
    server_client: Any,
    *,
    only_if_changed: bool = True,
    clamp_hp: bool = True,
) -> Tuple[int, int]:
    """
    Pulls authoritative character sheets from the server and hydrates any sheet-backed
    tokens in the current EncounterState.

    Returns: (tokens_considered, tokens_updated)

    Criteria for "sheet-backed":
      - token.player_id is non-empty AND token.character_id is non-empty
      - token.kind == "pc" OR token.side == "player" (fallback)

    This is UI-safe to call from a QTimer tick (no Qt usage) and will never raise.
    """
    try:
        tokens = getattr(state, "tokens", None)
        if not isinstance(tokens, dict) or not tokens:
            return (0, 0)

        considered = 0
        updated = 0

        for ts in list(tokens.values()):
            try:
                player_id = (getattr(ts, "player_id", "") or "").strip()
                character_id = (getattr(ts, "character_id", "") or "").strip()
                kind = (getattr(ts, "kind", "") or "").strip().lower()
                side = (getattr(ts, "side", "") or "").strip().lower()

                if not player_id or not character_id:
                    continue
                if kind not in ("pc", "player") and side != "player":
                    continue

                considered += 1

                sheet = server_client.get_character_sheet(character_id)
                if not isinstance(sheet, dict) or not sheet:
                    continue

                sheet_u = _sheet_updated_at(sheet)
                last_u = getattr(ts, "_last_sheet_updated_at", 0) or 0
                if only_if_changed and sheet_u and last_u and sheet_u <= last_u:
                    continue

                cv = _extract_combat_view(sheet)
                if not cv:
                    continue

                # Apply to token
                max_hp = int(cv["max_hp"])
                cur_hp = int(cv["current_hp"])
                if clamp_hp:
                    cur_hp = max(0, min(cur_hp, max_hp))

                setattr(ts, "max_hp", max_hp)
                setattr(ts, "hp", cur_hp)
                setattr(ts, "ac", int(cv["defense"]))

                new_move = int(cv["movement_ft"])
                setattr(ts, "movement", new_move)
                setattr(ts, "base_movement", new_move)

                # Clamp remaining if already initialized
                try:
                    mr = getattr(ts, "movement_remaining", None)
                    if mr is not None:
                        setattr(ts, "movement_remaining", max(0, min(int(mr), new_move)))
                except Exception:
                    pass

                setattr(ts, "attack_modifier", int(cv["attack_modifier"]))
                setattr(ts, "vision_ft", int(cv["vision_ft"]))
                setattr(ts, "darkvision_ft", int(cv.get("darkvision_ft", getattr(ts, "darkvision_ft", 0)) or 0))
                setattr(ts, "weapon_id", str(cv["weapon_id"] or ""))
                setattr(ts, "armor_id", str(cv["armor_id"] or ""))
                setattr(ts, "weapon", str(cv["weapon_id"] or ""))
                setattr(ts, "armor", str(cv["armor_id"] or ""))
                if cv.get("damage_profile"):
                    setattr(ts, "damage_profile", dict(cv.get("damage_profile") or {}))
                    setattr(ts, "damage_resistances", list((cv.get("damage_profile") or {}).get("resistances", []) or []))
                    setattr(ts, "damage_immunities", list((cv.get("damage_profile") or {}).get("immunities", []) or []))
                    setattr(ts, "damage_vulnerabilities", list((cv.get("damage_profile") or {}).get("vulnerabilities", []) or []))
                if cv.get("save_bonus"):
                    setattr(ts, "save_bonus", dict(cv.get("save_bonus") or {}))
                if cv.get("ignore_difficult_terrain"):
                    setattr(ts, "ignore_difficult_terrain", True)

                setattr(ts, "_last_sheet_updated_at", sheet_u or 0)
                updated += 1
            except Exception:
                continue

        return (considered, updated)
    except Exception:
        return (0, 0)
