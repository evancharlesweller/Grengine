from __future__ import annotations

from typing import Any, Dict, List


def _norm_id(value: str) -> str:
    return str(value or '').strip().lower().replace('-', '_').replace(' ', '_')


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _ability_mod(score: Any) -> int:
    try:
        return (int(score) - 10) // 2
    except Exception:
        return 0


def _merge_unique_strs(existing: Any, additions: Any) -> List[str]:
    out: List[str] = []
    seen = set()
    def _push_many(src: Any) -> None:
        if isinstance(src, str):
            vals = [src]
        elif isinstance(src, (list, tuple, set)):
            vals = list(src)
        else:
            vals = []
        for item in vals:
            val = str(item or '').strip()
            if not val:
                continue
            key = _norm_id(val)
            if key in seen:
                continue
            seen.add(key)
            out.append(val)
    _push_many(existing)
    _push_many(additions)
    return out


FEATURE_DEFS: Dict[str, Dict[str, Any]] = {
    # Vision / movement
    'elf_darkvision': {'darkvision_ft': 60},
    'dwarf_darkvision': {'darkvision_ft': 60},
    'dramau_darkvision': {'darkvision_ft': 60},
    'deep_dwarf_deepvision': {'darkvision_ft': 120},
    'moon_elf_night_attunement': {'darkvision_ft': 120},
    'shadow_dramau_umbral_sight': {'darkvision_ft': 120},
    'wood_elf_forest_affinity': {'movement_bonus_ft': 10},
    'stormen_stone_strider': {'ignore_difficult_terrain': True},
    'surface_dwarf_traveler_of_the_roads': {'travel_speed_bonus_ft': 5},

    # Damage typing / survivability
    'dwarf_poison_resilience': {'damage_resistances': ['poison']},
    'sun_elf_radiant_lineage': {'damage_resistances': ['radiant']},
    'fire_dramau_fire_resistance': {'damage_resistances': ['fire']},
    'frost_dramau_cold_resistance': {'damage_resistances': ['cold']},
    'shadow_dramau_shadow_resistance': {'damage_resistances': ['necrotic']},
    'stormen_mountain_born': {'damage_resistances': ['cold']},

    # AC formulas / passive chassis
    'dramau_scaled_hide': {'ac_formula_min': '13+dex', 'requires_unarmored': True},
    'barbarian_unarmored_defense': {'ac_formula_min': '10+dex+con', 'requires_unarmored': True},
    'monk_unarmored_defense': {'ac_formula_min': '10+dex+wis', 'requires_unarmored': True},

    # Save support (future-facing but already usable in save_engine if hydrated to TokenState)
    'imperial_human_diplomatic_presence': {'save_bonus': {'cha': 1}},
    'deep_dwarf_subterranean_instinct': {'save_bonus': {'wis': 1}},
}


