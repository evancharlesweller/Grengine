from copy import deepcopy
from typing import Any, Dict, List, Optional

PHB_SUBCLASSES: Dict[str, Dict[str, Dict[str, Any]]] = {
    "barbarian": {
        "berserker": {"name": "Path of the Berserker", "features": {3:["Frenzy"],6:["Mindless Rage"],10:["Intimidating Presence"],14:["Retaliation"]}},
        "totem_warrior": {"name": "Path of the Totem Warrior", "features": {3:["Spirit Seeker", "Totem Spirit"],6:["Aspect of the Beast"],10:["Spirit Walker"],14:["Totemic Attunement"]}},
    },
    "bard": {
        "lore": {"name":"College of Lore","features":{3:["Bonus Proficiencies","Cutting Words"],6:["Additional Magical Secrets"],14:["Peerless Skill"]}},
        "valor": {"name":"College of Valor","features":{3:["Bonus Proficiencies","Combat Inspiration"],6:["Extra Attack"],14:["Battle Magic"]}},
    },
    "cleric": {
        "knowledge": {"name":"Knowledge Domain","features":{1:["Blessings of Knowledge"],2:["Channel Divinity: Knowledge of the Ages"],6:["Read Thoughts"],8:["Potent Spellcasting"],17:["Visions of the Past"]}, "spells": {"always_prepared": {1:["command","identify"],3:["augury","suggestion"],5:["nondetection","speak_with_dead"],7:["arcane_eye","confusion"],9:["legend_lore","scrying"]}}},
        "life": {"name":"Life Domain","features":{1:["Bonus Proficiency","Disciple of Life"],2:["Channel Divinity: Preserve Life"],6:["Blessed Healer"],8:["Divine Strike"],17:["Supreme Healing"]}, "spells":{"always_prepared": {1:["bless","cure_wounds"],3:["lesser_restoration","spiritual_weapon"],5:["beacon_of_hope","revivify"],7:["death_ward","guardian_of_faith"],9:["mass_cure_wounds","raise_dead"]}}},
        "light": {"name":"Light Domain","features":{1:["Bonus Cantrip","Warding Flare"],2:["Channel Divinity: Radiance of the Dawn"],6:["Improved Flare"],8:["Potent Spellcasting"],17:["Corona of Light"]}, "spells":{"always_prepared": {1:["burning_hands","faerie_fire"],3:["flaming_sphere","scorching_ray"],5:["daylight","fireball"],7:["guardian_of_faith","wall_of_fire"],9:["flame_strike","scrying"]}}},
        "nature": {"name":"Nature Domain","features":{1:["Acolyte of Nature","Bonus Proficiency"],2:["Channel Divinity: Charm Animals and Plants"],6:["Dampen Elements"],8:["Divine Strike"],17:["Master of Nature"]}, "spells":{"always_prepared": {1:["animal_friendship","speak_with_animals"],3:["barkskin","spike_growth"],5:["plant_growth","wind_wall"],7:["dominate_beast","grasping_vine"],9:["insect_plague","tree_stride"]}}},
        "tempest": {"name":"Tempest Domain","features":{1:["Bonus Proficiencies","Wrath of the Storm"],2:["Channel Divinity: Destructive Wrath"],6:["Thunderbolt Strike"],8:["Divine Strike"],17:["Stormborn"]}, "spells":{"always_prepared": {1:["fog_cloud","thunderwave"],3:["gust_of_wind","shatter"],5:["call_lightning","sleet_storm"],7:["control_water","ice_storm"],9:["destructive_wave","insect_plague"]}}},
        "trickery": {"name":"Trickery Domain","features":{1:["Blessing of the Trickster"],2:["Channel Divinity: Invoke Duplicity"],6:["Channel Divinity: Cloak of Shadows"],8:["Divine Strike"],17:["Improved Duplicity"]}, "spells":{"always_prepared": {1:["charm_person","disguise_self"],3:["mirror_image","pass_without_trace"],5:["blink","dispel_magic"],7:["dimension_door","polymorph"],9:["dominate_person","modify_memory"]}}},
        "war": {"name":"War Domain","features":{1:["Bonus Proficiencies","War Priest"],2:["Channel Divinity: Guided Strike"],6:["Channel Divinity: War God's Blessing"],8:["Divine Strike"],17:["Avatar of Battle"]}, "spells":{"always_prepared": {1:["divine_favor","shield_of_faith"],3:["magic_weapon","spiritual_weapon"],5:["crusaders_mantle","spirit_guardians"],7:["freedom_of_movement","stoneskin"],9:["flame_strike","hold_monster"]}}},
    },
    "druid": {
        "land": {"name":"Circle of the Land","features":{2:["Bonus Cantrip","Natural Recovery"],6:["Land's Stride"],10:["Nature's Ward"],14:["Nature's Sanctuary"]}},
        "moon": {"name":"Circle of the Moon","features":{2:["Combat Wild Shape","Circle Forms"],6:["Primal Strike"],10:["Elemental Wild Shape"],14:["Thousand Forms"]}},
    },
    "fighter": {
        "champion": {"name":"Champion","features":{3:["Improved Critical"],7:["Remarkable Athlete"],10:["Additional Fighting Style"],15:["Superior Critical"],18:["Survivor"]}},
        "battle_master": {"name":"Battle Master","features":{3:["Combat Superiority","Student of War"],7:["Know Your Enemy"],10:["Improved Combat Superiority"],15:["Relentless"]}},
        "eldritch_knight": {"name":"Eldritch Knight","features":{3:["Weapon Bond"],7:["War Magic"],10:["Eldritch Strike"],15:["Arcane Charge"],18:["Improved War Magic"]}, "spells":{"expanded_access":{"schools":["abjuration","evocation"],"extra_any_spells":{3:1,8:2,14:3,20:4}}}},
    },
    "monk": {
        "open_hand": {"name":"Way of the Open Hand","features":{3:["Open Hand Technique"],6:["Wholeness of Body"],11:["Tranquility"],17:["Quivering Palm"]}},
        "shadow": {"name":"Way of Shadow","features":{3:["Shadow Arts"],6:["Shadow Step"],11:["Cloak of Shadows"],17:["Opportunist"]}},
        "four_elements": {"name":"Way of the Four Elements","features":{3:["Disciple of the Elements"],6:["Elemental Attunement"],11:["Elemental Discipline"],17:["Elemental Mastery"]}},
    },
    "paladin": {
        "devotion": {"name":"Oath of Devotion","features":{3:["Channel Divinity: Sacred Weapon","Channel Divinity: Turn the Unholy"],7:["Aura of Devotion"],15:["Purity of Spirit"],20:["Holy Nimbus"]}, "spells":{"always_prepared": {3:["protection_from_evil_and_good","sanctuary"],5:["lesser_restoration","zone_of_truth"],9:["beacon_of_hope","dispel_magic"],13:["freedom_of_movement","guardian_of_faith"],17:["commune","flame_strike"]}}},
        "ancients": {"name":"Oath of the Ancients","features":{3:["Channel Divinity: Nature's Wrath","Channel Divinity: Turn the Faithless"],7:["Aura of Warding"],15:["Undying Sentinel"],20:["Elder Champion"]}, "spells":{"always_prepared": {3:["ensnaring_strike","speak_with_animals"],5:["moonbeam","misty_step"],9:["plant_growth","protection_from_energy"],13:["ice_storm","stoneskin"],17:["commune_with_nature","tree_stride"]}}},
        "vengeance": {"name":"Oath of Vengeance","features":{3:["Channel Divinity: Abjure Enemy","Channel Divinity: Vow of Enmity"],7:["Relentless Avenger"],15:["Soul of Vengeance"],20:["Avenging Angel"]}, "spells":{"always_prepared": {3:["bane","hunters_mark"],5:["hold_person","misty_step"],9:["haste","protection_from_energy"],13:["banishment","dimension_door"],17:["hold_monster","scrying"]}}},
    },
    "ranger": {
        "hunter": {"name":"Hunter","features":{3:["Hunter's Prey"],7:["Defensive Tactics"],11:["Multiattack"],15:["Superior Hunter's Defense"]}},
        "beast_master": {"name":"Beast Master","features":{3:["Ranger's Companion"],7:["Exceptional Training"],11:["Bestial Fury"],15:["Share Spells"]}},
    },
    "rogue": {
        "thief": {"name":"Thief","features":{3:["Fast Hands","Second-Story Work"],9:["Supreme Sneak"],13:["Use Magic Device"],17:["Thief's Reflexes"]}},
        "assassin": {"name":"Assassin","features":{3:["Bonus Proficiencies","Assassinate"],9:["Infiltration Expertise"],13:["Impostor"],17:["Death Strike"]}},
        "arcane_trickster": {"name":"Arcane Trickster","features":{3:["Mage Hand Legerdemain"],9:["Magical Ambush"],13:["Versatile Trickster"],17:["Spell Thief"]}, "spells":{"expanded_access":{"schools":["enchantment","illusion"],"extra_any_spells":{3:1,8:2,14:3,20:4}}}},
    },
    "sorcerer": {
        "draconic_bloodline": {"name":"Draconic Bloodline","features":{1:["Dragon Ancestor","Draconic Resilience"],6:["Elemental Affinity"],14:["Dragon Wings"],18:["Draconic Presence"]}},
        "wild_magic": {"name":"Wild Magic","features":{1:["Wild Magic Surge","Tides of Chaos"],6:["Bend Luck"],14:["Controlled Chaos"],18:["Spell Bombardment"]}},
    },
    "warlock": {
        "archfey": {"name":"The Archfey","features":{1:["Fey Presence"],6:["Misty Escape"],10:["Beguiling Defenses"],14:["Dark Delirium"]}, "spells":{"expanded_access":{"spell_names":["faerie_fire","sleep","calm_emotions","phantasmal_force","blink","plant_growth","dominate_beast","greater_invisibility","dominate_person","seeming"]}}},
        "fiend": {"name":"The Fiend","features":{1:["Dark One's Blessing"],6:["Dark One's Own Luck"],10:["Fiendish Resilience"],14:["Hurl Through Hell"]}, "spells":{"expanded_access":{"spell_names":["burning_hands","command","blindness_deafness","scorching_ray","fireball","stinking_cloud","fire_shield","wall_of_fire","flame_strike","hallow"]}}},
        "great_old_one": {"name":"The Great Old One","features":{1:["Awakened Mind"],6:["Entropic Ward"],10:["Thought Shield"],14:["Create Thrall"]}, "spells":{"expanded_access":{"spell_names":["dissonant_whispers","tashas_hideous_laughter","detect_thoughts","phantasmal_force","clairvoyance","sending","dominate_beast","evards_black_tentacles","dominate_person","telekinesis"]}}},
    },
    "wizard": {
        "abjuration": {"name":"School of Abjuration","features":{2:["Abjuration Savant","Arcane Ward"],6:["Projected Ward"],10:["Improved Abjuration"],14:["Spell Resistance"]}},
        "conjuration": {"name":"School of Conjuration","features":{2:["Conjuration Savant","Minor Conjuration"],6:["Benign Transposition"],10:["Focused Conjuration"],14:["Durable Summons"]}},
        "divination": {"name":"School of Divination","features":{2:["Divination Savant","Portent"],6:["Expert Divination"],10:["The Third Eye"],14:["Greater Portent"]}},
        "enchantment": {"name":"School of Enchantment","features":{2:["Enchantment Savant","Hypnotic Gaze"],6:["Instinctive Charm"],10:["Split Enchantment"],14:["Alter Memories"]}},
        "evocation": {"name":"School of Evocation","features":{2:["Evocation Savant","Sculpt Spells"],6:["Potent Cantrip"],10:["Empowered Evocation"],14:["Overchannel"]}},
        "illusion": {"name":"School of Illusion","features":{2:["Illusion Savant","Improved Minor Illusion"],6:["Malleable Illusions"],10:["Illusory Self"],14:["Illusory Reality"]}},
        "necromancy": {"name":"School of Necromancy","features":{2:["Necromancy Savant","Grim Harvest"],6:["Undead Thralls"],10:["Inured to Undeath"],14:["Command Undead"]}},
        "transmutation": {"name":"School of Transmutation","features":{2:["Transmutation Savant","Minor Alchemy"],6:["Transmuter's Stone"],10:["Shapechanger"],14:["Master Transmuter"]}},
    },
}

