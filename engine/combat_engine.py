from __future__ import annotations

import random
import re
import os
import json
from typing import Any, Dict, Optional, Tuple, Literal

from ui.encounter_state import EncounterState, TokenState
from net.server_client import ServerClient
from .combat_models import NpcAttackOutcome, SheetCombatView, RollMode
from .damage_engine import normalize_damage_type, resolve_damage
from .condition_semantics import attack_mode_from_conditions, merge_roll_modes
from .trait_engine import apply_passives_to_combat_view
from .spell_engine import load_spells_db as load_normalized_spells_db

_DICE_RE = re.compile(r"^\s*(\d+)\s*d\s*(\d+)\s*([+-]\s*\d+)?\s*$", re.IGNORECASE)

def evaluate_hit(d20_roll: int, attack_mod: int, target_ac: int):
    total = int(d20_roll) + int(attack_mod)
    return (total >= int(target_ac)), total

def roll_dice(expr):
    """
    If expr is int (e.g. 20): returns int (single roll).
    If expr is str (e.g. "1d8", "2d6+3"): returns (total:int, rolls:list[int]).
    """
    if isinstance(expr, int):
        return random.randint(1, expr)

    s = str(expr).strip()
    m = _DICE_RE.match(s)
    if not m:
        # last-resort: try treating it like a plain integer sides count
        try:
            sides = int(s)
            return random.randint(1, sides)
        except Exception as e:
            raise ValueError(f"Unsupported dice expression: {expr!r}") from e

    n = int(m.group(1))
    sides = int(m.group(2))
    mod = 0
    if m.group(3):
        mod = int(m.group(3).replace(" ", ""))

    rolls = [random.randint(1, sides) for _ in range(n)]
    return (sum(rolls) + mod), rolls

def choose_d20(rolls: list[int], mode: RollMode) -> int:
    """rolls may contain 1 or 2 values. If 2 and adv/dis, choose appropriately."""
    if not rolls:
        raise ValueError("No rolls provided")

    if len(rolls) == 1:
        return int(rolls[0])

    r1, r2 = int(rolls[0]), int(rolls[1])
    if mode == "adv":
        return max(r1, r2)
    if mode == "dis":
        return min(r1, r2)
    # normal with two rolls: default to first
    return r1


def resolve_attack(
    d20: int,
    attacker_mod: int,
    target_ac: int,
    weapon_attack_bonus: int = 0,
) -> Tuple[bool, int, bool, bool]:
    """
    Returns: (hit?, total_attack_roll, is_nat20, is_nat1)
    D&D5e: nat20 = auto-hit, nat1 = auto-miss
    """
    d20 = int(d20)
    is_nat20 = (d20 == 20)
    is_nat1 = (d20 == 1)

    total = d20 + int(attacker_mod) + int(weapon_attack_bonus)

    if is_nat20:
        return True, total, True, False
    if is_nat1:
        return False, total, False, True

    return (total >= int(target_ac)), total, False, False


def roll_damage(damage_expr: str) -> Tuple[int, str]:
    """
    Supports dice strings: '1d8+3', '2d6', '1d4 + 1'
    Returns (total, breakdown_str).
    """
    s = str(damage_expr or "").strip()
    m = _DICE_RE.match(s)
    if not m:
        # If damage is stored as int, or malformed string
        try:
            val = int(s)
            return val, str(val)
        except Exception:
            return 1, "1"

    n = int(m.group(1))
    sides = int(m.group(2))
    mod_val = int(m.group(3).replace(" ", "")) if m.group(3) else 0

    rolls = [random.randint(1, sides) for _ in range(n)]
    total = sum(rolls) + mod_val

    # Human-friendly breakdown
    mod_str = ""
    if mod_val:
        mod_str = f"{'+' if mod_val > 0 else ''}{mod_val}"
    breakdown = f"{n}d{sides}{mod_str} -> {rolls}{(' ' + mod_str) if mod_str else ''}".strip()
    return total, breakdown


