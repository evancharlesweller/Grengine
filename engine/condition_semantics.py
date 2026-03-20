from __future__ import annotations

from typing import Any, Dict, Iterable, List, Set


def _norm(value: str) -> str:
    return str(value or "").strip().lower()


def active_condition_names(actor: Any) -> Set[str]:
    out: Set[str] = set()
    for raw in list(getattr(actor, "statuses", []) or []):
        if not isinstance(raw, dict):
            continue
        name = _norm(raw.get("name") or raw.get("condition") or raw.get("condition_name"))
        if name:
            out.add(name)
    return out


def merge_roll_modes(*modes: str) -> str:
    seen_adv = False
    seen_dis = False
    for mode in modes:
        m = _norm(mode)
        if m in {"adv", "advantage"}:
            seen_adv = True
        elif m in {"dis", "disadvantage"}:
            seen_dis = True
    if seen_adv and seen_dis:
        return "normal"
    if seen_adv:
        return "advantage"
    if seen_dis:
        return "disadvantage"
    return "normal"


def _weapon_tags(weapon_data: Dict[str, Any] | None) -> Set[str]:
    wd = weapon_data if isinstance(weapon_data, dict) else {}
    tags = set()
    raw_tags = wd.get("tags") or []
    if isinstance(raw_tags, (list, tuple, set)):
        for t in raw_tags:
            nt = _norm(str(t))
            if nt:
                tags.add(nt)
    weapon_type = _norm(wd.get("weapon_type") or wd.get("type") or "")
    if weapon_type:
        tags.add(weapon_type)
    if wd.get("ranged") is True:
        tags.add("ranged")
    if wd.get("melee") is True:
        tags.add("melee")
    return tags


def _is_ranged_attack(weapon_data: Dict[str, Any] | None, attack_kind: str = "weapon") -> bool:
    tags = _weapon_tags(weapon_data)
    if "ranged" in tags:
        return True
    if "melee" in tags:
        return False
    return _norm(attack_kind) == "spell" and "melee" not in tags


def _is_melee_attack(weapon_data: Dict[str, Any] | None, attack_kind: str = "weapon") -> bool:
    return not _is_ranged_attack(weapon_data, attack_kind=attack_kind)


def attack_mode_from_conditions(attacker: Any, target: Any, weapon_data: Dict[str, Any] | None = None, attack_kind: str = "weapon") -> Dict[str, Any]:
    atk = active_condition_names(attacker)
    tgt = active_condition_names(target)
    forced_modes: List[str] = []
    reasons: List[str] = []

    try:
        atk_x = int(getattr(attacker, "grid_x", 0) or 0)
        atk_y = int(getattr(attacker, "grid_y", 0) or 0)
        tgt_x = int(getattr(target, "grid_x", 0) or 0)
        tgt_y = int(getattr(target, "grid_y", 0) or 0)
        dist_cells = max(abs(atk_x - tgt_x), abs(atk_y - tgt_y))
        blindsense_ok = bool(int(getattr(attacker, "blindsense_ft", 0) or 0) >= (dist_cells * 5)) if dist_cells >= 0 else False
    except Exception:
        blindsense_ok = False

    if "poisoned" in atk:
        forced_modes.append("disadvantage")
        reasons.append("attacker poisoned")
    if "blinded" in atk:
        forced_modes.append("disadvantage")
        reasons.append("attacker blinded")
    if "restrained" in atk:
        forced_modes.append("disadvantage")
        reasons.append("attacker restrained")
    if "stunned" in atk:
        forced_modes.append("disadvantage")
        reasons.append("attacker stunned")
    if "prone" in atk:
        forced_modes.append("disadvantage")
        reasons.append("attacker prone")

    if "blinded" in tgt:
        forced_modes.append("advantage")
        reasons.append("target blinded")
    if "restrained" in tgt:
        forced_modes.append("advantage")
        reasons.append("target restrained")
    if "stunned" in tgt:
        forced_modes.append("advantage")
        reasons.append("target stunned")

    if "prone" in tgt:
        if _is_ranged_attack(weapon_data, attack_kind=attack_kind):
            forced_modes.append("disadvantage")
            reasons.append("target prone vs ranged")
        else:
            forced_modes.append("advantage")
            reasons.append("target prone vs melee")

    if bool(getattr(attacker, "reckless_attack_active", False)) and _is_melee_attack(weapon_data, attack_kind=attack_kind):
        forced_modes.append("advantage")
        reasons.append("reckless attack")

    if bool(getattr(target, "reckless_attack_active", False)):
        forced_modes.append("advantage")
        reasons.append("target reckless")

    if bool(getattr(target, "patient_defense_active", False)):
        forced_modes.append("disadvantage")
        reasons.append("target patient defense")

    if bool(getattr(attacker, "empty_body_active", False)):
        forced_modes.append("advantage")
        reasons.append("attacker invisible/empty body")
    if bool(getattr(target, "empty_body_active", False)):
        forced_modes.append("disadvantage")
        reasons.append("target invisible/empty body")

    # Broad blindsense handling: within blindsense range, do not let unseen/invisible style
    # conditions impose attack disadvantage against the sensed target.
    if blindsense_ok and "blinded" in atk:
        try:
            forced_modes.remove("disadvantage")
        except ValueError:
            pass
        reasons.append("blindsense offsets unseen-target penalty")

    # Elusive: no attack roll has advantage against the rogue while not incapacitated.
    if bool(getattr(target, "elusive", False)) and "stunned" not in tgt and "incapacitated" not in tgt:
        forced_modes = [m for m in forced_modes if _norm(m) not in {"adv", "advantage"}]
        reasons.append("target elusive: no advantage against target")

    return {"mode": merge_roll_modes(*forced_modes), "reasons": reasons}


def save_rule_from_conditions(actor: Any, ability_key: str, base_mode: str = "normal") -> Dict[str, Any]:
    names = active_condition_names(actor)
    key = str(ability_key or "").strip().upper()
    mode = str(base_mode or "normal")
    reasons: List[str] = []
    auto_fail = False

    if "restrained" in names and key == "DEX":
        mode = merge_roll_modes(mode, "disadvantage")
        reasons.append("restrained: DEX saves disadvantage")

    if "stunned" in names and key in {"STR", "DEX"}:
        auto_fail = True
        reasons.append(f"stunned: auto-fail {key} saves")

    if bool(getattr(actor, "rage_active", False)) and key == "STR":
        mode = merge_roll_modes(mode, "advantage")
        reasons.append("rage: STR saves advantage")

    if bool(getattr(actor, "danger_sense", False)) and key == "DEX":
        mode = merge_roll_modes(mode, "advantage")
        reasons.append("danger sense: DEX saves advantage")

    return {"mode": mode, "auto_fail": auto_fail, "reasons": reasons}


def effective_speed_ft(actor: Any, base_speed: int) -> int:
    names = active_condition_names(actor)
    if "restrained" in names or "stunned" in names:
        return 0
    return max(0, int(base_speed or 0))


def condition_semantic_summary(name: str) -> str:
    n = _norm(name)
    mapping = {
        "poisoned": "disadvantage on attacks; ability checks above board",
        "restrained": "speed 0; attacks at disadvantage; attacks vs you advantage; DEX saves disadvantage",
        "blinded": "attacks at disadvantage; attacks vs you advantage; sight-based checks above board",
        "stunned": "speed 0; attacks vs you advantage; STR/DEX saves auto-fail",
        "prone": "melee attacks vs you advantage; ranged attacks vs you disadvantage",
        "charmed": "roleplay/status only unless otherwise specified",
    }
    return mapping.get(n, "")