def resolve_sheet_passives(sheet: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(sheet, dict):
        return {}

    abilities = dict(sheet.get('abilities') or {}) if isinstance(sheet.get('abilities'), dict) else {}
    stats = dict(sheet.get('stats') or {}) if isinstance(sheet.get('stats'), dict) else {}
    combat = dict(sheet.get('combat') or {}) if isinstance(sheet.get('combat'), dict) else {}
    equipped = dict(sheet.get('equipped') or {}) if isinstance(sheet.get('equipped'), dict) else {}
    equipment = dict(sheet.get('equipment') or {}) if isinstance(sheet.get('equipment'), dict) else {}
    resources = dict(sheet.get('resources') or {}) if isinstance(sheet.get('resources'), dict) else {}

    feature_ids: List[str] = []
    for bucket in (sheet.get('trait_ids'), sheet.get('ability_ids')):
        if isinstance(bucket, list):
            feature_ids.extend(_norm_id(x) for x in bucket if str(x or '').strip())

    out: Dict[str, Any] = {
        'feature_ids': list(dict.fromkeys(feature_ids)),
        'darkvision_ft': 0,
        'movement_bonus_ft': 0,
        'travel_speed_bonus_ft': 0,
        'damage_resistances': [],
        'damage_immunities': [],
        'damage_vulnerabilities': [],
        'save_bonus': {},
        'ignore_difficult_terrain': False,
    }

    has_armor = bool(
        str(equipped.get('armor_id') or equipped.get('armor') or equipment.get('armor_id') or equipment.get('armor') or '').strip()
    )
    dex_mod = _ability_mod(abilities.get('dex', 10))
    con_mod = _ability_mod(abilities.get('con', 10))
    wis_mod = _ability_mod(abilities.get('wis', 10))
    current_ac = _safe_int(combat.get('ac', stats.get('defense', stats.get('ac', 10))), 10)
    ac_floor = current_ac

    for fid in out['feature_ids']:
        spec = FEATURE_DEFS.get(fid, {})
        if not spec:
            continue
        out['darkvision_ft'] = max(int(out.get('darkvision_ft', 0) or 0), int(spec.get('darkvision_ft', 0) or 0))
        out['movement_bonus_ft'] = int(out.get('movement_bonus_ft', 0) or 0) + int(spec.get('movement_bonus_ft', 0) or 0)
        out['travel_speed_bonus_ft'] = int(out.get('travel_speed_bonus_ft', 0) or 0) + int(spec.get('travel_speed_bonus_ft', 0) or 0)
        out['damage_resistances'] = _merge_unique_strs(out.get('damage_resistances'), spec.get('damage_resistances'))
        out['damage_immunities'] = _merge_unique_strs(out.get('damage_immunities'), spec.get('damage_immunities'))
        out['damage_vulnerabilities'] = _merge_unique_strs(out.get('damage_vulnerabilities'), spec.get('damage_vulnerabilities'))
        if spec.get('ignore_difficult_terrain'):
            out['ignore_difficult_terrain'] = True
        if isinstance(spec.get('save_bonus'), dict):
            for k, v in spec.get('save_bonus', {}).items():
                nk = _norm_id(k)[:3].upper()
                try:
                    out['save_bonus'][nk] = int(out['save_bonus'].get(nk, 0)) + int(v)
                except Exception:
                    continue

        formula = str(spec.get('ac_formula_min') or '').strip().lower()
        if formula:
            if spec.get('requires_unarmored') and has_armor:
                continue
            candidate = current_ac
            if formula == '13+dex':
                candidate = 13 + dex_mod
            elif formula == '10+dex+con':
                candidate = 10 + dex_mod + con_mod
            elif formula == '10+dex+wis':
                candidate = 10 + dex_mod + wis_mod
            ac_floor = max(ac_floor, int(candidate))

    combat = dict(sheet.get('combat') or {}) if isinstance(sheet.get('combat'), dict) else {}
    if bool(combat.get('rage_active', False)):
        out['damage_resistances'] = _merge_unique_strs(out.get('damage_resistances'), ['bludgeoning', 'piercing', 'slashing'])

    if ac_floor > current_ac:
        out['ac_min'] = int(ac_floor)
    return out


def apply_passives_to_combat_view(sheet: Dict[str, Any], view: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(view or {})
    resolved = resolve_sheet_passives(sheet)
    if not resolved:
        return out

    # Vision
    base_vision = _safe_int(out.get('vision_ft', 60), 60)
    darkvision_ft = int(resolved.get('darkvision_ft', 0) or 0)
    out['darkvision_ft'] = max(_safe_int(out.get('darkvision_ft', 0), 0), darkvision_ft)
    if darkvision_ft > 0:
        out['vision_ft'] = max(base_vision, darkvision_ft)

    # Movement
    move_key = 'movement_ft' if 'movement_ft' in out else 'movement'
    base_move = _safe_int(out.get(move_key, 30), 30)
    out[move_key] = max(0, base_move + int(resolved.get('movement_bonus_ft', 0) or 0))

    # AC floor
    if resolved.get('ac_min') is not None:
        out['ac'] = max(_safe_int(out.get('ac', 10), 10), int(resolved.get('ac_min') or 10))
        out['defense'] = max(_safe_int(out.get('defense', out.get('ac', 10)), 10), int(resolved.get('ac_min') or 10))

    # Damage profile merge
    resists = _merge_unique_strs(out.get('damage_resistances'), resolved.get('damage_resistances'))
    immunes = _merge_unique_strs(out.get('damage_immunities'), resolved.get('damage_immunities'))
    vulns = _merge_unique_strs(out.get('damage_vulnerabilities'), resolved.get('damage_vulnerabilities'))
    if resists:
        out['damage_resistances'] = resists
    if immunes:
        out['damage_immunities'] = immunes
    if vulns:
        out['damage_vulnerabilities'] = vulns

    profile = dict(out.get('damage_profile') or {}) if isinstance(out.get('damage_profile'), dict) else {}
    if resists and not profile.get('resistances'):
        profile['resistances'] = list(resists)
    elif resists:
        profile['resistances'] = _merge_unique_strs(profile.get('resistances'), resists)
    if immunes:
        profile['immunities'] = _merge_unique_strs(profile.get('immunities'), immunes)
    if vulns:
        profile['vulnerabilities'] = _merge_unique_strs(profile.get('vulnerabilities'), vulns)
    if profile:
        out['damage_profile'] = profile

    if resolved.get('save_bonus'):
        out['save_bonus'] = dict(resolved.get('save_bonus') or {})
    if resolved.get('ignore_difficult_terrain'):
        out['ignore_difficult_terrain'] = True
    return out
