from copy import deepcopy
from typing import Any, Dict, List, Optional

PHB_FEATS: Dict[str, Dict[str, Any]] = {
    "alert": {"name": "Alert", "implemented": True, "passive": True},
    "athlete": {"name": "Athlete", "implemented": True, "passive": True, "needs_choice": "ability", "ability_choices": ["str", "dex"]},
    "actor": {"name": "Actor", "implemented": True, "passive": True, "ability_bonus": {"cha": 1}},
    "charger": {"name": "Charger", "implemented": False},
    "crossbow_expert": {"name": "Crossbow Expert", "implemented": False},
    "defensive_duelist": {"name": "Defensive Duelist", "implemented": False},
    "dual_wielder": {"name": "Dual Wielder", "implemented": True, "passive": True},
    "dungeon_delver": {"name": "Dungeon Delver", "implemented": False},
    "durable": {"name": "Durable", "implemented": True, "passive": True, "ability_bonus": {"con": 1}},
    "elemental_adept": {"name": "Elemental Adept", "implemented": True, "passive": True, "needs_choice": "damage_type"},
    "grappler": {"name": "Grappler", "implemented": False},
    "great_weapon_master": {"name": "Great Weapon Master", "implemented": True},
    "healer": {"name": "Healer", "implemented": True, "passive": True},
    "heavily_armored": {"name": "Heavily Armored", "implemented": True, "passive": True, "ability_bonus": {"str": 1}},
    "heavy_armor_master": {"name": "Heavy Armor Master", "implemented": False},
    "inspiring_leader": {"name": "Inspiring Leader", "implemented": False},
    "keen_mind": {"name": "Keen Mind", "implemented": True, "passive": True, "ability_bonus": {"int": 1}},
    "lightly_armored": {"name": "Lightly Armored", "implemented": True, "passive": True},
    "lucky": {"name": "Lucky", "implemented": False},
    "mage_slayer": {"name": "Mage Slayer", "implemented": False},
    "mobile": {"name": "Mobile", "implemented": True, "passive": True},
    "magic_initiate": {"name": "Magic Initiate", "implemented": False},
    "martial_adept": {"name": "Martial Adept", "implemented": False},
    "medium_armor_master": {"name": "Medium Armor Master", "implemented": True, "passive": True},
    "moderately_armored": {"name": "Moderately Armored", "implemented": True, "passive": True, "needs_choice": "ability", "ability_choices": ["str", "dex"]},
    "mounted_combatant": {"name": "Mounted Combatant", "implemented": False},
    "observant": {"name": "Observant", "implemented": True, "passive": True, "needs_choice": "ability", "ability_choices": ["int", "wis"]},
    "polearm_master": {"name": "Polearm Master", "implemented": False},
    "resilient": {"name": "Resilient", "implemented": True, "passive": True, "needs_choice": "ability", "ability_choices": ["str", "dex", "con", "int", "wis", "cha"]},
    "ritual_caster": {"name": "Ritual Caster", "implemented": False},
    "savage_attacker": {"name": "Savage Attacker", "implemented": True, "passive": True, "active": True},
    "sentinel": {"name": "Sentinel", "implemented": True},
    "sharpshooter": {"name": "Sharpshooter", "implemented": True},
    "shield_master": {"name": "Shield Master", "implemented": True, "passive": True},
    "skilled": {"name": "Skilled", "implemented": False},
    "skulker": {"name": "Skulker", "implemented": True, "passive": True},
    "spell_sniper": {"name": "Spell Sniper", "implemented": False},
    "tavern_brawler": {"name": "Tavern Brawler", "implemented": False},
    "tough": {"name": "Tough", "implemented": True, "passive": True},
    "war_caster": {"name": "War Caster", "implemented": True, "passive": True},
    "weapon_master": {"name": "Weapon Master", "implemented": False},
}

VALID_ABILITIES = {"str", "dex", "con", "int", "wis", "cha"}


def get_feat_registry() -> Dict[str, Dict[str, Any]]:
    return deepcopy(PHB_FEATS)


