# server_main.py
import os
import sys
import json
import time
import uuid
import secrets
from typing import Optional, List, Dict, Any, Tuple, Set
import random
from copy import deepcopy

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from engine.trait_engine import apply_passives_to_combat_view
from engine.spell_engine import ensure_spellcasting_foundation, refresh_spell_slots, consume_spell_slot, load_spells_db, get_spell_effects

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from roll_server.server.classes.progression import asi_levels_for_class, class_needs_subclass_choice, subclass_unlock_level
from roll_server.server.classes.subclasses import get_subclasses_for_class, apply_subclass_passives
from roll_server.server.classes.feats import feat_display_options, sanitize_feat_ids, validate_feat_choice, apply_feat_passives, apply_feat_on_selection
from roll_server.server.classes.spellcasting import subclass_spell_grants


app = FastAPI(title="Grengine DM Server", version="0.3.0")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
PROJECT_ROOT = os.path.abspath(os.path.join(BASE_DIR, ".."))

app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ------------------------------------------------------------
# Campaign resolution
# ------------------------------------------------------------
CAMPAIGNS_ROOT = os.environ.get("GRENGINE_CAMPAIGNS_ROOT", os.path.join(PROJECT_ROOT, "campaigns"))
DEFAULT_CAMPAIGN_ID = os.environ.get("GRENGINE_DEFAULT_CAMPAIGN", "Test")


# ------------------------------------------------------------
# Phase E foundation registries (creation-time autofill only)
# ------------------------------------------------------------
CLASS_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "barbarian": {
        "display_name": "Barbarian",
        "hit_die": 12,
        "primary_ability": "str",
        "save_proficiencies": ["str", "con"],
        "skill_options": ["animal_handling", "athletics", "intimidation", "nature", "perception", "survival"],
        "proficiencies": {"armor": "Light armor, medium armor, shields", "weapons": "Simple weapons, martial weapons"},
        "ability_ids": ["barbarian_rage", "barbarian_unarmored_defense"],
        "resource_pools": {"rage": {"current": 2, "max": 2, "refresh": "long_rest"}},
        "feature_lines": ["Rage", "Unarmored Defense"],
        "level_grants": {
            2: {
                "ability_ids": ["barbarian_reckless_attack"],
                "feature_lines": ["Reckless Attack", "Danger Sense"],
            },
            3: {
                "feature_lines": ["Primal Path"],
            },
            4: {
                "feature_lines": ["Ability Score Improvement"],
            },
            5: {
                "combat": {"attacks_per_action": 2},
                "feature_lines": ["Extra Attack", "Fast Movement"],
            },
            6: {
                "feature_lines": ["Path Feature"],
            },
            7: {
                "feature_lines": ["Feral Instinct"],
            },
            8: {
                "feature_lines": ["Ability Score Improvement"],
            },
            9: {
                "feature_lines": ["Brutal Critical"],
            },
            10: {
                "feature_lines": ["Path Feature"],
            },
            11: {
                "feature_lines": ["Relentless Rage"],
            },
            12: {
                "feature_lines": ["Ability Score Improvement"],
            },
            13: {
                "feature_lines": ["Brutal Critical (2 dice)"],
            },
            14: {
                "feature_lines": ["Path Feature"],
            },
            15: {
                "feature_lines": ["Persistent Rage"],
            },
            16: {
                "feature_lines": ["Ability Score Improvement"],
            },
            17: {
                "feature_lines": ["Brutal Critical (3 dice)"],
            },
            18: {
                "feature_lines": ["Indomitable Might"],
            },
            19: {
                "feature_lines": ["Ability Score Improvement"],
            },
            20: {
                "feature_lines": ["Primal Champion"],
            },
        },
    },
    "bard": {
        "display_name": "Bard",
        "hit_die": 8,
        "primary_ability": "cha",
        "save_proficiencies": ["dex", "cha"],
        "proficiencies": {"armor": "Light armor", "weapons": "Simple weapons, hand crossbows, longswords, rapiers, shortswords", "tools": "Three musical instruments of your choice"},
        "ability_ids": ["bard_bardic_inspiration", "bard_spellcasting"],
        "resource_pools": {"bardic_inspiration": {"current": 3, "max": 3, "refresh": "long_rest"}},
        "feature_lines": ["Bardic Inspiration", "Spellcasting"],
        "level_grants": {
            2: {"feature_lines": ["Jack of All Trades", "Song of Rest"]},
            3: {"feature_lines": ["Bard College", "Expertise"]},
            4: {"feature_lines": ["Ability Score Improvement"]},
            5: {"feature_lines": ["Bardic Inspiration (d8)", "Font of Inspiration"]},
            6: {"feature_lines": ["Countercharm", "Bard College Feature"]},
            7: {"feature_lines": ["Spellcasting Improvement"]},
            8: {"feature_lines": ["Ability Score Improvement"]},
            9: {"feature_lines": ["Song of Rest (d8)"]},
            10: {"feature_lines": ["Expertise", "Magical Secrets", "Bardic Inspiration (d10)"]},
            11: {"feature_lines": ["Spellcasting Improvement"]},
            12: {"feature_lines": ["Ability Score Improvement"]},
            13: {"feature_lines": ["Song of Rest (d10)"]},
            14: {"feature_lines": ["Magical Secrets", "Bard College Feature"]},
            15: {"feature_lines": ["Bardic Inspiration (d12)"]},
            16: {"feature_lines": ["Ability Score Improvement"]},
            17: {"feature_lines": ["Song of Rest (d12)"]},
            18: {"feature_lines": ["Magical Secrets"]},
            19: {"feature_lines": ["Ability Score Improvement"]},
            20: {"feature_lines": ["Superior Inspiration"]},
        },
    },
    "cleric": {
        "display_name": "Cleric",
        "hit_die": 8,
        "primary_ability": "wis",
        "save_proficiencies": ["wis", "cha"],
        "proficiencies": {"armor": "Light armor, medium armor, shields", "weapons": "Simple weapons"},
        "ability_ids": ["cleric_spellcasting", "cleric_divine_domain"],
        "feature_lines": ["Spellcasting", "Divine Domain"],
        "level_grants": {
            2: {"feature_lines": ["Channel Divinity (1/rest)"]},
            3: {"feature_lines": ["Spellcasting Improvement"]},
            4: {"feature_lines": ["Ability Score Improvement"]},
            5: {"feature_lines": ["Destroy Undead (CR 1/2)"]},
            6: {"feature_lines": ["Channel Divinity (2/rest)", "Divine Domain Feature"]},
            7: {"feature_lines": ["Spellcasting Improvement"]},
            8: {"feature_lines": ["Ability Score Improvement", "Divine Domain Feature"]},
            9: {"feature_lines": ["Destroy Undead (CR 1)"]},
            10: {"feature_lines": ["Divine Intervention"]},
            11: {"feature_lines": ["Spellcasting Improvement"]},
            12: {"feature_lines": ["Ability Score Improvement"]},
            13: {"feature_lines": ["Destroy Undead (CR 2)"]},
            14: {"feature_lines": ["Divine Domain Feature"]},
            15: {"feature_lines": ["Spellcasting Improvement"]},
            16: {"feature_lines": ["Ability Score Improvement"]},
            17: {"feature_lines": ["Destroy Undead (CR 3)", "Divine Domain Feature"]},
            18: {"feature_lines": ["Channel Divinity (3/rest)"]},
            19: {"feature_lines": ["Ability Score Improvement"]},
            20: {"feature_lines": ["Improved Divine Intervention"]},
        },
    },
    "druid": {
        "display_name": "Druid",
        "hit_die": 8,
        "primary_ability": "wis",
        "save_proficiencies": ["int", "wis"],
        "proficiencies": {"armor": "Light armor, medium armor, shields (nonmetal)", "weapons": "Clubs, daggers, darts, javelins, maces, quarterstaffs, scimitars, sickles, slings, spears"},
        "ability_ids": ["druid_spellcasting", "druid_druidic"],
        "resource_pools": {"wild_shape": {"current": 0, "max": 0, "refresh": "short_rest"}},
        "feature_lines": ["Spellcasting", "Druidic"],
        "level_grants": {
            2: {"feature_lines": ["Wild Shape", "Druid Circle"]},
            3: {"feature_lines": ["Spellcasting Improvement"]},
            4: {"feature_lines": ["Wild Shape Improvement", "Ability Score Improvement"]},
            5: {"feature_lines": ["Spellcasting Improvement"]},
            6: {"feature_lines": ["Druid Circle Feature"]},
            7: {"feature_lines": ["Spellcasting Improvement"]},
            8: {"feature_lines": ["Wild Shape Improvement", "Ability Score Improvement"]},
            9: {"feature_lines": ["Spellcasting Improvement"]},
            10: {"feature_lines": ["Druid Circle Feature"]},
            11: {"feature_lines": ["Spellcasting Improvement"]},
            12: {"feature_lines": ["Ability Score Improvement"]},
            13: {"feature_lines": ["Spellcasting Improvement"]},
            14: {"feature_lines": ["Druid Circle Feature"]},
            15: {"feature_lines": ["Timeless Body"]},
            16: {"feature_lines": ["Ability Score Improvement"]},
            17: {"feature_lines": ["Spellcasting Improvement"]},
            18: {"feature_lines": ["Beast Spells"]},
            19: {"feature_lines": ["Ability Score Improvement"]},
            20: {"feature_lines": ["Archdruid"]},
        },
    },
    "fighter": {
        "display_name": "Fighter",
        "hit_die": 10,
        "primary_ability": "str",
        "save_proficiencies": ["str", "con"],
        "proficiencies": {"armor": "All armor, shields", "weapons": "Simple weapons, martial weapons"},
        "ability_ids": ["fighter_fighting_style", "fighter_second_wind"],
        "resource_pools": {"second_wind": {"current": 1, "max": 1, "refresh": "short_rest"}},
        "feature_lines": ["Fighting Style", "Second Wind"],
        "level_grants": {
            2: {
                "ability_ids": ["fighter_action_surge"],
                "resource_pools": {"action_surge": {"current": 1, "max": 1, "refresh": "short_rest"}},
                "feature_lines": ["Action Surge"],
            },
            3: {
                "feature_lines": ["Martial Archetype"],
            },
            4: {
                "feature_lines": ["Ability Score Improvement"],
            },
            5: {
                "combat": {"attacks_per_action": 2},
                "feature_lines": ["Extra Attack"],
            },
            6: {
                "feature_lines": ["Ability Score Improvement"],
            },
            7: {
                "feature_lines": ["Martial Archetype Feature"],
            },
            8: {
                "feature_lines": ["Ability Score Improvement"],
            },
            9: {
                "ability_ids": ["fighter_indomitable"],
                "resource_pools": {"indomitable": {"current": 1, "max": 1, "refresh": "long_rest"}},
                "feature_lines": ["Indomitable"],
            },
            10: {
                "feature_lines": ["Martial Archetype Feature"],
            },
            11: {
                "combat": {"attacks_per_action": 3},
                "feature_lines": ["Extra Attack (2)"],
            },
            12: {
                "feature_lines": ["Ability Score Improvement"],
            },
            13: {
                "resource_pools": {"indomitable": {"current": 2, "max": 2, "refresh": "long_rest"}},
                "feature_lines": ["Indomitable (2 uses)"],
            },
            14: {
                "feature_lines": ["Ability Score Improvement"],
            },
            15: {
                "feature_lines": ["Martial Archetype Feature"],
            },
            16: {
                "feature_lines": ["Ability Score Improvement"],
            },
            17: {
                "resource_pools": {
                    "action_surge": {"current": 2, "max": 2, "refresh": "short_rest"},
                    "indomitable": {"current": 3, "max": 3, "refresh": "long_rest"}
                },
                "feature_lines": ["Action Surge (2 uses)", "Indomitable (3 uses)"],
            },
            18: {
                "feature_lines": ["Martial Archetype Feature"],
            },
            19: {
                "feature_lines": ["Ability Score Improvement"],
            },
            20: {
                "combat": {"attacks_per_action": 4},
                "feature_lines": ["Extra Attack (3)"],
            },
        },
    },
    "monk": {
        "display_name": "Monk",
        "hit_die": 8,
        "primary_ability": "dex",
        "save_proficiencies": ["str", "dex"],
        "proficiencies": {"weapons": "Simple weapons, shortswords", "tools": "One artisan tool or one musical instrument"},
        "ability_ids": ["monk_unarmored_defense", "monk_martial_arts"],
        "feature_lines": ["Unarmored Defense", "Martial Arts"],
        "level_grants": {
            2: {
                "ability_ids": ["monk_flurry_of_blows", "monk_patient_defense", "monk_step_of_the_wind"],
                "feature_lines": ["Ki", "Unarmored Movement"],
            },
            3: {
                "ability_ids": ["monk_deflect_missiles"],
                "feature_lines": ["Deflect Missiles", "Monastic Tradition"],
            },
            4: {
                "feature_lines": ["Ability Score Improvement", "Slow Fall"],
            },
            5: {
                "ability_ids": ["monk_stunning_strike"],
                "combat": {"attacks_per_action": 2},
                "feature_lines": ["Extra Attack", "Stunning Strike"],
            },
            6: {
                "feature_lines": ["Ki-Empowered Strikes", "Monastic Tradition Feature"],
            },
            7: {
                "ability_ids": ["monk_stillness_of_mind"],
                "feature_lines": ["Evasion", "Stillness of Mind"],
            },
            8: {
                "feature_lines": ["Ability Score Improvement"],
            },
            9: {
                "feature_lines": ["Unarmored Movement Improvement"],
            },
            10: {
                "feature_lines": ["Purity of Body"],
            },
            11: {
                "feature_lines": ["Monastic Tradition Feature"],
            },
            12: {
                "feature_lines": ["Ability Score Improvement"],
            },
            13: {
                "feature_lines": ["Tongue of the Sun and Moon"],
            },
            14: {
                "ability_ids": ["monk_diamond_soul"],
                "feature_lines": ["Diamond Soul"],
            },
            15: {
                "feature_lines": ["Timeless Body"],
            },
            16: {
                "feature_lines": ["Ability Score Improvement"],
            },
            17: {
                "feature_lines": ["Monastic Tradition Feature"],
            },
            18: {
                "ability_ids": ["monk_empty_body"],
                "feature_lines": ["Empty Body"],
            },
            19: {
                "feature_lines": ["Ability Score Improvement"],
            },
            20: {
                "feature_lines": ["Perfect Self"],
            },
        },
    },
    "paladin": {
        "display_name": "Paladin",
        "hit_die": 10,
        "primary_ability": "str",
        "save_proficiencies": ["wis", "cha"],
        "proficiencies": {"armor": "All armor, shields", "weapons": "Simple weapons, martial weapons"},
        "ability_ids": ["paladin_divine_sense", "paladin_lay_on_hands"],
        "resource_pools": {"lay_on_hands": {"current": 5, "max": 5, "refresh": "long_rest"}},
        "feature_lines": ["Divine Sense", "Lay on Hands"],
        "level_grants": {
            2: {
                "feature_lines": ["Fighting Style", "Divine Smite", "Spellcasting"],
            },
            3: {
                "feature_lines": ["Divine Health", "Sacred Oath"],
            },
            4: {
                "feature_lines": ["Ability Score Improvement"],
            },
            5: {
                "combat": {"attacks_per_action": 2},
                "feature_lines": ["Extra Attack"],
            },
            6: {
                "feature_lines": ["Aura of Protection"],
            },
            7: {
                "feature_lines": ["Sacred Oath Feature"],
            },
            8: {
                "feature_lines": ["Ability Score Improvement"],
            },
            9: {
                "feature_lines": ["Spellcasting Improvement"],
            },
            10: {
                "feature_lines": ["Aura of Courage"],
            },
            11: {
                "feature_lines": ["Improved Divine Smite"],
            },
            12: {
                "feature_lines": ["Ability Score Improvement"],
            },
            13: {
                "feature_lines": ["Spellcasting Improvement"],
            },
            14: {
                "ability_ids": ["paladin_cleansing_touch"],
                "feature_lines": ["Cleansing Touch"],
            },
            15: {
                "feature_lines": ["Sacred Oath Feature"],
            },
            16: {
                "feature_lines": ["Ability Score Improvement"],
            },
            17: {
                "feature_lines": ["Spellcasting Improvement"],
            },
            18: {
                "feature_lines": ["Aura Improvements"],
            },
            19: {
                "feature_lines": ["Ability Score Improvement"],
            },
            20: {
                "feature_lines": ["Sacred Oath Feature"],
            },
        },
    },
    "ranger": {
        "display_name": "Ranger",
        "hit_die": 10,
        "primary_ability": "dex",
        "save_proficiencies": ["str", "dex"],
        "proficiencies": {"armor": "Light armor, medium armor, shields", "weapons": "Simple weapons, martial weapons"},
        "ability_ids": ["ranger_favored_enemy", "ranger_natural_explorer"],
        "feature_lines": ["Favored Enemy", "Natural Explorer"],
        "level_grants": {
            2: {"feature_lines": ["Fighting Style", "Spellcasting"]},
            3: {"feature_lines": ["Ranger Archetype", "Primeval Awareness"]},
            4: {"feature_lines": ["Ability Score Improvement"]},
            5: {"combat": {"attacks_per_action": 2}, "feature_lines": ["Extra Attack"]},
            6: {"feature_lines": ["Favored Enemy Improvement", "Natural Explorer Improvement"]},
            7: {"feature_lines": ["Ranger Archetype Feature"]},
            8: {"feature_lines": ["Ability Score Improvement", "Land's Stride"]},
            9: {"feature_lines": ["Spellcasting Improvement"]},
            10: {"feature_lines": ["Natural Explorer Improvement", "Hide in Plain Sight"]},
            11: {"feature_lines": ["Ranger Archetype Feature"]},
            12: {"feature_lines": ["Ability Score Improvement"]},
            13: {"feature_lines": ["Spellcasting Improvement"]},
            14: {"feature_lines": ["Favored Enemy Improvement", "Vanish"]},
            15: {"feature_lines": ["Ranger Archetype Feature"]},
            16: {"feature_lines": ["Ability Score Improvement"]},
            17: {"feature_lines": ["Spellcasting Improvement"]},
            18: {"feature_lines": ["Feral Senses"]},
            19: {"feature_lines": ["Ability Score Improvement"]},
            20: {"feature_lines": ["Foe Slayer"]},
        },
    },
    "rogue": {
        "display_name": "Rogue",
        "hit_die": 8,
        "primary_ability": "dex",
        "save_proficiencies": ["dex", "int"],
        "skill_options": ["acrobatics", "athletics", "deception", "insight", "intimidation", "investigation", "perception", "performance", "persuasion", "sleight_of_hand", "stealth"],
        "proficiencies": {"armor": "Light armor", "weapons": "Simple weapons, hand crossbows, longswords, rapiers, shortswords", "tools": "Thieves' tools"},
        "ability_ids": ["rogue_sneak_attack", "rogue_thieves_cant"],
        "feature_lines": ["Sneak Attack", "Expertise", "Thieves' Cant"],
        "level_grants": {
            2: {
                "ability_ids": ["rogue_cunning_action"],
                "feature_lines": ["Cunning Action"],
            },
            3: {
                "feature_lines": ["Roguish Archetype"],
            },
            4: {
                "feature_lines": ["Ability Score Improvement"],
            },
            5: {
                "ability_ids": ["rogue_uncanny_dodge"],
                "feature_lines": ["Uncanny Dodge"],
            },
            6: {
                "feature_lines": ["Expertise"],
            },
            7: {
                "feature_lines": ["Evasion"],
            },
            8: {
                "feature_lines": ["Ability Score Improvement"],
            },
            9: {
                "feature_lines": ["Roguish Archetype Feature"],
            },
            10: {
                "feature_lines": ["Ability Score Improvement"],
            },
            11: {
                "feature_lines": ["Reliable Talent"],
            },
            12: {
                "feature_lines": ["Ability Score Improvement"],
            },
            13: {
                "feature_lines": ["Roguish Archetype Feature"],
            },
            14: {
                "feature_lines": ["Blindsense"],
            },
            15: {
                "ability_ids": ["rogue_slippery_mind"],
                "feature_lines": ["Slippery Mind"],
            },
            16: {
                "feature_lines": ["Ability Score Improvement"],
            },
            17: {
                "feature_lines": ["Roguish Archetype Feature"],
            },
            18: {
                "feature_lines": ["Elusive"],
            },
            19: {
                "feature_lines": ["Ability Score Improvement"],
            },
            20: {
                "ability_ids": ["rogue_stroke_of_luck"],
                "resource_pools": {"stroke_of_luck": {"current": 1, "max": 1, "refresh": "short_rest"}},
                "feature_lines": ["Stroke of Luck"],
            },
        },
    },
    "sorcerer": {
        "display_name": "Sorcerer",
        "hit_die": 6,
        "primary_ability": "cha",
        "save_proficiencies": ["con", "cha"],
        "proficiencies": {"weapons": "Daggers, darts, slings, quarterstaffs, light crossbows"},
        "ability_ids": ["sorcerer_spellcasting", "sorcerer_origin"],
        "resource_pools": {"sorcery_points": {"current": 0, "max": 0, "refresh": "long_rest"}},
        "feature_lines": ["Spellcasting", "Sorcerous Origin"],
        "level_grants": {
            2: {"feature_lines": ["Font of Magic"]},
            3: {"feature_lines": ["Metamagic"]},
            4: {"feature_lines": ["Ability Score Improvement"]},
            5: {"feature_lines": ["Spellcasting Improvement"]},
            6: {"feature_lines": ["Sorcerous Origin Feature"]},
            7: {"feature_lines": ["Spellcasting Improvement"]},
            8: {"feature_lines": ["Ability Score Improvement"]},
            9: {"feature_lines": ["Spellcasting Improvement"]},
            10: {"feature_lines": ["Metamagic"]},
            11: {"feature_lines": ["Spellcasting Improvement"]},
            12: {"feature_lines": ["Ability Score Improvement"]},
            13: {"feature_lines": ["Spellcasting Improvement"]},
            14: {"feature_lines": ["Sorcerous Origin Feature"]},
            15: {"feature_lines": ["Spellcasting Improvement"]},
            16: {"feature_lines": ["Ability Score Improvement"]},
            17: {"feature_lines": ["Spellcasting Improvement"]},
            18: {"feature_lines": ["Sorcerous Origin Feature"]},
            19: {"feature_lines": ["Ability Score Improvement"]},
            20: {"feature_lines": ["Sorcerous Restoration"]},
        },
    },
    "warlock": {
        "display_name": "Warlock",
        "hit_die": 8,
        "primary_ability": "cha",
        "save_proficiencies": ["wis", "cha"],
        "proficiencies": {"armor": "Light armor", "weapons": "Simple weapons"},
        "ability_ids": ["warlock_otherworldly_patron", "warlock_pact_magic"],
        "resource_pools": {
            "mystic_arcanum_6": {"current": 0, "max": 0, "refresh": "long_rest"},
            "mystic_arcanum_7": {"current": 0, "max": 0, "refresh": "long_rest"},
            "mystic_arcanum_8": {"current": 0, "max": 0, "refresh": "long_rest"},
            "mystic_arcanum_9": {"current": 0, "max": 0, "refresh": "long_rest"}
        },
        "feature_lines": ["Otherworldly Patron", "Pact Magic"],
        "level_grants": {
            2: {"feature_lines": ["Eldritch Invocations"]},
            3: {"feature_lines": ["Pact Boon"]},
            4: {"feature_lines": ["Ability Score Improvement"]},
            5: {"feature_lines": ["Spellcasting Improvement"]},
            6: {"feature_lines": ["Otherworldly Patron Feature"]},
            7: {"feature_lines": ["Spellcasting Improvement"]},
            8: {"feature_lines": ["Ability Score Improvement"]},
            9: {"feature_lines": ["Spellcasting Improvement"]},
            10: {"feature_lines": ["Otherworldly Patron Feature"]},
            11: {"feature_lines": ["Mystic Arcanum (6th level)"]},
            12: {"feature_lines": ["Ability Score Improvement"]},
            13: {"feature_lines": ["Mystic Arcanum (7th level)"]},
            14: {"feature_lines": ["Otherworldly Patron Feature"]},
            15: {"feature_lines": ["Mystic Arcanum (8th level)"]},
            16: {"feature_lines": ["Ability Score Improvement"]},
            17: {"feature_lines": ["Mystic Arcanum (9th level)"]},
            18: {"feature_lines": ["Spellcasting Improvement"]},
            19: {"feature_lines": ["Ability Score Improvement"]},
            20: {"feature_lines": ["Eldritch Master"]},
        },
    },
    "wizard": {
        "display_name": "Wizard",
        "hit_die": 6,
        "primary_ability": "int",
        "save_proficiencies": ["int", "wis"],
        "proficiencies": {"weapons": "Daggers, darts, slings, quarterstaffs, light crossbows"},
        "ability_ids": ["wizard_spellcasting", "wizard_arcane_recovery"],
        "resource_pools": {"arcane_recovery": {"current": 1, "max": 1, "refresh": "long_rest"}},
        "feature_lines": ["Spellcasting", "Arcane Recovery"],
        "level_grants": {
            2: {"feature_lines": ["Arcane Tradition"]},
            3: {"feature_lines": ["Spellcasting Improvement"]},
            4: {"feature_lines": ["Ability Score Improvement"]},
            5: {"feature_lines": ["Spellcasting Improvement"]},
            6: {"feature_lines": ["Arcane Tradition Feature"]},
            7: {"feature_lines": ["Spellcasting Improvement"]},
            8: {"feature_lines": ["Ability Score Improvement"]},
            9: {"feature_lines": ["Spellcasting Improvement"]},
            10: {"feature_lines": ["Arcane Tradition Feature"]},
            11: {"feature_lines": ["Spellcasting Improvement"]},
            12: {"feature_lines": ["Ability Score Improvement"]},
            13: {"feature_lines": ["Spellcasting Improvement"]},
            14: {"feature_lines": ["Arcane Tradition Feature"]},
            15: {"feature_lines": ["Spellcasting Improvement"]},
            16: {"feature_lines": ["Ability Score Improvement"]},
            17: {"feature_lines": ["Spellcasting Improvement"]},
            18: {"feature_lines": ["Spell Mastery"]},
            19: {"feature_lines": ["Ability Score Improvement"]},
            20: {"feature_lines": ["Signature Spells"]},
        },
    },
}

RACE_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "human": {
        "display_name": "Human",
        "trait_ids": ["human_bonus_skill"],
        "feature_lines": ["Gain proficiency in one additional skill of your choice."],
    },
    "gottish human": {
        "display_name": "Gottish Human",
        "trait_ids": ["gottish_human_adaptable_workforce", "gottish_human_hardy_laborers"],
        "languages": [{"name": "Gottish", "speak": True, "read": True, "write": True}],
        "ability_bonuses": {"choice_any_3": 1},
        "feature_lines": ["Adaptable Workforce", "Hardy Laborers"],
    },
    "imperial human": {
        "display_name": "Imperial Human",
        "trait_ids": ["imperial_human_imperial_education", "imperial_human_diplomatic_presence"],
        "languages": [{"name": "Imperial", "speak": True, "read": True, "write": True}],
        "ability_bonuses": {"cha": 2, "wis": 1},
        "feature_lines": ["Imperial Education", "Diplomatic Presence"],
    },
    "shiman human": {
        "display_name": "Shiman Human",
        "trait_ids": ["shiman_human_island_sailors", "shiman_human_disciplined_mind"],
        "languages": [{"name": "Shiman", "speak": True, "read": True, "write": True}],
        "ability_bonuses": {"dex": 2, "wis": 1},
        "feature_lines": ["Island Sailors", "Disciplined Mind"],
    },
    "elf": {
        "display_name": "Elf",
        "trait_ids": ["elf_darkvision", "elf_trance", "elf_keen_senses"],
        "languages": [{"name": "Elvish", "speak": True, "read": True, "write": True}],
        "ability_bonuses": {"dex": 2},
        "feature_lines": ["Darkvision", "Trance", "Keen Senses"],
        "skill_proficiencies": ["perception"],
        "vision_ft": 60,
    },
    "sun elf": {
        "base_race": "elf",
        "display_name": "Sun Elf",
        "trait_ids": ["sun_elf_solar_affinity", "sun_elf_radiant_lineage"],
        "ability_bonuses": {"int": 1},
        "feature_lines": ["Solar Affinity", "Radiant Lineage"],
        "damage_resistances": ["radiant"],
    },
    "moon elf": {
        "base_race": "elf",
        "display_name": "Moon Elf",
        "trait_ids": ["moon_elf_lunar_affinity", "moon_elf_night_attunement"],
        "ability_bonuses": {"wis": 1},
        "feature_lines": ["Lunar Affinity", "Night Attunement"],
        "vision_ft": 120,
    },
    "wood elf": {
        "base_race": "elf",
        "display_name": "Wood Elf",
        "trait_ids": ["wood_elf_forest_affinity", "wood_elf_natures_veil"],
        "ability_bonuses": {"wis": 1},
        "feature_lines": ["Forest Affinity", "Nature's Veil"],
        "movement_ft": 40,
    },
    "dwarf": {
        "display_name": "Dwarf",
        "trait_ids": ["dwarf_darkvision", "dwarf_stonecraft", "dwarf_poison_resilience"],
        "languages": [{"name": "Dwarvish", "speak": True, "read": True, "write": True}],
        "ability_bonuses": {"con": 2},
        "feature_lines": ["Darkvision", "Stonecraft", "Poison Resilience"],
        "vision_ft": 60,
    },
    "deep dwarf": {
        "base_race": "dwarf",
        "display_name": "Deep Dwarf",
        "trait_ids": ["deep_dwarf_deepvision", "deep_dwarf_stone_endurance", "deep_dwarf_subterranean_instinct", "deep_dwarf_light_sensitivity_minor"],
        "ability_bonuses": {"str": 1},
        "feature_lines": ["Deepvision", "Stone Endurance", "Subterranean Instinct", "Light Sensitivity (Minor)"],
        "vision_ft": 120,
        "resource_pools": {"stone_endurance": {"current": 1, "max": 1, "refresh": "long_rest"}},
    },
    "shallow dwarf": {
        "base_race": "dwarf",
        "display_name": "Shallow Dwarf",
        "trait_ids": ["shallow_dwarf_urban_stonecraft", "shallow_dwarf_tunnel_fighter", "shallow_dwarf_surface_adaptation"],
        "ability_bonuses": {"str": 1},
        "feature_lines": ["Urban Stonecraft", "Tunnel Fighter", "Surface Adaptation"],
    },
    "surface dwarf": {
        "base_race": "dwarf",
        "display_name": "Surface Dwarf",
        "trait_ids": ["surface_dwarf_merchants_mind", "surface_dwarf_trade_networks", "surface_dwarf_traveler_of_the_roads"],
        "ability_bonuses": {"cha": 1},
        "feature_lines": ["Merchant's Mind", "Trade Networks", "Traveler of the Roads"],
    },
    "dramau": {
        "display_name": "Dramau",
        "trait_ids": ["dramau_darkvision", "dramau_scaled_hide", "dramau_draconic_claws", "dramau_draconic_presence"],
        "languages": [{"name": "Draconic", "speak": True, "read": True, "write": True}],
        "ability_bonuses": {"con": 2},
        "feature_lines": ["Darkvision", "Scaled Hide", "Draconic Claws", "Draconic Presence"],
        "vision_ft": 60,
        "base_ac_formula": "13 + DEX (when not wearing heavy armor)",
    },
    "fire dramau": {
        "base_race": "dramau",
        "display_name": "Fire Dramau",
        "trait_ids": ["fire_dramau_fire_resistance", "fire_dramau_ember_breath"],
        "ability_bonuses": {"str": 1},
        "feature_lines": ["Fire Resistance", "Ember Breath"],
        "damage_resistances": ["fire"],
        "resource_pools": {"ember_breath": {"current": 1, "max": 1, "refresh": "long_rest"}},
    },
    "frost dramau": {
        "base_race": "dramau",
        "display_name": "Frost Dramau",
        "trait_ids": ["frost_dramau_cold_resistance", "frost_dramau_rime_breath"],
        "ability_bonuses": {"wis": 1},
        "feature_lines": ["Cold Resistance", "Rime Breath"],
        "damage_resistances": ["cold"],
        "resource_pools": {"rime_breath": {"current": 1, "max": 1, "refresh": "long_rest"}},
    },
    "shadow dramau": {
        "base_race": "dramau",
        "display_name": "Shadow Dramau",
        "trait_ids": ["shadow_dramau_shadow_resistance", "shadow_dramau_umbral_sight", "shadow_dramau_shadow_breath"],
        "ability_bonuses": {"dex": 1},
        "feature_lines": ["Shadow Resistance", "Umbral Sight", "Shadow Breath"],
        "vision_ft": 120,
        "resource_pools": {"shadow_breath": {"current": 1, "max": 1, "refresh": "long_rest"}},
    },
    "metallic dramau": {
        "base_race": "dramau",
        "display_name": "Metallic Dramau",
        "trait_ids": ["metallic_dramau_metallic_resistance", "metallic_dramau_commanding_voice", "metallic_dramau_metallic_breath"],
        "ability_bonuses": {"cha": 1},
        "feature_lines": ["Metallic Resistance", "Commanding Voice", "Metallic Breath"],
        "resource_pools": {"metallic_breath": {"current": 1, "max": 1, "refresh": "long_rest"}},
    },
    "stormen": {
        "display_name": "Stormen",
        "trait_ids": ["stormen_mountain_born", "stormen_giant_blooded_frame", "stormen_stone_strider", "stormen_honored_challenge"],
        "languages": [{"name": "Stormen", "speak": True, "read": True, "write": True}],
        "ability_bonuses": {"str": 2, "con": 1},
        "feature_lines": ["Mountain Born", "Giant-Blooded Frame", "Stone Strider", "Honored Challenge"],
        "damage_resistances": ["cold"],
        "resource_pools": {"honored_challenge": {"current": 1, "max": 1, "refresh": "long_rest"}},
    },
}


def _slug_key(value: str) -> str:
    return " ".join(str(value or "").strip().lower().replace("-", " ").split())


def _merge_string_csv(a: str, b: str) -> str:
    vals = []
    seen = set()
    for raw in [a, b]:
        for part in str(raw or "").split(","):
            item = part.strip()
            key = item.lower()
            if item and key not in seen:
                seen.add(key)
                vals.append(item)
    return ", ".join(vals)


def _merge_langs(existing: Any, additions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    seen = {}
    for src in [existing if isinstance(existing, list) else [], additions or []]:
        for lang in src:
            if not isinstance(lang, dict):
                continue
            name = str(lang.get("name") or "").strip()
            if not name:
                continue
            key = name.lower()
            cur = seen.get(key, {"name": name, "speak": False, "read": False, "write": False})
            cur["speak"] = bool(cur.get("speak") or lang.get("speak"))
            cur["read"] = bool(cur.get("read") or lang.get("read"))
            cur["write"] = bool(cur.get("write") or lang.get("write"))
            seen[key] = cur
    out.extend(seen.values())
    out.sort(key=lambda x: str(x.get("name") or "").lower())
    return out


def _merge_unique_list(existing: Any, additions: Any) -> List[str]:
    out = []
    seen = set()
    for src in [existing if isinstance(existing, list) else [], additions if isinstance(additions, list) else []]:
        for item in src:
            val = str(item or "").strip()
            if not val:
                continue
            key = val.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(val)
    return out


def _calc_default_max_hp(sheet: Dict[str, Any], class_template: Dict[str, Any], level: int) -> int:
    abilities = sheet.get("abilities", {}) if isinstance(sheet.get("abilities"), dict) else {}
    con_mod = max(-5, min(10, (int(abilities.get("con", 10) or 10) - 10) // 2))
    hit_die = int(class_template.get("hit_die", 8) or 8)
    lvl = max(1, int(level or 1))
    if lvl <= 1:
        return max(1, hit_die + con_mod)
    avg_gain = max(1, ((hit_die // 2) + 1) + con_mod)
    return max(1, (hit_die + con_mod) + ((lvl - 1) * avg_gain))


def _apply_ability_bonuses(sheet: Dict[str, Any], bonuses: Dict[str, Any]) -> None:
    abilities = sheet.setdefault("abilities", {})
    if not isinstance(abilities, dict):
        abilities = {}
        sheet["abilities"] = abilities
    for key, bonus in (bonuses or {}).items():
        k = str(key or "").strip().lower()
        if k == "choice_any_3":
            continue
        if k in ("str", "dex", "con", "int", "wis", "cha"):
            try:
                abilities[k] = int(abilities.get(k, 10) or 10) + int(bonus or 0)
            except Exception:
                continue


def _resolve_race_template(race_name: str) -> Tuple[str, Dict[str, Any], Dict[str, Any]]:
    key = _slug_key(race_name)
    tpl = dict(RACE_TEMPLATES.get(key, {}))
    base = {}
    base_key = _slug_key(tpl.get("base_race", ""))
    if base_key:
        base = dict(RACE_TEMPLATES.get(base_key, {}))
    return key, base, tpl




def _load_items_db(campaign_id: str) -> Dict[str, Any]:
    st = get_state(campaign_id)
    path = str(st.get("items_path", "") or "").strip()
    if not path:
        path = os.path.join(CAMPAIGNS_ROOT, campaign_id, "items.json")
    return _read_json(path, {}) if path else {}


def load_spells_db_for_campaign(campaign_id: str) -> Dict[str, Dict[str, Any]]:
    st = get_state(campaign_id)
    path = os.path.join(str(st.get("campaign_path", "") or ""), "spells.json")
    try:
        data = load_spells_db(path)
    except Exception:
        data = {}
    return data if isinstance(data, dict) else {}


SPELLCASTER_CANTRIPS_BY_LEVEL: Dict[str, Dict[int, int]] = {
    "bard": {1: 2, 4: 3, 10: 4},
    "cleric": {1: 3, 4: 4, 10: 5},
    "druid": {1: 2, 4: 3, 10: 4},
    "sorcerer": {1: 4, 4: 5, 10: 6},
    "warlock": {1: 2, 4: 3, 10: 4},
    "wizard": {1: 3, 4: 4, 10: 5},
}

KNOWN_SPELLS_BY_LEVEL: Dict[str, Dict[int, int]] = {
    "bard": {1: 4, 2: 5, 3: 6, 4: 7, 5: 8, 6: 9, 7: 10, 8: 11, 9: 12, 10: 14, 11: 15, 12: 15, 13: 16, 14: 18, 15: 19, 16: 19, 17: 20, 18: 22, 19: 22, 20: 22},
    "sorcerer": {1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10, 10: 11, 11: 12, 12: 12, 13: 13, 14: 13, 15: 14, 16: 14, 17: 15, 18: 15, 19: 15, 20: 15},
    "warlock": {1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10, 10: 10, 11: 11, 12: 11, 13: 12, 14: 12, 15: 13, 16: 13, 17: 14, 18: 14, 19: 15, 20: 15},
    "ranger": {2: 2, 3: 3, 4: 3, 5: 4, 6: 4, 7: 5, 8: 5, 9: 6, 10: 6, 11: 7, 12: 7, 13: 8, 14: 8, 15: 9, 16: 9, 17: 10, 18: 10, 19: 11, 20: 11},
}


def _progression_value(table: Dict[int, int], level: int) -> int:
    lvl = max(0, int(level or 0))
    best = 0
    for key in sorted(int(k) for k in table.keys()):
        if lvl >= key:
            best = int(table.get(key, best) or best)
        else:
            break
    return best


def _spellcaster_cantrip_limit(class_key: str, level: int) -> int:
    tbl = SPELLCASTER_CANTRIPS_BY_LEVEL.get(str(class_key or "").strip().lower(), {})
    return _progression_value(tbl, level) if tbl else 0


def _spellcaster_known_limit(class_key: str, level: int) -> int:
    tbl = KNOWN_SPELLS_BY_LEVEL.get(str(class_key or "").strip().lower(), {})
    return _progression_value(tbl, level) if tbl else 0


FULL_CASTER_SLOTS_BY_LEVEL: Dict[int, Dict[int, int]] = {
    1: {1: 2},
    2: {1: 3},
    3: {1: 4, 2: 2},
    4: {1: 4, 2: 3},
    5: {1: 4, 2: 3, 3: 2},
    6: {1: 4, 2: 3, 3: 3},
    7: {1: 4, 2: 3, 3: 3, 4: 1},
    8: {1: 4, 2: 3, 3: 3, 4: 2},
    9: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    10: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    11: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
    12: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1},
    13: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    14: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    16: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1},
    17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2, 6: 1, 7: 1, 8: 1, 9: 1},
    18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 1, 7: 1, 8: 1, 9: 1},
    19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 1, 8: 1, 9: 1},
    20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 3, 6: 2, 7: 2, 8: 1, 9: 1},
}

HALF_CASTER_SLOTS_BY_LEVEL: Dict[int, Dict[int, int]] = {
    1: {},
    2: {1: 2},
    3: {1: 3},
    4: {1: 3},
    5: {1: 4, 2: 2},
    6: {1: 4, 2: 2},
    7: {1: 4, 2: 3},
    8: {1: 4, 2: 3},
    9: {1: 4, 2: 3, 3: 2},
    10: {1: 4, 2: 3, 3: 2},
    11: {1: 4, 2: 3, 3: 3},
    12: {1: 4, 2: 3, 3: 3},
    13: {1: 4, 2: 3, 3: 3, 4: 1},
    14: {1: 4, 2: 3, 3: 3, 4: 1},
    15: {1: 4, 2: 3, 3: 3, 4: 2},
    16: {1: 4, 2: 3, 3: 3, 4: 2},
    17: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    18: {1: 4, 2: 3, 3: 3, 4: 3, 5: 1},
    19: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
    20: {1: 4, 2: 3, 3: 3, 4: 3, 5: 2},
}

WARLOCK_PACT_SLOTS_BY_LEVEL: Dict[int, Dict[str, int]] = {
    1: {"slot_level": 1, "slots": 1},
    2: {"slot_level": 1, "slots": 2},
    3: {"slot_level": 2, "slots": 2},
    4: {"slot_level": 2, "slots": 2},
    5: {"slot_level": 3, "slots": 2},
    6: {"slot_level": 3, "slots": 2},
    7: {"slot_level": 4, "slots": 2},
    8: {"slot_level": 4, "slots": 2},
    9: {"slot_level": 5, "slots": 2},
    10: {"slot_level": 5, "slots": 2},
    11: {"slot_level": 5, "slots": 3},
    12: {"slot_level": 5, "slots": 3},
    13: {"slot_level": 5, "slots": 3},
    14: {"slot_level": 5, "slots": 3},
    15: {"slot_level": 5, "slots": 3},
    16: {"slot_level": 5, "slots": 3},
    17: {"slot_level": 5, "slots": 4},
    18: {"slot_level": 5, "slots": 4},
    19: {"slot_level": 5, "slots": 4},
    20: {"slot_level": 5, "slots": 4},
}

SPELLCASTING_CLASS_RULES: Dict[str, Dict[str, Any]] = {
    "wizard": {"ability": "int", "progression": "full", "known_mode": "prepared", "spellbook": True, "min_level": 1, "label": "Wizard"},
    "cleric": {"ability": "wis", "progression": "full", "known_mode": "prepared", "spellbook": False, "min_level": 1, "label": "Cleric"},
    "druid": {"ability": "wis", "progression": "full", "known_mode": "prepared", "spellbook": False, "min_level": 1, "label": "Druid"},
    "bard": {"ability": "cha", "progression": "full", "known_mode": "known", "spellbook": False, "min_level": 1, "label": "Bard"},
    "sorcerer": {"ability": "cha", "progression": "full", "known_mode": "known", "spellbook": False, "min_level": 1, "label": "Sorcerer"},
    "warlock": {"ability": "cha", "progression": "pact", "known_mode": "known", "spellbook": False, "min_level": 1, "label": "Warlock"},
    "ranger": {"ability": "wis", "progression": "half", "known_mode": "prepared", "spellbook": False, "min_level": 2, "label": "Ranger"},
    "paladin": {"ability": "cha", "progression": "half", "known_mode": "prepared", "spellbook": False, "min_level": 2, "label": "Paladin"},
}

SPELL_CLASS_FALLBACKS: Dict[str, Set[str]] = {
    "fire_bolt": {"wizard", "sorcerer", "artificer"},
    "magic_missile": {"wizard", "sorcerer"},
    "sacred_flame": {"cleric"},
    "cure_wounds": {"bard", "cleric", "druid", "paladin", "ranger"},
    "bless": {"cleric", "paladin"},
    "false_life": {"wizard", "warlock", "sorcerer"},
    "lesser_restoration": {"bard", "cleric", "druid", "paladin", "ranger"},
    "shield": {"wizard", "sorcerer"},
    "hellish_rebuke": {"warlock"},
    "longstrider": {"bard", "druid", "ranger", "wizard"},
    "light": {"bard", "cleric", "sorcerer", "wizard"},
    "mage_armor": {"wizard", "sorcerer"},
    "shield_of_faith": {"cleric", "paladin"},
    "hold_person": {"bard", "cleric", "druid", "sorcerer", "warlock", "wizard"},
}


SPELL_CLASS_METADATA_KEYS: Tuple[str, ...] = (
    "classes",
    "class_list",
    "class_lists",
    "spell_lists",
    "available_to",
    "caster_classes",
)

METAMAGIC_OPTION_COSTS: Dict[str, int] = {
    "careful_spell": 1,
    "distant_spell": 1,
    "empowered_spell": 1,
    "extended_spell": 1,
    "heightened_spell": 3,
    "quickened_spell": 2,
    "seeking_spell": 2,
    "subtle_spell": 1,
    "transmuted_spell": 1,
    "twinned_spell": 1,
}

METAMAGIC_OPTION_LABELS: Dict[str, str] = {
    "careful_spell": "Careful Spell",
    "distant_spell": "Distant Spell",
    "empowered_spell": "Empowered Spell",
    "extended_spell": "Extended Spell",
    "heightened_spell": "Heightened Spell",
    "quickened_spell": "Quickened Spell",
    "seeking_spell": "Seeking Spell",
    "subtle_spell": "Subtle Spell",
    "transmuted_spell": "Transmuted Spell",
    "twinned_spell": "Twinned Spell",
}


def _spell_listify(value: Any) -> List[str]:
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
    return []


def _spell_unique(items: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for item in items:
        s = str(item or "").strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out



def _spell_id_from_row(spell_row: Dict[str, Any]) -> str:
    return str((spell_row or {}).get("spell_id") or (spell_row or {}).get("id") or "").strip()


def _spell_level_from_row(spell_row: Dict[str, Any]) -> int:
    try:
        return max(0, int((spell_row or {}).get("level", 0) or 0))
    except Exception:
        return 0


def _coerce_class_name_token(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return ""
    raw = raw.replace("/", ",")
    raw = raw.replace(";", ",")
    raw = raw.replace("|", ",")
    raw = raw.replace(" and ", ",")
    raw = raw.replace(" spell list", "")
    raw = raw.replace(" spells", "")
    raw = raw.replace(" spell", "")
    raw = raw.strip()
    if raw in SPELLCASTING_CLASS_RULES:
        return raw
    return _slug_key(raw)


def _extract_spell_classes(spell_row: Dict[str, Any]) -> Set[str]:
    out: Set[str] = set()
    row = spell_row if isinstance(spell_row, dict) else {}
    for key in SPELL_CLASS_METADATA_KEYS:
        val = row.get(key)
        if isinstance(val, dict):
            for subk, subv in val.items():
                ck = _coerce_class_name_token(subk)
                if ck in SPELLCASTING_CLASS_RULES and bool(subv):
                    out.add(ck)
        elif isinstance(val, (list, tuple, set)):
            for item in val:
                ck = _coerce_class_name_token(item)
                if ck in SPELLCASTING_CLASS_RULES:
                    out.add(ck)
        elif isinstance(val, str):
            for part in val.replace("\n", ",").split(","):
                ck = _coerce_class_name_token(part)
                if ck in SPELLCASTING_CLASS_RULES:
                    out.add(ck)
    sid = _spell_id_from_row(row)
    if sid and not out:
        out.update(SPELL_CLASS_FALLBACKS.get(sid, set()))
    return out


def _spell_allowed_for_any_class(spell_row: Dict[str, Any], active_classes: List[str]) -> bool:
    classes = _extract_spell_classes(spell_row)
    if not classes:
        return True
    active = {str(c or "").strip().lower() for c in (active_classes or []) if str(c or "").strip()}
    return bool(classes & active)


def _sanitize_generic_spell_ids(campaign_id: str, raw_ids: List[str], *, require_cantrip: bool = False, require_leveled: bool = False) -> List[str]:
    spells_db = load_spells_db_for_campaign(campaign_id) if campaign_id else {}
    out: List[str] = []
    seen: Set[str] = set()
    for raw in raw_ids:
        sid = str(raw or "").strip()
        if not sid or sid in seen:
            continue
        row = (spells_db or {}).get(sid) if isinstance(spells_db, dict) else None
        if not isinstance(row, dict):
            continue
        level = _spell_level_from_row(row)
        if require_cantrip and level > 0:
            continue
        if require_leveled and level <= 0:
            continue
        seen.add(sid)
        out.append(sid)
    return out


def _sanitize_metamagic_option_ids(raw_ids: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for raw in raw_ids:
        key = _slug_key(raw)
        if key in METAMAGIC_OPTION_COSTS and key not in seen:
            seen.add(key)
            out.append(key)
    return out


def _resource_pool_state(sheet: Dict[str, Any], pool_name: str) -> Dict[str, Any]:
    pools = sheet.setdefault("resource_pools", {}) if isinstance(sheet.get("resource_pools"), dict) else {}
    sheet["resource_pools"] = pools
    pool = pools.setdefault(str(pool_name or "").strip(), {}) if isinstance(pools.get(str(pool_name or "").strip()), dict) else {}
    pools[str(pool_name or "").strip()] = pool
    pool.setdefault("current", 0)
    pool.setdefault("max", 0)
    pool.setdefault("refresh", "long_rest")
    return pool


def _spend_resource_pool(sheet: Dict[str, Any], pool_name: str, amount: int) -> Tuple[bool, Dict[str, Any]]:
    pool = _resource_pool_state(sheet, pool_name)
    spend = max(0, int(amount or 0))
    current = max(0, int(pool.get("current", 0) or 0))
    maximum = max(0, int(pool.get("max", current) or current))
    if spend > current:
        return False, {"pool": str(pool_name or ""), "current": current, "max": maximum, "requested": spend}
    pool["current"] = max(0, current - spend)
    pool["max"] = maximum
    return True, {"pool": str(pool_name or ""), "current": int(pool.get("current", 0) or 0), "max": maximum, "spent": spend}


def _bard_magical_secrets_limit(level: int) -> int:
    lvl = max(0, int(level or 0))
    total = 0
    if lvl >= 10:
        total += 2
    if lvl >= 14:
        total += 2
    if lvl >= 18:
        total += 2
    return total


def _metamagic_choice_limit(level: int) -> int:
    lvl = max(0, int(level or 0))
    if lvl >= 17:
        return 4
    if lvl >= 10:
        return 3
    if lvl >= 3:
        return 2
    return 0


def _metamagic_total_cost(option_ids: List[str], spell_level: int) -> int:
    total = 0
    lvl = max(0, int(spell_level or 0))
    for oid in _sanitize_metamagic_option_ids(option_ids):
        if oid == "twinned_spell":
            total += max(1, lvl)
        else:
            total += max(0, int(METAMAGIC_OPTION_COSTS.get(oid, 0) or 0))
    return total


def _sanitize_spell_id_lists_for_sheet(
    campaign_id: str,
    active_classes: List[str],
    cantrips: List[str],
    known: List[str],
    prepared: List[str],
    spellbook: List[str],
    *,
    class_levels: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], List[str], List[str], List[str], Dict[str, Any]]:
    spells_db = load_spells_db_for_campaign(campaign_id) if campaign_id else {}
    active = [str(c or "").strip().lower() for c in (active_classes or []) if str(c or "").strip()]
    active_set = set(active)
    class_levels = class_levels if isinstance(class_levels, dict) else {}
    diagnostics: Dict[str, Any] = {
        "invalid_ids_removed": [],
        "class_filtered_removed": [],
        "allowed_spell_ids": [],
        "allowed_cantrip_ids": [],
        "allowed_leveled_spell_ids": [],
    }

    caps = _active_class_spell_level_caps(class_levels, active)
    allowed_ids: Set[str] = set()
    allowed_cantrips: Set[str] = set()
    allowed_leveled: Set[str] = set()
    diagnostics["level_filtered_removed"] = []
    if isinstance(spells_db, dict):
        for sid, row in spells_db.items():
            sid = str(sid or "").strip()
            if not sid or not isinstance(row, dict):
                continue
            level = _spell_level_from_row(row)
            spell_classes = _extract_spell_classes(row)
            if spell_classes and active_set and not (spell_classes & active_set):
                continue
            if spell_classes and active_set:
                allowed_for_level = False
                for class_key in (spell_classes & active_set):
                    if level <= int(caps.get(class_key, 0) or 0):
                        allowed_for_level = True
                        break
                if not allowed_for_level:
                    continue
            allowed_ids.add(sid)
            if level <= 0:
                allowed_cantrips.add(sid)
            else:
                allowed_leveled.add(sid)

    def _sanitize(raw_ids: List[str], *, require_cantrip: bool = False, require_leveled: bool = False) -> List[str]:
        out: List[str] = []
        seen: Set[str] = set()
        for raw in raw_ids:
            sid = str(raw or "").strip()
            if not sid or sid in seen:
                continue
            row = (spells_db or {}).get(sid) if isinstance(spells_db, dict) else None
            if not isinstance(row, dict):
                diagnostics["invalid_ids_removed"].append(sid)
                continue
            level = _spell_level_from_row(row)
            if require_cantrip and level > 0:
                diagnostics["class_filtered_removed"].append(sid)
                continue
            if require_leveled and level <= 0:
                diagnostics["class_filtered_removed"].append(sid)
                continue
            spell_classes = _extract_spell_classes(row)
            if spell_classes and active_set and not (spell_classes & active_set):
                diagnostics["class_filtered_removed"].append(sid)
                continue
            if spell_classes and active_set and not _spell_allowed_for_sheet_level(row, active, class_levels):
                diagnostics["level_filtered_removed"].append(sid)
                continue
            seen.add(sid)
            out.append(sid)
        return out

    cantrips = _sanitize(cantrips, require_cantrip=True)
    known = _sanitize(known, require_leveled=True)
    prepared = _sanitize(prepared, require_leveled=True)
    spellbook = _sanitize(spellbook, require_leveled=True)

    diagnostics["invalid_ids_removed"] = _spell_unique(diagnostics["invalid_ids_removed"])
    diagnostics["class_filtered_removed"] = _spell_unique(diagnostics["class_filtered_removed"])
    diagnostics["level_filtered_removed"] = _spell_unique(diagnostics["level_filtered_removed"])
    diagnostics["allowed_spell_ids"] = sorted(allowed_ids)
    diagnostics["allowed_cantrip_ids"] = sorted(allowed_cantrips)
    diagnostics["allowed_leveled_spell_ids"] = sorted(allowed_leveled)
    return cantrips, known, prepared, spellbook, diagnostics


def _spellcaster_ability_mod(sheet: Dict[str, Any], ability_key: str) -> int:
    abilities = sheet.get("abilities") if isinstance(sheet.get("abilities"), dict) else {}
    score = int(abilities.get(str(ability_key or "").strip().lower(), 10) or 10)
    return (score - 10) // 2


def _set_spell_slot_row(sc: Dict[str, Any], level: int, total: int, refresh: str, *, source: str = "") -> None:
    spells = sc.setdefault("spells", {}) if isinstance(sc.get("spells"), dict) else {}
    sc["spells"] = spells
    row = spells.setdefault(str(int(level)), {}) if isinstance(spells.get(str(int(level))), dict) else {}
    spells[str(int(level))] = row
    prev_used = int(row.get("used", 0) or 0)
    total = max(0, int(total or 0))
    row["total"] = total
    row["used"] = max(0, min(prev_used, total))
    row["remaining"] = max(0, total - int(row.get("used", 0) or 0))
    row["refresh"] = str(refresh or "long_rest").strip().lower() or "long_rest"
    if source:
        row["source"] = str(source)
    elif "source" in row:
        row.pop("source", None)
    row.setdefault("list", "")


def _clear_spell_rows(sc: Dict[str, Any], *, keep_used: bool = False) -> None:
    spells = sc.setdefault("spells", {}) if isinstance(sc.get("spells"), dict) else {}
    sc["spells"] = spells
    for level in range(1, 10):
        key = str(level)
        row = spells.setdefault(key, {}) if isinstance(spells.get(key), dict) else {}
        spells[key] = row
        used = int(row.get("used", 0) or 0) if keep_used else 0
        row["total"] = 0
        row["used"] = max(0, min(used, 0))
        row["remaining"] = 0
        row["refresh"] = "long_rest"
        row.pop("source", None)
        row.setdefault("list", "")


def _derive_full_caster_slots(caster_level: int) -> Dict[int, int]:
    lvl = max(0, min(int(caster_level or 0), 20))
    return dict(FULL_CASTER_SLOTS_BY_LEVEL.get(lvl, {}))


def _derive_half_caster_slots(caster_level: int) -> Dict[int, int]:
    lvl = max(0, min(int(caster_level or 0), 20))
    return dict(HALF_CASTER_SLOTS_BY_LEVEL.get(lvl, {}))


def _derive_warlock_pact_slots(warlock_level: int) -> Dict[str, int]:
    lvl = max(0, min(int(warlock_level or 0), 20))
    return dict(WARLOCK_PACT_SLOTS_BY_LEVEL.get(lvl, {"slot_level": 0, "slots": 0}))


def _normalize_slot_source(value: Any) -> str:
    src = str(value or "auto").strip().lower()
    return src if src in {"auto", "shared", "pact", "arcanum"} else "auto"


def _get_pact_magic_state(sc: Dict[str, Any]) -> Dict[str, Any]:
    pm = sc.setdefault("pact_magic", {}) if isinstance(sc.get("pact_magic"), dict) else {}
    sc["pact_magic"] = pm
    pm.setdefault("enabled", False)
    pm.setdefault("slot_level", 0)
    pm.setdefault("slots_total", 0)
    pm.setdefault("refresh", "short_rest")
    pm.setdefault("used", 0)
    pm.setdefault("remaining", 0)
    pm.setdefault("mixed_with_other_slots", False)
    return pm


def _recompute_pact_magic_remaining(sc: Dict[str, Any]) -> Dict[str, Any]:
    pm = _get_pact_magic_state(sc)
    total = max(0, int(pm.get("slots_total", 0) or 0))
    used = max(0, min(int(pm.get("used", 0) or 0), total))
    pm["used"] = used
    pm["remaining"] = max(0, total - used)
    return pm


def _can_consume_pact_magic(sheet: Dict[str, Any], slot_level: int, count: int = 1) -> Tuple[bool, str, Dict[str, Any]]:
    sc = ensure_spellcasting_foundation(sheet).get("spellcasting", {})
    pm = _recompute_pact_magic_remaining(sc)
    level = max(0, int(slot_level or 0))
    count = max(1, int(count or 1))
    pact_level = max(0, int(pm.get("slot_level", 0) or 0))
    total = max(0, int(pm.get("slots_total", 0) or 0))
    used = max(0, int(pm.get("used", 0) or 0))
    remaining = max(0, total - used)
    if level <= 0:
        return True, "cantrip", {"level": 0, "total": 0, "used": 0, "remaining": 999, "slot_source": "pact"}
    if not bool(pm.get("enabled")):
        return False, "No pact magic slots available", {"level": level, "total": total, "used": used, "remaining": remaining, "slot_source": "pact"}
    if level != pact_level:
        return False, f"Pact magic only provides level {pact_level} slots", {"level": pact_level, "total": total, "used": used, "remaining": remaining, "slot_source": "pact"}
    if remaining < count:
        return False, f"Not enough pact magic slots of level {pact_level}", {"level": pact_level, "total": total, "used": used, "remaining": remaining, "slot_source": "pact"}
    return True, "ok", {"level": pact_level, "total": total, "used": used, "remaining": remaining, "slot_source": "pact"}


def _consume_pact_magic(sheet: Dict[str, Any], slot_level: int, count: int = 1) -> Tuple[bool, str, Dict[str, Any]]:
    ok, msg, state = _can_consume_pact_magic(sheet, slot_level, count=count)
    if not ok:
        return False, msg, state
    if int(slot_level or 0) <= 0:
        return True, msg, state
    sc = ensure_spellcasting_foundation(sheet).get("spellcasting", {})
    pm = _get_pact_magic_state(sc)
    total = max(0, int(pm.get("slots_total", 0) or 0))
    pm["used"] = min(total, max(0, int(pm.get("used", 0) or 0)) + max(1, int(count or 1)))
    pm["remaining"] = max(0, total - int(pm.get("used", 0) or 0))
    return True, "consumed", {"level": max(0, int(pm.get("slot_level", 0) or 0)), "total": total, "used": int(pm.get("used", 0) or 0), "remaining": int(pm.get("remaining", 0) or 0), "slot_source": "pact"}


def _consume_any_spell_slot(sheet: Dict[str, Any], slot_level: int, *, count: int = 1, slot_source: str = "auto") -> Tuple[bool, str, Dict[str, Any]]:
    src = _normalize_slot_source(slot_source)
    level = max(0, int(slot_level or 0))
    if level <= 0:
        return True, "cantrip", {"level": 0, "total": 0, "used": 0, "remaining": 999, "slot_source": "none"}
    if src == "shared":
        ok, msg, state = consume_spell_slot(sheet, level, count=count)
        if isinstance(state, dict):
            state["slot_source"] = "shared"
        return ok, msg, state
    if src == "pact":
        return _consume_pact_magic(sheet, level, count=count)

    sc = (ensure_spellcasting_foundation(sheet).get("spellcasting") or {}) if isinstance(ensure_spellcasting_foundation(sheet).get("spellcasting"), dict) else {}
    pm = _get_pact_magic_state(sc)
    if bool(pm.get("enabled")) and level == max(0, int(pm.get("slot_level", 0) or 0)) and max(0, int(pm.get("remaining", 0) or 0)) >= max(1, int(count or 1)):
        return _consume_pact_magic(sheet, level, count=count)
    ok, msg, state = consume_spell_slot(sheet, level, count=count)
    if isinstance(state, dict):
        state["slot_source"] = "shared" if ok else src
    return ok, msg, state


def _refresh_pact_magic_for_rest(sheet: Dict[str, Any], rest_type: str) -> None:
    sc = ensure_spellcasting_foundation(sheet).get("spellcasting", {})
    pm = _get_pact_magic_state(sc)
    refresh = str(pm.get("refresh") or "short_rest").strip().lower()
    rest_type = str(rest_type or "").strip().lower()
    if not bool(pm.get("enabled")):
        pm["used"] = 0
        pm["remaining"] = max(0, int(pm.get("slots_total", 0) or 0))
        return
    if refresh == "short_rest" and rest_type in {"short_rest", "long_rest"}:
        pm["used"] = 0
    elif refresh == "long_rest" and rest_type == "long_rest":
        pm["used"] = 0
    _recompute_pact_magic_remaining(sc)


def _mystic_arcanum_pool_name(slot_level: int) -> str:
    level = max(0, int(slot_level or 0))
    return f"mystic_arcanum_{level}" if 6 <= level <= 9 else ""


def _can_consume_mystic_arcanum(sheet: Dict[str, Any], slot_level: int) -> Tuple[bool, str, Dict[str, Any]]:
    level = max(0, int(slot_level or 0))
    pool_name = _mystic_arcanum_pool_name(level)
    if not pool_name:
        return False, "Mystic Arcanum only applies to levels 6-9", {"slot_source": "arcanum", "level": level}
    pools = sheet.get("resource_pools") if isinstance(sheet.get("resource_pools"), dict) else {}
    pool = pools.get(pool_name) if isinstance(pools.get(pool_name), dict) else {}
    total = max(0, int(pool.get("max", 0) or 0))
    current = max(0, int(pool.get("current", 0) or 0))
    if total <= 0:
        return False, f"No Mystic Arcanum available for level {level}", {"slot_source": "arcanum", "level": level, "current": current, "max": total}
    if current <= 0:
        return False, f"Mystic Arcanum level {level} already used", {"slot_source": "arcanum", "level": level, "current": current, "max": total}
    return True, "ok", {"slot_source": "arcanum", "level": level, "current": current, "max": total, "remaining": current}


def _consume_mystic_arcanum(sheet: Dict[str, Any], slot_level: int) -> Tuple[bool, str, Dict[str, Any]]:
    ok, msg, state = _can_consume_mystic_arcanum(sheet, slot_level)
    if not ok:
        return False, msg, state
    pools = sheet.setdefault("resource_pools", {}) if isinstance(sheet.get("resource_pools"), dict) else {}
    sheet["resource_pools"] = pools
    pool_name = _mystic_arcanum_pool_name(slot_level)
    pool = pools.setdefault(pool_name, {"current": 1, "max": 1, "refresh": "long_rest"})
    cur = max(0, int(pool.get("current", 0) or 0))
    pool["current"] = max(0, cur - 1)
    return True, "consumed", {"slot_source": "arcanum", "level": max(0, int(slot_level or 0)), "current": int(pool.get("current", 0) or 0), "max": int(pool.get("max", 0) or 0), "remaining": int(pool.get("current", 0) or 0)}


def _restore_shared_spell_slots(sheet: Dict[str, Any], slot_level: int, count: int = 1) -> Tuple[bool, str, Dict[str, Any]]:
    sc = ensure_spellcasting_foundation(sheet).get("spellcasting", {})
    level = max(0, int(slot_level or 0))
    count = max(1, int(count or 1))
    if level <= 0:
        return False, "Cantrip slots cannot be restored", {"level": level, "slot_source": "shared"}
    row = (sc.get("spells", {}) or {}).get(str(level))
    if not isinstance(row, dict):
        return False, f"No slot row for level {level}", {"level": level, "slot_source": "shared"}
    total = max(0, int(row.get("total", 0) or 0))
    used = max(0, int(row.get("used", 0) or 0))
    refresh = str(row.get("refresh") or sc.get("slot_refresh") or "long_rest").strip().lower()
    source = str(row.get("source") or "spellcasting").strip().lower()
    if total <= 0:
        return False, f"No level {level} spell slots available", {"level": level, "slot_source": "shared", "total": total, "used": used, "remaining": max(0, total-used)}
    if source == "pact_magic" or refresh == "short_rest":
        return False, f"Level {level} is not a recoverable shared slot row", {"level": level, "slot_source": "shared", "total": total, "used": used, "remaining": max(0, total-used)}
    if used <= 0:
        return False, f"No spent level {level} slots to recover", {"level": level, "slot_source": "shared", "total": total, "used": used, "remaining": max(0, total-used)}
    actual = min(used, count)
    row["used"] = max(0, used - actual)
    row["remaining"] = max(0, total - int(row.get("used", 0) or 0))
    return True, "restored", {"level": level, "slot_source": "shared", "total": total, "used": int(row.get("used", 0) or 0), "remaining": int(row.get("remaining", 0) or 0), "restored": actual}


def _restore_pact_magic_slots(sheet: Dict[str, Any], count: int = 1) -> Tuple[bool, str, Dict[str, Any]]:
    sc = ensure_spellcasting_foundation(sheet).get("spellcasting", {})
    pm = sc.get("pact_magic") if isinstance(sc.get("pact_magic"), dict) else {}
    total = max(0, int(pm.get("slots_total", 0) or 0))
    used = max(0, int(pm.get("used", 0) or 0))
    slot_level = max(0, int(pm.get("slot_level", 0) or 0))
    if total <= 0 or slot_level <= 0:
        return False, "No pact magic slots available", {"slot_source": "pact", "level": slot_level, "total": total, "used": used, "remaining": max(0, total-used)}
    if used <= 0:
        return False, "No spent pact magic slots to restore", {"slot_source": "pact", "level": slot_level, "total": total, "used": used, "remaining": max(0, total-used)}
    actual = min(max(1, int(count or 1)), used)
    pm["used"] = max(0, used - actual)
    pm["remaining"] = max(0, total - int(pm.get("used", 0) or 0))
    return True, "restored", {"slot_source": "pact", "level": slot_level, "total": total, "used": int(pm.get("used", 0) or 0), "remaining": int(pm.get("remaining", 0) or 0), "restored": actual}


def _font_of_magic_slot_cost(slot_level: int) -> int:
    return {1: 2, 2: 3, 3: 5, 4: 6, 5: 7}.get(max(0, int(slot_level or 0)), 0)


def _restore_one_shared_spell_slot_if_possible(sheet: Dict[str, Any], slot_level: int) -> Tuple[bool, str, Dict[str, Any]]:
    return _restore_shared_spell_slots(sheet, slot_level, count=1)


def _parse_slot_level_list_from_request(req: Any) -> List[int]:
    levels: List[int] = []
    raw_mode = str(getattr(req, "mode", "") or "").strip()
    if raw_mode:
        for part in raw_mode.replace(";", ",").replace("|", ",").split(","):
            part = part.strip()
            if not part:
                continue
            try:
                levels.append(int(part))
            except Exception:
                continue
    if not levels and getattr(req, "amount", None) is not None:
        try:
            levels = [int(getattr(req, "amount"))]
        except Exception:
            levels = []
    return [max(0, int(x or 0)) for x in levels if int(x or 0) > 0]


def _max_spell_level_for_class_progression(class_key: str, class_level: int) -> int:
    ck = _slug_key(class_key)
    lvl = max(0, int(class_level or 0))
    if ck in {"wizard", "cleric", "druid", "bard", "sorcerer"}:
        rows = _derive_full_caster_slots(lvl)
        return max(rows.keys(), default=0)
    if ck in {"ranger", "paladin"}:
        rows = _derive_half_caster_slots(lvl)
        return max(rows.keys(), default=0)
    if ck == "warlock":
        pact = _derive_warlock_pact_slots(lvl)
        return max(0, int(pact.get("slot_level", 0) or 0))
    return 0


def _max_spell_level_for_class_features(class_key: str, class_level: int) -> int:
    ck = _slug_key(class_key)
    lvl = max(0, int(class_level or 0))
    base = _max_spell_level_for_class_progression(ck, lvl)
    if ck == "warlock":
        if lvl >= 17:
            return max(base, 9)
        if lvl >= 15:
            return max(base, 8)
        if lvl >= 13:
            return max(base, 7)
        if lvl >= 11:
            return max(base, 6)
    return base


def _active_class_spell_level_caps(class_levels: Dict[str, Any], active_classes: List[str]) -> Dict[str, int]:
    caps: Dict[str, int] = {}
    for class_key in (active_classes or []):
        ck = _slug_key(class_key)
        if not ck:
            continue
        lvl = max(0, int((class_levels or {}).get(ck, 0) or 0))
        caps[ck] = _max_spell_level_for_class_features(ck, lvl)
    return caps


def _spell_allowed_for_sheet_level(spell_row: Dict[str, Any], active_classes: List[str], class_levels: Dict[str, Any]) -> bool:
    level = _spell_level_from_row(spell_row)
    classes = _extract_spell_classes(spell_row)
    if not classes:
        return True
    caps = _active_class_spell_level_caps(class_levels or {}, active_classes or [])
    for class_key in classes:
        if class_key in caps and level <= int(caps.get(class_key, 0) or 0):
            return True
    return False


def _max_spell_level_for_sheet(sheet: Dict[str, Any], sc: Optional[Dict[str, Any]] = None) -> int:
    sc = sc if isinstance(sc, dict) else ((sheet.get("spellcasting") or {}) if isinstance(sheet.get("spellcasting"), dict) else {})
    active_classes = _spell_listify(sc.get("spellcasting_classes"))
    class_levels = sheet.get("class_levels") if isinstance(sheet.get("class_levels"), dict) else {}
    caps = _active_class_spell_level_caps(class_levels, active_classes)
    return max(caps.values(), default=0)


def _wizard_spell_mastery_limit(wizard_level: int) -> int:
    return 2 if max(0, int(wizard_level or 0)) >= 18 else 0


def _wizard_signature_spell_limit(wizard_level: int) -> int:
    return 2 if max(0, int(wizard_level or 0)) >= 20 else 0


def _sanitize_wizard_feature_spell_ids(
    campaign_id: str,
    spell_ids: List[str],
    *,
    allowed_ids: List[str],
    required_level: int,
    max_items: int,
) -> List[str]:
    allowed = set(_spell_unique(_spell_listify(allowed_ids)))
    if max_items <= 0:
        return []
    db = load_spells_db_for_campaign(campaign_id)
    out: List[str] = []
    for sid in _sanitize_generic_spell_ids(campaign_id, spell_ids, require_leveled=True):
        if sid in set(out):
            continue
        if allowed and sid not in allowed:
            continue
        row = db.get(sid) if isinstance(db, dict) else None
        if not isinstance(row, dict):
            continue
        if int(_spell_level_from_row(row)) != int(required_level):
            continue
        out.append(sid)
        if len(out) >= max_items:
            break
    return out


def _derive_spellcasting_for_sheet(campaign_id: str, sheet: Dict[str, Any]) -> None:
    sheet = ensure_spellcasting_foundation(sheet)
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    class_levels = sheet.get("class_levels") if isinstance(sheet.get("class_levels"), dict) else {}
    meta = sheet.get("meta") if isinstance(sheet.get("meta"), dict) else {}

    cantrips = _spell_unique(_spell_listify(sc.get("cantrips")))
    known = _spell_unique(_spell_listify(sc.get("known_spells") or sheet.get("known_spells")))
    prepared = _spell_unique(_spell_listify(sc.get("prepared_spells") or sheet.get("prepared_spells")))
    spellbook = _spell_unique(_spell_listify(sc.get("spellbook_spells") or sheet.get("spellbook_spells")))
    bonus_spell_ids = _spell_unique(_spell_listify(sc.get("bonus_spell_ids") or sheet.get("bonus_spell_ids")))
    metamagic_options = _sanitize_metamagic_option_ids(_spell_unique(_spell_listify(sc.get("metamagic_options") or sheet.get("metamagic_options"))))
    spell_mastery_spells = _spell_unique(_spell_listify(sc.get("spell_mastery_spells") or sheet.get("spell_mastery_spells")))
    signature_spells = _spell_unique(_spell_listify(sc.get("signature_spells") or sheet.get("signature_spells")))

    active_spellcasting_classes: List[str] = []
    for class_key, raw_level in (class_levels or {}).items():
        ck = str(class_key or "").strip().lower()
        rule = SPELLCASTING_CLASS_RULES.get(ck)
        if not rule:
            continue
        level = max(0, int(raw_level or 0))
        if level < int(rule.get("min_level", 1) or 1):
            continue
        active_spellcasting_classes.append(ck)

    if not active_spellcasting_classes:
        meta_class = _slug_key((meta or {}).get("class", ""))
        rule = SPELLCASTING_CLASS_RULES.get(meta_class)
        meta_level = max(0, int((meta or {}).get("level", 0) or 0))
        if rule and meta_level >= int(rule.get("min_level", 1) or 1):
            active_spellcasting_classes.append(meta_class)
            if meta_class not in class_levels:
                class_levels[meta_class] = meta_level
                sheet["class_levels"] = class_levels

    active_spellcasting_classes = _spell_unique(active_spellcasting_classes)
    primary_class = ""
    if active_spellcasting_classes:
        primary_class = max(active_spellcasting_classes, key=lambda ck: int(class_levels.get(ck, 0) or 0))
    primary_rule = SPELLCASTING_CLASS_RULES.get(primary_class, {}) if primary_class else {}

    prev_pm = dict(sc.get("pact_magic") or {}) if isinstance(sc.get("pact_magic"), dict) else {}

    sc["spellcasting_classes"] = list(active_spellcasting_classes)
    sc["spellcasting_profile"] = "none"
    sc["preparation_formula"] = ""
    sc["spellbook_enabled"] = bool(primary_rule.get("spellbook", False))
    sc["multiclass_caster_level"] = 0
    sc["preparation_max"] = 0
    sc["slot_options"] = []
    sc["known_limit"] = 0
    sc["cantrip_limit"] = 0
    sc["spellbook_minimum"] = 0
    sc["bonus_spell_limit"] = 0
    sc["bonus_spell_ids"] = []
    sc["metamagic_choice_limit"] = 0
    sc["metamagic_options"] = []
    sc["spell_mastery_limit"] = 0
    sc["spell_mastery_spells"] = []
    sc["signature_spell_limit"] = 0
    sc["signature_spells"] = []
    sc["sanitized_spell_state"] = {}
    sc["allowed_spell_ids"] = []
    sc["allowed_cantrip_ids"] = []
    sc["allowed_leveled_spell_ids"] = []
    sc["pact_magic"] = {
        "enabled": False,
        "slot_level": 0,
        "slots_total": 0,
        "refresh": "short_rest",
        "used": max(0, int(prev_pm.get("used", 0) or 0)),
        "remaining": 0,
        "mixed_with_other_slots": False,
    }

    cantrips, known, prepared, spellbook, spell_diag = _sanitize_spell_id_lists_for_sheet(
        campaign_id,
        active_spellcasting_classes,
        cantrips,
        known,
        prepared,
        spellbook,
        class_levels=class_levels,
    )
    sc["sanitized_spell_state"] = dict(spell_diag)
    sc["allowed_spell_ids"] = list(spell_diag.get("allowed_spell_ids") or [])
    sc["allowed_cantrip_ids"] = list(spell_diag.get("allowed_cantrip_ids") or [])
    sc["allowed_leveled_spell_ids"] = list(spell_diag.get("allowed_leveled_spell_ids") or [])

    subclass_grants = subclass_spell_grants(campaign_id, sheet, load_spells_db_for_campaign(campaign_id))
    subclass_bonus_spell_ids = _spell_unique(_spell_listify((subclass_grants or {}).get("always_prepared")) + _spell_listify((subclass_grants or {}).get("bonus_known")))
    subclass_expanded_ids = _spell_unique(_spell_listify((subclass_grants or {}).get("expanded_access_spell_ids")))

    if not primary_class:
        _clear_spell_rows(sc)
        sc["class"] = ""
        sc["ability"] = ""
        sc["save_dc"] = 0
        sc["attack_bonus"] = 0
        sc["slot_refresh"] = "long_rest"
        sc["known_mode"] = ""
        sc["cantrips"] = cantrips
        sc["known_spells"] = known
        sc["prepared_spells"] = prepared
        sc["spellbook_spells"] = spellbook
        sc["bonus_spell_ids"] = []
        sc["metamagic_options"] = []
        sc["spell_mastery_spells"] = []
        sc["signature_spells"] = []
        sheet["known_spells"] = list(known)
        sheet["prepared_spells"] = list(prepared)
        sheet["spellbook_spells"] = list(spellbook)
        sheet["bonus_spell_ids"] = []
        sheet["metamagic_options"] = []
        sheet["spell_mastery_spells"] = []
        sheet["signature_spells"] = []
        return

    ability_key = str(primary_rule.get("ability", "") or "").strip().lower()
    class_label = str(primary_rule.get("label") or primary_class.replace("_", " ").title())
    mode = str(primary_rule.get("known_mode", "") or "").strip().lower()
    total_level = sum(max(0, int(v or 0)) for v in class_levels.values()) or int((meta or {}).get("level", 1) or 1)
    prof_bonus = _compute_prof_bonus(total_level)
    ability_mod = _spellcaster_ability_mod(sheet, ability_key) if ability_key else 0

    full_levels = 0
    half_levels = 0
    warlock_level = 0
    for class_key in active_spellcasting_classes:
        class_level = max(0, int(class_levels.get(class_key, 0) or 0))
        rule = SPELLCASTING_CLASS_RULES.get(class_key, {})
        progression = str(rule.get("progression", "") or "").strip().lower()
        if progression == "full":
            full_levels += class_level
        elif progression == "half":
            half_levels += class_level
        elif progression == "pact":
            warlock_level += class_level

    standard_effective_level = full_levels + (half_levels // 2)
    sc["multiclass_caster_level"] = max(0, standard_effective_level)

    shared_slots: Dict[int, int] = {}
    if standard_effective_level > 0:
        shared_slots = _derive_full_caster_slots(standard_effective_level)
    elif half_levels > 0:
        shared_slots = _derive_half_caster_slots(max(0, half_levels))

    pact = _derive_warlock_pact_slots(warlock_level) if warlock_level > 0 else {"slot_level": 0, "slots": 0}

    _clear_spell_rows(sc, keep_used=True)
    slot_options: List[Dict[str, Any]] = []
    if shared_slots:
        for slot_level, total in shared_slots.items():
            _set_spell_slot_row(sc, slot_level, total, "long_rest", source="spellcasting")
            slot_options.append({"slot_level": int(slot_level), "source": "shared", "refresh": "long_rest"})
        sc["slot_refresh"] = "long_rest"
    else:
        sc["slot_refresh"] = "short_rest" if warlock_level > 0 else "long_rest"

    pact_level = max(0, int(pact.get("slot_level", 0) or 0))
    pact_total = max(0, int(pact.get("slots", 0) or 0))
    if warlock_level > 0 and pact_level > 0 and pact_total > 0:
        pm = _get_pact_magic_state(sc)
        pm["enabled"] = True
        pm["slot_level"] = pact_level
        pm["slots_total"] = pact_total
        pm["refresh"] = "short_rest"
        pm["mixed_with_other_slots"] = bool(shared_slots)
        if not shared_slots:
            _set_spell_slot_row(sc, pact_level, pact_total, "short_rest", source="pact_magic")
            sc["slot_refresh"] = "short_rest"
            row = ((sc.get("spells") or {}).get(str(pact_level)) or {}) if pact_level > 0 else {}
            pm["used"] = max(0, int(row.get("used", 0) or 0))
            pm["remaining"] = max(0, int(row.get("remaining", 0) or 0))
        else:
            _recompute_pact_magic_remaining(sc)
            slot_options.append({"slot_level": pact_level, "source": "pact", "refresh": "short_rest"})

    if warlock_level > 0 and not shared_slots:
        sc["spellcasting_profile"] = "pact"
    elif shared_slots and len(active_spellcasting_classes) <= 1:
        sc["spellcasting_profile"] = str(primary_rule.get("progression", "standard") or "standard")
    elif shared_slots:
        sc["spellcasting_profile"] = "multiclass"

    class_level = max(0, int(class_levels.get(primary_class, 0) or 0))
    if primary_class == "wizard":
        spellbook = _spell_unique(spellbook or known)
        known = list(spellbook)
        prepared = [sid for sid in prepared if sid in set(spellbook)]
    elif mode == "known":
        prepared = []
        if not bool(primary_rule.get("spellbook", False)):
            spellbook = []
    elif mode == "prepared":
        if not bool(primary_rule.get("spellbook", False)):
            spellbook = []
        if primary_class != "wizard":
            known = []

    prep_cap = 0
    if primary_class in {"cleric", "druid"}:
        prep_cap = max(1, class_level + ability_mod)
        sc["preparation_formula"] = f"{primary_class.title()} level + {ability_key.upper()} modifier (current max {prep_cap})"
    elif primary_class in {"paladin", "ranger"}:
        prep_cap = max(1, (class_level // 2) + ability_mod)
        sc["preparation_formula"] = f"Half {primary_class.title()} level + {ability_key.upper()} modifier (current max {prep_cap})"
    elif primary_class == "wizard":
        prep_cap = max(1, class_level + ability_mod)
        sc["preparation_formula"] = f"Wizard level + {ability_key.upper()} modifier (current max {prep_cap})"
    elif mode == "known":
        sc["preparation_formula"] = "Known-spells caster"
    sc["preparation_max"] = max(0, prep_cap)
    if prep_cap > 0:
        prepared = list(prepared)[:prep_cap]

    known_limit = _spellcaster_known_limit(primary_class, class_level)
    cantrip_limit = _spellcaster_cantrip_limit(primary_class, class_level)
    if mode == "known" and known_limit > 0:
        known = list(known)[:known_limit]
    if cantrip_limit > 0:
        cantrips = list(cantrips)[:cantrip_limit]

    raw_bonus_spell_ids = list(bonus_spell_ids)
    bonus_spell_ids = []
    if "bard" in active_spellcasting_classes:
        bard_level = max(0, int(class_levels.get("bard", 0) or 0))
        bonus_limit = _bard_magical_secrets_limit(bard_level)
        bard_subclass = str(((sheet.get("subclasses") or {}) if isinstance(sheet.get("subclasses"), dict) else {}).get("bard") or "").strip().lower()
        if bard_subclass == "lore" and bard_level >= 6:
            bonus_limit += 2
        sc["bonus_spell_limit"] = max(0, bonus_limit)
        if bonus_limit > 0:
            bard_max_spell_level = _max_spell_level_for_class_progression("bard", bard_level)
            spells_db = load_spells_db_for_campaign(campaign_id)
            allowed_bonus: List[str] = []
            for sid in _sanitize_generic_spell_ids(campaign_id, raw_bonus_spell_ids, require_leveled=True):
                row = spells_db.get(sid) if isinstance(spells_db, dict) else None
                if not isinstance(row, dict):
                    continue
                spell_level = _spell_level_from_row(row)
                if bard_max_spell_level > 0 and spell_level > bard_max_spell_level:
                    continue
                allowed_bonus.append(sid)
            bonus_spell_ids = allowed_bonus[:bonus_limit]
        else:
            bonus_spell_ids = []

    sorcerer_level = max(0, int(class_levels.get("sorcerer", 0) or 0))
    metamagic_limit = _metamagic_choice_limit(sorcerer_level)
    sc["metamagic_choice_limit"] = max(0, metamagic_limit)
    if metamagic_limit > 0:
        metamagic_options = _sanitize_metamagic_option_ids(metamagic_options)[:metamagic_limit]
    else:
        metamagic_options = []

    wizard_level = max(0, int(class_levels.get("wizard", 0) or 0))
    spell_mastery_limit = _wizard_spell_mastery_limit(wizard_level)
    signature_spell_limit = _wizard_signature_spell_limit(wizard_level)
    sc["spell_mastery_limit"] = max(0, spell_mastery_limit)
    sc["signature_spell_limit"] = max(0, signature_spell_limit)
    wizard_allowed = list(spellbook) if primary_class == "wizard" else []
    spell_mastery_spells = _sanitize_wizard_feature_spell_ids(
        campaign_id,
        spell_mastery_spells,
        allowed_ids=wizard_allowed,
        required_level=1,
        max_items=spell_mastery_limit,
    )
    signature_spells = _sanitize_wizard_feature_spell_ids(
        campaign_id,
        signature_spells,
        allowed_ids=wizard_allowed,
        required_level=3,
        max_items=signature_spell_limit,
    )

    bonus_spell_ids = _spell_unique(list(bonus_spell_ids) + list(subclass_bonus_spell_ids))
    if bonus_spell_ids or subclass_expanded_ids:
        sc["allowed_spell_ids"] = sorted(set(sc.get("allowed_spell_ids") or []) | set(bonus_spell_ids) | set(subclass_expanded_ids))
        sc["allowed_leveled_spell_ids"] = sorted(set(sc.get("allowed_leveled_spell_ids") or []) | set(bonus_spell_ids) | set(subclass_expanded_ids))

    if primary_class == "wizard":
        sc["spellbook_minimum"] = max(len(spellbook), 6 + max(0, class_level - 1) * 2)
    else:
        sc["spellbook_minimum"] = 0

    sc["known_limit"] = max(0, known_limit)
    sc["cantrip_limit"] = max(0, cantrip_limit)

    if primary_class == "wizard":
        spellbook = [sid for sid in spellbook if sid in set(sc["allowed_leveled_spell_ids"])]
        known = list(spellbook)
        prepared = [sid for sid in prepared if sid in set(spellbook)]
    elif mode == "known":
        allowed_leveled = set(sc["allowed_leveled_spell_ids"])
        known = [sid for sid in known if sid in allowed_leveled]
        prepared = []
        spellbook = [] if not sc["spellbook_enabled"] else [sid for sid in spellbook if sid in allowed_leveled]
    elif mode == "prepared":
        allowed_leveled = set(sc["allowed_leveled_spell_ids"])
        prepared = [sid for sid in prepared if sid in allowed_leveled]
        known = []
        if not sc["spellbook_enabled"]:
            spellbook = []

    sc["class"] = class_label
    sc["ability"] = ability_key
    sc["save_dc"] = 8 + prof_bonus + ability_mod if ability_key else 0
    sc["attack_bonus"] = prof_bonus + ability_mod if ability_key else 0
    sc["known_mode"] = mode
    sc["spellbook_enabled"] = bool(primary_rule.get("spellbook", False))
    sc["cantrips"] = cantrips
    sc["known_spells"] = known
    sc["prepared_spells"] = prepared
    sc["spellbook_spells"] = spellbook
    sc["bonus_spell_ids"] = list(bonus_spell_ids)
    sc["metamagic_options"] = list(metamagic_options)
    sc["spell_mastery_spells"] = list(spell_mastery_spells)
    sc["signature_spells"] = list(signature_spells)
    sc["slot_options"] = slot_options
    sheet["known_spells"] = list(known)
    sheet["prepared_spells"] = list(prepared)
    sheet["spellbook_spells"] = list(spellbook)
    sheet["bonus_spell_ids"] = list(bonus_spell_ids)
    sheet["metamagic_options"] = list(metamagic_options)
    sheet["spell_mastery_spells"] = list(spell_mastery_spells)
    sheet["signature_spells"] = list(signature_spells)

    if warlock_level > 0:
        _recompute_pact_magic_remaining(sc)


def _sheet_spell_ids(sheet: Dict[str, Any]) -> Tuple[List[str], List[str], List[str], List[str]]:
    sheet = ensure_sheet_minimum(sheet, str(sheet.get("character_id", "") or ""))
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}

    def _norm(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(v).strip() for v in value if str(v).strip()]
        if isinstance(value, str):
            return [part.strip() for part in value.replace("\n", ",").split(",") if part.strip()]
        return []

    cantrips = _norm(sc.get("cantrips"))
    known = _norm(sc.get("known_spells") or sheet.get("known_spells"))
    prepared = _norm(sc.get("prepared_spells") or sheet.get("prepared_spells"))
    spellbook = _norm(sc.get("spellbook_spells") or sheet.get("spellbook_spells"))
    bonus = _norm(sc.get("bonus_spell_ids") or sheet.get("bonus_spell_ids"))
    if bonus:
        known = _spell_unique(list(known) + list(bonus))
    return cantrips, known, prepared, spellbook


def _character_can_declare_spell(sheet: Dict[str, Any], spell_id: str, spell_row: Dict[str, Any]) -> Tuple[bool, str, Dict[str, Any]]:
    cantrips, known, prepared, spellbook = _sheet_spell_ids(sheet)
    sid = str(spell_id or "").strip()
    level = int((spell_row or {}).get("level", 0) or 0)
    sc = (sheet.get("spellcasting") or {}) if isinstance(sheet.get("spellcasting"), dict) else {}
    mode = str(sc.get("known_mode", "") or "").strip().lower()
    active_classes = _spell_listify(sc.get("spellcasting_classes"))
    legal_ids = set(_spell_listify(sc.get("allowed_spell_ids")))
    if not active_classes:
        return False, "Character has no active spellcasting class", {"cantrips": cantrips, "known": known, "prepared": prepared, "spellbook": spellbook}
    if legal_ids and sid not in legal_ids:
        return False, "Spell is not legal for this character's active spellcasting classes", {"spellcasting_classes": active_classes, "allowed_spell_ids": sorted(legal_ids)}
    class_levels = sheet.get("class_levels") if isinstance(sheet.get("class_levels"), dict) else {}
    if not _spell_allowed_for_any_class(spell_row, active_classes):
        return False, "Spell is not legal for this character's active spellcasting classes", {"spellcasting_classes": active_classes}
    if not _spell_allowed_for_sheet_level(spell_row, active_classes, class_levels):
        return False, "Spell level is not currently available to this character", {"spellcasting_classes": active_classes, "max_spell_level": _max_spell_level_for_sheet(sheet, sc)}
    if level <= 0:
        if sid not in set(cantrips):
            return False, "Cantrip is not on this character's cantrip list", {"cantrips": cantrips}
        return True, "ok", {"cantrips": cantrips, "known": known, "prepared": prepared, "spellbook": spellbook}
    if mode in {"prepared", "prepared_only", "prepared_known"}:
        if sid not in set(prepared):
            if sid in set(spellbook):
                return False, "Spell is in the spellbook but not currently prepared", {"prepared": prepared, "spellbook": spellbook}
            return False, "Spell is not currently prepared", {"prepared": prepared, "spellbook": spellbook}
        return True, "ok", {"cantrips": cantrips, "known": known, "prepared": prepared, "spellbook": spellbook}
    if sid not in set(known):
        return False, "Spell is not on this character's known spell list", {"known": known}
    return True, "ok", {"cantrips": cantrips, "known": known, "prepared": prepared, "spellbook": spellbook}


def _find_item_by_id(items_db: Dict[str, Any], bucket: str, item_id: str) -> Dict[str, Any]:
    target = str(item_id or "").strip()
    if not target:
        return {}
    for it in (items_db.get(bucket) or []):
        if isinstance(it, dict) and str(it.get("item_id") or it.get("id") or "").strip() == target:
            return it
    return {}


def _recompute_sheet_derived_state(campaign_id: str, character_id: str, sheet: Dict[str, Any]) -> Dict[str, Any]:
    sheet = ensure_sheet_minimum(sheet, character_id)
    meta = sheet.get("meta") if isinstance(sheet.get("meta"), dict) else {}

    if not isinstance(sheet.get("class_levels"), dict):
        sheet["class_levels"] = {}
    class_levels = sheet["class_levels"]

    class_key = _slug_key((meta or {}).get("class", ""))
    meta_level = max(1, int((meta or {}).get("level", 1) or 1))

    # For the current single-class workflow, keep the primary class level
    # in sync with meta.level so hit dice and level-based grants update.
    if class_key:
        class_levels[class_key] = meta_level

    effective_level = meta_level if class_key else 0
    class_tpl = dict(CLASS_TEMPLATES.get(class_key, {})) if class_key else {}

    if class_tpl and effective_level > 0:
        _apply_class_level_grants(sheet, class_tpl, effective_level)

    _sync_sheet_derived_resources(sheet)
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    stats = sheet.setdefault("stats", {}) if isinstance(sheet.get("stats"), dict) else {}
    abilities = sheet.setdefault("abilities", {}) if isinstance(sheet.get("abilities"), dict) else {}
    equipped = sheet.setdefault("equipped", {}) if isinstance(sheet.get("equipped"), dict) else {}
    resources = sheet.setdefault("resources", {}) if isinstance(sheet.get("resources"), dict) else {}
    combat = sheet.setdefault("combat", {}) if isinstance(sheet.get("combat"), dict) else {}
    base_stats = sheet.setdefault("base_stats", {}) if isinstance(sheet.get("base_stats"), dict) else {}

    dex_mod = (int(abilities.get("dex", 10) or 10) - 10) // 2

    # Default 5e unarmored baseline for most characters.
    default_unarmored_ac = 10 + dex_mod

    # Keep a stored baseline if the sheet explicitly defines one,
    # but never let it fall below the normal 5e unarmored default.
    stored_base = int(
        stats.get(
            "defense_base",
            base_stats.get("ac_base", default_unarmored_ac)
        ) or default_unarmored_ac
    )
    base_defense = max(stored_base, default_unarmored_ac)

    base_view = {
        "ac": base_defense,
        "defense": base_defense,
        "movement_ft": int(stats.get("movement_ft", 30) or 30),
        "vision_ft": int(stats.get("vision_ft", 60) or 60),
        "damage_resistances": list(resources.get("damage_resistances") or []),
    }

    fast_move_bonus = int(combat.get("fast_movement_bonus_ft", 0) or 0)
    if fast_move_bonus > 0:
        base_view["movement_ft"] = int(base_view.get("movement_ft", 30) or 30) + fast_move_bonus

    items_db = _load_items_db(campaign_id)
    armor_id = str(equipped.get("armor_id") or equipped.get("armor") or "").strip()
    weapon_id = str(equipped.get("weapon_id") or equipped.get("weapon") or "").strip()
    armor = _find_item_by_id(items_db, "armors", armor_id)

    if armor:
        armor_type = str(armor.get("armor_type") or "").strip().lower()
        ac_bonus = int(armor.get("ac_bonus", 0) or 0)
        armor_base = 10 + ac_bonus

        if armor_type == "light":
            armor_ac = armor_base + dex_mod
        elif armor_type == "medium":
            armor_ac = armor_base + min(2, dex_mod)
        elif armor_type == "heavy":
            armor_ac = armor_base
        else:
            # Fallback: treat unknown armor types like light armor for now.
            armor_ac = armor_base + dex_mod

        base_view["ac"] = max(int(base_view["ac"]), int(armor_ac))
        base_view["defense"] = max(int(base_view["defense"]), int(armor_ac))

    derived_view = apply_subclass_passives(sheet, base_view)
    derived_view = apply_feat_passives(sheet, derived_view)
    derived_view = apply_passives_to_combat_view(sheet, derived_view)

    stats["defense"] = int(
        derived_view.get("defense", derived_view.get("ac", stats.get("defense", base_defense))) or base_defense
    )
    stats["movement_ft"] = int(derived_view.get("movement_ft", stats.get("movement_ft", 30)) or 30)
    stats["vision_ft"] = int(derived_view.get("vision_ft", stats.get("vision_ft", 60)) or 60)

    if weapon_id or ("weapon_id" in equipped):
        stats["weapon_id"] = weapon_id
    if armor_id or ("armor_id" in equipped):
        stats["armor_id"] = armor_id

    combat["ac"] = int(derived_view.get("ac", stats["defense"]) or stats["defense"])
    combat["weapon_ref"] = weapon_id
    combat["armor_id"] = armor_id

    # Always sync these so stale state gets cleared properly too.
    combat["darkvision_ft"] = int(derived_view.get("darkvision_ft", 0) or 0)
    resources["damage_resistances"] = list(derived_view.get("damage_resistances") or [])

    return sheet

def _apply_creation_autofill(sheet: Dict[str, Any], class_name: str, race_name: str, level: int, max_hp_override: Optional[int] = None) -> None:
    meta = sheet.setdefault("meta", {})
    prof = sheet.setdefault("proficiencies", {})
    other = prof.setdefault("other", {}) if isinstance(prof, dict) else {}
    if not isinstance(other, dict):
        other = {}
        prof["other"] = other
    prof.setdefault("saves", {})
    prof.setdefault("skills", {})
    prof.setdefault("languages", [])
    resources = sheet.setdefault("resources", {})
    if not isinstance(resources, dict):
        resources = {}
        sheet["resources"] = resources

    class_key = _slug_key(class_name)
    race_key, race_base, race_tpl = _resolve_race_template(race_name)
    class_tpl = dict(CLASS_TEMPLATES.get(class_key, {}))

    sheet["class_levels"] = {}
    if class_key:
        sheet["class_levels"][class_key] = max(1, int(level or 1))
    sheet["race_id"] = race_key
    sheet["base_race_id"] = _slug_key(race_tpl.get("base_race", "")) or race_key
    sheet.setdefault("lineage_id", "")
    sheet.setdefault("trait_ids", [])
    sheet.setdefault("ability_ids", [])
    sheet.setdefault("resource_pools", {})
    sheet.setdefault("feature_state", {"used_this_turn": [], "used_this_round": [], "once_per_turn_flags": {}, "once_per_round_flags": {}})

    meta["class"] = class_tpl.get("display_name") or str(class_name or "").strip()
    meta["race"] = race_tpl.get("display_name") or race_base.get("display_name") or str(race_name or "").strip()
    meta["level"] = max(1, min(int(level or 1), 20))

    for bonus_src in (race_base.get("ability_bonuses", {}), race_tpl.get("ability_bonuses", {})):
        _apply_ability_bonuses(sheet, bonus_src)

    stats = sheet.setdefault("stats", {})
    if not isinstance(stats, dict):
        stats = {}
        sheet["stats"] = stats

    if class_tpl:
        hit_die = int(class_tpl.get("hit_die", 8) or 8)
        sheet.setdefault("combat", {})
        combat = sheet["combat"] if isinstance(sheet.get("combat"), dict) else {}
        if not isinstance(combat, dict):
            combat = {}
            sheet["combat"] = combat
        combat["hit_die_sides"] = hit_die
        combat["hit_dice_total"] = meta["level"]
        combat.setdefault("hit_dice_used", 0)
        resources["hit_dice_remaining"] = max(0, meta["level"] - int(combat.get("hit_dice_used", 0) or 0))
        other["armor"] = _merge_string_csv(other.get("armor", ""), class_tpl.get("proficiencies", {}).get("armor", ""))
        other["weapons"] = _merge_string_csv(other.get("weapons", ""), class_tpl.get("proficiencies", {}).get("weapons", ""))
        other["tools"] = _merge_string_csv(other.get("tools", ""), class_tpl.get("proficiencies", {}).get("tools", ""))
        other["other"] = _merge_string_csv(other.get("other", ""), class_tpl.get("proficiencies", {}).get("other", ""))
        for save in class_tpl.get("save_proficiencies", []) or []:
            prof["saves"][str(save).lower()] = True
        sheet["ability_ids"] = _merge_unique_list(sheet.get("ability_ids"), class_tpl.get("ability_ids"))
        pools = sheet.get("resource_pools", {}) if isinstance(sheet.get("resource_pools"), dict) else {}
        for rid, rv in (class_tpl.get("resource_pools") or {}).items():
            pools[rid] = rv
        sheet["resource_pools"] = pools
        _apply_class_level_grants(sheet, class_tpl, meta["level"])

    for race_src in (race_base, race_tpl):
        if not race_src:
            continue
        if race_src.get("vision_ft") is not None:
            stats["vision_ft"] = int(race_src.get("vision_ft") or 0)
        if race_src.get("movement_ft") is not None:
            stats["movement_ft"] = int(race_src.get("movement_ft") or 0)
        prof["languages"] = _merge_langs(prof.get("languages"), race_src.get("languages") or [])
        for skill in race_src.get("skill_proficiencies", []) or []:
            prof["skills"][str(skill).lower()] = True
        sheet["trait_ids"] = _merge_unique_list(sheet.get("trait_ids"), race_src.get("trait_ids"))
        if race_src.get("resource_pools"):
            pools = sheet.get("resource_pools", {}) if isinstance(sheet.get("resource_pools"), dict) else {}
            for rid, rv in (race_src.get("resource_pools") or {}).items():
                pools[rid] = rv
            sheet["resource_pools"] = pools

    damage_resistances = []
    for src in (race_base, race_tpl):
        damage_resistances = _merge_unique_list(damage_resistances, src.get("damage_resistances") if isinstance(src, dict) else [])
    if damage_resistances:
        resources["damage_resistances"] = damage_resistances

    feature_lines = []
    if class_tpl.get("feature_lines"):
        feature_lines.extend(class_tpl.get("feature_lines") or [])
    if race_base.get("feature_lines"):
        feature_lines.extend(race_base.get("feature_lines") or [])
    if race_tpl.get("feature_lines"):
        feature_lines.extend(race_tpl.get("feature_lines") or [])
    feature_text = "\n".join([f"• {line}" for line in feature_lines if str(line or "").strip()])
    sheet.setdefault("features", {})
    if not isinstance(sheet.get("features"), dict):
        sheet["features"] = {}
    if feature_text:
        existing = str(sheet["features"].get("features_and_traits") or "").strip()
        sheet["features"]["features_and_traits"] = feature_text if not existing else existing

    if race_tpl.get("base_ac_formula") and not sheet["features"].get("attacks_and_spellcasting"):
        sheet["features"]["attacks_and_spellcasting"] = str(race_tpl.get("base_ac_formula"))

    if max_hp_override is None:
        max_hp = _calc_default_max_hp(sheet, class_tpl, meta["level"]) if class_tpl else int(stats.get("max_hp", 10) or 10)
    else:
        try:
            max_hp = max(1, min(int(max_hp_override), 999))
        except Exception:
            max_hp = _calc_default_max_hp(sheet, class_tpl, meta["level"]) if class_tpl else int(stats.get("max_hp", 10) or 10)
    stats["max_hp"] = max_hp
    stats["current_hp"] = max_hp
    resources["current_hp"] = max_hp
    _sync_sheet_derived_resources(sheet)


def _apply_class_level_grants(sheet: Dict[str, Any], class_tpl: Dict[str, Any], level: int) -> None:
    if not isinstance(sheet, dict) or not isinstance(class_tpl, dict):
        return

    grants = class_tpl.get("level_grants") or {}
    if not isinstance(grants, dict):
        return

    if not isinstance(sheet.get("ability_ids"), list):
        sheet["ability_ids"] = []
    if not isinstance(sheet.get("trait_ids"), list):
        sheet["trait_ids"] = []
    if not isinstance(sheet.get("resource_pools"), dict):
        sheet["resource_pools"] = {}
    if not isinstance(sheet.get("features"), dict):
        sheet["features"] = {}
    if not isinstance(sheet.get("combat"), dict):
        sheet["combat"] = {}

    existing_ft = str(sheet["features"].get("features_and_traits") or "").splitlines()
    existing_bullets = {line.strip().lstrip("•").strip() for line in existing_ft if str(line).strip()}
    feature_lines = []

    for lvl_key, spec in grants.items():
        try:
            grant_level = int(lvl_key)
        except Exception:
            continue
        if level < grant_level or not isinstance(spec, dict):
            continue

        # active abilities
        sheet["ability_ids"] = _merge_unique_list(sheet.get("ability_ids"), spec.get("ability_ids"))

        # passive traits
        sheet["trait_ids"] = _merge_unique_list(sheet.get("trait_ids"), spec.get("trait_ids"))

        # resource pools: update/scale, do not only insert once
        pools = sheet.get("resource_pools", {}) if isinstance(sheet.get("resource_pools"), dict) else {}
        for rid, rv in (spec.get("resource_pools") or {}).items():
            if not isinstance(rv, dict):
                continue
            cur_pool = pools.get(rid, {}) if isinstance(pools.get(rid), dict) else {}
            new_max = int(rv.get("max", cur_pool.get("max", 0)) or 0)
            old_max = int(cur_pool.get("max", 0) or 0)
            cur_val = int(cur_pool.get("current", new_max) or new_max)

            cur_pool["max"] = new_max
            cur_pool["refresh"] = str(rv.get("refresh", cur_pool.get("refresh", "")) or "").strip()

            # keep current sensible when max increases
            if old_max <= 0:
                cur_pool["current"] = max(0, min(cur_val, new_max))
            else:
                cur_pool["current"] = max(0, min(cur_val, new_max))

            pools[rid] = cur_pool
        sheet["resource_pools"] = pools

        # combat scalars
        combat = sheet.get("combat", {}) if isinstance(sheet.get("combat"), dict) else {}
        for ck, cv in (spec.get("combat") or {}).items():
            combat[ck] = cv
        sheet["combat"] = combat

        # readable feature lines
        for line in (spec.get("feature_lines") or []):
            s = str(line or "").strip()
            if s and s not in existing_bullets:
                feature_lines.append(s)
                existing_bullets.add(s)

    if feature_lines:
        current = str(sheet["features"].get("features_and_traits") or "").strip()
        addon = "\n".join(f"• {line}" for line in feature_lines)
        sheet["features"]["features_and_traits"] = current + ("\n" if current else "") + addon

def _sync_sheet_derived_resources(sheet: Dict[str, Any]) -> None:
    if not isinstance(sheet, dict):
        return
    meta = sheet.get("meta") if isinstance(sheet.get("meta"), dict) else {}
    level = max(1, min(int((meta or {}).get("level", 1) or 1), 20))
    combat = sheet.setdefault("combat", {}) if isinstance(sheet.get("combat"), dict) else {}
    resources = sheet.setdefault("resources", {}) if isinstance(sheet.get("resources"), dict) else {}
    resource_pools = sheet.setdefault("resource_pools", {}) if isinstance(sheet.get("resource_pools"), dict) else {}
    abilities = sheet.setdefault("abilities", {}) if isinstance(sheet.get("abilities"), dict) else {}

    class_levels = sheet.get("class_levels") if isinstance(sheet.get("class_levels"), dict) else {}
    if class_levels:
        derived_total = sum(max(0, int(v or 0)) for v in class_levels.values())
        primary_class = max(class_levels.items(), key=lambda kv: int(kv[1] or 0))[0]
    else:
        derived_total = level
        primary_class = _slug_key((meta or {}).get("class", ""))
    if derived_total <= 0:
        derived_total = level

    hit_die_sides = int(combat.get("hit_die_sides", 0) or 0)
    tpl = CLASS_TEMPLATES.get(primary_class or "", {}) if primary_class else {}
    #Fighter attacks per action progression
    if primary_class == "fighter":
        attacks = 1
        if derived_total >= 5:
            attacks = 2
        if derived_total >= 11:
            attacks = 3
        if derived_total >= 20:
            attacks = 4
        combat["attacks_per_action"] = attacks
    tpl_hit_die = int((tpl or {}).get("hit_die", 0) or 0)
    if tpl_hit_die > 0:
        hit_die_sides = tpl_hit_die
    if hit_die_sides <= 0:
        hit_die_sides = 8

    hit_total = derived_total
    hit_used = max(0, int(combat.get("hit_dice_used", 0) or 0))
    hit_used = min(hit_used, hit_total)
    combat["hit_die_sides"] = hit_die_sides
    combat["hit_dice_total"] = hit_total
    combat["hit_dice_used"] = hit_used
    resources["hit_dice_remaining"] = max(0, hit_total - hit_used)

    # Fighter derived combat/resource scaling.
    fighter_level = max(0, int(class_levels.get("fighter", 0) or 0))
    if fighter_level > 0:
        attacks = 1
        if fighter_level >= 5:
            attacks = 2
        if fighter_level >= 11:
            attacks = 3
        if fighter_level >= 20:
            attacks = 4
        combat["attacks_per_action"] = attacks

        if fighter_level >= 2:
            max_amt = 2 if fighter_level >= 17 else 1
            pool = resource_pools.setdefault("action_surge", {"current": max_amt, "max": max_amt, "refresh": "short_rest"})
            cur = int(pool.get("current", max_amt) or max_amt)
            pool["max"] = max_amt
            pool["refresh"] = "short_rest"
            pool["current"] = max(0, min(cur, max_amt))

        if fighter_level >= 9:
            max_amt = 1
            if fighter_level >= 13:
                max_amt = 2
            if fighter_level >= 17:
                max_amt = 3
            pool = resource_pools.setdefault("indomitable", {"current": max_amt, "max": max_amt, "refresh": "long_rest"})
            cur = int(pool.get("current", max_amt) or max_amt)
            pool["max"] = max_amt
            pool["refresh"] = "long_rest"
            pool["current"] = max(0, min(cur, max_amt))

    # Barbarian derived combat/resource scaling.
    barbarian_level = max(0, int(class_levels.get("barbarian", 0) or 0))
    if barbarian_level > 0:
        rage_max = 2
        if barbarian_level >= 3:
            rage_max = 3
        if barbarian_level >= 6:
            rage_max = 4
        if barbarian_level >= 12:
            rage_max = 5
        if barbarian_level >= 17:
            rage_max = 6
        rage_pool = resource_pools.setdefault("rage", {"current": rage_max, "max": rage_max, "refresh": "long_rest"})
        rage_cur = int(rage_pool.get("current", rage_max) or rage_max)
        rage_pool["max"] = rage_max
        rage_pool["refresh"] = "long_rest"
        rage_pool["current"] = max(0, min(rage_cur, rage_max))

        rage_damage_bonus = 2
        if barbarian_level >= 9:
            rage_damage_bonus = 3
        if barbarian_level >= 16:
            rage_damage_bonus = 4
        combat["rage_damage_bonus"] = rage_damage_bonus

        attacks = 2 if barbarian_level >= 5 else 1
        combat["attacks_per_action"] = max(int(combat.get("attacks_per_action", 1) or 1), attacks)

        if barbarian_level >= 5:
            equipped = sheet.get("equipped") if isinstance(sheet.get("equipped"), dict) else {}
            armor_id = str(equipped.get("armor_id") or equipped.get("armor") or "").strip()
            armor_name = str(equipped.get("armor_name") or "").strip().lower()
            is_heavy = ("heavy" in armor_name)
            combat["fast_movement_bonus_ft"] = 10 if not armor_id or not is_heavy else 0
        else:
            combat.pop("fast_movement_bonus_ft", None)

        if barbarian_level >= 2:
            combat["danger_sense"] = True
        else:
            combat.pop("danger_sense", None)

        if barbarian_level >= 7:
            combat["initiative_advantage"] = True
        else:
            combat.pop("initiative_advantage", None)

        if barbarian_level >= 20:
            progression = sheet.setdefault("progression", {}) if isinstance(sheet.get("progression"), dict) else {}
            if not bool(progression.get("barbarian_primal_champion_applied", False)):
                abilities = sheet.setdefault("abilities", {}) if isinstance(sheet.get("abilities"), dict) else {}
                for key in ("str", "con"):
                    cur = int(abilities.get(key, 10) or 10)
                    abilities[key] = min(24, cur + 4)
                progression["barbarian_primal_champion_applied"] = True

        combat["relentless_rage_uses"] = int(combat.get("relentless_rage_uses", 0) or 0)

        brutal = 0
        if barbarian_level >= 9:
            brutal = 1
        if barbarian_level >= 13:
            brutal = 2
        if barbarian_level >= 17:
            brutal = 3
        if brutal > 0:
            combat["brutal_critical_dice"] = brutal
        else:
            combat.pop("brutal_critical_dice", None)

    # Rogue derived combat/resource scaling.
    rogue_level = max(0, int(class_levels.get("rogue", 0) or 0))
    if rogue_level > 0:
        combat["sneak_attack_dice"] = max(1, (rogue_level + 1) // 2)

        if rogue_level >= 7:
            combat["evasion"] = True
        else:
            combat.pop("evasion", None)

        if rogue_level >= 11:
            combat["reliable_talent"] = True
        else:
            combat.pop("reliable_talent", None)

        if rogue_level >= 14:
            combat["blindsense_ft"] = 10
        else:
            combat.pop("blindsense_ft", None)

        if rogue_level >= 15:
            combat["slippery_mind"] = True
            prof = sheet.setdefault("proficiencies", {}) if isinstance(sheet.get("proficiencies"), dict) else {}
            saves = prof.setdefault("saves", {}) if isinstance(prof.get("saves"), dict) else {}
            saves["wis"] = True
        else:
            combat.pop("slippery_mind", None)

        if rogue_level >= 18:
            combat["elusive"] = True
        else:
            combat.pop("elusive", None)

        if rogue_level >= 20:
            pool = resource_pools.setdefault("stroke_of_luck", {"current": 1, "max": 1, "refresh": "short_rest"})
            cur = int(pool.get("current", 1) or 1)
            pool["max"] = 1
            pool["refresh"] = "short_rest"
            pool["current"] = max(0, min(cur, 1))

    # Monk base-class scaling.
    monk_level = max(0, int(class_levels.get("monk", 0) or 0))
    if monk_level > 0:
        ki_max = monk_level if monk_level >= 2 else 0
        if ki_max > 0:
            pool = resource_pools.setdefault("ki", {"current": ki_max, "max": ki_max, "refresh": "short_rest"})
            cur = int(pool.get("current", ki_max) or ki_max)
            pool["max"] = ki_max
            pool["refresh"] = "short_rest"
            pool["current"] = max(0, min(cur, ki_max))
        else:
            resource_pools.pop("ki", None)

        martial_die = 4
        if monk_level >= 5:
            martial_die = 6
        if monk_level >= 11:
            martial_die = 8
        if monk_level >= 17:
            martial_die = 10
        combat["martial_arts_die"] = martial_die

        if monk_level >= 5:
            combat["attacks_per_action"] = max(int(combat.get("attacks_per_action", 1) or 1), 2)

        move_bonus = 0
        if monk_level >= 2:
            move_bonus = 10
        if monk_level >= 6:
            move_bonus = 15
        if monk_level >= 10:
            move_bonus = 20
        if monk_level >= 14:
            move_bonus = 25
        if monk_level >= 18:
            move_bonus = 30
        combat["unarmored_movement_bonus_ft"] = move_bonus

        if monk_level >= 3:
            combat["deflect_missiles"] = True
        else:
            combat.pop("deflect_missiles", None)

        if monk_level >= 4:
            combat["slow_fall_reduction"] = monk_level * 5
        else:
            combat.pop("slow_fall_reduction", None)

        if monk_level >= 5:
            combat["stunning_strike"] = True
        else:
            combat.pop("stunning_strike", None)

        if monk_level >= 6:
            combat["ki_empowered_strikes"] = True
        else:
            combat.pop("ki_empowered_strikes", None)

        if monk_level >= 7:
            combat["evasion"] = True
            combat["stillness_of_mind"] = True
        else:
            combat.pop("stillness_of_mind", None)

        if monk_level >= 10:
            combat["purity_of_body"] = True
            resists = set(str(x).strip().lower() for x in (resources.get("damage_immunities") or []) if str(x).strip())
            resists.add("poison")
            resources["damage_immunities"] = sorted(resists)
        else:
            combat.pop("purity_of_body", None)

        if monk_level >= 14:
            combat["diamond_soul"] = True
            prof = sheet.setdefault("proficiencies", {}) if isinstance(sheet.get("proficiencies"), dict) else {}
            saves = prof.setdefault("saves", {}) if isinstance(prof.get("saves"), dict) else {}
            for k in ("str","dex","con","int","wis","cha"):
                saves[k] = True
        else:
            combat.pop("diamond_soul", None)

        if monk_level >= 15:
            combat["timeless_body"] = True
        else:
            combat.pop("timeless_body", None)

        if monk_level >= 18:
            combat["empty_body_available"] = True
        else:
            combat.pop("empty_body_available", None)
            combat["empty_body_active"] = False if bool(combat.get("empty_body_active", False)) else False

        if monk_level >= 20:
            combat["perfect_self"] = True
        else:
            combat.pop("perfect_self", None)

    # Bard base-class scaling.
    bard_level = max(0, int(class_levels.get("bard", 0) or 0))
    if bard_level > 0:
        cha_score = int(abilities.get("cha", 10) or 10)
        cha_mod = (cha_score - 10) // 2
        bardic_max = max(1, cha_mod)
        bardic_die = 6
        if bard_level >= 5:
            bardic_die = 8
        if bard_level >= 10:
            bardic_die = 10
        if bard_level >= 15:
            bardic_die = 12
        bardic_refresh = "short_rest" if bard_level >= 5 else "long_rest"
        pool = resource_pools.setdefault("bardic_inspiration", {"current": bardic_max, "max": bardic_max, "refresh": bardic_refresh})
        cur = int(pool.get("current", bardic_max) or bardic_max)
        pool["max"] = bardic_max
        pool["refresh"] = bardic_refresh
        pool["current"] = max(0, min(cur, bardic_max))
        combat["bardic_inspiration_die"] = bardic_die
        combat["jack_of_all_trades"] = bard_level >= 2
        combat["song_of_rest_die"] = 6 if bard_level >= 2 else 0
        if bard_level >= 9:
            combat["song_of_rest_die"] = 8
        if bard_level >= 13:
            combat["song_of_rest_die"] = 10
        if bard_level >= 17:
            combat["song_of_rest_die"] = 12
        if bard_level >= 20:
            combat["superior_inspiration"] = True
        else:
            combat.pop("superior_inspiration", None)

    # Cleric base-class scaling.
    cleric_level = max(0, int(class_levels.get("cleric", 0) or 0))
    if cleric_level > 0:
        cd_max = 1
        if cleric_level >= 6:
            cd_max = 2
        if cleric_level >= 18:
            cd_max = 3
        pool = resource_pools.setdefault("channel_divinity", {"current": cd_max, "max": cd_max, "refresh": "short_rest"})
        cur = int(pool.get("current", cd_max) or cd_max)
        pool["max"] = cd_max
        pool["refresh"] = "short_rest"
        pool["current"] = max(0, min(cur, cd_max))
        destroy_cr = "1/2"
        if cleric_level >= 8:
            destroy_cr = "1"
        if cleric_level >= 11:
            destroy_cr = "2"
        if cleric_level >= 14:
            destroy_cr = "3"
        if cleric_level >= 17:
            destroy_cr = "4"
        combat["destroy_undead_cr"] = destroy_cr if cleric_level >= 5 else ""
        di_max = 1 if cleric_level >= 10 else 0
        di_pool = resource_pools.setdefault("divine_intervention", {"current": di_max, "max": di_max, "refresh": "long_rest"})
        di_cur = int(di_pool.get("current", di_max) or di_max)
        di_pool["max"] = di_max
        di_pool["refresh"] = "long_rest"
        di_pool["current"] = max(0, min(di_cur, di_max))
        combat["divine_intervention"] = cleric_level >= 10
        combat["improved_divine_intervention"] = cleric_level >= 20

    # Druid base-class scaling.
    druid_level = max(0, int(class_levels.get("druid", 0) or 0))
    if druid_level > 0:
        wild_max = 2 if druid_level >= 2 else 0
        if druid_level >= 20:
            wild_max = 999
        pool = resource_pools.setdefault("wild_shape", {"current": wild_max, "max": wild_max, "refresh": "short_rest"})
        cur = int(pool.get("current", wild_max) or wild_max)
        pool["max"] = wild_max
        pool["refresh"] = "short_rest"
        pool["current"] = max(0, min(cur, wild_max)) if wild_max < 999 else wild_max
        combat["wild_shape_available"] = druid_level >= 2
        combat["wild_shape_max_cr"] = 0
        if druid_level >= 4:
            combat["wild_shape_max_cr"] = 0.5
        if druid_level >= 8:
            combat["wild_shape_max_cr"] = 1
        combat["timeless_body"] = druid_level >= 18
        combat["beast_spells"] = druid_level >= 18
        combat["archdruid"] = druid_level >= 20

    # Ranger base-class scaling.
    ranger_level = max(0, int(class_levels.get("ranger", 0) or 0))
    if ranger_level > 0:
        if ranger_level >= 5:
            combat["attacks_per_action"] = max(int(combat.get("attacks_per_action", 1) or 1), 2)
        combat["lands_stride"] = ranger_level >= 8
        combat["hide_in_plain_sight"] = ranger_level >= 10
        combat["vanish"] = ranger_level >= 14
        combat["feral_senses"] = ranger_level >= 18
        abilities_bonus = combat.get("attack_bonus_bonus") if isinstance(combat.get("attack_bonus_bonus"), dict) else {}
        if ranger_level >= 20:
            abilities_bonus["foe_slayer"] = max((int(abilities.get("wis", 10) or 10) - 10) // 2, 0)
        else:
            abilities_bonus.pop("foe_slayer", None)
        if abilities_bonus:
            combat["attack_bonus_bonus"] = abilities_bonus

    # Sorcerer base-class scaling.
    sorcerer_level = max(0, int(class_levels.get("sorcerer", 0) or 0))
    if sorcerer_level > 0:
        sp_max = sorcerer_level if sorcerer_level >= 2 else 0
        pool = resource_pools.setdefault("sorcery_points", {"current": sp_max, "max": sp_max, "refresh": "long_rest"})
        cur = int(pool.get("current", sp_max) or sp_max)
        pool["max"] = sp_max
        pool["refresh"] = "long_rest"
        pool["current"] = max(0, min(cur, sp_max))
        combat["font_of_magic"] = sorcerer_level >= 2
        combat["metamagic_choices"] = 0
        if sorcerer_level >= 3:
            combat["metamagic_choices"] = 2
        if sorcerer_level >= 10:
            combat["metamagic_choices"] = 3
        if sorcerer_level >= 17:
            combat["metamagic_choices"] = 4
        combat["sorcerous_restoration"] = sorcerer_level >= 20

    # Warlock base-class scaling.
    warlock_level = max(0, int(class_levels.get("warlock", 0) or 0))
    if warlock_level > 0:
        for arcanum_level, unlock_level in ((6, 11), (7, 13), (8, 15), (9, 17)):
            pool_name = f"mystic_arcanum_{arcanum_level}"
            max_amt = 1 if warlock_level >= unlock_level else 0
            pool = resource_pools.setdefault(pool_name, {"current": max_amt, "max": max_amt, "refresh": "long_rest"})
            cur = int(pool.get("current", max_amt) or max_amt)
            pool["max"] = max_amt
            pool["refresh"] = "long_rest"
            pool["current"] = max(0, min(cur, max_amt))
        em_max = 1 if warlock_level >= 20 else 0
        em_pool = resource_pools.setdefault("eldritch_master", {"current": em_max, "max": em_max, "refresh": "long_rest"})
        em_cur = int(em_pool.get("current", em_max) or em_max)
        em_pool["max"] = em_max
        em_pool["refresh"] = "long_rest"
        em_pool["current"] = max(0, min(em_cur, em_max))
        combat["eldritch_master"] = warlock_level >= 20

    # Wizard base-class scaling.
    wizard_level = max(0, int(class_levels.get("wizard", 0) or 0))
    if wizard_level > 0:
        pool = resource_pools.setdefault("arcane_recovery", {"current": 1, "max": 1, "refresh": "long_rest"})
        cur = int(pool.get("current", 1) or 1)
        pool["max"] = 1
        pool["refresh"] = "long_rest"
        pool["current"] = max(0, min(cur, 1))
        arcane_budget = max(1, wizard_level // 2)
        combat["arcane_recovery_levels"] = arcane_budget
        prev_remaining = int(combat.get("arcane_recovery_levels_remaining", arcane_budget) or arcane_budget)
        combat["arcane_recovery_levels_remaining"] = arcane_budget if int(pool.get("current", 0) or 0) > 0 else max(0, min(prev_remaining, arcane_budget))
        combat["spell_mastery"] = wizard_level >= 18
        combat["signature_spells"] = wizard_level >= 20

    # Paladin base-class scaling (non-spell pass).
    pal_level = max(0, int(class_levels.get("paladin", 0) or 0))
    if pal_level > 0:
        cha_score = int(abilities.get("cha", 10) or 10)
        cha_mod = (cha_score - 10) // 2

        # Lay on Hands pool scales with level.
        pool = resource_pools.setdefault("lay_on_hands", {"current": 5 * pal_level, "max": 5 * pal_level, "refresh": "long_rest"})
        max_amt = 5 * pal_level
        cur = int(pool.get("current", max_amt) or max_amt)
        pool["max"] = max_amt
        pool["refresh"] = "long_rest"
        pool["current"] = max(0, min(cur, max_amt))

        # Divine Sense uses: 1 + CHA modifier (minimum 1), refresh long rest.
        divine_max = max(1, 1 + cha_mod)
        ds_pool = resource_pools.setdefault("divine_sense", {"current": divine_max, "max": divine_max, "refresh": "long_rest"})
        ds_cur = int(ds_pool.get("current", divine_max) or divine_max)
        ds_pool["max"] = divine_max
        ds_pool["refresh"] = "long_rest"
        ds_pool["current"] = max(0, min(ds_cur, divine_max))

        # Extra Attack is a displayed/derived scalar only.
        if pal_level >= 5:
            combat["attacks_per_action"] = max(int(combat.get("attacks_per_action", 1) or 1), 2)

        # Aura of Protection: fully apply to the paladin's own saves now and
        # surface radius/bonus flags for broader ally integration later.
        if pal_level >= 6:
            aura_bonus = int(cha_mod)
            combat["aura_of_protection_bonus"] = aura_bonus
            combat["aura_of_protection_radius_ft"] = 30 if pal_level >= 18 else 10
            save_bonus = combat.get("save_bonus") if isinstance(combat.get("save_bonus"), dict) else {}
            for key in ("STR", "DEX", "CON", "INT", "WIS", "CHA"):
                save_bonus[key] = int(aura_bonus)
            combat["save_bonus"] = save_bonus
        else:
            combat.pop("aura_of_protection_bonus", None)
            combat.pop("aura_of_protection_radius_ft", None)

        # Aura of Courage flag/radius for future condition integration.
        if pal_level >= 10:
            combat["aura_of_courage"] = True
            combat["aura_of_courage_radius_ft"] = 30 if pal_level >= 18 else 10
        else:
            combat.pop("aura_of_courage", None)
            combat.pop("aura_of_courage_radius_ft", None)

        # Improved Divine Smite: +1d8 radiant on each melee weapon hit.
        if pal_level >= 11:
            combat["improved_divine_smite_dice"] = 1
        else:
            combat.pop("improved_divine_smite_dice", None)

        # Cleansing Touch uses: CHA modifier minimum 1, refresh long rest.
        if pal_level >= 14:
            ct_max = max(1, cha_mod)
            ct_pool = resource_pools.setdefault("cleansing_touch", {"current": ct_max, "max": ct_max, "refresh": "long_rest"})
            ct_cur = int(ct_pool.get("current", ct_max) or ct_max)
            ct_pool["max"] = ct_max
            ct_pool["refresh"] = "long_rest"
            ct_pool["current"] = max(0, min(ct_cur, ct_max))


def _csv_tokens(value: Any) -> List[str]:
    if isinstance(value, str):
        parts = [x.strip().lower() for x in value.split(",")]
        return [x for x in parts if x]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip().lower() for x in value if str(x).strip()]
    return []


def _has_armor_proficiency(sheet: Dict[str, Any], armor: Dict[str, Any]) -> bool:
    prof = sheet.get("proficiencies") if isinstance(sheet.get("proficiencies"), dict) else {}
    other = prof.get("other") if isinstance(prof.get("other"), dict) else {}
    tokens = set(_csv_tokens(other.get("armor", "")))
    armor_type = str(armor.get("armor_type") or "").strip().lower()
    if "all armor" in tokens:
        return True
    if armor_type and armor_type in tokens:
        return True
    if armor_type and f"{armor_type} armor" in tokens:
        return True
    return False


def _has_weapon_proficiency(sheet: Dict[str, Any], weapon: Dict[str, Any]) -> bool:
    prof = sheet.get("proficiencies") if isinstance(sheet.get("proficiencies"), dict) else {}
    other = prof.get("other") if isinstance(prof.get("other"), dict) else {}
    tokens = set(_csv_tokens(other.get("weapons", "")))
    name = str(weapon.get("name") or "").strip().lower()
    category = str(weapon.get("category") or weapon.get("weapon_category") or weapon.get("proficiency_group") or "").strip().lower()
    if "martial weapons" in tokens:
        return True
    if "simple weapons" in tokens and (category == "simple" or any(k in name for k in ["club","dagger","dart","javelin","mace","quarterstaff","sickle","sling","spear","knife","staff"])):
        return True
    if name and name in tokens:
        return True
    # common explicit proficiencies like shortswords, rapiers, etc.
    singular = name[:-1] if name.endswith('s') else name
    if singular and singular in tokens:
        return True
    return False


def _read_json(path: str, default):
    try:
        if not os.path.exists(path):
            return default
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default

def _write_json(path: str, data: dict):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

def _safe_campaign_id(campaign_id: str) -> str:
    safe = "".join(ch for ch in (campaign_id or "").strip() if ch.isalnum() or ch in ("_", "-", "."))
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid campaign_id")
    return safe

def _campaign_path(campaign_id: str) -> str:
    cid = _safe_campaign_id(campaign_id)
    path = os.path.join(CAMPAIGNS_ROOT, cid)
    if not os.path.isdir(path):
        raise HTTPException(status_code=404, detail=f"Campaign not found: {cid}")
    return path

def _character_path(char_dir: str, character_id: str) -> str:
    safe = "".join(ch for ch in (character_id or "") if ch.isalnum() or ch in ("_", "-", "."))
    if not safe:
        raise HTTPException(status_code=400, detail="Invalid character_id")
    return os.path.join(char_dir, f"{safe}.json")

def ensure_sheet_minimum(sheet: dict, character_id: str) -> dict:
    """Ensure a character sheet has required keys.

    Canonical schema:
      - sheet["stats"] is authoritative for combat-relevant values
        (current_hp, max_hp, defense/ac, movement_ft, vision_ft, attack_modifier)

    Back-compat:
      - base_stats/resources are maintained as mirrors so older code continues to work.

    Phase C additions:
      - lifecycle + meta + abilities + proficiencies support the portal sheet editor.
    """
    if not isinstance(sheet, dict):
        sheet = {}

    # ---- Identity ----
    sheet.setdefault("character_id", character_id)
    sheet.setdefault("player_id", "")
    sheet.setdefault("display_name", character_id)

    # ---- Lifecycle / edit locks ----
    # lifecycle.status: "creating" (editable) or "active" (locked) or "retired"
    sheet.setdefault("lifecycle", {})
    lc = sheet["lifecycle"]
    if not isinstance(lc, dict):
        lc = {}
        sheet["lifecycle"] = lc
    lc.setdefault("status", "active")
    lc.setdefault("created_at", int(time.time()))

    # ---- Core identity / 5e-ish metadata (editor-facing) ----
    sheet.setdefault("meta", {})
    meta = sheet["meta"]
    if not isinstance(meta, dict):
        meta = {}
        sheet["meta"] = meta
    meta.setdefault("class", "")
    meta.setdefault("subclass", "")
    meta.setdefault("level", 1)
    meta.setdefault("race", "")
    meta.setdefault("background", "")
    meta.setdefault("alignment", "")
    meta.setdefault("experience_points", 0)

    # ---- Phase E structured identity / grants ----
    sheet.setdefault("race_id", "")
    sheet.setdefault("base_race_id", "")
    sheet.setdefault("lineage_id", "")
    sheet.setdefault("class_levels", {})
    if not isinstance(sheet.get("class_levels"), dict):
        sheet["class_levels"] = {}
    sheet.setdefault("trait_ids", [])
    if not isinstance(sheet.get("trait_ids"), list):
        sheet["trait_ids"] = []
    sheet.setdefault("ability_ids", [])
    if not isinstance(sheet.get("ability_ids"), list):
        sheet["ability_ids"] = []
    sheet.setdefault("subclasses", {})
    if not isinstance(sheet.get("subclasses"), dict):
        sheet["subclasses"] = {}
    sheet.setdefault("feats", [])
    if not isinstance(sheet.get("feats"), list):
        sheet["feats"] = []
    sheet.setdefault("feat_state", {})
    if not isinstance(sheet.get("feat_state"), dict):
        sheet["feat_state"] = {}
    sheet.setdefault("_feat_applied", {})
    if not isinstance(sheet.get("_feat_applied"), dict):
        sheet["_feat_applied"] = {}
    sheet.setdefault("resource_pools", {})
    if not isinstance(sheet.get("resource_pools"), dict):
        sheet["resource_pools"] = {}
    sheet.setdefault("feature_state", {
        "used_this_turn": [],
        "used_this_round": [],
        "once_per_turn_flags": {},
        "once_per_round_flags": {},
    })
    if not isinstance(sheet.get("feature_state"), dict):
        sheet["feature_state"] = {
            "used_this_turn": [],
            "used_this_round": [],
            "once_per_turn_flags": {},
            "once_per_round_flags": {},
        }

    # ---- Background / personality (classic 5e sheet fields) ----
    sheet.setdefault("background", {})
    bg = sheet["background"]
    if not isinstance(bg, dict):
        bg = {}
        sheet["background"] = bg
    bg.setdefault("personality_traits", "")
    bg.setdefault("ideals", "")
    bg.setdefault("bonds", "")
    bg.setdefault("flaws", "")
    bg.setdefault("backstory", "")

    # ---- Active combat effects (DM-authoritative status display) ----
    sheet.setdefault("combat_effects", [])
    if not isinstance(sheet["combat_effects"], list):
        sheet["combat_effects"] = []

    # ---- Features & Traits ----
    sheet.setdefault("features", {})
    ft = sheet["features"]
    if not isinstance(ft, dict):
        ft = {}
        sheet["features"] = ft
    ft.setdefault("features_and_traits", "")
    ft.setdefault("attacks_and_spellcasting", "")

    # ---- Details (appearance / orgs / treasure) ----
    sheet.setdefault("details", {})
    dt = sheet["details"]
    if not isinstance(dt, dict):
        dt = {}
        sheet["details"] = dt
    for k in ("age", "height", "weight", "eyes", "skin", "hair"):
        dt.setdefault(k, "")
    dt.setdefault("appearance", "")
    dt.setdefault("allies_and_organizations", "")
    dt.setdefault("treasure", "")

    # ---- Currency ----
    sheet.setdefault("currency", {})
    cur = sheet["currency"]
    if not isinstance(cur, dict):
        cur = {}
        sheet["currency"] = cur
    for k in ("cp", "sp", "ep", "gp", "pp"):
        try:
            cur.setdefault(k, 0)
        except Exception:
            cur[k] = 0

    # ---- Combat trackers (non-authoritative helpers) ----
    sheet.setdefault("combat", {})
    cb = sheet["combat"]
    if not isinstance(cb, dict):
        cb = {}
        sheet["combat"] = cb
    cb.setdefault("inspiration", False)
    cb.setdefault("hit_die_sides", 8)
    cb.setdefault("hit_dice_total", 1)
    cb.setdefault("hit_dice_used", 0)
    cb.setdefault("death_saves", {"successes": 0, "failures": 0})
    if not isinstance(cb.get("death_saves"), dict):
        cb["death_saves"] = {"successes": 0, "failures": 0}

    # ---- Spellcasting (Phase F foundation) ----
    ensure_spellcasting_foundation(sheet)

    # ---- Abilities (scores) ----
    sheet.setdefault("abilities", {})
    ab = sheet["abilities"]
    if not isinstance(ab, dict):
        ab = {}
        sheet["abilities"] = ab
    for k in ("str", "dex", "con", "int", "wis", "cha"):
        ab.setdefault(k, 10)

    # ---- Proficiency flags ----
    sheet.setdefault("proficiencies", {})
    prof = sheet["proficiencies"]
    if not isinstance(prof, dict):
        prof = {}
        sheet["proficiencies"] = prof

    # Back-compat arrays (older prototypes)
    prof.setdefault("save_proficiencies", [])   # e.g. ["wis","cha"]
    prof.setdefault("skill_proficiencies", [])  # e.g. ["athletics","perception"]

    # Preferred (Phase C): explicit boolean maps for saves/skills
    prof.setdefault("saves", {})
    prof.setdefault("skills", {})
    if not isinstance(prof.get("saves"), dict):
        prof["saves"] = {}
    if not isinstance(prof.get("skills"), dict):
        prof["skills"] = {}

    # If arrays exist, mirror them into the maps once (non-destructive)
    try:
        for a in (prof.get("save_proficiencies") or []):
            k = str(a).strip().lower()
            if k:
                prof["saves"].setdefault(k, True)
        for s in (prof.get("skill_proficiencies") or []):
            k = str(s).strip().lower()
            if k:
                prof["skills"].setdefault(k, True)
    except Exception:
        pass
    prof.setdefault("languages", [])            # list of {name,speak,read,write}

    prof.setdefault("other", {})  # {armor,weapons,tools,other}
    if not isinstance(prof.get("other"), dict):
        prof["other"] = {}
    prof["other"].setdefault("armor", "")
    prof["other"].setdefault("weapons", "")
    prof["other"].setdefault("tools", "")
    prof["other"].setdefault("other", "")

    # ---- Canonical stats (authoritative) ----
    sheet.setdefault("stats", {})
    st = sheet["stats"]
    if not isinstance(st, dict):
        st = {}
        sheet["stats"] = st

    st.setdefault("max_hp", 10)
    st.setdefault("current_hp", int(st.get("max_hp", 10)))
    st.setdefault("defense_base", 10)
    st.setdefault("defense", int(st.get("defense_base", 10)))
    st.setdefault("movement_ft", 30)
    st.setdefault("attack_modifier", 0)
    st.setdefault("vision_ft", 60)

    # ---- Resource mirrors / canonical soft state ----
    sheet.setdefault("resources", {})
    rs = sheet["resources"]
    if not isinstance(rs, dict):
        rs = {}
        sheet["resources"] = rs
    rs.setdefault("current_hp", int(st.get("current_hp", st.get("max_hp", 10))))
    rs.setdefault("temp_hp", 0)
    rs.setdefault("damage_resistances", [])
    rs.setdefault("hit_dice_remaining", max(0, int((sheet.get("combat", {}) or {}).get("hit_dice_total", 1) or 1) - int((sheet.get("combat", {}) or {}).get("hit_dice_used", 0) or 0)))

    # ---- Equipped ----
    sheet.setdefault("equipped", {})
    eq = sheet["equipped"]
    if not isinstance(eq, dict):
        eq = {}
        sheet["equipped"] = eq
    eq.setdefault("weapon_id", "")
    eq.setdefault("armor_id", "")

    # ---- Legacy mirrors (base_stats/resources) ----
    sheet.setdefault("base_stats", {})
    bs = sheet["base_stats"]
    if not isinstance(bs, dict):
        bs = {}
        sheet["base_stats"] = bs

    # Mirror canonical -> legacy (overwrite to prevent stale 10hp/0hp)
    bs["max_hp"] = int(st.get("max_hp", 10))
    bs["ac_base"] = int(st.get("defense_base", 10))
    bs["ac"] = int(st.get("defense", st.get("defense_base", 10)))
    bs["movement"] = int(st.get("movement_ft", 30))
    bs["attack_modifier"] = int(st.get("attack_modifier", 0))
    bs["vision_ft"] = int(st.get("vision_ft", 60))
    bs["weapon_id"] = (eq.get("weapon_id") or bs.get("weapon_id") or "").strip()
    bs["armor_id"] = (eq.get("armor_id") or bs.get("armor_id") or "").strip()
    bs.setdefault("weapon", "")
    bs.setdefault("armor", "")

    sheet.setdefault("resources", {})
    res = sheet["resources"]
    if not isinstance(res, dict):
        res = {}
        sheet["resources"] = res

    # Mirror canonical -> legacy (overwrite to prevent stale 0)
    res["current_hp"] = int(st.get("current_hp", bs["max_hp"]))
    res.setdefault("temp_hp", 0)

    # ---- Other required sections ----
    sheet.setdefault("inventory", [])
    # Notes are stored server-side and are player-editable. Notes may also be
    # represented in inventory as "note" items (portal convenience).
    sheet.setdefault("notes", [])
    sheet.setdefault("status_effects", [])
    sheet.setdefault("updated_at", int(time.time()))
    return sheet
# ------------------------------------------------------------
# In-memory per-campaign state buckets
# ------------------------------------------------------------
_campaign_state: Dict[str, Dict[str, Any]] = {}

def get_state(campaign_id: str) -> Dict[str, Any]:
    cid = _safe_campaign_id(campaign_id)
    if cid in _campaign_state:
        return _campaign_state[cid]

    path = _campaign_path(cid)

    # Canonical campaign folders (your CampaignLoader creates these)
    char_dir = os.path.join(path, "characters")
    os.makedirs(char_dir, exist_ok=True)

    items_path = os.path.join(path, "items.json")  # your campaign has items.json at root
    pins_path = os.path.join(path, "server", "pins.json")

    st = {
        "campaign_id": cid,
        "campaign_path": path,
        "char_dir": char_dir,
        "items_path": items_path,
        "pins_path": pins_path,

        # Handouts + DM templates
        "handouts_path": os.path.join(path, "server", "handouts.json"),
        "handout_templates_path": os.path.join(path, "server", "handout_templates.json"),

        "handouts_path": os.path.join(path, "server", "handouts.json"),

        "handouts": [],                    # persisted handouts list

        # Option B queues (campaign-scoped)
        "pending_attacks": {},              # pending_attack_id -> dict
        "roll_queue": [],                   # list of to-hit roll submissions
        "damage_roll_queue": [],            # list of damage roll submissions
        "attack_results": {},               # player_id -> list
        "player_messages": {},              # player_id -> list
        # Generic roll request system (Phase C foundation)
        "pending_roll_requests": {},       # request_id -> dict
        "roll_request_results": {},        # player_id -> list of resolved rolls
        "dm_roll_request_results": [],      # DM-facing resolved generic roll queue
        "pending_spell_declarations": [],   # DM-facing spell declaration queue
        "pending_reaction_spell_declarations": [],  # DM-facing reaction-spell declaration queue
        "reaction_response_queue": [],            # player -> DM reaction decisions
        "player_logs": {},                 # player_id -> list of log entries

        # persisted
        "handouts": [],                    # list of handout dicts
        "handout_templates": [],           # list of template dicts
    }

    # Load persisted handouts/templates (best-effort)
    try:
        h = _read_json(st["handouts_path"], [])
        if isinstance(h, list):
            st["handouts"] = h
    except Exception:
        pass

    try:
        t = _read_json(st["handout_templates_path"], [])
        if isinstance(t, list):
            st["handout_templates"] = t
    except Exception:
        pass
    _campaign_state[cid] = st
    return st

def list_campaign_ids() -> List[str]:
    if not os.path.isdir(CAMPAIGNS_ROOT):
        return []
    out = []
    for name in os.listdir(CAMPAIGNS_ROOT):
        p = os.path.join(CAMPAIGNS_ROOT, name)
        if os.path.isdir(p):
            safe = "".join(ch for ch in name if ch.isalnum() or ch in ("_", "-", "."))
            if safe == name:
                out.append(name)
    out.sort()
    return out

# ------------------------------------------------------------
# Sessions (PIN login)
# ------------------------------------------------------------
SESSION_TTL_SECONDS = int(os.environ.get("GRENGINE_SESSION_TTL_SECONDS", str(12 * 60 * 60)))  # 12h
_sessions: Dict[str, Dict[str, Any]] = {}  # token -> {player_id, campaign_id, expires_at, active_character_id}
_rest_states: Dict[str, Dict[str, Any]] = {}  # campaign_id -> rest session dict
_levelup_states: Dict[str, Dict[str, Any]] = {}  # campaign_id -> pending level-up grants by character_id


def _cleanup_sessions(now: float) -> None:
    dead = [tok for tok, s in _sessions.items() if float(s.get("expires_at", 0)) <= now]
    for tok in dead:
        _sessions.pop(tok, None)

def _read_pins(campaign_id: str) -> Dict[str, str]:
    st = get_state(campaign_id)
    pins = _read_json(st["pins_path"], {})
    return pins if isinstance(pins, dict) else {}

def _issue_token(player_id: str, campaign_id: str) -> Tuple[str, int]:
    now = time.time()
    _cleanup_sessions(now)
    tok = secrets.token_urlsafe(32)
    exp = int(now + SESSION_TTL_SECONDS)
    _sessions[tok] = {
        "player_id": player_id,
        "campaign_id": _safe_campaign_id(campaign_id),
        "expires_at": exp,
        "active_character_id": "",
        "issued_at": int(now),
    }
    return tok, exp

def _parse_bearer(authorization: Optional[str]) -> Optional[str]:
    if not authorization:
        return None
    parts = authorization.split()
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None

def require_session(campaign_id: str, authorization: Optional[str] = Header(None)) -> Dict[str, Any]:
    tok = _parse_bearer(authorization)
    if not tok:
        raise HTTPException(status_code=401, detail="Missing Authorization Bearer token")

    now = time.time()
    _cleanup_sessions(now)

    sess = _sessions.get(tok)
    if not sess:
        raise HTTPException(status_code=401, detail="Invalid or expired session token")

    if sess.get("campaign_id") != _safe_campaign_id(campaign_id):
        raise HTTPException(status_code=403, detail="Session campaign mismatch")

    return {"token": tok, **sess}

# ------------------------------------------------------------
# Static root
# ------------------------------------------------------------
@app.get("/")
async def root():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))

# ------------------------------------------------------------
# Public: list campaigns (for portal dropdown)
# ------------------------------------------------------------
@app.get("/api/campaigns")
async def api_list_campaigns():
    return {"campaigns": list_campaign_ids(), "default": DEFAULT_CAMPAIGN_ID}


# ------------------------------------------------------------
# Public: active campaign (single-campaign portal mode)
# ------------------------------------------------------------
@app.get("/api/active_campaign")
async def api_active_campaign():
    # Portal uses this to avoid a campaign dropdown.
    # Server defines the active campaign via GRENGINE_DEFAULT_CAMPAIGN (or default "Test").
    return {"campaign_id": DEFAULT_CAMPAIGN_ID}
# ------------------------------------------------------------
# Auth endpoints
# ------------------------------------------------------------
class LoginRequest(BaseModel):
    player_id: str
    pin: str

@app.post("/api/campaigns/{campaign_id}/auth/login")
async def api_login(campaign_id: str, req: LoginRequest):
    cid = _safe_campaign_id(campaign_id)
    pins = _read_pins(cid)
    if not pins:
        raise HTTPException(
            status_code=500,
            detail=f"pins.json missing or empty for campaign '{cid}'. Create: campaigns/{cid}/server/pins.json"
        )

    player_id = (req.player_id or "").strip()
    pin = (req.pin or "").strip()
    if not player_id or not pin:
        raise HTTPException(status_code=400, detail="player_id and pin required")

    expected = str(pins.get(player_id, "")).strip()
    if expected != pin:
        raise HTTPException(status_code=403, detail="Invalid PIN")

    token, expires_at = _issue_token(player_id, cid)
    return {"ok": True, "token": token, "player_id": player_id, "campaign_id": cid, "expires_at": expires_at}

@app.post("/api/campaigns/{campaign_id}/auth/logout")
async def api_logout(campaign_id: str, sess=Depends(require_session)):
    tok = sess.get("token")
    _sessions.pop(tok, None)
    return {"ok": True}

@app.get("/api/campaigns/{campaign_id}/auth/me")
async def api_me(campaign_id: str, sess=Depends(require_session)):
    return {
        "ok": True,
        "player_id": sess["player_id"],
        "campaign_id": sess["campaign_id"],
        "expires_at": sess["expires_at"],
        "active_character_id": sess.get("active_character_id", "") or ""
    }

# ------------------------------------------------------------
# Character selection (campaign-scoped, session protected)
# ------------------------------------------------------------
def load_character_sheet(campaign_id: str, character_id: str) -> dict:
    st = get_state(campaign_id)
    path = _character_path(st["char_dir"], character_id)
    sheet = _read_json(path, {})
    return sheet if isinstance(sheet, dict) else {}

def save_character_sheet(campaign_id: str, character_id: str, sheet: dict) -> None:
    st = get_state(campaign_id)
    path = _character_path(st["char_dir"], character_id)
    sheet = _recompute_sheet_derived_state(campaign_id, character_id, sheet)
    sheet["character_id"] = character_id
    sheet["updated_at"] = int(time.time() * 1000)
    _write_json(path, sheet)

def list_player_characters(campaign_id: str, player_id: str) -> List[Dict[str, Any]]:
    st = get_state(campaign_id)
    out: List[Dict[str, Any]] = []
    if not os.path.isdir(st["char_dir"]):
        return out

    for fn in os.listdir(st["char_dir"]):
        if not fn.lower().endswith(".json"):
            continue
        try:
            character_id = fn[:-5]
            sheet = load_character_sheet(campaign_id, character_id)
            if not sheet:
                continue
            sheet = ensure_sheet_minimum(sheet, character_id)
            if (sheet.get("player_id") or "").strip() != player_id:
                continue
            out.append({
                "character_id": character_id,
                "display_name": sheet.get("display_name", character_id),
            })
        except Exception:
            continue

    out.sort(key=lambda x: (x.get("display_name") or x["character_id"]).lower())
    return out

@app.get("/api/campaigns/{campaign_id}/characters")
async def api_characters(campaign_id: str, sess=Depends(require_session)):
    player_id = sess["player_id"]
    chars = list_player_characters(campaign_id, player_id)
    return {"player_id": player_id, "characters": chars, "active_character_id": sess.get("active_character_id", "") or ""}

class SelectCharacterRequest(BaseModel):
    character_id: str

@app.post("/api/campaigns/{campaign_id}/characters/select")
async def api_select_character(campaign_id: str, req: SelectCharacterRequest, sess=Depends(require_session)):
    player_id = sess["player_id"]
    cid = _safe_campaign_id(campaign_id)
    char_id = (req.character_id or "").strip()
    sheet = load_character_sheet(cid, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != player_id:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    tok = sess["token"]
    _sessions[tok]["active_character_id"] = char_id
    return {"ok": True, "active_character_id": char_id}


# ------------------------------------------------------------
# Player sheet endpoints (Phase C1)
# ------------------------------------------------------------
def _require_active_character(sess: Dict[str, Any]) -> str:
    cid = (sess.get("active_character_id") or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="No active character selected")
    return cid

def _compute_prof_bonus(level: int) -> int:
    try:
        lvl = int(level)
    except Exception:
        lvl = 1
    if lvl >= 17:
        return 6
    if lvl >= 13:
        return 5
    if lvl >= 9:
        return 4
    if lvl >= 5:
        return 3
    return 2

def _is_creating(sheet: Dict[str, Any]) -> bool:
    lc = sheet.get("lifecycle", {}) or {}
    return str(lc.get("status", "active")).strip().lower() == "creating"

_ALLOWED_ALWAYS = {
    "stats.current_hp",
    "resources.current_hp",
    "resources.temp_hp",
    "stats.attack_modifier",
}

# Always-editable prefixes (player-facing, non-combat-breaking fields)
_ALLOWED_ALWAYS_PREFIXES = (
    "background.",
    "features.",
    # Languages gate readable handouts and must remain editable post-finalize.
    "proficiencies.languages",
    "proficiencies.languages.",
    "spellcasting.",
    "currency.",
    "details.",
    "combat.",
)
_ALLOWED_CREATING_PREFIXES = (
    "meta.",
    "abilities.",
    "proficiencies.",
    "stats.",
    "base_stats.",
    "equipped.",
    "background.",
    "features.",
    "spellcasting.",
    "currency.",
    "details.",
    "combat.",
)

def _set_path(obj: Dict[str, Any], path_str: str, value: Any) -> None:
    parts = path_str.split(".")
    cur = obj
    for p in parts[:-1]:
        if p not in cur or not isinstance(cur.get(p), dict):
            cur[p] = {}
        cur = cur[p]
    cur[parts[-1]] = value

def _get_path(obj: Dict[str, Any], path_str: str, default=None):
    parts = path_str.split(".")
    cur = obj
    for p in parts:
        if not isinstance(cur, dict) or p not in cur:
            return default
        cur = cur[p]
    return cur

class CreateCharacterRequest(BaseModel):
    display_name: str = Field("", description="Visible name")
    character_id: str = Field("", description="Optional explicit id; otherwise generated")
    class_name: str = Field("", description="Requested class name")
    race: str = Field("", description="Requested race/subrace name")
    background: str = Field("", description="Requested background")
    level: int = Field(1, ge=1, le=20, description="Starting level")
    max_hp: Optional[int] = Field(None, ge=1, le=999, description="Optional HP override during creation")
    auto_apply_defaults: bool = Field(True, description="Apply server-side class/race autofill")

@app.post("/api/campaigns/{campaign_id}/characters/create")
async def api_create_character(campaign_id: str, req: CreateCharacterRequest, sess=Depends(require_session)):
    st = get_state(campaign_id)
    player_id = sess["player_id"]

    name = (req.display_name or "").strip() or "New Character"
    cid_raw = (req.character_id or "").strip()

    if cid_raw:
        character_id = "".join(ch for ch in cid_raw if ch.isalnum() or ch in ("_", "-", "."))
    else:
        slug = "".join(ch for ch in name.lower() if ch.isalnum() or ch in ("_", "-", "."))[:24].strip(".-_")
        character_id = (slug or "pc") + "_" + uuid.uuid4().hex[:6]

    existing = load_character_sheet(campaign_id, character_id)
    if existing:
        raise HTTPException(status_code=409, detail="Character_id already exists")

    sheet = ensure_sheet_minimum({}, character_id)
    sheet["player_id"] = player_id
    sheet["display_name"] = name
    sheet.setdefault("lifecycle", {})
    sheet["lifecycle"]["status"] = "creating"
    sheet["lifecycle"]["created_at"] = int(time.time())

    if (req.background or "").strip():
        sheet.setdefault("meta", {})
        sheet["meta"]["background"] = (req.background or "").strip()
    if req.auto_apply_defaults:
        _apply_creation_autofill(sheet, req.class_name, req.race, int(req.level or 1), req.max_hp)
    else:
        sheet.setdefault("meta", {})
        sheet["meta"]["class"] = (req.class_name or "").strip()
        sheet["meta"]["race"] = (req.race or "").strip()
        sheet["meta"]["level"] = max(1, min(int(req.level or 1), 20))
        if req.max_hp is not None:
            sheet.setdefault("stats", {})
            sheet["stats"]["max_hp"] = int(req.max_hp)
            sheet["stats"]["current_hp"] = int(req.max_hp)
            sheet.setdefault("resources", {})
            sheet["resources"]["current_hp"] = int(req.max_hp)

    save_character_sheet(campaign_id, character_id, sheet)
    return {"ok": True, "character_id": character_id, "display_name": name}

@app.get("/api/campaigns/{campaign_id}/sheet/mine")
async def api_sheet_mine(campaign_id: str, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    sheet = _recompute_sheet_derived_state(campaign_id, char_id, sheet)

    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    try:
        lvl = int((sheet.get("meta", {}) or {}).get("level", 1))
    except Exception:
        lvl = 1
    sheet["_derived"] = {"proficiency_bonus": _compute_prof_bonus(lvl)}
    return JSONResponse(sheet)

class PatchSheetRequest(BaseModel):
    patch: Dict[str, Any] = Field(default_factory=dict, description="Nested patch object (limited fields)")
    finalize: bool = Field(False, description="If true, attempt to finalize character (creating->active)")


class AddNoteRequest(BaseModel):
    title: str = ""
    text: str = ""


class UseAbilityRequest(BaseModel):
    ability_id: str = ""
    amount: Optional[int] = None
    target_character_id: Optional[str] = None
    mode: Optional[str] = None


class ConsumeSpellSlotRequest(BaseModel):
    slot_level: int = Field(..., ge=0, le=9)
    count: int = Field(1, ge=1, le=9)
    spell_id: str = ""
    slot_source: str = "auto"


class DeclareSpellRequest(BaseModel):
    spell_id: str = ""
    slot_level: Optional[int] = Field(None, ge=0, le=9)
    consume_slot: bool = True
    upcast_level: Optional[int] = Field(None, ge=0, le=9)
    slot_source: str = "auto"
    metamagic_options: List[str] = Field(default_factory=list)
    target_hint: str = ""
    notes: str = ""


class SpellListUpdateRequest(BaseModel):
    spell_ids: List[str] = Field(default_factory=list)
    replace: bool = True


class WizardSpellbookLearnRequest(BaseModel):
    spell_id: str = ""


def _validate_spell_list_update(campaign_id: str, sheet: Dict[str, Any], kind: str, spell_ids: List[str], *, replacing: bool = True, character_id: str = "") -> List[str]:
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    cid = _safe_campaign_id(campaign_id)
    state = _levelup_states.get(cid, {}) if isinstance(_levelup_states.get(cid, {}), dict) else {}
    spec = state.get(str(character_id or "").strip()) if str(character_id or "").strip() else None
    if isinstance(spec, dict):
        preview = _levelup_preview_sheet(campaign_id, str(character_id or "").strip(), sheet, int(spec.get("target_level", ((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("level", 1)) or 1))
        preview_sc = preview.get("spellcasting") if isinstance(preview.get("spellcasting"), dict) else {}
        if isinstance(preview_sc, dict) and preview_sc:
            sc = preview_sc
    cleaned = _spell_unique([str(x).strip() for x in (spell_ids or []) if str(x).strip()])
    active_classes = _spell_listify(sc.get("spellcasting_classes"))
    if kind == "cantrips":
        allowed = set(_spell_listify(sc.get("allowed_cantrip_ids")))
        limit = max(0, int(sc.get("cantrip_limit", 0) or 0))
    elif kind in {"known", "spellbook", "prepared", "bonus"}:
        allowed = set(_spell_listify(sc.get("allowed_leveled_spell_ids")))
        if kind == "known":
            limit = max(0, int(sc.get("known_limit", 0) or 0))
        elif kind == "prepared":
            limit = max(0, int(sc.get("preparation_max", 0) or 0))
        elif kind == "bonus":
            limit = max(0, int(sc.get("bonus_spell_limit", 0) or 0))
        else:
            limit = 0
    else:
        allowed = set(_spell_listify(sc.get("allowed_spell_ids")))
        limit = 0

    spells_db = load_spells_db_for_campaign(campaign_id) if campaign_id else {}
    for sid in cleaned:
        row = (spells_db or {}).get(sid) if isinstance(spells_db, dict) else None
        if not isinstance(row, dict):
            raise HTTPException(status_code=400, detail=f"Unknown spell id: {sid}")
        if allowed and sid not in allowed:
            raise HTTPException(status_code=400, detail=f"Spell not legal right now: {sid}")
        if kind == "spellbook":
            if "wizard" not in set(active_classes):
                raise HTTPException(status_code=400, detail="Only Wizards can modify a spellbook")
        elif kind == "prepared":
            mode = str(sc.get("known_mode") or "").strip().lower()
            if mode != "prepared":
                raise HTTPException(status_code=400, detail="Active character is not using a prepared-spell model")
            if "wizard" in set(active_classes):
                spellbook_ids = set(_spell_listify(sc.get("spellbook_spells") or sheet.get("spellbook_spells")))
                if sid not in spellbook_ids:
                    raise HTTPException(status_code=400, detail=f"Prepared spell must be in the spellbook: {sid}")
        elif kind == "known":
            mode = str(sc.get("known_mode") or "").strip().lower()
            if mode != "known":
                if "wizard" in set(active_classes):
                    # known UI aliases to spellbook; validation already covered by allowed set
                    pass
                else:
                    raise HTTPException(status_code=400, detail="Active character is not using a known-spells model")
        elif kind == "bonus" and "bard" not in set(active_classes):
            raise HTTPException(status_code=400, detail="Active character does not currently support bonus off-list spells")

    if replacing and limit > 0 and len(cleaned) > limit:
        label = {"cantrips": "cantrips", "known": "known spells", "prepared": "prepared spells", "bonus": "bonus spells"}.get(kind, kind)
        raise HTTPException(status_code=400, detail=f"Too many {label}: max {limit}")
    return cleaned


class MetamagicSelectionRequest(BaseModel):
    option_ids: List[str] = Field(default_factory=list)
    replace: bool = True


class WizardFeatureSelectionRequest(BaseModel):
    spell_ids: List[str] = Field(default_factory=list)
    replace: bool = True


class RestRequest(BaseModel):
    rest_type: str = "short_rest"
    spend_hit_dice: Optional[int] = None


class DMRestControlRequest(BaseModel):
    rest_type: str = "short_rest"


class ShortRestRollRequest(BaseModel):
    spend_hit_dice: int = 1


def _active_rest_participants(campaign_id: str) -> List[Dict[str, str]]:
    cid = _safe_campaign_id(campaign_id)
    out: List[Dict[str, str]] = []
    seen = set()
    now = time.time()
    _cleanup_sessions(now)
    for sess in list(_sessions.values()):
        try:
            if str(sess.get("campaign_id") or "") != cid:
                continue
            char_id = str(sess.get("active_character_id") or "").strip()
            player_id = str(sess.get("player_id") or "").strip()
            if not char_id or char_id in seen:
                continue
            out.append({"character_id": char_id, "player_id": player_id})
            seen.add(char_id)
        except Exception:
            continue
    return out


def _get_rest_state(campaign_id: str) -> Dict[str, Any]:
    state = _rest_states.get(_safe_campaign_id(campaign_id)) or {}
    return state if isinstance(state, dict) else {}


def _set_rest_state(campaign_id: str, state: Dict[str, Any]) -> None:
    _rest_states[_safe_campaign_id(campaign_id)] = state


def _clear_rest_state(campaign_id: str) -> None:
    _rest_states.pop(_safe_campaign_id(campaign_id), None)


def _serialize_rest_state_for_character(campaign_id: str, character_id: str) -> Dict[str, Any]:
    state = _get_rest_state(campaign_id)
    if not state:
        return {"active": False}
    participants = state.get("participants") if isinstance(state.get("participants"), dict) else {}
    if character_id not in participants:
        return {"active": False}
    pdata = participants.get(character_id) if isinstance(participants.get(character_id), dict) else {}
    return {
        "active": str(state.get("status") or "") == "active",
        "rest_type": str(state.get("type") or ""),
        "status": str(state.get("status") or ""),
        "rest_id": str(state.get("rest_id") or ""),
        "character_id": character_id,
        "done": bool(pdata.get("done", False)),
        "done_count": sum(1 for v in participants.values() if isinstance(v, dict) and v.get("done")),
        "participant_count": len(participants),
        "participants": [
            {
                "character_id": cid,
                "player_id": str((v or {}).get("player_id") or ""),
                "done": bool((v or {}).get("done", False)),
            }
            for cid, v in participants.items()
        ],
    }


def _asi_levels_for_class(class_key: str) -> Set[int]:
    return asi_levels_for_class(class_key)



def _levelup_preview_sheet(campaign_id: str, character_id: str, sheet: Dict[str, Any], target_level: int, spec: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    preview = deepcopy(sheet if isinstance(sheet, dict) else {})
    preview = ensure_sheet_minimum(preview, character_id)
    meta = preview.setdefault("meta", {}) if isinstance(preview.get("meta"), dict) else {}
    class_levels = preview.setdefault("class_levels", {}) if isinstance(preview.get("class_levels"), dict) else {}
    class_key = _slug_key(meta.get("class", ""))
    meta["level"] = max(1, min(20, int(target_level or meta.get("level", 1) or 1)))
    if class_key:
        class_levels[class_key] = meta["level"]
        preview["class_levels"] = class_levels
    if isinstance(spec, dict):
        subclass_id = str(spec.get("subclass_id") or "").strip().lower()
        if class_key and subclass_id:
            subclasses = preview.setdefault("subclasses", {}) if isinstance(preview.get("subclasses"), dict) else {}
            subclasses[class_key] = subclass_id
            preview["subclasses"] = subclasses
        feat_ids = sanitize_feat_ids(spec.get("feat_ids") if isinstance(spec.get("feat_ids"), list) else [])
        if feat_ids:
            preview["feats"] = sanitize_feat_ids(list(preview.get("feats") or []) + feat_ids)
        feat_state = preview.setdefault("feat_state", {}) if isinstance(preview.get("feat_state"), dict) else {}
        pending_feat_state = spec.get("feat_state") if isinstance(spec.get("feat_state"), dict) else {}
        for feat_id, row in pending_feat_state.items():
            if isinstance(row, dict):
                feat_state[str(feat_id).strip().lower()] = dict(row)
        preview["feat_state"] = feat_state
    _recompute_sheet_derived_state(campaign_id, character_id, preview)
    return preview


def _levelup_choice_summary(campaign_id: str, character_id: str, sheet: Dict[str, Any], spec: Dict[str, Any]) -> Dict[str, Any]:
    current = ensure_sheet_minimum(deepcopy(sheet), character_id)
    _recompute_sheet_derived_state(campaign_id, character_id, current)
    target_level = max(1, min(20, int(spec.get("target_level", ((current.get("meta") or {}).get("level", 1))) or 1)))
    preview = _levelup_preview_sheet(campaign_id, character_id, current, target_level, spec)

    cur_sc = current.get("spellcasting") if isinstance(current.get("spellcasting"), dict) else {}
    tgt_sc = preview.get("spellcasting") if isinstance(preview.get("spellcasting"), dict) else {}

    def _list_len(source, key):
        return len(_spell_listify((source or {}).get(key)))

    current_cantrips = _list_len(cur_sc, "cantrips")
    current_known = _list_len(cur_sc, "known_spells")
    current_spellbook = _list_len(cur_sc, "spellbook_spells")
    current_bonus = _list_len(cur_sc, "bonus_spell_ids")
    current_meta = _list_len(cur_sc, "metamagic_options")

    target_cantrips = max(current_cantrips, int(tgt_sc.get("cantrip_limit", current_cantrips) or current_cantrips))
    target_known = max(current_known, int(tgt_sc.get("known_limit", current_known) or current_known))
    target_spellbook = max(current_spellbook, int(tgt_sc.get("spellbook_minimum", current_spellbook) or current_spellbook))
    target_bonus = max(current_bonus, int(tgt_sc.get("bonus_spell_limit", current_bonus) or current_bonus))
    target_meta = max(current_meta, int(tgt_sc.get("metamagic_choice_limit", current_meta) or current_meta))

    class_key = _slug_key((((current.get("meta") or {}) if isinstance(current.get("meta"), dict) else {}).get("class", "")))
    current_subclasses = current.get("subclasses") if isinstance(current.get("subclasses"), dict) else {}
    preview_subclasses = preview.get("subclasses") if isinstance(preview.get("subclasses"), dict) else {}
    subclass_required = class_needs_subclass_choice(class_key, target_level, current)
    feat_options = feat_display_options()
    current_feats = sanitize_feat_ids(current.get("feats") if isinstance(current.get("feats"), list) else [])
    preview_feats = sanitize_feat_ids(preview.get("feats") if isinstance(preview.get("feats"), list) else [])
    out = {
        "current_level": int(((current.get("meta") or {}) if isinstance(current.get("meta"), dict) else {}).get("level", 1) or 1),
        "target_level": int(target_level),
        "current_counts": {
            "cantrips": current_cantrips,
            "known_spells": current_known,
            "spellbook_spells": current_spellbook,
            "bonus_spell_ids": current_bonus,
            "metamagic_options": current_meta,
        },
        "target_counts": {
            "cantrips": target_cantrips,
            "known_spells": target_known,
            "spellbook_spells": target_spellbook,
            "bonus_spell_ids": target_bonus,
            "metamagic_options": target_meta,
        },
        "pending": {
            "cantrips": max(0, target_cantrips - current_cantrips),
            "known_spells": max(0, target_known - current_known),
            "spellbook_spells": max(0, target_spellbook - current_spellbook),
            "bonus_spell_ids": max(0, target_bonus - current_bonus),
            "metamagic_options": max(0, target_meta - current_meta),
        },
        "target_max_spell_level": max(0, int(tgt_sc.get("sanitized_spell_state", {}).get("max_spell_level", 0) or 0)),
        "preview_spellcasting": {
            "known_mode": str(tgt_sc.get("known_mode", "") or ""),
            "cantrip_limit": int(tgt_sc.get("cantrip_limit", 0) or 0),
            "known_limit": int(tgt_sc.get("known_limit", 0) or 0),
            "preparation_max": int(tgt_sc.get("preparation_max", 0) or 0),
            "spellbook_minimum": int(tgt_sc.get("spellbook_minimum", 0) or 0),
            "bonus_spell_limit": int(tgt_sc.get("bonus_spell_limit", 0) or 0),
            "metamagic_choice_limit": int(tgt_sc.get("metamagic_choice_limit", 0) or 0),
            "allowed_spell_ids": list(tgt_sc.get("allowed_spell_ids") or []),
            "allowed_cantrip_ids": list(tgt_sc.get("allowed_cantrip_ids") or []),
            "allowed_leveled_spell_ids": list(tgt_sc.get("allowed_leveled_spell_ids") or []),
            "spellcasting_classes": list(tgt_sc.get("spellcasting_classes") or []),
            "class": str(tgt_sc.get("class", "") or ""),
            "ability": str(tgt_sc.get("ability", "") or ""),
            "save_dc": int(tgt_sc.get("save_dc", 0) or 0),
            "attack_bonus": int(tgt_sc.get("attack_bonus", 0) or 0),
            "spells": deepcopy(tgt_sc.get("spells") or {}),
        },
        "subclass": {
            "required": bool(subclass_required),
            "class_key": class_key,
            "unlock_level": int(subclass_unlock_level(class_key) or 0),
            "current": str(current_subclasses.get(class_key) or ""),
            "preview": str(preview_subclasses.get(class_key) or spec.get("subclass_id") or ""),
            "options": get_subclasses_for_class(class_key),
        },
        "feat_progression": {
            "requires_choice": bool(spec.get("requires_feat_or_asi", False)),
            "feat_mode": str(spec.get("feat_mode") or ""),
            "asi_points": int(spec.get("asi_points", 0) or 0),
            "current_feats": list(current_feats),
            "preview_feats": list(preview_feats),
            "selected_feat_ids": list(spec.get("feat_ids") or []),
            "options": feat_options,
        },
    }
    return out


def _levelup_pending_requirements(campaign_id: str, character_id: str, sheet: Dict[str, Any], spec: Dict[str, Any]) -> List[str]:
    summary = _levelup_choice_summary(campaign_id, character_id, sheet, spec)
    cur = summary.get("current_counts") or {}
    tgt = summary.get("target_counts") or {}
    msgs: List[str] = []
    labels = {
        "cantrips": "cantrips",
        "known_spells": "known spells",
        "spellbook_spells": "spellbook spells",
        "bonus_spell_ids": "bonus spells",
        "metamagic_options": "metamagic options",
    }
    for key, label in labels.items():
        c = int(cur.get(key, 0) or 0)
        t = int(tgt.get(key, c) or c)
        if t > c:
            msgs.append(f"Choose {t - c} more {label}")
    subclass_info = summary.get("subclass") if isinstance(summary.get("subclass"), dict) else {}
    if bool(subclass_info.get("required")) and not str(spec.get("subclass_id") or '').strip():
        msgs.append("Choose a subclass")
    if bool(spec.get("requires_feat_or_asi", False)):
        feat_mode = str(spec.get("feat_mode") or '').strip().lower()
        if feat_mode not in {'asi', 'feat'}:
            msgs.append("Choose Ability Score Improvement or feat")
        elif feat_mode == 'feat':
            feat_ids = sanitize_feat_ids(spec.get("feat_ids") if isinstance(spec.get("feat_ids"), list) else [])
            if not feat_ids:
                msgs.append("Choose a feat")
            for feat_id in feat_ids:
                err = validate_feat_choice(feat_id, (spec.get('feat_state') or {}).get(feat_id) if isinstance(spec.get('feat_state'), dict) else None)
                if err:
                    msgs.append(err)
    elif bool(spec.get("requires_asi", False)):
        if not bool(spec.get("asi_applied", False)):
            inc_a = str(spec.get('increase_a') or '').strip().lower()
            inc_b = str(spec.get('increase_b') or '').strip().lower()
            if inc_a not in {'str','dex','con','int','wis','cha'} or inc_b not in {'str','dex','con','int','wis','cha'}:
                msgs.append('Choose two ability score increases')
    return msgs

def _serialize_levelup_state_for_character(campaign_id: str, character_id: str) -> Dict[str, Any]:
    state = _levelup_states.get(_safe_campaign_id(campaign_id), {})
    spec = state.get(str(character_id or "").strip()) if isinstance(state, dict) else None
    if not isinstance(spec, dict):
        return {"active": False}
    sheet = load_character_sheet(campaign_id, character_id)
    if not sheet:
        return {"active": False}
    sheet = ensure_sheet_minimum(sheet, character_id)
    summary = _levelup_choice_summary(campaign_id, character_id, sheet, spec)
    return {
        "active": True,
        "character_id": str(character_id or "").strip(),
        "target_level": int(spec.get("target_level", 1) or 1),
        "class_key": str(spec.get("class_key", "") or ""),
        "class_name": str(spec.get("class_name", "") or ""),
        "requires_asi": bool(spec.get("requires_asi", False)),
        "requires_feat_or_asi": bool(spec.get("requires_feat_or_asi", False)),
        "asi_points": int(spec.get("asi_points", 0) or 0),
        "feat_mode": str(spec.get("feat_mode", "") or ""),
        "feat_ids": list(spec.get("feat_ids") or []),
        "subclass_id": str(spec.get("subclass_id", "") or ""),
        "granted_at": int(spec.get("granted_at", 0) or 0),
        "choices": summary,
    }


def _grant_levelup_spec_for_sheet(sheet: Dict[str, Any]) -> Dict[str, Any]:
    meta = sheet.get("meta") if isinstance(sheet.get("meta"), dict) else {}
    class_key = _slug_key((meta or {}).get("class", ""))
    current_level = max(1, int((meta or {}).get("level", 1) or 1))
    target_level = min(20, current_level + 1)
    unlock_level = int(subclass_unlock_level(class_key) or 0)
    requires_subclass = class_needs_subclass_choice(class_key, target_level, sheet)
    requires_choice = target_level in _asi_levels_for_class(class_key)
    return {
        "target_level": target_level,
        "class_key": class_key,
        "class_name": str((meta or {}).get("class", "") or ""),
        "requires_asi": bool(requires_choice),
        "requires_feat_or_asi": bool(requires_choice),
        "asi_points": 2 if requires_choice else 0,
        "requires_subclass": bool(requires_subclass),
        "subclass_unlock_level": unlock_level,
        "subclass_id": "",
        "feat_mode": "",
        "feat_ids": [],
        "feat_state": {},
        "granted_at": int(time.time()),
    }


def _apply_levelup_asi(sheet: Dict[str, Any], increase_a: str, increase_b: str) -> None:
    allowed = {"str", "dex", "con", "int", "wis", "cha"}
    inc_a = str(increase_a or "").strip().lower()
    inc_b = str(increase_b or "").strip().lower()
    if inc_a not in allowed or inc_b not in allowed:
        raise HTTPException(status_code=400, detail="Two valid ability score choices are required")
    abilities = sheet.setdefault("abilities", {}) if isinstance(sheet.get("abilities"), dict) else {}
    if inc_a == inc_b:
        cur = int(abilities.get(inc_a, 10) or 10)
        abilities[inc_a] = min(20, cur + 2)
    else:
        cur_a = int(abilities.get(inc_a, 10) or 10)
        cur_b = int(abilities.get(inc_b, 10) or 10)
        abilities[inc_a] = min(20, cur_a + 1)
        abilities[inc_b] = min(20, cur_b + 1)


def _finalize_levelup_apply(campaign_id: str, char_id: str, sheet: Dict[str, Any], spec: Dict[str, Any], hp_gain: int) -> Dict[str, Any]:
    target_level = max(1, min(20, int(spec.get("target_level", 1) or 1)))
    meta = sheet.setdefault("meta", {}) if isinstance(sheet.get("meta"), dict) else {}
    stats = sheet.setdefault("stats", {}) if isinstance(sheet.get("stats"), dict) else {}
    resources = sheet.setdefault("resources", {}) if isinstance(sheet.get("resources"), dict) else {}

    old_max_hp = max(1, int(stats.get("max_hp", 1) or 1))
    old_current_hp = max(0, int(stats.get("current_hp", old_max_hp) or old_max_hp))

    meta["level"] = target_level
    class_key = _slug_key(meta.get("class", ""))
    if class_key:
        if not isinstance(sheet.get("class_levels"), dict):
            sheet["class_levels"] = {}
        sheet["class_levels"][class_key] = target_level

    hp_gain = max(1, int(hp_gain or 1))
    new_max_hp = old_max_hp + hp_gain
    stats["max_hp"] = new_max_hp
    stats["current_hp"] = max(0, min(new_max_hp, old_current_hp + hp_gain))
    resources["current_hp"] = stats["current_hp"]

    subclasses = sheet.setdefault("subclasses", {}) if isinstance(sheet.get("subclasses"), dict) else {}
    subclass_id = str(spec.get("subclass_id") or "").strip().lower()
    if class_key and subclass_id:
        subclasses[class_key] = subclass_id
        sheet["subclasses"] = subclasses
        if isinstance(meta, dict):
            meta["subclass"] = subclass_id.replace("_", " ").title()

    if str(spec.get("feat_mode") or "").strip().lower() == "feat":
        feat_ids = sanitize_feat_ids(spec.get("feat_ids") if isinstance(spec.get("feat_ids"), list) else [])
        current_feats = sanitize_feat_ids(sheet.get("feats") if isinstance(sheet.get("feats"), list) else [])
        feat_state = sheet.setdefault("feat_state", {}) if isinstance(sheet.get("feat_state"), dict) else {}
        pending_feat_state = spec.get("feat_state") if isinstance(spec.get("feat_state"), dict) else {}
        for feat_id in feat_ids:
            if feat_id not in current_feats:
                current_feats.append(feat_id)
                row_state = pending_feat_state.get(feat_id) if isinstance(pending_feat_state.get(feat_id), dict) else {}
                if row_state:
                    feat_state[feat_id] = dict(row_state)
                apply_feat_on_selection(sheet, feat_id, row_state)
        sheet["feats"] = current_feats
        sheet["feat_state"] = feat_state

    sheet = _recompute_sheet_derived_state(campaign_id, char_id, sheet)
    save_character_sheet(campaign_id, char_id, sheet)
    return sheet


def _create_levelup_hp_request(campaign_id: str, player_id: str, char_id: str, hit_die_sides: int) -> Dict[str, Any]:
    st = get_state(campaign_id)
    now = time.time()
    _cleanup_expired_roll_requests(st, now)
    request_id = uuid.uuid4().hex
    rr = {
        "request_id": request_id,
        "character_id": str(char_id or "").strip(),
        "player_id": str(player_id or "").strip(),
        "roll_kind": "levelup_hit_die",
        "expected_sides": int(hit_die_sides),
        "expected_count_min": 1,
        "expected_count_max": 1,
        "adv_mode": "normal",
        "dc": None,
        "label": f"Level Up HP (d{int(hit_die_sides)})",
        "context": {},
        "created_at": now,
        "expires_at": now + 180,
    }
    st.setdefault("pending_roll_requests", {})[request_id] = rr
    return {
        "request_id": request_id,
        "roll_kind": "levelup_hit_die",
        "expected_sides": int(hit_die_sides),
        "expected_count_min": 1,
        "expected_count_max": 1,
        "adv_mode": "normal",
        "label": rr["label"],
        "context": {},
        "expires_at": rr["expires_at"],
    }


def _active_session_characters_for_campaign(campaign_id: str) -> List[str]:
    cid = _safe_campaign_id(campaign_id)
    out: List[str] = []
    seen = set()
    now = time.time()
    _cleanup_sessions(now)
    for sess in _sessions.values():
        if sess.get("campaign_id") != cid:
            continue
        char_id = str(sess.get("active_character_id") or "").strip()
        if not char_id or char_id in seen:
            continue
        out.append(char_id)
        seen.add(char_id)
    return out

class DMGrantLevelCharacterBody(BaseModel):
    character_id: str

class LevelUpSubmitRequest(BaseModel):
    increase_a: str = ""
    increase_b: str = ""
    hp_gain_method: str = "average"
    subclass_id: str = ""
    feat_mode: str = ""
    feat_ids: List[str] = Field(default_factory=list)
    feat_state: Dict[str, Any] = Field(default_factory=dict)


@app.get("/api/campaigns/{campaign_id}/dm/levelup/active_characters")
async def api_dm_levelup_active_characters(campaign_id: str):
    ids = _active_session_characters_for_campaign(campaign_id)
    out: List[Dict[str, Any]] = []
    for character_id in ids:
        sheet = load_character_sheet(campaign_id, character_id)
        if not sheet:
            continue
        sheet = ensure_sheet_minimum(sheet, character_id)
        out.append({
            "character_id": character_id,
            "display_name": str(sheet.get("display_name") or character_id),
            "player_id": str(sheet.get("player_id") or ""),
            "class_name": str(((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("class") or ""),
            "level": int((((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("level") or 1)),
        })
    out.sort(key=lambda x: (x.get("display_name") or x.get("character_id") or "").lower())
    return {"ok": True, "characters": out}

@app.post("/api/campaigns/{campaign_id}/dm/levelup/grant_party")
async def api_dm_levelup_grant_party(campaign_id: str):
    participant_ids = _active_session_characters_for_campaign(campaign_id)
    state = _levelup_states.setdefault(_safe_campaign_id(campaign_id), {})

    count = 0
    granted = []
    for character_id in participant_ids:
        sheet = load_character_sheet(campaign_id, character_id)
        if not sheet:
            continue
        sheet = ensure_sheet_minimum(sheet, character_id)
        state[character_id] = _grant_levelup_spec_for_sheet(sheet)
        granted.append(character_id)
        count += 1

    _levelup_states[_safe_campaign_id(campaign_id)] = state
    return {"ok": True, "count": count, "character_ids": granted}


@app.post("/api/campaigns/{campaign_id}/dm/levelup/grant_character")
async def api_dm_levelup_grant_character(campaign_id: str, body: DMGrantLevelCharacterBody):
    character_id = str(body.character_id or "").strip()
    if not character_id:
        raise HTTPException(status_code=400, detail="character_id required")

    sheet = load_character_sheet(campaign_id, character_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")

    sheet = ensure_sheet_minimum(sheet, character_id)
    state = _levelup_states.setdefault(_safe_campaign_id(campaign_id), {})
    state[character_id] = _grant_levelup_spec_for_sheet(sheet)
    _levelup_states[_safe_campaign_id(campaign_id)] = state

    return {"ok": True, "character_id": character_id}

@app.get("/api/campaigns/{campaign_id}/levelup/mine/status")
async def api_levelup_mine_status(campaign_id: str, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    return _serialize_levelup_state_for_character(campaign_id, char_id)

@app.post("/api/campaigns/{campaign_id}/levelup/mine/submit")
async def api_levelup_mine_submit(campaign_id: str, req: LevelUpSubmitRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    cid = _safe_campaign_id(campaign_id)
    state = _levelup_states.get(cid, {})
    spec = state.get(str(char_id or "").strip()) if isinstance(state, dict) else None
    if not isinstance(spec, dict):
        raise HTTPException(status_code=404, detail="No pending level up for active character")

    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")

    sheet = ensure_sheet_minimum(sheet, char_id)

    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    target_level = max(1, min(20, int(spec.get("target_level", 1) or 1)))
    requires_asi = bool(spec.get("requires_asi", False))
    requires_choice = bool(spec.get("requires_feat_or_asi", False))

    spec["subclass_id"] = str(req.subclass_id or spec.get("subclass_id") or "").strip().lower()
    if isinstance(req.feat_state, dict) and req.feat_state:
        spec["feat_state"] = {str(k).strip().lower(): dict(v) for k, v in req.feat_state.items() if isinstance(v, dict)}
    submitted_feat_mode = str(req.feat_mode or spec.get("feat_mode") or "").strip().lower()
    if requires_choice:
        spec["feat_mode"] = submitted_feat_mode
        spec["feat_ids"] = sanitize_feat_ids(req.feat_ids if isinstance(req.feat_ids, list) else spec.get("feat_ids") or [])
        if submitted_feat_mode == "asi" and not bool(spec.get("asi_applied", False)):
            _apply_levelup_asi(sheet, req.increase_a, req.increase_b)
            spec["asi_applied"] = True
            spec["increase_a"] = str(req.increase_a or "").strip().lower()
            spec["increase_b"] = str(req.increase_b or "").strip().lower()
        elif submitted_feat_mode != "asi":
            spec["asi_applied"] = False
    elif requires_asi and not bool(spec.get("asi_applied", False)):
        _apply_levelup_asi(sheet, req.increase_a, req.increase_b)
        spec["asi_applied"] = True
        spec["increase_a"] = str(req.increase_a or "").strip().lower()
        spec["increase_b"] = str(req.increase_b or "").strip().lower()

    meta = sheet.setdefault("meta", {}) if isinstance(sheet.get("meta"), dict) else {}
    class_key = _slug_key(meta.get("class", ""))
    class_tpl = dict(CLASS_TEMPLATES.get(class_key, {})) if class_key else {}
    hit_die = max(1, int(class_tpl.get("hit_die", 8) or 8))

    hp_method = str(req.hp_gain_method or spec.get("hp_gain_method") or "average").strip().lower()
    if hp_method not in ("average", "roll"):
        hp_method = "average"
    spec["hp_gain_method"] = hp_method
    spec["target_level"] = target_level
    state[str(char_id or "").strip()] = spec
    _levelup_states[cid] = state

    pending_msgs = _levelup_pending_requirements(campaign_id, char_id, sheet, spec)
    if pending_msgs:
        raise HTTPException(status_code=409, detail="; ".join(pending_msgs))

    if hp_method == "roll":
        request_id = str(spec.get("hp_roll_request_id") or "").strip()
        pending_existing = None
        if request_id:
            pending_existing = (get_state(campaign_id).get("pending_roll_requests", {}) or {}).get(request_id)
        if pending_existing:
            return {"ok": True, "pending_roll": {
                "request_id": pending_existing.get("request_id", request_id),
                "roll_kind": pending_existing.get("roll_kind", "levelup_hit_die"),
                "expected_sides": pending_existing.get("expected_sides", hit_die),
                "expected_count_min": pending_existing.get("expected_count_min", 1),
                "expected_count_max": pending_existing.get("expected_count_max", 1),
                "adv_mode": pending_existing.get("adv_mode", "normal"),
                "label": pending_existing.get("label", f"Level Up HP (d{hit_die})"),
                "context": pending_existing.get("context", {}),
                "expires_at": pending_existing.get("expires_at", 0),
            }, "levelup_state": _serialize_levelup_state_for_character(campaign_id, char_id)}
        pending = _create_levelup_hp_request(campaign_id, sess["player_id"], char_id, hit_die)
        spec["hp_roll_request_id"] = pending["request_id"]
        state[str(char_id or "").strip()] = spec
        _levelup_states[cid] = state
        return {"ok": True, "pending_roll": pending, "levelup_state": _serialize_levelup_state_for_character(campaign_id, char_id)}

    con_mod = max(-5, min(10, (int((sheet.get("abilities", {}) or {}).get("con", 10) or 10) - 10) // 2))
    avg_gain = max(1, ((hit_die // 2) + 1) + con_mod)
    sheet = _finalize_levelup_apply(campaign_id, char_id, sheet, spec, avg_gain)

    state.pop(str(char_id or "").strip(), None)
    _levelup_states[cid] = state

    return {
        "ok": True,
        "character_id": char_id,
        "sheet": sheet,
        "levelup_state": {"active": False},
    }

def _apply_long_rest_to_sheet(campaign_id: str, character_id: str, sheet: Dict[str, Any]) -> Dict[str, Any]:
    stats = sheet.setdefault("stats", {}) if isinstance(sheet.get("stats"), dict) else {}
    combat = sheet.setdefault("combat", {}) if isinstance(sheet.get("combat"), dict) else {}
    resources = sheet.setdefault("resources", {}) if isinstance(sheet.get("resources"), dict) else {}
    _sync_sheet_derived_resources(sheet)
    max_hp = max(1, int(stats.get("max_hp", 1) or 1))
    stats["current_hp"] = max_hp
    resources["current_hp"] = max_hp
    total = max(0, int(combat.get("hit_dice_total", 0) or 0))
    used = max(0, int(combat.get("hit_dice_used", 0) or 0))
    recover = max(1, total // 2) if total > 0 else 0
    combat["hit_dice_used"] = max(0, used - recover)
    combat["rage_active"] = False
    combat["action_surge_used"] = False
    combat["relentless_rage_uses"] = 0
    combat["divine_sense_active"] = False
    _refresh_resource_pools_for_rest(sheet, "long_rest")
    refresh_spell_slots(sheet, "long_rest")
    _refresh_pact_magic_for_rest(sheet, "long_rest")
    _sync_sheet_derived_resources(sheet)
    sheet = _recompute_sheet_derived_state(campaign_id, character_id, sheet)
    return sheet


def _auto_resolve_short_rest_if_ready(campaign_id: str) -> Optional[Dict[str, Any]]:
    state = _get_rest_state(campaign_id)
    if not state or str(state.get("type") or "") != "short_rest" or str(state.get("status") or "") != "active":
        return None
    participants = state.get("participants") if isinstance(state.get("participants"), dict) else {}
    if not participants:
        state["status"] = "resolved"
        _set_rest_state(campaign_id, state)
        _clear_rest_state(campaign_id)
        return {"resolved": True, "participants": 0}
    if not all(bool((v or {}).get("done", False)) for v in participants.values()):
        return None
    resolved = []
    for char_id in list(participants.keys()):
        sheet = load_character_sheet(campaign_id, char_id)
        if not sheet:
            continue
        sheet = ensure_sheet_minimum(sheet, char_id)
        _refresh_resource_pools_for_rest(sheet, "short_rest")
        refresh_spell_slots(sheet, "short_rest")
        _refresh_pact_magic_for_rest(sheet, "short_rest")
        _sync_sheet_derived_resources(sheet)
        sheet = _recompute_sheet_derived_state(campaign_id, char_id, sheet)
        save_character_sheet(campaign_id, char_id, sheet)
        resolved.append(char_id)
    state["status"] = "resolved"
    _set_rest_state(campaign_id, state)
    _clear_rest_state(campaign_id)
    return {"resolved": True, "participants": resolved}


def _refresh_resource_pools_for_rest(sheet: Dict[str, Any], rest_type: str) -> None:
    ensure_spellcasting_foundation(sheet)
    pools = sheet.setdefault("resource_pools", {}) if isinstance(sheet.get("resource_pools"), dict) else {}
    if rest_type not in ("short_rest", "long_rest"):
        return
    for _, pool in list(pools.items()):
        if not isinstance(pool, dict):
            continue
        refresh = str(pool.get("refresh", "") or "").strip().lower()
        if not refresh:
            continue
        if refresh == "short_rest" and rest_type in ("short_rest", "long_rest"):
            pool["current"] = int(pool.get("max", pool.get("current", 0)) or 0)
        elif refresh == "long_rest" and rest_type == "long_rest":
            pool["current"] = int(pool.get("max", pool.get("current", 0)) or 0)


class FeatStateToggleRequest(BaseModel):
    feat_id: str
    feat_state: Dict[str, Any] = Field(default_factory=dict)


@app.post("/api/campaigns/{campaign_id}/characters/me/feat_state")
async def api_set_feat_state_mine(campaign_id: str, req: FeatStateToggleRequest, sess=Depends(require_session)):
    """Set runtime feat_state for an active feat — e.g. enable/disable GWM or Sharpshooter toggle.

    Only the feat_state dict for the given feat_id is updated; the feat must already
    be present on sheet["feats"] for this endpoint to accept the request.
    """
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    feat_id = str(req.feat_id or "").strip().lower()
    if not feat_id:
        raise HTTPException(status_code=400, detail="feat_id required")

    feats = [str(x).strip().lower() for x in (sheet.get("feats") or []) if str(x).strip()]
    if feat_id not in feats:
        raise HTTPException(status_code=400, detail="Feat not present on character sheet")

    feat_state_map = sheet.setdefault("feat_state", {}) if isinstance(sheet.get("feat_state"), dict) else {}
    sheet["feat_state"] = feat_state_map

    incoming = req.feat_state if isinstance(req.feat_state, dict) else {}
    existing = feat_state_map.get(feat_id) if isinstance(feat_state_map.get(feat_id), dict) else {}
    existing.update(incoming)
    feat_state_map[feat_id] = existing

    save_character_sheet(campaign_id, char_id, sheet)

    return {
        "ok": True,
        "feat_id": feat_id,
        "feat_state": feat_state_map.get(feat_id, {}),
    }


@app.post("/api/campaigns/{campaign_id}/abilities/mine/use")
async def api_use_ability_mine(campaign_id: str, req: UseAbilityRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    ability_id = _slug_key(req.ability_id)
    if not ability_id:
        raise HTTPException(status_code=400, detail="ability_id required")
    if ability_id not in [_slug_key(x) for x in (sheet.get("ability_ids") or [])]:
        raise HTTPException(status_code=400, detail="Ability not on sheet")

    resource_pools = sheet.setdefault("resource_pools", {}) if isinstance(sheet.get("resource_pools"), dict) else {}
    stats = sheet.setdefault("stats", {}) if isinstance(sheet.get("stats"), dict) else {}
    combat = sheet.setdefault("combat", {}) if isinstance(sheet.get("combat"), dict) else {}
    meta = sheet.setdefault("meta", {}) if isinstance(sheet.get("meta"), dict) else {}
    result: Dict[str, Any] = {"ok": True, "ability_id": ability_id}

    if ability_id == "fighter_second_wind":
        pool = resource_pools.setdefault("second_wind", {"current": 1, "max": 1, "refresh": "short_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Second Wind has no uses remaining")
        lvl = max(1, int(meta.get("level", 1) or 1))
        heal = random.randint(1, 10) + lvl
        max_hp = max(1, int(stats.get("max_hp", 1) or 1))
        old_hp = int(stats.get("current_hp", max_hp) or max_hp)
        new_hp = min(max_hp, old_hp + heal)
        stats["current_hp"] = new_hp
        sheet.setdefault("resources", {})
        if isinstance(sheet.get("resources"), dict):
            sheet["resources"]["current_hp"] = new_hp
        pool["current"] = cur - 1
        result.update({"heal": heal, "current_hp": new_hp, "resource_pool": "second_wind"})
    elif ability_id == "barbarian_rage":
        pool = resource_pools.setdefault("rage", {"current": 2, "max": 2, "refresh": "long_rest"})
        is_active = bool(combat.get("rage_active", False))
        if is_active:
            combat["rage_active"] = False
            result.update({"active": False, "toggled_off": True})
        else:
            cur = int(pool.get("current", 0) or 0)
            if cur <= 0:
                raise HTTPException(status_code=409, detail="Rage has no uses remaining")
            pool["current"] = cur - 1
            combat["rage_active"] = True
            result.update({"active": True, "resource_pool": "rage"})
    elif ability_id == "barbarian_reckless_attack":
        is_active = bool(combat.get("reckless_attack_active", False))
        combat["reckless_attack_active"] = not is_active
        result.update({"active": not is_active, "toggled": True, "note": "Advantage on your melee attack rolls using Strength this turn; attacks against you have advantage until your next turn."})
    elif ability_id == "fighter_action_surge":
        pool = resource_pools.setdefault("action_surge", {"current": 1, "max": 1, "refresh": "short_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Action Surge has no uses remaining")
        pool["current"] = cur - 1
        combat["action_surge_used"] = True
        result.update({"resource_pool": "action_surge", "used": True})
    elif ability_id == "fighter_indomitable":
        pool = resource_pools.setdefault("indomitable", {"current": 1, "max": 1, "refresh": "long_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Indomitable has no uses remaining")
        pool["current"] = cur - 1
        result.update({"resource_pool": "indomitable", "used": True})
    elif ability_id == "rogue_cunning_action":
        action_mode = _slug_key(req.mode or "") or "dash"
        if action_mode not in {"dash", "disengage", "hide"}:
            raise HTTPException(status_code=400, detail="Cunning Action mode must be dash, disengage, or hide")
        combat["cunning_action_declared"] = action_mode
        result.update({"declared": action_mode, "note": f"Cunning Action declared: {action_mode}. Action economy remains player-tracked."})
    elif ability_id == "rogue_uncanny_dodge":
        is_active = bool(combat.get("uncanny_dodge_armed", False))
        combat["uncanny_dodge_armed"] = not is_active
        result.update({"active": not is_active, "toggled": True, "note": "When armed, the next attack damage against you is halved and your reaction is spent."})
    elif ability_id == "rogue_stroke_of_luck":
        pool = resource_pools.setdefault("stroke_of_luck", {"current": 1, "max": 1, "refresh": "short_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Stroke of Luck has no uses remaining")
        is_active = bool(combat.get("stroke_of_luck_armed", False))
        combat["stroke_of_luck_armed"] = not is_active
        result.update({"active": not is_active, "toggled": True, "resource_pool": "stroke_of_luck", "note": "When armed, your next attack roll or ability check can be treated as a 20. The use is consumed when applied."})
    elif ability_id == "monk_flurry_of_blows":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("ki", {"current": 0, "max": 0, "refresh": "short_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Ki has no points remaining")
        pool["current"] = cur - 1
        combat["flurry_of_blows_active"] = True
        result.update({"resource_pool": "ki", "used": True, "note": "Flurry of Blows declared for tracking. Bonus-action attacks remain player-tracked."})
    elif ability_id == "monk_patient_defense":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("ki", {"current": 0, "max": 0, "refresh": "short_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Ki has no points remaining")
        pool["current"] = cur - 1
        combat["patient_defense_active"] = True
        result.update({"resource_pool": "ki", "used": True, "active": True, "note": "Patient Defense active until your next turn. Attack rolls against you are made at disadvantage."})
    elif ability_id == "monk_step_of_the_wind":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("ki", {"current": 0, "max": 0, "refresh": "short_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Ki has no points remaining")
        pool["current"] = cur - 1
        mode = _slug_key(req.mode or "disengage") or "disengage"
        if mode not in {"dash", "disengage"}:
            mode = "disengage"
        combat["step_of_the_wind_mode"] = mode
        result.update({"resource_pool": "ki", "used": True, "declared": mode, "note": "Step of the Wind declared for tracking. Jump/dash/disengage handling remains player-tracked."})
    elif ability_id == "monk_deflect_missiles":
        combat["deflect_missiles_armed"] = not bool(combat.get("deflect_missiles_armed", False))
        result.update({"active": bool(combat.get("deflect_missiles_armed", False)), "toggled": True, "note": "When armed, the next ranged weapon attack damage against you is reduced automatically."})
    elif ability_id == "monk_stunning_strike":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("ki", {"current": 0, "max": 0, "refresh": "short_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Ki has no points remaining")
        if bool(combat.get("stunning_strike_armed", False)):
            combat["stunning_strike_armed"] = False
            result.update({"active": False, "toggled": True, "note": "Stunning Strike cleared."})
        else:
            pool["current"] = cur - 1
            combat["stunning_strike_armed"] = True
            result.update({"resource_pool": "ki", "active": True, "used": True, "note": "Stunning Strike armed. Your next melee hit will force a CON save or stun the target."})
    elif ability_id == "monk_stillness_of_mind":
        statuses = list(sheet.get("combat_effects") or []) if isinstance(sheet.get("combat_effects"), list) else []
        kept = []
        removed = []
        for raw in statuses:
            nm = str((raw or {}).get("name") or "").strip().lower()
            if nm in {"charmed", "frightened"}:
                removed.append(nm)
            else:
                kept.append(raw)
        if isinstance(sheet.get("combat_effects"), list):
            sheet["combat_effects"] = kept
        result.update({"removed": removed, "note": "Stillness of Mind clears charmed/frightened from the sheet effect list when present."})
    elif ability_id == "monk_diamond_soul":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("ki", {"current": 0, "max": 0, "refresh": "short_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Ki has no points remaining")
        pool["current"] = cur - 1
        result.update({"resource_pool": "ki", "used": True, "note": "Diamond Soul reroll declared. Save reroll remains player/DM adjudicated in edge cases."})
    elif ability_id == "monk_empty_body":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("ki", {"current": 0, "max": 0, "refresh": "short_rest"})
        active = bool(combat.get("empty_body_active", False))
        if active:
            combat["empty_body_active"] = False
            result.update({"active": False, "toggled": True, "note": "Empty Body ended."})
        else:
            cur = int(pool.get("current", 0) or 0)
            cost = 4
            if cur < cost:
                raise HTTPException(status_code=409, detail="Not enough ki for Empty Body")
            pool["current"] = cur - cost
            combat["empty_body_active"] = True
            result.update({"resource_pool": "ki", "used": True, "active": True, "note": "Empty Body active: broad invisibility/resistance handling is enabled."})
    elif ability_id == "wizard_arcane_recovery":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("arcane_recovery", {"current": 1, "max": 1, "refresh": "long_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Arcane Recovery has no uses remaining")
        recovery_levels = _parse_slot_level_list_from_request(req)
        if not recovery_levels:
            raise HTTPException(status_code=400, detail="Provide slot levels to recover using amount or mode, e.g. amount=2 or mode='1,1,2'")
        if any(lvl < 1 or lvl > 5 for lvl in recovery_levels):
            raise HTTPException(status_code=400, detail="Arcane Recovery can only restore slot levels 1 through 5")
        budget_max = max(1, int(combat.get("arcane_recovery_levels", 1) or 1))
        budget_remaining = max(0, int(combat.get("arcane_recovery_levels_remaining", budget_max) or budget_max))
        total_requested = sum(recovery_levels)
        if total_requested > budget_remaining:
            raise HTTPException(status_code=400, detail=f"Arcane Recovery can restore at most {budget_remaining} more slot levels right now")
        restored_rows: List[Dict[str, Any]] = []
        for slot_level in recovery_levels:
            ok, msg, state = _restore_shared_spell_slots(sheet, slot_level, count=1)
            if not ok:
                raise HTTPException(status_code=400, detail=msg)
            restored_rows.append(state)
        pool["current"] = max(0, cur - 1)
        combat["arcane_recovery_levels_remaining"] = max(0, budget_remaining - total_requested)
        result.update({
            "resource_pool": "arcane_recovery",
            "used": True,
            "restored_slots": restored_rows,
            "recovered_levels_total": total_requested,
            "arcane_recovery_levels_remaining": int(combat.get("arcane_recovery_levels_remaining", 0) or 0),
        })
    elif ability_id == "bard_bardic_inspiration":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("bardic_inspiration", {"current": 0, "max": 0, "refresh": "long_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Bardic Inspiration has no uses remaining")
        pool["current"] = cur - 1
        target_id = str(req.target_character_id or "").strip()
        grants = list(combat.get("bardic_inspiration_grants") or []) if isinstance(combat.get("bardic_inspiration_grants"), list) else []
        grant = {"target_character_id": target_id or "", "die": f"d{int(combat.get('bardic_inspiration_die', 6) or 6)}", "ts": int(time.time())}
        grants.append(grant)
        combat["bardic_inspiration_grants"] = grants[-20:]
        result.update({"resource_pool": "bardic_inspiration", "used": True, "grant": grant, "note": "Bardic Inspiration use tracked. Consumption by the target remains player/DM managed."})
    elif ability_id == "cleric_divine_domain":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("channel_divinity", {"current": 0, "max": 0, "refresh": "short_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Channel Divinity has no uses remaining")
        pool["current"] = cur - 1
        mode = str(req.mode or "channel_divinity").strip() or "channel_divinity"
        combat["channel_divinity_last_mode"] = mode
        result.update({"resource_pool": "channel_divinity", "used": True, "mode": mode, "note": "Channel Divinity use tracked. Domain-specific effect resolution remains subclass work in Phase H."})
    elif ability_id == "druid_druidic":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("wild_shape", {"current": 0, "max": 0, "refresh": "short_rest"})
        max_amt = int(pool.get("max", 0) or 0)
        active = bool(combat.get("wild_shape_active", False))
        if active:
            combat["wild_shape_active"] = False
            combat.pop("wild_shape_form", None)
            result.update({"active": False, "toggled": True, "note": "Wild Shape ended."})
        else:
            if max_amt <= 0 and not bool(combat.get("archdruid", False)):
                raise HTTPException(status_code=409, detail="Wild Shape is not available")
            cur = int(pool.get("current", 0) or 0)
            if cur <= 0 and not bool(combat.get("archdruid", False)):
                raise HTTPException(status_code=409, detail="Wild Shape has no uses remaining")
            if not bool(combat.get("archdruid", False)):
                pool["current"] = cur - 1
            combat["wild_shape_active"] = True
            if str(req.mode or "").strip():
                combat["wild_shape_form"] = str(req.mode or "").strip()
            result.update({"resource_pool": "wild_shape", "used": not bool(combat.get("archdruid", False)), "active": True, "form": combat.get("wild_shape_form", "")})
    elif ability_id == "sorcerer_spellcasting":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("sorcery_points", {"current": 0, "max": 0, "refresh": "long_rest"})
        cur = int(pool.get("current", 0) or 0)
        max_points = int(pool.get("max", 0) or 0)
        mode = _slug_key(req.mode or "")
        level = max(0, int(req.amount or 0))
        if mode in {"slot_to_points", "spell_slot_to_points", "convert_slot_to_points"}:
            if level < 1 or level > 5:
                raise HTTPException(status_code=400, detail="Font of Magic can only convert spell slots of level 1 through 5 into Sorcery Points")
            gain = level
            if cur + gain > max_points:
                raise HTTPException(status_code=400, detail="Not enough Sorcery Point capacity for that conversion")
            ok, msg, slot_state = _consume_any_spell_slot(sheet, level, count=1, slot_source="shared")
            if not ok:
                raise HTTPException(status_code=400, detail=msg)
            pool["current"] = cur + gain
            result.update({"resource_pool": "sorcery_points", "conversion": "slot_to_points", "slot_level": level, "gained": gain, "current": int(pool.get("current", 0) or 0), "slot_state": slot_state})
        elif mode in {"points_to_slot", "create_slot", "create_spell_slot"}:
            if level < 1 or level > 5:
                raise HTTPException(status_code=400, detail="Font of Magic can only create spell slots of level 1 through 5")
            cost = _font_of_magic_slot_cost(level)
            if cost <= 0:
                raise HTTPException(status_code=400, detail="Invalid slot level for Font of Magic")
            if cur < cost:
                raise HTTPException(status_code=409, detail="Not enough Sorcery Points for that conversion")
            ok, msg, slot_state = _restore_one_shared_spell_slot_if_possible(sheet, level)
            if not ok:
                raise HTTPException(status_code=400, detail=msg)
            pool["current"] = cur - cost
            result.update({"resource_pool": "sorcery_points", "conversion": "points_to_slot", "slot_level": level, "spent": cost, "current": int(pool.get("current", 0) or 0), "slot_state": slot_state})
        else:
            raise HTTPException(status_code=400, detail="Sorcerer spellcasting mode must be slot_to_points or points_to_slot")
    elif ability_id == "warlock_pact_magic":
        _sync_sheet_derived_resources(sheet)
        mode = _slug_key(req.mode or "eldritch_master") or "eldritch_master"
        if mode != "eldritch_master":
            raise HTTPException(status_code=400, detail="Warlock pact magic active use currently supports eldritch_master only")
        if not bool(combat.get("eldritch_master", False)):
            raise HTTPException(status_code=400, detail="Eldritch Master is not currently available")
        pool = resource_pools.setdefault("eldritch_master", {"current": 0, "max": 0, "refresh": "long_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Eldritch Master has no uses remaining")
        ok, msg, pact_state = _restore_pact_magic_slots(sheet, count=99)
        if not ok and "No spent pact magic slots" not in str(msg):
            raise HTTPException(status_code=400, detail=msg)
        pool["current"] = cur - 1
        result.update({"resource_pool": "eldritch_master", "used": True, "pact_state": pact_state, "note": "Eldritch Master restores expended pact magic slots."})
    elif ability_id == "cleric_spellcasting":
        _sync_sheet_derived_resources(sheet)
        if not bool(combat.get("divine_intervention", False)):
            raise HTTPException(status_code=400, detail="Divine Intervention is not currently available")
        pool = resource_pools.setdefault("divine_intervention", {"current": 0, "max": 0, "refresh": "long_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Divine Intervention has no uses remaining")
        pool["current"] = cur - 1
        result.update({"resource_pool": "divine_intervention", "used": True, "auto_success": bool(combat.get("improved_divine_intervention", False)), "note": "Divine Intervention declaration tracked. Final divine effect remains DM adjudicated."})
    elif ability_id == "paladin_divine_sense":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("divine_sense", {"current": 1, "max": 1, "refresh": "long_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Divine Sense has no uses remaining")
        pool["current"] = cur - 1
        combat["divine_sense_active"] = True
        result.update({"resource_pool": "divine_sense", "used": True, "note": "Divine Sense use tracked. Supernatural detection remains player/DM interpreted for now."})
    elif ability_id == "paladin_lay_on_hands":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("lay_on_hands", {"current": 5, "max": 5, "refresh": "long_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Lay on Hands has no points remaining")
        requested = int(req.amount or 0)
        if requested <= 0:
            raise HTTPException(status_code=400, detail="Lay on Hands amount must be at least 1")
        spend = min(cur, requested)

        target_id = str(req.target_character_id or "").strip() or char_id
        target_sheet = sheet if target_id == char_id else load_character_sheet(campaign_id, target_id)
        if not target_sheet:
            raise HTTPException(status_code=404, detail="Lay on Hands target not found")
        target_sheet = ensure_sheet_minimum(target_sheet, target_id)
        target_stats = target_sheet.setdefault("stats", {}) if isinstance(target_sheet.get("stats"), dict) else {}
        target_resources = target_sheet.setdefault("resources", {}) if isinstance(target_sheet.get("resources"), dict) else {}
        max_hp = max(1, int(target_stats.get("max_hp", 1) or 1))
        old_hp = int(target_stats.get("current_hp", max_hp) or max_hp)
        if old_hp >= max_hp:
            raise HTTPException(status_code=409, detail="Target is already at full HP")
        heal = min(spend, max_hp - old_hp)
        new_hp = old_hp + heal
        target_stats["current_hp"] = new_hp
        target_resources["current_hp"] = new_hp
        pool["current"] = max(0, cur - spend)
        if target_id != char_id:
            target_sheet = _recompute_sheet_derived_state(campaign_id, target_id, target_sheet)
            save_character_sheet(campaign_id, target_id, target_sheet)
        result.update({"heal": heal, "spent": spend, "current_hp": new_hp, "resource_pool": "lay_on_hands", "target_character_id": target_id})
    elif ability_id == "paladin_cleansing_touch":
        _sync_sheet_derived_resources(sheet)
        pool = resource_pools.setdefault("cleansing_touch", {"current": 1, "max": 1, "refresh": "long_rest"})
        cur = int(pool.get("current", 0) or 0)
        if cur <= 0:
            raise HTTPException(status_code=409, detail="Cleansing Touch has no uses remaining")
        pool["current"] = cur - 1
        result.update({"resource_pool": "cleansing_touch", "used": True, "note": "Cleansing Touch use tracked. Spell-ending resolution remains DM adjudicated until spell-state integration is implemented."})
    else:
        raise HTTPException(status_code=400, detail="Ability use not implemented yet")

    save_character_sheet(campaign_id, char_id, sheet)
    fresh = load_character_sheet(campaign_id, char_id) or {}
    fresh = ensure_sheet_minimum(fresh, char_id)
    fresh = _recompute_sheet_derived_state(campaign_id, char_id, fresh)
    save_character_sheet(campaign_id, char_id, fresh)
    result["sheet"] = fresh
    return result


def _create_short_rest_hit_dice_request(campaign_id: str, player_id: str, char_id: str, spend: int, hit_die_sides: int) -> Dict[str, Any]:
    st = get_state(campaign_id)
    now = time.time()
    _cleanup_expired_roll_requests(st, now)
    request_id = uuid.uuid4().hex
    label = f"Short Rest Hit Dice ({spend}d{hit_die_sides})"
    rr = {
        "request_id": request_id,
        "character_id": char_id,
        "player_id": player_id,
        "roll_kind": "short_rest_hit_dice",
        "expected_sides": int(hit_die_sides),
        "expected_count_min": int(spend),
        "expected_count_max": int(spend),
        "adv_mode": "normal",
        "dc": None,
        "label": label,
        "context": {"spend_hit_dice": int(spend)},
        "created_at": now,
        "expires_at": now + 300,
    }
    st.setdefault("pending_roll_requests", {})[request_id] = rr
    _push_player_log(st, player_id, {
        "ts": now,
        "type": "ROLL_REQUESTED",
        "request_id": request_id,
        "roll_kind": "short_rest_hit_dice",
        "label": label,
        "expected": {"sides": int(hit_die_sides), "min": int(spend), "max": int(spend)},
        "adv_mode": "normal",
        "dc": None,
        "context": {"spend_hit_dice": int(spend)},
    })
    return {
        "request_id": request_id,
        "roll_kind": "short_rest_hit_dice",
        "label": label,
        "expected_sides": int(hit_die_sides),
        "expected_count_min": int(spend),
        "expected_count_max": int(spend),
        "adv_mode": "normal",
        "context": {"spend_hit_dice": int(spend)},
    }


@app.get("/api/campaigns/{campaign_id}/rest/mine/status")
async def api_rest_status_mine(campaign_id: str, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    return _serialize_rest_state_for_character(campaign_id, char_id)


@app.post("/api/campaigns/{campaign_id}/rest/mine/request_hit_dice")
async def api_rest_request_hit_dice_mine(campaign_id: str, req: ShortRestRollRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    rest_state = _get_rest_state(campaign_id)
    if not rest_state or str(rest_state.get("type") or "") != "short_rest" or str(rest_state.get("status") or "") != "active":
        raise HTTPException(status_code=409, detail="Short rest is not active")
    participants = rest_state.get("participants") if isinstance(rest_state.get("participants"), dict) else {}
    pdata = participants.get(char_id) if isinstance(participants.get(char_id), dict) else None
    if pdata is None:
        raise HTTPException(status_code=403, detail="Active character is not part of this short rest")
    if bool(pdata.get("done", False)):
        raise HTTPException(status_code=409, detail="Character is already marked done with short rest")

    combat = sheet.setdefault("combat", {}) if isinstance(sheet.get("combat"), dict) else {}
    _sync_sheet_derived_resources(sheet)
    total = max(0, int(combat.get("hit_dice_total", 0) or 0))
    used = max(0, int(combat.get("hit_dice_used", 0) or 0))
    remaining = max(0, total - used)
    spend = max(1, int(req.spend_hit_dice or 1))
    spend = min(spend, remaining)
    if spend <= 0:
        raise HTTPException(status_code=409, detail="No hit dice remaining")

    hit_die_sides = max(1, int(combat.get("hit_die_sides", 8) or 8))
    pending = _create_short_rest_hit_dice_request(campaign_id, sess["player_id"], char_id, spend, hit_die_sides)
    return {"ok": True, "pending_roll": pending, "rest_state": _serialize_rest_state_for_character(campaign_id, char_id)}


@app.post("/api/campaigns/{campaign_id}/rest/mine/done")
async def api_rest_done_mine(campaign_id: str, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    rest_state = _get_rest_state(campaign_id)
    if not rest_state or str(rest_state.get("type") or "") != "short_rest" or str(rest_state.get("status") or "") != "active":
        raise HTTPException(status_code=409, detail="Short rest is not active")
    participants = rest_state.get("participants") if isinstance(rest_state.get("participants"), dict) else {}
    pdata = participants.get(char_id) if isinstance(participants.get(char_id), dict) else None
    if pdata is None:
        raise HTTPException(status_code=403, detail="Active character is not part of this short rest")
    pdata["done"] = True
    participants[char_id] = pdata
    rest_state["participants"] = participants
    _set_rest_state(campaign_id, rest_state)
    auto = _auto_resolve_short_rest_if_ready(campaign_id)
    return {"ok": True, "done": True, "rest_state": _serialize_rest_state_for_character(campaign_id, char_id), "auto_resolved": bool(auto)}


@app.post("/api/campaigns/{campaign_id}/rest/mine/undo_done")
async def api_rest_undo_done_mine(campaign_id: str, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    rest_state = _get_rest_state(campaign_id)
    if not rest_state or str(rest_state.get("type") or "") != "short_rest" or str(rest_state.get("status") or "") != "active":
        raise HTTPException(status_code=409, detail="Short rest is not active")
    participants = rest_state.get("participants") if isinstance(rest_state.get("participants"), dict) else {}
    pdata = participants.get(char_id) if isinstance(participants.get(char_id), dict) else None
    if pdata is None:
        raise HTTPException(status_code=403, detail="Active character is not part of this short rest")
    pdata["done"] = False
    participants[char_id] = pdata
    rest_state["participants"] = participants
    _set_rest_state(campaign_id, rest_state)
    return {"ok": True, "done": False, "rest_state": _serialize_rest_state_for_character(campaign_id, char_id)}


@app.get("/api/campaigns/{campaign_id}/rest/control/status")
async def api_rest_control_status(campaign_id: str):
    state = _get_rest_state(campaign_id)
    if not state:
        return {"active": False}
    participants = state.get("participants") if isinstance(state.get("participants"), dict) else {}
    return {
        "active": str(state.get("status") or "") == "active",
        "rest_type": str(state.get("type") or ""),
        "status": str(state.get("status") or ""),
        "rest_id": str(state.get("rest_id") or ""),
        "participant_count": len(participants),
        "done_count": sum(1 for v in participants.values() if isinstance(v, dict) and v.get("done")),
        "participants": [
            {"character_id": cid, "player_id": str((v or {}).get("player_id") or ""), "done": bool((v or {}).get("done", False))}
            for cid, v in participants.items()
        ],
    }


@app.post("/api/campaigns/{campaign_id}/spell_slots/mine/consume")
async def api_consume_spell_slot_mine(campaign_id: str, req: ConsumeSpellSlotRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    ok, msg, state = _consume_any_spell_slot(sheet, req.slot_level, count=req.count, slot_source=req.slot_source)
    if not ok:
        raise HTTPException(status_code=400, detail=msg)
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "slot_state": state, "spellcasting": sheet.get("spellcasting", {})}


@app.post("/api/campaigns/{campaign_id}/spells/mine/declare")
async def api_declare_spell_mine(campaign_id: str, req: DeclareSpellRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    player_id = str(sess.get("player_id") or "").strip()
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    spells_db = load_spells_db_for_campaign(campaign_id)
    spell_id = str(req.spell_id or "").strip()
    spell_row = (spells_db or {}).get(spell_id) or {}
    if not spell_row:
        raise HTTPException(status_code=404, detail="Spell not found in campaign spells.json")

    allowed, reason, list_state = _character_can_declare_spell(sheet, spell_id, spell_row)
    if not allowed:
        raise HTTPException(status_code=400, detail=reason)

    spell_level = int(spell_row.get("level", 0) or 0)
    max_spell_level = _max_spell_level_for_sheet(sheet)
    if spell_level > 0 and max_spell_level > 0 and spell_level > max_spell_level and not _can_consume_mystic_arcanum(sheet, spell_level)[0]:
        raise HTTPException(status_code=400, detail="Spell level too high for this character")
    requested_source = _normalize_slot_source(req.slot_source)
    slot_level = req.slot_level if req.slot_level is not None else spell_level
    if spell_level <= 0:
        slot_level = 0
    else:
        slot_level = max(spell_level, int(slot_level or spell_level))
        if slot_level > 9:
            raise HTTPException(status_code=400, detail="Invalid slot level")
        if spell_level >= 6 and requested_source == "auto":
            arcanum_ok, _arcanum_msg, _arcanum_state = _can_consume_mystic_arcanum(sheet, spell_level)
            if arcanum_ok and int(slot_level) == int(spell_level):
                requested_source = "arcanum"

    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    chosen_metamagic = _sanitize_metamagic_option_ids([str(x).strip() for x in (req.metamagic_options or []) if str(x).strip()])
    spell_mastery_spells = set(_spell_listify(sc.get("spell_mastery_spells") or sheet.get("spell_mastery_spells")))
    signature_spells = set(_spell_listify(sc.get("signature_spells") or sheet.get("signature_spells")))
    known_metamagic = set(_sanitize_metamagic_option_ids(_spell_listify(sc.get("metamagic_options") or sheet.get("metamagic_options"))))
    if chosen_metamagic and not set(chosen_metamagic).issubset(known_metamagic):
        raise HTTPException(status_code=400, detail="Requested metamagic option is not known by this character")
    metamagic_state = {"options": [], "spent": 0, "pool": {}}
    if chosen_metamagic:
        cost = _metamagic_total_cost(chosen_metamagic, int(slot_level))
        ok_meta, pool_state = _spend_resource_pool(sheet, "sorcery_points", cost)
        if not ok_meta:
            raise HTTPException(status_code=400, detail="Not enough Sorcery Points for requested Metamagic")
        metamagic_state = {"options": list(chosen_metamagic), "spent": int(cost), "pool": pool_state}
    slot_state = {"level": int(slot_level), "remaining": 999 if int(slot_level) <= 0 else 0, "slot_source": "none"}
    consume_slot = bool(req.consume_slot)
    free_cast_state = {"type": "", "used": False}
    if spell_id in spell_mastery_spells and int(spell_level) in {1, 2} and int(slot_level) == int(spell_level):
        consume_slot = False
        free_cast_state = {"type": "spell_mastery", "used": True}
        slot_state = {"level": int(slot_level), "remaining": 999, "slot_source": "spell_mastery"}
    elif spell_id in signature_spells and int(spell_level) == 3 and int(slot_level) == 3:
        pool_name = f"signature_spell_{_slug_key(spell_id)}"
        pools = sheet.setdefault("resource_pools", {}) if isinstance(sheet.get("resource_pools"), dict) else {}
        sheet["resource_pools"] = pools
        pool = pools.setdefault(pool_name, {"current": 1, "max": 1, "refresh": "short_rest"})
        cur = int(pool.get("current", 1) or 1)
        pool["max"] = 1
        pool["refresh"] = "short_rest"
        if cur > 0:
            pool["current"] = cur - 1
            consume_slot = False
            free_cast_state = {"type": "signature_spell", "used": True, "pool": pool_name, "remaining": int(pool.get("current", 0) or 0)}
            slot_state = {"level": 3, "remaining": int(pool.get("current", 0) or 0), "slot_source": "signature_spell"}
    if consume_slot and int(slot_level) > 0:
        if requested_source == "arcanum":
            ok, msg, slot_state = _consume_mystic_arcanum(sheet, int(slot_level))
        else:
            ok, msg, slot_state = _consume_any_spell_slot(sheet, int(slot_level), count=1, slot_source=requested_source)
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
        save_character_sheet(campaign_id, char_id, sheet)
    else:
        save_character_sheet(campaign_id, char_id, sheet)

    spellcasting = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    decl = {
        "declaration_id": uuid.uuid4().hex[:12],
        "ts": int(time.time()),
        "player_id": player_id,
        "character_id": char_id,
        "character_name": str(((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("name") or char_id),
        "spell_id": spell_id,
        "spell_name": str(spell_row.get("name") or spell_id),
        "spell_level": spell_level,
        "slot_level": int(slot_level),
        "slot_source": str((slot_state or {}).get("slot_source") or requested_source),
        "free_cast_state": free_cast_state,
        "consume_slot": bool(req.consume_slot),
        "upcast_level": int(req.upcast_level) if req.upcast_level is not None else None,
        "target_hint": str(req.target_hint or "").strip(),
        "notes": str(req.notes or "").strip(),
        "target_mode": str(spell_row.get("target_mode") or ""),
        "range_ft": int(spell_row.get("range_ft", 0) or 0),
        "save_type": str(spell_row.get("save_type") or ""),
        "attack_roll": bool(spell_row.get("attack_roll", False)),
        "damage": dict(spell_row.get("damage") or {}) if isinstance(spell_row.get("damage"), dict) else {},
        "targeting": dict(spell_row.get("targeting") or {}) if isinstance(spell_row.get("targeting"), dict) else {},
        "effects": list(get_spell_effects(spell_row, cast_level=int(slot_level))) if isinstance(get_spell_effects(spell_row, cast_level=int(slot_level)), list) else [],
        "concentration": bool(spell_row.get("concentration", False)),
        "reaction": bool(spell_row.get("reaction", False)),
        "reaction_trigger": str(spell_row.get("reaction_trigger") or ""),
        "reaction_window": str(spell_row.get("reaction_window") or ""),
        "spellcasting": {
            "class": str(spellcasting.get("class") or ""),
            "ability": str(spellcasting.get("ability") or ""),
            "save_dc": int(spellcasting.get("save_dc", 0) or 0),
            "attack_bonus": int(spellcasting.get("attack_bonus", 0) or 0),
            "known_mode": str(spellcasting.get("known_mode") or ""),
            "spellcasting_profile": str(spellcasting.get("spellcasting_profile") or ""),
            "slot_options": list(spellcasting.get("slot_options") or []),
            "pact_magic": dict(spellcasting.get("pact_magic") or {}) if isinstance(spellcasting.get("pact_magic"), dict) else {},
        },
    }

    st = get_state(campaign_id)
    queue = st.setdefault("pending_spell_declarations", [])
    queue.append(decl)
    if len(queue) > 500:
        del queue[:-500]
    if bool(decl.get("reaction")):
        rqueue = st.setdefault("pending_reaction_spell_declarations", [])
        rqueue.append(dict(decl))
        if len(rqueue) > 200:
            del rqueue[:-200]

    _push_player_log(st, player_id, {
        "ts": decl["ts"],
        "type": "SPELL_DECLARED",
        "spell_id": spell_id,
        "spell_name": decl["spell_name"],
        "slot_level": int(slot_level),
        "spell_level": spell_level,
        "target_mode": decl["target_mode"],
        "target_hint": decl["target_hint"],
        "notes": decl["notes"],
        "consume_slot": bool(req.consume_slot),
        "remaining": int(slot_state.get("remaining", 0) or 0),
        "reaction": bool(decl.get("reaction", False)),
        "reaction_trigger": str(decl.get("reaction_trigger") or ""),
    })

    return {
        "ok": True,
        "declaration": decl,
        "slot_state": slot_state,
        "spellcasting": sheet.get("spellcasting", {}),
        "known_state": list_state,
    }


@app.get("/api/campaigns/{campaign_id}/next_spell_declarations")
async def api_next_spell_declarations(campaign_id: str):
    st = get_state(campaign_id)
    raw = list(st.get("pending_spell_declarations", []) or [])
    out = [d for d in raw if not bool((d or {}).get("reaction"))]
    st["pending_spell_declarations"] = []
    return out


@app.get("/api/campaigns/{campaign_id}/next_reaction_spell_declarations")
async def api_next_reaction_spell_declarations(campaign_id: str):
    st = get_state(campaign_id)
    out = list(st.get("pending_reaction_spell_declarations", []) or [])
    st["pending_reaction_spell_declarations"] = []
    return out




class ReactionResponseSubmission(BaseModel):
    request_id: str
    choice: str = "decline"  # accept|decline
    reaction_kind: str = ""
    spell_id: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)


@app.post("/api/campaigns/{campaign_id}/reactions/respond")
async def api_reaction_respond(campaign_id: str, sub: ReactionResponseSubmission, sess=Depends(require_session)):
    st = get_state(campaign_id)
    item = {
        "request_id": str(sub.request_id or "").strip(),
        "choice": str(sub.choice or "decline").strip().lower(),
        "reaction_kind": str(sub.reaction_kind or "").strip(),
        "spell_id": str(sub.spell_id or "").strip(),
        "payload": dict(sub.payload or {}),
        "player_id": str(sess.get("player_id") or "").strip(),
        "character_id": str(sess.get("active_character_id") or "").strip(),
        "received_at": int(time.time()),
    }
    if not item["request_id"]:
        raise HTTPException(status_code=400, detail="Missing request_id")
    st.setdefault("reaction_response_queue", []).append(item)
    if len(st["reaction_response_queue"]) > 500:
        del st["reaction_response_queue"][:-500]
    return {"ok": True, "queued": len(st["reaction_response_queue"])}


@app.get("/api/campaigns/{campaign_id}/next_reaction_responses")
async def api_next_reaction_responses(campaign_id: str):
    st = get_state(campaign_id)
    out = list(st.get("reaction_response_queue", []) or [])
    st["reaction_response_queue"] = []
    return out


class AutoReactionSpellUseRequest(BaseModel):
    spell_id: str
    slot_level: int = 0
    note: str = ""


@app.post("/api/campaigns/{campaign_id}/characters/{character_id}/use_reaction_spell")
async def api_use_reaction_spell(campaign_id: str, character_id: str, req: AutoReactionSpellUseRequest):
    char_id = str(character_id or "").strip()
    if not char_id:
        raise HTTPException(status_code=400, detail="Missing character_id")
    sheet = load_character_sheet(campaign_id, char_id)
    ensure_sheet_minimum(sheet)
    sheet = _recompute_sheet_derived_state(sheet)
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    spell_id = _slug_key(req.spell_id)
    if not spell_id:
        raise HTTPException(status_code=400, detail="Missing spell_id")
    known = {str(x).strip() for x in list(sc.get("known_spell_ids") or []) if str(x).strip()}
    prepared = {str(x).strip() for x in list(sc.get("prepared_spell_ids") or []) if str(x).strip()}
    always_prepared = {str(x).strip() for x in list(sc.get("always_prepared_spell_ids") or []) if str(x).strip()}
    spellbook = {str(x).strip() for x in list(sc.get("spellbook_spell_ids") or []) if str(x).strip()}
    if spell_id not in known and spell_id not in prepared and spell_id not in always_prepared and spell_id not in spellbook:
        raise HTTPException(status_code=400, detail="Spell not available to character")
    db = load_spells_db(campaign_id)
    row = db.get(spell_id) or {}
    if not row and spell_id == "counterspell":
        row = {"spell_id": "counterspell", "name": "Counterspell", "level": 3, "reaction": True, "reaction_trigger": "when you see a creature cast a spell"}
    if not row:
        raise HTTPException(status_code=404, detail="Unknown spell_id")
    spell_level = int(row.get("level", 0) or 0)
    slot_level = int(req.slot_level or spell_level or 0)
    if spell_level > 0:
        if slot_level < spell_level:
            raise HTTPException(status_code=400, detail="slot_level below spell level")
        ok, msg, slot_state = _consume_any_spell_slot(sheet, int(slot_level), count=1, slot_source="auto_reaction")
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
    else:
        slot_state = {"level": 0, "remaining": 999, "slot_source": "cantrip"}
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "character_id": char_id, "spell_id": spell_id, "slot_state": slot_state, "spell_level": spell_level}


@app.post("/api/campaigns/{campaign_id}/rest/control/start")
async def api_rest_control_start(campaign_id: str, req: DMRestControlRequest):
    rest_type = _slug_key(req.rest_type)
    if rest_type not in ("short_rest", "long_rest"):
        raise HTTPException(status_code=400, detail="Unsupported rest type")
    current = _get_rest_state(campaign_id)
    if current and str(current.get("status") or "") == "active":
        raise HTTPException(status_code=409, detail="A rest is already active")
    participants_list = _active_rest_participants(campaign_id)
    participants = {p["character_id"]: {"player_id": p["player_id"], "done": False} for p in participants_list if p.get("character_id")}
    state = {
        "rest_id": uuid.uuid4().hex[:12],
        "type": rest_type,
        "status": "active",
        "started_at": int(time.time() * 1000),
        "participants": participants,
    }
    _set_rest_state(campaign_id, state)
    return {"ok": True, "active": True, "rest_type": rest_type, "participant_count": len(participants), "participants": participants_list}


@app.post("/api/campaigns/{campaign_id}/rest/control/resolve")
async def api_rest_control_resolve(campaign_id: str, req: DMRestControlRequest):
    rest_type = _slug_key(req.rest_type)
    state = _get_rest_state(campaign_id)
    if not state or str(state.get("status") or "") != "active":
        raise HTTPException(status_code=409, detail="No active rest to resolve")
    if rest_type and str(state.get("type") or "") != rest_type:
        raise HTTPException(status_code=409, detail="Active rest type mismatch")
    participants = state.get("participants") if isinstance(state.get("participants"), dict) else {}
    resolved = []
    if str(state.get("type") or "") == "short_rest":
        for char_id in list(participants.keys()):
            sheet = load_character_sheet(campaign_id, char_id)
            if not sheet:
                continue
            sheet = ensure_sheet_minimum(sheet, char_id)
            _refresh_resource_pools_for_rest(sheet, "short_rest")
            refresh_spell_slots(sheet, "short_rest")
            _refresh_pact_magic_for_rest(sheet, "short_rest")
            _sync_sheet_derived_resources(sheet)
            sheet = _recompute_sheet_derived_state(campaign_id, char_id, sheet)
            save_character_sheet(campaign_id, char_id, sheet)
            resolved.append(char_id)
    else:
        for char_id in list(participants.keys()):
            sheet = load_character_sheet(campaign_id, char_id)
            if not sheet:
                continue
            sheet = ensure_sheet_minimum(sheet, char_id)
            sheet = _apply_long_rest_to_sheet(campaign_id, char_id, sheet)
            save_character_sheet(campaign_id, char_id, sheet)
            resolved.append(char_id)
    state["status"] = "resolved"
    _set_rest_state(campaign_id, state)
    _clear_rest_state(campaign_id)
    return {"ok": True, "resolved": True, "rest_type": rest_type or str(state.get("type") or ""), "participants": resolved}


@app.post("/api/campaigns/{campaign_id}/rest/control/cancel")
async def api_rest_control_cancel(campaign_id: str, req: DMRestControlRequest):
    rest_type = _slug_key(req.rest_type)
    state = _get_rest_state(campaign_id)
    if not state or str(state.get("status") or "") != "active":
        raise HTTPException(status_code=409, detail="No active rest to cancel")
    if rest_type and str(state.get("type") or "") != rest_type:
        raise HTTPException(status_code=409, detail="Active rest type mismatch")
    _clear_rest_state(campaign_id)
    return {"ok": True, "cancelled": True, "rest_type": rest_type or str(state.get("type") or "")}


@app.post("/api/campaigns/{campaign_id}/notes/mine")
async def api_add_note_mine(campaign_id: str, req: AddNoteRequest, sess=Depends(require_session)):
    """Create a note for the active character and also mirror it into inventory as a note item."""
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)

    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    title = (req.title or "").strip()
    text = (req.text or "").strip()
    now = int(time.time())

    note_id = uuid.uuid4().hex
    note = {
        "note_id": note_id,
        "title": title or "Note",
        "text": text,
        "created_at": now,
        "updated_at": now,
    }
    sheet.setdefault("notes", [])
    if not isinstance(sheet["notes"], list):
        sheet["notes"] = []
    sheet["notes"].append(note)

    # Mirror into inventory (Phase C convenience)
    sheet.setdefault("inventory", [])
    if not isinstance(sheet["inventory"], list):
        sheet["inventory"] = []
    sheet["inventory"].append({
        "type": "note",
        "note_id": note_id,
        "name": note["title"],
        "qty": 1,
        "weight": 0,
        "created_at": now,
    })

    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "note_id": note_id}


@app.get("/api/campaigns/{campaign_id}/notes/mine")
async def api_list_notes_mine(campaign_id: str, sess=Depends(require_session)):
    """List notes for the active character."""
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)

    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    notes = sheet.get("notes", [])
    if not isinstance(notes, list):
        notes = []
    out = []
    for n in notes:
        if not isinstance(n, dict):
            continue
        out.append({
            "note_id": (n.get("note_id") or "").strip(),
            "title": n.get("title") or "Note",
            "updated_at": int(n.get("updated_at") or n.get("created_at") or 0),
            "created_at": int(n.get("created_at") or 0),
        })
    out.sort(key=lambda x: int(x.get("updated_at", 0)), reverse=True)
    return {"notes": out}


@app.get("/api/campaigns/{campaign_id}/notes/mine/{note_id}")
async def api_get_note_mine(campaign_id: str, note_id: str, sess=Depends(require_session)):
    """Get a single note for the active character."""
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)

    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    nid = (note_id or "").strip()
    notes = sheet.get("notes", [])
    if not isinstance(notes, list):
        notes = []
    for n in notes:
        if isinstance(n, dict) and (n.get("note_id") or "").strip() == nid:
            return JSONResponse(n)
    raise HTTPException(status_code=404, detail="Note not found")


@app.patch("/api/campaigns/{campaign_id}/notes/mine/{note_id}")
async def api_update_note_mine(campaign_id: str, note_id: str, req: AddNoteRequest, sess=Depends(require_session)):
    """Update a note title/text for the active character."""
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)

    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    nid = (note_id or "").strip()
    title = (req.title or "").strip()
    text = (req.text or "").strip()
    now = int(time.time())

    notes = sheet.get("notes", [])
    if not isinstance(notes, list):
        notes = []
        sheet["notes"] = notes

    found = False
    for n in notes:
        if not isinstance(n, dict):
            continue
        if (n.get("note_id") or "").strip() != nid:
            continue
        if title:
            n["title"] = title
        n["text"] = text
        n["updated_at"] = now
        found = True
        break
    if not found:
        raise HTTPException(status_code=404, detail="Note not found")

    # Mirror title into inventory note item if present
    inv = sheet.get("inventory", [])
    if isinstance(inv, list):
        for it in inv:
            if not isinstance(it, dict):
                continue
            if (it.get("type") == "note") and ((it.get("note_id") or "").strip() == nid):
                if title:
                    it["name"] = title
                break

    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "note_id": nid, "updated_at": now}


@app.delete("/api/campaigns/{campaign_id}/notes/mine/{note_id}")
async def api_delete_note_mine(campaign_id: str, note_id: str, sess=Depends(require_session)):
    """Delete a note from the active character (and remove its inventory mirror)."""
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)

    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    nid = (note_id or "").strip()
    notes = sheet.get("notes", [])
    if not isinstance(notes, list):
        notes = []
    before = len(notes)
    notes = [n for n in notes if not (isinstance(n, dict) and (n.get("note_id") or "").strip() == nid)]
    sheet["notes"] = notes

    inv = sheet.get("inventory", [])
    if isinstance(inv, list):
        inv = [it for it in inv if not (isinstance(it, dict) and it.get("type") == "note" and (it.get("note_id") or "").strip() == nid)]
        sheet["inventory"] = inv

    deleted = (before != len(notes))
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "deleted": deleted}


class SendNoteRequest(BaseModel):
    to_character_id: str


@app.post("/api/campaigns/{campaign_id}/notes/mine/{note_id}/send")
async def api_send_note_mine(campaign_id: str, note_id: str, req: SendNoteRequest, sess=Depends(require_session)):
    """Send a copy of a note to another character (peer-to-peer convenience)."""
    from_char = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, from_char)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, from_char)

    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    nid = (note_id or "").strip()
    src = None
    for n in (sheet.get("notes", []) or []):
        if isinstance(n, dict) and (n.get("note_id") or "").strip() == nid:
            src = n
            break
    if not src:
        raise HTTPException(status_code=404, detail="Note not found")

    to_char = (req.to_character_id or "").strip()
    if not to_char:
        raise HTTPException(status_code=400, detail="to_character_id required")
    recv = load_character_sheet(campaign_id, to_char)
    if not recv:
        raise HTTPException(status_code=404, detail="Recipient character not found")
    recv = ensure_sheet_minimum(recv, to_char)

    now = int(time.time())
    new_id = uuid.uuid4().hex
    note = {
        "note_id": new_id,
        "title": src.get("title") or "Note",
        "text": src.get("text") or "",
        "created_at": now,
        "updated_at": now,
        "from_character_id": from_char,
        "from_player_id": sess.get("player_id", ""),
    }
    recv.setdefault("notes", [])
    if not isinstance(recv["notes"], list):
        recv["notes"] = []
    recv["notes"].append(note)
    recv.setdefault("inventory", [])
    if not isinstance(recv["inventory"], list):
        recv["inventory"] = []
    recv["inventory"].append({
        "type": "note",
        "note_id": new_id,
        "name": note["title"],
        "qty": 1,
        "weight": 0,
        "created_at": now,
    })

    save_character_sheet(campaign_id, to_char, recv)
    return {"ok": True, "sent": True, "to_character_id": to_char, "note_id": new_id}


class InventoryAddRequest(BaseModel):
    item_id: str
    qty: int = Field(1, ge=1)


class InventoryAdjustRequest(BaseModel):
    index: int = Field(..., ge=0)
    delta: int = Field(...)


class InventoryRemoveRequest(BaseModel):
    index: int = Field(..., ge=0)


class EquipRequest(BaseModel):
    weapon_id: Optional[str] = None
    armor_id: Optional[str] = None


class InventoryUseRequest(BaseModel):
    index: int = Field(..., ge=0)


class InventoryTransferRequest(BaseModel):
    index: int = Field(..., ge=0)
    qty: int = Field(1, ge=1)
    to_character_id: str = Field(..., description="Target character_id")


def _parse_heal_amount(effect: str) -> int:
    """Parse a simple effect string like 'Heal 10' (case-insensitive)."""
    if not effect:
        return 0
    s = str(effect).strip().lower()
    if not s.startswith("heal"):
        return 0
    # Accept: "heal 10" / "heal:10" / "heal+10"
    for ch in [":", "+"]:
        s = s.replace(ch, " ")
    parts = [p for p in s.split() if p]
    if len(parts) < 2:
        return 0
    try:
        amt = int(float(parts[1]))
    except Exception:
        return 0
    return max(0, amt)


def _push_hp_log(sheet: dict, delta: int, source: str, extra: Dict[str, Any]) -> None:
    """Append to sheet["hp_log"] with a small cap."""
    sheet.setdefault("hp_log", [])
    if not isinstance(sheet.get("hp_log"), list):
        sheet["hp_log"] = []
    entry = {
        "ts": int(time.time()),
        "delta": int(delta),
        "source": str(source or ""),
    }
    try:
        if isinstance(extra, dict):
            entry.update(extra)
    except Exception:
        pass
    sheet["hp_log"].append(entry)
    if len(sheet["hp_log"]) > 200:
        del sheet["hp_log"][:-200]


@app.post("/api/campaigns/{campaign_id}/inventory/mine/add")
async def api_inventory_add_mine(campaign_id: str, req: InventoryAddRequest, sess=Depends(require_session)):
    """Player-managed inventory add. Server resolves weight/icon from items.json when possible."""
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    item_id = (req.item_id or "").strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="item_id required")

    idx = _items_index(campaign_id)
    src = idx.get(item_id, {}) if isinstance(idx.get(item_id, {}), dict) else {}
    name = str(src.get("name") or item_id)
    weight = float(src.get("weight") or 0) if src.get("weight") is not None else 0.0
    icon = str(src.get("icon") or "").strip()
    itype = str(src.get("type") or "item").strip()

    sheet.setdefault("inventory", [])
    if not isinstance(sheet["inventory"], list):
        sheet["inventory"] = []

    # Merge with existing stack (same item_id, not notes)
    for it in sheet["inventory"]:
        if not isinstance(it, dict):
            continue
        if it.get("type") == "note":
            continue
        if str(it.get("item_id") or "").strip() == item_id:
            it["qty"] = int(it.get("qty", 1) or 1) + int(req.qty)
            save_character_sheet(campaign_id, char_id, sheet)
            return {"ok": True, "merged": True}

    sheet["inventory"].append({
        "type": itype,
        "item_id": item_id,
        "name": name,
        "qty": int(req.qty),
        "weight": weight,
        "icon": icon,
        "created_at": int(time.time()),
    })
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "merged": False}


@app.post("/api/campaigns/{campaign_id}/inventory/mine/adjust")
async def api_inventory_adjust_mine(campaign_id: str, req: InventoryAdjustRequest, sess=Depends(require_session)):
    """Adjust quantity by delta for a specific inventory row index (cannot adjust note items)."""
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    inv = sheet.get("inventory", [])
    if not isinstance(inv, list) or req.index >= len(inv):
        raise HTTPException(status_code=400, detail="Invalid inventory index")
    it = inv[req.index]
    if not isinstance(it, dict):
        raise HTTPException(status_code=400, detail="Invalid inventory row")
    if it.get("type") == "note":
        raise HTTPException(status_code=403, detail="Cannot adjust note quantity")

    cur = int(it.get("qty", 1) or 1)
    cur = max(0, cur + int(req.delta))
    if cur <= 0:
        inv.pop(req.index)
    else:
        it["qty"] = cur

    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True}


@app.post("/api/campaigns/{campaign_id}/inventory/mine/remove")
async def api_inventory_remove_mine(campaign_id: str, req: InventoryRemoveRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    inv = sheet.get("inventory", [])
    if not isinstance(inv, list) or req.index >= len(inv):
        raise HTTPException(status_code=400, detail="Invalid inventory index")
    inv.pop(req.index)
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True}


@app.post("/api/campaigns/{campaign_id}/equip/mine")
async def api_equip_mine(campaign_id: str, req: EquipRequest, sess=Depends(require_session)):
    """Set equipped weapon/armor ids for the active character."""
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    sheet.setdefault("equipped", {})
    if not isinstance(sheet["equipped"], dict):
        sheet["equipped"] = {}

    items_db = _load_items_db(campaign_id)
    if req.weapon_id is not None and (req.weapon_id or "").strip():
        weapon = _find_item_by_id(items_db, "weapons", (req.weapon_id or "").strip())
        if weapon and not _has_weapon_proficiency(sheet, weapon):
            raise HTTPException(status_code=400, detail="Character is not proficient with that weapon")
    if req.armor_id is not None and (req.armor_id or "").strip():
        armor = _find_item_by_id(items_db, "armors", (req.armor_id or "").strip())
        if armor and not _has_armor_proficiency(sheet, armor):
            raise HTTPException(status_code=400, detail="Character is not proficient with that armor")

    if req.weapon_id is not None:
        sheet["equipped"]["weapon_id"] = (req.weapon_id or "").strip()
        # Keep legacy mirror in sync
        sheet.setdefault("base_stats", {})
        if isinstance(sheet["base_stats"], dict):
            sheet["base_stats"]["weapon_id"] = sheet["equipped"]["weapon_id"]
    if req.armor_id is not None:
        sheet["equipped"]["armor_id"] = (req.armor_id or "").strip()
        sheet.setdefault("base_stats", {})
        if isinstance(sheet["base_stats"], dict):
            sheet["base_stats"]["armor_id"] = sheet["equipped"]["armor_id"]

    print(
        "[EQUIP_REQ]",
        "player=", sess.get("player_id", ""),
        "char=", char_id,
        "weapon_id=", req.weapon_id,
        "armor_id=", req.armor_id,
        "before=", (sheet.get("equipped", {}) or {}).copy(),
    )

    sheet = _recompute_sheet_derived_state(campaign_id, char_id, sheet)
    save_character_sheet(campaign_id, char_id, sheet)
    fresh = load_character_sheet(campaign_id, char_id)
    print("[EQUIP_SAVED]", fresh.get("equipped", {}), "AC=", (fresh.get("stats", {}) or {}).get("defense"))
    return {
        "ok": True,
        "equipped": sheet.get("equipped", {}),
        "defense": (sheet.get("stats", {}) or {}).get("defense", 10),
    }

@app.get("/api/campaigns/{campaign_id}/party/characters")
async def api_party_characters(campaign_id: str, sess=Depends(require_session)):
    """Minimal list of all character sheets in the campaign (for peer transfer UI)."""
    st = get_state(campaign_id)
    out: List[Dict[str, Any]] = []
    try:
        if not os.path.isdir(st["char_dir"]):
            return {"characters": out}
        for fn in os.listdir(st["char_dir"]):
            if not fn.lower().endswith(".json"):
                continue
            cid = fn[:-5]
            sheet = load_character_sheet(campaign_id, cid)
            if not sheet:
                continue
            sheet = ensure_sheet_minimum(sheet, cid)
            out.append({
                "character_id": cid,
                "display_name": sheet.get("display_name", cid),
                "player_id": (sheet.get("player_id") or "").strip(),
            })
    except Exception:
        pass
    out.sort(key=lambda x: (x.get("display_name") or x.get("character_id") or "").lower())
    return {"characters": out}


@app.post("/api/campaigns/{campaign_id}/inventory/mine/use")
async def api_inventory_use_mine(campaign_id: str, req: InventoryUseRequest, sess=Depends(require_session)):
    """Use an inventory item (MVP: consumable heal effects only)."""
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    inv = sheet.get("inventory", [])
    if not isinstance(inv, list) or req.index >= len(inv):
        raise HTTPException(status_code=400, detail="Invalid inventory index")
    it = inv[req.index]
    if not isinstance(it, dict):
        raise HTTPException(status_code=400, detail="Invalid inventory row")
    if it.get("type") == "note":
        raise HTTPException(status_code=403, detail="Cannot use note")

    item_id = str(it.get("item_id") or "").strip()
    idx = _items_index(campaign_id)
    src = idx.get(item_id, {}) if isinstance(idx.get(item_id, {}), dict) else {}

    effect = str(src.get("effect") or it.get("effect") or "").strip()
    heal_amt = _parse_heal_amount(effect)
    if heal_amt <= 0:
        raise HTTPException(status_code=400, detail="Item has no usable effect")

    cur_qty = int(it.get("qty", 1) or 1)
    if cur_qty <= 0:
        raise HTTPException(status_code=400, detail="No quantity to use")
    if cur_qty == 1:
        inv.pop(req.index)
    else:
        it["qty"] = cur_qty - 1

    st_stats = sheet.get("stats", {}) if isinstance(sheet.get("stats"), dict) else {}
    max_hp = int(st_stats.get("max_hp", 10) or 10)
    cur_hp = int(st_stats.get("current_hp", max_hp) or max_hp)
    new_hp = min(max_hp, cur_hp + heal_amt)
    delta = new_hp - cur_hp
    st_stats["current_hp"] = new_hp
    sheet["stats"] = st_stats
    sheet.setdefault("resources", {})
    if isinstance(sheet.get("resources"), dict):
        sheet["resources"]["current_hp"] = new_hp

    _push_hp_log(sheet, delta=delta, source="use_item", extra={
        "item_id": item_id,
        "item_name": str(src.get("name") or it.get("name") or item_id),
        "heal": heal_amt,
        "after": {"current_hp": new_hp, "max_hp": max_hp},
    })

    save_character_sheet(campaign_id, char_id, sheet)

    try:
        st_campaign = get_state(campaign_id)
        _push_player_log(st_campaign, sess["player_id"], {
            "ts": time.time(),
            "type": "ITEM_USED",
            "character_id": char_id,
            "item_id": item_id,
            "heal": heal_amt,
            "hp": {"before": cur_hp, "after": new_hp, "max": max_hp},
        })
    except Exception:
        pass

    return {"ok": True, "heal": heal_amt, "current_hp": new_hp, "max_hp": max_hp}


@app.post("/api/campaigns/{campaign_id}/inventory/mine/transfer")
async def api_inventory_transfer_mine(campaign_id: str, req: InventoryTransferRequest, sess=Depends(require_session)):
    """Peer-to-peer inventory transfer (no DM approval)."""
    from_char = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, from_char)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, from_char)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    inv = sheet.get("inventory", [])
    if not isinstance(inv, list) or req.index >= len(inv):
        raise HTTPException(status_code=400, detail="Invalid inventory index")
    it = inv[req.index]
    if not isinstance(it, dict):
        raise HTTPException(status_code=400, detail="Invalid inventory row")
    if it.get("type") == "note":
        raise HTTPException(status_code=403, detail="Use notes/send to transfer notes")

    to_char = (req.to_character_id or "").strip()
    if not to_char:
        raise HTTPException(status_code=400, detail="to_character_id required")
    recv = load_character_sheet(campaign_id, to_char)
    if not recv:
        raise HTTPException(status_code=404, detail="Recipient character sheet not found")
    recv = ensure_sheet_minimum(recv, to_char)

    qty = int(req.qty)
    cur_qty = int(it.get("qty", 1) or 1)
    if qty <= 0 or qty > cur_qty:
        raise HTTPException(status_code=400, detail="Invalid qty")

    # Remove from sender
    if qty == cur_qty:
        inv.pop(req.index)
    else:
        it["qty"] = cur_qty - qty

    # Add to recipient (stack)
    recv.setdefault("inventory", [])
    if not isinstance(recv.get("inventory"), list):
        recv["inventory"] = []

    item_id = str(it.get("item_id") or "").strip()
    added = False
    for rit in recv["inventory"]:
        if not isinstance(rit, dict):
            continue
        if rit.get("type") == "note":
            continue
        if str(rit.get("item_id") or "").strip() == item_id:
            rit["qty"] = int(rit.get("qty", 1) or 1) + qty
            added = True
            break
    if not added:
        recv["inventory"].append({
            "type": str(it.get("type") or "item").strip(),
            "item_id": item_id,
            "name": str(it.get("name") or item_id),
            "qty": qty,
            "weight": float(it.get("weight") or 0) if it.get("weight") is not None else 0.0,
            "icon": str(it.get("icon") or "").strip(),
            "created_at": int(time.time()),
        })

    save_character_sheet(campaign_id, from_char, sheet)
    save_character_sheet(campaign_id, to_char, recv)

    try:
        st_campaign = get_state(campaign_id)
        to_player = (recv.get("player_id") or "").strip()
        payload = {
            "ts": time.time(),
            "type": "INVENTORY_TRANSFER",
            "item_id": item_id,
            "item_name": str(it.get("name") or item_id),
            "qty": qty,
            "from_character_id": from_char,
            "to_character_id": to_char,
        }
        _push_player_log(st_campaign, sess["player_id"], dict(payload, direction="sent"))
        if to_player:
            _push_player_log(st_campaign, to_player, dict(payload, direction="received"))
    except Exception:
        pass

    return {"ok": True, "to_character_id": to_char, "qty": qty}

@app.patch("/api/campaigns/{campaign_id}/sheet/mine")
async def api_patch_sheet_mine(campaign_id: str, req: PatchSheetRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)

    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")

    creating = _is_creating(sheet)
    patch_obj = req.patch or {}
    if not isinstance(patch_obj, dict):
        raise HTTPException(status_code=400, detail="patch must be an object")

    def _flatten(d, prefix=""):
        out = []
        for k, v in d.items():
            key = f"{prefix}{k}" if not prefix else f"{prefix}.{k}"
            if isinstance(v, dict):
                out.extend(_flatten(v, key))
            else:
                out.append((key, v))
        return out

    updates = _flatten(patch_obj)
    changed = []
    max_hp = int(_get_path(sheet, "stats.max_hp", 10) or 10)

    for path_str, value in updates:
        path_str = str(path_str)

        if path_str in _ALLOWED_ALWAYS:
            if path_str.endswith("current_hp"):
                try:
                    cur = int(value)
                except Exception:
                    continue
                cur = max(0, min(cur, max_hp))
                _set_path(sheet, "stats.current_hp", cur)
                _set_path(sheet, "resources.current_hp", cur)
                changed.append("stats.current_hp")
            elif path_str.endswith("temp_hp"):
                try:
                    tmp = int(value)
                except Exception:
                    continue
                tmp = max(0, tmp)
                _set_path(sheet, "resources.temp_hp", tmp)
                changed.append("resources.temp_hp")
            else:
                _set_path(sheet, path_str, value)
                changed.append(path_str)
            continue

        # Always-editable prefixes
        if any(path_str.startswith(pfx) for pfx in _ALLOWED_ALWAYS_PREFIXES):
            # Coerce some common numeric fields
            if path_str.startswith("currency."):
                try:
                    value = int(value)
                except Exception:
                    continue
                value = max(0, min(value, 10_000_000))
            if path_str.startswith("spellcasting.") and path_str.endswith(("save_dc", "attack_bonus")):
                try:
                    value = int(value)
                except Exception:
                    continue
                value = max(0, min(value, 99))
            if path_str.startswith("spellcasting.spells.") and path_str.endswith(("total", "used")):
                try:
                    value = int(value)
                except Exception:
                    continue
                value = max(0, min(value, 99))
            if path_str in ("spellcasting.cantrips", "spellcasting.known_spells", "spellcasting.prepared_spells", "spellcasting.spellbook_spells"):
                if isinstance(value, str):
                    value = [v.strip() for v in value.replace("\n", ",").split(",") if v.strip()]
                elif isinstance(value, list):
                    value = [str(v).strip() for v in value if str(v).strip()]
                else:
                    continue
            if path_str.startswith("combat."):
                if path_str in ("combat.hit_die_sides", "combat.hit_dice_total", "combat.hit_dice_used"):
                    try:
                        value = int(value)
                    except Exception:
                        continue
                    if path_str == "combat.hit_die_sides":
                        value = max(1, min(value, 20))
                    else:
                        value = max(0, min(value, 99))
                if path_str.startswith("combat.death_saves."):
                    try:
                        value = int(value)
                    except Exception:
                        continue
                    value = max(0, min(value, 3))
            _set_path(sheet, path_str, value)
            changed.append(path_str)
            continue

        if creating and any(path_str.startswith(pfx) for pfx in _ALLOWED_CREATING_PREFIXES):
            if path_str in ("meta.level", "meta.experience_points"):
                try:
                    value = int(value)
                except Exception:
                    continue
                if path_str == "meta.level":
                    value = max(1, min(value, 20))
            if path_str.startswith("abilities."):
                try:
                    value = int(value)
                except Exception:
                    continue
                value = max(1, min(value, 30))
            if path_str in ("stats.max_hp", "stats.defense", "stats.movement_ft", "stats.vision_ft", "stats.attack_modifier"):
                try:
                    value = int(value)
                except Exception:
                    continue
                if path_str == "stats.max_hp":
                    value = max(1, min(value, 999))
                    cur = int(_get_path(sheet, "stats.current_hp", value) or value)
                    cur = max(0, min(cur, value))
                    _set_path(sheet, "stats.current_hp", cur)
                    _set_path(sheet, "resources.current_hp", cur)
                if path_str == "stats.defense":
                    value = max(0, min(value, 99))
                    _set_path(sheet, "stats.defense_base", value)
                if path_str == "stats.movement_ft":
                    value = max(0, min(value, 300))
                if path_str == "stats.vision_ft":
                    value = max(0, min(value, 1000))
            _set_path(sheet, path_str, value)
            changed.append(path_str)
            continue

        raise HTTPException(status_code=403, detail=f"Field not editable: {path_str}")

    sheet = ensure_sheet_minimum(sheet, char_id)

    if "stats.current_hp" in changed or "resources.temp_hp" in changed:
        sheet.setdefault("hp_log", [])
        sheet["hp_log"].append({
            "ts": int(time.time()),
            "delta": 0,
            "source": "player_edit",
            "reason": "sheet_patch",
            "after": {
                "current_hp": int(_get_path(sheet, "stats.current_hp", 0) or 0),
                "temp_hp": int(_get_path(sheet, "resources.temp_hp", 0) or 0),
                "max_hp": int(_get_path(sheet, "stats.max_hp", 10) or 10),
            }
        })

    if req.finalize:
        if not creating:
            raise HTTPException(status_code=400, detail="Character is not in creating state")
        sheet.setdefault("lifecycle", {})
        sheet["lifecycle"]["status"] = "active"
        sheet["lifecycle"]["finalized_at"] = int(time.time())

    # Keep back-compat arrays in sync with the preferred boolean maps.
    try:
        prof = sheet.get("proficiencies", {}) or {}
        if isinstance(prof, dict):
            saves = prof.get("saves", {})
            skills = prof.get("skills", {})
            if isinstance(saves, dict):
                prof["save_proficiencies"] = sorted([k for k, v in saves.items() if v])
            if isinstance(skills, dict):
                prof["skill_proficiencies"] = sorted([k for k, v in skills.items() if v])
    except Exception:
        pass

    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "character_id": char_id, "changed": changed, "creating": _is_creating(sheet)}


# ------------------------------------------------------------
# Items endpoint (campaign-scoped)
# ------------------------------------------------------------
def load_items_db(campaign_id: str) -> dict:
    st = get_state(campaign_id)
    data = _read_json(st["items_path"], {
        "weapons": [], "armors": [], "health_items": [], "misc_items": []
    })
    if not isinstance(data, dict):
        data = {"weapons": [], "armors": [], "health_items": [], "misc_items": []}
    for k in ("weapons", "armors", "health_items", "misc_items"):
        if k not in data or not isinstance(data.get(k), list):
            data[k] = []
    return data


def _items_index(campaign_id: str) -> Dict[str, Dict[str, Any]]:
    """Flatten campaign items.json into a simple item_id -> item dict index."""
    db = load_items_db(campaign_id)
    idx: Dict[str, Dict[str, Any]] = {}
    for cat in ("weapons", "armors", "health_items", "misc_items"):
        arr = db.get(cat, []) or []
        if not isinstance(arr, list):
            continue
        for it in arr:
            if not isinstance(it, dict):
                continue
            item_id = str(it.get("item_id") or it.get("id") or it.get("weapon_id") or it.get("armor_id") or "").strip()
            if not item_id:
                continue
            idx[item_id] = it
    return idx

@app.get("/api/campaigns/{campaign_id}/items")
async def api_items(campaign_id: str, sess=Depends(require_session)):
    # items are safe to show to authenticated player
    return JSONResponse(load_items_db(campaign_id))


@app.post("/api/campaigns/{campaign_id}/spells/mine/wizard/learn")
async def api_wizard_learn_spell_mine(campaign_id: str, req: WizardSpellbookLearnRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    cid = _safe_campaign_id(campaign_id)
    state = _levelup_states.get(cid, {}) if isinstance(_levelup_states.get(cid, {}), dict) else {}
    spec = state.get(str(char_id or "").strip()) if str(char_id or "").strip() else None
    if isinstance(spec, dict):
        preview = _levelup_preview_sheet(campaign_id, str(char_id or "").strip(), sheet, int(spec.get("target_level", ((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("level", 1)) or 1))
        preview_sc = preview.get("spellcasting") if isinstance(preview.get("spellcasting"), dict) else {}
        if isinstance(preview_sc, dict) and preview_sc:
            sc = preview_sc
    active_classes = set(_spell_listify(sc.get("spellcasting_classes")))
    if "wizard" not in active_classes:
        raise HTTPException(status_code=400, detail="Active character is not currently a Wizard")
    spell_id = str(req.spell_id or "").strip()
    spells_db = load_spells_db_for_campaign(campaign_id)
    spell_row = (spells_db or {}).get(spell_id) if isinstance(spells_db, dict) else None
    if not isinstance(spell_row, dict):
        raise HTTPException(status_code=404, detail="Spell not found in campaign spells.json")
    if _spell_level_from_row(spell_row) <= 0:
        raise HTTPException(status_code=400, detail="Wizard spellbooks only store leveled spells")
    spell_classes = _extract_spell_classes(spell_row)
    if spell_classes and "wizard" not in spell_classes:
        raise HTTPException(status_code=400, detail="Spell is not on the Wizard spell list")
    class_levels = sheet.get("class_levels") if isinstance(sheet.get("class_levels"), dict) else {}
    wizard_level = max(0, int(class_levels.get("wizard", 0) or 0))
    max_spell_level = _max_spell_level_for_class_progression("wizard", wizard_level)
    spell_level = _spell_level_from_row(spell_row)
    if max_spell_level > 0 and spell_level > max_spell_level:
        raise HTTPException(status_code=400, detail=f"Wizard level currently supports learning up to spell level {max_spell_level}")
    spellbook = _spell_unique(_spell_listify(sc.get("spellbook_spells") or sheet.get("spellbook_spells")))
    if spell_id in set(spellbook):
        return {"ok": True, "already_known": True, "spell_id": spell_id, "spellcasting": sc}
    spellbook.append(spell_id)
    sc["spellbook_spells"] = spellbook
    sheet["spellbook_spells"] = list(spellbook)
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "spell_id": spell_id, "spellcasting": sheet.get("spellcasting", {})}


@app.post("/api/campaigns/{campaign_id}/spells/mine/prepared/set")
async def api_set_prepared_spells_mine(campaign_id: str, req: SpellListUpdateRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    cid = _safe_campaign_id(campaign_id)
    state = _levelup_states.get(cid, {}) if isinstance(_levelup_states.get(cid, {}), dict) else {}
    spec = state.get(str(char_id or "").strip()) if str(char_id or "").strip() else None
    if isinstance(spec, dict):
        preview = _levelup_preview_sheet(campaign_id, str(char_id or "").strip(), sheet, int(spec.get("target_level", ((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("level", 1)) or 1))
        preview_sc = preview.get("spellcasting") if isinstance(preview.get("spellcasting"), dict) else {}
        if isinstance(preview_sc, dict) and preview_sc:
            sc = preview_sc
    incoming = _validate_spell_list_update(campaign_id, sheet, "prepared", req.spell_ids or [], replacing=bool(req.replace), character_id=char_id)
    current = _spell_unique(_spell_listify(sc.get("prepared_spells") or sheet.get("prepared_spells")))
    prepared = list(incoming) if bool(req.replace) else _spell_unique(current + incoming)
    sc["prepared_spells"] = prepared
    sheet["prepared_spells"] = list(prepared)
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "spellcasting": sheet.get("spellcasting", {}), "levelup_state": _serialize_levelup_state_for_character(campaign_id, char_id)}


@app.post("/api/campaigns/{campaign_id}/spells/mine/known/set")
async def api_set_known_spells_mine(campaign_id: str, req: SpellListUpdateRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    cid = _safe_campaign_id(campaign_id)
    state = _levelup_states.get(cid, {}) if isinstance(_levelup_states.get(cid, {}), dict) else {}
    spec = state.get(str(char_id or "").strip()) if str(char_id or "").strip() else None
    if isinstance(spec, dict):
        preview = _levelup_preview_sheet(campaign_id, str(char_id or "").strip(), sheet, int(spec.get("target_level", ((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("level", 1)) or 1))
        preview_sc = preview.get("spellcasting") if isinstance(preview.get("spellcasting"), dict) else {}
        if isinstance(preview_sc, dict) and preview_sc:
            sc = preview_sc
    mode = str(sc.get("known_mode") or "").strip().lower()
    active_classes = set(_spell_listify(sc.get("spellcasting_classes")))
    incoming = _validate_spell_list_update(campaign_id, sheet, "known", req.spell_ids or [], replacing=bool(req.replace), character_id=char_id)
    if mode != "known":
        if "wizard" in active_classes:
            current = _spell_unique(_spell_listify(sc.get("spellbook_spells") or sheet.get("spellbook_spells")))
            spellbook = list(incoming) if bool(req.replace) else _spell_unique(current + incoming)
            sc["spellbook_spells"] = list(spellbook)
            sheet["spellbook_spells"] = list(spellbook)
            _derive_spellcasting_for_sheet(campaign_id, sheet)
            save_character_sheet(campaign_id, char_id, sheet)
            return {"ok": True, "spellcasting": sheet.get("spellcasting", {}), "aliased_from": "known", "saved_to": "spellbook"}
        raise HTTPException(status_code=400, detail="Active character is not using a known-spells model")
    current = _spell_unique(_spell_listify(sc.get("known_spells") or sheet.get("known_spells")))
    known = list(incoming) if bool(req.replace) else _spell_unique(current + incoming)
    sc["known_spells"] = known
    sheet["known_spells"] = list(known)
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "spellcasting": sheet.get("spellcasting", {}), "levelup_state": _serialize_levelup_state_for_character(campaign_id, char_id)}


@app.post("/api/campaigns/{campaign_id}/spells/mine/spellbook/set")
async def api_set_spellbook_spells_mine(campaign_id: str, req: SpellListUpdateRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    cid = _safe_campaign_id(campaign_id)
    state = _levelup_states.get(cid, {}) if isinstance(_levelup_states.get(cid, {}), dict) else {}
    spec = state.get(str(char_id or "").strip()) if str(char_id or "").strip() else None
    if isinstance(spec, dict):
        preview = _levelup_preview_sheet(campaign_id, str(char_id or "").strip(), sheet, int(spec.get("target_level", ((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("level", 1)) or 1))
        preview_sc = preview.get("spellcasting") if isinstance(preview.get("spellcasting"), dict) else {}
        if isinstance(preview_sc, dict) and preview_sc:
            sc = preview_sc
    active_classes = set(_spell_listify(sc.get("spellcasting_classes")))
    if "wizard" not in active_classes:
        raise HTTPException(status_code=400, detail="Active character is not currently a Wizard")
    incoming = _validate_spell_list_update(campaign_id, sheet, "spellbook", req.spell_ids or [], replacing=bool(req.replace), character_id=char_id)
    current = _spell_unique(_spell_listify(sc.get("spellbook_spells") or sheet.get("spellbook_spells")))
    chosen = list(incoming) if bool(req.replace) else _spell_unique(current + incoming)
    sc["spellbook_spells"] = list(chosen)
    sheet["spellbook_spells"] = list(chosen)
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "spellcasting": sheet.get("spellcasting", {}), "aliased_from": "spellbook"}



@app.post("/api/campaigns/{campaign_id}/spells/mine/cantrips/set")
async def api_set_cantrips_mine(campaign_id: str, req: SpellListUpdateRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    cid = _safe_campaign_id(campaign_id)
    state = _levelup_states.get(cid, {}) if isinstance(_levelup_states.get(cid, {}), dict) else {}
    spec = state.get(str(char_id or "").strip()) if str(char_id or "").strip() else None
    if isinstance(spec, dict):
        preview = _levelup_preview_sheet(campaign_id, str(char_id or "").strip(), sheet, int(spec.get("target_level", ((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("level", 1)) or 1))
        preview_sc = preview.get("spellcasting") if isinstance(preview.get("spellcasting"), dict) else {}
        if isinstance(preview_sc, dict) and preview_sc:
            sc = preview_sc
    incoming = _validate_spell_list_update(campaign_id, sheet, "cantrips", req.spell_ids or [], replacing=bool(req.replace), character_id=char_id)
    current = _spell_unique(_spell_listify(sc.get("cantrips")))
    cantrips = list(incoming) if bool(req.replace) else _spell_unique(current + incoming)
    sc["cantrips"] = cantrips
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "spellcasting": sheet.get("spellcasting", {}), "levelup_state": _serialize_levelup_state_for_character(campaign_id, char_id)}


@app.post("/api/campaigns/{campaign_id}/spells/mine/bonus/set")
async def api_set_bonus_spells_mine(campaign_id: str, req: SpellListUpdateRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    cid = _safe_campaign_id(campaign_id)
    state = _levelup_states.get(cid, {}) if isinstance(_levelup_states.get(cid, {}), dict) else {}
    spec = state.get(str(char_id or "").strip()) if str(char_id or "").strip() else None
    if isinstance(spec, dict):
        preview = _levelup_preview_sheet(campaign_id, str(char_id or "").strip(), sheet, int(spec.get("target_level", ((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("level", 1)) or 1))
        preview_sc = preview.get("spellcasting") if isinstance(preview.get("spellcasting"), dict) else {}
        if isinstance(preview_sc, dict) and preview_sc:
            sc = preview_sc
    active_classes = set(_spell_listify(sc.get("spellcasting_classes")))
    if "bard" not in active_classes:
        raise HTTPException(status_code=400, detail="Active character does not currently support bonus off-list spells")
    limit = max(0, int(sc.get("bonus_spell_limit", 0) or 0))
    if limit <= 0:
        raise HTTPException(status_code=400, detail="No bonus spell selections are currently available")
    incoming = _validate_spell_list_update(campaign_id, sheet, "bonus", req.spell_ids or [], replacing=bool(req.replace), character_id=char_id)
    current = _spell_unique(_spell_listify(sc.get("bonus_spell_ids") or sheet.get("bonus_spell_ids")))
    chosen = list(incoming) if bool(req.replace) else _spell_unique(current + incoming)
    sc["bonus_spell_ids"] = list(chosen)
    sheet["bonus_spell_ids"] = list(chosen)
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "spellcasting": sheet.get("spellcasting", {}), "levelup_state": _serialize_levelup_state_for_character(campaign_id, char_id)}


@app.post("/api/campaigns/{campaign_id}/spells/mine/metamagic/set")
async def api_set_metamagic_mine(campaign_id: str, req: MetamagicSelectionRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    cid = _safe_campaign_id(campaign_id)
    state = _levelup_states.get(cid, {}) if isinstance(_levelup_states.get(cid, {}), dict) else {}
    spec = state.get(str(char_id or "").strip()) if str(char_id or "").strip() else None
    if isinstance(spec, dict):
        preview = _levelup_preview_sheet(campaign_id, str(char_id or "").strip(), sheet, int(spec.get("target_level", ((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("level", 1)) or 1))
        preview_sc = preview.get("spellcasting") if isinstance(preview.get("spellcasting"), dict) else {}
        if isinstance(preview_sc, dict) and preview_sc:
            sc = preview_sc
    active_classes = set(_spell_listify(sc.get("spellcasting_classes")))
    if "sorcerer" not in active_classes:
        raise HTTPException(status_code=400, detail="Active character is not currently a Sorcerer")
    limit = max(0, int(sc.get("metamagic_choice_limit", 0) or 0))
    if limit <= 0:
        raise HTTPException(status_code=400, detail="This character does not currently have Metamagic selections")
    incoming = _sanitize_metamagic_option_ids([str(x).strip() for x in (req.option_ids or []) if str(x).strip()])
    current = _sanitize_metamagic_option_ids(_spell_listify(sc.get("metamagic_options") or sheet.get("metamagic_options")))
    chosen = list(incoming) if bool(req.replace) else _sanitize_metamagic_option_ids(current + incoming)
    sc["metamagic_options"] = list(chosen[:limit])
    sheet["metamagic_options"] = list(chosen[:limit])
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "spellcasting": sheet.get("spellcasting", {}), "levelup_state": _serialize_levelup_state_for_character(campaign_id, char_id)}


@app.post("/api/campaigns/{campaign_id}/spells/mine/spell-mastery/set")
async def api_set_spell_mastery_mine(campaign_id: str, req: WizardFeatureSelectionRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    cid = _safe_campaign_id(campaign_id)
    state = _levelup_states.get(cid, {}) if isinstance(_levelup_states.get(cid, {}), dict) else {}
    spec = state.get(str(char_id or "").strip()) if str(char_id or "").strip() else None
    if isinstance(spec, dict):
        preview = _levelup_preview_sheet(campaign_id, str(char_id or "").strip(), sheet, int(spec.get("target_level", ((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("level", 1)) or 1))
        preview_sc = preview.get("spellcasting") if isinstance(preview.get("spellcasting"), dict) else {}
        if isinstance(preview_sc, dict) and preview_sc:
            sc = preview_sc
    limit = max(0, int(sc.get("spell_mastery_limit", 0) or 0))
    if limit <= 0:
        raise HTTPException(status_code=400, detail="Spell Mastery is not currently available")
    incoming = _spell_unique([str(x).strip() for x in (req.spell_ids or []) if str(x).strip()])
    current = _spell_unique(_spell_listify(sc.get("spell_mastery_spells") or sheet.get("spell_mastery_spells")))
    chosen = list(incoming) if bool(req.replace) else _spell_unique(current + incoming)
    sc["spell_mastery_spells"] = list(chosen[:limit])
    sheet["spell_mastery_spells"] = list(chosen[:limit])
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "spellcasting": sheet.get("spellcasting", {}), "levelup_state": _serialize_levelup_state_for_character(campaign_id, char_id)}


@app.post("/api/campaigns/{campaign_id}/spells/mine/signature/set")
async def api_set_signature_spells_mine(campaign_id: str, req: WizardFeatureSelectionRequest, sess=Depends(require_session)):
    char_id = _require_active_character(sess)
    sheet = load_character_sheet(campaign_id, char_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, char_id)
    if (sheet.get("player_id") or "").strip() != sess["player_id"]:
        raise HTTPException(status_code=403, detail="Character does not belong to this player")
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
    cid = _safe_campaign_id(campaign_id)
    state = _levelup_states.get(cid, {}) if isinstance(_levelup_states.get(cid, {}), dict) else {}
    spec = state.get(str(char_id or "").strip()) if str(char_id or "").strip() else None
    if isinstance(spec, dict):
        preview = _levelup_preview_sheet(campaign_id, str(char_id or "").strip(), sheet, int(spec.get("target_level", ((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("level", 1)) or 1))
        preview_sc = preview.get("spellcasting") if isinstance(preview.get("spellcasting"), dict) else {}
        if isinstance(preview_sc, dict) and preview_sc:
            sc = preview_sc
    limit = max(0, int(sc.get("signature_spell_limit", 0) or 0))
    if limit <= 0:
        raise HTTPException(status_code=400, detail="Signature Spells are not currently available")
    incoming = _spell_unique([str(x).strip() for x in (req.spell_ids or []) if str(x).strip()])
    current = _spell_unique(_spell_listify(sc.get("signature_spells") or sheet.get("signature_spells")))
    chosen = list(incoming) if bool(req.replace) else _spell_unique(current + incoming)
    sc["signature_spells"] = list(chosen[:limit])
    sheet["signature_spells"] = list(chosen[:limit])
    _derive_spellcasting_for_sheet(campaign_id, sheet)
    save_character_sheet(campaign_id, char_id, sheet)
    return {"ok": True, "spellcasting": sheet.get("spellcasting", {}), "levelup_state": _serialize_levelup_state_for_character(campaign_id, char_id)}


@app.get("/api/campaigns/{campaign_id}/spells")
async def api_spells(campaign_id: str, sess=Depends(require_session)):
    player_id = sess["player_id"]
    active_character_id = str(sess.get("active_character_id") or "").strip()
    active_classes: List[str] = []
    if active_character_id:
        try:
            sheet = load_character_sheet(campaign_id, active_character_id)
            sheet = ensure_sheet_minimum(sheet, active_character_id)
            if (sheet.get("player_id") or "").strip() == player_id:
                _derive_spellcasting_for_sheet(campaign_id, sheet)
                sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
                active_classes = _spell_listify(sc.get("spellcasting_classes"))
        except Exception:
            active_classes = []
    rows = list(load_spells_db_for_campaign(campaign_id).values())
    spellcasting = {}
    if active_character_id:
        try:
            sheet = load_character_sheet(campaign_id, active_character_id)
            sheet = ensure_sheet_minimum(sheet, active_character_id)
            if (sheet.get("player_id") or "").strip() == player_id:
                _derive_spellcasting_for_sheet(campaign_id, sheet)
                spellcasting = dict(sheet.get("spellcasting", {}) or {})
        except Exception:
            spellcasting = {}
    if active_classes:
        for row in rows:
            if isinstance(row, dict):
                legal = sorted(_extract_spell_classes(row))
                row.setdefault("legal_for_classes", legal)
                row["is_legal_for_active_classes"] = _spell_allowed_for_any_class(row, active_classes) or str(row.get("id") or row.get("spell_id") or "") in set(_spell_listify((spellcasting or {}).get("bonus_spell_ids")))
    return {"spells": rows, "active_spellcasting_classes": active_classes, "spellcasting": spellcasting, "metamagic_catalog": [{"id": k, "name": METAMAGIC_OPTION_LABELS.get(k, k.replace("_", " ").title()), "cost": METAMAGIC_OPTION_COSTS.get(k, 0)} for k in sorted(METAMAGIC_OPTION_COSTS.keys())]}

# ------------------------------------------------------------
# Option B combat endpoints (campaign-scoped)
# DM-facing endpoints: no auth
# Player-facing endpoints: session-protected “mine”
# ------------------------------------------------------------
class PendingAttackRegistration(BaseModel):
    pending_attack_id: str
    encounter_id: str = ""

    attacker_token_id: str
    attacker_name: str = ""
    attacker_character_id: str = ""
    attacker_player_id: str

    damage_expr: str = ""

    target_token_id: str = ""
    target_name: str = ""
    target_character_id: str = ""

    weapon_id: str = ""
    weapon_name: str = ""
    roll_mode: str = "normal"

    expires_in_sec: int = 90


class RollRequestRegistration(BaseModel):
    request_id: str = Field("", description="Optional; server will generate if empty")
    character_id: str = Field(..., description="Target PC character id")
    player_id: str = Field(..., description="Owning player id")
    roll_kind: str = Field(..., description="save|check|death_save|attack_to_hit|damage|other")
    expected_sides: int = Field(..., description="Die sides expected (e.g., 20 for d20)")
    expected_count_min: int = Field(1, description="Minimum number of dice results accepted")
    expected_count_max: int = Field(1, description="Maximum number of dice results accepted")
    adv_mode: str = Field("normal", description="normal|advantage|disadvantage (controls auto-choose and UI)")
    dc: Optional[int] = Field(None, description="Optional DC for saves/checks")
    label: str = Field("", description="UI label, e.g., 'DEX Save (Fall)'")
    context: Dict[str, Any] = Field(default_factory=dict, description="Arbitrary structured context")
    ttl_s: int = Field(180, description="Time-to-live in seconds")


class RollRequestSubmit(BaseModel):
    request_id: str
    die_sides: int
    rolls: List[int]
    mode: str = Field("normal", description="normal|advantage|disadvantage (optional)")
    chosen: Optional[int] = Field(None, description="Chosen roll if multiple")
    extras: Dict[str, Any] = Field(default_factory=dict, description="Optional attachments (resource spends, etc.)")


def _cleanup_expired_pending(st: Dict[str, Any], now: float) -> None:
    pending_attacks = st["pending_attacks"]
    expired = [pid for pid, pa in pending_attacks.items() if float(pa.get("expires_at", 0)) <= now]
    for pid in expired:
        pending_attacks.pop(pid, None)


def _cleanup_expired_roll_requests(st: Dict[str, Any], now: float) -> None:
    pending = st.get("pending_roll_requests", {})
    expired = [rid for rid, rr in pending.items() if float(rr.get("expires_at", 0)) <= now]
    for rid in expired:
        pending.pop(rid, None)


def _push_player_log(st: Dict[str, Any], player_id: str, entry: Dict[str, Any]) -> None:
    """Append a structured log entry visible to the player in the portal."""
    logs = st.setdefault("player_logs", {})
    buf = logs.setdefault(player_id, [])
    buf.append(entry)
    # cap to last 200 entries
    if len(buf) > 200:
        del buf[:-200]


@app.post("/api/campaigns/{campaign_id}/pending_attacks")
async def api_register_pending_attack(campaign_id: str, req: PendingAttackRegistration):
    st = get_state(campaign_id)
    pending_attacks = st["pending_attacks"]

    now = time.time()
    ttl = int(req.expires_in_sec)

    # Remove existing pending for this attacker token (avoid stacking)
    to_remove = []
    for pid, pa in pending_attacks.items():
        if pa.get("attacker_token_id") == req.attacker_token_id:
            to_remove.append(pid)
    for pid in to_remove:
        pending_attacks.pop(pid, None)

    pending_attacks[req.pending_attack_id] = {
        "pending_attack_id": req.pending_attack_id,
        "encounter_id": req.encounter_id or "",
        "created_at": int(now),
        "expires_at": float(now + float(ttl)),
        "ttl_seconds": ttl,
        "damage_expr": req.damage_expr or "",

        "attacker_token_id": req.attacker_token_id,
        "attacker_name": req.attacker_name or "",
        "attacker_character_id": req.attacker_character_id or "",
        "attacker_player_id": req.attacker_player_id,

        "target_token_id": req.target_token_id or "",
        "target_name": req.target_name or "",
        "target_character_id": req.target_character_id or "",

        "weapon_id": req.weapon_id or "",
        "weapon_name": req.weapon_name or "",
        "roll_mode": str(req.roll_mode or "normal"),
    }
    return {"ok": True, "replaced": len(to_remove)}


@app.post("/api/campaigns/{campaign_id}/roll_requests")
async def api_register_roll_request(campaign_id: str, req: RollRequestRegistration):
    """
    Register a generic roll request for a specific player/character.
    Intended to be called by the DM tool / engine services (no session auth).
    """
    st = get_state(campaign_id)
    now = time.time()
    _cleanup_expired_roll_requests(st, now)

    request_id = (req.request_id or "").strip() or uuid.uuid4().hex

    expected_sides = int(req.expected_sides)
    if expected_sides <= 1:
        raise HTTPException(status_code=400, detail="expected_sides must be > 1")

    count_min = max(1, int(req.expected_count_min))
    count_max = max(count_min, int(req.expected_count_max))

    adv_mode = (req.adv_mode or "normal").strip().lower()
    if adv_mode in ("adv", "advantage"):
        adv_mode = "advantage"
    elif adv_mode in ("dis", "disadvantage"):
        adv_mode = "disadvantage"
    else:
        adv_mode = "normal"

    ttl = max(10, int(req.ttl_s))
    st["pending_roll_requests"][request_id] = {
        "request_id": request_id,
        "character_id": (req.character_id or "").strip(),
        "player_id": (req.player_id or "").strip(),
        "roll_kind": (req.roll_kind or "").strip(),
        "expected_sides": expected_sides,
        "expected_count_min": count_min,
        "expected_count_max": count_max,
        "adv_mode": adv_mode,
        "dc": int(req.dc) if req.dc is not None else None,
        "label": (req.label or "").strip(),
        "context": req.context or {},
        "created_at": now,
        "expires_at": now + ttl,
    }

    # Player-facing log
    _push_player_log(st, (req.player_id or "").strip(), {
        "ts": now,
        "type": "ROLL_REQUESTED",
        "request_id": request_id,
        "roll_kind": (req.roll_kind or "").strip(),
        "label": (req.label or "").strip(),
        "expected": {"sides": expected_sides, "min": count_min, "max": count_max},
        "adv_mode": adv_mode,
        "dc": int(req.dc) if req.dc is not None else None,
        "context": req.context or {},
    })

    return {"ok": True, "request_id": request_id}


@app.delete("/api/campaigns/{campaign_id}/roll_requests/{request_id}")
async def api_cancel_roll_request(campaign_id: str, request_id: str):
    st = get_state(campaign_id)
    existed = st.get("pending_roll_requests", {}).pop(request_id, None) is not None
    return {"ok": True, "existed": existed}


@app.delete("/api/campaigns/{campaign_id}/pending_attacks/{pending_attack_id}")
async def api_cancel_pending_attack(campaign_id: str, pending_attack_id: str):
    st = get_state(campaign_id)
    existed = st["pending_attacks"].pop(pending_attack_id, None) is not None
    return {"ok": True, "existed": existed}


# Player “mine” views (auth)
@app.get("/api/campaigns/{campaign_id}/pending_attacks/mine")
async def api_pending_attacks_mine(campaign_id: str, sess=Depends(require_session)):
    st = get_state(campaign_id)
    now = time.time()
    _cleanup_expired_pending(st, now)

    player_id = sess["player_id"]
    out = []
    for pa in st["pending_attacks"].values():
        if pa.get("attacker_player_id") != player_id:
            continue
        out.append({
            "pending_attack_id": pa.get("pending_attack_id", ""),
            "encounter_id": pa.get("encounter_id", ""),
            "created_at": int(pa.get("created_at", int(now))),
            "expires_at": int(float(pa.get("expires_at", now))),
            "ttl_seconds": int(pa.get("ttl_seconds", 90)),
            "damage_expr": pa.get("damage_expr", ""),
            "attacker": {
                "token_id": pa.get("attacker_token_id", ""),
                "name": pa.get("attacker_name", ""),
                "character_id": pa.get("attacker_character_id", "") or None,
            },
            "target": {
                "token_id": pa.get("target_token_id", ""),
                "name": pa.get("target_name", ""),
                "character_id": pa.get("target_character_id", "") or None,
            },
            "weapon": {
                "weapon_id": pa.get("weapon_id", ""),
                "name": pa.get("weapon_name", "") or pa.get("weapon_id", ""),
            },
            "roll_mode": pa.get("roll_mode", "normal"),
        })
    return {"player_id": player_id, "server_time": int(now), "pending_attacks": out}


@app.get("/api/campaigns/{campaign_id}/roll_requests/mine")
async def api_roll_requests_mine(campaign_id: str, sess=Depends(require_session)):
    st = get_state(campaign_id)
    now = time.time()
    _cleanup_expired_roll_requests(st, now)

    player_id = sess["player_id"]
    active_char = sess.get("active_character_id") or ""

    out = []
    for rr in st.get("pending_roll_requests", {}).values():
        if rr.get("player_id") != player_id:
            continue
        # If player has an active character selected, prefer requests for that character.
        if active_char and rr.get("character_id") and rr.get("character_id") != active_char:
            continue
        out.append({
            "request_id": rr.get("request_id", ""),
            "character_id": rr.get("character_id", ""),
            "roll_kind": rr.get("roll_kind", ""),
            "expected_sides": rr.get("expected_sides", 20),
            "expected_count_min": rr.get("expected_count_min", 1),
            "expected_count_max": rr.get("expected_count_max", 1),
            "adv_mode": rr.get("adv_mode", "normal"),
            "dc": rr.get("dc", None),
            "label": rr.get("label", ""),
            "context": rr.get("context", {}),
            "expires_at": rr.get("expires_at", 0),
        })

    # deterministic ordering: soonest expiry first
    out.sort(key=lambda x: float(x.get("expires_at", 0)))
    return {"roll_requests": out}


@app.post("/api/campaigns/{campaign_id}/roll_requests/submit")
async def api_roll_requests_submit(campaign_id: str, req: RollRequestSubmit, sess=Depends(require_session)):
    st = get_state(campaign_id)
    now = time.time()
    _cleanup_expired_roll_requests(st, now)

    player_id = sess["player_id"]
    request_id = (req.request_id or "").strip()
    rr = st.get("pending_roll_requests", {}).get(request_id)
    if not rr:
        raise HTTPException(status_code=404, detail="Roll request not found or expired")

    if rr.get("player_id") != player_id:
        raise HTTPException(status_code=403, detail="Roll request does not belong to this player")

    # Validate dice shape
    expected_sides = int(rr.get("expected_sides", 20))
    if int(req.die_sides) != expected_sides:
        raise HTTPException(status_code=400, detail=f"Expected d{expected_sides}")

    rolls = list(req.rolls or [])
    if not rolls:
        raise HTTPException(status_code=400, detail="Missing rolls")

    cmin = int(rr.get("expected_count_min", 1))
    cmax = int(rr.get("expected_count_max", cmin))
    if not (cmin <= len(rolls) <= cmax):
        raise HTTPException(status_code=400, detail=f"Expected {cmin}-{cmax} roll(s)")

    for r in rolls:
        if not (1 <= int(r) <= expected_sides):
            raise HTTPException(status_code=400, detail=f"Invalid roll value: {r}")

    # Resolve advantage/disadvantage selection deterministically if not supplied
    mode_raw = (req.mode or "").strip().lower()
    if mode_raw in ("adv", "advantage"):
        mode = "advantage"
    elif mode_raw in ("dis", "disadvantage"):
        mode = "disadvantage"
    elif mode_raw == "normal":
        mode = "normal"
    else:
        mode = str(rr.get("adv_mode", "normal") or "normal")

    chosen = req.chosen
    if chosen is None:
        if len(rolls) > 1 and mode == "advantage":
            chosen = max(int(r) for r in rolls)
        elif len(rolls) > 1 and mode == "disadvantage":
            chosen = min(int(r) for r in rolls)
        else:
            chosen = int(rolls[0])

    if int(chosen) not in [int(r) for r in rolls]:
        raise HTTPException(status_code=400, detail="Chosen must be one of the rolls")

    if str(rr.get("roll_kind") or "") == "short_rest_hit_dice":
        char_id = str(rr.get("character_id") or "").strip()
        sheet = load_character_sheet(campaign_id, char_id)
        if not sheet:
            raise HTTPException(status_code=404, detail="Character sheet not found")
        sheet = ensure_sheet_minimum(sheet, char_id)
        if (sheet.get("player_id") or "").strip() != player_id:
            raise HTTPException(status_code=403, detail="Character does not belong to this player")

        rest_state = _get_rest_state(campaign_id)
        if not rest_state or str(rest_state.get("type") or "") != "short_rest" or str(rest_state.get("status") or "") != "active":
            raise HTTPException(status_code=409, detail="Short rest is not active")
        participants = rest_state.get("participants") if isinstance(rest_state.get("participants"), dict) else {}
        pdata = participants.get(char_id) if isinstance(participants.get(char_id), dict) else None
        if pdata is None:
            raise HTTPException(status_code=403, detail="Active character is not part of this short rest")
        if bool(pdata.get("done", False)):
            raise HTTPException(status_code=409, detail="Character is already marked done with short rest")

        stats = sheet.setdefault("stats", {}) if isinstance(sheet.get("stats"), dict) else {}
        combat = sheet.setdefault("combat", {}) if isinstance(sheet.get("combat"), dict) else {}
        resources = sheet.setdefault("resources", {}) if isinstance(sheet.get("resources"), dict) else {}
        abilities = sheet.setdefault("abilities", {}) if isinstance(sheet.get("abilities"), dict) else {}
        _sync_sheet_derived_resources(sheet)
        total = max(0, int(combat.get("hit_dice_total", 0) or 0))
        used = max(0, int(combat.get("hit_dice_used", 0) or 0))
        remaining = max(0, total - used)
        spend = max(1, int((rr.get("context") or {}).get("spend_hit_dice", len(rolls)) or len(rolls)))
        spend = min(spend, remaining)
        if spend <= 0:
            raise HTTPException(status_code=409, detail="No hit dice remaining")
        if len(rolls) != spend:
            raise HTTPException(status_code=400, detail=f"Expected exactly {spend} hit-die roll(s)")
        hit_die_sides = max(1, int(combat.get("hit_die_sides", 8) or 8))
        if expected_sides != hit_die_sides:
            raise HTTPException(status_code=400, detail=f"Expected d{hit_die_sides}")
        con_mod = (int(abilities.get("con", 10) or 10) - 10) // 2
        max_hp = max(1, int(stats.get("max_hp", 1) or 1))
        old_hp = int(stats.get("current_hp", max_hp) or max_hp)
        healed_raw = sum(max(0, int(r) + con_mod) for r in rolls)
        new_hp = min(max_hp, old_hp + healed_raw)
        healed = max(0, new_hp - old_hp)
        stats["current_hp"] = new_hp
        resources["current_hp"] = new_hp
        combat["hit_dice_used"] = min(total, used + spend)
        _sync_sheet_derived_resources(sheet)
        sheet = _recompute_sheet_derived_state(campaign_id, char_id, sheet)
        save_character_sheet(campaign_id, char_id, sheet)

        result = {
            "ts": now,
            "type": "ROLL_SUBMITTED",
            "request_id": request_id,
            "character_id": char_id,
            "roll_kind": "short_rest_hit_dice",
            "label": rr.get("label", ""),
            "dc": None,
            "die_sides": expected_sides,
            "rolls": [int(r) for r in rolls],
            "mode": "normal",
            "chosen": int(chosen),
            "extras": {
                "spent_hit_dice": spend,
                "healed": healed,
                "current_hp": new_hp,
                "con_mod": con_mod,
            },
            "context": rr.get("context", {}),
        }

        st["pending_roll_requests"].pop(request_id, None)
        rrbuf = st.setdefault("roll_request_results", {}).setdefault(player_id, [])
        rrbuf.append(result)
        if len(rrbuf) > 200:
            del rrbuf[:-200]
        _push_player_log(st, player_id, result)
        return {"ok": True, "result": result, "sheet": sheet, "rest_state": _serialize_rest_state_for_character(campaign_id, char_id)}

    if str(rr.get("roll_kind") or "") == "levelup_hit_die":
        char_id = str(rr.get("character_id") or "").strip()
        sheet = load_character_sheet(campaign_id, char_id)
        if not sheet:
            raise HTTPException(status_code=404, detail="Character sheet not found")
        sheet = ensure_sheet_minimum(sheet, char_id)
        if (sheet.get("player_id") or "").strip() != player_id:
            raise HTTPException(status_code=403, detail="Character does not belong to this player")

        cid = _safe_campaign_id(campaign_id)
        state = _levelup_states.get(cid, {})
        spec = state.get(char_id) if isinstance(state, dict) else None
        if not isinstance(spec, dict):
            raise HTTPException(status_code=409, detail="No pending level up for this character")
        if str(spec.get("hp_roll_request_id") or "").strip() != request_id:
            raise HTTPException(status_code=409, detail="Level-up HP roll request mismatch")

        pending_msgs = _levelup_pending_requirements(campaign_id, char_id, sheet, spec)
        if pending_msgs:
            raise HTTPException(status_code=409, detail="; ".join(pending_msgs))

        abilities = sheet.setdefault("abilities", {}) if isinstance(sheet.get("abilities"), dict) else {}
        con_mod = max(-5, min(10, (int(abilities.get("con", 10) or 10) - 10) // 2))
        rolled = int(rolls[0])
        hp_gain = max(1, rolled + con_mod)
        sheet = _finalize_levelup_apply(campaign_id, char_id, sheet, spec, hp_gain)

        result = {
            "ts": now,
            "type": "ROLL_SUBMITTED",
            "request_id": request_id,
            "character_id": char_id,
            "roll_kind": "levelup_hit_die",
            "label": rr.get("label", ""),
            "dc": None,
            "die_sides": expected_sides,
            "rolls": [int(r) for r in rolls],
            "mode": "normal",
            "chosen": int(chosen),
            "extras": {
                "hp_gain": hp_gain,
                "con_mod": con_mod,
                "new_level": int(((sheet.get("meta") or {}) if isinstance(sheet.get("meta"), dict) else {}).get("level", 1)),
                "current_hp": int(((sheet.get("stats") or {}) if isinstance(sheet.get("stats"), dict) else {}).get("current_hp", 1)),
                "max_hp": int(((sheet.get("stats") or {}) if isinstance(sheet.get("stats"), dict) else {}).get("max_hp", 1)),
            },
            "context": rr.get("context", {}),
        }

        st["pending_roll_requests"].pop(request_id, None)
        spec.pop("hp_roll_request_id", None)
        state.pop(char_id, None)
        _levelup_states[cid] = state
        rrbuf = st.setdefault("roll_request_results", {}).setdefault(player_id, [])
        rrbuf.append(result)
        if len(rrbuf) > 200:
            del rrbuf[:-200]
        _push_player_log(st, player_id, result)
        return {"ok": True, "result": result, "sheet": sheet, "levelup_state": {"active": False}}

    if str(rr.get("roll_kind") or "") == "check":
        try:
            char_id = str(rr.get("character_id") or "").strip()
            if char_id:
                sheet = load_character_sheet(campaign_id, char_id)
                if sheet:
                    sheet = ensure_sheet_minimum(sheet, char_id)
                    combat = sheet.get("combat") if isinstance(sheet.get("combat"), dict) else {}
                    if bool(combat.get("reliable_talent", False)):
                        ctx = rr.get("context") if isinstance(rr.get("context"), dict) else {}
                        proficient = bool(ctx.get("proficient", False))
                        if not proficient:
                            skill_key = str(ctx.get("skill_key", "") or ctx.get("skill", "")).strip().lower()
                            prof = sheet.get("proficiencies") if isinstance(sheet.get("proficiencies"), dict) else {}
                            skills = prof.get("skills") if isinstance(prof.get("skills"), dict) else {}
                            proficient = bool(skills.get(skill_key)) if skill_key else False
                        if proficient:
                            chosen = max(int(chosen), 10)
        except Exception:
            pass

    # Stroke of Luck: if armed, the next attack roll or ability check can be treated as a 20.
    if str(rr.get("roll_kind") or "") in {"check", "attack_to_hit"}:
        try:
            char_id = str(rr.get("character_id") or "").strip()
            if char_id:
                sheet = load_character_sheet(campaign_id, char_id)
                if sheet:
                    sheet = ensure_sheet_minimum(sheet, char_id)
                    combat = sheet.setdefault("combat", {}) if isinstance(sheet.get("combat"), dict) else {}
                    resource_pools = sheet.setdefault("resource_pools", {}) if isinstance(sheet.get("resource_pools"), dict) else {}
                    if bool(combat.get("stroke_of_luck_armed", False)) and int(chosen) < 20:
                        pool = resource_pools.setdefault("stroke_of_luck", {"current": 1, "max": 1, "refresh": "short_rest"})
                        cur = int(pool.get("current", 0) or 0)
                        if cur > 0:
                            chosen = 20
                            pool["current"] = max(0, cur - 1)
                            combat["stroke_of_luck_armed"] = False
                            save_character_sheet(campaign_id, char_id, sheet)
                            extra_payload = req.extras or {}
                            if isinstance(extra_payload, dict):
                                extra_payload["stroke_of_luck_used"] = True
                                req.extras = extra_payload
        except Exception:
            pass

    result = {
        "ts": now,
        "type": "ROLL_SUBMITTED",
        "request_id": request_id,
        "character_id": rr.get("character_id", ""),
        "roll_kind": rr.get("roll_kind", ""),
        "label": rr.get("label", ""),
        "dc": rr.get("dc", None),
        "die_sides": expected_sides,
        "rolls": [int(r) for r in rolls],
        "mode": mode,
        "chosen": int(chosen),
        "extras": req.extras or {},
        "context": rr.get("context", {}),
    }

    # Remove request (mark as resolved)
    st["pending_roll_requests"].pop(request_id, None)

    # Store results per player
    rrbuf = st.setdefault("roll_request_results", {}).setdefault(player_id, [])
    rrbuf.append(result)
    if len(rrbuf) > 200:
        del rrbuf[:-200]

    # DM-facing queue for deterministic engine resolution
    dm_buf = st.setdefault("dm_roll_request_results", [])
    dm_buf.append(result)
    if len(dm_buf) > 500:
        del dm_buf[:-500]

    _push_player_log(st, player_id, result)

    return {"ok": True, "result": result}


@app.get("/api/campaigns/{campaign_id}/next_roll_request_results")
async def api_next_roll_request_results(campaign_id: str):
    st = get_state(campaign_id)
    out = list(st.get("dm_roll_request_results", []) or [])
    st["dm_roll_request_results"] = []
    return out


@app.get("/api/campaigns/{campaign_id}/logs/mine")
async def api_logs_mine(campaign_id: str, limit: int = 200, sess=Depends(require_session)):
    st = get_state(campaign_id)
    player_id = sess["player_id"]
    lim = max(1, min(500, int(limit)))
    buf = st.get("player_logs", {}).get(player_id, []) or []
    return {"logs": buf[-lim:]}


# ------------------------------------------------------------
# Rolls: player submits d20 (auth, server derives player_id)
# DM polls next_rolls (no auth)
# ------------------------------------------------------------
class RollSubmissionV1(BaseModel):
    pending_attack_id: str
    mode: str = "normal"  # normal | advantage | disadvantage (also adv/dis)
    rolls: List[int] = Field(default_factory=list)
    attacker_character_id: str = ""  # optional client hint; server can also use session selection
    damage_roll: Dict[str, Any] = Field(default_factory=dict)  # unused in Option B for to-hit

@app.post("/api/campaigns/{campaign_id}/rolls")
async def api_receive_rolls_v1(campaign_id: str, sub: RollSubmissionV1, sess=Depends(require_session)):
    st = get_state(campaign_id)
    pending_attacks = st["pending_attacks"]
    roll_queue = st["roll_queue"]

    pa = pending_attacks.get(sub.pending_attack_id)
    if not pa:
        return JSONResponse({"status": "rejected", "reason": "pending_attack_not_found_or_expired"}, status_code=400)

    now = time.time()
    if now > float(pa.get("expires_at", 0)):
        pending_attacks.pop(sub.pending_attack_id, None)
        return JSONResponse({"status": "rejected", "reason": "pending_attack_not_found_or_expired"}, status_code=400)

    player_id = sess["player_id"]
    if player_id != pa.get("attacker_player_id"):
        return JSONResponse({"status": "rejected", "reason": "player_id_mismatch"}, status_code=403)

    mode_raw = (sub.mode or "normal").strip().lower()
    mode = {"adv": "advantage", "dis": "disadvantage"}.get(mode_raw, mode_raw)
    if mode not in ("normal", "advantage", "disadvantage"):
        return JSONResponse({"status": "rejected", "reason": "invalid_roll_format"}, status_code=400)

    rolls = sub.rolls or []
    if mode == "normal":
        if len(rolls) != 1:
            return JSONResponse({"status": "rejected", "reason": "invalid_roll_format"}, status_code=400)
    else:
        if len(rolls) != 2:
            return JSONResponse({"status": "rejected", "reason": "invalid_roll_format"}, status_code=400)

    # Prefer session-selected character_id (Phase 1)
    sess_char = (sess.get("active_character_id") or "").strip()
    attacker_char = sess_char or (sub.attacker_character_id or pa.get("attacker_character_id", "") or "")

    queued = {
        "pending_attack_id": sub.pending_attack_id,
        "player_id": player_id,
        "mode": {"advantage": "adv", "disadvantage": "dis"}.get(mode, mode),
        "roll": rolls[0] if rolls else None,
        "rolls": rolls if len(rolls) > 1 else None,
        "damage_roll": {},

        "attacker_token_id": pa.get("attacker_token_id", ""),
        "attacker_character_id": attacker_char,
        "encounter_id": pa.get("encounter_id", ""),
        "received_at": now,
    }
    roll_queue.append(queued)
    pending_attacks.pop(sub.pending_attack_id, None)
    return {"status": "accepted", "pending_attack_id": sub.pending_attack_id, "received_at": int(now)}

@app.get("/api/campaigns/{campaign_id}/next_rolls")
async def api_next_rolls(campaign_id: str):
    st = get_state(campaign_id)
    out = list(st["roll_queue"])
    st["roll_queue"].clear()
    return JSONResponse(out)

# ------------------------------------------------------------
# Damage rolls: player submits damage (auth)
# DM polls next_damage_rolls (no auth)
# ------------------------------------------------------------
class DamageRollSubmission(BaseModel):
    attack_id: str
    damage_roll: Dict[str, Any] = Field(default_factory=dict)

@app.post("/api/campaigns/{campaign_id}/damage_rolls")
async def api_receive_damage_roll(campaign_id: str, sub: DamageRollSubmission, sess=Depends(require_session)):
    st = get_state(campaign_id)
    if not sub.attack_id:
        return JSONResponse({"status": "rejected", "reason": "missing_attack_id"}, status_code=400)

    player_id = sess["player_id"]
    st["damage_roll_queue"].append({
        "attack_id": sub.attack_id,
        "player_id": player_id,
        "damage_roll": sub.damage_roll or {},
        "received_at": time.time(),
    })
    return {"status": "accepted", "attack_id": sub.attack_id}

@app.get("/api/campaigns/{campaign_id}/next_damage_rolls")
async def api_next_damage_rolls(campaign_id: str):
    st = get_state(campaign_id)
    out = list(st["damage_roll_queue"])
    st["damage_roll_queue"].clear()
    return JSONResponse(out)

# ------------------------------------------------------------
# Attack results (DM -> Player) + Messages (DM -> Player)
# Campaign-scoped
# ------------------------------------------------------------
class AttackResultSubmission(BaseModel):
    attack_id: str
    player_id: str  # who should see it

    encounter_id: str = ""
    attacker_token_id: str = ""
    attacker_name: str = ""
    target_token_id: str = ""
    target_name: str = ""

    roll: int = 0
    total: int = 0
    ac: int = 0
    result: str = "MISS"
    nat20: bool = False
    nat1: bool = False

    damage: int = 0
    target_hp: int = 0
    target_max_hp: int = 0

    ttl_seconds: int = 120

def _cleanup_expired_results(st: Dict[str, Any], now: float) -> None:
    ar = st["attack_results"]
    for pid in list(ar.keys()):
        kept = []
        for r in ar.get(pid, []):
            if float(r.get("expires_at", 0)) > now:
                kept.append(r)
        if kept:
            ar[pid] = kept
        else:
            ar.pop(pid, None)

@app.post("/api/campaigns/{campaign_id}/attack_results")
async def api_post_attack_result(campaign_id: str, res: AttackResultSubmission):
    st = get_state(campaign_id)
    now = time.time()
    ttl = int(res.ttl_seconds or 120)
    item = res.dict()
    item["created_at"] = int(now)
    item["expires_at"] = float(now + float(ttl))
    item["ttl_seconds"] = ttl
    st["attack_results"].setdefault(res.player_id, []).append(item)
    return {"ok": True, "queued": len(st["attack_results"].get(res.player_id, []))}

@app.get("/api/campaigns/{campaign_id}/attack_results/mine")
async def api_attack_results_mine(campaign_id: str, sess=Depends(require_session)):
    st = get_state(campaign_id)
    now = time.time()
    _cleanup_expired_results(st, now)
    pid = sess["player_id"]
    out = st["attack_results"].pop(pid, [])
    return {"player_id": pid, "server_time": int(now), "results": out}

class PlayerMessageSubmission(BaseModel):
    player_id: str
    kind: str = "info"  # info|ok|warn|err
    text: str = ""
    data: Dict[str, Any] = Field(default_factory=dict)
    ttl_seconds: int = 120



def _cleanup_expired_messages(st: Dict[str, Any], now: float) -> None:
    pm = st["player_messages"]
    for pid in list(pm.keys()):
        kept = []
        for m in pm.get(pid, []):
            if float(m.get("expires_at", 0)) > now:
                kept.append(m)
        if kept:
            pm[pid] = kept
        else:
            pm.pop(pid, None)

@app.post("/api/campaigns/{campaign_id}/messages")
async def api_post_player_message(campaign_id: str, msg: PlayerMessageSubmission):
    st = get_state(campaign_id)
    now = time.time()
    ttl = int(msg.ttl_seconds or 120)
    item = msg.dict()
    item["message_id"] = uuid.uuid4().hex
    item["created_at"] = int(now)
    item["expires_at"] = float(now + float(ttl))
    item["ttl_seconds"] = ttl
    st["player_messages"].setdefault(msg.player_id, []).append(item)
    return {"ok": True, "queued": len(st["player_messages"].get(msg.player_id, []))}

@app.get("/api/campaigns/{campaign_id}/messages/mine")
async def api_messages_mine(campaign_id: str, sess=Depends(require_session)):
    st = get_state(campaign_id)
    now = time.time()
    _cleanup_expired_messages(st, now)
    pid = sess["player_id"]
    out = st["player_messages"].pop(pid, [])
    return {"player_id": pid, "server_time": int(now), "messages": out}


# ------------------------------------------------------------
# Handouts / Readables (DM push -> Player portal)
# ------------------------------------------------------------
class HandoutPush(BaseModel):
    player_id: str = Field(..., description="Target player_id")
    character_id: str = Field("", description="Optional target character_id")
    title: str = ""
    kind: str = "handout"  # handout|readable|image
    text: str = ""
    image_url: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)


class HandoutTemplate(BaseModel):
    template_id: str = Field("", description="Optional; server will generate if empty")
    title: str = ""
    kind: str = "handout"  # handout|readable|image
    text: str = ""
    image_url: str = ""
    payload: Dict[str, Any] = Field(default_factory=dict)


def _save_handout_templates(st: Dict[str, Any]) -> None:
    try:
        _write_json(st["handout_templates_path"], st.get("handout_templates", []) or [])
    except Exception:
        pass


@app.get("/api/campaigns/{campaign_id}/players")
async def api_players_index(campaign_id: str):
    """DM convenience: list players from pins.json and their characters."""
    st = get_state(campaign_id)
    pins = _read_json(st["pins_path"], {})
    if not isinstance(pins, dict):
        pins = {}

    # Scan characters and bucket by player_id
    by_player: Dict[str, List[Dict[str, Any]]] = {str(pid): [] for pid in pins.keys()}
    try:
        for fn in os.listdir(st["char_dir"]):
            if not fn.lower().endswith(".json"):
                continue
            cid = fn[:-5]
            sheet = load_character_sheet(campaign_id, cid)
            if not isinstance(sheet, dict):
                continue
            sheet = ensure_sheet_minimum(sheet, cid)
            pid = (sheet.get("player_id") or "").strip()
            if not pid:
                continue
            by_player.setdefault(pid, []).append({
                "character_id": cid,
                "display_name": sheet.get("display_name", cid),
            })
    except Exception:
        pass

    players_out = []
    for pid in sorted(by_player.keys(), key=lambda x: x.lower()):
        chars = by_player.get(pid, []) or []
        chars.sort(key=lambda x: (x.get("display_name") or x.get("character_id") or "").lower())
        players_out.append({"player_id": pid, "characters": chars})
    return {"players": players_out}


@app.get("/api/campaigns/{campaign_id}/handout_templates")
async def api_get_handout_templates(campaign_id: str):
    st = get_state(campaign_id)
    tpls = st.get("handout_templates", []) or []
    # newest first by updated_at if present
    def _k(x):
        return int(x.get("updated_at", 0))
    tpls = list(tpls)
    tpls.sort(key=_k, reverse=True)
    return {"templates": tpls}


@app.post("/api/campaigns/{campaign_id}/handout_templates")
async def api_upsert_handout_template(campaign_id: str, tpl: HandoutTemplate):
    st = get_state(campaign_id)
    now = int(time.time())
    template_id = (tpl.template_id or "").strip() or uuid.uuid4().hex
    item = {
        "template_id": template_id,
        "title": tpl.title or "Untitled Handout",
        "kind": (tpl.kind or "handout").strip(),
        "text": tpl.text or "",
        "image_url": tpl.image_url or "",
        "payload": tpl.payload or {},
        "updated_at": now,
    }
    buf = st.setdefault("handout_templates", [])
    replaced = False
    for i, existing in enumerate(list(buf)):
        if str(existing.get("template_id", "")) == template_id:
            buf[i] = item
            replaced = True
            break
    if not replaced:
        buf.append(item)
    # cap
    if len(buf) > 500:
        del buf[:-500]
    _save_handout_templates(st)
    return {"ok": True, "template_id": template_id}


@app.delete("/api/campaigns/{campaign_id}/handout_templates/{template_id}")
async def api_delete_handout_template(campaign_id: str, template_id: str):
    st = get_state(campaign_id)
    tid = (template_id or "").strip()
    if not tid:
        raise HTTPException(status_code=400, detail="missing template_id")
    buf = st.setdefault("handout_templates", [])
    before = len(buf)
    buf[:] = [x for x in buf if str(x.get("template_id", "")) != tid]
    _save_handout_templates(st)
    return {"ok": True, "deleted": (len(buf) != before)}

def _save_handouts(st: Dict[str, Any]) -> None:
    try:
        _write_json(st["handouts_path"], st.get("handouts", []) or [])
    except Exception:
        pass

@app.post("/api/campaigns/{campaign_id}/handouts")
async def api_push_handout(campaign_id: str, h: HandoutPush):
    """DM-facing endpoint: push a handout to a player."""
    st = get_state(campaign_id)
    now = int(time.time())
    item = {
        "handout_id": uuid.uuid4().hex,
        "player_id": (h.player_id or "").strip(),
        "character_id": (h.character_id or "").strip(),
        "title": h.title or "Handout",
        "kind": (h.kind or "handout").strip(),
        "text": h.text or "",
        "image_url": h.image_url or "",
        "payload": h.payload or {},
        "created_at": now,
        "read": False,
    }
    st.setdefault("handouts", []).append(item)
    # cap
    if len(st["handouts"]) > 500:
        st["handouts"] = st["handouts"][-500:]
    _save_handouts(st)
    # also push to player activity log
    try:
        _push_player_log(st, item["player_id"], {
            "ts": now,
            "type": "HANDOUT_PUSHED",
            "handout_id": item["handout_id"],
            "title": item["title"],
            "kind": item["kind"],
        })
    except Exception:
        pass
    return {"ok": True, "handout_id": item["handout_id"]}

def _get_character_language_map(sheet: Dict[str, Any]) -> Dict[str, Dict[str, bool]]:
    """Return map: language(lower) -> {speak, read, write}."""
    out: Dict[str, Dict[str, bool]] = {}
    try:
        prof = sheet.get("proficiencies", {}) if isinstance(sheet.get("proficiencies", {}), dict) else {}
        langs = prof.get("languages", []) if isinstance(prof.get("languages", []), list) else []
        for e in langs:
            if not isinstance(e, dict):
                continue
            name = str(e.get("name", "") or "").strip()
            if not name:
                continue
            out[name.lower()] = {
                "speak": bool(e.get("speak", True)),
                "read": bool(e.get("read", True)),
                "write": bool(e.get("write", True)),
            }
    except Exception:
        pass
    return out


def _scramble_text(text: str, seed: str) -> str:
    """Deterministically scramble letters in `text` using a per-handout seed.
    Keeps whitespace and punctuation; scrambles a-z/A-Z. Preserves case.
    """
    import random
    rnd = random.Random()
    rnd.seed(seed or "seed")
    alpha = "abcdefghijklmnopqrstuvwxyz"
    mapping = {c: alpha[rnd.randrange(26)] for c in alpha}
    mapping_u = {c.upper(): mapping[c].upper() for c in alpha}
    out_chars = []
    for ch in str(text or ""):
        if ch in mapping:
            out_chars.append(mapping[ch])
        elif ch in mapping_u:
            out_chars.append(mapping_u[ch])
        else:
            out_chars.append(ch)
    return "".join(out_chars)


@app.get("/api/campaigns/{campaign_id}/handouts/mine")
async def api_handouts_mine(campaign_id: str, sess=Depends(require_session)):
    st = get_state(campaign_id)
    pid = sess["player_id"]
    active_char = (sess.get("active_character_id") or "").strip()

    # Load active character sheet for language gating (best-effort).
    langmap: Dict[str, Dict[str, bool]] = {}
    if active_char:
        try:
            sheet = load_character_sheet(campaign_id, active_char)
            sheet = ensure_sheet_minimum(sheet, active_char)
            langmap = _get_character_language_map(sheet)
        except Exception:
            langmap = {}

    out = []
    for h in st.get("handouts", []) or []:
        if h.get("player_id") != pid:
            continue

        # if character_id set, require match (prefer active character)
        hid_char = (h.get("character_id") or "").strip()
        if hid_char:
            if active_char and hid_char != active_char:
                continue

        # Language gating: do not leak readable text if player cannot read the language.
        item = dict(h) if isinstance(h, dict) else {}
        payload = item.get("payload", {}) if isinstance(item.get("payload", {}), dict) else {}

        lang = str(payload.get("language") or payload.get("lang") or "").strip()
        if lang:
            perms = langmap.get(lang.lower(), {})
            can_read = bool(perms.get("read", False))
            mode = str(payload.get("unreadable_mode") or "blocked").strip().lower()
            if mode not in ("blocked", "scramble"):
                mode = "blocked"

            if not can_read:
                # Remove plaintext from payload (if any) and set visible body.
                raw_text = str(item.get("text", "") or payload.get("text") or payload.get("body") or "")
                if mode == "scramble":
                    item["text"] = _scramble_text(raw_text, seed=f"{item.get('handout_id','')}-{pid}-{lang}")
                    item["payload"] = dict(payload)
                    item["payload"].pop("text", None)
                    item["payload"].pop("body", None)
                else:
                    item["text"] = ""
                    item["payload"] = dict(payload)
                    item["payload"].pop("text", None)
                    item["payload"].pop("body", None)

                item["unreadable"] = True
                item["unreadable_mode"] = mode
                item["language"] = lang

        out.append(item)

    # newest first
    out.sort(key=lambda x: int(x.get("created_at", 0)), reverse=True)
    return {"handouts": out}

class HandoutRead(BaseModel):
    handout_id: str

class HandoutShareRequest(BaseModel):
    handout_id: str
    to_character_id: str = ""
    to_player_id: str = ""
    to_party: bool = False

@app.post("/api/campaigns/{campaign_id}/handouts/mine/share")
async def api_share_handout_mine(campaign_id: str, req: HandoutShareRequest, sess=Depends(require_session)):
    """Player-facing: share a handout you possess to another PC (or party)."""
    st = get_state(campaign_id)
    hid = (req.handout_id or "").strip()
    if not hid:
        raise HTTPException(status_code=400, detail="handout_id required")

    handouts = st.setdefault("handouts", [])
    src = None
    for h in handouts:
        if h.get("handout_id") == hid and (h.get("player_id") or "").strip() == sess["player_id"]:
            src = h
            break
    if not src:
        raise HTTPException(status_code=404, detail="Handout not found for player")

    recipients: List[Tuple[str, str]] = []
    if bool(req.to_party):
        # all PCs in campaign
        if os.path.isdir(st["char_dir"]):
            for fn in os.listdir(st["char_dir"]):
                if not fn.lower().endswith(".json"):
                    continue
                cid = fn[:-5]
                sh = load_character_sheet(campaign_id, cid)
                if not sh:
                    continue
                sh = ensure_sheet_minimum(sh, cid)
                pid = (sh.get("player_id") or "").strip()
                if not pid:
                    continue
                recipients.append((pid, cid))
    else:
        to_char = (req.to_character_id or "").strip()
        to_player = (req.to_player_id or "").strip()
        if to_char:
            sh = load_character_sheet(campaign_id, to_char)
            if not sh:
                raise HTTPException(status_code=404, detail="Target character not found")
            sh = ensure_sheet_minimum(sh, to_char)
            to_player = (sh.get("player_id") or "").strip()
        if not to_player:
            raise HTTPException(status_code=400, detail="Recipient required")
        recipients = [(to_player, to_char)]

    seen=set()
    cleaned=[]
    for pid,cid in recipients:
        pid=(pid or "").strip()
        cid=(cid or "").strip()
        if not pid:
            continue
        key=(pid,cid)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append((pid,cid))

    now = int(time.time())
    pushed=0
    for pid,cid in cleaned:
        item=dict(src)
        item["handout_id"]=uuid.uuid4().hex
        item["player_id"]=pid
        item["character_id"]=cid
        item["created_at"]=now
        item["read"]=False
        handouts.append(item)
        pushed += 1
        try:
            _push_player_log(st, pid, {"ts": time.time(), "type": "HANDOUT_RECEIVED", "handout_id": item["handout_id"], "title": item.get("title","Handout")})
        except Exception:
            pass

    if len(handouts) > 500:
        st["handouts"] = handouts[-500:]
    _save_handouts(st)
    return {"ok": True, "shared": pushed}


@app.post("/api/campaigns/{campaign_id}/handouts/read")
async def api_mark_handout_read(campaign_id: str, req: HandoutRead, sess=Depends(require_session)):
    st = get_state(campaign_id)
    pid = sess["player_id"]
    hid = (req.handout_id or "").strip()
    updated = False
    for h in st.get("handouts", []) or []:
        if h.get("handout_id") == hid and h.get("player_id") == pid:
            h["read"] = True
            updated = True
            break
    if updated:
        _save_handouts(st)
    return {"ok": True, "updated": updated}

# ------------------------------------------------------------
# Character sheet read/write (DM + future player editing)
# DM uses these already; keep campaign scoped.
# ------------------------------------------------------------
@app.get("/api/campaigns/{campaign_id}/characters/{character_id}")
async def api_get_character(campaign_id: str, character_id: str):
    sheet = load_character_sheet(campaign_id, character_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = _recompute_sheet_derived_state(campaign_id, character_id, sheet)
    return JSONResponse(sheet)

@app.post("/api/campaigns/{campaign_id}/characters/{character_id}")
async def api_upsert_character(campaign_id: str, character_id: str, payload: Dict[str, Any]):
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Payload must be a JSON object")
    existing = load_character_sheet(campaign_id, character_id)
    if existing:
        merged = dict(existing)
        merged.update(payload)
        merged.setdefault("base_stats", {})
        merged.setdefault("resources", {})
        if isinstance(existing.get("base_stats"), dict) and isinstance(payload.get("base_stats"), dict):
            bs = dict(existing["base_stats"]); bs.update(payload["base_stats"]); merged["base_stats"] = bs
        if isinstance(existing.get("resources"), dict) and isinstance(payload.get("resources"), dict):
            rs = dict(existing["resources"]); rs.update(payload["resources"]); merged["resources"] = rs
        sheet = ensure_sheet_minimum(merged, character_id)
    else:
        sheet = ensure_sheet_minimum(payload, character_id)

    save_character_sheet(campaign_id, character_id, sheet)
    return {"ok": True, "character_id": character_id, "updated_at": sheet.get("updated_at", 0)}

class DamageRequest(BaseModel):
    amount: int = Field(ge=0)
    source: str = "attack"
    encounter_id: str = ""
    token_id: str = ""
    pending_attack_id: str = ""

@app.post("/api/campaigns/{campaign_id}/characters/{character_id}/apply_damage")
async def api_apply_damage(campaign_id: str, character_id: str, req: DamageRequest):
    sheet = load_character_sheet(campaign_id, character_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, character_id)

    bs = sheet["base_stats"]
    res = sheet["resources"]

    dmg = int(req.amount)
    temp_hp = int(res.get("temp_hp", 0) or 0)
    cur_hp = int(res.get("current_hp", 0) or 0)
    # Prefer `stats.max_hp` when present (some campaigns keep authoritative values there)
    max_hp = int(sheet.get("stats", {}).get("max_hp", bs.get("max_hp", 10)) or 10)

    remaining = dmg
    if temp_hp > 0 and remaining > 0:
        used = min(temp_hp, remaining)
        temp_hp -= used
        remaining -= used
    if remaining > 0:
        cur_hp = max(0, cur_hp - remaining)

    res["temp_hp"] = temp_hp
    res["current_hp"] = cur_hp

    sheet.setdefault("stats", {})
    sheet["stats"]["current_hp"] = cur_hp

    sheet.setdefault("hp_log", [])
    sheet["hp_log"].append({
        "ts": int(time.time()),
        "delta": -dmg,
        "source": req.source,
        "encounter_id": req.encounter_id,
        "token_id": req.token_id,
        "pending_attack_id": req.pending_attack_id,
        "after": {"current_hp": cur_hp, "temp_hp": temp_hp}
    })

    save_character_sheet(campaign_id, character_id, sheet)
    return {"ok": True, "character_id": character_id, "max_hp": max_hp, "current_hp": cur_hp, "temp_hp": temp_hp}

class UpdateCombatEffectsRequest(BaseModel):
    effects: List[Dict[str, Any]] = Field(default_factory=list)


@app.post("/api/campaigns/{campaign_id}/characters/{character_id}/combat_effects")
async def api_set_combat_effects(campaign_id: str, character_id: str, req: UpdateCombatEffectsRequest):
    sheet = load_character_sheet(campaign_id, character_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, character_id)

    cleaned: List[Dict[str, Any]] = []
    for raw in list(req.effects or []):
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name", "") or raw.get("condition_name", "") or raw.get("title", "")).strip()
        if not name:
            continue
        cleaned.append({
            "effect_id": str(raw.get("effect_id", "") or raw.get("condition_id", "") or uuid.uuid4().hex[:12]),
            "name": name,
            "source": str(raw.get("source", "") or ""),
            "summary": str(raw.get("summary", "") or ""),
            "rounds_remaining": raw.get("rounds_remaining", None),
            "timing": str(raw.get("timing", "") or ""),
            "damage_type": str(raw.get("damage_type", "") or ""),
            "meta": raw.get("meta", {}) if isinstance(raw.get("meta", {}), dict) else {},
        })

    sheet["combat_effects"] = cleaned
    save_character_sheet(campaign_id, character_id, sheet)
    return {"ok": True, "character_id": character_id, "effects": cleaned}


class SetHpRequest(BaseModel):
    current_hp: int
    temp_hp: int = 0
    encounter_id: str = ""
    token_id: str = ""
    reason: str = ""

@app.post("/api/campaigns/{campaign_id}/characters/{character_id}/set_hp")
async def api_set_hp(campaign_id: str, character_id: str, req: SetHpRequest):
    sheet = load_character_sheet(campaign_id, character_id)
    if not sheet:
        raise HTTPException(status_code=404, detail="Character sheet not found")
    sheet = ensure_sheet_minimum(sheet, character_id)

    bs = sheet["base_stats"]
    res = sheet["resources"]

    # Prefer `stats.max_hp` if present
    max_hp = int(sheet.get("stats", {}).get("max_hp", bs.get("max_hp", 10)) or 10)

    cur_hp = int(req.current_hp)
    cur_hp = max(0, min(cur_hp, max_hp))

    temp_hp = int(req.temp_hp)
    temp_hp = max(0, temp_hp)

    # ---- Canonical is stats.* (ensure_sheet_minimum mirrors stats -> resources) ----
    sheet.setdefault("stats", {})
    sheet["stats"]["current_hp"] = cur_hp

    # Keep legacy mirrors in sync too (harmless; ensure_sheet_minimum will re-mirror anyway)
    res["current_hp"] = cur_hp
    res["temp_hp"] = temp_hp

    sheet.setdefault("hp_log", [])
    sheet["hp_log"].append({
        "ts": int(time.time()),
        "delta": 0,
        "source": "set_hp",
        "reason": req.reason,
        "encounter_id": req.encounter_id,
        "token_id": req.token_id,
        "after": {"current_hp": cur_hp, "temp_hp": temp_hp, "max_hp": max_hp},
    })

    save_character_sheet(campaign_id, character_id, sheet)
    return {"ok": True, "character_id": character_id, "max_hp": max_hp, "current_hp": cur_hp, "temp_hp": temp_hp}

# ------------------------------------------------------------
# Compatibility wrappers (optional): old endpoints map to DEFAULT_CAMPAIGN
# Keeps you from bricking old clients while you update DM app.
# ------------------------------------------------------------
@app.get("/items")
async def compat_items():
    return JSONResponse(load_items_db(DEFAULT_CAMPAIGN_ID))

@app.post("/pending_attacks")
async def compat_pending_attacks(req: PendingAttackRegistration):
    return await api_register_pending_attack(DEFAULT_CAMPAIGN_ID, req)

@app.delete("/pending_attacks/{pending_attack_id}")
async def compat_cancel_pending_attack(pending_attack_id: str):
    return await api_cancel_pending_attack(DEFAULT_CAMPAIGN_ID, pending_attack_id)

@app.get("/next_rolls")
async def compat_next_rolls():
    return await api_next_rolls(DEFAULT_CAMPAIGN_ID)

@app.get("/next_damage_rolls")
async def compat_next_damage_rolls():
    return await api_next_damage_rolls(DEFAULT_CAMPAIGN_ID)

@app.post("/attack_results")
async def compat_attack_results(res: AttackResultSubmission):
    return await api_post_attack_result(DEFAULT_CAMPAIGN_ID, res)

@app.post("/reactions/respond")
async def compat_reactions_respond(sub: ReactionResponseSubmission, sess=Depends(require_session)):
    return await api_reaction_respond(DEFAULT_CAMPAIGN_ID, sub, sess)

@app.get("/next_reaction_responses")
async def compat_next_reaction_responses():
    return await api_next_reaction_responses(DEFAULT_CAMPAIGN_ID)

@app.post("/messages")
async def compat_messages(msg: PlayerMessageSubmission):
    return await api_post_player_message(DEFAULT_CAMPAIGN_ID, msg)

@app.get("/characters/{character_id}")
async def compat_get_character(character_id: str):
    return await api_get_character(DEFAULT_CAMPAIGN_ID, character_id)

@app.post("/characters/{character_id}")
async def compat_upsert_character(character_id: str, payload: Dict[str, Any]):
    return await api_upsert_character(DEFAULT_CAMPAIGN_ID, character_id, payload)

@app.post("/characters/{character_id}/apply_damage")
async def compat_apply_damage(character_id: str, req: DamageRequest):
    return await api_apply_damage(DEFAULT_CAMPAIGN_ID, character_id, req)

@app.post("/debug/reset")
async def debug_reset():
    # Reset all campaign buckets + sessions
    _campaign_state.clear()
    _sessions.clear()
    return {"ok": True}

@app.get("/debug/state")
async def debug_state():
    return {
        "campaigns_root": CAMPAIGNS_ROOT,
        "known_campaigns": list(_campaign_state.keys()),
        "sessions": len(_sessions),
        "default_campaign": DEFAULT_CAMPAIGN_ID,
    }