def get_subclass_registry() -> Dict[str, Dict[str, Dict[str, Any]]]:
    return deepcopy(PHB_SUBCLASSES)

def get_subclasses_for_class(class_key: str) -> List[Dict[str, Any]]:
    ck = str(class_key or '').strip().lower()
    out = []
    for subclass_id, row in (PHB_SUBCLASSES.get(ck) or {}).items():
        out.append({"subclass_id": subclass_id, **deepcopy(row)})
    out.sort(key=lambda x: str(x.get('name') or x.get('subclass_id') or '').lower())
    return out

def get_subclass_row(class_key: str, subclass_id: str) -> Dict[str, Any]:
    return deepcopy((PHB_SUBCLASSES.get(str(class_key or '').strip().lower(), {}) or {}).get(str(subclass_id or '').strip().lower(), {}) or {})

def subclass_feature_lines(class_key: str, subclass_id: str, level: int) -> List[str]:
    row = get_subclass_row(class_key, subclass_id)
    feats = row.get('features') if isinstance(row.get('features'), dict) else {}
    out: List[str] = []
    for raw_lvl, lines in feats.items():
        try:
            lvl = int(raw_lvl)
        except Exception:
            continue
        if int(level or 0) < lvl:
            continue
        for line in lines or []:
            txt = str(line or '').strip()
            if txt and txt not in out:
                out.append(txt)
    return out