def feat_display_options() -> List[Dict[str, Any]]:
    out = []
    for feat_id, row in PHB_FEATS.items():
        item = {"feat_id": feat_id, **deepcopy(row)}
        out.append(item)
    out.sort(key=lambda x: str(x.get('name') or x.get('feat_id') or '').lower())
    return out


def sanitize_feat_ids(raw_ids: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in raw_ids or []:
        feat_id = str(raw or '').strip().lower()
        if not feat_id or feat_id in seen or feat_id not in PHB_FEATS:
            continue
        out.append(feat_id)
        seen.add(feat_id)
    return out


def feat_needs_choice(feat_id: str) -> str:
    return str((PHB_FEATS.get(str(feat_id or '').strip().lower(), {}) or {}).get('needs_choice') or '')


def validate_feat_choice(feat_id: str, feat_state: Optional[Dict[str, Any]]) -> Optional[str]:
    feat_row = PHB_FEATS.get(str(feat_id or '').strip().lower(), {}) or {}
    need = str(feat_row.get('needs_choice') or '')
    if not need:
        return None
    state = feat_state if isinstance(feat_state, dict) else {}
    if need == 'ability':
        ability = str(state.get('ability') or '').strip().lower()
        allowed = set(feat_row.get('ability_choices') or list(VALID_ABILITIES))
        if ability not in allowed:
            return 'Feat choice requires a valid ability selection'
    elif need == 'damage_type':
        dmg = str(state.get('damage_type') or '').strip().lower()
        if not dmg:
            return 'Feat choice requires a damage type selection'
    return None


def apply_feat_passives(sheet: Dict[str, Any], base_view: Dict[str, Any]) -> Dict[str, Any]:
    view = dict(base_view or {})
    feats = sanitize_feat_ids(sheet.get('feats') if isinstance(sheet.get('feats'), list) else [])
    feat_state = sheet.get('feat_state') if isinstance(sheet.get('feat_state'), dict) else {}
    combat = sheet.get('combat') if isinstance(sheet.get('combat'), dict) else {}
    prof = sheet.get('proficiencies') if isinstance(sheet.get('proficiencies'), dict) else {}
    other = prof.get('other') if isinstance(prof.get('other'), dict) else {}
    skills = prof.get('skills') if isinstance(prof.get('skills'), dict) else {}

    if 'alert' in feats:
        combat['initiative_bonus'] = int(combat.get('initiative_bonus', 0) or 0) + 5
        combat['cannot_be_surprised_by_hidden'] = True
    if 'athlete' in feats:
        view['movement_ft'] = int(view.get('movement_ft', 30) or 30) + 5
        combat['athlete_climb_no_extra'] = True
        combat['athlete_stand_5ft'] = True
    if 'dual_wielder' in feats:
        combat['dual_wielder_bonus_ac'] = 1
    if 'durable' in feats:
        combat['durable_feat'] = True
    if 'healer' in feats:
        combat['healer_feat'] = True
    if 'keen_mind' in feats:
        combat['keen_mind'] = True
    if 'medium_armor_master' in feats:
        combat['medium_armor_master'] = True
    if 'mobile' in feats:
        view['movement_ft'] = int(view.get('movement_ft', 30) or 30) + 10
        combat['mobile_no_difficult_dash'] = True
    if 'observant' in feats:
        combat['passive_perception_bonus'] = int(combat.get('passive_perception_bonus', 0) or 0) + 5
        combat['passive_investigation_bonus'] = int(combat.get('passive_investigation_bonus', 0) or 0) + 5
    if 'savage_attacker' in feats:
        combat['savage_attacker'] = True
    if 'shield_master' in feats:
        combat['shield_master_bonus_shove'] = True
        combat['shield_master_dex_cover'] = True
    if 'skulker' in feats:
        combat['skulker'] = True
    if 'war_caster' in feats:
        combat['war_caster_advantage_concentration'] = True
        combat['war_caster_somatic_hands_free'] = True
        combat['war_caster_opportunity_spell'] = True
    if 'elemental_adept' in feats:
        dmg = str((feat_state.get('elemental_adept') or {}).get('damage_type') or '').strip().lower()
        if dmg:
            combat.setdefault('elemental_adept_damage_types', [])
            if dmg not in combat['elemental_adept_damage_types']:
                combat['elemental_adept_damage_types'].append(dmg)
    if 'lightly_armored' in feats:
        other['armor'] = _merge_csv(other.get('armor', ''), 'light armor')
    if 'moderately_armored' in feats:
        other['armor'] = _merge_csv(other.get('armor', ''), 'medium armor, shields')
    if 'heavily_armored' in feats:
        other['armor'] = _merge_csv(other.get('armor', ''), 'heavy armor')
    if 'resilient' in feats:
        ability = str((feat_state.get('resilient') or {}).get('ability') or '').strip().lower()
        if ability in VALID_ABILITIES:
            saves = prof.get('saves') if isinstance(prof.get('saves'), dict) else {}
            saves[ability] = True
            prof['saves'] = saves
    prof['other'] = other
    prof['skills'] = skills
    sheet['proficiencies'] = prof
    sheet['combat'] = combat
    return view


def apply_feat_on_selection(sheet: Dict[str, Any], feat_id: str, feat_state: Optional[Dict[str, Any]] = None) -> None:
    feat_id = str(feat_id or '').strip().lower()
    feat_row = PHB_FEATS.get(feat_id, {}) or {}
    abilities = sheet.setdefault('abilities', {}) if isinstance(sheet.get('abilities'), dict) else {}
    stats = sheet.setdefault('stats', {}) if isinstance(sheet.get('stats'), dict) else {}
    resources = sheet.setdefault('resources', {}) if isinstance(sheet.get('resources'), dict) else {}
    meta = sheet.setdefault('meta', {}) if isinstance(sheet.get('meta'), dict) else {}
    feats_applied = sheet.setdefault('_feat_applied', {}) if isinstance(sheet.get('_feat_applied'), dict) else {}
    if feat_id in feats_applied:
        return
    level = max(1, int(meta.get('level', 1) or 1))
    state = feat_state if isinstance(feat_state, dict) else {}

    for ability, bonus in (feat_row.get('ability_bonus') or {}).items():
        if ability in VALID_ABILITIES:
            abilities[ability] = min(20, int(abilities.get(ability, 10) or 10) + int(bonus or 0))

    if feat_needs_choice(feat_id) == 'ability':
        ability = str(state.get('ability') or '').strip().lower()
        allowed = set(feat_row.get('ability_choices') or list(VALID_ABILITIES))
        if ability in allowed:
            abilities[ability] = min(20, int(abilities.get(ability, 10) or 10) + 1)

    if feat_id == 'tough':
        bonus = 2 * level
        stats['max_hp'] = max(1, int(stats.get('max_hp', 1) or 1) + bonus)
        stats['current_hp'] = max(0, int(stats.get('current_hp', 0) or 0) + bonus)
        resources['current_hp'] = int(stats['current_hp'])

    feats_applied[feat_id] = True
    sheet['_feat_applied'] = feats_applied


def _merge_csv(existing: str, extra: str) -> str:
    vals = []
    seen = set()
    for raw in [existing, extra]:
        for part in str(raw or '').replace(';', ',').split(','):
            tok = part.strip()
            if not tok:
                continue
            low = tok.lower()
            if low in seen:
                continue
            seen.add(low)
            vals.append(tok)
    return ', '.join(vals)


def sheet_has_feat(sheet: Dict[str, Any], feat_id: str) -> bool:
    feat_id = str(feat_id or '').strip().lower()
    if not feat_id:
        return False
    feats = sanitize_feat_ids(sheet.get('feats') if isinstance(sheet, dict) and isinstance(sheet.get('feats'), list) else [])
    return feat_id in feats


def feat_toggle_enabled(sheet: Dict[str, Any], feat_id: str, default: bool = False) -> bool:
    if not sheet_has_feat(sheet, feat_id):
        return False
    feat_state = sheet.get('feat_state') if isinstance(sheet, dict) and isinstance(sheet.get('feat_state'), dict) else {}
    row = feat_state.get(str(feat_id or '').strip().lower()) if isinstance(feat_state, dict) else {}
    if isinstance(row, dict) and 'enabled' in row:
        return bool(row.get('enabled'))
    return bool(default)