def roll_damage_crit(damage_expr: str) -> Tuple[int, str]:
    """
    Crit rule (5e): double the number of dice, keep modifier once.
    Example: 1d8+3 -> 2d8+3
    """
    s = str(damage_expr or "").strip()
    m = _DICE_RE.match(s)
    if not m:
        return roll_damage(s)

    n = int(m.group(1))
    sides = int(m.group(2))
    mod_val = int(m.group(3).replace(" ", "")) if m.group(3) else 0

    # double dice count
    n2 = max(1, n * 2)
    rolls = [random.randint(1, sides) for _ in range(n2)]
    total = sum(rolls) + mod_val

    mod_str = ""
    if mod_val:
        mod_str = f"{'+' if mod_val > 0 else ''}{mod_val}"
    breakdown = f"CRIT {n2}d{sides}{mod_str} -> {rolls}{(' ' + mod_str) if mod_str else ''}".strip()
    return total, breakdown

class CombatEngine:
    """Core combat logic (no PyQt imports).

    The UI layer should:
      - choose attacker/target (selection, right-click, etc.)
      - call engine methods to compute outcomes + update token state
      - render overlays, toasts, and visuals based on returned results
    """

    def __init__(self, state: EncounterState, campaign_path: str, campaign_logger=None, server_client: ServerClient | None = None):
        self.state = state
        self.campaign_path = campaign_path
        self.server_client: ServerClient | None = server_client
        self.spells_db = {}
        self.campaign_logger = campaign_logger

    # ------------------------------
    # Sheet normalization
    # ------------------------------
    @staticmethod
    def get_sheet_combat_view(sheet: Dict[str, Any]) -> SheetCombatView:
        """Extract a stable combat view from multiple sheet schemas.

        IMPORTANT HP RULE:
        - Your server is authoritative for current HP under resources.current_hp.
        - Some sheets may also have stats.current_hp, but it may be stale.
        - Therefore: prefer resources.current_hp first.
        """

        if not isinstance(sheet, dict):
            sheet = {}

        stats = sheet.get("stats") or {}
        combat = sheet.get("combat") or {}
        equip = sheet.get("equipment") or {}
        equipped = sheet.get("equipped") or {}
        base = sheet.get("base_stats") or {}
        res = sheet.get("resources") or {}

        # --------------------
        # HP (authoritative: resources.current_hp)
        # --------------------
        current_hp = res.get("current_hp")
        if current_hp is None:
            current_hp = stats.get("current_hp")
        if current_hp is None:
            current_hp = stats.get("hp")
        if current_hp is None:
            current_hp = sheet.get("current_hp")

        # Max HP: keep your existing preference order (stats.max_hp can be authoritative in some campaigns)
        max_hp = stats.get("max_hp")
        if max_hp is None:
            max_hp = base.get("max_hp")
        if max_hp is None:
            max_hp = res.get("max_hp")
        if max_hp is None:
            max_hp = stats.get("hp_max")
        if max_hp is None:
            max_hp = sheet.get("max_hp")

        # --------------------
        # AC
        # --------------------
        ac = combat.get("ac")
        if ac is None:
            ac = stats.get("ac")
        if ac is None:
            ac = stats.get("defense")
        if ac is None:
            ac = stats.get("armor_class")
        if ac is None:
            ac = base.get("ac")

        # --------------------
        # Attack mod
        # --------------------
        attack_modifier = combat.get("attack_modifier")
        if attack_modifier is None:
            attack_modifier = stats.get("attack_modifier")
        if attack_modifier is None:
            attack_modifier = base.get("attack_modifier")

        # --------------------
        # Weapon ref (prefer equipped.weapon_id)
        # --------------------
        weapon_ref = combat.get("weapon_ref") or ""
        if not weapon_ref:
            weapon_ref = equipped.get("weapon_id") or ""
        if not weapon_ref:
            weapon_ref = equip.get("primary_weapon") or ""
        if not weapon_ref:
            weapon_ref = base.get("weapon_id") or base.get("weapon_ref") or ""

        damage_expr = combat.get("damage_expr") or base.get("damage_expr") or ""

        raw_view = {
            "current_hp": int(current_hp or 0),
            "max_hp": int(max_hp or 10),
            "ac": int(ac or 10),
            "attack_modifier": int(attack_modifier or 0),
            "weapon_ref": str(weapon_ref or "").strip(),
            "damage_expr": str(damage_expr or "").strip(),
            "vision_ft": int(stats.get("vision_ft") or base.get("vision_ft") or 60),
            "movement_ft": int(stats.get("movement_ft") or stats.get("movement") or base.get("movement_ft") or base.get("movement") or 30),
        }
        view = apply_passives_to_combat_view(sheet, raw_view)
        return SheetCombatView(
            current_hp=int(view.get("current_hp", 0) or 0),
            max_hp=int(view.get("max_hp", 10) or 10),
            ac=int(view.get("ac", 10) or 10),
            attack_modifier=int(view.get("attack_modifier", 0) or 0),
            weapon_ref=str(view.get("weapon_ref", "") or "").strip(),
            damage_expr=str(view.get("damage_expr", "") or "").strip(),
        )

    def hydrate_tokenstate_from_sheet(self, ts: TokenState, sheet: Dict[str, Any], *, include_hp: bool = True) -> None:
        view = self.get_sheet_combat_view(sheet)

        ts.ac = int(view.ac)
        ts.attack_modifier = int(view.attack_modifier)

        # Keep weapon_ref for overlays / attack resolution
        if view.weapon_ref:
            ts.weapon_ref = view.weapon_ref

        if view.damage_expr:
            ts.damage_expr = view.damage_expr

        # Always update max_hp even if include_hp=False
        ts.max_hp = max(1, int(view.max_hp))

        if include_hp:
            ts.hp = max(0, min(int(view.current_hp), ts.max_hp))

        # Phase D3: typed damage modifiers / profile
        try:
            damage_profile = {}
            for key in ("damage_profile", "damage_modifiers"):
                raw_profile = sheet.get(key)
                if isinstance(raw_profile, dict) and raw_profile:
                    damage_profile.update(raw_profile)
            if isinstance(sheet.get("resistances"), (list, tuple, set, str)):
                damage_profile.setdefault("resistances", sheet.get("resistances"))
            if isinstance(sheet.get("immunities"), (list, tuple, set, str)):
                damage_profile.setdefault("immunities", sheet.get("immunities"))
            if isinstance(sheet.get("vulnerabilities"), (list, tuple, set, str)):
                damage_profile.setdefault("vulnerabilities", sheet.get("vulnerabilities"))
            for container_key in ("stats", "combat", "resources"):
                block = sheet.get(container_key) or {}
                if not isinstance(block, dict):
                    continue
                for subkey in ("damage_profile", "damage_modifiers"):
                    raw_profile = block.get(subkey)
                    if isinstance(raw_profile, dict) and raw_profile:
                        merged = dict(damage_profile)
                        merged.update(raw_profile)
                        damage_profile = merged
                for subkey, bucket in (("resistances", "resistances"), ("immunities", "immunities"), ("vulnerabilities", "vulnerabilities")):
                    if isinstance(block.get(subkey), (list, tuple, set, str)) and subkey not in damage_profile:
                        damage_profile[subkey] = block.get(subkey)
            resolved_view = apply_passives_to_combat_view(sheet, {"damage_profile": damage_profile})
            damage_profile = dict(resolved_view.get("damage_profile") or damage_profile or {})
            ts.damage_profile = dict(damage_profile or {})
            ts.damage_resistances = list((damage_profile.get("resistances", []) if isinstance(damage_profile, dict) else []) or [])
            ts.damage_immunities = list((damage_profile.get("immunities", []) if isinstance(damage_profile, dict) else []) or [])
            ts.damage_vulnerabilities = list((damage_profile.get("vulnerabilities", []) if isinstance(damage_profile, dict) else []) or [])
            if resolved_view.get("darkvision_ft") is not None:
                ts.darkvision_ft = int(resolved_view.get("darkvision_ft") or getattr(ts, "darkvision_ft", 0) or 0)
            if resolved_view.get("save_bonus"):
                ts.save_bonus = dict(resolved_view.get("save_bonus") or {})
            if resolved_view.get("ignore_difficult_terrain"):
                setattr(ts, "ignore_difficult_terrain", True)
        except Exception:
            pass

    # ------------------------------
    # Damage application
    # ------------------------------
    def apply_damage_to_token(
        self,
        target_ts: TokenState,
        amount: int,
        *,
        encounter_id: str = "",
        pending_attack_id: str = "",
        damage_type: str = "",
        source_meta: Optional[Dict[str, Any]] = None,
    ) -> dict:
        dmg_type = normalize_damage_type(damage_type)
        resolution = resolve_damage(int(amount), dmg_type, actor=target_ts, source=source_meta)
        dmg = int(resolution.get("final_damage", 0) or 0)

        if getattr(target_ts, "stat_source", "") == "character_sheet" and getattr(target_ts, "character_id", ""):
            try:
                if not self.server_client:
                    raise RuntimeError("ServerClient is not configured on CombatEngine")

                j = self.server_client.apply_damage_to_character(
                    target_ts.character_id,
                    dmg,
                    token_id=target_ts.token_id,
                    encounter_id=encounter_id,
                    pending_attack_id=pending_attack_id,
                )

                if isinstance(j, dict):
                    print("[DMG][SERVER]", target_ts.character_id, "->", j)

                if isinstance(j, dict):
                    hp = j.get("current_hp")
                    max_hp = j.get("max_hp")
                    if hp is not None:
                        target_ts.hp = int(hp)
                    if max_hp is not None:
                        target_ts.max_hp = max(1, int(max_hp))

            except Exception as e:
                print("[DMG] server apply failed, falling back local:", e)
                target_ts.hp = max(0, int(getattr(target_ts, "hp", 0)) - dmg)
        else:
            target_ts.hp = max(0, int(getattr(target_ts, "hp", 0)) - dmg)

        target_ts.max_hp = max(1, int(getattr(target_ts, "max_hp", 10) or 10))
        target_ts.hp = max(0, min(int(getattr(target_ts, "hp", 0)), target_ts.max_hp))
        resolution["applied_damage"] = int(dmg)
        resolution["hp_after"] = int(getattr(target_ts, "hp", 0) or 0)
        resolution["max_hp_after"] = int(getattr(target_ts, "max_hp", 10) or 10)
        return resolution

    # ------------------------------
    # NPC resolution
    # ------------------------------
    def resolve_npc_attack(
        self,
        attacker_id: str,
        target_id: str,
        *,
        encounter_id: str = "",
        meta: Optional[Dict[str, Any]] = None,
        apply_damage: bool = True,
    ) -> Optional[NpcAttackOutcome]:
        attacker = self.state.tokens.get(attacker_id)
        target = self.state.tokens.get(target_id)
        if not attacker or not target:
            return None

        # Ensure items DB is available for NPC weapon damage resolution
        if getattr(self, "items_db", None) is None:
            self.load_items_db()

        # ---- Hydrate from sheets if available ----
        # Attacker: hydrate stats (no HP override needed)
        if getattr(attacker, "stat_source", "") == "character_sheet" and getattr(attacker, "character_id", ""):
            if not self.server_client:
                raise RuntimeError("ServerClient is not configured on CombatEngine")
            s = self.server_client.get_character_sheet(attacker.character_id)
            if isinstance(s, dict):
                self.hydrate_tokenstate_from_sheet(attacker, s, include_hp=False)

        # Target: hydrate stats; only hydrate HP if token looks uninitialized
        if getattr(target, "stat_source", "") == "character_sheet" and getattr(target, "character_id", ""):
            if not self.server_client:
                raise RuntimeError("ServerClient is not configured on CombatEngine")
            s = self.server_client.get_character_sheet(target.character_id)
            if isinstance(s, dict):
                view = self.get_sheet_combat_view(s)

                token_hp = int(getattr(target, "hp", 0) or 0)
                token_max = int(getattr(target, "max_hp", 0) or 0)

                is_uninitialized = (
                    (token_max <= 10 and int(view.max_hp) > token_max)
                    or (token_hp == 0 and token_max <= 10 and int(view.current_hp) > 0)
                )

                self.hydrate_tokenstate_from_sheet(target, s, include_hp=is_uninitialized)

        # ---- Roll to hit ----
        attack_mod = int(getattr(attacker, "attack_modifier", 0) or 0)
        ac_base = int(getattr(target, "ac", 10) or 10)
        d20 = 0
        weapon_ref = (getattr(attacker, "weapon_id", "") or "").strip() or (getattr(attacker, "weapon", "") or "").strip()
        weapon_data = self.get_weapon_data(weapon_ref) if weapon_ref else {}
        # ---- Targeting LOS policy (Phase B5) ----
        # Default: NPC weapon attacks require LOS unless weapon data sets requires_los=false.
        try:
            weapon_ref = (getattr(attacker, "weapon_id", "") or getattr(attacker, "weapon", "") or "").strip() or "unarmed"
            requires_los = True
            try:
                weapons = (self.items_db or {}).get("weapons", []) or []
                wdata = None
                for w in weapons:
                    try:
                        if str(w.get("id", "")).strip() == weapon_ref or str(w.get("name", "")).strip().lower() == weapon_ref.lower():
                            wdata = w
                            break
                    except Exception:
                        continue
                if isinstance(wdata, dict) and ("requires_los" in wdata):
                    requires_los = bool(wdata.get("requires_los"))
            except Exception:
                requires_los = True

            if requires_los:
                from .visibility_polygon_engine import build_segments_from_meta
                from .los_engine import has_los
                segs = build_segments_from_meta(meta or {}, include_blocked=True, door_state=getattr(self.state, 'door_state', {}) or {})
                if not has_los(
                    attacker_grid_x=int(getattr(attacker, "grid_x", 0) or 0),
                    attacker_grid_y=int(getattr(attacker, "grid_y", 0) or 0),
                    target_grid_x=int(getattr(target, "grid_x", 0) or 0),
                    target_grid_y=int(getattr(target, "grid_y", 0) or 0),
                    segments=segs,
                ):
                    if self.campaign_logger:
                        self.campaign_logger.combat(
                            "npc_no_los_block",
                            attacker_token_id=attacker.token_id,
                            attacker_name=attacker.display_name,
                            target_token_id=target.token_id,
                            target_name=target.display_name,
                            encounter_id=encounter_id,
                            weapon_ref=weapon_ref,
                        )
                    return NpcAttackOutcome(
                        d20=int(d20),
                        total_to_hit=int(0),
                        target_ac=int(ac_base),
                        is_hit=False,
                        damage_roll_expr="",
                        damage_total=0,
                    )

            # ---- Perception policy (B-X4: Vision Types) ----
            # Default: weapon attacks require sight unless weapon data sets requires_sight=false.
            try:
                requires_sight = True
                if isinstance(wdata, dict) and ("requires_sight" in wdata):
                    requires_sight = bool(wdata.get("requires_sight"))
            except Exception:
                requires_sight = True

            try:
                from .perception_engine import can_perceive_target

                pres = can_perceive_target(
                    attacker_ts=attacker,
                    target_ts=target,
                    meta=meta or {},
                    feet_per_square=int(getattr(self.state, "grid_ft", 5) or 5),
                    requires_sight=bool(requires_sight),
                )
                if not bool(pres.can_perceive):
                    if self.campaign_logger:
                        self.campaign_logger.combat(
                            "npc_no_perception_block",
                            attacker_token_id=attacker.token_id,
                            attacker_name=attacker.display_name,
                            target_token_id=target.token_id,
                            target_name=target.display_name,
                            encounter_id=encounter_id,
                            weapon_ref=weapon_ref,
                            light_level=str(getattr(pres, "light_level", "")),
                            method=str(getattr(pres, "method", "")),
                            reason=str(getattr(pres, "reason", "")),
                        )
                    return NpcAttackOutcome(
                        d20=int(d20),
                        total_to_hit=int(0),
                        target_ac=int(ac_base),
                        is_hit=False,
                        damage_roll_expr="",
                        damage_total=0,
                    )
            except Exception:
                pass
        except Exception:
            pass


        # ---- Cover (geometry-derived) ----
        cover_tier = "none"
        cover_bonus = 0
        cover_total_blocked = False
        try:
            if meta:
                from .cover_engine import compute_cover

                cover_tier, cover_bonus, _dbg = compute_cover(
                    attacker_grid_x=int(getattr(attacker, "grid_x", 0) or 0),
                    attacker_grid_y=int(getattr(attacker, "grid_y", 0) or 0),
                    target_grid_x=int(getattr(target, "grid_x", 0) or 0),
                    target_grid_y=int(getattr(target, "grid_y", 0) or 0),
                    meta=meta or {},
                    door_state=getattr(self.state, "door_state", {}) or {},
                )
                if cover_tier == "total":
                    cover_total_blocked = True
        except Exception:
            # Cover is best-effort for NPCs; never crash resolution.
            cover_tier = "none"
            cover_bonus = 0
            cover_total_blocked = False

        ac_effective = int(ac_base + int(cover_bonus or 0))

        attack_cond = attack_mode_from_conditions(attacker, target, weapon_data if isinstance(weapon_data, dict) else {}, "weapon")
        npc_mode = str(merge_roll_modes(attack_cond.get("mode", "normal")) or "normal")
        if npc_mode == "advantage":
            d20 = max(int(roll_dice(20)), int(roll_dice(20)))
        elif npc_mode == "disadvantage":
            d20 = min(int(roll_dice(20)), int(roll_dice(20)))
        else:
            d20 = int(roll_dice(20))

        if cover_total_blocked:
            hit = False
            total_to_hit = int(d20 + attack_mod)
        else:
            hit, total_to_hit = evaluate_hit(d20, attack_mod, ac_effective)

        damage_expr = ""
        damage_total = 0
        damage_rolls = []

        if hit:
            # 1) explicit damage_expr if present
            damage_expr = (getattr(attacker, "damage_expr", "") or "").strip()

            # 2) resolve from weapon_id / weapon via items.json
            if not damage_expr:
                weapon_ref = (getattr(attacker, "weapon_id", "") or "").strip()
                if not weapon_ref:
                    weapon_ref = (getattr(attacker, "weapon", "") or "").strip()

                wd = self.get_weapon_data(weapon_ref) if weapon_ref else {}
                damage_expr = str(wd.get("damage", "") or "").strip()

            # 3) final fallback
            if not damage_expr:
                damage_expr = "1d4"

            damage_total, damage_rolls = roll_dice(damage_expr)
            if bool(apply_damage):
                self.apply_damage_to_token(target, int(damage_total), encounter_id=encounter_id, damage_type=str((weapon_data or {}).get("damage_type", "") if isinstance(weapon_data, dict) else ""), source_meta=(weapon_data if isinstance(weapon_data, dict) else None))

        # ---- Terminal prints (debug) ----
        try:
            if cover_total_blocked:
                print(
                    f"[NPC] {attacker.display_name} -> {target.display_name}: "
                    f"d20={d20} total={total_to_hit} vs AC={ac_base}(+{cover_bonus} cover={cover_tier}) => BLOCKED"
                )
            else:
                print(
                    f"[NPC] {attacker.display_name} -> {target.display_name}: "
                    f"d20={d20} total={total_to_hit} vs AC={ac_base}(+{cover_bonus} cover={cover_tier})={ac_effective} => {'HIT' if hit else 'MISS'}"
                )
        except Exception:
            pass
        if hit:
            print(
                f"[NPC] Damage {damage_expr} => {damage_total} ({damage_expr} -> {damage_rolls}) | "
                f"Target HP now {int(getattr(target, 'hp', 0) or 0)}/{int(getattr(target, 'max_hp', 10) or 10)}"
            )

        # ---- Structured logging ----
        if self.campaign_logger:
            self.campaign_logger.combat(
                "npc_attack_resolved",
                attacker_token_id=attacker.token_id,
                attacker_name=attacker.display_name,
                target_token_id=target.token_id,
                target_name=target.display_name,
                d20=int(d20),
                total=int(total_to_hit),
                target_ac=int(ac_effective),
                target_ac_base=int(ac_base),
                cover_tier=str(cover_tier),
                cover_bonus=int(cover_bonus),
                cover_total_blocked=bool(cover_total_blocked),
                hit=bool(hit),
                damage=int(damage_total),
                target_hp=int(getattr(target, "hp", 0) or 0),
                target_max_hp=int(getattr(target, "max_hp", 10) or 10),
                encounter_id=encounter_id,
                damage_expr=damage_expr if hit else "",
                mode=str(npc_mode),
                condition_reasons=list(attack_cond.get("reasons", []) or []),
            )

        return NpcAttackOutcome(
            d20=int(d20),
            total_to_hit=int(total_to_hit),
            target_ac=int(ac_effective),
            is_hit=bool(hit),
            damage_roll_expr=damage_expr if hit else "",
            damage_total=int(damage_total),
        )

    def load_spells_db(self):
        """
        Load campaign spells.json and normalize Phase F spell fields while
        remaining backward-compatible with older spell entries.
        """
        path = os.path.join(self.campaign_path, "spells.json")
        if not os.path.exists(path):
            self.spells_db = {}
            print("[SPELLS] spells.json not found:", path)
            return

        try:
            self.spells_db = load_normalized_spells_db(path)
        except Exception as e:
            self.spells_db = {}
            print("[SPELLS] Failed to load spells.json:", e)
            return

        print(f"[SPELLS] Loaded {len(self.spells_db)} spells")

    def load_items_db(self) -> None:
        """Load campaign items.json into memory for weapon resolution (NPC damage, etc.)."""
        path = os.path.join(self.campaign_path, "items.json")
        if not os.path.exists(path):
            self.items_db = {}
            return
        try:
            with open(path, "r", encoding="utf-8") as f:
                self.items_db = json.load(f)
        except Exception:
            self.items_db = {}

    def get_weapon_data(self, weapon_ref: str) -> Dict[str, Any]:
        """
        Resolve weapon_ref (item_id or name) -> weapon dict from items.json.
        Fallback: unarmed.
        """
        weapon_ref = (weapon_ref or "").strip()
        db = getattr(self, "items_db", None) or {}
        weapons = (db.get("weapons", []) or []) if isinstance(db, dict) else []

        # direct id match
        for w in weapons:
            if str(w.get("item_id", "")).strip() == weapon_ref:
                return w

        # name match
        low = weapon_ref.lower()
        for w in weapons:
            nm = str(w.get("name", "") or "").strip().lower()
            if nm and nm == low:
                return w

        # fallback to "unarmed" by name
        for w in weapons:
            if str(w.get("name", "") or "").strip().lower() == "unarmed":
                return w

        return {}