def apply_subclass_passives(sheet: Dict[str, Any], derived_view: Dict[str, Any]) -> Dict[str, Any]:
    view = dict(derived_view or {})
    meta = sheet.get('meta') if isinstance(sheet.get('meta'), dict) else {}
    class_levels = sheet.get('class_levels') if isinstance(sheet.get('class_levels'), dict) else {}
    subclasses = sheet.get('subclasses') if isinstance(sheet.get('subclasses'), dict) else {}
    combat = sheet.get('combat') if isinstance(sheet.get('combat'), dict) else {}
    resources = sheet.get('resources') if isinstance(sheet.get('resources'), dict) else {}
    resource_pools = sheet.setdefault('resource_pools', {}) if isinstance(sheet.get('resource_pools'), dict) else {}
    features = sheet.setdefault('features', {}) if isinstance(sheet.get('features'), dict) else {}
    abilities = sheet.get('abilities') if isinstance(sheet.get('abilities'), dict) else {}
    prof = sheet.get('proficiencies') if isinstance(sheet.get('proficiencies'), dict) else {}
    other = prof.get('other') if isinstance(prof.get('other'), dict) else {}
    class_key = str(meta.get('class') or '').strip().lower().replace(' ', '_')
    for ck, subclass_id in subclasses.items():
        level = int(class_levels.get(ck, 0) or 0)
        lines = subclass_feature_lines(ck, subclass_id, level)
        if lines:
            existing = str(features.get('features_and_traits') or '').splitlines()
            existing_set = {line.strip().lstrip('•').strip() for line in existing if str(line).strip()}
            merged = existing[:]
            for line in lines:
                if line not in existing_set:
                    merged.append(f'• {line}')
                    existing_set.add(line)
            features['features_and_traits'] = '\n'.join([m for m in merged if str(m).strip()])
    primary_subclass = str(subclasses.get(class_key) or '').strip().lower()
    primary_level = int(class_levels.get(class_key, 0) or 0)

    if class_key == 'fighter':
        if primary_subclass == 'champion':
            if primary_level >= 3:
                combat['critical_hit_min'] = 19 if primary_level < 15 else 18
            if primary_level >= 7:
                combat['remarkable_athlete'] = True
            if primary_level >= 18:
                combat['champion_survivor'] = True
        elif primary_subclass == 'battle_master':
            if primary_level >= 3:
                die_size = 8 if primary_level < 10 else 10 if primary_level < 18 else 12
                _ensure_pool(resource_pools, 'superiority_dice', 4 if primary_level < 15 else 5, die_size=die_size, refresh='short_rest')
                combat['battle_master_maneuvers'] = True
            if primary_level >= 7:
                combat['know_your_enemy'] = True
        elif primary_subclass == 'eldritch_knight':
            if primary_level >= 3:
                combat['weapon_bond'] = True
            if primary_level >= 7:
                combat['eldritch_knight_war_magic'] = True
            if primary_level >= 10:
                combat['eldritch_strike'] = True

    elif class_key == 'rogue':
        if primary_subclass == 'thief' and primary_level >= 3:
            combat['thief_fast_hands'] = True
            combat['second_story_work'] = True
        elif primary_subclass == 'assassin':
            if primary_level >= 3:
                combat['assassinate'] = True
            if primary_level >= 17:
                combat['death_strike'] = True
        elif primary_subclass == 'arcane_trickster' and primary_level >= 9:
            combat['magical_ambush'] = True

    elif class_key == 'paladin':
        if primary_subclass == 'ancients' and primary_level >= 7:
            resources.setdefault('damage_resistances', [])
            if 'spell_damage' not in resources['damage_resistances']:
                resources['damage_resistances'].append('spell_damage')
        elif primary_subclass == 'devotion':
            if primary_level >= 7:
                combat['aura_charm_immunity'] = True
            if primary_level >= 15:
                combat['purity_of_spirit'] = True
        elif primary_subclass == 'vengeance' and primary_level >= 7:
            combat['relentless_avenger'] = True

    elif class_key == 'cleric':
        if primary_subclass == 'life' and primary_level >= 1:
            combat['life_domain_heal_bonus'] = True
        elif primary_subclass == 'light':
            if primary_level >= 1:
                combat['warding_flare'] = True
            if primary_level >= 17:
                combat['corona_of_light'] = True
        elif primary_subclass == 'tempest':
            if primary_level >= 1:
                _ensure_pool(resource_pools, 'wrath_of_the_storm', max(1, (int((abilities or {}).get('wis', 10) or 10) - 10) // 2), refresh='long_rest')
                combat['wrath_of_the_storm'] = True
            if primary_level >= 6:
                combat['thunderbolt_strike'] = True
        elif primary_subclass == 'war' and primary_level >= 1:
            _ensure_pool(resource_pools, 'war_priest', max(1, (int((abilities or {}).get('wis', 10) or 10) - 10) // 2), refresh='long_rest')
            combat['war_priest'] = True

    elif class_key == 'sorcerer':
        if primary_subclass == 'draconic_bloodline' and primary_level >= 1:
            view['ac'] = max(int(view.get('ac', 10) or 10), 13 + ((int((abilities or {}).get('dex', 10) or 10) - 10) // 2))
            view['defense'] = max(int(view.get('defense', view.get('ac', 10)) or 10), int(view['ac']))
            combat['draconic_resilience'] = True
            if primary_level >= 6:
                combat['elemental_affinity'] = True
        elif primary_subclass == 'wild_magic' and primary_level >= 1:
            combat['wild_magic_surge'] = True
            combat['tides_of_chaos'] = True

    elif class_key == 'wizard':
        if primary_subclass == 'evocation' and primary_level >= 2:
            combat['evocation_sculpt_spells'] = True
        elif primary_subclass == 'abjuration' and primary_level >= 2:
            _ensure_pool(resource_pools, 'arcane_ward', max(1, 2 * primary_level), refresh='long_rest')
            combat['arcane_ward'] = True
        elif primary_subclass == 'divination' and primary_level >= 2:
            _ensure_pool(resource_pools, 'portent', 2 if primary_level < 14 else 3, refresh='long_rest')
            combat['portent'] = True
        elif primary_subclass == 'illusion' and primary_level >= 2:
            combat['improved_minor_illusion'] = True

    elif class_key == 'bard':
        if primary_subclass == 'valor' and primary_level >= 6:
            combat['extra_attack'] = True
        elif primary_subclass == 'lore':
            if primary_level >= 3:
                other['skills_bonus_proficiencies'] = _merge_csv(other.get('skills_bonus_proficiencies', ''), 'any 3')
            if primary_level >= 6:
                combat['additional_magical_secrets'] = 2

    elif class_key == 'barbarian':
        if primary_subclass == 'berserker' and primary_level >= 3:
            combat['berserker_frenzy'] = True
        elif primary_subclass == 'totem_warrior' and primary_level >= 3:
            combat['totem_warrior'] = True

    elif class_key == 'druid':
        if primary_subclass == 'moon' and primary_level >= 2:
            combat['combat_wild_shape'] = True
            if primary_level >= 6:
                combat['moon_primal_strike'] = True
        elif primary_subclass == 'land' and primary_level >= 2:
            _ensure_pool(resource_pools, 'natural_recovery', 1, refresh='long_rest')
            combat['natural_recovery'] = True

    elif class_key == 'ranger':
        if primary_subclass == 'hunter' and primary_level >= 3:
            combat['hunters_prey'] = True
        elif primary_subclass == 'beast_master' and primary_level >= 3:
            combat['rangers_companion'] = True

    elif class_key == 'monk':
        if primary_subclass == 'open_hand' and primary_level >= 3:
            combat['open_hand_technique'] = True
        elif primary_subclass == 'shadow' and primary_level >= 3:
            combat['shadow_arts'] = True
        elif primary_subclass == 'four_elements' and primary_level >= 3:
            combat['four_elements_disciplines'] = True

    elif class_key == 'warlock':
        if primary_subclass == 'fiend' and primary_level >= 1:
            combat['dark_ones_blessing'] = True
        elif primary_subclass == 'archfey' and primary_level >= 1:
            combat['fey_presence'] = True
        elif primary_subclass == 'great_old_one' and primary_level >= 1:
            combat['awakened_mind'] = True

    prof['other'] = other
    sheet['proficiencies'] = prof
    sheet['combat'] = combat
    sheet['resources'] = resources
    sheet['resource_pools'] = resource_pools
    sheet['features'] = features
    return view


def _ensure_pool(resource_pools: Dict[str, Any], key: str, maximum: int, *, die_size: int = 0, refresh: str = 'long_rest') -> None:
    maximum = max(0, int(maximum or 0))
    if maximum <= 0:
        return
    row = resource_pools.get(key) if isinstance(resource_pools.get(key), dict) else {}
    current = int(row.get('current', maximum) or maximum)
    row['max'] = maximum
    row['current'] = min(maximum, max(0, current))
    row['refresh'] = str(row.get('refresh') or refresh)
    if die_size > 0:
        row['die_size'] = int(die_size)
    resource_pools[key] = row


def _merge_csv(existing: str, extra: str) -> str:
    vals = []
    seen = set()
    for raw in [existing, extra]:
        for part in str(raw or '').replace(';', ',').split(','):
            tok = part.strip()
            if not tok:
                continue
            key = tok.lower()
            if key in seen:
                continue
            seen.add(key)
            vals.append(tok)
    return ', '.join(vals)

def collect_subclass_spell_refs(class_key: str, subclass_id: str, level: int) -> Dict[str, Any]:
    row = get_subclass_row(class_key, subclass_id)
    out = {"always_prepared": [], "bonus_known": [], "expanded_access": []}
    spells = row.get('spells') if isinstance(row.get('spells'), dict) else {}
    for grant_type in ('always_prepared', 'bonus_known'):
        lvl_map = spells.get(grant_type) if isinstance(spells.get(grant_type), dict) else {}
        for raw_lvl, refs in lvl_map.items():
            try:
                grant_level = int(raw_lvl)
            except Exception:
                continue
            if int(level or 0) >= grant_level:
                for ref in refs or []:
                    ref_s = str(ref or '').strip()
                    if ref_s and ref_s not in out[grant_type]:
                        out[grant_type].append(ref_s)
    expanded = spells.get('expanded_access') if isinstance(spells.get('expanded_access'), dict) else {}
    if expanded:
        out['expanded_access'].append(deepcopy(expanded))
    return out

def resolve_spell_refs(spells_db: Dict[str, Any], refs: List[str]) -> List[str]:
    if not isinstance(spells_db, dict):
        return []
    by_name = {}
    for sid, row in spells_db.items():
        if not isinstance(row, dict):
            continue
        by_name[str(row.get('name') or '').strip().lower()] = sid
    out: List[str] = []
    seen = set()
    for ref in refs or []:
        raw = str(ref or '').strip()
        if not raw:
            continue
        sid = raw if raw in spells_db else by_name.get(raw.replace('_', ' ').lower()) or by_name.get(raw.lower())
        if sid and sid not in seen:
            out.append(sid)
            seen.add(sid)
    return out
