import os
import json
import requests
import random
import uuid
import time
from typing import Optional, Dict, Any
from PyQt5.QtWidgets import QMainWindow, QFileDialog, QAction, QGraphicsScene, QMenu, QDockWidget, QColorDialog, QPushButton, QToolBar, QMessageBox, QInputDialog, QDialog, QVBoxLayout
from PyQt5.QtGui import QPixmap, QPen, QColor
from PyQt5.QtCore import QRectF, Qt, QTimer
from PyQt5.QtCore import QEvent
from PyQt5.QtWidgets import QComboBox, QLabel
from PyQt5.QtWidgets import QTabWidget
from PyQt5 import sip
from ui.asset_browser import AssetBrowser
from ui.map_view import MapView
from ui.map_metadata_editor import MapMetadataEditorWidget
from ui.token import DraggableToken
from ui.upload_asset_tab import UploadAssetTab
from ui.equipment_manager import EquipmentManagerTab
from ui.token_manager import TokenManagerTab
from ui.encounter_manager.encounter_window import EncounterWindow
from ui.shop_system import ShopPanel
from ui.constants import GRID_SIZE
from ui.encounter_state import EncounterState, TokenState
from engine.combat_engine import CombatEngine
from engine.save_engine import normalize_ability_key, resolve_save_result, roll_engine_save_result, compute_damage_after_save
from engine.condition_engine import canonical_condition_record, process_turn_hook
from engine.condition_semantics import attack_mode_from_conditions, save_rule_from_conditions, effective_speed_ft, condition_semantic_summary, merge_roll_modes
from engine.spell_resolution import normalized_effects_from_spell, parse_rounds_from_duration, normalize_effect_type, normalized_targeting, split_target_hints
from ui.player_view_window import PlayerViewWindow
from ui.combat_hud import CombatHudWidget
from engine.combat_utils import choose_d20, roll_damage, roll_damage_crit, resolve_attack
from engine.cover_engine import compute_cover, merge_cover_tiers, cover_bonus_for_tier
from engine.movement_range_engine import compute_reachable_cells, compute_min_cost_ft, is_blocked
from engine.los_engine import has_los
from engine.hazard_engine import resolve_hazards
from engine.visibility_polygon_engine import build_segments_from_meta
from ui.item_lookup import get_weapon, get_weapon_or_unarmed
from ui.campaign_log import CampaignLogger, CampaignLogWidget
from ui.initiative_panel import InitiativePanelWidget
from ui.encounter_state import ensure_movement_initialized, reset_movement_for_turn, spend_movement
from ui.campaign_config import load_campaign_config
from ui.rules.registry import RulesRegistry
from net.server_client import ServerClient
from engine.services.sheet_sync_service import sync_sheet_backed_tokens
from engine.combat_engine import CombatEngine
from engine.trait_engine import apply_passives_to_combat_view
from engine.spell_engine import start_concentration, clear_concentration
from encounter.history_runtime import HistoryRuntime
# Phase B1/B2: map metadata service (disk I/O lives outside ui)
try:
    from engine.services.map_metadata_service import ensure_loaded as ensure_map_meta_loaded
except Exception:
    ensure_map_meta_loaded = None

SERVER_BASE = "http://127.0.0.1:8000"

def derive_campaign_id(campaign_path: str) -> str:
    """
    Campaign ID = folder name of the campaign path.
    Example: ..\\campaigns\\Test -> "Test"
    """
    if not campaign_path:
        return "Test"
    p = os.path.normpath(campaign_path)
    name = os.path.basename(p)
    return name or "Test"

def get_sheet_combat_view(sheet: dict) -> dict:
    """
    Normalizes character sheet into combat-relevant fields.

    Priority for weapon/armor:
      1) sheet["equipped"] (authoritative equipped loadout)
      2) sheet["base_stats"].weapon_id / armor_id
      3) sheet["base_stats"].weapon / armor (legacy)
    """
    if not isinstance(sheet, dict):
        return {}

    # NOTE: Multiple sheet schemas exist in this project.
    # - schema_version=2 uses top-level "stats" and "equipment".
    # - older schemas use "base_stats" + "resources" + optional "equipped".
    base = sheet.get("base_stats", {}) or {}
    res = sheet.get("resources", {}) or {}
    stats = sheet.get("stats", {}) or {}
    equipment = sheet.get("equipment", {}) or {}
    combat = sheet.get("combat", {}) or {}
    eq = sheet.get("equipped", {}) or {}

    # Equipped is authoritative (support both schemas)
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

    # Fallbacks (support both weapon_id + legacy "weapon" fields)
    bs_weapon = str(
        base.get("weapon_id", "")
        or base.get("weapon", "")
        or stats.get("weapon_id", "")
        or stats.get("weapon", "")
        or combat.get("weapon_ref", "")
        or ""
    ).strip()
    bs_armor  = str(
        base.get("armor_id", "")
        or base.get("armor", "")
        or stats.get("armor_id", "")
        or stats.get("armor", "")
        or ""
    ).strip()

    weapon_id = eq_weapon or bs_weapon
    armor_id  = eq_armor  or bs_armor

    # HP: prefer schema_version=2 stats, then resources, then base_stats.
    max_hp = int(stats.get("max_hp", None) or base.get("max_hp", 10) or 10)
    current_hp = stats.get("current_hp", None)
    if current_hp is None:
        current_hp = res.get("current_hp", None)
    if current_hp is None:
        current_hp = res.get("hp", None)
    if current_hp is None:
        current_hp = max_hp

    view = {
        "ac": int(combat.get("ac", None) or stats.get("defense", None) or stats.get("ac", None) or base.get("ac", 10) or 10),
        "attack_modifier": int(combat.get("attack_modifier", None) or stats.get("attack_modifier", None) or base.get("attack_modifier", 0) or 0),
        "weapon_id": weapon_id,
        "armor_id": armor_id,
        "movement": int(stats.get("movement_ft", None) or stats.get("movement", None) or base.get("movement_ft", None) or base.get("movement", 30) or 30),
        "vision_ft": int(stats.get("vision_ft", None) or base.get("vision_ft", 60) or 60),
        "max_hp": max_hp,
        "current_hp": int(current_hp),
        "rage_active": bool(combat.get("rage_active", False)),
        "reckless_attack_active": bool(combat.get("reckless_attack_active", False)),
        "rage_damage_bonus": int(combat.get("rage_damage_bonus", 0) or 0),
        "brutal_critical_dice": int(combat.get("brutal_critical_dice", 0) or 0),
        "attacks_per_action": int(combat.get("attacks_per_action", 1) or 1),
        "initiative_advantage": bool(combat.get("initiative_advantage", False)),
        "danger_sense": bool(combat.get("danger_sense", False)),
        "relentless_rage_uses": int(combat.get("relentless_rage_uses", 0) or 0),
        "sneak_attack_dice": int(combat.get("sneak_attack_dice", 0) or 0),
        "uncanny_dodge_armed": bool(combat.get("uncanny_dodge_armed", False)),
        "evasion": bool(combat.get("evasion", False)),
        "reliable_talent": bool(combat.get("reliable_talent", False)),
        "blindsense_ft": int(combat.get("blindsense_ft", 0) or 0),
        "slippery_mind": bool(combat.get("slippery_mind", False)),
        "elusive": bool(combat.get("elusive", False)),
        "aura_of_protection_bonus": int(combat.get("aura_of_protection_bonus", 0) or 0),
        "aura_of_protection_radius_ft": int(combat.get("aura_of_protection_radius_ft", 0) or 0),
        "aura_of_courage": bool(combat.get("aura_of_courage", False)),
        "aura_of_courage_radius_ft": int(combat.get("aura_of_courage_radius_ft", 0) or 0),
        "improved_divine_smite_dice": int(combat.get("improved_divine_smite_dice", 0) or 0),
        "divine_sense_active": bool(combat.get("divine_sense_active", False)),
        "martial_arts_die": int(combat.get("martial_arts_die", 0) or 0),
        "unarmored_movement_bonus_ft": int(combat.get("unarmored_movement_bonus_ft", 0) or 0),
        "deflect_missiles_armed": bool(combat.get("deflect_missiles_armed", False)),
        "patient_defense_active": bool(combat.get("patient_defense_active", False)),
        "step_of_the_wind_mode": str(combat.get("step_of_the_wind_mode", "") or ""),
        "stunning_strike_armed": bool(combat.get("stunning_strike_armed", False)),
        "ki_empowered_strikes": bool(combat.get("ki_empowered_strikes", False)),
        "slow_fall_reduction": int(combat.get("slow_fall_reduction", 0) or 0),
        "diamond_soul": bool(combat.get("diamond_soul", False)),
        "empty_body_active": bool(combat.get("empty_body_active", False)),
    }
    if isinstance(combat.get("save_bonus"), dict):
        view["save_bonus"] = dict(combat.get("save_bonus") or {})
    return apply_passives_to_combat_view(sheet, view)

class MainWindow(QMainWindow):
    def __init__(self, campaign_path):
        super().__init__()
        self.campaign_path = campaign_path
        self.campaign_id = derive_campaign_id(self.campaign_path)
        # Canonical name throughout the codebase is `server_client`.
        self.server_client = ServerClient(base_url=SERVER_BASE, campaign_id=self.campaign_id)
        # Back-compat alias (older code used `self.server`).
        self.server = self.server_client
        self._start_sheet_sync_timer()  # Phase C9: hydrate PC tokens from server sheets
        print(f"[Campaign] campaign_id={self.campaign_id} path={self.campaign_path}")
        self.setWindowTitle("Grengine")
        self.setGeometry(100, 100, 1000, 800)
        # --- Campaign Log (must be created early) ---
        self.campaign_logger = CampaignLogger(self.campaign_path)
        self.campaign_logger.system("Campaign loaded", campaign_path=self.campaign_path)

        self.scene = QGraphicsScene()
        self.view = MapView(self.scene)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self.show_context_menu)
        self.setCentralWidget(self.view)

        # Map metadata editor state
        self.map_view = self.view
        self.current_map_meta = {}
        self.current_map_meta_path = ""
        self.map_metadata_editor = None
        self.map_metadata_dock = None

        # -----------------------------
        # Phase 4.3: AoE template preview state
        # -----------------------------
        self.spells_db = {}               # spell_id -> spell dict
        self._aoe_active = False          # whether we're previewing a template
        self._aoe_spell_id = ""           # currently selected spell_id
        self._aoe_target_cell = None      # (gx, gy) under mouse while active
        self._aoe_last_selected_pc_token_id = None  # cached selection for convenience
        self._aoe_locked = False
        self._aoe_locked_cell = None   # (gx, gy) when locked

        self._aoe_affected_token_ids = []

        self._awaiting_aoe_damage = None  # dict like _awaiting_damage but with target_token_ids list

        # Ensure we receive mouse move events on the view
        try:
            self.view.setMouseTracking(True)
            self.view.viewport().setMouseTracking(True)
            self.view.viewport().installEventFilter(self)
        except Exception as e:
            print("[AOE] Failed to install event filter:", e)

        self.asset_browser = AssetBrowser(self.campaign_path)
        self.asset_browser.map_selected.connect(self.load_map_from_path)
        self.asset_browser.token_selected.connect(self.add_token_from_browser)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.view, "Map View")

        self.upload_tab = UploadAssetTab(self.campaign_path, self.asset_browser)
        self.tabs.addTab(self.upload_tab, "Maps")

        self.token_manager_tab = TokenManagerTab(self.campaign_path, server_client=getattr(self, "server_client", None))
        self.token_manager_tab.tokens_updated.connect(self.asset_browser.load_assets)  # NEW
        self.tabs.addTab(self.token_manager_tab, "Tokens")

        self.equipment_tab = EquipmentManagerTab(self.campaign_path)
        self.tabs.addTab(self.equipment_tab, "Equipment")

        self.setCentralWidget(self.tabs)

        dock = QDockWidget("Assets", self)
        dock.setWidget(self.asset_browser)
        dock.setFloating(False)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)

        self.create_menu()
        self.token_items = []
        self.current_map_path = None

        self.create_drawing_toolbar()

        self.aoe_spell_combo = QComboBox()
        self.aoe_spell_combo.setMinimumWidth(220)
        self.aoe_spell_combo.currentIndexChanged.connect(self._on_aoe_spell_combo_changed)

        # --- Campaign config + ruleset (Phase 5.0.1) ---
        self.campaign_config = load_campaign_config(self.campaign_path)
        self.ruleset_id = str(self.campaign_config.get("ruleset", "default") or "default")
        self.rules = RulesRegistry.load(self.ruleset_id)

        print(f"[Campaign] loaded campaign.json ruleset={self.ruleset_id} grid_ft={self.campaign_config.get('grid_ft')} vision_rules={self.campaign_config.get('vision_rules')}")

        # Put it somewhere convenient (example: your main toolbar)
        try:
            self.toolbar.addSeparator()
            self.toolbar.addWidget(QLabel("Spell: "))
            self.toolbar.addWidget(self.aoe_spell_combo)
        except Exception:
            pass

        self.roll_timer = QTimer()
        self.roll_timer.timeout.connect(self.check_for_incoming_rolls)
        self.roll_timer.start(1000)

        self._awaiting_damage = None  # dict with attack_id, player_id, target_token_id, dmg_expr, crit

        self.scene.selectionChanged.connect(self.handle_selection_changed)

        self.shop_panel = ShopPanel(self.campaign_path, self.send_item_to_player)

        shop_dock = QDockWidget("Shop", self)
        shop_dock.setWidget(self.shop_panel)
        self.addDockWidget(Qt.LeftDockWidgetArea, shop_dock)

        self.state = EncounterState(campaign_path=self.campaign_path)
        # Core combat logic (non-UI)
        self.combat_engine = CombatEngine(
            self.state,
            campaign_path=self.campaign_path,
            campaign_logger=self.campaign_logger,
            server_client=self.server_client,
        )
        self.player_view_window = None
        self._armed_attacker_id = None
        self._armed_target_id = None
        self.state.pending_damage = None

        self.combat_engine.load_spells_db()
        try:
            self.combat_engine.load_items_db()
        except Exception:
            pass

        self.hp_authority = "encounter"

        # --- Phase 5.0.3: deterministic event history (base + replay) ---
        self.history_runtime = HistoryRuntime()
        # base snapshot is captured when initiative/encounter starts (or you can do it immediately

        # --- Combat HUD (DM-side) ---
        self._hud_status_msg = "Idle"
        self._hud_status_until = 0.0  # monotonic timestamp

        self.combat_hud = CombatHudWidget(
            on_arm_pc_attack=self.hud_arm_pc_attack,
            on_cancel_pending=self.hud_cancel_pending,
            on_force_resolve_npc=self.hud_force_resolve_npc,
            on_clear_selection=self.hud_clear_selection,
            on_roll_death_save=self.hud_roll_death_save,
            on_cancel_awaiting_damage=self.hud_cancel_awaiting_damage,  # NEW
            on_revert_illegal_move=self.hud_revert_illegal_move,  # NEW
        )
        combat_dock = QDockWidget("Combat", self)
        combat_dock.setWidget(self.combat_hud)
        combat_dock.setFloating(False)
        self.addDockWidget(Qt.LeftDockWidgetArea, combat_dock)

        # --- Initiative / Turn Order (Phase 3) ---
        self.initiative_panel = InitiativePanelWidget(
            on_roll_all=self.ui_roll_initiative_all,
            on_roll_pcs=self.ui_roll_initiative_pcs,
            on_roll_npcs=self.ui_roll_initiative_npcs,
            on_roll_selected=self.ui_roll_initiative_selected,
            on_start_encounter=self.ui_start_initiative_encounter,
            on_end_turn=self.ui_end_turn,
            on_prev_turn=self.ui_prev_turn,
            on_next_turn=self.ui_next_turn,
            on_end_encounter=self.ui_end_initiative_encounter,  # only if it exists
            on_undo=self.ui_history_undo,
            on_redo=self.ui_history_redo,
        )
        initiative_dock = QDockWidget("Initiative", self)
        initiative_dock.setWidget(self.initiative_panel)
        initiative_dock.setFloating(False)
        self.addDockWidget(Qt.LeftDockWidgetArea, initiative_dock)

        self._combat_hud_timer = QTimer()
        self._combat_hud_timer.timeout.connect(self.update_combat_hud)
        self._combat_hud_timer.start(250)
        self.state.pending_attack = None
        self._pending_save_requests = {}
        self._pending_death_save_requests = {}
        self._last_save_result = None

        self.state_sync_timer = QTimer()
        self.state_sync_timer.timeout.connect(self.sync_token_positions_to_state)
        self.state_sync_timer.start(250)  # 4x/sec is plenty

        self.campaign_log_widget = CampaignLogWidget(self.campaign_logger, initial_tail=300)
        log_dock = QDockWidget("Campaign Log", self)
        log_dock.setWidget(self.campaign_log_widget)
        log_dock.setFloating(False)
        self.addDockWidget(Qt.RightDockWidgetArea, log_dock)

        self._log_timer = QTimer()
        self._log_timer.timeout.connect(self.campaign_log_widget.refresh)  # or .reload/.update_tail depending on your widget API
        self._log_timer.start(1000)  # 1x/sec is plenty

    def _history_capture_base_if_needed(self) -> None:
        """
        Ensure we have a base snapshot for deterministic replay.
        Uses HistoryRuntime (Phase 5.0.3). Safe no-op if runtime missing.
        """
        hr = getattr(self, "history_runtime", None)
        if not hr:
            return
        try:
            hr.capture_base_if_needed(self.state)
        except Exception:
            # Never crash the game loop due to history
            pass


    def _history_append_and_apply(self, ev: dict) -> None:
        """
        Append an event to deterministic history, and optionally capture a checkpoint
        if ev["checkpoint"] is True.

        IMPORTANT: The caller already mutated EncounterState. History is for replay/logging,
        not for applying effects at runtime.
        """
        hr = getattr(self, "history_runtime", None)
        if not hr:
            return

        if not isinstance(ev, dict):
            return

        # Normalize: allow old call sites that pass {"type": "..."} instead of {"event_type": "..."}
        if "type" in ev and "event_type" not in ev:
            ev["event_type"] = ev.get("type")

        try:
            hr.append_event(self.state, ev, advance_cursor=True)
        except Exception:
            # Never crash the game loop due to history
            pass

    def _replay_to_cursor(self, cursor: int, *, reason: str = "") -> None:
        """
        Rebuild EncounterState from history base + events[:cursor], then hard-sync UI.
        This MUST refresh HP bars immediately (no waiting for the next damage event).
        """
        hr = getattr(self, "history_runtime", None)
        hist = getattr(hr, "history", None) if hr else None
        if not hr or not hist or getattr(hist, "base_snapshot", None) is None:
            return

        # Clamp cursor
        try:
            events = list(getattr(hist, "events", []) or [])
            cursor = max(0, min(int(cursor), len(events)))
        except Exception:
            cursor = int(cursor) if cursor is not None else 0

        # Replay and then sync scene tokens (HP bars, positions, death sprites, repaint)
        hr.replay_to_cursor(
            self.state,
            cursor,
            on_after_replay=self._sync_scene_from_state_after_replay,
        )

        # Optional: HUD status line
        if reason:
            self._set_hud_status(f"{reason}: replayed to step {cursor}")
            self.update_combat_hud()
    
    def _post_replay_ui_sync(self) -> None:
        """
        After undo/redo replay mutates EncounterState, push state back into graphics EVERY time:
        - token positions
        - hp/max_hp + hp bar
        - death sprite
        - overlays/highlights
        """
        tokens = getattr(self.state, "tokens", {}) or {}

        # 1) Position + HP visuals
        for token_id, ts in tokens.items():
            item = None
            try:
                item = self._get_scene_token_item(token_id)
            except Exception:
                item = None
            if item is None:
                continue

            # --- Position: force scene to state (this fixes your "undo doesn't move token back") ---
            try:
                gx = int(getattr(ts, "grid_x", 0) or 0)
                gy = int(getattr(ts, "grid_y", 0) or 0)

                item._suppress_move_callback = True
                item.setPos(gx * GRID_SIZE, gy * GRID_SIZE)  # IMPORTANT: use GRID_SIZE constant
                item._suppress_move_callback = False
            except Exception:
                try:
                    item._suppress_move_callback = False
                except Exception:
                    pass

            # --- HP/death visuals ---
            try:
                self.apply_state_hp_to_scene_token(token_id)
            except Exception:
                pass

            try:
                item.update()
            except Exception:
                pass

        # 2) Highlight active token
        try:
            self._update_active_token_highlight()
        except Exception:
            pass

        # 3) Rebuild overlays for active token (if any)
        try:
            active_id = getattr(self.state, "active_token_id", None)
            if active_id:
                active_item = self._get_scene_token_item(active_id)
                if active_item is not None:
                    try:
                        active_item.setSelected(True)
                    except Exception:
                        pass
                    try:
                        self.process_token_selection(active_item)
                    except Exception:
                        pass
        except Exception:
            pass

        # 4) Player view + repaint
        try:
            self.refresh_player_view()
        except Exception:
            pass

        try:
            self.scene.update()
        except Exception:
            pass

        try:
            self.view.viewport().update()
        except Exception:
            pass

    def _sync_scene_token_hp_from_state(self, token_id: str) -> None:
        ts = self.state.tokens.get(token_id)
        if not ts:
            return
        tok = self._get_scene_token_item(token_id)
        if not tok:
            return
        tok.hp = int(getattr(ts, "hp", tok.hp) or tok.hp)
        tok.max_hp = int(getattr(ts, "max_hp", tok.max_hp) or tok.max_hp)
        try:
            tok.update_hp_bar()
        except Exception:
            pass

    def _sync_scene_from_state_after_replay(self) -> None:
        """
        After history replay, hard-sync all visible scene tokens from EncounterState.
        This fixes HP bar desync and ensures death sprites/positions match state.
        """
        state_tokens = getattr(self.state, "tokens", {}) or {}

        # 1) Update every scene token item from state
        for item in list(self.scene.items()):
            tid = getattr(item, "token_id", None)
            if not tid:
                continue
            ts = state_tokens.get(tid)
            if not ts:
                continue

            # Position sync (grid -> scene coords)
            try:
                self._apply_state_pos_to_scene_token(tid)
            except Exception:
                pass

            # HP/max_hp sync + bar redraw
            try:
                item.hp = int(getattr(ts, "hp", 0) or 0)
                item.max_hp = int(getattr(ts, "max_hp", 10) or 10)
                if hasattr(item, "update_hp_bar"):
                    item.update_hp_bar()
            except Exception:
                pass

            # Death visual sync (if you have helper, use it; otherwise rely on apply_state_hp_to_scene_token)
            try:
                # If you already have a robust method that swaps sprites when hp<=0, call it:
                self.apply_state_hp_to_scene_token(tid)
            except Exception:
                pass

            # Ensure Qt repaints this item
            try:
                item.update()
            except Exception:
                pass

        # 2) Refresh highlights / HUD / panels / views
        try:
            self._update_active_token_highlight()
        except Exception:
            pass

        try:
            self.update_initiative_panel()
        except Exception:
            pass

        try:
            self.update_combat_hud()
        except Exception:
            pass

        try:
            self.refresh_player_view()
        except Exception:
            pass

        # 3) Force a repaint (this is the part that usually fixes “bar updates only later”)
        try:
            self.scene.update()
        except Exception:
            pass
        try:
            self.view.viewport().update()
        except Exception:
            pass

    def _apply_state_pos_to_scene_token(self, token_id: str) -> None:
        """
        Set scene token position from TokenState without re-triggering move callback.
        """
        ts = self.state.tokens.get(token_id)
        if not ts:
            return
        tok = next((t for t in self.token_items if getattr(t, "token_id", None) == token_id), None)
        if tok is None:
            return
        try:
            tok._suppress_move_callback = True
            tok.setPos(int(ts.grid_x) * self.grid_size, int(ts.grid_y) * self.grid_size)
        except Exception:
            pass
        finally:
            try:
                tok._suppress_move_callback = False
            except Exception:
                pass


    def create_menu(self):
        menubar = self.menuBar()
        file_menu = menubar.addMenu("File")

        #open_map_action = QAction("Load Map", self)
        #open_map_action.triggered.connect(self.load_map)
        #file_menu.addAction(open_map_action)

        #load_token_action = QAction("Add Token", self)
        #load_token_action.triggered.connect(self.add_token)
        #file_menu.addAction(load_token_action)

        save_encounter_action = QAction("Save Encounter", self)
        save_encounter_action.triggered.connect(self.save_encounter)
        file_menu.addAction(save_encounter_action)

        load_encounter_action = QAction("Load Encounter", self)
        load_encounter_action.triggered.connect(self.load_encounter)
        file_menu.addAction(load_encounter_action)

        open_encounter_window = QAction("Open Encounter Manager", self)
        open_encounter_window.triggered.connect(self.open_encounter_manager)
        file_menu.addAction(open_encounter_window)

        clear_tokens_action = QAction("Clear Tokens", self)
        clear_tokens_action.triggered.connect(self.clear_tokens)
        file_menu.addAction(clear_tokens_action)

        view_menu = menubar.addMenu("View")

        tools_menu = menubar.addMenu("Tools")
        open_map_meta_editor_action = QAction("Map Metadata Editor", self)
        open_map_meta_editor_action.triggered.connect(self.open_map_metadata_editor)
        tools_menu.addAction(open_map_meta_editor_action)

        open_handouts_action = QAction("Handouts (DM Push)...", self)
        open_handouts_action.triggered.connect(self.open_handouts_dm_dialog)
        tools_menu.addAction(open_handouts_action)
        sync_sheets_action = QAction("Sync PC Sheets Now", self)
        sync_sheets_action.triggered.connect(self.sync_pc_sheets_now)
        tools_menu.addAction(sync_sheets_action)

        open_player_view_action = QAction("Open Player View", self)
        open_player_view_action.triggered.connect(self.open_player_view)
        view_menu.addAction(open_player_view_action)

        combat_menu = self.menuBar().addMenu("Combat")
        resolve_npc_action = QAction("Resolve NPC Attack (Selected Attacker -> Target)", self)
        resolve_npc_action.triggered.connect(self.resolve_npc_attack_menu)
        combat_menu.addAction(resolve_npc_action)

    def open_map_metadata_editor(self):
        # Lazy-create a dockable editor so the DM can paint walls/blocked/terrain
        if self.map_metadata_dock is None:
            self.map_metadata_editor = MapMetadataEditorWidget(self)
            self.map_metadata_dock = QDockWidget("Map Metadata", self)
            self.map_metadata_dock.setWidget(self.map_metadata_editor)
            self.map_metadata_dock.visibilityChanged.connect(self._on_map_meta_dock_visibility_changed)
            self.addDockWidget(Qt.RightDockWidgetArea, self.map_metadata_dock)
            # enable click dispatch to the map view
            self.map_metadata_editor.enable_on_map(self.map_metadata_editor.is_painting_enabled())
            self.map_metadata_editor.redraw_overlays()
        else:
            self.map_metadata_dock.show()
            try:
                self.map_metadata_editor.enable_on_map(self.map_metadata_editor.is_painting_enabled())
                self.map_metadata_editor.redraw_overlays()
            except Exception:
                pass

    def open_handouts_dm_dialog(self):
        """DM tool: author + store templates + push handouts to players/party."""
        try:
            from ui.handouts_dm_dialog import HandoutsDMPushDialog
        except Exception as e:
            print("[UI] Failed to import HandoutsDMPushDialog:", e)
            return

        dlg = HandoutsDMPushDialog(self, server_client=getattr(self, "server_client", None))
        dlg.exec_()

    def _pick_token_at(self, scene_pos):
        """Return the topmost DraggableToken under scene_pos, ignoring overlay rects."""
        # QGraphicsScene.items returns topmost-first when using DescendingOrder.
        for it in self.scene.items(scene_pos, Qt.IntersectsItemShape, Qt.DescendingOrder, self.view.transform()):
            cur = it
            while cur is not None and not isinstance(cur, DraggableToken):
                cur = cur.parentItem()
            if isinstance(cur, DraggableToken):
                return cur
        return None

    
    def _pick_door_at(self, scene_pos):
        """Return door dict from current_map_meta if cursor is near a door edge."""
        try:
            # Prefer the UI constant; fallback keeps older builds working.
            from ui.constants import GRID_SIZE
        except Exception:
            GRID_SIZE = 64

        meta = getattr(self, "current_map_meta", {}) or {}
        doors = (meta or {}).get("doors", []) or []
        if not isinstance(doors, list) or not doors:
            return None

        try:
            x = float(scene_pos.x())
            y = float(scene_pos.y())
        except Exception:
            return None

        if GRID_SIZE <= 0:
            return None

        gx = int(x // GRID_SIZE)
        gy = int(y // GRID_SIZE)
        if gx < 0 or gy < 0:
            return None

        # local coords in cell [0,1)
        lx = (x / GRID_SIZE) - gx
        ly = (y / GRID_SIZE) - gy

        # distance to each edge
        dN = abs(ly - 0.0)
        dS = abs(1.0 - ly)
        dW = abs(lx - 0.0)
        dE = abs(1.0 - lx)

        best = min([(dN, "N"), (dE, "E"), (dS, "S"), (dW, "W")], key=lambda t: t[0])
        dist, edge = best

        # tolerance: must be close to an edge
        # NOTE: Users right-click roughly near edges; keep this forgiving.
        if dist > 0.24:
            return None

        # Canonicalize: treat each physical edge as a single canonical tuple (N/W representation)
        def _canon(ex: int, ey: int, ed: str):
            ed = str(ed or "N").upper().strip()
            if ed == "S":
                return (ex, ey + 1, "N")
            if ed == "E":
                return (ex + 1, ey, "W")
            if ed == "N":
                return (ex, ey, "N")
            return (ex, ey, "W")

        clicked = _canon(gx, gy, edge)

        for d0 in doors:

            if not isinstance(d0, dict):
                continue
            try:
                dx = int(d0.get("x"))
                dy = int(d0.get("y"))
                de = str(d0.get("edge", d0.get("dir", "N"))).upper().strip()
            except Exception:
                continue
                        # Compare canonical edge tuples so authoring may use N/W or S/E forms.
            if _canon(dx, dy, de) == clicked:
                return d0
        return None

    def set_door_state(self, door_id: str, *, is_open: bool) -> None:
        """BX2: Set door open/closed state (encounter override) with deterministic history."""
        door_id = str(door_id or "").strip()
        if not door_id:
            return

        if not hasattr(self.state, "door_state") or self.state.door_state is None:
            self.state.door_state = {}

        # If multiple door segments are directly adjacent (e.g. double doors),
        # set them together for usability.
        meta = getattr(self, "current_map_meta", {}) or {}
        doors = (meta or {}).get("doors", []) or []
        ds = self.state.door_state

        def _canon(ex: int, ey: int, ed: str):
            ed = str(ed or "N").upper().strip()
            if ed == "S":
                return (ex, ey + 1, "N")
            if ed == "E":
                return (ex + 1, ey, "W")
            if ed == "N":
                return (ex, ey, "N")
            return (ex, ey, "W")

        def _edge_endpoints(t):
            # t is canonical (x,y,'N' or 'W') in grid-space; endpoints are grid vertices.
            x, y, d = t
            if d == "N":
                return ((x, y), (x + 1, y))
            # W
            return ((x, y), (x, y + 1))

        # Find canonical tuple for the clicked door_id
        target_t = None
        for dd in doors:
            if not isinstance(dd, dict):
                continue
            try:
                if str(dd.get("id") or "").strip() != door_id:
                    continue
                dx = int(dd.get("x"))
                dy = int(dd.get("y"))
                de = str(dd.get("edge", dd.get("dir", "N"))).upper().strip()
                target_t = _canon(dx, dy, de)
                break
            except Exception:
                continue

        group_ids = [door_id]
        if target_t is not None:
            # BFS across doors that share an endpoint and are collinear (directly adjacent)
            target_orient = target_t[2]  # 'N' or 'W'
            visited = set([target_t])
            frontier = [target_t]

            # Build door lookup by canonical tuple -> id
            canon_to_ids = {}
            for dd in doors:
                if not isinstance(dd, dict):
                    continue
                try:
                    did = str(dd.get("id") or "").strip()
                    if not did:
                        continue
                    cx = int(dd.get("x"))
                    cy = int(dd.get("y"))
                    ce = str(dd.get("edge", dd.get("dir", "N"))).upper().strip()
                    ct = _canon(cx, cy, ce)
                    canon_to_ids.setdefault(ct, []).append(did)
                except Exception:
                    continue

            while frontier:
                cur = frontier.pop()
                a, b = _edge_endpoints(cur)

                # Adjacent edges of same orientation share an endpoint and extend linearly by 1 cell.
                # For horizontal (N): neighbors at (x-1,y,N) and (x+1,y,N)
                # For vertical (W): neighbors at (x,y-1,W) and (x,y+1,W)
                x, y, d = cur
                neighs = []
                if d == "N":
                    neighs = [(x - 1, y, "N"), (x + 1, y, "N")]
                else:  # 'W'
                    neighs = [(x, y - 1, "W"), (x, y + 1, "W")]

                for nt in neighs:
                    if nt in visited:
                        continue
                    if nt in canon_to_ids:
                        visited.add(nt)
                        frontier.append(nt)

            # Collect ids for all connected door segments in the run
            for ct in visited:
                for did in canon_to_ids.get(ct, []):
                    if did not in group_ids:
                        group_ids.append(did)

        # Apply state to group
        for did in group_ids:
            ds[str(did)] = bool(is_open)

        try:
            print(f"[DOOR] SET {door_id} -> {'OPEN' if bool(is_open) else 'CLOSED'} (group={len(group_ids)})")
        except Exception:
            pass

        # Deterministic history events (SET, not TOGGLE) -- record each door in the group
        try:
            for did in list(group_ids):
                self._history_append_and_apply({"type": "DOOR_SET", "door_id": str(did), "is_open": bool(is_open)})
        except Exception:
            # Fallback: state already mutated above
            pass

        # Campaign log (for human-readable trace)
        try:
            self.campaign_logger.event("DOOR_SET", door_id=str(door_id), is_open=bool(is_open))
        except Exception:
            pass

        # Refresh visuals
        try:
            self.refresh_player_view()
        except Exception:
            pass
        try:
            self.redraw_overlays()
        except Exception:
            pass

    def show_context_menu(self, position):
        scene_pos = self.view.mapToScene(position)
        item = self._pick_token_at(scene_pos)
        door = None
        map_click = False
        if not item:
            door = self._pick_door_at(scene_pos)
            if not door:
                map_click = True

        menu = QMenu(self)

        # BX5.2: Map context menu (spawn/clear runtime fog zones)
        if map_click:
            try:
                gx = int(scene_pos.x() // GRID_SIZE)
                gy = int(scene_pos.y() // GRID_SIZE)
            except Exception:
                return
            spawn_smoke = menu.addAction("Spawn Smoke Cloud (r=3, 3 turns)")
            spawn_gas = menu.addAction("Spawn Gas Cloud (r=5, 5 turns)")
            spawn_poison = menu.addAction("Spawn Poison Gas Cloud (r=5, 5 turns, 2d6 @ turn start)")
            menu.addSeparator()
            clear_clouds = menu.addAction("Clear All Clouds")
            chosen = menu.exec_(self.view.mapToGlobal(position))
            if chosen is None:
                return
            if not hasattr(self.state, "runtime_fog_zones") or self.state.runtime_fog_zones is None:
                self.state.runtime_fog_zones = []
            if chosen == clear_clouds:
                self.state.runtime_fog_zones = []
                try:
                    print("[CLOUD] Cleared all runtime clouds")
                except Exception:
                    pass
                try:
                    self.campaign_logger.event("CLOUD_CLEAR_ALL")
                except Exception:
                    pass
                self.refresh_player_view()
                return
            if chosen == spawn_smoke:
                self._spawn_runtime_cloud(kind="smoke", cx=gx, cy=gy, r=3, density=0.8, ttl_turns=3)
                self._redraw_runtime_cloud_overlays()
                try:
                    print(f"[CLOUD] Spawn smoke at ({gx},{gy}) r=3 ttl=3")
                except Exception:
                    pass
                try:
                    self.campaign_logger.event("CLOUD_SPAWN", kind="smoke", gx=int(gx), gy=int(gy), r=3, ttl_turns=3)
                except Exception:
                    pass
                self.refresh_player_view()
                return
            if chosen == spawn_gas:
                self._spawn_runtime_cloud(kind="gas", cx=gx, cy=gy, r=5, density=0.9, ttl_turns=5)
                self._redraw_runtime_cloud_overlays()
                try:
                    print(f"[CLOUD] Spawn gas at ({gx},{gy}) r=5 ttl=5")
                except Exception:
                    pass
                try:
                    self.campaign_logger.event("CLOUD_SPAWN", kind="gas", gx=int(gx), gy=int(gy), r=5, ttl_turns=5)
                except Exception:
                    pass
                self.refresh_player_view()
                return

            if chosen == spawn_poison:
                self._spawn_runtime_cloud(
                    kind="poison_gas",
                    cx=gx,
                    cy=gy,
                    r=5,
                    density=0.95,
                    ttl_turns=5,
                    extra={
                        "damage": "2d6",
                        "damage_type": "poison",
                        "triggers": ["turn_start"],
                        "save_ability": "CON",
                        "save_dc": 13,
                        "save_mode": "normal",
                        "save_on_success": "half"
                    },
                )
                self._redraw_runtime_cloud_overlays()
                try:
                    print(f"[CLOUD] Spawn poison gas at ({gx},{gy}) r=5 ttl=5 dmg=2d6 trigger=turn_start")
                except Exception:
                    pass
                try:
                    self.campaign_logger.event(
                        "CLOUD_SPAWN",
                        kind="poison_gas",
                        gx=int(gx),
                        gy=int(gy),
                        r=5,
                        ttl_turns=5,
                        damage_expr="2d6",
                        trigger="turn_start",
                        damage_type="poison",
                    )
                except Exception:
                    pass
                self.refresh_player_view()
                return


        # BX2: Door context menu when right-clicking near a door edge
        if door is not None:
            door_id = str(door.get("id") or "").strip()
            if not door_id:
                return
            # Determine effective open state (encounter override wins)
            ds = getattr(self.state, "door_state", {}) or {}
            is_open = bool(ds.get(door_id, door.get("is_open", False)))
            act = menu.addAction("Close Door" if is_open else "Open Door")
            menu.addSeparator()
            chosen = menu.exec_(self.view.mapToGlobal(position))
            if chosen == act:
                self.set_door_state(door_id, is_open=not is_open)
            return


        set_attacker = menu.addAction("Set as Attacker")
        set_target = menu.addAction("Set as Target (auto-resolve)")
        menu.addSeparator()

        delete_action = menu.addAction("Delete Token")
        heal_full_action = menu.addAction("Heal Selected PC to Full (DM)")
        request_save_action = menu.addAction("Request Manual Saving Throw…")
        apply_condition_action = menu.addAction("Apply Condition…")
        clear_conditions_action = menu.addAction("Clear Conditions")

        menu.addSeparator()

        # ---- Cover override submenu (DM) ----
        cover_actions = {}  # QAction -> tier
        cover_menu = menu.addMenu("Cover Override")

        def _add_cover(label: str, tier: str):
            act = cover_menu.addAction(label)
            cover_actions[act] = tier

        _add_cover("Clear", "none")
        cover_menu.addSeparator()
        _add_cover("Half (+2 AC)", "half")
        _add_cover("3/4 (+5 AC)", "three_quarters")
        cover_menu.addSeparator()
        _add_cover("Total (Duck / Hidden)", "total")

        # ---- OPTIONAL: Spells submenu (only if you want it here) ----
        spell_actions = {}
        spells = getattr(self, "spells_db", {}) or {}
        if spells:
            menu.addSeparator()
            cast_menu = menu.addMenu("Cast Spell (attack roll)")
            try:
                spell_items = sorted(spells.items(), key=lambda kv: str((kv[1] or {}).get("name", kv[0])).lower())
            except Exception:
                spell_items = list(spells.items())

            for sid, sd in spell_items:
                try:
                    targeting = str((sd or {}).get("target_mode") or (((sd or {}).get("targeting") or {}).get("kind")) or (sd or {}).get("targeting", "") or "").strip().lower()
                    if targeting and targeting not in {"attack_roll", "attack"}:
                        continue
                    label = str((sd or {}).get("name", sid))
                    act = cast_menu.addAction(label)
                    spell_actions[act] = sid
                except Exception:
                    continue

        # ---- EXECUTE ONCE ----
        action = menu.exec_(self.view.mapToGlobal(position))
        if action is None:
            return
        
        # ---- Cover override ----
        if action in cover_actions:
            tier = cover_actions.get(action, "none")
            try:
                self.on_token_cover_override(item, getattr(item, "token_id", ""), tier)
            except Exception:
                pass
            self.update_combat_hud()
            return

        # ---- Heal: ONLY runs if you clicked that menu item ----
        if action == heal_full_action:
            self.ui_heal_selected_pc_to_full(tok_item=item)
            return

        if action == request_save_action:
            self.ui_request_saving_throw(item)
            return

        if action == apply_condition_action:
            self.ui_apply_condition(item)
            return

        if action == clear_conditions_action:
            self.ui_clear_conditions(item)
            return

        # ---- Spell selection ----
        if action in spell_actions:
            spell_id = spell_actions.get(action, "")
            attacker_id = getattr(self, "_armed_attacker_id", None)
            if not attacker_id or attacker_id not in self.state.tokens:
                self._set_hud_status("Set an attacker first, then choose a spell.", hold_sec=3.0)
                self.update_combat_hud()
                return

            caster_ts = self.state.tokens.get(attacker_id)
            target_ts = self.state.tokens.get(getattr(item, "token_id", ""))
            if not caster_ts or not target_ts:
                self._set_hud_status("Caster/target missing.", hold_sec=3.0)
                return

            if getattr(caster_ts, "side", "") != "player" and getattr(caster_ts, "kind", "") != "pc":
                self._set_hud_status("Only PCs can be spell casters in this flow (v1).", hold_sec=3.5)
                return

            if self.state.pending_attack:
                self._clear_pending_local_and_server()

            self._armed_target_id = target_ts.token_id
            self._arm_pending_pc_spell_attack(caster_ts, target_ts, spell_id)
            return

        # ---- Delete ----
        if action == delete_action:
            self.scene.removeItem(item)
            if item in self.token_items:
                self.token_items.remove(item)
            if item.token_id in self.state.tokens:
                del self.state.tokens[item.token_id]
            self.refresh_player_view()
            return

        # ---- Set attacker ----
        if action == set_attacker:
            if getattr(self, "_armed_attacker_id", None) != item.token_id:
                self._clear_pending_local_and_server()
            self._armed_attacker_id = item.token_id
            self._armed_target_id = None
            print(f"[ARM] Attacker set to {getattr(item,'display_name','?')} ({item.token_id})")
            self.update_combat_hud()
            return

        # ---- Set target ----
        if action == set_target:
            if getattr(self, "_armed_target_id", None) != item.token_id:
                self._clear_pending_local_and_server()
            self._armed_target_id = item.token_id
            print(f"[ARM] Target set to {getattr(item,'display_name','?')} ({item.token_id})")
            self._auto_resolve_if_ready()
            self.update_combat_hud()
            return

    def _is_player_controlled_token(self, ts) -> bool:
        return bool((getattr(ts, "player_id", "") or "").strip() and ((getattr(ts, "kind", "") == "pc") or (getattr(ts, "side", "") == "player")))

    def _prompt_save_request_inputs(self, default_label: str = "Manual Saving Throw") -> dict | None:
        ability, ok = QInputDialog.getItem(
            self,
            "Manual Saving Throw",
            "Ability:",
            ["STR", "DEX", "CON", "INT", "WIS", "CHA"],
            1,
            False,
        )
        if not ok:
            return None

        dc, ok = QInputDialog.getInt(self, "Saving Throw", "DC:", 10, 1, 99, 1)
        if not ok:
            return None

        mode, ok = QInputDialog.getItem(
            self,
            "Saving Throw",
            "Roll mode:",
            ["normal", "advantage", "disadvantage"],
            0,
            False,
        )
        if not ok:
            return None

        label, ok = QInputDialog.getText(self, "Saving Throw", "Label:", text=str(default_label or "Saving Throw"))
        if not ok:
            return None

        return {
            "ability": normalize_ability_key(ability),
            "dc": int(dc),
            "adv_mode": str(mode or "normal"),
            "label": str(label or default_label or "Saving Throw").strip() or "Saving Throw",
        }

    def _prompt_condition_inputs(self) -> dict | None:
        name, ok = QInputDialog.getItem(
            self,
            "Apply Condition",
            "Condition:",
            ["Poisoned", "Blinded", "Restrained", "Prone", "Stunned", "Charmed"],
            0,
            False,
        )
        if not ok:
            return None

        rounds, ok = QInputDialog.getInt(self, "Apply Condition", "Duration in rounds (0 = indefinite):", 0, 0, 99, 1)
        if not ok:
            return None

        save_ability, ok = QInputDialog.getItem(
            self,
            "Apply Condition",
            "Auto save to end condition:",
            ["None", "STR", "DEX", "CON", "INT", "WIS", "CHA"],
            0,
            False,
        )
        if not ok:
            return None

        payload = {
            "name": str(name or "").strip().lower(),
            "rounds_remaining": int(rounds) if int(rounds) > 0 else None,
            "source": "dm_applied",
        }

        if str(save_ability or "None").upper() != "NONE":
            dc, ok = QInputDialog.getInt(self, "Apply Condition", "Save DC:", 10, 1, 99, 1)
            if not ok:
                return None
            save_mode, ok = QInputDialog.getItem(
                self,
                "Apply Condition",
                "Save mode:",
                ["normal", "advantage", "disadvantage"],
                0,
                False,
            )
            if not ok:
                return None
            save_timing, ok = QInputDialog.getItem(
                self,
                "Apply Condition",
                "Save timing:",
                ["end", "start"],
                0,
                False,
            )
            if not ok:
                return None
            payload["save"] = {
                "ability": str(save_ability or "").strip().upper(),
                "dc": int(dc),
                "mode": str(save_mode or "normal"),
                "timing": str(save_timing or "end"),
                "auto": True,
            }

        tick_amount, ok = QInputDialog.getInt(self, "Apply Condition", "Tick damage each turn (0 = none):", 0, 0, 999, 1)
        if not ok:
            return None
        if int(tick_amount) > 0:
            tick_timing, ok = QInputDialog.getItem(
                self,
                "Apply Condition",
                "Tick damage timing:",
                ["start", "end"],
                0,
                False,
            )
            if not ok:
                return None
            payload["tick_damage"] = {
                "amount": int(tick_amount),
                "timing": str(tick_timing or "start"),
                "damage_type": str(payload.get("name", "") or ""),
            }

        return payload

    def ui_apply_condition(self, tok_item) -> None:
        token_id = str(getattr(tok_item, "token_id", "") or "")
        ts = self.state.tokens.get(token_id)
        if not ts:
            return
        payload = self._prompt_condition_inputs()
        if not payload:
            return
        try:
            cond = canonical_condition_record(payload)
        except Exception as e:
            QMessageBox.warning(self, "Apply Condition", f"Invalid condition: {e}")
            return
        current = [s for s in list(getattr(ts, "statuses", []) or []) if isinstance(s, dict)]
        current.append(cond)
        ts.statuses = current
        try:
            self.campaign_logger.combat(
                "condition_applied",
                token_id=token_id,
                token_name=str(getattr(ts, "display_name", token_id) or token_id),
                condition_id=str(cond.get("condition_id", "") or ""),
                condition_name=str(cond.get("name", "condition") or "condition"),
                rounds_remaining=cond.get("rounds_remaining", None),
                save=dict(cond.get("save", {}) or {}),
                tick_damage=dict(cond.get("tick_damage", {}) or {}),
            )
        except Exception:
            pass
        self._sync_token_statuses_to_sheet(ts)
        self._refresh_condition_movement_state(ts)
        self._set_hud_status(f"Applied {str(cond.get('name', 'condition')).title()} to {getattr(ts, 'display_name', token_id)}.", hold_sec=2.5)
        self.update_combat_hud()

    def _sync_temp_hp_to_sheet(self, ts) -> None:
        try:
            if ts is None or not self._is_player_controlled_token(ts):
                return
            character_id = str(getattr(ts, "character_id", "") or "").strip()
            if not character_id or not hasattr(self, "server") or self.server is None:
                return
            sheet = self.server.get_character_sheet(character_id) or {}
            if not isinstance(sheet, dict) or not sheet:
                return
            resources = sheet.setdefault("resources", {}) if isinstance(sheet.get("resources"), dict) else {}
            sheet["resources"] = resources
            resources["temp_hp"] = max(0, int(getattr(ts, "temp_hp", 0) or 0))
            self.server.upsert_character_sheet(character_id, sheet)
        except Exception as e:
            print("[SPELL] temp hp sync failed:", e)

    def _sync_spell_modified_stats_to_sheet(self, ts) -> None:
        try:
            if ts is None or not self._is_player_controlled_token(ts):
                return
            character_id = str(getattr(ts, "character_id", "") or "").strip()
            if not character_id or not hasattr(self, "server") or self.server is None:
                return
            sheet = self.server.get_character_sheet(character_id) or {}
            if not isinstance(sheet, dict) or not sheet:
                return
            stats = sheet.setdefault("stats", {}) if isinstance(sheet.get("stats"), dict) else {}
            sheet["stats"] = stats
            stats["defense"] = int(getattr(ts, "ac", 0) or 0)
            stats["movement_ft"] = int(getattr(ts, "base_movement", getattr(ts, "movement", 0)) or 0)
            stats["vision_ft"] = int(getattr(ts, "vision_ft", 0) or 0)
            stats["attack_modifier"] = int(getattr(ts, "attack_modifier", 0) or 0)
            self.server.upsert_character_sheet(character_id, sheet)
        except Exception as e:
            print("[SPELL] modified stat sync failed:", e)

    def _spell_modifier_label(self, effect: Dict[str, Any], spell_name: str) -> str:
        field_name = str(effect.get("field") or effect.get("stat") or effect.get("target") or effect.get("name") or "bonus").strip()
        return f"{spell_name} ({field_name})" if field_name else spell_name

    def _apply_spell_numeric_modifier(self, ts, *, field: str, amount: int, apply_now: bool = True) -> bool:
        try:
            field_key = str(field or "").strip().lower()
            amount = int(amount or 0)
            if ts is None or not field_key or amount == 0:
                return False
            delta = amount if apply_now else -amount
            if field_key in {"ac", "defense", "armor_class"}:
                setattr(ts, "ac", int(getattr(ts, "ac", 0) or 0) + delta)
                return True
            if field_key in {"attack", "attack_bonus", "attack_modifier", "spell_attack_bonus"}:
                setattr(ts, "attack_modifier", int(getattr(ts, "attack_modifier", 0) or 0) + delta)
                return True
            if field_key in {"movement", "movement_ft", "speed", "base_movement"}:
                current_base = int(getattr(ts, "base_movement", getattr(ts, "movement", 0)) or 0)
                current_move = int(getattr(ts, "movement", current_base) or current_base)
                setattr(ts, "base_movement", max(0, current_base + delta))
                setattr(ts, "movement", max(0, current_move + delta))
                if getattr(ts, "movement_remaining", None) is not None:
                    setattr(ts, "movement_remaining", max(0, int(getattr(ts, "movement_remaining", 0) or 0) + delta))
                return True
            if field_key in {"vision", "vision_ft", "light_radius", "bright_light_ft"}:
                current = int(getattr(ts, "vision_ft", 0) or 0)
                setattr(ts, "vision_ft", max(0, current + delta))
                return True
            if field_key in {"darkvision", "darkvision_ft"}:
                current_dark = int(getattr(ts, "darkvision_ft", 0) or 0)
                setattr(ts, "darkvision_ft", max(0, current_dark + delta))
                return True
            return False
        except Exception:
            return False

    def _apply_spell_save_bonus_modifier(self, ts, *, ability: str, amount: int, apply_now: bool = True) -> bool:
        try:
            if ts is None:
                return False
            key = normalize_ability_key(ability)
            amount = int(amount or 0)
            if not key or amount == 0:
                return False
            cur = dict(getattr(ts, "save_bonus", {}) or {}) if isinstance(getattr(ts, "save_bonus", {}), dict) else {}
            delta = amount if apply_now else -amount
            cur[key] = int(cur.get(key, 0) or 0) + delta
            if int(cur.get(key, 0) or 0) == 0:
                cur.pop(key, None)
            setattr(ts, "save_bonus", cur)
            return True
        except Exception:
            return False

    def _cleanup_spell_status_effect(self, ts, raw: Dict[str, Any]) -> None:
        try:
            if ts is None or not isinstance(raw, dict):
                return
            meta = dict(raw.get("meta") or {}) if isinstance(raw.get("meta"), dict) else {}
            effect_kind = str(meta.get("effect_kind") or "").strip().lower()
            if effect_kind not in {"modifier", "light"}:
                return
            mods = list(meta.get("modifiers") or []) if isinstance(meta.get("modifiers"), list) else []
            changed = False
            for spec in mods:
                if not isinstance(spec, dict):
                    continue
                field = str(spec.get("field") or "").strip().lower()
                amount = int(spec.get("amount", 0) or 0)
                if field.startswith("save_bonus"):
                    ab = str(spec.get("ability") or field.split(".", 1)[1] if "." in field else "").strip().lower()
                    changed = self._apply_spell_save_bonus_modifier(ts, ability=ab, amount=amount, apply_now=False) or changed
                else:
                    changed = self._apply_spell_numeric_modifier(ts, field=field, amount=amount, apply_now=False) or changed
            if changed:
                try:
                    self._sync_spell_modified_stats_to_sheet(ts)
                except Exception:
                    pass
                try:
                    self.refresh_player_view()
                except Exception:
                    pass
        except Exception as e:
            print("[SPELL] cleanup status effect failed:", e)

    def _remove_spell_linked_effects(self, caster_ts, *, spell_id: str = "", spell_name: str = "") -> int:
        removed = 0
        caster_token_id = str(getattr(caster_ts, "token_id", "") or "").strip().lower() if caster_ts is not None else ""
        for ts in list((getattr(self.state, "tokens", {}) or {}).values()):
            statuses = [canonical_condition_record(s) for s in list(getattr(ts, "statuses", []) or []) if isinstance(s, dict)]
            if not statuses:
                continue
            kept = []
            local_removed = 0
            for raw in statuses:
                src = str(raw.get("source", "") or "").strip().lower()
                meta = dict(raw.get("meta") or {}) if isinstance(raw.get("meta"), dict) else {}
                raw_spell_id = str(meta.get("spell_id", raw.get("spell_id", "")) or "").strip().lower()
                raw_spell_name = str(meta.get("spell_name", "") or "").strip().lower()
                raw_caster = str(meta.get("caster_token_id", "") or "").strip().lower()
                if spell_id and raw_spell_id == spell_id.lower() and (not caster_token_id or raw_caster in {"", caster_token_id}):
                    self._cleanup_spell_status_effect(ts, raw)
                    local_removed += 1
                    continue
                if spell_name and (src == spell_name.lower() or raw_spell_name == spell_name.lower()) and (not caster_token_id or raw_caster in {"", caster_token_id}):
                    self._cleanup_spell_status_effect(ts, raw)
                    local_removed += 1
                    continue
                kept.append(raw)
            if local_removed:
                ts.statuses = kept
                self._refresh_condition_movement_state(ts)
                self._sync_token_statuses_to_sheet(ts)
                removed += local_removed
        return removed

    def _tick_status_durations_for_token(self, ts) -> None:
        try:
            if ts is None:
                return
            statuses = [canonical_condition_record(s) for s in list(getattr(ts, "statuses", []) or []) if isinstance(s, dict)]
            if not statuses:
                return
            kept = []
            expired = []
            for raw in statuses:
                rounds = raw.get("rounds_remaining", None)
                if rounds is None:
                    kept.append(raw)
                    continue
                try:
                    rounds = int(rounds) - 1
                except Exception:
                    rounds = None
                if rounds is None or rounds <= 0:
                    self._cleanup_spell_status_effect(ts, raw)
                    expired.append(str(raw.get("name") or raw.get("condition") or "condition"))
                    continue
                raw["rounds_remaining"] = int(rounds)
                kept.append(raw)
            if len(kept) != len(statuses):
                ts.statuses = kept
                self._refresh_condition_movement_state(ts)
                self._sync_token_statuses_to_sheet(ts)
                try:
                    self.campaign_logger.combat("spell_status_expired", token_id=str(getattr(ts, "token_id", "") or ""), token_name=str(getattr(ts, "display_name", getattr(ts, "token_id", "Token")) or "Token"), expired=list(expired or []))
                except Exception:
                    pass
        except Exception as e:
            print("[SPELL] status duration tick failed:", e)

    def _clear_spell_concentration(self, caster_ts, *, reason: str = "", remove_linked_effects: bool = True) -> bool:
        try:
            if caster_ts is None or not bool(getattr(caster_ts, "concentration_active", False)):
                return False
            spell_id = str(getattr(caster_ts, "concentration_spell_id", "") or "").strip()
            spell_name = str(getattr(caster_ts, "concentration_spell_name", spell_id or "Concentration") or "Concentration").strip()
            setattr(caster_ts, "concentration_active", False)
            setattr(caster_ts, "concentration_spell_id", "")
            setattr(caster_ts, "concentration_spell_name", "")
            setattr(caster_ts, "concentration_rounds_remaining", None)
            removed = self._remove_spell_linked_effects(caster_ts, spell_id=spell_id, spell_name=spell_name) if remove_linked_effects else 0
            self._sync_sheet_spellcasting_concentration(caster_ts, spell_id="", active=False, source=str(reason or "ended"), rounds_remaining=None)
            try:
                self.campaign_logger.combat("spell_concentration_ended", caster_token_id=str(getattr(caster_ts, "token_id", "") or ""), caster_name=str(getattr(caster_ts, "display_name", getattr(caster_ts, "token_id", "Caster")) or "Caster"), spell_id=spell_id, spell_name=spell_name, reason=str(reason or "ended"), removed_effects=int(removed or 0))
            except Exception:
                pass
            return True
        except Exception as e:
            print("[SPELL] failed to clear concentration:", e)
            return False

    def _maybe_open_concentration_save_after_damage(self, target_ts, damage_amount: int, *, source_kind: str = "") -> None:
        try:
            if target_ts is None or int(damage_amount or 0) <= 0:
                return
            if not bool(getattr(target_ts, "concentration_active", False)):
                return
            dc = max(10, int(int(damage_amount) // 2))
            context = {
                "kind": "concentration_save",
                "source_kind": str(source_kind or "damage"),
                "spell_id": str(getattr(target_ts, "concentration_spell_id", "") or ""),
                "spell_name": str(getattr(target_ts, "concentration_spell_name", "") or ""),
            }
            deferred = {
                "concentration_check": True,
            }
            if self._is_player_controlled_token(target_ts):
                req_id = self._register_pc_deferred_damage_save_request(
                    target_ts,
                    ability="CON",
                    dc=int(dc),
                    mode="normal",
                    label="Concentration Save",
                    context=context,
                    deferred_effect=deferred,
                )
            else:
                req_id = f"npc-concentration:{uuid.uuid4().hex[:10]}"
                self._resolve_npc_save_request(target_ts, {
                    "ability": "CON",
                    "dc": int(dc),
                    "adv_mode": "normal",
                    "label": "Concentration Save",
                    "context": context,
                    "deferred_effect": deferred,
                })
            try:
                self.campaign_logger.combat("spell_concentration_save_requested", request_id=str(req_id), token_id=str(getattr(target_ts, "token_id", "") or ""), token_name=str(getattr(target_ts, "display_name", getattr(target_ts, "token_id", "Token")) or "Token"), dc=int(dc), damage=int(damage_amount), spell_id=str(getattr(target_ts, "concentration_spell_id", "") or ""), spell_name=str(getattr(target_ts, "concentration_spell_name", "") or ""), target_is_pc=bool(self._is_player_controlled_token(target_ts)))
            except Exception:
                pass
        except Exception as e:
            print("[SPELL] concentration save hook failed:", e)

    def _tick_spell_state_for_token(self, token_id: str, *, timing: str) -> None:
        try:
            ts = self.state.tokens.get(str(token_id or ""))
            if not ts:
                return
            if str(timing or "") != "end":
                return
            self._tick_status_durations_for_token(ts)
            rounds_remaining = getattr(ts, "concentration_rounds_remaining", None)
            if bool(getattr(ts, "concentration_active", False)) and rounds_remaining is not None:
                try:
                    rounds_remaining = int(rounds_remaining) - 1
                except Exception:
                    rounds_remaining = None
                if rounds_remaining is None or rounds_remaining <= 0:
                    self._clear_spell_concentration(ts, reason="duration_expired")
                else:
                    setattr(ts, "concentration_rounds_remaining", int(rounds_remaining))
                    self._sync_sheet_spellcasting_concentration(ts, spell_id=str(getattr(ts, "concentration_spell_id", "") or ""), active=True, source="dm_engine", rounds_remaining=int(rounds_remaining))
                    try:
                        self.campaign_logger.combat("spell_concentration_ticked", token_id=str(getattr(ts, "token_id", "") or ""), token_name=str(getattr(ts, "display_name", getattr(ts, "token_id", "Token")) or "Token"), spell_id=str(getattr(ts, "concentration_spell_id", "") or ""), spell_name=str(getattr(ts, "concentration_spell_name", "") or ""), rounds_remaining=int(rounds_remaining))
                    except Exception:
                        pass
        except Exception as e:
            print("[SPELL] duration tick failed:", e)

    def _sync_sheet_spellcasting_concentration(self, ts, *, spell_id: str = "", active: bool = False, source: str = "", rounds_remaining=None) -> None:
        try:
            if not self._is_player_controlled_token(ts):
                return
            character_id = str(getattr(ts, "character_id", "") or "").strip()
            if not character_id or not hasattr(self, "server") or self.server is None:
                return
            sheet = self.server.get_character_sheet(character_id) or {}
            if not isinstance(sheet, dict) or not sheet:
                return
            if active:
                start_concentration(sheet, spell_id=str(spell_id or ""), source=str(source or "spell"), rounds_remaining=rounds_remaining)
            else:
                clear_concentration(sheet, reason=str(source or ""))
            self.server.upsert_character_sheet(character_id, sheet)
        except Exception as e:
            print("[SPELL] concentration sync failed:", e)

    def _begin_spell_concentration(self, caster_ts, payload: Dict[str, Any]) -> None:
        try:
            if caster_ts is None or not bool((payload or {}).get("concentration", False)):
                return
            spell_id = str((payload or {}).get("spell_id") or "").strip()
            spell_name = str((payload or {}).get("spell_name") or spell_id or "Spell").strip()
            if bool(getattr(caster_ts, "concentration_active", False)):
                self._clear_spell_concentration(caster_ts, reason="replaced")
            rounds_remaining = parse_rounds_from_duration((payload or {}).get("duration"))
            setattr(caster_ts, "concentration_active", True)
            setattr(caster_ts, "concentration_spell_id", spell_id)
            setattr(caster_ts, "concentration_spell_name", spell_name)
            setattr(caster_ts, "concentration_rounds_remaining", rounds_remaining)
            self._sync_sheet_spellcasting_concentration(caster_ts, spell_id=spell_id, active=True, source="dm_engine", rounds_remaining=rounds_remaining)
            self._sync_token_statuses_to_sheet(caster_ts)
            try:
                self.campaign_logger.combat("spell_concentration_started", caster_token_id=str(getattr(caster_ts, "token_id", "") or ""), caster_name=str(getattr(caster_ts, "display_name", getattr(caster_ts, "token_id", "Caster")) or "Caster"), spell_id=spell_id, spell_name=spell_name, rounds_remaining=rounds_remaining)
            except Exception:
                pass
        except Exception as e:
            print("[SPELL] failed to begin concentration:", e)

    def _apply_spell_effect_to_target(self, caster_ts, target_ts, effect: Dict[str, Any], payload: Dict[str, Any]) -> bool:
        if target_ts is None or not isinstance(effect, dict):
            return False
        eff_type = normalize_effect_type(effect.get("type"))
        if not eff_type:
            return False
        spell_id = str((payload or {}).get("spell_id") or "").strip()
        spell_name = str((payload or {}).get("spell_name") or spell_id or eff_type.title()).strip()
        source_meta = {"source_kind": "spell", "is_spell": True, "magical": True, "tags": ["spell", "magical"]}
        if eff_type == "heal":
            expr = str(effect.get("expr") or "").strip()
            amount, _, _ = self._roll_spell_damage_total(expr)
            if amount <= 0:
                return False
            old_hp = int(getattr(target_ts, "hp", 0) or 0)
            max_hp = int(getattr(target_ts, "max_hp", old_hp) or old_hp)
            target_ts.hp = min(max_hp, old_hp + int(amount))
            if int(target_ts.hp) > 0 and str(getattr(target_ts, "death_state", "alive") or "alive") != "alive":
                target_ts.death_state = "alive"
                target_ts.death_save_successes = 0
                target_ts.death_save_failures = 0
            try:
                self._sync_death_state_to_sheet(target_ts)
                self._sync_token_statuses_to_sheet(target_ts)
                self.redraw_token_hp_bar(str(getattr(target_ts, "token_id", "") or ""))
                self.refresh_player_view()
            except Exception:
                pass
            try:
                self.campaign_logger.combat("spell_healing_applied", spell_id=spell_id, spell_name=spell_name, caster_token_id=str(getattr(caster_ts, "token_id", "") or ""), target_token_id=str(getattr(target_ts, "token_id", "") or ""), target_name=str(getattr(target_ts, "display_name", getattr(target_ts, "token_id", "Target")) or "Target"), amount=int(amount), expr=expr)
            except Exception:
                pass
            return True
        if eff_type == "temp_hp":
            expr = str(effect.get("expr") or "").strip()
            amount, _, _ = self._roll_spell_damage_total(expr)
            if amount <= 0:
                return False
            current = max(0, int(getattr(target_ts, "temp_hp", 0) or 0))
            new_amount = max(current, int(amount))
            setattr(target_ts, "temp_hp", new_amount)
            self._sync_temp_hp_to_sheet(target_ts)
            try:
                self.refresh_player_view()
            except Exception:
                pass
            try:
                self.campaign_logger.combat("spell_temp_hp_applied", spell_id=spell_id, spell_name=spell_name, caster_token_id=str(getattr(caster_ts, "token_id", "") or ""), target_token_id=str(getattr(target_ts, "token_id", "") or ""), target_name=str(getattr(target_ts, "display_name", getattr(target_ts, "token_id", "Target")) or "Target"), amount=int(new_amount), replaced=int(current), expr=expr)
            except Exception:
                pass
            return True
        if eff_type == "damage":
            expr = str(effect.get("expr") or "").strip()
            amount, _, _ = self._roll_spell_damage_total(expr)
            if amount <= 0:
                return False
            self.apply_damage_to_token(target_ts, int(amount), encounter_id=getattr(self, "encounter_id", ""), pending_attack_id=f"spell:{spell_id}:{uuid.uuid4().hex[:8]}", damage_type=str(effect.get("damage_type") or effect.get("type_name") or ""), source_kind="spell", source_meta=source_meta)
            return True
        if eff_type == "condition_apply":
            cond_name = str(effect.get("name") or effect.get("condition") or "").strip()
            if not cond_name:
                return False
            meta = {
                "spell_id": spell_id,
                "spell_name": spell_name,
                "caster_token_id": str(getattr(caster_ts, "token_id", "") or ""),
                "concentration": bool((payload or {}).get("concentration", False)),
            }
            cond = canonical_condition_record({
                "name": cond_name,
                "source": spell_name,
                "rounds_remaining": effect.get("rounds_remaining", parse_rounds_from_duration((payload or {}).get("duration"))),
                "save": dict(effect.get("save") or {}),
                "tick_damage": dict(effect.get("tick_damage") or {}),
                "meta": meta,
            })
            current = [canonical_condition_record(s) for s in list(getattr(target_ts, "statuses", []) or []) if isinstance(s, dict)]
            current.append(cond)
            target_ts.statuses = current
            self._refresh_condition_movement_state(target_ts)
            self._sync_token_statuses_to_sheet(target_ts)
            try:
                self.campaign_logger.combat("spell_condition_applied", spell_id=spell_id, spell_name=spell_name, caster_token_id=str(getattr(caster_ts, "token_id", "") or ""), target_token_id=str(getattr(target_ts, "token_id", "") or ""), target_name=str(getattr(target_ts, "display_name", getattr(target_ts, "token_id", "Target")) or "Target"), condition=str(cond_name))
            except Exception:
                pass
            return True
        if eff_type == "condition_remove":
            names = []
            if str(effect.get("name") or "").strip():
                names.append(str(effect.get("name") or "").strip().lower())
            if isinstance(effect.get("names"), list):
                names.extend([str(x).strip().lower() for x in list(effect.get("names") or []) if str(x).strip()])
            remove_all = bool(effect.get("all", False))
            statuses = [canonical_condition_record(s) for s in list(getattr(target_ts, "statuses", []) or []) if isinstance(s, dict)]
            kept = []
            removed = []
            for raw in statuses:
                raw_name = str(raw.get("name") or raw.get("condition") or "").strip().lower()
                if remove_all or (names and raw_name in set(names)):
                    self._cleanup_spell_status_effect(target_ts, raw)
                    removed.append(raw_name or "condition")
                    continue
                kept.append(raw)
            if not removed:
                return False
            target_ts.statuses = kept
            self._refresh_condition_movement_state(target_ts)
            self._sync_token_statuses_to_sheet(target_ts)
            try:
                self.campaign_logger.combat("spell_condition_removed", spell_id=spell_id, spell_name=spell_name, caster_token_id=str(getattr(caster_ts, "token_id", "") or ""), target_token_id=str(getattr(target_ts, "token_id", "") or ""), target_name=str(getattr(target_ts, "display_name", getattr(target_ts, "token_id", "Target")) or "Target"), conditions=list(removed))
            except Exception:
                pass
            return True
        if eff_type in {"bonus", "light"}:
            amount = int(effect.get("amount", 0) or 0)
            field = str(effect.get("field") or effect.get("stat") or ("vision_ft" if eff_type == "light" else "")).strip().lower()
            ability = normalize_ability_key(effect.get("ability") or effect.get("save") or "")
            changed = False
            mods = []
            if field.startswith("save_bonus") or field in {"save", "save_bonus", "saving_throw", "saving_throws"}:
                if ability and amount:
                    changed = self._apply_spell_save_bonus_modifier(target_ts, ability=ability, amount=amount, apply_now=True)
                    mods.append({"field": f"save_bonus.{ability}", "ability": ability, "amount": int(amount)})
            elif field and amount:
                changed = self._apply_spell_numeric_modifier(target_ts, field=field, amount=amount, apply_now=True)
                mods.append({"field": field, "amount": int(amount)})
            if not changed:
                return False
            duration_rounds = effect.get("rounds_remaining", parse_rounds_from_duration((payload or {}).get("duration")))
            meta = {
                "spell_id": spell_id,
                "spell_name": spell_name,
                "caster_token_id": str(getattr(caster_ts, "token_id", "") or ""),
                "concentration": bool((payload or {}).get("concentration", False)),
                "effect_kind": "light" if eff_type == "light" else "modifier",
                "modifiers": mods,
            }
            current = [canonical_condition_record(s) for s in list(getattr(target_ts, "statuses", []) or []) if isinstance(s, dict)]
            current.append(canonical_condition_record({
                "name": str(effect.get("label") or self._spell_modifier_label(effect, spell_name) or spell_name),
                "source": spell_name,
                "rounds_remaining": duration_rounds,
                "meta": meta,
            }))
            target_ts.statuses = current
            self._refresh_condition_movement_state(target_ts)
            self._sync_token_statuses_to_sheet(target_ts)
            self._sync_spell_modified_stats_to_sheet(target_ts)
            try:
                self.refresh_player_view()
            except Exception:
                pass
            try:
                self.campaign_logger.combat("spell_modifier_applied", spell_id=spell_id, spell_name=spell_name, caster_token_id=str(getattr(caster_ts, "token_id", "") or ""), target_token_id=str(getattr(target_ts, "token_id", "") or ""), target_name=str(getattr(target_ts, "display_name", getattr(target_ts, "token_id", "Target")) or "Target"), modifiers=list(mods or []), rounds_remaining=duration_rounds)
            except Exception:
                pass
            return True
        return False

    def _apply_declared_non_attack_spell_effects(self, payload: Dict[str, Any], caster_ts, target_ts=None, target_list=None) -> bool:
        try:
            effects = normalized_effects_from_spell(payload or {}, cast_level=int((payload or {}).get("slot_level", 0) or (payload or {}).get("spell_level", 0) or 0))
            if not effects:
                return False
            targets = [ts for ts in list(target_list or []) if ts is not None]
            if not targets:
                targets = [target_ts or caster_ts]
            applied = False
            for resolved_target in targets:
                for effect in effects:
                    save_cfg = dict(effect.get("save") or {}) if isinstance(effect.get("save"), dict) else {}
                    if bool(effect.get("attack_roll", False)) or save_cfg:
                        continue
                    if self._apply_spell_effect_to_target(caster_ts, resolved_target, effect, payload):
                        applied = True
            if applied and bool((payload or {}).get("concentration", False)):
                self._begin_spell_concentration(caster_ts, payload)
            return applied
        except Exception as e:
            print("[SPELL] failed to apply declared non-attack effects:", e)
            return False

    def _effects_for_portal(self, ts) -> list[dict]:
        effects = []
        death_state = str(getattr(ts, "death_state", "alive") or "alive").strip().lower()
        if death_state in {"down", "stable", "dead"}:
            ds_s = int(getattr(ts, "death_save_successes", 0) or 0)
            ds_f = int(getattr(ts, "death_save_failures", 0) or 0)
            if death_state == "down":
                summary = f"At 0 HP; death saves S={ds_s}/3 F={ds_f}/3"
            elif death_state == "stable":
                summary = "Stable at 0 HP"
            else:
                summary = "Dead"
            effects.append({
                "effect_id": f"death:{getattr(ts, 'token_id', '')}",
                "name": death_state.title(),
                "source": "combat",
                "summary": summary,
                "rounds_remaining": None,
                "timing": "",
                "damage_type": "",
                "meta": {"death_state": death_state, "successes": ds_s, "failures": ds_f},
            })

        if bool(getattr(ts, "concentration_active", False)):
            cname = str(getattr(ts, "concentration_spell_name", getattr(ts, "concentration_spell_id", "Concentration")) or "Concentration").strip()
            effects.append({
                "effect_id": f"concentration:{getattr(ts, 'token_id', '')}",
                "name": "Concentration",
                "source": "spell",
                "summary": f"Maintaining {cname}",
                "rounds_remaining": getattr(ts, "concentration_rounds_remaining", None),
                "timing": "",
                "damage_type": "",
                "meta": {"spell_id": str(getattr(ts, "concentration_spell_id", "") or "")},
            })

        for raw in list(getattr(ts, "statuses", []) or []):
            if not isinstance(raw, dict):
                continue
            cond = canonical_condition_record(raw)
            save = dict(cond.get("save", {}) or {})
            tick = dict(cond.get("tick_damage", {}) or {})
            bits = []
            if cond.get("rounds_remaining") is not None:
                bits.append(f"{int(cond.get('rounds_remaining'))} rounds left")
            if save.get("ability") and save.get("dc") is not None:
                when = str(save.get("timing", "") or "").strip()
                when_txt = f" {when}-turn" if when else ""
                bits.append(f"{save.get('ability')} save DC {int(save.get('dc'))}{when_txt}")
            if int(tick.get("amount", 0) or 0) > 0:
                timing = str(tick.get("timing", "") or "start").replace("_", " ")
                dtype = str(tick.get("damage_type", "") or "damage")
                bits.append(f"{int(tick.get('amount'))} {dtype} at {timing} of turn")
            semantic = condition_semantic_summary(str(cond.get("name", "") or ""))
            if semantic:
                bits.append(semantic)
            effects.append({
                "effect_id": str(cond.get("condition_id", "") or ""),
                "name": str(cond.get("name", "Condition") or "Condition").title(),
                "source": str(cond.get("source", "") or ""),
                "summary": "; ".join([b for b in bits if b]),
                "rounds_remaining": cond.get("rounds_remaining", None),
                "timing": str(save.get("timing", "") or tick.get("timing", "") or ""),
                "damage_type": str(tick.get("damage_type", "") or ""),
                "meta": dict(cond.get("meta", {}) or {}),
            })
        return effects

    def _effective_speed_for_token(self, ts) -> int:
        base = int(getattr(ts, "movement", getattr(ts, "base_movement", 30)) or 30)
        return int(effective_speed_ft(ts, base))

    def _refresh_condition_movement_state(self, ts) -> None:
        try:
            if not ts:
                return
            cap = self._effective_speed_for_token(ts)
            current = getattr(ts, "movement_remaining", None)
            if current is None:
                ts.movement_remaining = int(cap)
            else:
                ts.movement_remaining = max(0, min(int(current), int(cap)))
            if getattr(self.state, "active_token_id", None) == getattr(ts, "token_id", None):
                self._refresh_active_token_movement_overlay()
        except Exception:
            pass

    def _resolve_immediate_save_result(self, ts, *, ability: str, dc: int, mode: str = "normal", label: str = "Saving Throw", context: dict | None = None, deferred_effect: dict | None = None, source: str = "engine") -> dict:
        spec = {
            "request_id": f"auto-save:{uuid.uuid4().hex}",
            "token_id": str(getattr(ts, "token_id", "") or ""),
            "token_name": str(getattr(ts, "display_name", getattr(ts, "token_id", "Token")) or "Token"),
            "character_id": str(getattr(ts, "character_id", "") or ""),
            "player_id": str(getattr(ts, "player_id", "") or ""),
            "ability": normalize_ability_key(ability),
            "dc": int(dc),
            "adv_mode": str(mode or "normal"),
            "label": str(label or "Saving Throw"),
            "context": dict(context or {}),
            "deferred_effect": dict(deferred_effect or {}),
        }
        result = resolve_save_result(
            actor=ts,
            rules=self.rules,
            ability_key=spec["ability"],
            d20_value=0,
            dc=int(spec["dc"]),
            mode=str(spec["adv_mode"]),
            rolls=[0],
            request_id=spec["request_id"],
            label=spec["label"],
            context=dict(spec["context"]),
        )
        result["player_id"] = spec.get("player_id", "")
        result["character_id"] = spec.get("character_id", "")
        result["token_id"] = spec.get("token_id", "")
        result["token_name"] = spec.get("token_name", "")
        self._handle_resolved_save_result(result, ts=ts, source=source)
        source_kind = str(((spec.get("context") or {}).get("source_kind", "") or "")).strip().lower()
        if source_kind == "condition":
            self._apply_condition_save_resolution(ts, result, spec)
        else:
            self._apply_deferred_save_effect_resolution(ts, result, spec)
        return result

    def _sync_token_statuses_to_sheet(self, ts) -> None:
        try:
            if not self._is_player_controlled_token(ts):
                return
            character_id = str(getattr(ts, "character_id", "") or "").strip()
            if not character_id:
                return
            if not hasattr(self, "server") or self.server is None:
                return
            self.server.update_character_effects(character_id, self._effects_for_portal(ts))
        except Exception as e:
            print("[STATUS] sync failed:", e)

    def _sync_combat_flags_to_sheet(self, ts, updates: dict) -> None:
        try:
            if not self._is_player_controlled_token(ts):
                return
            character_id = str(getattr(ts, "character_id", "") or "").strip()
            if not character_id:
                return
            if not hasattr(self, "server") or self.server is None:
                return
            sheet = self.server.get_character_sheet(character_id) or {}
            if not isinstance(sheet, dict) or not sheet:
                return
            combat = sheet.setdefault("combat", {}) if isinstance(sheet.get("combat"), dict) else {}
            for k, v in dict(updates or {}).items():
                combat[k] = v
            self.server.upsert_character_sheet(character_id, sheet)
        except Exception as e:
            print("[COMBAT] sync failed:", e)

    def _sync_resource_pool_current_to_sheet(self, ts, pool_name: str, current: int) -> None:
        try:
            if not self._is_player_controlled_token(ts):
                return
            character_id = str(getattr(ts, "character_id", "") or "").strip()
            if not character_id or not hasattr(self, "server") or self.server is None:
                return
            sheet = self.server.get_character_sheet(character_id) or {}
            if not isinstance(sheet, dict) or not sheet:
                return
            pools = sheet.setdefault("resource_pools", {}) if isinstance(sheet.get("resource_pools"), dict) else {}
            pool = pools.setdefault(str(pool_name or "").strip(), {}) if isinstance(pools.get(str(pool_name or "").strip()), dict) else {}
            pool["current"] = max(0, int(current or 0))
            self.server.upsert_character_sheet(character_id, sheet)
        except Exception as e:
            print("[RESOURCE_POOL] sync failed:", e)

    def _get_sheet_spell_save_dc(self, ts) -> int:
        try:
            character_id = str(getattr(ts, "character_id", "") or "").strip()
            if not character_id or not hasattr(self, "server") or self.server is None:
                return 0
            sheet = self.server.get_character_sheet(character_id) or {}
            sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
            return max(0, int(sc.get("save_dc", 0) or 0))
        except Exception:
            return 0

    def _maybe_absorb_arcane_ward(self, target_ts, incoming_amount: int) -> tuple[int, int]:
        try:
            if target_ts is None:
                return int(incoming_amount or 0), 0
            character_id = str(getattr(target_ts, "character_id", "") or "").strip()
            if not character_id or not hasattr(self, "server") or self.server is None:
                return int(incoming_amount or 0), 0
            sheet = self.server.get_character_sheet(character_id) or {}
            if not isinstance(sheet, dict) or not sheet:
                return int(incoming_amount or 0), 0
            combat = sheet.get("combat") if isinstance(sheet.get("combat"), dict) else {}
            if not bool(combat.get("arcane_ward", False)):
                return int(incoming_amount or 0), 0
            pools = sheet.get("resource_pools") if isinstance(sheet.get("resource_pools"), dict) else {}
            ward = pools.get("arcane_ward") if isinstance(pools.get("arcane_ward"), dict) else {}
            cur = max(0, int(ward.get("current", 0) or 0))
            if cur <= 0 or int(incoming_amount or 0) <= 0:
                return int(incoming_amount or 0), 0
            absorbed = min(cur, max(0, int(incoming_amount or 0)))
            ward["current"] = cur - absorbed
            pools["arcane_ward"] = ward
            sheet["resource_pools"] = pools
            self.server.upsert_character_sheet(character_id, sheet)
            self._set_hud_status(f"{getattr(target_ts, 'display_name', character_id)} Arcane Ward absorbs {absorbed} damage.", hold_sec=3.0)
            return max(0, int(incoming_amount or 0) - absorbed), absorbed
        except Exception as e:
            print("[ARCANE_WARD] absorb failed:", e)
            return int(incoming_amount or 0), 0

    def _grant_dark_ones_blessing_if_needed(self, attacker_ts, defeated_ts) -> None:
        try:
            if attacker_ts is None or defeated_ts is None:
                return
            if str(getattr(defeated_ts, "death_state", "alive") or "alive") not in {"down", "dead"} and int(getattr(defeated_ts, "hp", 0) or 0) > 0:
                return
            character_id = str(getattr(attacker_ts, "character_id", "") or "").strip()
            if not character_id or not hasattr(self, "server") or self.server is None:
                return
            sheet = self.server.get_character_sheet(character_id) or {}
            if not isinstance(sheet, dict) or not sheet:
                return
            combat = sheet.get("combat") if isinstance(sheet.get("combat"), dict) else {}
            if not bool(combat.get("dark_ones_blessing", False)):
                return
            class_levels = sheet.get("class_levels") if isinstance(sheet.get("class_levels"), dict) else {}
            warlock_level = int(class_levels.get("warlock", 0) or 0)
            if warlock_level <= 0:
                return
            abilities = sheet.get("abilities") if isinstance(sheet.get("abilities"), dict) else {}
            cha = int(abilities.get("cha", 10) or 10)
            cha_mod = (cha - 10) // 2
            grant = max(1, warlock_level + cha_mod)
            current_temp = max(0, int(getattr(attacker_ts, "temp_hp", 0) or 0))
            if grant <= current_temp:
                return
            setattr(attacker_ts, "temp_hp", int(grant))
            self._sync_temp_hp_to_sheet(attacker_ts)
            self._set_hud_status(f"{getattr(attacker_ts, 'display_name', character_id)} gains {grant} temp HP (Dark One's Blessing).", hold_sec=3.5)
        except Exception as e:
            print("[DARK_ONES_BLESSING] failed:", e)

    def _maybe_offer_wrath_of_the_storm(self, reactor_ts, *, source_kind: str = "", source_meta: dict | None = None, final_damage: int = 0) -> bool:
        try:
            if reactor_ts is None or int(final_damage or 0) <= 0:
                return False
            if not self._token_can_take_reaction(reactor_ts):
                return False
            character_id = str(getattr(reactor_ts, "character_id", "") or "").strip()
            if not character_id or not hasattr(self, "server") or self.server is None:
                return False
            sheet = self.server.get_character_sheet(character_id) or {}
            if not isinstance(sheet, dict) or not sheet:
                return False
            combat = sheet.get("combat") if isinstance(sheet.get("combat"), dict) else {}
            if not bool(combat.get("wrath_of_the_storm", False)):
                return False
            pools = sheet.get("resource_pools") if isinstance(sheet.get("resource_pools"), dict) else {}
            wrath_pool = pools.get("wrath_of_the_storm") if isinstance(pools.get("wrath_of_the_storm"), dict) else {}
            if int(wrath_pool.get("current", 0) or 0) <= 0:
                return False
            src_kind_norm = str(source_kind or "").strip().lower()
            if src_kind_norm != "attack":
                return False
            src = source_meta if isinstance(source_meta, dict) else {}
            attacker_ts = self.state.tokens.get(str(src.get("attacker_token_id") or src.get("source_token_id") or ""))
            if attacker_ts is None:
                return False
            dx = abs(int(getattr(reactor_ts, "grid_x", 0) or 0) - int(getattr(attacker_ts, "grid_x", 0) or 0))
            dy = abs(int(getattr(reactor_ts, "grid_y", 0) or 0) - int(getattr(attacker_ts, "grid_y", 0) or 0))
            dist_ft = max(dx, dy) * int(getattr(self.state, "grid_ft", 5) or 5)
            if dist_ft > 5:
                return False
            class_levels = sheet.get("class_levels") if isinstance(sheet.get("class_levels"), dict) else {}
            cleric_level = int(class_levels.get("cleric", 0) or 0)
            dice_count = 2 if cleric_level < 6 else 3 if cleric_level < 14 else 4
            dc = self._get_sheet_spell_save_dc(reactor_ts)
            context = {
                "attacker_token_id": str(getattr(attacker_ts, "token_id", "") or ""),
                "dice_count": int(dice_count),
                "save_dc": int(dc or 0),
            }
            if self._is_player_controlled_token(reactor_ts):
                return self._offer_reaction_choice(
                    reactor_ts,
                    reaction_kind="wrath_of_the_storm_attack",
                    spell_id="",
                    text=f"Reaction available: use Wrath of the Storm against {getattr(attacker_ts, 'display_name', 'attacker')}?",
                    context=context,
                )
            self._consume_reaction(reactor_ts, reason="wrath_of_the_storm")
            self._execute_wrath_of_the_storm(reactor_ts, attacker_ts, dice_count=int(dice_count), save_dc=int(dc or 0))
            return True
        except Exception as e:
            print("[WRATH_OF_THE_STORM] offer failed:", e)
            return False

    def _execute_wrath_of_the_storm(self, reactor_ts, attacker_ts, *, dice_count: int = 2, save_dc: int = 0) -> None:
        try:
            if reactor_ts is None or attacker_ts is None:
                return
            character_id = str(getattr(reactor_ts, "character_id", "") or "").strip()
            if not character_id or not hasattr(self, "server") or self.server is None:
                return
            sheet = self.server.get_character_sheet(character_id) or {}
            pools = sheet.get("resource_pools") if isinstance(sheet.get("resource_pools"), dict) else {}
            wrath_pool = pools.get("wrath_of_the_storm") if isinstance(pools.get("wrath_of_the_storm"), dict) else {}
            cur = max(0, int(wrath_pool.get("current", 0) or 0))
            if cur <= 0:
                return
            wrath_pool["current"] = cur - 1
            pools["wrath_of_the_storm"] = wrath_pool
            sheet["resource_pools"] = pools
            self.server.upsert_character_sheet(character_id, sheet)
            rolls = [random.randint(1, 8) for _ in range(max(1, int(dice_count or 2)))]
            base_damage = sum(rolls)
            damage, save_result = self._maybe_apply_damage_save(
                attacker_ts,
                base_damage=int(base_damage),
                source_payload={"save_ability": "dex", "save_dc": int(save_dc or self._get_sheet_spell_save_dc(reactor_ts) or 0), "save_on_success": "half"},
                label="Wrath of the Storm",
                context={"source_kind": "wrath_of_the_storm", "source_token_id": str(getattr(reactor_ts, 'token_id', '') or '')},
            )
            self.apply_damage_to_token(
                attacker_ts,
                int(damage),
                encounter_id=getattr(self, "encounter_id", "") or "",
                pending_attack_id=f"wrath:{uuid.uuid4().hex[:8]}",
                damage_type="lightning",
                source_kind="wrath_of_the_storm",
                source_meta={"attacker_token_id": str(getattr(reactor_ts, 'token_id', '') or ''), "source_token_id": str(getattr(reactor_ts, 'token_id', '') or ''), "tags": ["reaction", "tempest_domain"]},
            )
            self._set_hud_status(f"{getattr(reactor_ts, 'display_name', character_id)} uses Wrath of the Storm ({base_damage} rolled, {damage} applied).", hold_sec=3.5)
            try:
                self.campaign_logger.combat(
                    "wrath_of_the_storm",
                    reactor_token_id=str(getattr(reactor_ts, 'token_id', '') or ''),
                    target_token_id=str(getattr(attacker_ts, 'token_id', '') or ''),
                    damage_rolls=list(rolls),
                    base_damage=int(base_damage),
                    applied_damage=int(damage),
                    save_success=bool((save_result or {}).get('success', False)),
                    save_total=int((save_result or {}).get('total', 0) or 0),
                    save_dc=int(save_dc or 0),
                )
            except Exception:
                pass
        except Exception as e:
            print("[WRATH_OF_THE_STORM] execute failed:", e)

    def _apply_deferred_save_effect_resolution(self, ts, result: Dict[str, Any], spec: Dict[str, Any]) -> None:
        """Apply post-save effect math for deferred PC saves.

        Some save requests are triggered by the engine but rolled in the portal.
        When they return, the engine still owns the outcome math. Keep this helper
        defensive so older/manual save requests that carry no deferred payload do
        nothing instead of crashing the poll loop.
        """
        try:
            payload = dict(spec.get("deferred_effect") or {})
            if not payload:
                return

            cond_on_fail = dict(payload.get("apply_condition_on_fail") or {})
            if cond_on_fail and not bool(result.get("success", False)):
                try:
                    cond = canonical_condition_record(cond_on_fail)
                    current = [canonical_condition_record(s) for s in list(getattr(ts, "statuses", []) or []) if isinstance(s, dict)]
                    current.append(cond)
                    ts.statuses = current
                    self._refresh_condition_movement_state(ts)
                    self._sync_token_statuses_to_sheet(ts)
                    self._set_hud_status(f"{getattr(ts, 'display_name', getattr(ts, 'token_id', 'Token'))} is stunned.", hold_sec=3.5)
                except Exception as e:
                    print("[SAVE] failed to apply deferred condition:", e)

            if bool(payload.get("concentration_check", False)):
                if not bool(result.get("success", False)):
                    self._clear_spell_concentration(ts, reason="failed_save")
                else:
                    try:
                        self.campaign_logger.combat(
                            "spell_concentration_maintained",
                            token_id=str(getattr(ts, "token_id", "") or ""),
                            token_name=str(getattr(ts, "display_name", getattr(ts, "token_id", "Token")) or "Token"),
                            request_id=str(result.get("request_id", "") or spec.get("request_id", "")),
                            spell_id=str(getattr(ts, "concentration_spell_id", "") or ""),
                            spell_name=str(getattr(ts, "concentration_spell_name", "") or ""),
                        )
                    except Exception:
                        pass
                return

            base_damage = int(payload.get("base_damage", 0) or 0)
            if base_damage <= 0:
                return

            success_mode = str(payload.get("save_on_success", "none") or "none")
            ability_key = str(result.get("ability", "") or "").strip().lower()
            if bool(getattr(ts, "evasion", False)) and ability_key in {"dex", "dexterity"}:
                success_mode = "none" if bool(result.get("success", False)) else "half"
            final_damage = compute_damage_after_save(base_damage, bool(result.get("success", False)), success_mode)
            if final_damage <= 0:
                return

            pending_attack_id = str(payload.get("pending_attack_id", "") or payload.get("effect_id", "") or "save-effect")
            self.apply_damage_to_token(
                ts,
                int(final_damage),
                encounter_id=getattr(self, "encounter_id", ""),
                pending_attack_id=pending_attack_id,
                damage_type=str(payload.get("damage_type", "") or ""),
                source_kind=str(payload.get("source_kind", "save" ) or "save"),
            )
            try:
                self.campaign_logger.combat(
                    "save_effect_resolved",
                    token_id=str(getattr(ts, "token_id", "") or ""),
                    token_name=str(getattr(ts, "display_name", getattr(ts, "token_id", "Token")) or "Token"),
                    request_id=str(result.get("request_id", "") or spec.get("request_id", "")),
                    success=bool(result.get("success", False)),
                    damage_before=int(base_damage),
                    damage_after=int(final_damage),
                    save_on_success=success_mode,
                    source_kind=str((spec.get("context") or {}).get("source_kind", payload.get("source_kind", "deferred")) or "deferred"),
                )
            except Exception:
                pass
        except Exception as e:
            print("[SAVE] deferred effect resolution failed:", e)

    def _apply_condition_save_resolution(self, ts, result: Dict[str, Any], spec: Dict[str, Any]) -> None:
        condition_id = str(((spec.get("context") or {}).get("condition_id", "") or spec.get("condition_id", "")).strip())
        timing = str(((spec.get("context") or {}).get("timing", "") or spec.get("timing", "")).strip())
        updated = []
        removed_name = "condition"
        for raw in list(getattr(ts, "statuses", []) or []):
            if not isinstance(raw, dict):
                continue
            cond = canonical_condition_record(raw)
            if str(cond.get("condition_id", "")) == condition_id:
                removed_name = str(cond.get("name", removed_name) or removed_name)
                cond.setdefault("save", {})["pending_request_id"] = ""
                if bool(result.get("success", False)):
                    continue
            updated.append(cond)
        ts.statuses = updated
        self._sync_token_statuses_to_sheet(ts)
        self._refresh_condition_movement_state(ts)
        if bool(result.get("success", False)):
            try:
                self.campaign_logger.combat(
                    "condition_removed",
                    token_id=str(getattr(ts, "token_id", "") or ""),
                    token_name=str(getattr(ts, "display_name", getattr(ts, "token_id", "")) or ""),
                    condition_id=condition_id,
                    condition_name=removed_name,
                    reason="save_success",
                    timing=timing,
                )
            except Exception:
                pass

    def ui_clear_conditions(self, tok_item) -> None:
        token_id = str(getattr(tok_item, "token_id", "") or "")
        ts = self.state.tokens.get(token_id)
        if not ts:
            return
        cleared = len(list(getattr(ts, "statuses", []) or []))
        ts.statuses = []
        try:
            self.campaign_logger.combat(
                "conditions_cleared",
                token_id=token_id,
                token_name=str(getattr(ts, "display_name", token_id) or token_id),
                count=int(cleared),
            )
        except Exception:
            pass
        self._sync_token_statuses_to_sheet(ts)
        self._refresh_condition_movement_state(ts)
        self._set_hud_status(f"Cleared conditions from {getattr(ts, 'display_name', token_id)}.", hold_sec=2.0)
        self.update_combat_hud()

    def _process_condition_turn_hook(self, token_id: str, *, timing: str) -> None:
        if not token_id or token_id not in self.state.tokens:
            return
        ts = self.state.tokens.get(token_id)
        if not ts:
            return

        def _resolve(ability: str, dc: int, mode: str, label: str, context: dict) -> dict:
            rules = getattr(self, "rules", None)
            if rules is None:
                rules = RulesRegistry.get("default")
            ctx = {
                "source_kind": "condition",
                "token_id": str(token_id),
                **dict(context or {}),
            }
            if self._is_player_controlled_token(ts):
                request_id = uuid.uuid4().hex
                spec = {
                    "request_id": request_id,
                    "token_id": str(getattr(ts, "token_id", "") or ""),
                    "token_name": str(getattr(ts, "display_name", "") or getattr(ts, "token_id", "") or "Token"),
                    "character_id": str(getattr(ts, "character_id", "") or ""),
                    "player_id": str(getattr(ts, "player_id", "") or ""),
                    "ability": normalize_ability_key(ability),
                    "dc": int(dc),
                    "adv_mode": str(mode or "normal"),
                    "label": str(label or "Saving Throw"),
                    "roll_kind": "save",
                    "expected_sides": 20,
                    "expected_count_min": 2 if str(mode or "normal") in {"advantage", "disadvantage"} else 1,
                    "expected_count_max": 2 if str(mode or "normal") in {"advantage", "disadvantage"} else 1,
                    "ttl_s": 90,
                    "context": {
                        "ability": normalize_ability_key(ability),
                        "dc": int(dc),
                        "token_id": str(getattr(ts, "token_id", "") or ""),
                        "token_name": str(getattr(ts, "display_name", "") or getattr(ts, "token_id", "") or "Token"),
                        "kind": "condition_save",
                        **ctx,
                    },
                }
                resp = self.server.register_roll_request(spec)
                returned_id = str((resp or {}).get("request_id", "") or request_id)
                if returned_id:
                    spec["request_id"] = returned_id
                    self._pending_save_requests[returned_id] = spec
                    return {"request_id": returned_id, "resolved": False}
                return {"success": False, "resolved": True, "error": "request_failed"}
            return roll_engine_save_result(
                actor=ts,
                rules=rules,
                ability_key=str(ability or ""),
                dc=int(dc),
                mode=str(mode or "normal"),
                label=str(label or "Saving Throw"),
                context=ctx,
            )

        result = process_turn_hook(actor=ts, timing=str(timing or ""), save_resolver=_resolve)
        ts.statuses = list(result.get("statuses", []) or [])
        self._sync_token_statuses_to_sheet(ts)
        for ev in list(result.get("events", []) or []):
            etype = str(ev.get("event", "") or "")
            if etype == "save_requested":
                try:
                    self.campaign_logger.combat(
                        "save_requested",
                        request_id=str(ev.get("request_id", "") or ""),
                        token_id=str(token_id),
                        name=str(getattr(ts, "display_name", token_id) or token_id),
                        player_id=str(getattr(ts, "player_id", "") or ""),
                        character_id=str(getattr(ts, "character_id", "") or ""),
                        ability=str(((ev.get("result") or {}).get("context") or {}).get("ability", "") or ""),
                        dc=int((((ev.get("result") or {}).get("context") or {}).get("dc", 0) or 0)),
                        mode=str(((ev.get("result") or {}).get("mode", "normal") or "normal")),
                        label=str(((ev.get("result") or {}).get("context") or {}).get("condition_name", ev.get("condition_name", "Save")) or "Save"),
                    )
                except Exception:
                    pass
            elif etype == "save":
                save_result = dict(ev.get("result", {}) or {})
                try:
                    self.campaign_logger.combat(
                        "save_resolved",
                        token_id=str(token_id),
                        token_name=str(getattr(ts, "display_name", token_id) or token_id),
                        ability=str(save_result.get("ability", "")),
                        dc=int(save_result.get("dc", 0) or 0),
                        mode=str(save_result.get("mode", "normal") or "normal"),
                        d20=int(save_result.get("chosen", 0) or 0),
                        modifier=int(save_result.get("modifier", 0) or 0),
                        total=int(save_result.get("total", 0) or 0),
                        success=bool(save_result.get("success", False)),
                        label=str(save_result.get("label", ev.get("condition_name", "Save")) or "Save"),
                        source_kind="condition_auto",
                        condition_id=str(ev.get("condition_id", "") or ""),
                        condition_name=str(ev.get("condition_name", "") or ""),
                    )
                except Exception:
                    pass
            elif etype == "damage":
                amount = int(ev.get("amount", 0) or 0)
                if amount > 0:
                    self.apply_damage_to_token(
                        ts,
                        amount,
                        encounter_id=getattr(self, "encounter_id", ""),
                        pending_attack_id=f"status:{str(ev.get('condition_name', 'condition') or 'condition')}",
                        damage_type=str(ev.get("damage_type", "") or ""),
                        source_kind="status",
                    )
                    try:
                        self.campaign_logger.combat(
                            "status_damage",
                            token_id=str(token_id),
                            name=str(getattr(ts, "display_name", token_id) or token_id),
                            source=str(ev.get("condition_name", "condition") or "condition"),
                            amount=int(amount),
                            damage_type=str(ev.get("damage_type", "") or ""),
                            timing=str(ev.get("timing", timing) or timing),
                        )
                    except Exception:
                        pass
            elif etype in {"removed", "expired"}:
                try:
                    self.campaign_logger.combat(
                        "condition_removed",
                        token_id=str(token_id),
                        token_name=str(getattr(ts, "display_name", token_id) or token_id),
                        condition_id=str(ev.get("condition_id", "") or ""),
                        condition_name=str(ev.get("condition_name", "") or ""),
                        reason=str(ev.get("reason", etype) or etype),
                        timing=str(ev.get("timing", timing) or timing),
                    )
                except Exception:
                    pass

    def ui_request_saving_throw(self, tok_item) -> None:
        token_id = getattr(tok_item, "token_id", "")
        ts = self.state.tokens.get(token_id)
        if not ts:
            self._set_hud_status("Token not found for save request.", hold_sec=3.0)
            return

        cfg = self._prompt_save_request_inputs(default_label=f"{getattr(ts, 'display_name', 'Token')} Manual Save")
        if not cfg:
            return

        if self._is_player_controlled_token(ts):
            self._register_pc_save_request(ts, cfg)
        else:
            self._resolve_npc_save_request(ts, cfg)

    def _register_pc_save_request(self, ts, cfg: Dict[str, Any]) -> None:
        adjusted = save_rule_from_conditions(ts, cfg.get("ability", ""), cfg.get("adv_mode", "normal"))
        cfg = dict(cfg or {})
        cfg["adv_mode"] = str(adjusted.get("mode", cfg.get("adv_mode", "normal")) or "normal")
        if bool(adjusted.get("auto_fail", False)):
            self._resolve_immediate_save_result(
                ts,
                ability=str(cfg.get("ability", "") or ""),
                dc=int(cfg.get("dc", 10) or 10),
                mode=str(cfg.get("adv_mode", "normal") or "normal"),
                label=str(cfg.get("label", "Saving Throw") or "Saving Throw"),
                context={
                    "ability": normalize_ability_key(cfg.get("ability", "")),
                    "dc": int(cfg.get("dc", 10) or 10),
                    "token_id": str(getattr(ts, "token_id", "") or ""),
                    "token_name": str(getattr(ts, "display_name", "") or getattr(ts, "token_id", "") or "Token"),
                    "kind": "manual_saving_throw",
                    "source_kind": "manual",
                },
                source="engine_auto_fail",
            )
            return
        request_id = uuid.uuid4().hex
        spec = {
            "request_id": request_id,
            "token_id": str(getattr(ts, "token_id", "") or ""),
            "token_name": str(getattr(ts, "display_name", "") or getattr(ts, "token_id", "") or "Token"),
            "character_id": str(getattr(ts, "character_id", "") or ""),
            "player_id": str(getattr(ts, "player_id", "") or ""),
            "ability": normalize_ability_key(cfg.get("ability", "")),
            "dc": int(cfg.get("dc", 10) or 10),
            "adv_mode": str(cfg.get("adv_mode", "normal") or "normal"),
            "label": str(cfg.get("label", "Saving Throw") or "Saving Throw"),
            "roll_kind": "save",
            "expected_sides": 20,
            "expected_count_min": 2 if str(cfg.get("adv_mode", "normal")) in {"advantage", "disadvantage"} else 1,
            "expected_count_max": 2 if str(cfg.get("adv_mode", "normal")) in {"advantage", "disadvantage"} else 1,
            "ttl_s": 90,
            "context": {
                "ability": normalize_ability_key(cfg.get("ability", "")),
                "dc": int(cfg.get("dc", 10) or 10),
                "token_id": str(getattr(ts, "token_id", "") or ""),
                "token_name": str(getattr(ts, "display_name", "") or getattr(ts, "token_id", "") or "Token"),
                "kind": "manual_saving_throw",
            },
        }
        resp = self.server.register_roll_request(spec)
        returned_id = str((resp or {}).get("request_id", "") or request_id)
        if not returned_id:
            self._set_hud_status(f"Failed to request save for {spec['token_name']}.", hold_sec=4.0)
            return
        spec["request_id"] = returned_id
        self._pending_save_requests[returned_id] = spec
        self._set_hud_status(f"Requested {spec['ability']} save DC {spec['dc']} for {spec['token_name']}.", hold_sec=4.0)
        try:
            self.campaign_logger.combat(
                "save_requested",
                request_id=returned_id,
                token_id=spec["token_id"],
                name=spec["token_name"],
                player_id=spec["player_id"],
                character_id=spec["character_id"],
                ability=spec["ability"],
                dc=int(spec["dc"]),
                mode=spec["adv_mode"],
                label=spec["label"],
            )
        except Exception:
            pass

    def _register_pc_deferred_damage_save_request(self, ts, *, ability: str, dc: int, mode: str = "normal", label: str = "Saving Throw", context: dict | None = None, deferred_effect: dict | None = None) -> str:
        adjusted = save_rule_from_conditions(ts, ability, mode)
        ability_key = normalize_ability_key(ability)
        adv_mode = str(adjusted.get("mode", mode) or "normal")
        if bool(adjusted.get("auto_fail", False)):
            self._resolve_immediate_save_result(
                ts,
                ability=ability_key,
                dc=int(dc),
                mode=adv_mode,
                label=str(label or "Saving Throw"),
                context={
                    "ability": ability_key,
                    "dc": int(dc),
                    "token_id": str(getattr(ts, "token_id", "") or ""),
                    "token_name": str(getattr(ts, "display_name", "") or getattr(ts, "token_id", "") or "Token"),
                    **dict(context or {}),
                },
                deferred_effect=dict(deferred_effect or {}),
                source="engine_auto_fail",
            )
            return ""
        request_id = uuid.uuid4().hex
        spec = {
            "request_id": request_id,
            "token_id": str(getattr(ts, "token_id", "") or ""),
            "token_name": str(getattr(ts, "display_name", "") or getattr(ts, "token_id", "") or "Token"),
            "character_id": str(getattr(ts, "character_id", "") or ""),
            "player_id": str(getattr(ts, "player_id", "") or ""),
            "ability": ability_key,
            "dc": int(dc),
            "adv_mode": adv_mode,
            "label": str(label or "Saving Throw"),
            "roll_kind": "save",
            "expected_sides": 20,
            "expected_count_min": 2 if adv_mode in {"advantage", "disadvantage"} else 1,
            "expected_count_max": 2 if adv_mode in {"advantage", "disadvantage"} else 1,
            "ttl_s": 90,
            "context": {
                "ability": ability_key,
                "dc": int(dc),
                "token_id": str(getattr(ts, "token_id", "") or ""),
                "token_name": str(getattr(ts, "display_name", "") or getattr(ts, "token_id", "") or "Token"),
                **dict(context or {}),
            },
            "deferred_effect": dict(deferred_effect or {}),
        }
        resp = self.server.register_roll_request(spec)
        returned_id = str((resp or {}).get("request_id", "") or request_id)
        if not returned_id:
            return ""
        spec["request_id"] = returned_id
        self._pending_save_requests[returned_id] = spec
        try:
            self.campaign_logger.combat(
                "save_requested",
                request_id=returned_id,
                token_id=spec["token_id"],
                name=spec["token_name"],
                player_id=spec["player_id"],
                character_id=spec["character_id"],
                ability=spec["ability"],
                dc=int(spec["dc"]),
                mode=spec["adv_mode"],
                label=spec["label"],
                source_kind=str((spec.get("context") or {}).get("source_kind", "deferred") or "deferred"),
            )
        except Exception:
            pass
        return returned_id

    def _resolve_npc_save_request(self, ts, cfg: Dict[str, Any]) -> None:
        try:
            adjusted = save_rule_from_conditions(ts, cfg.get("ability", ""), cfg.get("adv_mode", "normal"))
            cfg = dict(cfg or {})
            cfg["adv_mode"] = str(adjusted.get("mode", cfg.get("adv_mode", "normal")) or "normal")
            if bool(adjusted.get("auto_fail", False)):
                result = resolve_save_result(
                    actor=ts,
                    rules=self.rules,
                    ability_key=cfg.get("ability", ""),
                    d20_value=0,
                    dc=int(cfg.get("dc", 10) or 10),
                    mode=str(cfg.get("adv_mode", "normal") or "normal"),
                    rolls=[0],
                    request_id=f"npc-save:{uuid.uuid4().hex}",
                    label=str(cfg.get("label", "Saving Throw") or "Saving Throw"),
                    context={"token_id": getattr(ts, "token_id", ""), "token_name": getattr(ts, "display_name", "")},
                )
                result["token_id"] = getattr(ts, "token_id", "")
                result["token_name"] = getattr(ts, "display_name", getattr(ts, "token_id", "Token"))
                self._handle_resolved_save_result(result, ts=ts, source="dm_auto_fail")
                try:
                    self._apply_deferred_save_effect_resolution(ts, result, {"deferred_effect": dict(cfg.get("deferred_effect") or {}), "context": dict(cfg.get("context") or {})})
                except Exception:
                    pass
                return
            roll_count = 2 if str(cfg.get("adv_mode", "normal")) in {"advantage", "disadvantage"} else 1
            raw_rolls = [random.randint(1, 20) for _ in range(roll_count)]
            if str(cfg.get("adv_mode", "normal")) == "advantage":
                chosen = max(raw_rolls)
            elif str(cfg.get("adv_mode", "normal")) == "disadvantage":
                chosen = min(raw_rolls)
            else:
                chosen = raw_rolls[0]
            result = resolve_save_result(
                actor=ts,
                rules=self.rules,
                ability_key=cfg.get("ability", ""),
                d20_value=int(chosen),
                dc=int(cfg.get("dc", 10) or 10),
                mode=str(cfg.get("adv_mode", "normal") or "normal"),
                rolls=raw_rolls,
                request_id=f"npc-save:{uuid.uuid4().hex}",
                label=str(cfg.get("label", "Saving Throw") or "Saving Throw"),
                context={"token_id": getattr(ts, "token_id", ""), "token_name": getattr(ts, "display_name", "")},
            )
            result["token_id"] = getattr(ts, "token_id", "")
            result["token_name"] = getattr(ts, "display_name", getattr(ts, "token_id", "Token"))
            self._handle_resolved_save_result(result, ts=ts, source="dm_local")
            try:
                self._apply_deferred_save_effect_resolution(ts, result, {"deferred_effect": dict(cfg.get("deferred_effect") or {}), "context": dict(cfg.get("context") or {})})
            except Exception:
                pass
        except Exception as e:
            print("[SAVE] npc save resolution failed:", e)
            self._set_hud_status("NPC save request failed.", hold_sec=4.0)

    def handle_roll_request_result_payload(self, payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            return

        roll_kind = str(payload.get("roll_kind", "") or "").strip().lower()
        request_id = str(payload.get("request_id", "") or "").strip()
        if roll_kind == "death_save":
            spec = self._pending_death_save_requests.pop(request_id, None)
            if not spec:
                print(f"[DEATH] Unknown request_id result ignored: {request_id}")
                return
            token_id = str((payload.get("context") or {}).get("token_id", "") or spec.get("token_id", "")).strip()
            ts = self.state.tokens.get(token_id)
            if not ts:
                self._set_hud_status(f"Death save result received for missing token ({spec.get('token_name','?')}).", hold_sec=4.0)
                return
            self._apply_death_save_result(ts, payload, spec)
            return

        if roll_kind != "save":
            return

        spec = self._pending_save_requests.pop(request_id, None)
        ctx0 = dict(payload.get("context") or {})
        if spec and str(((spec.get("context") or {}).get("kind", "") or ctx0.get("kind", "")).strip().lower()) == "relentless_rage":
            token_id = str(ctx0.get("token_id", "") or spec.get("token_id", "")).strip()
            ts = self.state.tokens.get(token_id)
            if not ts:
                self._set_hud_status(f"Relentless Rage result received for missing token ({spec.get('token_name','token')}).", hold_sec=4.0)
                return
            try:
                result = resolve_save_result(
                    actor=ts,
                    rules=self.rules,
                    ability_key="CON",
                    d20_value=int(payload.get("chosen", 0) or 0),
                    dc=int(spec.get("dc", 10) or 10),
                    mode=str(payload.get("mode", spec.get("adv_mode", "normal")) or "normal"),
                    rolls=list(payload.get("rolls") or []),
                    extras=dict(payload.get("extras") or {}),
                    request_id=request_id,
                    label=str(spec.get("label", "Relentless Rage") or "Relentless Rage"),
                    context=ctx0 or dict(spec.get("context") or {}),
                )
            except Exception as e:
                print("[SAVE] Failed to resolve Relentless Rage:", e)
                return
            ts.pending_relentless_rage_request_id = ""
            if bool(result.get("success", False)):
                ts.hp = 1
                ts.death_state = "alive"
                ts.death_save_successes = 0
                ts.death_save_failures = 0
                ts.relentless_rage_uses = int(getattr(ts, "relentless_rage_uses", 0) or 0) + 1
                try:
                    if hasattr(self, "server") and self.server is not None:
                        sheet = self.server.get_character_sheet(str(getattr(ts, "character_id", "") or "")) or {}
                        combat_sheet = sheet.setdefault("combat", {}) if isinstance(sheet.get("combat"), dict) else {}
                        combat_sheet["relentless_rage_uses"] = int(getattr(ts, "relentless_rage_uses", 0) or 0)
                        self.server.upsert_character_sheet(str(getattr(ts, "character_id", "") or ""), sheet)
                except Exception:
                    pass
                self._set_hud_status(f"Relentless Rage succeeds: {getattr(ts, 'display_name', token_id)} stays at 1 HP.", hold_sec=4.0)
            else:
                self._set_hud_status(f"Relentless Rage fails for {getattr(ts, 'display_name', token_id)}.", hold_sec=4.0)
            try:
                self._sync_death_state_to_sheet(ts)
                self._sync_token_statuses_to_sheet(ts)
            except Exception:
                pass
            return
        if not spec:
            print(f"[SAVE] Unknown request_id result ignored: {request_id}")
            return

        token_id = str((payload.get("context") or {}).get("token_id", "") or spec.get("token_id", "")).strip()
        ts = self.state.tokens.get(token_id)
        if not ts:
            self._set_hud_status(f"Save result received for missing token ({spec.get('token_name','?')}).", hold_sec=4.0)
            return

        try:
            result = resolve_save_result(
                actor=ts,
                rules=self.rules,
                ability_key=spec.get("ability", ""),
                d20_value=int(payload.get("chosen", 0) or 0),
                dc=int(spec.get("dc", 10) or 10),
                mode=str(payload.get("mode", spec.get("adv_mode", "normal")) or "normal"),
                rolls=list(payload.get("rolls") or []),
                extras=dict(payload.get("extras") or {}),
                request_id=request_id,
                label=str(spec.get("label", "Saving Throw") or "Saving Throw"),
                context=dict(payload.get("context") or spec.get("context") or {}),
            )
        except Exception as e:
            print("[SAVE] Failed to resolve save result:", e)
            self._set_hud_status(f"Failed to resolve save for {spec.get('token_name','token')}.", hold_sec=4.0)
            return

        result["player_id"] = spec.get("player_id", "")
        result["character_id"] = spec.get("character_id", "")
        result["token_id"] = token_id
        result["token_name"] = spec.get("token_name", getattr(ts, "display_name", token_id))
        self._handle_resolved_save_result(result, ts=ts, source="portal")
        source_kind = str(((spec.get("context") or {}).get("source_kind", "") or "")).strip().lower()
        if source_kind == "condition":
            self._apply_condition_save_resolution(ts, result, spec)
        else:
            self._apply_deferred_save_effect_resolution(ts, result, spec)

    def _handle_resolved_save_result(self, result: Dict[str, Any], *, ts, source: str) -> None:
        name = str(result.get("token_name", getattr(ts, "display_name", getattr(ts, "token_id", "Token"))) or "Token")
        ability = str(result.get("ability", "") or "?")
        total = int(result.get("total", 0) or 0)
        dc = int(result.get("dc", 10) or 10)
        chosen = int(result.get("chosen", 0) or 0)
        modifier = int(result.get("modifier", 0) or 0)
        success = bool(result.get("success", False))
        outcome = "SUCCESS" if success else "FAIL"
        self._last_save_result = dict(result or {})
        self._set_hud_status(f"{name} {ability} save {outcome} ({total} vs DC {dc}).", hold_sec=5.0)
        try:
            self.campaign_logger.combat(
                "save_resolved",
                request_id=str(result.get("request_id", "") or ""),
                source=str(source or ""),
                token_id=str(getattr(ts, "token_id", "") or result.get("token_id", "")),
                name=name,
                player_id=str(result.get("player_id", "") or ""),
                character_id=str(result.get("character_id", "") or ""),
                ability=ability,
                d20=chosen,
                modifier=modifier,
                total=total,
                dc=dc,
                success=bool(success),
                mode=str(result.get("mode", "normal") or "normal"),
                rolls=list(result.get("rolls") or []),
                label=str(result.get("label", "") or ""),
            )
        except Exception:
            pass

    def clear_tokens(self):
        for token in self.token_items:
            self.scene.removeItem(token)
        self.token_items.clear()
        self.state.tokens.clear()
        self.refresh_player_view()


    def load_map(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Open Map Image", "", "Image Files (*.png *.jpg *.bmp)")
        if filepath:
            self.load_map_from_path(filepath)

    def draw_grid(self, cell_size=GRID_SIZE):
        if not hasattr(self, 'map_pixmap'):
            return

        width = self.map_pixmap.width()
        height = self.map_pixmap.height()

        pen = QPen(QColor("gray"))
        pen.setWidth(1)

        for x in range(0, width, cell_size):
            self.scene.addLine(x, 0, x, height, pen)
        for y in range(0, height, cell_size):
            self.scene.addLine(0, y, width, y, pen)

    def add_token(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Open Token Image", "", "Image Files (*.png *.jpg *.bmp)")
        if filepath:
            self.add_token_from_path(filepath)

    def load_map_from_path(self, filepath):
        self.current_map_path = filepath
        self.map_pixmap = QPixmap(filepath)
        # Ensure map metadata is loaded for this map (walls/blocked/terrain).
        # This enables Phase B2 LOS-based fog without requiring the editor dock to be open.
        try:
            if ensure_map_meta_loaded is not None and self.map_pixmap is not None:
                cols = max(1, int(self.map_pixmap.width()) // int(GRID_SIZE))
                rows = max(1, int(self.map_pixmap.height()) // int(GRID_SIZE))
                meta, meta_path = ensure_map_meta_loaded(filepath, cols, rows, GRID_SIZE)
                self.current_map_meta = meta
                self.current_map_meta_path = meta_path
        except Exception:
            pass
        self.scene.clear()
        self.scene.addPixmap(self.map_pixmap)
        self.scene.setSceneRect(QRectF(self.map_pixmap.rect()))
        self.draw_grid()
        self.token_items = []
        # Reload map metadata overlays if editor is open
        try:
            if self.map_metadata_editor is not None:
                self.map_metadata_editor.redraw_overlays()
        except Exception:
            pass

        # State update
        self.state.map_relpath = os.path.relpath(filepath, self.campaign_path)
        self.state.tokens.clear()
        self.refresh_player_view()

    def add_token_from_path(self, filepath):
        filename = os.path.basename(filepath)
        tokens_file = os.path.join(self.campaign_path, "tokens.json")
        token_template = None

        if os.path.exists(tokens_file):
            try:
                with open(tokens_file, "r") as f:
                    tokens_data = json.load(f)
                token_template = next((t for t in tokens_data if t.get("icon") == filename), None)
            except Exception:
                token_template = None

        pix = QPixmap(filepath)
        token = DraggableToken(pix, grid_size=GRID_SIZE)
        token.on_moved_callback = self.on_token_moved
        token.on_cover_override_callback = self.on_token_cover_override
        token.setPos(100, 100)
        token.filepath = filepath

        # Template -> token fields (with backward-compatible defaults)
        if token_template:
            token.display_name = token_template.get("name", filename)
            token.max_hp = token_template.get("max_hp", 10)
            token.hp = token.max_hp
            token.ac = token_template.get("ac", 10)
            token.weapon_id = token_template.get("weapon_id", "")
            token.armor_id = token_template.get("armor_id", "")
            token.weapon = token.weapon_id or token_template.get("weapon", "")
            token.armor = token.armor_id or token_template.get("armor", "")
            token.movement = token_template.get("movement", 30)
            token.attack_modifier = token_template.get("attack_modifier", 0)

            token.side = token_template.get("side", "enemy")
            token.vision_ft = token_template.get("vision_ft", 60)

            # B-X4: Vision types / senses
            token.vision_type = str(token_template.get("vision_type", "normal") or "normal")
            token.darkvision_ft = int(token_template.get("darkvision_ft", 0) or 0)
            token.blindsight_ft = int(token_template.get("blindsight_ft", 0) or 0)
            token.truesight_ft = int(token_template.get("truesight_ft", 0) or 0)
            token.tremorsense_ft = int(token_template.get("tremorsense_ft", 0) or 0)
            token.devils_sight_ft = int(token_template.get("devils_sight_ft", 0) or 0)

            # PC linking fields
            token.kind = token_template.get("kind", "npc")
            token.player_id = token_template.get("player_id", "")
            token.character_id = token_template.get("character_id", "")
            token.stat_source = token_template.get("stat_source", "template")
        else:
            token.display_name = filename
            token.max_hp = 10
            token.hp = 10
            token.ac = 10
            token.weapon = ""
            token.armor = ""
            token.movement = 30
            token.attack_modifier = 0

            token.side = "enemy"
            token.vision_ft = 60
            token.kind = "npc"
            token.player_id = ""
            token.character_id = ""
            token.stat_source = "template"

        token.update_hp_bar()
        self.scene.addItem(token)
        self.token_items.append(token)
        token.template_id = token_template.get("template_id", "") if token_template else ""

        rel_image = os.path.relpath(filepath, self.campaign_path)
        gx, gy = self.token_grid_xy(token)
        dead_icon = (token_template.get("dead_icon") or "").strip() if token_template else ""
        dead_rel = os.path.join("tokens", dead_icon) if dead_icon else ""


        self.state.tokens[token.token_id] = TokenState(
            token_id=token.token_id,
            display_name=token.display_name,
            image_relpath=rel_image,
            grid_x=gx,
            grid_y=gy,
            hp=token.hp,
            dead_image_relpath=dead_rel,
            max_hp=token.max_hp,
            ac=token.ac,
                weapon_id=getattr(token, "weapon_id", "") or "",
                armor_id=getattr(token, "armor_id", "") or "",
            weapon=token.weapon,
            armor=token.armor,
            base_movement=int(getattr(token, "base_movement", getattr(token, "movement", 0)) or 0),
            movement_remaining=None,
            movement=int(getattr(token, "movement", 0) or 0),
            attack_modifier=token.attack_modifier,
            side=token.side,
            vision_ft=token.vision_ft,
            vision_type=str(getattr(token, "vision_type", "normal") or "normal"),
            darkvision_ft=int(getattr(token, "darkvision_ft", 0) or 0),
            blindsight_ft=int(getattr(token, "blindsight_ft", 0) or 0),
            truesight_ft=int(getattr(token, "truesight_ft", 0) or 0),
            tremorsense_ft=int(getattr(token, "tremorsense_ft", 0) or 0),
            devils_sight_ft=int(getattr(token, "devils_sight_ft", 0) or 0),
            template_id=getattr(token, "template_id", ""),

            # Only keep these if TokenState supports them
            kind=token.kind,
            player_id=token.player_id,
            character_id=token.character_id,
            stat_source=token.stat_source,
        )

                # If this is sheet-backed, hydrate immediately so stats/abilities exist even before first selection
        try:
            ts = self.state.tokens.get(token.token_id)
            if ts and getattr(ts, "stat_source", "") == "character_sheet" and getattr(ts, "character_id", ""):
                # Prevent brand-new sheets (default 10 max_hp) from clobbering token templates.
                try:
                    if getattr(self, "server_client", None) is not None:
                        self.server_client.ensure_character_sheet_initialized(
                            ts.character_id,
                            base_stats={"ac": int(getattr(ts, "ac", 10) or 10)},
                            resources={"hp": int(getattr(ts, "hp", 0) or 0), "max_hp": int(getattr(ts, "max_hp", 10) or 10)},
                        )
                except Exception:
                    pass

                # Pull authoritative non-HP stats (AC/max_hp) without overwriting current HP.
                self.hydrate_tokenstate_from_sheet(ts, include_hp=False)
        except Exception:
            pass

        self.refresh_player_view()

    def save_encounter(self) -> None:
        save_path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Encounter",
            os.path.join(self.campaign_path, "encounters"),
            "JSON Files (*.json)"
        )
        if not save_path:
            return

        os.makedirs(os.path.dirname(save_path), exist_ok=True)

        tokens_out = []
        for t in list(getattr(self, "token_items", []) or []):
            if not hasattr(t, "filepath") or not t.filepath:
                continue

            gx, gy = self.token_grid_xy(t)

            tokens_out.append({
                "token_id": t.token_id,
                "template_id": getattr(t, "template_id", ""),
                "image": os.path.relpath(t.filepath, self.campaign_path),
                "x": int(gx),
                "y": int(gy),
                "display_name": getattr(t, "display_name", os.path.basename(t.filepath)),

                "hp": int(getattr(t, "hp", 10)),
                "max_hp": int(getattr(t, "max_hp", 10)),
                "death_state": getattr(t, "death_state", "alive"),
                "death_save_successes": int(getattr(t, "death_save_successes", 0)),
                "death_save_failures": int(getattr(t, "death_save_failures", 0)),
                "dead_image": getattr(t, "dead_image_relpath", "") or "",
                "ac": int(getattr(t, "ac", 10)),

                # persist both id + legacy fields safely
                "weapon_id": getattr(t, "weapon_id", "") or "",
                "armor_id": getattr(t, "armor_id", "") or "",
                "weapon": getattr(t, "weapon", "") or "",
                "armor": getattr(t, "armor", "") or "",

                "movement": int(getattr(t, "movement", 30)),
                "movement_remaining": getattr(t, "movement_remaining", None),
                "attack_modifier": int(getattr(t, "attack_modifier", 0)),

                "side": getattr(t, "side", "enemy"),
                "vision_ft": int(getattr(t, "vision_ft", 60)),
                "vision_type": str(getattr(t, "vision_type", "normal") or "normal"),
                "darkvision_ft": int(getattr(t, "darkvision_ft", 0) or 0),
                "blindsight_ft": int(getattr(t, "blindsight_ft", 0) or 0),
                "truesight_ft": int(getattr(t, "truesight_ft", 0) or 0),
                "tremorsense_ft": int(getattr(t, "tremorsense_ft", 0) or 0),
                "devils_sight_ft": int(getattr(t, "devils_sight_ft", 0) or 0),

                "kind": getattr(t, "kind", "npc"),
                "player_id": getattr(t, "player_id", ""),
                "character_id": getattr(t, "character_id", ""),
                "stat_source": getattr(t, "stat_source", "template"),

                # Phase 5.0.2: abilities/proficiency/save profs (safe defaults)
                "abilities": dict(getattr(t, "abilities", {}) or {}),
                "proficiency_bonus": int(getattr(t, "proficiency_bonus", 0) or 0),
                "save_proficiencies": list(getattr(t, "save_proficiencies", []) or []),
            })

        encounter_data = {
            "map": os.path.relpath(self.current_map_path, self.campaign_path) if self.current_map_path else None,
            "tokens": tokens_out,
            "door_state": dict(getattr(self.state, "door_state", {}) or {}),
            "runtime_fog_zones": list(getattr(self.state, "runtime_fog_zones", []) or []),
        }

        # -------- Initiative blob (as you had) --------
        init_blob = {
            "initiative_active": bool(getattr(self.state, "initiative_active", False)),
            "initiative_order": list(getattr(self.state, "initiative_order", []) or []),
            "initiative_values": dict(getattr(self.state, "initiative_values", {}) or {}),
            "current_turn_index": int(getattr(self.state, "current_turn_index", 0) or 0),
            "round_number": int(getattr(self.state, "round_number", 1) or 1),
            "active_token_id": getattr(self.state, "active_token_id", None),
        }
        if init_blob["initiative_order"] or init_blob["initiative_values"] or init_blob["initiative_active"]:
            encounter_data["initiative"] = init_blob

        # -------- Phase 5.1d: history persistence --------
        hr = getattr(self, "history_runtime", None)
        hist = getattr(hr, "history", None) if hr else None
        if hist and getattr(hist, "base_snapshot", None) is not None:
            encounter_data["history"] = {
                "base_snapshot": getattr(hist, "base_snapshot", None),
                "events": list(getattr(hist, "events", []) or []),
                "cursor": int(getattr(hist, "cursor", 0) or 0),
            }

        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(encounter_data, f, indent=2)

        try:
            self.campaign_logger.encounter(
                "save",
                save_path=save_path,
                map=os.path.relpath(self.current_map_path, self.campaign_path) if self.current_map_path else None,
                token_count=len(tokens_out),
                history_events=len(encounter_data.get("history", {}).get("events", []) or []),
                history_cursor=int(encounter_data.get("history", {}).get("cursor", 0) or 0),
            )
        except Exception:
            pass

        print(f"[ENCOUNTER] Saved: {save_path} (tokens={len(tokens_out)})")

    def load_encounter(self) -> None:
        load_path, _ = QFileDialog.getOpenFileName(
            self,
            "Load Encounter",
            os.path.join(self.campaign_path, "encounters"),
            "JSON Files (*.json)"
        )
        if not load_path:
            return

        with open(load_path, "r", encoding="utf-8") as f:
            encounter = json.load(f)

        # --- Load map first ---
        map_rel = encounter.get("map", "")
        map_path = os.path.join(self.campaign_path, map_rel) if map_rel else None
        if map_path:
            self.load_map_from_path(map_path)

        # BX2/BX5.2: restore encounter-level door overrides and runtime fog zones
        try:
            self.state.door_state = dict(encounter.get("door_state", {}) or {})
        except Exception:
            pass
        try:
            self.state.runtime_fog_zones = list(encounter.get("runtime_fog_zones", []) or [])
        except Exception:
            pass

        # Clear tokens list (load_map_from_path likely reset scene/state, but keep safe)
        self.token_items = []
        try:
            self.campaign_logger.encounter("load", load_path=load_path)
            self.campaign_logger.encounter("loaded", map=map_rel, token_count=len(encounter.get("tokens", [])))
        except Exception:
            pass

        # --- Rebuild tokens from file ---
        for token_data in encounter.get("tokens", []):
            token_rel = token_data.get("image", "")
            token_path = os.path.join(self.campaign_path, token_rel)

            if not os.path.exists(token_path):
                print(f"[LOAD] Missing token image: {token_path}")
                continue

            pix = QPixmap(token_path)
            token = DraggableToken(pix, grid_size=GRID_SIZE)
            token.on_moved_callback = self.on_token_moved

            gx = int(token_data.get("x", 0))
            gy = int(token_data.get("y", 0))
            token._suppress_move_callback = True
            token.setPos(gx * GRID_SIZE, gy * GRID_SIZE)
            token._suppress_move_callback = False
            token.filepath = token_path

            # Core identity
            token.token_id = token_data.get("token_id", "")
            token.display_name = token_data.get("display_name", os.path.basename(token_path))
            token.template_id = token_data.get("template_id", "")

            # Combat stats
            token.hp = int(token_data.get("hp", 10))
            token.max_hp = int(token_data.get("max_hp", 10))
            token.death_state = token_data.get("death_state", "alive")
            token.death_save_successes = int(token_data.get("death_save_successes", 0))
            token.death_save_failures = int(token_data.get("death_save_failures", 0))
            token.ac = int(token_data.get("ac", 10))

            token.weapon_id = token_data.get("weapon_id", "") or ""
            token.armor_id = token_data.get("armor_id", "") or ""
            token.weapon = token.weapon_id or token_data.get("weapon", "") or ""
            token.armor = token.armor_id or token_data.get("armor", "") or ""

            token.movement = int(token_data.get("movement", 30))
            token.movement_remaining = token_data.get("movement_remaining", None)
            token.attack_modifier = int(token_data.get("attack_modifier", 0))

            token.abilities = dict(token_data.get("abilities") or {})
            token.proficiency_bonus = int(token_data.get("proficiency_bonus") or 0)
            token.save_proficiencies = list(token_data.get("save_proficiencies") or [])

            token.side = token_data.get("side", "enemy")
            token.vision_ft = int(token_data.get("vision_ft", 60))

            # B-X4: Vision types / senses
            token.vision_type = str(token_data.get("vision_type", "normal") or "normal")
            token.darkvision_ft = int(token_data.get("darkvision_ft", 0) or 0)
            token.blindsight_ft = int(token_data.get("blindsight_ft", 0) or 0)
            token.truesight_ft = int(token_data.get("truesight_ft", 0) or 0)
            token.tremorsense_ft = int(token_data.get("tremorsense_ft", 0) or 0)
            token.devils_sight_ft = int(token_data.get("devils_sight_ft", 0) or 0)

            token.kind = token_data.get("kind", "npc")
            token.player_id = token_data.get("player_id", "")
            token.character_id = token_data.get("character_id", "")
            token.stat_source = token_data.get("stat_source", "template")

            token.dead_image_relpath = token_data.get("dead_image", "") or ""

            token.update_hp_bar()
            self.scene.addItem(token)
            self.token_items.append(token)

            # State update
            self.state.tokens[token.token_id] = TokenState(
                token_id=token.token_id,
                display_name=token.display_name,
                image_relpath=token_rel,
                grid_x=gx,
                grid_y=gy,

                hp=token.hp,
                max_hp=token.max_hp,
                death_state=getattr(token, "death_state", "alive"),
                death_save_successes=int(getattr(token, "death_save_successes", 0)),
                death_save_failures=int(getattr(token, "death_save_failures", 0)),
                dead_image_relpath=token.dead_image_relpath,

                ac=token.ac,
                weapon_id=getattr(token, "weapon_id", "") or "",
                armor_id=getattr(token, "armor_id", "") or "",
                weapon=token.weapon,
                armor=token.armor,
                movement=token.movement,
                movement_remaining=token_data.get("movement_remaining", None),
                base_movement=int(token_data.get("movement", 30)),
                attack_modifier=token.attack_modifier,
                side=token.side,
                vision_ft=token.vision_ft,
                vision_type=str(getattr(token, "vision_type", "normal") or "normal"),
                darkvision_ft=int(getattr(token, "darkvision_ft", 0) or 0),
                blindsight_ft=int(getattr(token, "blindsight_ft", 0) or 0),
                truesight_ft=int(getattr(token, "truesight_ft", 0) or 0),
                tremorsense_ft=int(getattr(token, "tremorsense_ft", 0) or 0),
                devils_sight_ft=int(getattr(token, "devils_sight_ft", 0) or 0),
                template_id=token.template_id,

                kind=token.kind,
                player_id=token.player_id,
                character_id=token.character_id,
                stat_source=token.stat_source,

                abilities=dict(token_data.get("abilities") or {}),
                proficiency_bonus=int(token_data.get("proficiency_bonus") or 0),
                save_proficiencies=list(token_data.get("save_proficiencies") or []),
            )

            # Hydrate sheet-backed tokens immediately
            try:
                ts = self.state.tokens.get(token.token_id)
                if ts and getattr(ts, "stat_source", "") == "character_sheet" and getattr(ts, "character_id", ""):
                    self.hydrate_tokenstate_from_sheet(ts, include_hp=True)

                    token.max_hp = int(getattr(ts, "max_hp", token.max_hp) or token.max_hp)
                    token.hp = int(getattr(ts, "hp", token.hp) or token.hp)
                    token.ac = int(getattr(ts, "ac", token.ac) or token.ac)

                    token.weapon_id = getattr(ts, "weapon_id", getattr(token, "weapon_id", "")) or ""
                    token.armor_id = getattr(ts, "armor_id", getattr(token, "armor_id", "")) or ""
                    token.weapon = getattr(ts, "weapon", getattr(token, "weapon", "")) or ""
                    token.armor = getattr(ts, "armor", getattr(token, "armor", "")) or ""
                    token.attack_modifier = int(getattr(ts, "attack_modifier", getattr(token, "attack_modifier", 0)) or 0)
                    token.movement = int(getattr(ts, "movement", getattr(token, "movement", 30)) or 30)
                    token.vision_ft = int(getattr(ts, "vision_ft", getattr(token, "vision_ft", 60)) or 60)

                    token.update_hp_bar()
            except Exception as e:
                print("[LOAD] Sheet hydration failed:", e)

        # -------- Restore initiative blob --------
        init_blob = encounter.get("initiative") or None
        if isinstance(init_blob, dict):
            self.state.initiative_active = bool(init_blob.get("initiative_active", False))
            self.state.initiative_order = list(init_blob.get("initiative_order", []) or [])
            self.state.initiative_values = dict(init_blob.get("initiative_values", {}) or {})
            self.state.current_turn_index = int(init_blob.get("current_turn_index", 0) or 0)
            self.state.round_number = int(init_blob.get("round_number", 1) or 1)
            self.state.active_token_id = init_blob.get("active_token_id", None)

        # -------- Restore history blob --------
        hist_blob = encounter.get("history") or None
        if isinstance(hist_blob, dict):
            try:
                hr = getattr(self, "history_runtime", None)
                if hr and getattr(hr, "history", None):
                    hist = hr.history
                    hist.base_snapshot = hist_blob.get("base_snapshot", None)
                    hist.events = list(hist_blob.get("events", []) or [])
                    hist.cursor = int(hist_blob.get("cursor", 0) or 0)

                    # Replay to cursor so state matches history timeline
                    if hist.base_snapshot is not None:
                        print(f"[HISTORY] Restored: events={len(hist.events)} cursor={hist.cursor}")
                        self._replay_to_cursor(int(hist.cursor), reason="LOAD")
                        self._post_replay_ui_sync()
            except Exception as e:
                print("[HISTORY] Failed to restore history:", e)

        # Final refresh
        self.update_initiative_panel()
        self.refresh_player_view()

        print(f"[ENCOUNTER] Loaded: {load_path} (tokens={len(encounter.get('tokens', []) or [])})")

    def open_encounter_manager(self):
        token_data = []
        for t in self.token_items:
            token_data.append({
                "name": os.path.basename(getattr(t, "filepath", "Unknown")),
                "hp": getattr(t, "hp", 10),
                "max_hp": getattr(t, "max_hp", 10),
                "x": int(t.x() // GRID_SIZE),
                "y": int(t.y() // GRID_SIZE)
            })
        self.encounter_window = EncounterWindow(token_data)
        self.encounter_window.show()

    def handle_selection_changed(self):
        # Guard: selectionChanged can fire during teardown / after scene was destroyed
        try:
            if self.scene is None or sip.isdeleted(self.scene):
                return
        except Exception:
            if self.scene is None:
                return

        try:
            selected = self.scene.selectedItems()
        except RuntimeError:
            return

        # Always clear overlays first (prevents "stuck until deselect/reselect")
        try:
            for token in self.token_items:
                token.hide_movement_range(self.scene)
                token.hide_attack_range(self.scene)
        except Exception:
            pass

        # Nothing selected => also clear Player View selection
        if not selected:
            if self.player_view_window is not None:
                setattr(self.player_view_window, "selected_token_id", None)
                self.refresh_player_view()
            try:
                self.selected_token_id = None
            except Exception:
                pass
            return

        item = selected[0]

        # Non-token selected => treat as deselect
        if not isinstance(item, DraggableToken):
            if self.player_view_window is not None:
                setattr(self.player_view_window, "selected_token_id", None)
                self.refresh_player_view()
            try:
                self.selected_token_id = None
            except Exception:
                pass
            return

        # Token selected => set selected_token_id and push into Player View
        try:
            self.selected_token_id = getattr(item, "token_id", None)
        except Exception:
            self.selected_token_id = None

        if self.player_view_window is not None:
            setattr(self.player_view_window, "selected_token_id", self.selected_token_id)
            self.refresh_player_view()

        # Now apply selection behavior (which can show overlays)
        self.process_token_selection(item)
    
    def create_drawing_toolbar(self):
        self.toolbar = QToolBar("Drawing Tools")
        self.addToolBar(self.toolbar)

        self.draw_toggle = QPushButton("Draw Mode: OFF")
        self.draw_toggle.setCheckable(True)
        self.draw_toggle.clicked.connect(self.toggle_draw_mode)
        self.toolbar.addWidget(self.draw_toggle)

        pen_button = QPushButton("Pen Color")
        pen_button.clicked.connect(self.select_pen_color)
        self.toolbar.addWidget(pen_button)

        erase_button = QPushButton("Eraser")
        erase_button.clicked.connect(lambda: self.view.set_erase_mode())
        self.toolbar.addWidget(erase_button)

        clear_button = QPushButton("Clear Drawings")
        clear_button.clicked.connect(lambda: self.view.clear_drawings())
        self.toolbar.addWidget(clear_button)

        self.toolbar.addSeparator()
        self.toolbar.addWidget(QLabel("Rests:"))

        sr_start_btn = QPushButton("Start Short Rest")
        sr_start_btn.clicked.connect(self.start_short_rest)
        self.toolbar.addWidget(sr_start_btn)

        sr_resolve_btn = QPushButton("Resolve Short Rest")
        sr_resolve_btn.clicked.connect(self.resolve_short_rest)
        self.toolbar.addWidget(sr_resolve_btn)

        sr_cancel_btn = QPushButton("Cancel Short Rest")
        sr_cancel_btn.clicked.connect(self.cancel_short_rest)
        self.toolbar.addWidget(sr_cancel_btn)

        lr_start_btn = QPushButton("Start Long Rest")
        lr_start_btn.clicked.connect(self.start_long_rest)
        self.toolbar.addWidget(lr_start_btn)

        lr_resolve_btn = QPushButton("Resolve Long Rest")
        lr_resolve_btn.clicked.connect(self.resolve_long_rest)
        self.toolbar.addWidget(lr_resolve_btn)

        lr_cancel_btn = QPushButton("Cancel Long Rest")
        lr_cancel_btn.clicked.connect(self.cancel_long_rest)
        self.toolbar.addWidget(lr_cancel_btn)

        self.toolbar.addSeparator()
        self.toolbar.addWidget(QLabel("Level Up:"))

        lvl_party_btn = QPushButton("Grant Party")
        lvl_party_btn.clicked.connect(self.grant_party_level_up)
        self.toolbar.addWidget(lvl_party_btn)

        lvl_selected_btn = QPushButton("Grant Selected PC")
        lvl_selected_btn.clicked.connect(self.grant_selected_pc_level_up)
        self.toolbar.addWidget(lvl_selected_btn)

    def toggle_draw_mode(self):
        enabled = self.draw_toggle.isChecked()
        self.view.set_drawing_enabled(enabled)
        self.draw_toggle.setText("Draw Mode: ON" if enabled else "Draw Mode: OFF")

    def select_pen_color(self):
        color = QColorDialog.getColor()
        if color.isValid():
            self.view.set_pen_color(color.name())

    def process_token_selection(self, item):
        if not isinstance(item, DraggableToken):
            return

        # Guard: scene could be torn down
        try:
            if self.scene is None or sip.isdeleted(self.scene):
                return
        except Exception:
            if self.scene is None:
                return

        token_id = getattr(item, "token_id", "") or ""
        self.selected_token_id = token_id

        # Hide movement/attack indicators from all tokens
        for tok in list(getattr(self, "token_items", [])):
            try:
                tok.hide_movement_range(self.scene)
                tok.hide_attack_range(self.scene)
            except Exception:
                pass

        # --- SOURCE OF TRUTH: EncounterState ---
        ts = self.state.tokens.get(token_id)
        print(
            "[TOKEN_SELECT]",
            "token_id=", token_id,
            "scene.weapon_id=", getattr(item, "weapon_id", ""),
            "scene.weapon=", getattr(item, "weapon", ""),
            "state.weapon_id=", getattr(ts, "weapon_id", "") if ts else "",
            "state.weapon=", getattr(ts, "weapon", "") if ts else "",
            "state.stat_source=", getattr(ts, "stat_source", "") if ts else "",
            "state.character_id=", getattr(ts, "character_id", "") if ts else "",
        )
        if ts is None:
            # Fallback: still show movement from scene token if state missing
            try:
                item.show_movement_range(self.scene)
            except Exception:
                pass

            try:
                if self.player_view_window is not None:
                    setattr(self.player_view_window, "selected_token_id", None)
                    self.refresh_player_view()
            except Exception:
                pass
            return

        # If sheet-backed PC, refresh stats when selected (so weapon swaps update immediately)
        try:
            self.hydrate_tokenstate_from_sheet(ts, include_hp=False)
        except Exception:
            pass

        # Push fresh state into the scene token so visuals remain consistent
        new_wid = getattr(ts, "weapon_id", "") or ""
        new_w = getattr(ts, "weapon", "") or ""
        # Avoid clobbering a valid scene weapon with an empty hydrated value
        if new_wid:
            item.weapon_id = new_wid
        if new_w:
            item.weapon = new_w
        elif new_wid:
            item.weapon = new_wid

        # Movement shown should be "remaining" only for the ACTIVE token during initiative.
        if bool(getattr(self.state, "initiative_active", False)) and getattr(self.state, "active_token_id", None) == ts.token_id:
            item.movement = int(getattr(ts, "movement_remaining", getattr(ts, "movement", 0)) or 0)
        else:
            item.movement = int(getattr(ts, "movement", getattr(item, "movement", 0)) or 0)

        # Show movement range for selected (now uses refreshed movement)
        try:
            item.show_movement_range(self.scene)
        except Exception:
            pass

        # Pull weapon stats from items.json using TokenState weapon fields (not scene cache)
        weapon_ref = (getattr(ts, "weapon_id", "") or getattr(ts, "weapon", "") or "").strip()
        print("[TOKEN_RANGE]", "weapon_ref=", weapon_ref)
        weapon_data = self.get_weapon_data(weapon_ref)  # may fall back to unarmed depending on implementation

        if weapon_data:
            # cache for recenter-on-move redraw in DraggableToken.mouseReleaseEvent
            try:
                item._cached_weapon_data = weapon_data
            except Exception:
                pass
            try:
                item.show_attack_range(self.scene, weapon_data)
            except Exception:
                pass
        else:
            # clear cache so we don't keep drawing stale range on move
            try:
                item._cached_weapon_data = None
            except Exception:
                pass

            if weapon_ref:
                print(f"[RANGE] Weapon '{weapon_ref}' not found in items.json")
            else:
                print("[RANGE] Token has no weapon set")

        # --- Player View: selection-driven overlays; PC-only ---
        is_pc = (getattr(ts, "side", "") == "player") or (getattr(ts, "kind", "") == "pc")
        if self.player_view_window is not None:
            try:
                if is_pc:
                    setattr(self.player_view_window, "selected_token_id", token_id)
                else:
                    setattr(self.player_view_window, "selected_token_id", None)
                self.refresh_player_view()
            except Exception:
                pass

        try:
            self._refresh_aoe_spell_combo()
        except Exception:
            pass

    def check_for_incoming_rolls(self):
        try:
            reaction_responses = self.server.fetch_reaction_responses()
        except Exception:
            reaction_responses = []
        for payload in reaction_responses:
            try:
                self.handle_reaction_response_payload(payload)
            except Exception as e:
                print("[REACTION] response handling failed:", e)

        rolls = self.server.fetch_rolls()
        for payload in rolls:
            self.handle_roll_payload(payload)

        dmg_rolls = self.server.fetch_damage_rolls()
        for payload in dmg_rolls:
            self.handle_damage_payload(payload)

        save_results = self.server.fetch_roll_request_results()
        for payload in save_results:
            self.handle_roll_request_result_payload(payload)

        spell_decls = self.server.fetch_spell_declarations()
        for payload in spell_decls:
            self.handle_spell_declaration_payload(payload)

    def _find_token_for_spell_character(self, character_id: str):
        character_id = str(character_id or "").strip()
        if not character_id:
            return None
        for _token_id, ts in (getattr(self.state, "tokens", {}) or {}).items():
            if str(getattr(ts, "character_id", "") or "").strip() == character_id:
                return ts
        return None

    def _find_token_for_spell_hint(self, hint: str, *, exclude_token_id: str = ""):
        raw = str(hint or "").strip()
        if not raw:
            return None
        lowered = raw.lower()
        tokens = list((getattr(self.state, "tokens", {}) or {}).values())

        # direct token_id / character_id match
        for ts in tokens:
            tid = str(getattr(ts, "token_id", "") or "").strip()
            cid = str(getattr(ts, "character_id", "") or "").strip()
            if exclude_token_id and tid == exclude_token_id:
                continue
            if raw == tid or (cid and raw == cid):
                return ts

        # exact display-name match
        for ts in tokens:
            tid = str(getattr(ts, "token_id", "") or "").strip()
            if exclude_token_id and tid == exclude_token_id:
                continue
            name = str(getattr(ts, "display_name", tid) or tid).strip()
            if name and name.lower() == lowered:
                return ts

        # substring display-name match
        for ts in tokens:
            tid = str(getattr(ts, "token_id", "") or "").strip()
            if exclude_token_id and tid == exclude_token_id:
                continue
            name = str(getattr(ts, "display_name", tid) or tid).strip()
            if name and lowered in name.lower():
                return ts
        return None

    def _select_scene_token(self, token_id: str) -> None:
        token_id = str(token_id or "").strip()
        if not token_id:
            return
        try:
            for item in self.scene.items():
                if getattr(item, "token_id", None) == token_id:
                    try:
                        self.scene.clearSelection()
                    except Exception:
                        pass
                    try:
                        item.setSelected(True)
                    except Exception:
                        pass
                    self.selected_token_id = token_id
                    return
        except Exception:
            return

    def _spell_target_matches_affects(self, caster_ts, target_ts, affects: str) -> bool:
        try:
            mode = str(affects or '').strip().lower()
            if not mode or mode in {'all', 'any', 'creature', 'creatures', 'point'}:
                return True
            caster_id = str(getattr(caster_ts, 'token_id', '') or '')
            target_id = str(getattr(target_ts, 'token_id', '') or '')
            if mode in {'self', 'caster'}:
                return bool(caster_id) and caster_id == target_id
            same_side = str(getattr(caster_ts, 'side', '') or '').strip().lower() == str(getattr(target_ts, 'side', '') or '').strip().lower()
            if mode in {'ally', 'allies', 'friendly', 'friendlies'}:
                return bool(caster_id) and caster_id != target_id and same_side
            if mode in {'ally_or_self', 'friendly_or_self', 'friendlies_or_self'}:
                return same_side
            if mode in {'enemy', 'enemies', 'hostile', 'hostiles'}:
                return not same_side if (getattr(caster_ts, 'side', None) is not None and getattr(target_ts, 'side', None) is not None) else True
            return True
        except Exception:
            return True

    def _resolve_declared_spell_targets(self, payload: Dict[str, Any], caster_ts, target_hint: str = '') -> list:
        try:
            if caster_ts is None:
                return []
            targeting = normalized_targeting(payload or {})
            kind = str(targeting.get('kind') or '').strip().lower()
            delivery = str(targeting.get('delivery') or '').strip().lower()
            affects = str(targeting.get('affects') or '').strip().lower()
            count = max(1, int(targeting.get('count') or 1))
            hints = split_target_hints(target_hint)
            out = []
            seen = set()

            def _push(ts):
                if ts is None:
                    return
                tid = str(getattr(ts, 'token_id', '') or '')
                if not tid or tid in seen:
                    return
                if affects and not self._spell_target_matches_affects(caster_ts, ts, affects):
                    return
                seen.add(tid)
                out.append(ts)

            if kind == 'self' or delivery == 'self':
                _push(caster_ts)
                return out

            for hint in hints:
                ts = self._find_token_for_spell_hint(hint, exclude_token_id='' if affects in {'ally_or_self', 'friendly_or_self'} else str(getattr(caster_ts, 'token_id', '') or ''))
                _push(ts)
                if len(out) >= count:
                    break

            if not out and kind in {'single', 'touch'} and affects in {'self', 'caster', 'ally_or_self', 'friendly_or_self'}:
                _push(caster_ts)

            if len(out) > count:
                out = out[:count]
            return out
        except Exception as e:
            print('[SPELL] target resolution failed:', e)
            return []

    def _spell_damage_expr_from_payload(self, payload: Dict[str, Any]) -> str:
        damage = (payload or {}).get("damage")
        if isinstance(damage, dict):
            expr = str(damage.get("expr", "") or "").strip()
            if expr:
                return expr
        return str((payload or {}).get("damage_expr", "") or "").strip()

    def _roll_spell_damage_total(self, damage_expr: str) -> tuple[int, list[int], int]:
        expr = str(damage_expr or "").strip()
        if not expr:
            return 0, [], 0
        try:
            from engine.spell_resolution import roll_spell_expr
            total, rolls, mod = roll_spell_expr(expr)
            return int(total or 0), list(rolls or []), int(mod or 0)
        except Exception as e:
            print("[SPELL] damage roll failed:", e)
            return 0, [], 0

    def _prime_declared_aoe_spell(self, payload: Dict[str, Any], caster_ts) -> bool:
        spell_id = str((payload or {}).get("spell_id") or "").strip()
        if not spell_id or spell_id not in (getattr(self, "spells_db", {}) or {}):
            return False
        try:
            self._aoe_active = True
            self._aoe_spell_id = spell_id
            self._aoe_locked = False
            self._aoe_locked_cell = None
            self._aoe_target_cell = (int(getattr(caster_ts, "grid_x", 0) or 0), int(getattr(caster_ts, "grid_y", 0) or 0))
            self._select_scene_token(str(getattr(caster_ts, "token_id", "") or ""))
            self._refresh_aoe_spell_combo()
            self._push_aoe_template_to_player_view()
            self.view.viewport().update()
            return True
        except Exception as e:
            print("[SPELL] failed to prime aoe spell:", e)
            return False

    def _register_declared_spell_save(self, payload: Dict[str, Any], caster_ts, target_ts) -> str:
        if caster_ts is None or target_ts is None:
            return ""
        ability = normalize_ability_key((payload or {}).get("save_type", ""))
        if not ability:
            return ""
        dc = int((((payload or {}).get("spellcasting") or {}) if isinstance((payload or {}).get("spellcasting"), dict) else {}).get("save_dc", 0) or 0)
        if dc <= 0:
            dc = 10
        spell_id = str((payload or {}).get("spell_id") or "").strip()
        spell_name = str((payload or {}).get("spell_name") or spell_id or "Spell").strip()
        spell_effects = normalized_effects_from_spell(payload or {}, cast_level=int((payload or {}).get("slot_level", 0) or (payload or {}).get("spell_level", 0) or 0))
        damage_effect = next((fx for fx in spell_effects if isinstance(fx, dict) and str(fx.get("type") or "").strip().lower() == "damage"), {})
        condition_effect = next((fx for fx in spell_effects if isinstance(fx, dict) and str(fx.get("type") or "").strip().lower() == "condition_apply"), {})
        save_cfg = dict(damage_effect.get("save") or {}) if isinstance(damage_effect.get("save"), dict) else {}
        damage_expr = str(damage_effect.get("expr") or self._spell_damage_expr_from_payload(payload)).strip()
        base_damage = 0
        rolls = []
        mod = 0
        if damage_expr:
            base_damage, rolls, mod = self._roll_spell_damage_total(damage_expr)
        deferred_effect = {
            "base_damage": int(base_damage),
            "save_on_success": str(save_cfg.get("on_success") or "half"),
            "pending_attack_id": f"spell:{spell_id}:{uuid.uuid4().hex[:8]}",
            "source_kind": "spell",
            "damage_type": str((((payload or {}).get("damage") or {}) if isinstance((payload or {}).get("damage"), dict) else {}).get("type", damage_effect.get("damage_type", "")) or ""),
        }
        if isinstance(condition_effect, dict) and str(condition_effect.get("name") or condition_effect.get("condition") or "").strip():
            cond_payload = {
                "name": str(condition_effect.get("name") or condition_effect.get("condition") or "").strip(),
                "source": spell_name,
                "rounds_remaining": condition_effect.get("rounds_remaining", parse_rounds_from_duration((payload or {}).get("duration"))),
                "save": dict(condition_effect.get("save") or {}),
                "tick_damage": dict(condition_effect.get("tick_damage") or {}),
                "meta": {
                    "spell_id": spell_id,
                    "spell_name": spell_name,
                    "caster_token_id": str(getattr(caster_ts, "token_id", "") or ""),
                    "concentration": bool((payload or {}).get("concentration", False)),
                },
            }
            if isinstance(cond_payload.get("save"), dict) and int(cond_payload["save"].get("dc", 0) or 0) <= 0:
                cond_payload["save"]["dc"] = int(dc)
            deferred_effect["apply_condition_on_fail"] = cond_payload
        context = {
            "kind": "spell_save",
            "source_kind": "spell",
            "spell_id": spell_id,
            "spell_name": spell_name,
            "caster_token_id": str(getattr(caster_ts, "token_id", "") or ""),
            "caster_name": str(getattr(caster_ts, "display_name", getattr(caster_ts, "token_id", "Caster")) or "Caster"),
        }
        req_id = ""
        if self._is_player_controlled_token(target_ts):
            req_id = self._register_pc_deferred_damage_save_request(
                target_ts,
                ability=ability,
                dc=int(dc),
                mode="normal",
                label=f"{spell_name} Save",
                context=context,
                deferred_effect=deferred_effect,
            )
        else:
            req_id = f"npc-save:{uuid.uuid4().hex[:10]}"
            self._resolve_npc_save_request(
                target_ts,
                {
                    "ability": ability,
                    "dc": int(dc),
                    "adv_mode": "normal",
                    "label": f"{spell_name} Save",
                    "context": context,
                    "deferred_effect": deferred_effect,
                },
            )
        try:
            self.campaign_logger.combat(
                "spell_save_requested",
                request_id=str(req_id),
                spell_id=spell_id,
                spell_name=spell_name,
                caster_token_id=str(getattr(caster_ts, "token_id", "") or ""),
                caster_name=str(getattr(caster_ts, "display_name", getattr(caster_ts, "token_id", "Caster")) or "Caster"),
                target_token_id=str(getattr(target_ts, "token_id", "") or ""),
                target_name=str(getattr(target_ts, "display_name", getattr(target_ts, "token_id", "Target")) or "Target"),
                ability=str(ability),
                dc=int(dc),
                damage_expr=str(damage_expr),
                rolled_damage=int(base_damage),
                damage_rolls=list(rolls or []),
                damage_mod=int(mod or 0),
                target_is_pc=bool(self._is_player_controlled_token(target_ts)),
            )
        except Exception:
            pass
        return req_id

    def _resolve_declared_aoe_spell(self, payload: Dict[str, Any], caster_ts, target_cell) -> bool:
        try:
            if caster_ts is None or not target_cell:
                return False
            spell_id = str((payload or {}).get("spell_id") or "").strip()
            spell_name = str((payload or {}).get("spell_name") or spell_id or "Spell").strip()
            target_cfg = dict((payload or {}).get("targeting") or {}) if isinstance((payload or {}).get("targeting"), dict) else {}
            template = dict(target_cfg.get("template") or {}) if isinstance(target_cfg.get("template"), dict) else {}
            cols = max(1, int(self.scene.sceneRect().width() // GRID_SIZE))
            rows = max(1, int(self.scene.sceneRect().height() // GRID_SIZE))
            affected_cells = self._compute_template_cells(caster_ts, (int(target_cell[0]), int(target_cell[1])), template, cols, rows)
            target_token_ids = self._tokens_in_cells(affected_cells)
            affects = str(target_cfg.get("affects") or "").strip().lower()
            if affects:
                target_token_ids = [tid for tid in list(target_token_ids or []) if self._spell_target_matches_affects(caster_ts, self.state.tokens.get(tid), affects)]
            if not target_token_ids:
                self._set_hud_status(f"{spell_name}: no targets in template.", hold_sec=3.0)
                return False
            effects = normalized_effects_from_spell(payload or {}, cast_level=int((payload or {}).get("slot_level", 0) or (payload or {}).get("spell_level", 0) or 0))
            damage_effect = next((fx for fx in effects if isinstance(fx, dict) and str(fx.get("type") or "").strip().lower() == "damage"), {})
            save_cfg = dict(damage_effect.get("save") or {}) if isinstance(damage_effect.get("save"), dict) else {}
            damage_expr = str(damage_effect.get("expr") or self._spell_damage_expr_from_payload(payload)).strip()
            base_damage = 0
            rolls = []
            mod = 0
            if damage_expr:
                base_damage, rolls, mod = self._roll_spell_damage_total(damage_expr)
            dc = int((((payload or {}).get("spellcasting") or {}) if isinstance((payload or {}).get("spellcasting"), dict) else {}).get("save_dc", 0) or 0)
            if save_cfg or str((payload or {}).get("save_type") or "").strip():
                ability = normalize_ability_key(save_cfg.get("ability") or (payload or {}).get("save_type", ""))
                on_success = str(save_cfg.get("on_success") or "half").strip().lower() or "half"
                for tid in target_token_ids:
                    target_ts = self.state.tokens.get(tid)
                    if not target_ts:
                        continue
                    deferred_effect = {
                        "base_damage": int(base_damage),
                        "save_on_success": str(on_success),
                        "pending_attack_id": f"spell:{spell_id}:{uuid.uuid4().hex[:8]}",
                        "source_kind": "spell",
                        "damage_type": str((((payload or {}).get("damage") or {}) if isinstance((payload or {}).get("damage"), dict) else {}).get("type", damage_effect.get("damage_type", "")) or ""),
                    }
                    condition_effect = next((fx for fx in effects if isinstance(fx, dict) and str(fx.get("type") or "").strip().lower() == "condition_apply"), {})
                    if isinstance(condition_effect, dict) and str(condition_effect.get("name") or condition_effect.get("condition") or "").strip():
                        cond_payload = {
                            "name": str(condition_effect.get("name") or condition_effect.get("condition") or "").strip(),
                            "source": spell_name,
                            "rounds_remaining": condition_effect.get("rounds_remaining", parse_rounds_from_duration((payload or {}).get("duration"))),
                            "save": dict(condition_effect.get("save") or {}),
                            "tick_damage": dict(condition_effect.get("tick_damage") or {}),
                            "meta": {
                                "spell_id": spell_id,
                                "spell_name": spell_name,
                                "caster_token_id": str(getattr(caster_ts, "token_id", "") or ""),
                                "concentration": bool((payload or {}).get("concentration", False)),
                            },
                        }
                        if isinstance(cond_payload.get("save"), dict) and int(cond_payload["save"].get("dc", 0) or 0) <= 0:
                            cond_payload["save"]["dc"] = int(dc or 10)
                        deferred_effect["apply_condition_on_fail"] = cond_payload
                    context = {
                        "kind": "spell_save",
                        "source_kind": "spell",
                        "spell_id": spell_id,
                        "spell_name": spell_name,
                        "caster_token_id": str(getattr(caster_ts, "token_id", "") or ""),
                        "caster_name": str(getattr(caster_ts, "display_name", getattr(caster_ts, "token_id", "Caster")) or "Caster"),
                    }
                    if self._is_player_controlled_token(target_ts):
                        req_id = self._register_pc_deferred_damage_save_request(target_ts, ability=ability, dc=int(dc or 10), mode="normal", label=f"{spell_name} Save", context=context, deferred_effect=deferred_effect)
                    else:
                        req_id = f"npc-save:{uuid.uuid4().hex[:10]}"
                        self._resolve_npc_save_request(target_ts, {"ability": ability, "dc": int(dc or 10), "adv_mode": "normal", "label": f"{spell_name} Save", "context": context, "deferred_effect": deferred_effect})
                try:
                    self.campaign_logger.combat("spell_aoe_save_requested", spell_id=spell_id, spell_name=spell_name, caster_token_id=str(getattr(caster_ts, "token_id", "") or ""), target_count=int(len(target_token_ids)), damage_expr=str(damage_expr), rolled_damage=int(base_damage), damage_rolls=list(rolls or []), damage_mod=int(mod or 0), dc=int(dc or 10), ability=str(ability), target_cell=[int(target_cell[0]), int(target_cell[1])])
                except Exception:
                    pass
            else:
                applied = 0
                for tid in target_token_ids:
                    target_ts = self.state.tokens.get(tid)
                    if not target_ts:
                        continue
                    applied_any = False
                    for effect in effects:
                        if self._apply_spell_effect_to_target(caster_ts, target_ts, effect, payload):
                            applied_any = True
                    if applied_any:
                        applied += 1
                if bool((payload or {}).get("concentration", False)):
                    self._begin_spell_concentration(caster_ts, payload)
                try:
                    self.campaign_logger.combat("spell_aoe_effect_applied", spell_id=spell_id, spell_name=spell_name, caster_token_id=str(getattr(caster_ts, "token_id", "") or ""), target_count=int(applied), target_cell=[int(target_cell[0]), int(target_cell[1])])
                except Exception:
                    pass
            self._aoe_active = False
            self._aoe_locked = False
            self._aoe_locked_cell = None
            self._aoe_target_cell = None
            try:
                self._push_aoe_template_to_player_view()
            except Exception:
                pass
            self.view.viewport().update()
            return True
        except Exception as e:
            print("[SPELL] aoe resolution failed:", e)
            return False


    def handle_spell_reaction_declaration_payload(self, payload: Dict[str, Any]) -> None:
        try:
            if not isinstance(payload, dict):
                return
            spell_name = str((payload or {}).get("spell_name") or (payload or {}).get("spell_id") or "Reaction Spell")
            caster_name = str((payload or {}).get("character_name") or (payload or {}).get("character_id") or "Player")
            trigger = str((payload or {}).get("reaction_trigger") or "").strip()
            target_hint = str((payload or {}).get("target_hint") or "").strip()
            try:
                self.campaign_logger.combat(
                    "spell_reaction_declared",
                    spell_id=str((payload or {}).get("spell_id") or ""),
                    spell_name=spell_name,
                    caster_name=caster_name,
                    character_id=str((payload or {}).get("character_id") or ""),
                    target_hint=target_hint,
                    reaction_trigger=trigger,
                )
            except Exception:
                pass
            self.handle_spell_declaration_payload(payload)
            msg = f"Reaction spell declared: {caster_name} → {spell_name}"
            if trigger:
                msg += f" ({trigger})"
            self._set_hud_status(msg, hold_sec=5.0)
        except Exception as e:
            print("[SPELL] Failed to handle reaction spell declaration:", e)

    def handle_spell_declaration_payload(self, payload: Dict[str, Any]) -> None:
        try:
            spell_name = str((payload or {}).get("spell_name") or (payload or {}).get("spell_id") or "Spell")
            caster_name = str((payload or {}).get("character_name") or (payload or {}).get("character_id") or "PC")
            target_mode = str((payload or {}).get("target_mode") or "").strip().lower()
            slot_level = int((payload or {}).get("slot_level", 0) or 0)
            range_ft = int((payload or {}).get("range_ft", 0) or 0)
            target_hint = str((payload or {}).get("target_hint") or "").strip()
            notes = str((payload or {}).get("notes") or "").strip()
            msg = f"Spell declared: {caster_name} → {spell_name}"
            if bool((payload or {}).get("reaction")):
                msg = f"Reaction spell declared: {caster_name} → {spell_name}"
            if slot_level > 0:
                msg += f" (slot {slot_level})"
            if target_mode:
                msg += f" [{target_mode}]"
            if range_ft > 0:
                msg += f" range {range_ft} ft"
            if target_hint:
                msg += f" target={target_hint}"
            if notes:
                msg += f" | {notes}"
            try:
                self._append_history_entry(msg)
            except Exception:
                pass
            try:
                self._set_hud_status(msg, hold_sec=5.0)
            except Exception:
                pass

            caster_ts = self._find_token_for_spell_character(str((payload or {}).get("character_id") or ""))
            if caster_ts is None:
                return

            try:
                if not bool((payload or {}).get("reaction", False)) and str((payload or {}).get("spell_id") or "").strip().lower() != "counterspell":
                    for ts, slot_options, suggested_slot in self._iter_counterspell_candidates(dict(payload or {})):
                        if self._is_player_controlled_token(ts):
                            if self._offer_reaction_choice(
                                ts,
                                reaction_kind="counterspell_spell",
                                spell_id="counterspell",
                                text=f"Reaction available: Counterspell {spell_name}?",
                                context={
                                    "payload": dict(payload or {}),
                                    "slot_level": int(suggested_slot or 3),
                                    "slot_options": [int(x) for x in slot_options],
                                    "recommended_slot_level": int(suggested_slot or 3),
                                    "on_success_action": {"type": "cancel_spell", "payload": dict(payload or {}), "reason": "counterspell", "message": f"{spell_name} is canceled by Counterspell."},
                                    "on_fail_action": {"type": "resume_spell", "payload": dict(payload or {})},
                                },
                            ):
                                return
                        else:
                            npc_slot = int(suggested_slot or 0)
                            if npc_slot <= 0:
                                continue
                            self.server.use_reaction_spell(str(getattr(ts, "character_id", "") or ""), spell_id="counterspell", slot_level=npc_slot, note="counterspell")
                            self._consume_reaction(ts, reason="counterspell")
                            self._attempt_counterspell_with_chain(
                                ts,
                                dict(payload or {}),
                                slot_level=npc_slot,
                                on_success={"type": "cancel_spell", "payload": dict(payload or {}), "reason": "counterspell", "message": f"{spell_name} is canceled by Counterspell."},
                                on_fail={"type": "resume_spell", "payload": dict(payload or {})},
                            )
                            return
            except Exception as e:
                print("[REACTION] counterspell offer failed:", e)

            target_list = self._resolve_declared_spell_targets(payload, caster_ts, target_hint)
            target_ts = target_list[0] if target_list else None

            if bool((payload or {}).get("attack_roll", False)) or target_mode in {"attack", "attack_roll"}:
                if target_ts is not None:
                    self._arm_pending_pc_spell_attack(caster_ts, target_ts, str((payload or {}).get("spell_id") or ""))
                else:
                    self._set_hud_status(f"{spell_name} declared. Pick a target token, then re-declare or arm manually.", hold_sec=5.0)
                return

            if target_mode in {"template", "aoe"}:
                if self._prime_declared_aoe_spell(payload, caster_ts):
                    self._set_hud_status(f"{spell_name} primed for AoE placement.", hold_sec=5.0)
                return

            save_type = str((payload or {}).get("save_type") or "").strip()
            if not save_type and not bool((payload or {}).get("attack_roll", False)) and target_mode not in {"template", "aoe"}:
                if self._apply_declared_non_attack_spell_effects(payload, caster_ts, target_ts, target_list=target_list):
                    applied_n = max(1, len(target_list)) if target_list else 1
                    self._set_hud_status(f"{spell_name} applied to {applied_n} target(s).", hold_sec=5.0)
                else:
                    self._set_hud_status(f"{spell_name} declared. No automated effect path matched yet.", hold_sec=5.0)
                return

            if save_type:
                if target_list:
                    req_ids = []
                    for resolved_target in target_list:
                        req_id = self._register_declared_spell_save(payload, caster_ts, resolved_target)
                        if req_id:
                            req_ids.append(req_id)
                    if req_ids:
                        self._set_hud_status(f"Requested {save_type.upper()} save for {len(req_ids)} target(s).", hold_sec=5.0)
                    else:
                        self._set_hud_status(f"Failed to register save for {spell_name}.", hold_sec=5.0)
                else:
                    self._set_hud_status(f"{spell_name} declared. No target token matched the target hint.", hold_sec=5.0)
                return
        except Exception as e:
            print("[SPELL] Failed to handle spell declaration:", e)

    def _token_distance_ft(self, a_ts, b_ts) -> int:
        try:
            agx = int(getattr(a_ts, "grid_x", 0) or 0)
            agy = int(getattr(a_ts, "grid_y", 0) or 0)
            bgx = int(getattr(b_ts, "grid_x", 0) or 0)
            bgy = int(getattr(b_ts, "grid_y", 0) or 0)
            return int(max(abs(agx - bgx), abs(agy - bgy)) * 5)
        except Exception:
            return 9999

    def _spellcasting_ability_mod_for_token(self, ts) -> int:
        try:
            if ts is None or not hasattr(self, "server") or self.server is None:
                return 0
            sheet = self.server.get_character_sheet(str(getattr(ts, "character_id", "") or "")) or {}
            sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
            ability = str(sc.get("ability") or "").strip().lower()
            stats = sheet.get("stats") if isinstance(sheet.get("stats"), dict) else {}
            val = int(stats.get(ability, getattr(ts, ability, 10) if ability else 10) or 10)
            return (val - 10) // 2
        except Exception:
            return 0

    def _choose_counterspell_slot_level(self, reactor_ts, spell_payload: Dict[str, Any]) -> int:
        try:
            char_id = str(getattr(reactor_ts, "character_id", "") or "").strip()
            spell_level = int((spell_payload or {}).get("slot_level", 0) or (spell_payload or {}).get("spell_level", 0) or 0)
            available = self._available_spell_slot_levels(char_id, "counterspell", min_slot_level=3)
            if not available:
                return 0
            if spell_level > 0:
                for lvl in available:
                    if int(lvl) >= int(spell_level):
                        return int(lvl)
            return int(max(available))
        except Exception:
            return 0

    def _spell_payload_name(self, spell_payload: Dict[str, Any]) -> str:
        try:
            return str((spell_payload or {}).get("spell_name") or (spell_payload or {}).get("name") or (spell_payload or {}).get("spell_id") or "Spell").strip() or "Spell"
        except Exception:
            return "Spell"

    def _build_counterspell_cast_payload(self, reactor_ts, target_spell_payload: Dict[str, Any], *, slot_level: int = 3) -> Dict[str, Any]:
        try:
            target_payload = dict(target_spell_payload or {})
            chain_depth = int(target_payload.get("chain_depth", 0) or 0)
            return {
                "character_id": str(getattr(reactor_ts, "character_id", "") or ""),
                "caster_token_id": str(getattr(reactor_ts, "token_id", "") or ""),
                "caster_name": str(getattr(reactor_ts, "display_name", getattr(reactor_ts, "token_id", "Counterspeller")) or "Counterspeller"),
                "spell_id": "counterspell",
                "spell_name": "Counterspell",
                "spell_level": 3,
                "slot_level": int(slot_level or 3),
                "reaction": True,
                "reaction_trigger": "when you see a creature cast a spell",
                "target_mode": "spell",
                "target_hint": self._spell_payload_name(target_payload),
                "counterspell_target_payload": target_payload,
                "chain_depth": int(chain_depth + 1),
            }
        except Exception:
            return {
                "spell_id": "counterspell",
                "spell_name": "Counterspell",
                "spell_level": 3,
                "slot_level": int(slot_level or 3),
                "reaction": True,
                "chain_depth": 1,
            }

    def _iter_counterspell_candidates(self, spell_payload: Dict[str, Any]):
        try:
            caster_token_id = str((spell_payload or {}).get("caster_token_id") or "").strip()
            caster_ts = self.state.tokens.get(caster_token_id) if caster_token_id else None
            if caster_ts is None:
                caster_ts = self._find_token_for_spell_character(str((spell_payload or {}).get("character_id") or ""))
            if caster_ts is None:
                return []
            out = []
            for ts in list((getattr(self.state, "tokens", {}) or {}).values()):
                if ts is None:
                    continue
                if str(getattr(ts, "token_id", "") or "") == str(getattr(caster_ts, "token_id", "") or ""):
                    continue
                if str(getattr(ts, "side", "") or "") == str(getattr(caster_ts, "side", "") or ""):
                    continue
                if not self._token_can_take_reaction(ts):
                    continue
                slot_options = self._available_spell_slot_levels(str(getattr(ts, "character_id", "") or ""), "counterspell", min_slot_level=3)
                if not slot_options:
                    continue
                out.append((ts, [int(x) for x in slot_options], int(self._choose_counterspell_slot_level(ts, dict(spell_payload or {})) or min(slot_options) or 3)))
            return out
        except Exception:
            return []

    def _execute_reaction_continuation(self, action: Dict[str, Any] | None) -> None:
        try:
            if not isinstance(action, dict):
                return
            action_type = str(action.get("type") or "").strip().lower()
            if action_type == "resume_spell":
                payload = dict(action.get("payload") or {})
                if payload:
                    self.handle_spell_declaration_payload(payload)
                return
            if action_type == "cancel_spell":
                payload = dict(action.get("payload") or {})
                canceled_name = self._spell_payload_name(payload)
                message = str(action.get("message") or f"{canceled_name} is canceled.").strip() or f"{canceled_name} is canceled."
                self._set_hud_status(message, hold_sec=4.0)
                try:
                    self.campaign_logger.combat(
                        "spell_canceled",
                        spell_id=str(payload.get("spell_id") or ""),
                        spell_name=canceled_name,
                        chain_depth=int(payload.get("chain_depth", 0) or 0),
                        reason=str(action.get("reason") or "counterspell"),
                    )
                except Exception:
                    pass
                return
        except Exception as e:
            print("[REACTION] continuation failed:", e)

    def _resolve_counterspell_base_with_actions(self, reactor_ts, target_spell_payload: Dict[str, Any], *, slot_level: int = 3, on_success: Dict[str, Any] | None = None, on_fail: Dict[str, Any] | None = None) -> None:
        try:
            target_name = self._spell_payload_name(target_spell_payload)
            if self._resolve_counterspell_attempt(reactor_ts, target_spell_payload, slot_level=int(slot_level or 3)):
                self._set_hud_status(f"{getattr(reactor_ts, 'display_name', 'Reactor')} counterspells {target_name}.", hold_sec=4.0)
                self._execute_reaction_continuation(on_success)
                return
            self._set_hud_status(f"{getattr(reactor_ts, 'display_name', 'Reactor')} fails to counterspell {target_name}.", hold_sec=4.0)
            self._execute_reaction_continuation(on_fail)
        except Exception as e:
            print("[REACTION] base counterspell resolve failed:", e)
            self._execute_reaction_continuation(on_fail)

    def _attempt_counterspell_with_chain(self, reactor_ts, target_spell_payload: Dict[str, Any], *, slot_level: int = 3, on_success: Dict[str, Any] | None = None, on_fail: Dict[str, Any] | None = None) -> str:
        try:
            counterspell_payload = self._build_counterspell_cast_payload(reactor_ts, target_spell_payload, slot_level=int(slot_level or 3))
            countered_name = self._spell_payload_name(counterspell_payload)
            for candidate_ts, slot_options, suggested_slot in self._iter_counterspell_candidates(counterspell_payload):
                deeper_success = dict(on_fail or {}) if isinstance(on_fail, dict) else None
                deeper_fail = {
                    "type": "resolve_counterspell_base",
                    "reactor_token_id": str(getattr(reactor_ts, "token_id", "") or ""),
                    "target_payload": dict(target_spell_payload or {}),
                    "slot_level": int(slot_level or 3),
                    "on_success": dict(on_success or {}) if isinstance(on_success, dict) else None,
                    "on_fail": dict(on_fail or {}) if isinstance(on_fail, dict) else None,
                }
                if self._is_player_controlled_token(candidate_ts):
                    offered = self._offer_reaction_choice(
                        candidate_ts,
                        reaction_kind="counterspell_spell",
                        spell_id="counterspell",
                        text=f"Reaction available: Counterspell {countered_name}?",
                        context={
                            "payload": dict(counterspell_payload or {}),
                            "slot_level": int(suggested_slot or 3),
                            "slot_options": [int(x) for x in slot_options],
                            "recommended_slot_level": int(suggested_slot or 3),
                            "on_success_action": deeper_success,
                            "on_fail_action": deeper_fail,
                        },
                    )
                    if offered:
                        return "pending"
                else:
                    npc_slot = int(suggested_slot or 0)
                    if npc_slot <= 0:
                        continue
                    self.server.use_reaction_spell(str(getattr(candidate_ts, "character_id", "") or ""), spell_id="counterspell", slot_level=npc_slot, note="counterspell")
                    self._consume_reaction(candidate_ts, reason="counterspell")
                    return self._attempt_counterspell_with_chain(candidate_ts, counterspell_payload, slot_level=npc_slot, on_success=deeper_success, on_fail=deeper_fail)
            self._resolve_counterspell_base_with_actions(reactor_ts, target_spell_payload, slot_level=int(slot_level or 3), on_success=on_success, on_fail=on_fail)
            return "resolved"
        except Exception as e:
            print("[REACTION] counterspell chain failed:", e)
            self._execute_reaction_continuation(on_fail)
            return "resolved"

    def _run_reaction_action(self, action: Dict[str, Any] | None) -> None:
        try:
            if not isinstance(action, dict):
                return
            action_type = str(action.get("type") or "").strip().lower()
            if action_type == "resolve_counterspell_base":
                reactor_ts = self.state.tokens.get(str(action.get("reactor_token_id") or ""))
                if reactor_ts is None:
                    self._execute_reaction_continuation(action.get("on_fail") if isinstance(action.get("on_fail"), dict) else None)
                    return
                self._resolve_counterspell_base_with_actions(
                    reactor_ts,
                    dict(action.get("target_payload") or {}),
                    slot_level=int(action.get("slot_level", 3) or 3),
                    on_success=dict(action.get("on_success") or {}) if isinstance(action.get("on_success"), dict) else None,
                    on_fail=dict(action.get("on_fail") or {}) if isinstance(action.get("on_fail"), dict) else None,
                )
                return
            self._execute_reaction_continuation(action)
        except Exception as e:
            print("[REACTION] run action failed:", e)

    def _resolve_counterspell_attempt(self, reactor_ts, spell_payload: Dict[str, Any], *, slot_level: int = 3) -> bool:
        try:
            spell_level = int((spell_payload or {}).get("slot_level", 0) or (spell_payload or {}).get("spell_level", 0) or 0)
            if int(slot_level or 0) >= int(spell_level or 0):
                return True
            mod = int(self._spellcasting_ability_mod_for_token(reactor_ts) or 0)
            dc = 10 + int(spell_level or 0)
            roll = random.randint(1, 20) + mod
            ok = int(roll) >= int(dc)
            try:
                self.campaign_logger.combat(
                    "counterspell_check",
                    reactor_token_id=str(getattr(reactor_ts, "token_id", "") or ""),
                    reactor_name=str(getattr(reactor_ts, "display_name", getattr(reactor_ts, "token_id", "Reactor")) or "Reactor"),
                    spell_id=str((spell_payload or {}).get("spell_id") or ""),
                    spell_name=str((spell_payload or {}).get("spell_name") or (spell_payload or {}).get("spell_id") or "Spell"),
                    spell_level=int(spell_level or 0),
                    counterspell_slot=int(slot_level or 0),
                    roll=int(roll),
                    dc=int(dc),
                    success=bool(ok),
                )
            except Exception:
                pass
            return bool(ok)
        except Exception:
            return False

    def _execute_hellish_rebuke(self, reactor_ts, attacker_ts, *, slot_level: int = 1) -> bool:
        try:
            if reactor_ts is None or attacker_ts is None:
                return False
            spell_row = (getattr(self, "spells_db", {}) or {}).get("hellish_rebuke") or {}
            base_expr = "2d10"
            try:
                dmg = spell_row.get("damage") if isinstance(spell_row.get("damage"), dict) else {}
                base_expr = str((dmg or {}).get("expr") or base_expr)
            except Exception:
                pass
            payload = {
                "reaction": True,
                "reaction_trigger": "when you take damage from a creature within 60 feet",
                "spell_id": "hellish_rebuke",
                "spell_name": str(spell_row.get("name") or "Hellish Rebuke"),
                "character_id": str(getattr(reactor_ts, "character_id", "") or ""),
                "character_name": str(getattr(reactor_ts, "display_name", getattr(reactor_ts, "token_id", "Caster")) or "Caster"),
                "slot_level": int(slot_level or 1),
                "spell_level": int(spell_row.get("level", 1) or 1),
                "target_mode": "single",
                "range_ft": 60,
                "target_hint": str(getattr(attacker_ts, "display_name", getattr(attacker_ts, "token_id", "Target")) or "Target"),
                "save_type": str(spell_row.get("save_type") or "dex"),
                "damage": dict(spell_row.get("damage") or {"expr": base_expr, "type": "fire"}),
                "targeting": dict(spell_row.get("targeting") or {"kind": "single", "affects": "enemies"}),
                "effects": list(spell_row.get("effects") or []),
                "concentration": False,
                "notes": "hellish rebuke",
            }
            self.handle_spell_reaction_declaration_payload(payload)
            return True
        except Exception as e:
            print("[REACTION] hellish rebuke execute failed:", e)
            return False

    def _maybe_offer_hellish_rebuke(self, reactor_ts, *, source_kind: str = "", source_meta: dict | None = None, final_damage: int = 0) -> bool:
        try:
            if reactor_ts is None or int(final_damage or 0) <= 0:
                return False
            if not self._token_can_take_reaction(reactor_ts):
                return False
            if not self._sheet_has_available_spell(str(getattr(reactor_ts, "character_id", "") or ""), "hellish_rebuke", min_slot_level=1):
                return False
            meta = source_meta if isinstance(source_meta, dict) else {}
            attacker_token_id = str(meta.get("attacker_token_id") or meta.get("caster_token_id") or meta.get("source_token_id") or "").strip()
            if not attacker_token_id:
                return False
            attacker_ts = self.state.tokens.get(attacker_token_id)
            if attacker_ts is None:
                return False
            if str(getattr(attacker_ts, "side", "") or "") == str(getattr(reactor_ts, "side", "") or ""):
                return False
            if self._token_distance_ft(reactor_ts, attacker_ts) > 60:
                return False
            slot_options = self._available_spell_slot_levels(str(getattr(reactor_ts, "character_id", "") or ""), "hellish_rebuke", min_slot_level=1)
            chosen_slot = int(max(slot_options) if slot_options else 1)
            context = {"attacker_token_id": attacker_token_id, "slot_level": int(chosen_slot or 1)}
            if len(slot_options) > 1:
                context["slot_options"] = [int(x) for x in slot_options]
                context["recommended_slot_level"] = int(chosen_slot or 1)
            if self._is_player_controlled_token(reactor_ts):
                return self._offer_reaction_choice(reactor_ts, reaction_kind="hellish_rebuke_damage", spell_id="hellish_rebuke", text=f"Reaction available: cast Hellish Rebuke on {getattr(attacker_ts, 'display_name', 'attacker')}?", context=context)
            self.server.use_reaction_spell(str(getattr(reactor_ts, "character_id", "") or ""), spell_id="hellish_rebuke", slot_level=int(chosen_slot or 1), note="hellish_rebuke")
            self._consume_reaction(reactor_ts, reason="hellish_rebuke")
            self._execute_hellish_rebuke(reactor_ts, attacker_ts, slot_level=int(chosen_slot or 1))
            return True
        except Exception as e:
            print("[REACTION] hellish rebuke offer failed:", e)
            return False

    def _get_reaction_window(self) -> dict:
        raw = getattr(self, "_reaction_window", None)
        return raw if isinstance(raw, dict) else {}

    def _queue_reaction_window(self, window: dict) -> None:
        q = getattr(self, "_reaction_window_queue", None)
        if not isinstance(q, list):
            q = []
        q.append(dict(window or {}))
        setattr(self, "_reaction_window_queue", q)

    def _clear_reaction_window(self) -> None:
        try:
            q = getattr(self, "_reaction_window_queue", None)
            if isinstance(q, list) and q:
                nxt = q.pop(0)
                setattr(self, "_reaction_window_queue", q)
                setattr(self, "_reaction_window", nxt)
                try:
                    msg_data = {
                        "type": "REACTION_CHOICE",
                        "request_id": str(nxt.get("request_id") or ""),
                        "reaction_kind": str(nxt.get("reaction_kind") or ""),
                        "spell_id": str(nxt.get("spell_id") or ""),
                    }
                    if isinstance(nxt.get("slot_options"), list):
                        msg_data["slot_options"] = [int(x) for x in list(nxt.get("slot_options") or []) if str(x).strip()]
                    if int(nxt.get("slot_level", 0) or 0) > 0:
                        msg_data["slot_level"] = int(nxt.get("slot_level", 0) or 0)
                    if int(nxt.get("recommended_slot_level", 0) or 0) > 0:
                        msg_data["recommended_slot_level"] = int(nxt.get("recommended_slot_level", 0) or 0)
                    self.server.post_message(
                        str(nxt.get("reactor_player_id") or ""),
                        str(nxt.get("prompt_text") or "Reaction available."),
                        "warn",
                        ttl_seconds=3600,
                        data=msg_data,
                    )
                except Exception:
                    pass
                return
            setattr(self, "_reaction_window", None)
        except Exception:
            pass

    def _available_spell_slot_levels(self, character_id: str, spell_id: str, *, min_slot_level: int = 1) -> list[int]:
        try:
            if not character_id or not hasattr(self, "server") or self.server is None:
                return []
            sheet = self.server.get_character_sheet(str(character_id or "").strip()) or {}
            if not isinstance(sheet, dict) or not sheet:
                return []
            sc = sheet.get("spellcasting") if isinstance(sheet.get("spellcasting"), dict) else {}
            spell_id = str(spell_id or "").strip()
            known_sets = []
            for key in ("known_spell_ids", "prepared_spell_ids", "always_prepared_spell_ids", "spellbook_spell_ids"):
                vals = {str(x).strip() for x in list(sc.get(key) or []) if str(x).strip()}
                known_sets.append(vals)
            if spell_id and not any(spell_id in vals for vals in known_sets):
                return []
            min_slot = int(min_slot_level or 0)
            if min_slot <= 0:
                return [0]
            out: list[int] = []
            slots = sc.get("spell_slots") if isinstance(sc.get("spell_slots"), dict) else {}
            for lvl in range(min_slot, 10):
                row = slots.get(str(lvl)) if isinstance(slots, dict) else None
                if isinstance(row, dict) and int(row.get("remaining", 0) or 0) > 0:
                    out.append(int(lvl))
            pact = sc.get("pact_magic") if isinstance(sc.get("pact_magic"), dict) else {}
            pact_level = int(pact.get("slot_level", 0) or 0)
            if int(pact.get("remaining", 0) or 0) > 0 and pact_level >= min_slot and pact_level not in out:
                out.append(int(pact_level))
            return sorted({int(x) for x in out if int(x) >= min_slot})
        except Exception:
            return []

    def _sheet_has_available_spell(self, character_id: str, spell_id: str, *, min_slot_level: int = 1) -> bool:
        try:
            return bool(self._available_spell_slot_levels(character_id, spell_id, min_slot_level=int(min_slot_level or 0)))
        except Exception:
            return False

    def _offer_reaction_choice(self, reactor_ts, *, reaction_kind: str, spell_id: str, text: str, context: dict) -> bool:
        try:
            if reactor_ts is None or not self._token_can_take_reaction(reactor_ts):
                return False
            player_id = str(getattr(reactor_ts, "player_id", "") or "").strip()
            if not player_id:
                return False
            req_id = uuid.uuid4().hex
            window = dict(context or {})
            window.update({
                "request_id": req_id,
                "reaction_kind": str(reaction_kind or ""),
                "spell_id": str(spell_id or ""),
                "reactor_token_id": str(getattr(reactor_ts, "token_id", "") or ""),
                "reactor_character_id": str(getattr(reactor_ts, "character_id", "") or ""),
                "reactor_player_id": player_id,
            })
            window["prompt_text"] = str(text or "")
            current = self._get_reaction_window()
            if current:
                self._queue_reaction_window(window)
                self._set_hud_status(f"Queued reaction: {text}", hold_sec=4.0)
                return True
            setattr(self, "_reaction_window", window)
            msg_data = {
                "type": "REACTION_CHOICE",
                "request_id": req_id,
                "reaction_kind": str(reaction_kind or ""),
                "spell_id": str(spell_id or ""),
            }
            if isinstance(window.get("slot_options"), list):
                msg_data["slot_options"] = [int(x) for x in list(window.get("slot_options") or []) if str(x).strip()]
            if int(window.get("slot_level", 0) or 0) > 0:
                msg_data["slot_level"] = int(window.get("slot_level", 0) or 0)
            if int(window.get("recommended_slot_level", 0) or 0) > 0:
                msg_data["recommended_slot_level"] = int(window.get("recommended_slot_level", 0) or 0)
            self.server.post_message(
                player_id,
                text,
                "warn",
                ttl_seconds=3600,
                data=msg_data,
            )
            self._set_hud_status(text, hold_sec=6.0)
            return True
        except Exception as e:
            print("[REACTION] offer failed:", e)
            return False

    def _apply_shield_reaction_status(self, reactor_ts) -> bool:
        try:
            if reactor_ts is None:
                return False
            cond = canonical_condition_record({
                "name": "Shield",
                "source": "Shield",
                "rounds_remaining": 1,
                "meta": {
                    "spell_id": "shield",
                    "spell_name": "Shield",
                    "caster_token_id": str(getattr(reactor_ts, "token_id", "") or ""),
                    "effect_kind": "modifier",
                    "modifiers": [{"field": "ac", "amount": 5}],
                },
            })
            current = [canonical_condition_record(s) for s in list(getattr(reactor_ts, "statuses", []) or []) if isinstance(s, dict)]
            current.append(cond)
            reactor_ts.statuses = current
            self._apply_spell_numeric_modifier(reactor_ts, field="ac", amount=5, apply_now=True)
            self._sync_token_statuses_to_sheet(reactor_ts)
            self._sync_spell_modified_stats_to_sheet(reactor_ts)
            self._consume_reaction(reactor_ts, reason="shield")
            return True
        except Exception as e:
            print("[REACTION] shield apply failed:", e)
            return False

    def handle_reaction_response_payload(self, payload: Dict[str, Any]) -> None:
        try:
            window = self._get_reaction_window()
            if not window:
                return
            if str((payload or {}).get("request_id") or "").strip() != str(window.get("request_id") or "").strip():
                return
            choice = str((payload or {}).get("choice") or "decline").strip().lower()
            reactor_ts = self.state.tokens.get(str(window.get("reactor_token_id") or ""))
            if choice == "accept" and reactor_ts is not None:
                response_payload = dict((payload or {}).get("payload") or {}) if isinstance((payload or {}).get("payload"), dict) else {}
                chosen_slot_level = int(response_payload.get("slot_level", window.get("slot_level", 0)) or window.get("slot_level", 0) or 0)
                spell_id = str(window.get("spell_id") or "")
                if spell_id:
                    self.server.use_reaction_spell(str(window.get("reactor_character_id") or ""), spell_id=spell_id, slot_level=chosen_slot_level, note=str(window.get("reaction_kind") or "reaction"))
                if str(window.get("reaction_kind") or "") == "shield_attack":
                    self._apply_shield_reaction_status(reactor_ts)
                    pending = dict(window.get("pending") or {})
                    setattr(self.state, "pending_attack", pending)
                    self._clear_reaction_window()
                    self.handle_roll_payload(dict(window.get("roll_payload") or {}))
                    return
                if str(window.get("reaction_kind") or "") == "npc_shield_attack":
                    self._apply_shield_reaction_status(reactor_ts)
                    attacker_ts = self.state.tokens.get(str(window.get("attacker_token_id") or ""))
                    target_ts = self.state.tokens.get(str(window.get("target_token_id") or ""))
                    preview = dict(window.get("npc_attack_preview") or {})
                    self._clear_reaction_window()
                    if attacker_ts is not None and target_ts is not None:
                        from engine.combat_models import NpcAttackOutcome
                        out = NpcAttackOutcome(
                            d20=int(preview.get("d20", 0) or 0),
                            total_to_hit=int(preview.get("total_to_hit", 0) or 0),
                            target_ac=int(preview.get("target_ac", 10) or 10) + 5,
                            is_hit=False,
                            damage_roll_expr="",
                            damage_total=0,
                        )
                        self._finalize_npc_attack_preview(attacker_ts, target_ts, out, encounter_id=str(window.get("encounter_id") or ""))
                    return
                if str(window.get("reaction_kind") or "") == "counterspell_spell":
                    pending_payload = dict(window.get("payload") or {})
                    on_success_action = dict(window.get("on_success_action") or {}) if isinstance(window.get("on_success_action"), dict) else None
                    on_fail_action = dict(window.get("on_fail_action") or {}) if isinstance(window.get("on_fail_action"), dict) else None
                    self._consume_reaction(reactor_ts, reason="counterspell")
                    self._clear_reaction_window()
                    self._attempt_counterspell_with_chain(
                        reactor_ts,
                        pending_payload,
                        slot_level=int(chosen_slot_level or window.get("slot_level", 3) or 3),
                        on_success=on_success_action,
                        on_fail=on_fail_action,
                    )
                    return
                if str(window.get("reaction_kind") or "") == "hellish_rebuke_damage":
                    self._consume_reaction(reactor_ts, reason="hellish_rebuke")
                    attacker_ts = self.state.tokens.get(str(window.get("attacker_token_id") or ""))
                    self._clear_reaction_window()
                    self._execute_hellish_rebuke(reactor_ts, attacker_ts, slot_level=int(chosen_slot_level or window.get("slot_level", 1) or 1))
                    return
                if str(window.get("reaction_kind") or "") == "wrath_of_the_storm_attack":
                    self._consume_reaction(reactor_ts, reason="wrath_of_the_storm")
                    attacker_ts = self.state.tokens.get(str(window.get("attacker_token_id") or ""))
                    self._clear_reaction_window()
                    self._execute_wrath_of_the_storm(
                        reactor_ts,
                        attacker_ts,
                        dice_count=int(window.get("dice_count", 2) or 2),
                        save_dc=int(window.get("save_dc", 0) or 0),
                    )
                    return
            if str(window.get("reaction_kind") or "") == "shield_attack":
                setattr(self.state, "pending_attack", dict(window.get("pending") or {}))
                self._clear_reaction_window()
                self.handle_roll_payload(dict(window.get("roll_payload") or {}))
                return
            if str(window.get("reaction_kind") or "") == "npc_shield_attack":
                attacker_ts = self.state.tokens.get(str(window.get("attacker_token_id") or ""))
                target_ts = self.state.tokens.get(str(window.get("target_token_id") or ""))
                preview = dict(window.get("npc_attack_preview") or {})
                self._clear_reaction_window()
                if attacker_ts is not None and target_ts is not None:
                    from engine.combat_models import NpcAttackOutcome
                    out = NpcAttackOutcome(
                        d20=int(preview.get("d20", 0) or 0),
                        total_to_hit=int(preview.get("total_to_hit", 0) or 0),
                        target_ac=int(preview.get("target_ac", 10) or 10),
                        is_hit=bool(preview.get("is_hit", False)),
                        damage_roll_expr=str(preview.get("damage_roll_expr", "") or ""),
                        damage_total=int(preview.get("damage_total", 0) or 0),
                    )
                    self._finalize_npc_attack_preview(attacker_ts, target_ts, out, encounter_id=str(window.get("encounter_id") or ""))
                return
            if str(window.get("reaction_kind") or "") == "counterspell_spell":
                on_fail_action = dict(window.get("on_fail_action") or {}) if isinstance(window.get("on_fail_action"), dict) else None
                self._clear_reaction_window()
                self._run_reaction_action(on_fail_action)
                return
            self._clear_reaction_window()
        except Exception as e:
            print("[REACTION] response payload failed:", e)

    def send_item_to_player(self, player_id, item):
        # Placeholder logic — later, this would send via socket or HTTP to player
        print(f"Sending item '{item['name']}' to player {player_id}")
        # You can add networking or queue code here

    def open_player_view(self):
        if self.player_view_window is None:
            self.player_view_window = PlayerViewWindow(self.state)
        else:
            # If DM state object was replaced (load/new encounter), keep Player View bound to the current state.
            try:
                self.player_view_window.state = self.state
            except Exception:
                pass
        self.player_view_window.show()
        self.player_view_window.refresh()

    def refresh_player_view(self):
        """
        Player View is a separate window. Refresh only if it exists and is visible.
        PlayerViewWindow is responsible for computing PC overlays + AoE previews
        from EncounterState + its own selection/template fields.
        """
        if self.player_view_window is None:
            return
        if not self.player_view_window.isVisible():
            return
        # Keep Player View bound to the current EncounterState (load/new encounter can replace self.state).
        try:
            self.player_view_window.state = self.state
        except Exception:
            pass


        # Update AoE template payload (if preview is active)
        self._push_aoe_template_to_player_view()

        # Phase B2: push current map metadata (walls/blocked/terrain) for engine-side LOS.
        # PlayerViewWindow must not do rules logic; it only renders.
        try:
            if self.player_view_window is not None:
                setattr(self.player_view_window, "map_meta", getattr(self, "current_map_meta", {}) or {})
                # BX2: push encounter door overrides so fog/LOS reflect opened doors
                try:
                    if getattr(self.player_view_window, "state", None) is not None:
                        setattr(self.player_view_window.state, "door_state", getattr(self.state, "door_state", {}) or {})
                except Exception:
                        pass
                # legacy/window-level attribute (harmless)
                setattr(self.player_view_window, "door_state", getattr(self.state, "door_state", {}) or {})
        except Exception:
            pass

        # Render player view
        self.player_view_window.refresh()

    def sync_token_positions_to_state(self) -> None:
        """
        Keep EncounterState positions aligned with scene tokens *without* breaking history determinism.

        Rule:
        - If any token position differs, commit it through a deterministic SET_POSITION event.
        - This prevents "silent" state mutation that Undo/Redo cannot reproduce.
        """
        any_changed = False

        for t in list(getattr(self, "token_items", []) or []):
            token_id = getattr(t, "token_id", None)
            if not token_id:
                continue
            if token_id not in self.state.tokens:
                continue

            ts = self.state.tokens[token_id]
            gx, gy = self.token_grid_xy(t)

            old_gx = int(getattr(ts, "grid_x", 0) or 0)
            old_gy = int(getattr(ts, "grid_y", 0) or 0)

            if old_gx == gx and old_gy == gy:
                continue

            # If we're suppressing callbacks (replay/load), do a direct state update (no history)
            # because replay/load already defines the truth.
            if bool(getattr(t, "_suppress_move_callback", False)):
                ts.grid_x = int(gx)
                ts.grid_y = int(gy)
                any_changed = True
                continue

            # Otherwise: commit via deterministic history event.
            ts.grid_x = int(gx)
            ts.grid_y = int(gy)

            try:
                self._history_append_and_apply({
                    "type": "SET_POSITION",
                    "token_id": str(token_id),
                    "from_gx": int(old_gx),
                    "from_gy": int(old_gy),
                    "to_gx": int(gx),
                    "to_gy": int(gy),
                    "movement_remaining": int(getattr(ts, "movement_remaining", 0) or 0),
                })
            except Exception as e:
                print(f"[HISTORY] Failed to append SET_POSITION from sync loop: {e}")

            any_changed = True

        if any_changed:
            try:
                self.refresh_player_view()
            except Exception:
                pass

    def token_grid_xy(self, t: DraggableToken):
        cx = t.x() + (t.pixmap().width() / 2)
        cy = t.y() + (t.pixmap().height() / 2)
        gx = int(cx // GRID_SIZE)
        gy = int(cy // GRID_SIZE)
        return gx, gy

    def _is_pc(self, ts) -> bool:
        return (getattr(ts, "side", "") == "player") or (getattr(ts, "kind", "") == "pc")

    def get_weapon_data(self, weapon_ref: str):
        """
        Resolve a weapon reference to its item dict.
        Supports:
        - exact item_id (UUID)
        - exact name (case-insensitive)
        - slug/alias (optional fields in items.json)
        - fallback: unarmed
        """
        weapon_ref = (weapon_ref or "").strip()
        items = self.items_db if hasattr(self, "items_db") else None  # if you cache items.json
        if items is None:
            # fallback: load items.json each time if you don't have caching
            path = os.path.join(self.campaign_path, "items.json")
            try:
                with open(path, "r", encoding="utf-8") as f:
                    items = json.load(f)
            except Exception:
                items = {}

        weapons = (items or {}).get("weapons", []) or []

        for w in weapons:
            if str(w.get("item_id", "")).strip() == weapon_ref:
                return self._normalize_weapon_record(w)

        # ---- 2) case-insensitive name match ----
        ref_l = weapon_ref.lower()
        for w in weapons:
            if str(w.get("name", "")).strip().lower() == ref_l:
                return w

        # ---- 3) optional alias/slug match ----
        for w in weapons:
            alias = str(w.get("alias", "") or w.get("slug", "") or "").strip().lower()
            if alias and alias == ref_l:
                return w

        # ---- 4) legacy: if ref is "pipe_rifle" and your items don't include alias,
        # you can try to normalize underscores/spaces ----
        norm = ref_l.replace("_", " ").strip()
        for w in weapons:
            nm = str(w.get("name", "")).strip().lower()
            if nm == norm:
                return w

        # ---- fallback to unarmed ----
        for w in weapons:
            if str(w.get("item_id", "")).strip() == "unarmed":
                return self._normalize_weapon_record(w)
        return None

    def apply_state_hp_to_scene_token(self, token_id: str):
        """Push HP/death state changes from EncounterState into the QGraphics token + force repaint."""
        ts = self.state.tokens.get(token_id)
        if not ts:
            return

        t = self._get_scene_token_item(token_id)
        if t is None:
            return

        # --- Sync HP ---
        try:
            t.hp = int(getattr(ts, "hp", 0) or 0)
            t.max_hp = int(getattr(ts, "max_hp", 10) or 10)
        except Exception:
            pass

        # Redraw bar
        try:
            if hasattr(t, "update_hp_bar"):
                t.update_hp_bar()
        except Exception:
            pass

        # Decide if "dead" should display
        show_dead = (int(getattr(ts, "hp", 0) or 0) <= 0)

        # PCs: don't show dead icon at 0; they're "down", not dead (death saves)
        if getattr(ts, "kind", "") == "pc":
            show_dead = False

        try:
            if show_dead:
                dead_rel = (getattr(ts, "dead_image_relpath", "") or "").strip()
                if dead_rel:
                    dead_abs = os.path.join(self.campaign_path, dead_rel)
                    if os.path.exists(dead_abs):
                        pm = QPixmap(dead_abs)
                        if not pm.isNull():
                            if getattr(t, "_is_dead_sprite", False) is not True:
                                t.setPixmap(pm.scaled(t.pixmap().size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                                t._is_dead_sprite = True
            else:
                if getattr(t, "_is_dead_sprite", False) is True:
                    alive_abs = os.path.join(self.campaign_path, getattr(ts, "image_relpath", ""))
                    pm = QPixmap(alive_abs)
                    if not pm.isNull():
                        t.setPixmap(pm.scaled(t.pixmap().size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
                        t._is_dead_sprite = False
        except Exception:
            pass

        # ---- FORCE PAINT ----
        try:
            t.update()
        except Exception:
            pass
        try:
            self.scene.update()
        except Exception:
            pass
        try:
            self.view.viewport().update()
        except Exception:
            pass
        
    def _apply_token_stats_to_scene_token(self, token_id: str) -> None:
        """
        Compatibility helper used by refactored combat flow.
        Push TokenState-derived stats (HP/max HP, dead sprite, etc.) onto the scene token.
        """
        self.apply_state_hp_to_scene_token(token_id)
    
    def _finalize_npc_attack_preview(self, attacker, target, outcome, *, encounter_id: str = ""):
        if attacker is None or target is None or outcome is None:
            return None

        hp_before = int(getattr(target, "hp", 0) or 0)
        max_before = int(getattr(target, "max_hp", 10) or 10)
        death_before = str(getattr(target, "death_state", "alive") or "alive")

        if bool(getattr(outcome, "is_hit", False)) and int(getattr(outcome, "damage_total", 0) or 0) > 0:
            weapon_ref = (getattr(attacker, "weapon_id", "") or "").strip() or (getattr(attacker, "weapon", "") or "").strip()
            weapon_data = self.get_weapon_data(weapon_ref) if weapon_ref else {}
            final_damage = int(getattr(outcome, "damage_total", 0) or 0)
            try:
                wd = weapon_data if isinstance(weapon_data, dict) else {}
                bucket = self._weapon_bucket(wd)
                is_melee = ("melee" in bucket) or (str(wd.get("weapon_type") or "").strip().lower() == "melee")
                final_damage, _sa_break = self._maybe_apply_savage_attacker(attacker, str(getattr(outcome, "damage_roll_expr", "") or ""), int(final_damage), "", crit=bool(int(getattr(outcome, "d20", 0) or 0) == 20), is_melee=bool(is_melee), source_kind="attack")
            except Exception:
                pass
            self.apply_damage_to_token(
                target,
                int(final_damage),
                encounter_id=str(encounter_id or getattr(self.state, "encounter_id", "") or ""),
                pending_attack_id=f"npc:{str(getattr(attacker, 'token_id', '') or '')}:{str(getattr(target, 'token_id', '') or '')}:{uuid.uuid4().hex[:8]}",
                damage_type=str((weapon_data or {}).get("damage_type", "") if isinstance(weapon_data, dict) else ""),
                source_kind="attack",
                source_meta=(dict(weapon_data) if isinstance(weapon_data, dict) else {"weapon_ref": str(weapon_ref or "")}) | {"attacker_token_id": str(getattr(attacker, "token_id", "") or ""), "attacker_name": str(getattr(attacker, "display_name", "") or "")},
            )

        hp_after = int(getattr(target, "hp", 0) or 0)
        max_after = int(getattr(target, "max_hp", 10) or 10)
        try:
            self._apply_death_rules_after_damage(target)
        except Exception:
            pass
        death_after = str(getattr(target, "death_state", "alive") or "alive")

        try:
            self._history_append_and_apply({
                "type": "NPC_ATTACK_RESOLVED",
                "attacker_token_id": str(getattr(attacker, "token_id", "") or ""),
                "target_token_id": str(getattr(target, "token_id", "") or ""),
                "d20": int(getattr(outcome, "d20", 0) or 0),
                "total_to_hit": int(getattr(outcome, "total_to_hit", 0) or 0),
                "target_ac": int(getattr(outcome, "target_ac", 10) or 10),
                "is_hit": bool(getattr(outcome, "is_hit", False)),
                "damage_roll_expr": str(getattr(outcome, "damage_roll_expr", "") or ""),
                "damage_total": int(locals().get("final_damage", getattr(outcome, "damage_total", 0)) or 0),
                "hp_before": hp_before,
                "max_hp_before": max_before,
                "death_state_before": death_before,
                "hp_after": hp_after,
                "max_hp_after": max_after,
                "death_state_after": death_after,
                "encounter_id": str(encounter_id or getattr(self.state, "encounter_id", "") or ""),
            })
        except Exception as e:
            print(f"[HISTORY] Failed to append NPC_ATTACK_RESOLVED: {e}")

        try:
            self.apply_state_hp_to_scene_token(str(getattr(target, "token_id", "") or ""))
        except Exception:
            pass
        try:
            self.refresh_player_view()
        except Exception:
            pass
        return outcome

    def resolve_npc_attack(
        self,
        attacker_id: str,
        target_id: str,
        *,
        encounter_id: str = "",
    ):
        """
        UI-layer wrapper.
        CombatEngine owns combat math preview; MainWindow owns interruption windows,
        deterministic history, and final application.
        """
        attacker = self.state.tokens.get(attacker_id)
        target = self.state.tokens.get(target_id)
        if not attacker or not target:
            return None

        outcome = self.combat_engine.resolve_npc_attack(
            attacker_id,
            target_id,
            encounter_id=str(encounter_id or getattr(self.state, "encounter_id", "") or ""),
            meta=getattr(self, "current_map_meta", {}) or {},
            apply_damage=False,
        )
        if outcome is None:
            return None

        try:
            if bool(getattr(outcome, "is_hit", False)) and not getattr(self, "_reaction_window", None) and self._token_can_take_reaction(target) and self._sheet_has_available_spell(str(getattr(target, "character_id", "") or ""), "shield", min_slot_level=1):
                would_miss = int(getattr(outcome, "total_to_hit", 0) or 0) < (int(getattr(outcome, "target_ac", 10) or 10) + 5)
                if would_miss:
                    context = {
                        "attacker_token_id": str(attacker_id or ""),
                        "target_token_id": str(target_id or ""),
                        "encounter_id": str(encounter_id or getattr(self.state, "encounter_id", "") or ""),
                        "npc_attack_preview": {
                            "d20": int(getattr(outcome, "d20", 0) or 0),
                            "total_to_hit": int(getattr(outcome, "total_to_hit", 0) or 0),
                            "target_ac": int(getattr(outcome, "target_ac", 10) or 10),
                            "is_hit": bool(getattr(outcome, "is_hit", False)),
                            "damage_roll_expr": str(getattr(outcome, "damage_roll_expr", "") or ""),
                            "damage_total": int(getattr(outcome, "damage_total", 0) or 0),
                        },
                        "slot_level": 1,
                    }
                    if self._is_player_controlled_token(target):
                        if self._offer_reaction_choice(target, reaction_kind="npc_shield_attack", spell_id="shield", text=f"Reaction available: cast Shield against {attacker.display_name}?", context=context):
                            return outcome
                    else:
                        self.server.use_reaction_spell(str(getattr(target, "character_id", "") or ""), spell_id="shield", slot_level=1, note="shield")
                        self._apply_shield_reaction_status(target)
                        outcome = type(outcome)(
                            d20=int(getattr(outcome, "d20", 0) or 0),
                            total_to_hit=int(getattr(outcome, "total_to_hit", 0) or 0),
                            target_ac=int(getattr(outcome, "target_ac", 10) or 10) + 5,
                            is_hit=False,
                            damage_roll_expr="",
                            damage_total=0,
                        )
            
        except Exception as e:
            print("[REACTION] npc shield offer failed:", e)

        return self._finalize_npc_attack_preview(attacker, target, outcome, encounter_id=str(encounter_id or getattr(self.state, "encounter_id", "") or ""))

    def _auto_resolve_if_ready(self):
        if not self._armed_attacker_id or not self._armed_target_id:
            return

        attacker = self.state.tokens.get(self._armed_attacker_id)
        target = self.state.tokens.get(self._armed_target_id)
        if not attacker or not target:
            return

        # Initiative gating only (NO per-turn action limits)
        if not self._enforce_active_turn(attacker.token_id, action_name="Auto Resolve"):
            return

        if getattr(attacker, "side", "") == "player":
            # PC path: arm pending attack (no auto-roll)
            self._arm_pending_pc_attack(attacker, target)
            return

        # NPC path: auto resolve immediately
        try:
            self.resolve_npc_attack(
                attacker.token_id,
                target.token_id,
                encounter_id=str(getattr(self.state, "encounter_id", "") or ""),
            )
        except Exception:
            return

        # Sync visuals immediately
        try:
            self._sync_scene_token_from_state(target.token_id)
        except Exception:
            pass

    def _arm_pending_pc_attack(self, attacker_ts: "TokenState", target_ts: "TokenState"):
        # Turn gating only (optional but recommended when initiative is running)
        if not self._enforce_active_turn(attacker_ts.token_id, action_name="Arm PC Attack"):
            return

        if self.state.pending_attack:
            existing = self.state.pending_attack.get("pending_attack_id", "")
            self._set_hud_status(
                f"Pending attack already active ({existing}). Cancel or resolve first.",
                hold_sec=3.5
            )
            self.update_combat_hud()
            return

        # Keep sheet-backed stats fresh (attack mod / weapon changes)
        try:
            self.hydrate_tokenstate_from_sheet(attacker_ts, include_hp=False)
        except Exception:
            pass

        player_id = (getattr(attacker_ts, "player_id", "") or "").strip() or attacker_ts.display_name
        pending_attack_id = uuid.uuid4().hex

        # Prefer id-backed weapon_id if it resolves; else legacy weapon; else unarmed
        candidate_id = (getattr(attacker_ts, "weapon_id", "") or "").strip()
        candidate_legacy = (getattr(attacker_ts, "weapon", "") or "").strip()

        weapon_ref = "unarmed"
        weapon_data = None

        if candidate_id:
            wd = self.get_weapon_data(candidate_id)
            if wd:
                weapon_ref = candidate_id
                weapon_data = wd

        if weapon_data is None and candidate_legacy:
            wd = self.get_weapon_data(candidate_legacy)
            if wd:
                weapon_ref = candidate_legacy
                weapon_data = wd

        if weapon_data is None:
            weapon_ref = "unarmed"
            weapon_data = self.get_weapon_data("unarmed") or {}

        weapon_name = weapon_data.get("name", weapon_ref)
        damage_expr = weapon_data.get("damage", "") or ""

        try:
            self.hydrate_tokenstate_from_sheet(attacker_ts, include_hp=False)
            self.hydrate_tokenstate_from_sheet(target_ts, include_hp=False)
        except Exception:
            pass
        cond_attack_preview = attack_mode_from_conditions(attacker_ts, target_ts, weapon_data, "weapon")
        preview_mode = {"advantage": "advantage", "disadvantage": "disadvantage"}.get(str(cond_attack_preview.get("mode", "normal") or "normal"), "normal")

        self.state.pending_attack = {
            "pending_attack_id": pending_attack_id,
            "encounter_id": getattr(self.state, "encounter_id", ""),
            "player_id": player_id,

            "attacker_token_id": attacker_ts.token_id,
            "attacker_character_id": getattr(attacker_ts, "character_id", "") or "",
            "target_token_id": target_ts.token_id,
            "target_character_id": getattr(target_ts, "character_id", "") or "",

            "attacker_name": attacker_ts.display_name,
            "target_name": target_ts.display_name,

            "damage_expr": damage_expr,
            "damage_type": str((weapon_data or {}).get("damage_type", "") if isinstance(weapon_data, dict) else ""),
            "source_meta": ((dict(weapon_data) if isinstance(weapon_data, dict) else {}) | {"feat_damage_bonus": int((locals().get("feat_damage_bonus", 0) or 0)), "feat_attack_penalty": int((locals().get("feat_attack_penalty", 0) or 0))}),
            "weapon_ref": weapon_ref,
            "weapon_name": weapon_name,

            "roll_mode": preview_mode,
            "created_monotonic": time.monotonic(),
            "expires_in_sec": 90,
        }

        ok = self.server.register_pending_attack(self.state.pending_attack)
        if not ok:
            print("[ARM] Could not register pending attack on server; clearing pending.")
            self.state.pending_attack = None
            return

        try:
            preview_note = ""
            if preview_mode == "advantage":
                preview_note = " Advantage applies."
            elif preview_mode == "disadvantage":
                preview_note = " Disadvantage applies."
            self.server.post_message(
                player_id,
                f"Attack armed: {attacker_ts.display_name} → {target_ts.display_name} ({weapon_name}). Roll now.{preview_note}",
                "ok",
                ttl_seconds=30,
                data={"pending_attack_id": pending_attack_id, "weapon": weapon_name},
            )
        except Exception:
            pass

        print(f"[ARM] PC pending attack armed: {attacker_ts.display_name} -> {target_ts.display_name} using {weapon_ref} pending_attack_id={pending_attack_id}")
        self.update_combat_hud()

    def _arm_pending_pc_spell_attack(self, caster_ts: TokenState, target_ts: TokenState, spell_id: str) -> None:
        """
        Phase 5.2: spell attacks (attack-roll spells) use the same PC roll pipeline as weapons,
        but resolve damage_expr / attack_bonus from spells.json instead of items.json weapons.

        This keeps the website unchanged: it still rolls to-hit, then rolls damage when asked.
        """
        if self.state.pending_attack:
            existing = self.state.pending_attack.get("pending_attack_id", "")
            self._set_hud_status(
                f"Pending attack already active ({existing}). Cancel or resolve first.",
                hold_sec=3.5
            )
            self.update_combat_hud()
            return

        spell_id = (spell_id or "").strip()
        spell_data = (getattr(self, "spells_db", {}) or {}).get(spell_id) or {}
        if not spell_data:
            self._set_hud_status(f"Spell not found: {spell_id}", hold_sec=4.0)
            return

        # Optional safety: only allow attack-roll spells through this path.
        targeting = str(spell_data.get("target_mode") or ((spell_data.get("targeting") or {}).get("kind")) or spell_data.get("targeting", "") or "").strip().lower()
        if targeting and targeting not in {"attack_roll", "attack"}:
            self._set_hud_status(f"Spell is not an attack-roll spell: {spell_data.get('name', spell_id)}", hold_sec=4.0)
            return

        player_id = (getattr(caster_ts, "player_id", "") or "").strip() or caster_ts.display_name
        pending_attack_id = uuid.uuid4().hex

        # Keep sheet data fresh (caster mods can change)
        try:
            self.hydrate_tokenstate_from_sheet(caster_ts)
        except Exception:
            pass

        spell_name = str(spell_data.get("name", spell_id))
        try:
            self.hydrate_tokenstate_from_sheet(target_ts, include_hp=False)
        except Exception:
            pass
        cond_attack_preview = attack_mode_from_conditions(caster_ts, target_ts, spell_data, "spell")
        preview_spell_mode = {"advantage": "advantage", "disadvantage": "disadvantage"}.get(str(cond_attack_preview.get("mode", "normal") or "normal"), "normal")
        # damage can be either:
        #   damage: {"expr":"2d6", "type":"fire"}
        # or legacy:
        #   damage_expr: "2d6"
        dmg_expr = ""
        dmg_block = spell_data.get("damage", None)
        if isinstance(dmg_block, dict):
            dmg_expr = str(dmg_block.get("expr", "") or "")
        if not dmg_expr:
            dmg_expr = str(spell_data.get("damage_expr", "") or "")
        dmg_expr = dmg_expr or "1"

        self.state.pending_attack = {
            "pending_attack_id": pending_attack_id,
            "encounter_id": getattr(self.state, "encounter_id", ""),
            "player_id": player_id,

            "attacker_token_id": caster_ts.token_id,
            "attacker_character_id": getattr(caster_ts, "character_id", "") or "",
            "target_token_id": target_ts.token_id,
            "target_character_id": getattr(target_ts, "character_id", "") or "",
            "target_name": getattr(target_ts, "display_name", target_ts.token_id),
            "attacker_name": getattr(caster_ts, "display_name", caster_ts.token_id),

            # Spell metadata
            "attack_kind": "spell",
            "spell_id": spell_id,
            "spell_name": spell_name,

            # Keep the existing keys for compatibility with logs/UI
            "damage_expr": dmg_expr,
            "damage_type": str((dmg_block.get("type", "") if isinstance(dmg_block, dict) else spell_data.get("damage_type", "")) or ""),
            "source_meta": {"source_kind": "spell", "is_spell": True, "magical": True, "tags": ["spell", "magical"]},
            "weapon_ref": spell_id,      # used only as a label; resolver checks attack_kind
            "weapon_name": spell_name,   # website UI expects a label

            "roll_mode": preview_spell_mode,
            "created_monotonic": time.monotonic(),
            "expires_in_sec": 90,
        }

        ok = self.server.register_pending_attack(self.state.pending_attack)
        if not ok:
            print("[ARM] Could not register pending spell attack on server; clearing pending.")
            self.state.pending_attack = None
            return

        self.server.post_message(
            player_id,
            f"Spell armed: {caster_ts.display_name} → {target_ts.display_name} ({spell_name}). Roll now.",
            "ok",
            ttl_seconds=30,
            data={"pending_attack_id": pending_attack_id, "weapon": spell_name, "spell_id": spell_id}
        )

        print(f"[ARM] PC pending spell armed: {caster_ts.display_name} -> {target_ts.display_name} spell={spell_id} pending_attack_id={pending_attack_id}")
        self.update_combat_hud()

    def handle_roll_payload(self, payload: dict):
        """
        Option B: Handle the *to-hit* roll ONLY.

        Flow:
        - DM arms pending attack (self.state.pending_attack)
        - Player website sends d20 roll for that pending_attack_id
        - DM resolves hit/miss
        - On HIT: set self._awaiting_damage + notify website NEED_DAMAGE + clear pending
        - On MISS: clear pending + send result to website
        """

        pending = getattr(self.state, "pending_attack", None)
        if not pending:
            print("[ROLL] Roll received but no pending PC attack armed.")
            return

        # Hard guardrail: don't let a second to-hit overwrite awaiting damage
        if getattr(self, "_awaiting_damage", None):
            self._set_hud_status("Already awaiting damage; resolve/cancel first.", hold_sec=4.0)
            self.campaign_logger.combat(
                "awaiting_damage_blocked_new_hit",
                pending_attack_id=pending.get("pending_attack_id", "")
            )
            return

        # HARD GUARDRAIL: ignore late rolls (local TTL)
        created = pending.get("created_monotonic")
        ttl = pending.get("expires_in_sec", 90)
        if created and (time.monotonic() - float(created)) > float(ttl):
            player_id = (pending.get("player_id", "") or "").strip()
            pid = pending.get("pending_attack_id", "")
            if player_id:
                self.server.post_message(player_id,
                    f"Roll ignored: pending attack expired (id={pid}). Re-arm and roll again.",
                    "warn",
                    ttl_seconds=25,
                    data={"pending_attack_id": pid}
                )
            print("[ROLL] Pending attack expired before roll; ignored.")
            self.state.pending_attack = None
            self.campaign_logger.combat("pending_expired", pending_attack_id=pid)
            self._set_hud_status("Late roll ignored (pending expired).", hold_sec=4.0)
            self.update_combat_hud()
            return

        # Validate pending_attack_id
        payload_pending_id = payload.get("pending_attack_id")
        if not payload_pending_id:
            print("[ROLL] Missing pending_attack_id in payload. Ignored.")
            return

        if payload_pending_id != pending.get("pending_attack_id"):
            print(
                f"[ROLL] pending_attack_id mismatch: got {payload_pending_id}, "
                f"expected {pending.get('pending_attack_id')}. Ignored."
            )
            player_id = (pending.get("player_id", "") or "").strip()
            if player_id:
                self.server.post_message(player_id,
                    "Roll ignored: pending_attack_id mismatch (stale roll). Re-arm and roll again.",
                    "warn",
                    ttl_seconds=25,
                    data={"got": payload_pending_id, "expected": pending.get("pending_attack_id")}
                )
            return

        # Validate player_id (optional but good)
        payload_player_id = payload.get("player_id")
        pending_player_id = pending.get("player_id")
        if payload_player_id and pending_player_id and payload_player_id != pending_player_id:
            print(f"[ROLL] player_id mismatch: got {payload_player_id}, expected {pending_player_id}. Ignored.")
            return

        # Validate attacker
        attacker_id = payload.get("attacker_token_id") or payload.get("token_id")
        if attacker_id != pending.get("attacker_token_id"):
            print(
                f"[ROLL] Roll for attacker {attacker_id} does not match pending attacker "
                f"{pending.get('attacker_token_id')}. Ignored."
            )
            return

        # Validate attacker_character_id if present
        payload_attacker_char = payload.get("attacker_character_id", "")
        pending_attacker_char = pending.get("attacker_character_id", "")
        if payload_attacker_char and pending_attacker_char and payload_attacker_char != pending_attacker_char:
            print(
                f"[ROLL] attacker_character_id mismatch: got {payload_attacker_char}, "
                f"expected {pending_attacker_char}. Ignored."
            )
            return

        # Parse rolls + mode
        mode_raw = (payload.get("mode") or pending.get("roll_mode", "normal") or "normal").strip().lower()
        mode = {"advantage": "adv", "disadvantage": "dis"}.get(mode_raw, mode_raw)

        if "rolls" in payload and isinstance(payload["rolls"], list):
            rolls_list = payload["rolls"]
        else:
            rolls_list = [payload.get("roll", 0)]

        attacker = self.state.tokens.get(pending.get("attacker_token_id", ""))
        target = self.state.tokens.get(pending.get("target_token_id", ""))
        if not attacker or not target:
            print("[ROLL] Attacker/target missing in state; clearing pending.")
            pid = pending.get("pending_attack_id", "")
            self.state.pending_attack = None
            self.campaign_logger.combat("resolve_aborted_missing_tokens", pending_attack_id=pid)
            self.update_combat_hud()
            return

        # Preview weapon/spell data so condition semantics can affect attack mode.
        attack_kind_preview = str(pending.get("attack_kind", "") or "").strip().lower() or "weapon"
        weapon_ref_preview = (pending.get("weapon_ref") or "").strip() or "unarmed"
        weapon_data_preview = None
        if attack_kind_preview == "spell":
            spell_id_preview = (pending.get("spell_id") or weapon_ref_preview or "").strip()
            weapon_data_preview = (getattr(self, "spells_db", {}) or {}).get(spell_id_preview) or {}
        else:
            sheet_weapon_id_preview = (getattr(attacker, "weapon_id", "") or "").strip()
            if sheet_weapon_id_preview and self.get_weapon_data(sheet_weapon_id_preview):
                weapon_ref_preview = sheet_weapon_id_preview
            weapon_data_preview = self.get_weapon_data(weapon_ref_preview) or {}

        condition_attack = attack_mode_from_conditions(attacker, target, weapon_data_preview, attack_kind_preview)
        merged_mode = merge_roll_modes(mode, condition_attack.get("mode", "normal"))
        mode = {"advantage": "adv", "disadvantage": "dis", "normal": "normal", "adv": "adv", "dis": "dis"}.get(merged_mode, "normal")

        # Choose d20
        d20 = choose_d20(rolls_list, mode)
        try:
            d20_int = int(d20)
        except Exception:
            d20_int = 0

        # Log receipt
        self.campaign_logger.combat(
            "roll_received",
            pending_attack_id=pending.get("pending_attack_id", ""),
            encounter_id=pending.get("encounter_id", ""),
            player_id=pending.get("player_id", ""),
            attacker_token_id=pending.get("attacker_token_id", ""),
            target_token_id=pending.get("target_token_id", ""),
            mode=mode,
            rolls=rolls_list,
            d20=d20_int,
        )

        # Hydrate ONCE (mutates token states)
        self.hydrate_tokenstate_from_sheet(attacker)
        self.hydrate_tokenstate_from_sheet(target)

        # Keep UI consistent
        self.apply_state_hp_to_scene_token(attacker.token_id)
        self.apply_state_hp_to_scene_token(target.token_id)

        attacker_mod = int(getattr(attacker, "attack_modifier", 0))
        target_ac = int(getattr(target, "ac", 10))
        # ---- Targeting LOS policy (Phase B5) ----
        # Gated by weapon/spell data field `requires_los` (defaults True for weapons).
        try:
            pending_kind = str(pending.get("attack_kind", "") or "").strip().lower() or "weapon"
            pending_weapon_ref = (pending.get("weapon_ref") or "").strip() or "unarmed"
            pending_weapon_data = None

            if pending_kind == "spell":
                spell_id = (pending.get("spell_id") or pending_weapon_ref or "").strip()
                pending_weapon_data = (getattr(self, "spells_db", {}) or {}).get(spell_id) or {}
            else:
                pending_weapon_data = self.get_weapon_data(pending_weapon_ref) or {}

            requires_los = True
            if isinstance(pending_weapon_data, dict) and ("requires_los" in pending_weapon_data):
                requires_los = bool(pending_weapon_data.get("requires_los"))

            if requires_los:
                meta = getattr(self, "current_map_meta", {}) or {}
                segs = build_segments_from_meta(meta or {}, include_blocked=True, door_state=getattr(self.state, "door_state", {}) or {})
                if not has_los(
                    attacker_grid_x=int(attacker.grid_x),
                    attacker_grid_y=int(attacker.grid_y),
                    target_grid_x=int(target.grid_x),
                    target_grid_y=int(target.grid_y),
                    segments=segs,
                ):
                    pid = pending.get("pending_attack_id", "")
                    self.state.pending_attack = None
                    self.campaign_logger.combat(
                        "no_los_block",
                        pending_attack_id=pid,
                        attacker_token_id=attacker.token_id,
                        target_token_id=target.token_id,
                        attack_kind=pending_kind,
                        weapon_ref=pending_weapon_ref,
                    )
                    try:
                        player_id = (pending.get("player_id", "") or "").strip()
                        if player_id:
                            self.server.post_message(
                                player_id,
                                f"Attack blocked: no line of sight to {target.display_name}.",
                                "warn",
                                ttl_seconds=25,
                                data={"pending_attack_id": pid, "reason": "no_los"},
                            )
                    except Exception:
                        pass
                    self._set_hud_status("Attack blocked: NO LOS.", hold_sec=4.0)
                    self.update_combat_hud()
                    return

            # ---- Perception policy (B-X4: Vision Types) ----
            # Default: attacks require sight unless data sets requires_sight=false.
            requires_sight = True
            if isinstance(pending_weapon_data, dict) and ("requires_sight" in pending_weapon_data):
                requires_sight = bool(pending_weapon_data.get("requires_sight"))

            try:
                from engine.perception_engine import can_perceive_target

                meta = getattr(self, "current_map_meta", {}) or {}
                pres = can_perceive_target(
                    attacker_ts=attacker,
                    target_ts=target,
                    meta=meta or {},
                    feet_per_square=int(getattr(self.state, "grid_ft", 5) or 5),
                    requires_sight=bool(requires_sight),
                )

                if not bool(getattr(pres, "can_perceive", False)):
                    try:
                        atk_bs = int(getattr(attacker, "blindsense_ft", 0) or 0)
                        dx = abs(int(getattr(attacker, "grid_x", 0) or 0) - int(getattr(target, "grid_x", 0) or 0))
                        dy = abs(int(getattr(attacker, "grid_y", 0) or 0) - int(getattr(target, "grid_y", 0) or 0))
                        dist_ft = max(dx, dy) * int(getattr(self.state, "grid_ft", 5) or 5)
                        if atk_bs > 0 and dist_ft <= atk_bs:
                            pres = type("_BlindsensePerception", (), {"can_perceive": True})()
                    except Exception:
                        pass
                if not bool(getattr(pres, "can_perceive", False)):
                    pid = pending.get("pending_attack_id", "")
                    self.state.pending_attack = None
                    self.campaign_logger.combat(
                        "no_perception_block",
                        pending_attack_id=pid,
                        attacker_token_id=attacker.token_id,
                        target_token_id=target.token_id,
                        attack_kind=pending_kind,
                        weapon_ref=pending_weapon_ref,
                        light_level=str(getattr(pres, "light_level", "")),
                        method=str(getattr(pres, "method", "")),
                        reason=str(getattr(pres, "reason", "")),
                    )
                    try:
                        player_id = (pending.get("player_id", "") or "").strip()
                        if player_id:
                            self.server.post_message(
                                player_id,
                                f"Attack blocked: you can't perceive {target.display_name} ({getattr(pres, 'reason', 'unseen')}).",
                                "warn",
                                ttl_seconds=25,
                                data={"pending_attack_id": pid, "reason": "no_perception"},
                            )
                    except Exception:
                        pass
                    self._set_hud_status("Attack blocked: CAN'T PERCEIVE TARGET.", hold_sec=4.0)
                    self.update_combat_hud()
                    return
            except Exception:
                pass
        except Exception:
            pass

        # Phase B3: Cover (derived from geometry)
        cover_tier = "none"
        cover_bonus = 0
        try:
            meta = getattr(self, "current_map_meta", {}) or {}
            cover_tier, cover_bonus, _cover_dbg = compute_cover(
                attacker_grid_x=int(attacker.grid_x),
                attacker_grid_y=int(attacker.grid_y),
                target_grid_x=int(target.grid_x),
                target_grid_y=int(target.grid_y),
                meta=meta,
            )
        except Exception:
            cover_tier = "none"
            cover_bonus = 0

        # Apply manual cover override (DM-side). Use the more-protective tier.
        try:
            override = str(getattr(target, "cover_override", "none") or "none").strip().lower()
            if override in ("none", "half", "three_quarters", "total"):
                cover_tier = merge_cover_tiers(cover_tier, override)
                cover_bonus = cover_bonus_for_tier(cover_tier)
        except Exception:
            pass

        # Total cover: cannot be targeted (treat as auto-miss and clear pending)
        if cover_tier == "total":
            pid = pending.get("pending_attack_id", "")
            self.state.pending_attack = None
            self.campaign_logger.combat(
                "cover_total_block",
                pending_attack_id=pid,
                attacker_token_id=attacker.token_id,
                target_token_id=target.token_id,
            )
            try:
                player_id = (pending.get("player_id", "") or "").strip()
                if player_id:
                    self.server.post_message(
                        player_id,
                        f"Attack blocked by total cover: {target.display_name}.",
                        "warn",
                        ttl_seconds=25,
                        data={"pending_attack_id": pid, "cover": "total"},
                    )
            except Exception:
                pass
            self._set_hud_status("Attack blocked by total cover.", hold_sec=4.0)
            self.update_combat_hud()
            return

        effective_target_ac = int(target_ac) + int(cover_bonus)

        
        # Weapon / Spell resolution:
        # - Weapons: start from armed weapon_ref; allow sheet weapon_id override ONLY if real
        # - Spells: resolve from spells_db when pending['attack_kind'] == 'spell'
        attack_kind = str(pending.get("attack_kind", "") or "").strip().lower() or "weapon"

        weapon_ref = (pending.get("weapon_ref") or "").strip() or "unarmed"
        weapon_data = None

        if attack_kind == "spell":
            spell_id = (pending.get("spell_id") or weapon_ref or "").strip()
            spell_data = (getattr(self, "spells_db", {}) or {}).get(spell_id) or {}
            weapon_ref = spell_id or weapon_ref
            weapon_data = spell_data  # reuse variable name for downstream logic
        else:
            sheet_weapon_id = (getattr(attacker, "weapon_id", "") or "").strip()
            if sheet_weapon_id and self.get_weapon_data(sheet_weapon_id):
                weapon_ref = sheet_weapon_id

            weapon_ref = weapon_ref or "unarmed"
            weapon_data = self.get_weapon_data(weapon_ref)

            if not weapon_data:
                    print(f"[ROLL] Weapon '{weapon_ref}' not found; clearing pending.")
                    pid = pending.get("pending_attack_id", "")
                    self.state.pending_attack = None
                    self.campaign_logger.combat("weapon_not_found", pending_attack_id=pid, weapon_ref=weapon_ref)
                    self._set_hud_status(f"Weapon not found: {weapon_ref}", hold_sec=4.0)
                    self.update_combat_hud()
                    return

            weapon_attack_bonus = int(weapon_data.get("attack_bonus", 0))
            feat_attack_penalty = 0
            feat_damage_bonus = 0
            gwm_pen, gwm_dmg = self._great_weapon_master_mods(attacker, weapon_data, attack_kind)
            ss_pen, ss_dmg = self._sharpshooter_mods(attacker, weapon_data, attack_kind)
            feat_attack_penalty += int(gwm_pen) + int(ss_pen)
            feat_damage_bonus += int(gwm_dmg) + int(ss_dmg)

            hit, total_attack, is_nat20, is_nat1 = resolve_attack(
                    d20=int(d20_int),
                    attacker_mod=int(attacker_mod) + int(feat_attack_penalty),
                    target_ac=int(effective_target_ac),
                    weapon_attack_bonus=int(weapon_attack_bonus),
                )

                # Always print a readable line
            print(
                    f"[PC] {attacker.display_name} -> {target.display_name}: "
                    f"d20={d20_int} mode={mode} total={total_attack} vs AC={effective_target_ac} (base {target_ac} + cover {cover_bonus}) => {'HIT' if hit else 'MISS'}"
                )
            if condition_attack.get("reasons"):
                    print(f"[PC][COND] attack mode={mode} reasons={'; '.join(condition_attack.get('reasons', []))}")
            if is_nat20:
                    print("[PC] NAT 20! Critical hit.")
            elif is_nat1:
                    print("[PC] NAT 1! Automatic miss.")

            if hit:
                try:
                    if not getattr(self, "_reaction_window", None) and self._token_can_take_reaction(target) and self._sheet_has_available_spell(str(getattr(target, "character_id", "") or ""), "shield", min_slot_level=1):
                        would_miss = int(total_attack) < int(effective_target_ac) + 5
                        if would_miss:
                            pending_copy = dict(pending or {})
                            self.state.pending_attack = None
                            if self._is_player_controlled_token(target):
                                offered = self._offer_reaction_choice(target, reaction_kind="shield_attack", spell_id="shield", text=f"Reaction available: cast Shield against {attacker.display_name}?", context={"pending": pending_copy, "roll_payload": dict(payload or {}), "slot_level": 1})
                                if offered:
                                    return
                            else:
                                self.server.use_reaction_spell(str(getattr(target, "character_id", "") or ""), spell_id="shield", slot_level=1, note="shield")
                                self._apply_shield_reaction_status(target)
                                self.state.pending_attack = pending_copy
                                self.handle_roll_payload(dict(payload or {}))
                                return
                except Exception as e:
                    print("[REACTION] shield offer failed:", e)

                # HIT => store awaiting damage + notify website + clear pending
            if hit:
            # Damage expression source differs for weapons vs spells
                if attack_kind == "spell":
                    dmg_expr = ""
                    try:
                        dmg_block = (weapon_data or {}).get("damage", None)
                        if isinstance(dmg_block, dict):
                            dmg_expr = str(dmg_block.get("expr", "") or "")
                    except Exception:
                        dmg_expr = ""
                    if not dmg_expr:
                        dmg_expr = str((pending.get("damage_expr") or (weapon_data or {}).get("damage_expr") or "1"))
                else:
                    dmg_expr = weapon_data.get("damage", "1")
                    is_crit = bool(is_nat20)

                    # Store enough context to emit a final attack result after damage is applied.
                    self._awaiting_damage = {
                        "attack_id": pending.get("pending_attack_id", ""),
                        "player_id": pending.get("player_id", ""),
                        "encounter_id": pending.get("encounter_id", ""),

                        "attacker_token_id": attacker.token_id,
                        "attacker_name": attacker.display_name,
                        "target_token_id": target.token_id,
                        "target_name": target.display_name,

                        "weapon_ref": weapon_ref,
                        "weapon_name": (weapon_data.get("name", weapon_ref) if isinstance(weapon_data, dict) else weapon_ref),
                        "damage_expr": str(dmg_expr),
                        "damage_type": str((weapon_data or {}).get("damage_type", "") if isinstance(weapon_data, dict) else ""),
                        "crit": bool(is_crit),

                        # To-hit context
                        "roll": int(d20_int),
                        "total": int(total_attack),
                        "ac": int(effective_target_ac),
                        "nat20": bool(is_nat20),
                        "nat1": bool(is_nat1),
                        "cover": str(cover_tier),
                        "cover_bonus": int(cover_bonus),
                        "roll_mode": str(pending.get("roll_mode", "normal") or "normal"),

                        "created_monotonic": time.monotonic(),
                        "expires_in_sec": 90,
                    }

                    self.campaign_logger.combat(
                                "awaiting_damage_set",
                                attack_id=self._awaiting_damage.get("attack_id", ""),
                                player_id=self._awaiting_damage.get("player_id", ""),
                                encounter_id=self._awaiting_damage.get("encounter_id", ""),
                                target_token_id=self._awaiting_damage.get("target_token_id", ""),
                                damage_expr=self._awaiting_damage.get("damage_expr", ""),
                                crit=bool(self._awaiting_damage.get("crit", False)),
                                ttl=float(self._awaiting_damage.get("expires_in_sec", 0)),
                            )

                    player_id = (pending.get("player_id", "") or "").strip()
                    if player_id:
                                self.server.post_message(player_id,
                                    f"Hit confirmed. Roll damage: {dmg_expr}" + (" (CRIT)" if is_crit else ""),
                                    "warn",
                                    ttl_seconds=60,
                                    data={
                                        "type": "NEED_DAMAGE",
                                        "attack_id": pending.get("pending_attack_id", ""),
                                        "damage_expr": str(dmg_expr),
                                        "crit": bool(is_crit),
                                        "weapon_name": (pending.get("weapon_name") if attack_kind == "spell" else weapon_data.get("name", weapon_ref)),
                                        "spell_id": (pending.get("spell_id") if attack_kind == "spell" else ""),
                                        "spell_id": (pending.get("spell_id") if attack_kind == "spell" else ""),
                                        "target_name": target.display_name,
                                        "dedupe_key": f"NEED_DAMAGE:{pending.get('pending_attack_id','')}",
                                    }
                                )

                            # Clear pending immediately
                    self.state.pending_attack = None

                            # Log HIT (to-hit) result
                    self.campaign_logger.combat(
                                "hit_to_hit_confirmed",
                                pending_attack_id=self._awaiting_damage.get("attack_id", ""),
                                encounter_id=self._awaiting_damage.get("encounter_id", ""),
                                attacker_token_id=attacker.token_id,
                                target_token_id=target.token_id,
                                weapon_ref=weapon_ref,
                                total_attack=int(total_attack),
                                target_ac=int(effective_target_ac),
                                nat20=bool(is_nat20),
                                nat1=bool(is_nat1),
                                damage_expr=str(dmg_expr),
                                crit=bool(is_crit),
                            )

                    self._set_hud_status("Hit confirmed; waiting for damage roll.", hold_sec=3.0)
                    self.update_combat_hud()
                    self.refresh_player_view()
                    return

        # MISS => clear pending + send result to website
        self.campaign_logger.combat(
            "miss",
            pending_attack_id=pending.get("pending_attack_id", ""),
            encounter_id=pending.get("encounter_id", ""),
            attacker_token_id=attacker.token_id,
            target_token_id=target.token_id,
            weapon_ref=weapon_ref,
            total_attack=int(total_attack),
            target_ac=int(effective_target_ac),
            nat20=bool(is_nat20),
            nat1=bool(is_nat1),
        )

        result_payload = {
            "attack_id": pending.get("pending_attack_id", ""),
            "player_id": pending.get("player_id", ""),
            "encounter_id": pending.get("encounter_id", ""),
            "attacker_token_id": attacker.token_id,
            "attacker_name": attacker.display_name,
            "target_token_id": target.token_id,
            "target_name": target.display_name,
            "roll": int(d20_int),
            "total": int(total_attack),
            "ac": int(effective_target_ac),
            "cover": str(cover_tier),
            "cover_bonus": int(cover_bonus),
            "result": "MISS",
            "nat20": bool(is_nat20),
            "nat1": bool(is_nat1),
            "damage": 0,
            "target_hp": int(getattr(target, "hp", 0)),
            "target_max_hp": int(getattr(target, "max_hp", 0)),
            "ttl_seconds": 120,
        }

        if result_payload.get("player_id"):
            self.server.post_attack_result( result_payload)
            self.server.post_message(result_payload["player_id"],
                "Attack resolved: MISS.",
                "info",
                ttl_seconds=12,
                data={"pending_attack_id": result_payload["attack_id"], "result": "MISS"}
            )

        # Clear pending + UI
        self.state.pending_attack = None
        self._set_hud_status("Attack resolved.", hold_sec=2.0)
        self.update_combat_hud()
        self.refresh_player_view()

    def resolve_npc_attack_menu(self) -> None:
        """
        Menu action: resolve the currently armed NPC->target attack once.
        """
        if not self._armed_attacker_id or not self._armed_target_id:
            self._set_hud_status("Select an attacker and target first.")
            self.update_combat_hud()
            return

        self.resolve_npc_attack(
            self._armed_attacker_id,
            self._armed_target_id,
            encounter_id=str(getattr(self.state, "encounter_id", "") or ""),
        )

    def add_token_from_browser(self, payload):
        """
        payload can be:
        - dict: {"template_id": "..."}   (new)
        - str:  "/abs/path/to/image.png" (legacy)
        """
        if isinstance(payload, dict) and payload.get("template_id"):
            self.add_token_from_template_id(payload["template_id"])
            return

        # legacy: payload is filepath string
        if isinstance(payload, str):
            self.add_token_from_path(payload)
            return

        print("[TOKENS] Unknown token_selected payload:", payload)

    def add_token_from_template_id(self, template_id: str):
        tokens_file = os.path.join(self.campaign_path, "tokens.json")
        if not os.path.exists(tokens_file):
            print("[TOKENS] tokens.json missing")
            return

        try:
            with open(tokens_file, "r") as f:
                tokens_data = json.load(f)
        except Exception as e:
            print("[TOKENS] Failed to load tokens.json:", e)
            return

        token_template = next((t for t in tokens_data if t.get("template_id") == template_id), None)
        if not token_template:
            print(f"[TOKENS] template_id not found: {template_id}")
            return

        icon_name = token_template.get("icon", "")
        token_path = os.path.join(self.campaign_path, "tokens", icon_name)

        if not os.path.exists(token_path):
            print(f"[TOKENS] Missing token image for template {template_id}: {token_path}")
            return

        # spawn using the same logic, but from template directly
        self._spawn_token_from_template(token_template, token_path)

    def _spawn_token_from_template(self, token_template: dict, token_path: str):
        # ---- PC spawn gating / sheet hydration ----
        kind = token_template.get("kind", "npc")
        side = token_template.get("side", "enemy")
        stat_source = token_template.get("stat_source", "template")
        player_id = token_template.get("player_id", "")
        character_id = token_template.get("character_id", "")

        dead_icon = (token_template.get("dead_icon") or "").strip()
        dead_rel = os.path.join("tokens", dead_icon) if dead_icon else ""

        sheet = None
        if kind == "pc" and side == "player" and stat_source == "character_sheet":
            if not player_id or not character_id:
                QMessageBox.warning(
                    self,
                    "Cannot Spawn PC",
                    "PC token must have player_id and character_id (sheet-backed)."
                )
                return

            sheet = self.server.get_character_sheet(character_id)
            if not sheet:
                QMessageBox.warning(
                    self,
                    "Cannot Spawn PC",
                    f"Character sheet not found on server for character_id={character_id}"
                )
                return

            sheet_player = (sheet.get("player_id") or "").strip()
            if sheet_player and sheet_player != player_id:
                QMessageBox.warning(
                    self,
                    "Cannot Spawn PC",
                    f"player_id mismatch for sheet.\nToken: {player_id}\nSheet: {sheet_player}"
                )
                return

        # ---- Create scene token ----
        pix = QPixmap(token_path)
        token = DraggableToken(pix, grid_size=GRID_SIZE)
        token.on_moved_callback = self.on_token_moved
        token.on_cover_override_callback = self.on_token_cover_override
        token.setPos(100, 100)
        token.filepath = token_path

        token.display_name = token_template.get("name", os.path.basename(token_path))

        # ---- Phase 5 rule: if sheet-backed, do NOT read hp/ac from base_stats/resources ----
        if sheet:
            # Single mapping source: CombatEngine view
            view = self.combat_engine.get_sheet_combat_view(sheet)

            token.max_hp = int(view.max_hp)
            token.hp = int(view.current_hp)
            token.ac = int(view.ac)
            token.attack_modifier = int(view.attack_modifier)

            # movement/vision in v2 schema live under stats as *_ft
            st = sheet.get("stats") or {}
            token.movement = int(st.get("movement_ft", token_template.get("movement", 30)) or 30)
            token.vision_ft = int(st.get("vision_ft", token_template.get("vision_ft", 60)) or 60)

            # Prefer equipped ids (authoritative)
            eq = sheet.get("equipped") or {}
            weapon_id = (eq.get("weapon_id") or token_template.get("weapon_id") or "").strip()
            armor_id = (eq.get("armor_id") or token_template.get("armor_id") or "").strip()

            token.weapon_id = weapon_id
            token.armor_id = armor_id

            # Keep legacy fields populated too (can be id or name)
            token.weapon = weapon_id or token_template.get("weapon", "")
            token.armor = armor_id or token_template.get("armor", "")

            # abilities/proficiency plumbing (prefer sheet if present)
            sheet_abilities = sheet.get("abilities") or {}
            sheet_prof_bonus = int(sheet.get("proficiency_bonus") or 0)
            sheet_save_profs = list(sheet.get("save_proficiencies") or [])

            token.abilities = dict(sheet_abilities or token_template.get("abilities") or {})
            token.proficiency_bonus = int(sheet_prof_bonus or token_template.get("proficiency_bonus") or 0)
            token.save_proficiencies = list(sheet_save_profs or token_template.get("save_proficiencies") or [])

        else:
            # ---- Template-backed token (NPC or local stats) ----
            token.max_hp = int(token_template.get("max_hp", 10))
            token.hp = int(token_template.get("max_hp", token.max_hp))
            token.ac = int(token_template.get("ac", 10))

            weapon_id = (token_template.get("weapon_id") or "").strip()
            armor_id = (token_template.get("armor_id") or "").strip()
            token.weapon_id = weapon_id
            token.armor_id = armor_id
            token.weapon = weapon_id or token_template.get("weapon", "")
            token.armor = armor_id or token_template.get("armor", "")

            token.movement = int(token_template.get("movement", 30))
            token.attack_modifier = int(token_template.get("attack_modifier", 0))
            token.vision_ft = int(token_template.get("vision_ft", 60))

            # B-X4: Vision types / senses
            token.vision_type = str(token_template.get("vision_type", "normal") or "normal")
            token.darkvision_ft = int(token_template.get("darkvision_ft", 0) or 0)
            token.blindsight_ft = int(token_template.get("blindsight_ft", 0) or 0)
            token.truesight_ft = int(token_template.get("truesight_ft", 0) or 0)
            token.tremorsense_ft = int(token_template.get("tremorsense_ft", 0) or 0)
            token.devils_sight_ft = int(token_template.get("devils_sight_ft", 0) or 0)

            token.abilities = dict(token_template.get("abilities") or {})
            token.proficiency_bonus = int(token_template.get("proficiency_bonus") or 0)
            token.save_proficiencies = list(token_template.get("save_proficiencies") or [])

        # ---- Common fields ----
        token.side = side
        token.kind = kind
        token.player_id = player_id
        token.character_id = character_id
        token.stat_source = stat_source
        token.template_id = token_template.get("template_id", "")

        token.update_hp_bar()
        self.scene.addItem(token)
        self.token_items.append(token)

        # ---- Insert into runtime state ----
        rel_image = os.path.relpath(token_path, self.campaign_path)
        gx, gy = self.token_grid_xy(token)

        self.state.tokens[token.token_id] = TokenState(
            token_id=token.token_id,
            display_name=token.display_name,
            image_relpath=rel_image,
            grid_x=gx,
            grid_y=gy,

            template_id=token.template_id,
            dead_image_relpath=dead_rel,

            hp=token.hp,
            max_hp=token.max_hp,
            ac=token.ac,

            weapon_id=getattr(token, "weapon_id", "") or "",
            armor_id=getattr(token, "armor_id", "") or "",

            weapon=token.weapon,
            armor=token.armor,
            movement=token.movement,
            attack_modifier=token.attack_modifier,
            side=token.side,
            vision_ft=token.vision_ft,
            vision_type=str(getattr(token, "vision_type", "normal") or "normal"),
            darkvision_ft=int(getattr(token, "darkvision_ft", 0) or 0),
            blindsight_ft=int(getattr(token, "blindsight_ft", 0) or 0),
            truesight_ft=int(getattr(token, "truesight_ft", 0) or 0),
            tremorsense_ft=int(getattr(token, "tremorsense_ft", 0) or 0),
            devils_sight_ft=int(getattr(token, "devils_sight_ft", 0) or 0),
            kind=token.kind,
            player_id=token.player_id,
            character_id=token.character_id,
            stat_source=token.stat_source,

            abilities=dict(getattr(token, "abilities", {}) or {}),
            proficiency_bonus=int(getattr(token, "proficiency_bonus", 0) or 0),
            save_proficiencies=list(getattr(token, "save_proficiencies", []) or []),
        )

        # ---- Safety: hydrate again from sheet into TokenState (authoritative refresh) ----
        ts = self.state.tokens.get(token.token_id)
        if ts and getattr(ts, "stat_source", "") == "character_sheet" and getattr(ts, "character_id", ""):
            self.hydrate_tokenstate_from_sheet(ts, include_hp=True)

            # Push authoritative stats into scene token
            token.hp = ts.hp
            token.max_hp = ts.max_hp
            token.ac = ts.ac
            token.movement = ts.movement
            token.weapon_id = getattr(ts, "weapon_id", "") or token.weapon_id
            token.armor_id = getattr(ts, "armor_id", "") or token.armor_id
            token.weapon = ts.weapon
            token.armor = ts.armor
            token.vision_ft = ts.vision_ft
            token.attack_modifier = ts.attack_modifier
            token.update_hp_bar()

        self.refresh_player_view()

    def _set_hud_status(self, msg: str, hold_sec: float = 2.5):
        self._hud_status_msg = msg
        self._hud_status_until = time.monotonic() + float(hold_sec)

    def update_combat_hud(self):
        """Refresh the Combat HUD and enforce pending-attack + awaiting-damage expiration."""
        now = time.monotonic()

        pending = getattr(self.state, "pending_attack", None)

        # --- Auto-clear expired pending attack ---
        pending_remaining = None
        if pending and pending.get("created_monotonic") is not None:
            try:
                created = float(pending.get("created_monotonic"))
                ttl = float(pending.get("expires_in_sec", 90))
                pending_remaining = ttl - (now - created)
                if pending_remaining <= 0:
                    pid = pending.get("pending_attack_id", "")
                    self.state.pending_attack = None
                    self._set_hud_status(f"Pending attack expired; cleared ({pid}).", hold_sec=4.0)
                    pending = None
                    pending_remaining = None
            except Exception as e:
                print("[HUD] pending parse error; clearing:", e)
                self.state.pending_attack = None
                pending = None
                pending_remaining = None

        # --- Auto-clear expired awaiting_damage ---
        aw = getattr(self, "_awaiting_damage", None)
        awaiting_remaining = None
        if aw and aw.get("created_monotonic") is not None:
            try:
                created = float(aw.get("created_monotonic"))
                ttl = float(aw.get("expires_in_sec", 90))
                awaiting_remaining = ttl - (now - created)
                if awaiting_remaining <= 0:
                    attack_id = (aw.get("attack_id", "") or "").strip()
                    player_id = (aw.get("player_id", "") or "").strip()
                    self._awaiting_damage = None
                    self._set_hud_status(f"Damage roll timed out; cleared ({attack_id}).", hold_sec=4.0)
                    self.campaign_logger.combat("awaiting_damage_expired", attack_id=attack_id, player_id=player_id)
                    aw = None
                    awaiting_remaining = None
            except Exception as e:
                print("[HUD] awaiting_damage parse error; clearing:", e)
                self._awaiting_damage = None
                aw = None
                awaiting_remaining = None

        # Re-load after possible clear
        aw = getattr(self, "_awaiting_damage", None)

        awaiting_text = "—"
        awaiting_expires_in = None
        if aw:
            attack_id = (aw.get("attack_id", "") or "").strip()
            player_id = (aw.get("player_id", "") or "").strip()
            tgt_id = (aw.get("target_token_id", "") or "").strip()
            tgt = self.state.tokens.get(tgt_id)
            tgt_name = tgt.display_name if tgt else tgt_id
            dmg_expr = (aw.get("damage_expr", "") or "").strip()
            crit = bool(aw.get("crit", False))

            awaiting_text = f"attack_id={attack_id} player={player_id} target={tgt_name} expr={dmg_expr} crit={crit}"
            awaiting_expires_in = awaiting_remaining

        # Selected attacker/target (these are your HUD selections)
        attacker_ts = self.state.tokens.get(self._armed_attacker_id) if getattr(self, "_armed_attacker_id", "") else None
        target_ts = self.state.tokens.get(self._armed_target_id) if getattr(self, "_armed_target_id", "") else None

        attacker_name = attacker_ts.display_name if attacker_ts else ""

        attacker_weapon = ""
        if attacker_ts:
            weapon_ref = (
                (getattr(attacker_ts, "weapon_id", "") or "").strip()
                or (getattr(attacker_ts, "weapon", "") or "").strip()
            )
            if weapon_ref:
                wd = self.get_weapon_data(weapon_ref)
                attacker_weapon = wd.get("name", weapon_ref) if wd else weapon_ref

        target_name = target_ts.display_name if target_ts else ""

        cover_text = "—"
        try:
            if attacker_ts and target_ts and getattr(self, "map_pixmap", None) is not None:
                cols = max(1, int(self.map_pixmap.width()) // int(GRID_SIZE))
                rows = max(1, int(self.map_pixmap.height()) // int(GRID_SIZE))
                meta = getattr(self, "current_map_meta", {}) or {}
                tier, bonus, _dbg = compute_cover(
                    attacker_grid_x=int(attacker_ts.grid_x),
                    attacker_grid_y=int(attacker_ts.grid_y),
                    target_grid_x=int(target_ts.grid_x),
                    target_grid_y=int(target_ts.grid_y),
                    meta=meta,
                )
                if tier == "none":
                    cover_text = "None"
                elif tier == "half":
                    cover_text = "Half (+2 AC)"
                elif tier == "three_quarters":
                    cover_text = "3/4 (+5 AC)"
                else:
                    cover_text = "Total (Blocked)"
        except Exception:
            cover_text = "—"


        pending_id = pending.get("pending_attack_id", "") if pending else ""

        expires_in = None
        if pending_remaining is not None:
            expires_in = max(0.0, float(pending_remaining))

        # Status line
        if pending:
            pid = (pending.get("pending_attack_id", "") or "").strip()
            status = f"Pending attack armed (pid={pid[:8]}). Waiting for to-hit roll."
        elif aw:
            aid = (aw.get("attack_id", "") or "").strip()
            status = f"Awaiting damage (pid={aid[:8]})."
        elif attacker_ts and target_ts:
            status = "Ready: attacker + target selected."
        elif attacker_ts:
            status = "Select a target."
        else:
            status = "Idle"

        # Enablement rules
        enable_clear = bool(attacker_ts or target_ts or pending or aw)
        enable_cancel = bool(pending)
        enable_cancel_awaiting = bool(aw)

        enable_arm = bool(attacker_ts and target_ts and getattr(attacker_ts, "side", "") == "player")
        enable_force = bool(attacker_ts and target_ts and getattr(attacker_ts, "side", "") != "player")

        selected_ts = self.get_selected_tokenstate()
        enable_death_save = bool(
            selected_ts
            and self._is_pc(selected_ts)
            and getattr(selected_ts, "death_state", "alive") == "down"
        )

        # Phase 3: keep initiative panel refreshed on same tick
        self.update_initiative_panel()

        # Render (NEVER crash session because HUD broke)
        try:
            self.combat_hud.render(
                status=status,
                attacker_name=attacker_name,
                attacker_weapon=attacker_weapon,
                target_name=target_name,
                cover_text=cover_text,
                pending_id=pending_id,
                expires_in=expires_in,
                awaiting_text=awaiting_text,
                awaiting_expires_in=awaiting_expires_in,
                enable_arm=enable_arm,
                enable_cancel=enable_cancel,
                enable_force=enable_force,
                enable_death_save=enable_death_save,
                enable_clear=enable_clear,
                enable_cancel_awaiting=enable_cancel_awaiting,
            )
        except Exception as e:
            print("[HUD] render error:", e)


    def hud_clear_selection(self):
        # Clear combat selection state
        self._armed_attacker_id = None
        self._armed_target_id = None
        self._clear_pending_local_and_server()

        # Clear scene selection so selectionChanged fires and overlays are removed
        try:
            if self.scene is not None:
                self.scene.blockSignals(True)
                self.scene.clearSelection()
                self.scene.blockSignals(False)
        except Exception:
            try:
                if self.scene is not None:
                    self.scene.clearSelection()
            except Exception:
                pass

        # Hard-clear overlays on DM scene (do not rely only on selectionChanged)
        try:
            for tok in list(getattr(self, "token_items", [])):
                try:
                    tok.hide_movement_range(self.scene)
                    tok.hide_attack_range(self.scene)
                except Exception:
                    pass
        except Exception:
            pass

        # Hard-clear Player View driver selection
        try:
            if self.player_view_window is not None:
                setattr(self.player_view_window, "selected_token_id", None)
                self.refresh_player_view()
        except Exception:
            pass

        # Clear any cached selection id if you track one
        try:
            self.selected_token_id = None
        except Exception:
            pass

        self._set_hud_status("Cleared selection.")
        self.update_combat_hud()

    def hud_cancel_pending(self):
        if self.state.pending_attack:
            pid = self.state.pending_attack.get("pending_attack_id", "")
            self.campaign_logger.combat("cancel_pending", pending_attack_id=pid)
            try:
                self.server.cancel_pending_attack( pid)
            except Exception as e:
                print("[HUD] server cancel failed:", e)

            player_id = (self.state.pending_attack.get("player_id", "") or "").strip()
            if player_id:
                self.server.post_message(player_id,
                    f"DM cancelled the pending attack (id={pid}).",
                    "warn",
                    ttl_seconds=20,
                    data={"pending_attack_id": pid}
                )

            self._clear_pending_local_and_server()
            self._set_hud_status(f"Cancelled pending attack {pid}.")
        else:
            self._set_hud_status("No pending attack to cancel.")
        self.update_combat_hud()

    def hud_arm_pc_attack(self):
        if not self._armed_attacker_id or not self._armed_target_id:
            self._set_hud_status("Select an attacker and a target first.")
            self.update_combat_hud()
            return

        attacker = self.state.tokens.get(self._armed_attacker_id)
        target = self.state.tokens.get(self._armed_target_id)
        if not attacker or not target:
            self._set_hud_status("Attacker/target missing in state.")
            self.update_combat_hud()
            return

        if attacker.side != "player":
            self._set_hud_status("Attacker is not a PC (player-side). Use Force Resolve NPC.")
            self.update_combat_hud()
            return
        
        # Phase 3: turn gating
        if not self._enforce_active_turn(attacker.token_id, action_name="Arm PC Attack"):
            return

        self._arm_pending_pc_attack(attacker, target)
        self._set_hud_status("PC attack armed; waiting for roll.")
        self.update_combat_hud()
        p = self.state.pending_attack or {}
        self.campaign_logger.combat(
            "arm_pc_attack",
            pending_attack_id=p.get("pending_attack_id", ""),
            attacker_token_id=p.get("attacker_token_id", ""),
            target_token_id=p.get("target_token_id", ""),
            player_id=p.get("player_id", ""),
            weapon_ref=p.get("weapon_ref", ""),
        )

    def hud_force_resolve_npc(self):
        if not self._armed_attacker_id or not self._armed_target_id:
            self._set_hud_status("Select an attacker and a target first.")
            self.update_combat_hud()
            return

        attacker = self.state.tokens.get(self._armed_attacker_id)
        target = self.state.tokens.get(self._armed_target_id)
        if not attacker or not target:
            self._set_hud_status("Attacker/target missing in state.")
            self.update_combat_hud()
            return

        if getattr(attacker, "side", "") == "player":
            self._set_hud_status("Attacker is a PC. Use Arm PC Attack.")
            self.update_combat_hud()
            return

        # Initiative gating only (NO per-turn action limits)
        if not self._enforce_active_turn(attacker.token_id, action_name="NPC Attack"):
            return

        try:
            self.campaign_logger.combat(
                "force_resolve_npc",
                attacker_token_id=attacker.token_id,
                target_token_id=target.token_id,
            )
        except Exception:
            pass

        # NPC attacks resolve immediately (system rolls)
        self.state.pending_attack = None

        try:
            self.resolve_npc_attack(
                attacker.token_id,
                target.token_id,
                encounter_id=str(getattr(self.state, "encounter_id", "") or ""),
            )
        except Exception as e:
            self._set_hud_status(f"NPC attack failed: {e}")
            self.update_combat_hud()
            return

        # Force visuals to reflect updated state
        try:
            self._sync_scene_token_from_state(target.token_id)
        except Exception:
            pass

        self._set_hud_status("NPC attack resolved.")
        self.update_combat_hud()

    def hud_roll_death_save(self):
        ts = self.get_selected_tokenstate()
        if not ts:
            self._set_hud_status("Select a downed PC token first.", hold_sec=3.0)
            self.update_combat_hud()
            return

        if not self._is_pc(ts):
            self._set_hud_status("Death saves are PCs only.", hold_sec=3.0)
            self.update_combat_hud()
            return

        if getattr(ts, "death_state", "alive") != "down":
            self._set_hud_status("Token is not downed.", hold_sec=3.0)
            self.update_combat_hud()
            return

        if not self._register_pc_death_save_request(ts, reason="manual"):
            self._set_hud_status("Could not request death save.", hold_sec=3.0)
        self.update_combat_hud()

    def _sync_death_state_to_sheet(self, ts) -> None:
        try:
            if not self._is_player_controlled_token(ts):
                return
            character_id = str(getattr(ts, "character_id", "") or "").strip()
            if not character_id or not getattr(self, "server_client", None):
                return
            self.server_client.update_character_death_saves(
                character_id,
                successes=int(getattr(ts, "death_save_successes", 0) or 0),
                failures=int(getattr(ts, "death_save_failures", 0) or 0),
            )
        except Exception as e:
            print("[DEATH] sync failed:", e)

    def _register_pc_death_save_request(self, ts, *, reason: str = "turn_start") -> bool:
        if not ts or not self._is_player_controlled_token(ts):
            return False
        if str(getattr(ts, "death_state", "alive") or "alive") != "down":
            return False
        if not getattr(self, "server", None):
            return False
        existing = str(getattr(ts, "pending_death_save_request_id", "") or "").strip()
        if existing and existing in getattr(self, "_pending_death_save_requests", {}):
            return True

        request_id = uuid.uuid4().hex
        spec = {
            "request_id": request_id,
            "token_id": str(getattr(ts, "token_id", "") or ""),
            "token_name": str(getattr(ts, "display_name", "") or getattr(ts, "token_id", "") or "Token"),
            "character_id": str(getattr(ts, "character_id", "") or ""),
            "player_id": str(getattr(ts, "player_id", "") or ""),
            "ability": "",
            "dc": 10,
            "adv_mode": "normal",
            "label": "Death Save",
            "roll_kind": "death_save",
            "expected_sides": 20,
            "expected_count_min": 1,
            "expected_count_max": 1,
            "ttl_s": 120,
            "context": {
                "kind": "death_save",
                "source_kind": "death",
                "token_id": str(getattr(ts, "token_id", "") or ""),
                "token_name": str(getattr(ts, "display_name", "") or getattr(ts, "token_id", "") or "Token"),
                "reason": str(reason or "turn_start"),
            },
        }
        resp = self.server.register_roll_request(spec)
        returned_id = str((resp or {}).get("request_id", "") or request_id)
        if not returned_id:
            return False
        spec["request_id"] = returned_id
        self._pending_death_save_requests[returned_id] = spec
        ts.pending_death_save_request_id = returned_id
        try:
            self.campaign_logger.combat(
                "save_requested",
                request_id=returned_id,
                token_id=str(getattr(ts, "token_id", "") or ""),
                name=str(getattr(ts, "display_name", getattr(ts, "token_id", "Token")) or "Token"),
                player_id=str(getattr(ts, "player_id", "") or ""),
                character_id=str(getattr(ts, "character_id", "") or ""),
                ability="DEATH",
                dc=10,
                mode="normal",
                label="Death Save",
            )
        except Exception:
            pass
        self._set_hud_status(f"Death save requested for {getattr(ts, 'display_name', 'PC')}.", hold_sec=3.0)
        return True

    def _apply_death_save_result(self, ts, payload: Dict[str, Any], spec: Dict[str, Any]) -> None:
        if not ts:
            return
        d20 = int(payload.get("chosen", 0) or 0)
        name = str(getattr(ts, "display_name", getattr(ts, "token_id", "PC")) or "PC")
        ts.pending_death_save_request_id = ""

        try:
            self.campaign_logger.combat(
                "death_save",
                token_id=str(getattr(ts, "token_id", "") or ""),
                display_name=name,
                d20=int(d20),
                successes=int(getattr(ts, "death_save_successes", 0) or 0),
                failures=int(getattr(ts, "death_save_failures", 0) or 0),
                death_state=str(getattr(ts, "death_state", "alive") or "alive"),
                hp=int(getattr(ts, "hp", 0) or 0),
            )
        except Exception:
            pass

        if d20 == 20:
            ts.hp = 1
            ts.death_state = "alive"
            ts.death_save_successes = 0
            ts.death_save_failures = 0
            self._set_hud_status(f"Death Save: NAT 20 → {name} stands up (HP=1)!", hold_sec=4.0)
        else:
            if d20 == 1:
                ts.death_save_failures = int(getattr(ts, "death_save_failures", 0) or 0) + 2
                self._set_hud_status(f"Death Save: NAT 1 → 2 failures ({ts.death_save_failures}/3)", hold_sec=4.0)
            elif d20 < 10:
                ts.death_save_failures = int(getattr(ts, "death_save_failures", 0) or 0) + 1
                self._set_hud_status(f"Death Save: {d20} fail ({ts.death_save_failures}/3)", hold_sec=3.5)
            else:
                ts.death_save_successes = int(getattr(ts, "death_save_successes", 0) or 0) + 1
                self._set_hud_status(f"Death Save: {d20} success ({ts.death_save_successes}/3)", hold_sec=3.5)

            if int(getattr(ts, "death_save_failures", 0) or 0) >= 3:
                ts.death_state = "dead"
                ts.hp = 0
                self._set_dead_visual_if_needed(ts.token_id)
                self._set_hud_status(f"{name} has died.", hold_sec=4.0)
            elif int(getattr(ts, "death_save_successes", 0) or 0) >= 3:
                ts.death_state = "stable"
                ts.hp = 0
                self._set_hud_status(f"{name} is stable (HP=0).", hold_sec=4.0)
            else:
                ts.death_state = "down"
                ts.hp = 0

        self._sync_death_state_to_sheet(ts)
        self._sync_token_statuses_to_sheet(ts)
        try:
            self.apply_state_hp_to_scene_token(ts.token_id)
        except Exception:
            pass
        try:
            self.refresh_player_view()
        except Exception:
            pass
        self.update_combat_hud()

    def _apply_damage_while_downed(self, ts, *, damage_amount: int, source_kind: str = "", source_meta: dict | None = None) -> None:
        if not ts or not self._is_pc(ts):
            return
        prior = str(getattr(ts, "death_state", "alive") or "alive")
        if prior not in {"down", "stable"}:
            return
        if int(damage_amount or 0) <= 0:
            return
        crit = False
        try:
            sm = dict(source_meta or {})
            crit = bool(sm.get("crit") or sm.get("critical") or sm.get("nat20"))
        except Exception:
            crit = False
        fails = 2 if crit else 1
        ts.death_state = "down"
        ts.hp = 0
        ts.death_save_failures = int(getattr(ts, "death_save_failures", 0) or 0) + fails
        if int(getattr(ts, "death_save_failures", 0) or 0) >= 3:
            ts.death_save_failures = 3
            ts.death_state = "dead"
            self._set_dead_visual_if_needed(ts.token_id)
            self._set_hud_status(f"{getattr(ts, 'display_name', 'PC')} has died.", hold_sec=4.0)
        else:
            self._set_hud_status(f"{getattr(ts, 'display_name', 'PC')} takes damage at 0 HP → {fails} death save failure{'s' if fails != 1 else ''}.", hold_sec=4.0)
        try:
            self.campaign_logger.combat(
                "death_save",
                token_id=str(getattr(ts, "token_id", "") or ""),
                display_name=str(getattr(ts, "display_name", getattr(ts, "token_id", "PC")) or "PC"),
                d20=0,
                successes=int(getattr(ts, "death_save_successes", 0) or 0),
                failures=int(getattr(ts, "death_save_failures", 0) or 0),
                death_state=str(getattr(ts, "death_state", "down") or "down"),
                hp=0,
            )
        except Exception:
            pass
        self._sync_death_state_to_sheet(ts)
        self._sync_token_statuses_to_sheet(ts)

    def _set_dead_visual_if_needed(self, token_id):
        """
        Swap a token's visual to a dead/corpse image if one exists.
        If no corpse visual is defined, do nothing.

        This helper must never crash because death resolution
        should not depend on visuals.
        """
        try:
            token = None

            # token lookup
            if hasattr(self, "scene_tokens"):
                token = self.scene_tokens.get(token_id)

            if token is None and hasattr(self, "encounter_state"):
                token = self.encounter_state.tokens.get(token_id)

            if token is None:
                return

            corpse_img = None

            # preferred corpse visual keys
            for key in (
                "dead_image",
                "corpse_image",
                "dead_token_image",
                "corpse_token_image"
            ):
                if isinstance(token, dict) and token.get(key):
                    corpse_img = token[key]
                    break

            # if no corpse visual exists, silently exit
            if not corpse_img:
                return

            # apply visual swap
            if isinstance(token, dict):
                token["image"] = corpse_img

            # refresh map rendering if available
            if hasattr(self, "map_widget") and hasattr(self.map_widget, "update"):
                self.map_widget.update()

        except Exception as e:
            print("[DEAD_VISUAL] non-fatal error:", e)

    def _maybe_open_death_save_for_token(self, token_id: str) -> None:
        if not token_id or token_id not in self.state.tokens:
            return
        ts = self.state.tokens.get(token_id)
        if not ts or not self._is_player_controlled_token(ts):
            return
        if str(getattr(ts, "death_state", "alive") or "alive") != "down":
            return
        self._register_pc_death_save_request(ts, reason="turn_start")

    def hydrate_tokenstate_from_sheet(
        self,
        ts: TokenState,
        sheet: Optional[Dict[str, Any]] = None,
        *,
        include_hp: bool = True,
    ) -> TokenState:
        """Hydrate a TokenState from an authoritative character sheet.

        - If sheet is None, fetch via ts.character_id using ServerClient.
        - Uses get_sheet_combat_view() to normalize across sheet schema variants.
        - Does NOT do any Qt work and never raises.
        """
        try:
            if sheet is None:
                cid = getattr(ts, "character_id", "") or ""
                if not cid or not getattr(self, "server_client", None):
                    return ts
                sheet = self.server_client.get_character_sheet(cid)

            if not isinstance(sheet, dict) or not sheet:
                return ts

            view = get_sheet_combat_view(sheet) or {}
            if not isinstance(view, dict) or not view:
                return ts

            # Core combat stats
            ts.ac = int(view.get("ac", getattr(ts, "ac", 10)) or 10)
            ts.attack_modifier = int(view.get("attack_modifier", getattr(ts, "attack_modifier", 0)) or 0)
            try:
                ts.feat_ids = [str(x).strip().lower() for x in ((sheet.get("feats") if isinstance(sheet.get("feats"), list) else []) or []) if str(x).strip()]
                ts.feat_state = dict(sheet.get("feat_state") or {}) if isinstance(sheet.get("feat_state"), dict) else {}
            except Exception:
                ts.feat_ids = list(getattr(ts, "feat_ids", []) or [])
                ts.feat_state = dict(getattr(ts, "feat_state", {}) or {})

            new_move = int(view.get("movement", getattr(ts, "movement", 30)) or 30)
            ts.movement = new_move
            ts.base_movement = new_move
            try:
                mr = getattr(ts, "movement_remaining", None)
                if mr is not None:
                    ts.movement_remaining = max(0, min(int(mr), new_move))
            except Exception:
                pass

            ts.vision_ft = int(view.get("vision_ft", getattr(ts, "vision_ft", 60)) or 60)
            ts.darkvision_ft = int(view.get("darkvision_ft", getattr(ts, "darkvision_ft", 0)) or 0)
            ts.rage_active = bool(view.get("rage_active", getattr(ts, "rage_active", False)))
            ts.reckless_attack_active = bool(view.get("reckless_attack_active", getattr(ts, "reckless_attack_active", False)))
            ts.rage_damage_bonus = int(view.get("rage_damage_bonus", getattr(ts, "rage_damage_bonus", 0)) or 0)
            ts.brutal_critical_dice = int(view.get("brutal_critical_dice", getattr(ts, "brutal_critical_dice", 0)) or 0)
            ts.attacks_per_action = int(view.get("attacks_per_action", getattr(ts, "attacks_per_action", 1)) or 1)
            ts.initiative_advantage = bool(view.get("initiative_advantage", getattr(ts, "initiative_advantage", False)))
            ts.danger_sense = bool(view.get("danger_sense", getattr(ts, "danger_sense", False)))
            ts.relentless_rage_uses = int(view.get("relentless_rage_uses", getattr(ts, "relentless_rage_uses", 0)) or 0)
            ts.sneak_attack_dice = int(view.get("sneak_attack_dice", getattr(ts, "sneak_attack_dice", 0)) or 0)
            ts.uncanny_dodge_armed = bool(view.get("uncanny_dodge_armed", getattr(ts, "uncanny_dodge_armed", False)))
            ts.evasion = bool(view.get("evasion", getattr(ts, "evasion", False)))
            ts.reliable_talent = bool(view.get("reliable_talent", getattr(ts, "reliable_talent", False)))
            ts.blindsense_ft = int(view.get("blindsense_ft", getattr(ts, "blindsense_ft", 0)) or 0)
            ts.slippery_mind = bool(view.get("slippery_mind", getattr(ts, "slippery_mind", False)))
            ts.elusive = bool(view.get("elusive", getattr(ts, "elusive", False)))
            ts.aura_of_protection_bonus = int(view.get("aura_of_protection_bonus", getattr(ts, "aura_of_protection_bonus", 0)) or 0)
            ts.aura_of_protection_radius_ft = int(view.get("aura_of_protection_radius_ft", getattr(ts, "aura_of_protection_radius_ft", 0)) or 0)
            ts.aura_of_courage = bool(view.get("aura_of_courage", getattr(ts, "aura_of_courage", False)))
            ts.aura_of_courage_radius_ft = int(view.get("aura_of_courage_radius_ft", getattr(ts, "aura_of_courage_radius_ft", 0)) or 0)
            ts.improved_divine_smite_dice = int(view.get("improved_divine_smite_dice", getattr(ts, "improved_divine_smite_dice", 0)) or 0)
            ts.divine_sense_active = bool(view.get("divine_sense_active", getattr(ts, "divine_sense_active", False)))
            if view.get("damage_profile"):
                ts.damage_profile = dict(view.get("damage_profile") or {})
                ts.damage_resistances = list((view.get("damage_profile") or {}).get("resistances", []) or [])
                ts.damage_immunities = list((view.get("damage_profile") or {}).get("immunities", []) or [])
                ts.damage_vulnerabilities = list((view.get("damage_profile") or {}).get("vulnerabilities", []) or [])
            if view.get("save_bonus"):
                ts.save_bonus = dict(view.get("save_bonus") or {})
            if view.get("ignore_difficult_terrain"):
                ts.ignore_difficult_terrain = True

            # Equipment
            wid = str(view.get("weapon_id", "") or "").strip()
            aid = str(view.get("armor_id", "") or "").strip()
            ts.weapon_id = wid
            ts.weapon = wid
            ts.armor_id = aid
            ts.armor = aid

            # HP
            ts.max_hp = max(1, int(view.get("max_hp", getattr(ts, "max_hp", 10)) or 10))
            if include_hp:
                ts.hp = max(0, min(int(view.get("current_hp", getattr(ts, "hp", ts.max_hp)) or 0), ts.max_hp))

            return ts
        except Exception:
            return ts
    def _roll_brutal_critical_bonus(self, damage_expr: str, extra_dice: int) -> tuple[int, str]:
        import re
        s = str(damage_expr or "").strip()
        m = re.match(r"^\s*(\d+)\s*d\s*(\d+)", s, re.IGNORECASE)
        if not m:
            return 0, ""
        base_n = int(m.group(1) or 0)
        sides = int(m.group(2) or 0)
        if base_n <= 0 or sides <= 0 or int(extra_dice or 0) <= 0:
            return 0, ""
        count = max(0, int(extra_dice)) * base_n
        rolls = [random.randint(1, sides) for _ in range(count)]
        return int(sum(rolls)), f"brutal critical {count}d{sides} -> {rolls}"

    def _register_relentless_rage_request(self, ts, *, dc: int, damage_amount: int, source_kind: str = "attack") -> bool:
        if not self._is_player_controlled_token(ts):
            return False
        existing = str(getattr(ts, "pending_relentless_rage_request_id", "") or "").strip()
        if existing and existing in getattr(self, "_pending_save_requests", {}):
            return True
        request_id = uuid.uuid4().hex
        spec = {
            "request_id": request_id,
            "token_id": str(getattr(ts, "token_id", "") or ""),
            "token_name": str(getattr(ts, "display_name", "") or getattr(ts, "token_id", "") or "Token"),
            "character_id": str(getattr(ts, "character_id", "") or ""),
            "player_id": str(getattr(ts, "player_id", "") or ""),
            "ability": "CON",
            "dc": int(dc),
            "adv_mode": "normal",
            "label": "Relentless Rage",
            "roll_kind": "save",
            "expected_sides": 20,
            "expected_count_min": 1,
            "expected_count_max": 1,
            "ttl_s": 120,
            "context": {
                "ability": "CON",
                "dc": int(dc),
                "token_id": str(getattr(ts, "token_id", "") or ""),
                "token_name": str(getattr(ts, "display_name", "") or getattr(ts, "token_id", "") or "Token"),
                "kind": "relentless_rage",
                "source_kind": str(source_kind or "attack"),
                "damage_amount": int(damage_amount or 0),
            },
        }
        resp = self.server.register_roll_request(spec)
        returned_id = str((resp or {}).get("request_id", "") or request_id)
        if not returned_id:
            return False
        spec["request_id"] = returned_id
        self._pending_save_requests[returned_id] = spec
        ts.pending_relentless_rage_request_id = returned_id
        return True

    def _token_has_feat(self, ts, feat_id: str) -> bool:
        try:
            feats = [str(x).strip().lower() for x in (getattr(ts, "feat_ids", []) or []) if str(x).strip()]
            return str(feat_id or "").strip().lower() in feats
        except Exception:
            return False

    def _token_feat_state(self, ts, feat_id: str) -> dict:
        try:
            feat_state = getattr(ts, "feat_state", {}) or {}
            if isinstance(feat_state, dict):
                row = feat_state.get(str(feat_id or "").strip().lower())
                if isinstance(row, dict):
                    return dict(row)
        except Exception:
            pass
        return {}

    def _token_feat_enabled(self, ts, feat_id: str, default: bool = False) -> bool:
        if not self._token_has_feat(ts, feat_id):
            return False
        row = self._token_feat_state(ts, feat_id)
        if "enabled" in row:
            return bool(row.get("enabled"))
        return bool(default)

    def _weapon_bucket(self, weapon_data: dict | None) -> set[str]:
        wd = weapon_data if isinstance(weapon_data, dict) else {}
        vals = []
        vals.extend(wd.get("tags") or [])
        vals.extend(wd.get("properties") or [])
        vals.append(wd.get("weapon_type") or "")
        vals.append(wd.get("type") or "")
        out = set()
        for v in vals:
            s = str(v or "").strip().lower()
            if s:
                out.add(s)
        return out

    def _great_weapon_master_mods(self, attacker_ts, weapon_data: dict | None, attack_kind: str) -> tuple[int, int]:
        if str(attack_kind or "").strip().lower() != "weapon":
            return (0, 0)
        if not self._token_feat_enabled(attacker_ts, "great_weapon_master", default=False):
            return (0, 0)
        bucket = self._weapon_bucket(weapon_data)
        if "melee" not in bucket and str((weapon_data or {}).get("weapon_type") or "").strip().lower() != "melee":
            return (0, 0)
        if "heavy" not in bucket:
            return (0, 0)
        return (-5, 10)

    def _sharpshooter_mods(self, attacker_ts, weapon_data: dict | None, attack_kind: str) -> tuple[int, int]:
        if str(attack_kind or "").strip().lower() != "weapon":
            return (0, 0)
        if not self._token_feat_enabled(attacker_ts, "sharpshooter", default=False):
            return (0, 0)
        bucket = self._weapon_bucket(weapon_data)
        if "ranged" not in bucket and str((weapon_data or {}).get("weapon_type") or "").strip().lower() != "ranged":
            return (0, 0)
        return (-5, 10)

    def _apply_sentinel_stop(self, reactor_ts, target_ts) -> None:
        try:
            if reactor_ts is None or target_ts is None or not self._token_has_feat(reactor_ts, "sentinel"):
                return
            target_ts.movement_remaining = 0
            target_ts.speed_zero_until_turn_end = True
            self._sync_scene_token_from_state(str(getattr(target_ts, "token_id", "") or ""))
            self._set_hud_status(f"Sentinel: {getattr(target_ts, 'display_name', 'Target')} speed becomes 0.", hold_sec=3.0)
        except Exception:
            pass

    def _maybe_apply_savage_attacker(self, attacker_ts, damage_expr: str, dmg: int, breakdown: str, *, crit: bool = False, is_melee: bool = False, source_kind: str = "attack") -> tuple[int, str]:
        try:
            if attacker_ts is None or not is_melee or not self._token_has_feat(attacker_ts, "savage_attacker"):
                return int(dmg), str(breakdown or "")
            turn_marker = self._current_turn_marker()
            if str(getattr(attacker_ts, "last_savage_attacker_turn_marker", "") or "") == turn_marker:
                return int(dmg), str(breakdown or "")
            expr = str(damage_expr or "").strip()
            if not expr:
                return int(dmg), str(breakdown or "")
            if bool(crit):
                reroll_total, reroll_breakdown = roll_damage_crit(expr)
            else:
                reroll_total, reroll_breakdown = roll_damage(expr)
            reroll_total = int(reroll_total or 0)
            if reroll_total > int(dmg or 0):
                attacker_ts.last_savage_attacker_turn_marker = turn_marker
                new_break = (str(breakdown or "") + (" | " if breakdown else "") + f"savage_attacker={reroll_total} ({reroll_breakdown})").strip()
                return reroll_total, new_break
        except Exception:
            pass
        return int(dmg), str(breakdown or "")

    def _current_turn_marker(self) -> str:
        try:
            round_no = int(getattr(self.state, "round_number", 1) or 1)
        except Exception:
            round_no = 1
        active_tid = str(getattr(self.state, "active_token_id", "") or "")
        return f"{round_no}:{active_tid}"

    def _weapon_allows_sneak_attack(self, source_meta: dict | None, weapon_ref: str = "") -> bool:
        meta = source_meta if isinstance(source_meta, dict) else {}
        weapon_type = str(meta.get("weapon_type", "") or "").strip().lower()
        props = [str(t).strip().lower() for t in (meta.get("properties") or []) if str(t).strip()]
        tags = [str(t).strip().lower() for t in (meta.get("tags") or []) if str(t).strip()]
        bucket = set(props + tags)
        ref = str(weapon_ref or "").strip().lower()
        return (
            weapon_type == "ranged"
            or "ranged" in bucket
            or "finesse" in bucket
            or ref in {"shortbow", "longbow", "crossbow", "hand_crossbow", "dagger", "rapier", "shortsword"}
        )

    def _token_adjacent(self, a, b) -> bool:
        try:
            ax, ay = int(getattr(a, "grid_x", 0) or 0), int(getattr(a, "grid_y", 0) or 0)
            bx, by = int(getattr(b, "grid_x", 0) or 0), int(getattr(b, "grid_y", 0) or 0)
            return max(abs(ax - bx), abs(ay - by)) <= 1
        except Exception:
            return False

    def _target_has_hostile_adjacent_ally(self, attacker_ts, target_ts) -> bool:
        for other in (getattr(self.state, "tokens", {}) or {}).values():
            if other is None or other is attacker_ts or other is target_ts:
                continue
            if str(getattr(other, "death_state", "alive") or "alive").strip().lower() in {"down", "stable", "dead"}:
                continue
            if not self._tokens_are_hostile(other, target_ts):
                continue
            if self._tokens_are_hostile(other, attacker_ts):
                continue
            if self._token_adjacent(other, target_ts):
                return True
        return False

    def _rogue_can_apply_sneak_attack(self, attacker_ts, target_ts, aw: dict) -> bool:
        if attacker_ts is None or target_ts is None:
            return False
        sneak_dice = int(getattr(attacker_ts, "sneak_attack_dice", 0) or 0)
        if sneak_dice <= 0:
            return False
        turn_marker = self._current_turn_marker()
        if str(getattr(attacker_ts, "last_sneak_attack_turn_marker", "") or "") == turn_marker:
            return False
        source_meta = aw.get("source_meta", {}) if isinstance(aw.get("source_meta"), dict) else {}
        weapon_ref = str(aw.get("weapon_ref", "") or "")
        if not self._weapon_allows_sneak_attack(source_meta, weapon_ref):
            return False
        roll_mode = str(aw.get("roll_mode", "normal") or "normal").strip().lower()
        if roll_mode == "disadvantage":
            return False
        if roll_mode == "advantage":
            return True
        return self._target_has_hostile_adjacent_ally(attacker_ts, target_ts)

    def _roll_sneak_attack_bonus(self, sneak_dice: int, crit: bool = False) -> tuple[int, str]:
        dice_count = max(0, int(sneak_dice or 0))
        if crit:
            dice_count *= 2
        if dice_count <= 0:
            return 0, ""
        rolls = [random.randint(1, 6) for _ in range(dice_count)]
        total = sum(rolls)
        return total, f"sneak_attack {dice_count}d6 rolls={rolls} total={total}"

    def apply_damage_to_token(
        self,
        target_ts: "TokenState",
        amount: int,
        *,
        encounter_id: str = "",
        pending_attack_id: str = "",
        damage_type: str = "",
        source_kind: str = "attack",
        source_meta: dict | None = None,
    ) -> None:
        """
        Wrapper: CombatEngine owns damage + sheet sync; MainWindow owns:
        - deterministic history event recording (APPLY_DAMAGE)
        - UI refresh (hp bar, dead sprite, player view)

        IMPORTANT:
        - replay/undo MUST NOT call the server again
        - therefore we record the post-damage results as hp_after/max_hp_after/death_state_after
        """
        if target_ts is None:
            return

        token_id = getattr(target_ts, "token_id", "") or ""
        if not token_id:
            return

        hp_before = int(getattr(target_ts, "hp", 0) or 0)
        max_before = int(getattr(target_ts, "max_hp", 10) or 10)
        death_before = str(getattr(target_ts, "death_state", "alive") or "alive")

        incoming_amount = int(amount)
        try:
            src_kind_norm = str(source_kind or "attack").strip().lower()
            src_meta = source_meta if isinstance(source_meta, dict) else {}
            tags = [str(t).strip().lower() for t in (src_meta.get("tags") or []) if str(t).strip()]
            weapon_type = str(src_meta.get("weapon_type", "") or "").strip().lower()
            is_ranged_attack = src_kind_norm == "attack" and ((weapon_type == "ranged") or ("ranged" in tags))
            if is_ranged_attack and bool(getattr(target_ts, "deflect_missiles_armed", False)) and self._token_can_take_reaction(target_ts):
                monk_level = 0
                try:
                    if hasattr(self, "server") and self.server is not None:
                        sheet = self.server.get_character_sheet(str(getattr(target_ts, "character_id", "") or "")) or {}
                        cls = sheet.get("class_levels") if isinstance(sheet.get("class_levels"), dict) else {}
                        monk_level = int(cls.get("monk", 0) or 0)
                except Exception:
                    monk_level = 0
                dex_mod = max(0, (int(getattr(target_ts, "dex", 10) or 10) - 10) // 2) if hasattr(target_ts, "dex") else 0
                reduction = random.randint(1, 10) + dex_mod + max(0, monk_level)
                incoming_amount = max(0, incoming_amount - reduction)
                target_ts.deflect_missiles_armed = False
                self._consume_reaction(target_ts, reason="deflect_missiles")
                self._set_hud_status(f"{getattr(target_ts, 'display_name', token_id)} deflects missiles (-{reduction}).", hold_sec=3.0)
                try:
                    self._sync_token_statuses_to_sheet(target_ts)
                except Exception:
                    pass
            if bool(getattr(target_ts, "empty_body_active", False)) and str(damage_type or "").strip().lower() != "force":
                incoming_amount = max(0, incoming_amount // 2)
            incoming_amount, _arcane_ward_absorbed = self._maybe_absorb_arcane_ward(target_ts, incoming_amount)
            if src_kind_norm == "fall":
                incoming_amount = max(0, incoming_amount - int(getattr(target_ts, "slow_fall_reduction", 0) or 0))
            if str(source_kind or "attack").strip().lower() == "attack" and bool(getattr(target_ts, "uncanny_dodge_armed", False)) and self._token_can_take_reaction(target_ts):
                reduced = max(0, incoming_amount // 2)
                if reduced != incoming_amount:
                    incoming_amount = reduced
                    target_ts.uncanny_dodge_armed = False
                    self._consume_reaction(target_ts, reason="uncanny_dodge")
                    self._set_hud_status(f"{getattr(target_ts, 'display_name', token_id)} uses Uncanny Dodge.", hold_sec=3.0)
                    try:
                        self._sync_token_statuses_to_sheet(target_ts)
                        self._sync_combat_flags_to_sheet(target_ts, {"deflect_missiles_armed": False})
                    except Exception:
                        pass
        except Exception:
            pass

        resolution = {}
        try:
            resolution = self.combat_engine.apply_damage_to_token(
                target_ts,
                incoming_amount,
                encounter_id=encounter_id,
                pending_attack_id=pending_attack_id,
                damage_type=str(damage_type or ""),
                source_meta=(source_meta if isinstance(source_meta, dict) else None),
            ) or {}
        finally:
            try:
                self._apply_death_rules_after_damage(target_ts)
            except Exception:
                pass

            hp_after = int(getattr(target_ts, "hp", 0) or 0)
            max_after = int(getattr(target_ts, "max_hp", 10) or 10)
            death_after = str(getattr(target_ts, "death_state", "alive") or "alive")
            try:
                self._sync_death_state_to_sheet(target_ts)
                self._sync_token_statuses_to_sheet(target_ts)
            except Exception:
                pass

            base_damage = int((resolution or {}).get("base_damage", int(amount)) or int(amount))
            final_damage = int((resolution or {}).get("final_damage", int(amount)) or int(amount))
            dmg_steps = list((resolution or {}).get("steps", []) or [])
            dtype = str((resolution or {}).get("damage_type", str(damage_type or "")) or str(damage_type or ""))

            overflow = max(0, int(final_damage) - max(0, int(hp_before)))
            try:
                self._maybe_offer_hellish_rebuke(target_ts, source_kind=str(source_kind or ""), source_meta=(source_meta if isinstance(source_meta, dict) else None), final_damage=int(final_damage))
            except Exception:
                pass
            try:
                self._maybe_offer_wrath_of_the_storm(target_ts, source_kind=str(source_kind or ""), source_meta=(source_meta if isinstance(source_meta, dict) else None), final_damage=int(final_damage))
            except Exception:
                pass
            if self._is_pc(target_ts) and int(final_damage) > 0:
                if death_before in {"down", "stable"}:
                    self._apply_damage_while_downed(target_ts, damage_amount=int(final_damage), source_kind=str(source_kind or ""), source_meta=(source_meta if isinstance(source_meta, dict) else None))
                    death_after = str(getattr(target_ts, "death_state", death_after) or death_after)
                    hp_after = int(getattr(target_ts, "hp", hp_after) or hp_after)
                elif hp_after <= 0 and overflow >= int(max_before):
                    target_ts.hp = 0
                    target_ts.death_state = "dead"
                    target_ts.death_save_successes = 0
                    target_ts.death_save_failures = 3
                    self._set_dead_visual_if_needed(token_id)
                    self._set_hud_status(f"{getattr(target_ts, 'display_name', token_id)} dies instantly.", hold_sec=4.0)
                    self._sync_death_state_to_sheet(target_ts)
                    self._sync_token_statuses_to_sheet(target_ts)
                    death_after = "dead"
                    hp_after = 0
                elif hp_after <= 0 and bool(getattr(target_ts, "rage_active", False)):
                    try:
                        barb_level = 0
                        if hasattr(self, "server") and self.server is not None:
                            sheet = self.server.get_character_sheet(str(getattr(target_ts, "character_id", "") or "")) or {}
                            cls = sheet.get("class_levels") if isinstance(sheet.get("class_levels"), dict) else {}
                            barb_level = int((cls.get("barbarian", 0) or 0))
                            if not barb_level:
                                meta = sheet.get("meta") if isinstance(sheet.get("meta"), dict) else {}
                                if str(meta.get("class", "") or "").strip().lower() == "barbarian":
                                    barb_level = int(meta.get("level", 0) or 0)
                        if barb_level >= 11:
                            rr_dc = 10 + int(getattr(target_ts, "relentless_rage_uses", 0) or 0) * 5
                            if self._register_relentless_rage_request(target_ts, dc=rr_dc, damage_amount=int(final_damage), source_kind=str(source_kind or "attack")):
                                target_ts.death_state = "down"
                                death_after = "down"
                                self._set_hud_status(f"{getattr(target_ts, 'display_name', token_id)} must roll Relentless Rage (DC {rr_dc}).", hold_sec=4.0)
                    except Exception:
                        pass

            try:
                src_meta = source_meta if isinstance(source_meta, dict) else {}
                attacker_for_bless = self.state.tokens.get(str(src_meta.get("attacker_token_id") or src_meta.get("source_token_id") or ""))
                if attacker_for_bless is not None and attacker_for_bless is not target_ts:
                    self._grant_dark_ones_blessing_if_needed(attacker_for_bless, target_ts)
            except Exception:
                pass

            extra_bits = []
            if dtype:
                extra_bits.append(dtype)
            if dmg_steps:
                extra_bits.append(",".join(dmg_steps))
            extra_txt = f" [{' | '.join(extra_bits)}]" if extra_bits else ""
            print(f"[DMG] {getattr(target_ts, 'display_name', token_id)} {hp_before}/{max_before} -> {hp_after}/{max_after} (amt={final_damage} base={base_damage}){extra_txt}")

            try:
                src = str(source_kind or "attack")
                pid = str(pending_attack_id or "")
                if pid.startswith("hazard:"):
                    src = "hazard"
                elif pid.startswith("status:"):
                    src = "status"
                elif pid.startswith("cloud:"):
                    src = "cloud"
                elif pid.startswith("fall:"):
                    src = "fall"
                self.campaign_logger.event(
                    "DAMAGE_APPLIED",
                    token_id=str(token_id),
                    token_name=getattr(target_ts, "display_name", token_id),
                    amount=int(final_damage),
                    base_damage=int(base_damage),
                    final_damage=int(final_damage),
                    damage_type=str(dtype),
                    damage_steps=list(dmg_steps),
                    hp_before=int(hp_before),
                    hp_after=int(hp_after),
                    max_hp_after=int(max_after),
                    death_state_before=str(death_before),
                    death_state_after=str(death_after),
                    pending_attack_id=str(pid),
                    source_kind=str(src),
                )
            except Exception:
                pass

            try:
                self._history_append_and_apply({
                    "type": "APPLY_DAMAGE",
                    "token_id": token_id,
                    "amount": int(amount),
                    "hp_before": hp_before,
                    "max_hp_before": max_before,
                    "death_state_before": death_before,
                    "hp_after": hp_after,
                    "max_hp_after": max_after,
                    "death_state_after": death_after,
                    "pending_attack_id": str(pending_attack_id or ""),
                    "encounter_id": str(encounter_id or getattr(self, "encounter_id", "") or ""),
                })
            except Exception as e:
                print(f"[HISTORY] Failed to append APPLY_DAMAGE event: {e}")

            try:
                self.apply_state_hp_to_scene_token(token_id)
            except Exception:
                pass

            try:
                self.refresh_player_view()
            except Exception:
                pass

    def _apply_death_rules_after_damage(self, ts: TokenState) -> None:
            """
            Enforces consistent post-damage death state transitions.

            Standardized states:
            alive | down | stable | dead
            """
            # If already dead, keep dead
            if getattr(ts, "death_state", "alive") == "dead":
                ts.hp = 0
                return

            # Only enforce at/below 0 HP
            if int(getattr(ts, "hp", 0)) > 0:
                # If a token got healed above 0, reset death save counters and mark alive.
                ts.death_state = "alive"
                ts.death_save_successes = 0
                ts.death_save_failures = 0
                return

            # Clamp at 0
            ts.hp = 0

            # PCs: down at 0 HP (death saves later)
            if self._is_pc(ts):
                # If they were stable and get hit again to 0, they go back to down
                if getattr(ts, "death_state", "alive") != "dead":
                    ts.death_state = "down"
                return

            # NPCs: dead at 0 HP
            ts.death_state = "dead"
            self._set_dead_visual_if_needed(ts.token_id)
            return

    def get_selected_tokenstate(self):
        sel = self.scene.selectedItems()
        if not sel:
            return None
        item = sel[0]
        if not isinstance(item, DraggableToken):
            return None
        return self.state.tokens.get(item.token_id)

    def _get_dead_sprite_path_for_token(self, ts: TokenState) -> str:
        # 1) per-token override stored in state
        rel = (getattr(ts, "dead_image_relpath", "") or "").strip()
        if rel:
            p = os.path.join(self.campaign_path, rel)
            if os.path.exists(p):
                return p

        # 2) fallback global default
        p = os.path.join(self.campaign_path, "assets", "tokens", "dead.png")
        return p if os.path.exists(p) else ""
    
    def _clear_pending_local_and_server(self):
        p = getattr(self.state, "pending_attack", None) or {}
        pid = p.get("pending_attack_id", "")
        if pid:
            self.server.cancel_pending_attack( pid)
        self.state.pending_attack = None

    def handle_damage_payload(self, payload: dict):
        """
        Option B step 2: Handle player damage roll response.

        Supports:
        - Single-target weapon damage via self._awaiting_damage
        - Multi-target AoE damage via self._awaiting_aoe_damage
        """

        # Decide which awaiting bucket matches this payload
        attack_id = (payload.get("attack_id", "") or "").strip()
        payload_player = (payload.get("player_id", "") or "").strip()

        aw = getattr(self, "_awaiting_damage", None)
        awo = getattr(self, "_awaiting_aoe_damage", None)

        def _matches(awdict):
            if not awdict:
                return False
            expected_id = (awdict.get("attack_id", "") or "").strip()
            if not attack_id or attack_id != expected_id:
                return False
            expected_player = (awdict.get("player_id", "") or "").strip()
            if payload_player and expected_player and payload_player != expected_player:
                return False
            return True

        mode = None
        if _matches(aw):
            mode = "single"
        elif _matches(awo):
            mode = "aoe"
            aw = awo  # unify variable name for parsing
        else:
            print("[DMG] Damage received but no matching awaiting state. Ignored.")
            return

        expected_id = (aw.get("attack_id", "") or "").strip()
        expected_player = (aw.get("player_id", "") or "").strip()

        # Extract damage
        dmg = None
        breakdown = ""

        dr = payload.get("damage_roll")
        if isinstance(dr, dict):
            try:
                dmg_total = dr.get("total", None)
                dr_expr = str(dr.get("expr", "") or "")
                if isinstance(dmg_total, (int, float)):
                    dmg = int(dmg_total)
                    breakdown = f"player_rolled expr={dr_expr} dice={dr.get('dice', [])} mod={dr.get('modifier', 0)}"
            except Exception:
                dmg = None

        if dmg is None:
            try:
                dmg = int(payload.get("damage_total", 0))
                breakdown = "player_rolled_total"
            except Exception:
                dmg = None

        if dmg is None:
            print("[DMG] Could not parse damage from payload. Ignored.")
            return

        # Barbarian rage damage bonus for melee weapon attacks is applied by the engine here.
        try:
            if mode == "single":
                attacker_id = (aw.get("attacker_token_id", "") or "").strip()
                attacker_ts = self.state.tokens.get(attacker_id) if attacker_id else None
                source_meta = aw.get("source_meta", {}) if isinstance(aw.get("source_meta"), dict) else {}
                weapon_type = str(source_meta.get("weapon_type", "") or "").strip().lower()
                tags = [str(t).strip().lower() for t in (source_meta.get("tags") or []) if str(t).strip()]
                is_melee = (weapon_type == "melee") or ("melee" in tags) or (str(aw.get("weapon_ref", "") or "") == "unarmed")
                if attacker_ts:
                    dmg, breakdown = self._maybe_apply_savage_attacker(attacker_ts, str(aw.get("damage_expr", "") or payload.get("damage_expr", "") or ""), int(dmg), str(breakdown), crit=bool(aw.get("crit", False)), is_melee=bool(is_melee), source_kind="attack")
                feat_damage_bonus = int(source_meta.get("feat_damage_bonus", 0) or 0)
                if feat_damage_bonus:
                    dmg += feat_damage_bonus
                    breakdown = (breakdown + (" | " if breakdown else "") + f"feat_bonus_damage={feat_damage_bonus}").strip()
                if attacker_ts and bool(getattr(attacker_ts, "rage_active", False)) and is_melee:
                    dmg += int(getattr(attacker_ts, "rage_damage_bonus", 0) or 0)
                if attacker_ts and is_melee:
                    ids_dice = int(getattr(attacker_ts, "improved_divine_smite_dice", 0) or 0)
                    if ids_dice > 0:
                        ids_roll_count = ids_dice * (2 if bool(aw.get("crit", False)) else 1)
                        ids_bonus, ids_breakdown = self._roll_brutal_critical_bonus("1d8", ids_roll_count)
                        if ids_bonus > 0:
                            dmg += ids_bonus
                            breakdown = (breakdown + (" | " if breakdown else "") + f"improved_divine_smite={ids_bonus}").strip()
                if attacker_ts and bool(aw.get("crit", False)) and is_melee:
                    brutal_bonus, brutal_breakdown = self._roll_brutal_critical_bonus(
                        str((aw.get("damage_expr", "") or payload.get("damage_expr", "") or "")),
                        int(getattr(attacker_ts, "brutal_critical_dice", 0) or 0),
                    )
                    if brutal_bonus > 0:
                        dmg += brutal_bonus
                        breakdown = (breakdown + (" | " if breakdown else "") + brutal_breakdown).strip()
                if attacker_ts and self._rogue_can_apply_sneak_attack(attacker_ts, target, aw):
                    sneak_bonus, sneak_breakdown = self._roll_sneak_attack_bonus(
                        int(getattr(attacker_ts, "sneak_attack_dice", 0) or 0),
                        bool(aw.get("crit", False)),
                    )
                    if sneak_bonus > 0:
                        dmg += sneak_bonus
                        attacker_ts.last_sneak_attack_turn_marker = self._current_turn_marker()
                        breakdown = (breakdown + (" | " if breakdown else "") + sneak_breakdown).strip()
                if attacker_ts and is_melee and bool(getattr(attacker_ts, "stunning_strike_armed", False)):
                    try:
                        attacker_ts.stunning_strike_armed = False
                        dc = 8 + int(getattr(attacker_ts, "proficiency_bonus", 2) or 2) + ((int(getattr(attacker_ts, "wis", 10) or 10) - 10) // 2)
                        if self._is_pc(target):
                            self._register_pc_deferred_damage_save_request(
                                target,
                                ability="CON",
                                dc=int(dc),
                                mode="normal",
                                label="Stunning Strike Save",
                                context={"kind": "stunning_strike", "source_kind": "monk_stunning_strike"},
                                deferred_effect={"apply_condition_on_fail": {"name": "stunned", "rounds_remaining": 2, "source": "Stunning Strike"}},
                            )
                        else:
                            self._resolve_npc_save_request(target, {
                                "ability": "CON",
                                "dc": int(dc),
                                "adv_mode": "normal",
                                "label": "Stunning Strike Save",
                                "deferred_effect": {"apply_condition_on_fail": {"name": "stunned", "rounds_remaining": 2, "source": "Stunning Strike"}},
                            })
                        try:
                            self._sync_token_statuses_to_sheet(attacker_ts)
                            self._sync_combat_flags_to_sheet(attacker_ts, {"stunning_strike_armed": False})
                        except Exception:
                            pass
                    except Exception:
                        pass
        except Exception:
            pass

        # Clear awaiting first (prevents double-apply if same payload arrives twice)
        if mode == "single":
            self._awaiting_damage = None
        else:
            self._awaiting_aoe_damage = None

        if mode == "single":
            target_id = (aw.get("target_token_id", "") or "").strip()
            target = self.state.tokens.get(target_id)
            if not target:
                print("[DMG] Target missing in state.")
                self.campaign_logger.combat("damage_aborted_missing_target", attack_id=expected_id, target_token_id=target_id)
                self.update_combat_hud()
                return

            self.apply_damage_to_token(
                target,
                dmg,
                encounter_id=str(aw.get("encounter_id", "") or ""),
                pending_attack_id=expected_id,
                damage_type=str(aw.get("damage_type", "") or ""),
                source_kind="attack",
                source_meta=(dict(aw.get("source_meta", {}) if isinstance(aw.get("source_meta", {}), dict) else {}) | {"crit": bool(aw.get("crit", False)), "nat20": bool(aw.get("nat20", False)), "attacker_token_id": str(aw.get("attacker_token_id", "") or ""), "attacker_name": str(aw.get("attacker_name", "") or "")}),
            )
            if str(aw.get("reaction_kind", "") or "") == "opportunity_attack" and attacker_ts is not None:
                self._apply_sentinel_stop(attacker_ts, target)

            # Always post a final attack result back to the player portal.
            # (This makes the portal Activity feed reflect the completed rotation.)
            try:
                result_payload = {
                    "attack_id": expected_id,
                    "player_id": expected_player,
                    "encounter_id": str(aw.get("encounter_id", "") or ""),

                    "attacker_token_id": str(aw.get("attacker_token_id", "") or ""),
                    "attacker_name": str(aw.get("attacker_name", "") or ""),
                    "target_token_id": target.token_id,
                    "target_name": target.display_name,

                    "roll": int(aw.get("roll", 0) or 0),
                    "total": int(aw.get("total", 0) or 0),
                    "ac": int(aw.get("ac", 0) or 0),
                    "cover": str(aw.get("cover", "") or ""),
                    "cover_bonus": int(aw.get("cover_bonus", 0) or 0),
                    "result": "HIT",
                    "nat20": bool(aw.get("nat20", False)),
                    "nat1": bool(aw.get("nat1", False)),

                    "damage": int(dmg),
                    "target_hp": int(getattr(target, "hp", 0)),
                    "target_max_hp": int(getattr(target, "max_hp", 0)),
                    "ttl_seconds": 120,
                }
                if expected_player:
                    self.server.post_attack_result(result_payload)
                    # Also post a concise message for the Activity feed.
                    self.server.post_message(
                        expected_player,
                        f"Attack resolved: HIT for {int(dmg)} damage.",
                        "ok",
                        ttl_seconds=12,
                        data={"pending_attack_id": expected_id, "result": "HIT", "damage": int(dmg)},
                    )
            except Exception:
                pass

            self.campaign_logger.combat(
                "awaiting_damage_resolved",
                attack_id=expected_id,
                player_id=expected_player,
                target_token_id=target.token_id,
                damage=int(dmg),
                damage_breakdown=str(breakdown),
                target_hp=int(getattr(target, "hp", 0)),
                target_max_hp=int(getattr(target, "max_hp", 0)),
            )

            self._set_hud_status(f"Damage applied ({dmg}).", hold_sec=3.0)
            self.update_combat_hud()
            self.refresh_player_view()
            return

        # AoE mode
        target_ids = list(aw.get("target_token_ids", []) or [])
        applied = 0
        for tid in target_ids:
            ts = self.state.tokens.get(tid)
            if not ts:
                continue
            self.apply_damage_to_token(
                ts,
                dmg,
                encounter_id=str(aw.get("encounter_id", "") or ""),
                pending_attack_id=expected_id,
                damage_type=str(aw.get("damage_type", "") or ""),
                source_kind="attack",
                source_meta=(dict(aw.get("source_meta", {}) if isinstance(aw.get("source_meta", {}), dict) else {}) | {"crit": bool(aw.get("crit", False)), "nat20": bool(aw.get("nat20", False)), "attacker_token_id": str(aw.get("attacker_token_id", "") or ""), "attacker_name": str(aw.get("attacker_name", "") or "")}),
            )
            applied += 1

        self.campaign_logger.combat(
            "aoe_damage_resolved",
            attack_id=expected_id,
            player_id=expected_player,
            caster_token_id=str(aw.get("caster_token_id", "") or ""),
            spell_id=str(aw.get("spell_id", "") or ""),
            spell_name=str(aw.get("spell_name", "") or ""),
            damage=int(dmg),
            damage_breakdown=str(breakdown),
            targets=int(applied),
        )

        self._set_hud_status(f"AoE damage applied ({dmg}) to {applied} targets.", hold_sec=3.0)
        self.update_combat_hud()
        self.refresh_player_view()

    def hud_cancel_awaiting_damage(self):
        aw = getattr(self, "_awaiting_damage", None)
        if not aw:
            self._set_hud_status("No awaiting damage to cancel.", hold_sec=2.5)
            self.update_combat_hud()
            return

        attack_id = (aw.get("attack_id", "") or "").strip()
        player_id = (aw.get("player_id", "") or "").strip()

        # Clear local awaiting state
        self._awaiting_damage = None

        # Notify player portal (optional but very useful UX)
        if player_id:
            self.server.post_message(player_id,
                f"Damage request cancelled by DM (attack_id={attack_id}).",
                "warn",
                ttl_seconds=15,
                data={"type": "DAMAGE_CANCELLED", "attack_id": attack_id}
            )

        self.campaign_logger.combat("awaiting_damage_cancelled", attack_id=attack_id, player_id=player_id)
        self._set_hud_status("Awaiting damage cancelled.", hold_sec=3.0)
        self.update_combat_hud()
    
    # =========================================================
    # Phase 3 — Initiative + Turn System (DM authoritative)
    # =========================================================

    def hud_revert_illegal_move(self):
        """Revert the selected token to its last legal grid cell (DM-only)."""
        tok = None

        # Prefer a selected token
        try:
            sel = [it for it in self.scene.selectedItems() if getattr(it, "token_id", None)]
            tok = sel[0] if sel else None
        except Exception:
            tok = None

        # Fallback: focus item
        if tok is None:
            try:
                tok = self.scene.focusItem()
            except Exception:
                tok = None

        if tok is None:
            self._set_hud_status("No token selected.", hold_sec=2.0)
            self.update_combat_hud()
            return

        token_id = str(getattr(tok, "token_id", "") or "")
        ts = self.state.tokens.get(token_id)
        if ts is None:
            self._set_hud_status("Selected token not in state.", hold_sec=2.0)
            self.update_combat_hud()
            return

        if not bool(getattr(ts, "illegal_position", False)):
            self._set_hud_status("Token is not marked illegal.", hold_sec=2.0)
            self.update_combat_hud()
            return

        lx = int(getattr(ts, "last_legal_gx", getattr(ts, "grid_x", 0)) or 0)
        ly = int(getattr(ts, "last_legal_gy", getattr(ts, "grid_y", 0)) or 0)

        # Reposition graphics without triggering move callback
        try:
            tok._suppress_move_callback = True
            tok.setPos(lx * self.grid_size, ly * self.grid_size)
        finally:
            try:
                tok._suppress_move_callback = False
            except Exception:
                pass

        # Commit state
        fx = int(getattr(ts, "grid_x", lx) or lx)
        fy = int(getattr(ts, "grid_y", ly) or ly)
        ts.grid_x = lx
        ts.grid_y = ly
        try:
            ts.illegal_position = False
            ts.illegal_reason = ""
        except Exception:
            pass

        # History event (optional; safe if history system exists)
        try:
            self._history_append_and_apply({
                "type": "SET_POSITION",
                "token_id": str(token_id),
                "from_gx": int(fx),
                "from_gy": int(fy),
                "to_gx": int(lx),
                "to_gy": int(ly),
                "cost_sq": 0,
                "cost_ft": 0,
                "revert_illegal": True,
            })
        except Exception:
            pass

        self._set_hud_status("Reverted illegal move.", hold_sec=2.0)
        try:
            self.refresh_player_view()
        except Exception:
            pass
        self.update_combat_hud()

    def _initiative_active(self) -> bool:
        return bool(getattr(self.state, "initiative_active", False))

    def _active_token_id(self):
        return getattr(self.state, "active_token_id", None)

    def _selected_token_ids(self) -> list[str]:
        ids = []
        try:
            for it in self.scene.selectedItems():
                tid = getattr(it, "token_id", None)
                if tid:
                    ids.append(tid)
        except Exception:
            pass
        return ids

    def _tokens_are_hostile(self, a, b) -> bool:
        a_side = str(getattr(a, "side", "") or "").strip().lower()
        b_side = str(getattr(b, "side", "") or "").strip().lower()
        friendly = {"player", "ally"}
        if a_side in friendly and b_side in friendly:
            return False
        if a_side == "enemy" and b_side == "enemy":
            return False
        if not a_side or not b_side:
            return False
        if a_side == "neutral" or b_side == "neutral":
            return False
        return a_side != b_side

    def _token_can_take_reaction(self, ts) -> bool:
        if ts is None:
            return False
        if str(getattr(ts, "death_state", "alive") or "alive").strip().lower() in {"down", "stable", "dead"}:
            return False
        if not bool(getattr(ts, "reaction_available", True)):
            return False
        try:
            active_condition_names = self._active_condition_names(ts)
            names = active_condition_names
        except Exception:
            names = set()
        if "stunned" in names:
            return False
        return True

    def _consume_reaction(self, ts, *, reason: str = "reaction") -> None:
        if ts is None:
            return
        try:
            ts.reaction_available = False
        except Exception:
            pass
        try:
            self.campaign_logger.combat(
                "reaction_spent",
                token_id=str(getattr(ts, "token_id", "") or ""),
                name=str(getattr(ts, "display_name", "") or ""),
                reason=str(reason or "reaction"),
            )
        except Exception:
            pass
    
    def _active_condition_names(self, ts) -> set[str]:
        names = set()
        if ts is None:
            return names

        raw = getattr(ts, "statuses", None)
        if isinstance(raw, dict):
            iterable = raw.values()
        elif isinstance(raw, (list, tuple, set)):
            iterable = raw
        else:
            iterable = []

        for item in iterable:
            if isinstance(item, str):
                name = item.strip().lower()
                if name:
                    names.add(name)
                continue
            if isinstance(item, dict):
                name = str(item.get("name", "") or item.get("condition_name", "")).strip().lower()
                if name:
                    names.add(name)
                continue
            name = str(getattr(item, "name", "") or getattr(item, "condition_name", "")).strip().lower()
            if name:
                names.add(name)

        return names

    def _melee_reach_sq_for_weapon(self, weapon_data: dict | None) -> int:
        wd = weapon_data if isinstance(weapon_data, dict) else {}
        try:
            rng = int(wd.get("range_ft", 0) or 0)
        except Exception:
            rng = 0
        if rng <= 0:
            rng = 5
        return max(1, int((rng + 4) // 5))

    def _weapon_supports_opportunity_attack(self, weapon_data: dict | None) -> bool:
        wd = weapon_data if isinstance(weapon_data, dict) else {}
        weapon_type = str(wd.get("weapon_type", wd.get("type", "")) or "").strip().lower()
        tags = {str(t).strip().lower() for t in (wd.get("tags") or []) if str(t).strip()}
        if weapon_type == "ranged" or "ranged" in tags:
            return False
        return True

    def _token_melee_weapon_data(self, ts):
        if ts is None:
            return None, ""
        weapon_ref = str(getattr(ts, "weapon_id", "") or getattr(ts, "weapon", "") or "").strip() or "unarmed"
        wd = self.get_weapon_data(weapon_ref) or self.get_weapon_data("unarmed") or {}
        if not self._weapon_supports_opportunity_attack(wd):
            wd = self.get_weapon_data("unarmed") or {}
            weapon_ref = str((wd or {}).get("item_id") or "unarmed")
        return wd, weapon_ref

    def _token_in_weapon_reach(self, reactor_ts, target_gx: int, target_gy: int, weapon_data: dict | None = None) -> bool:
        if reactor_ts is None:
            return False
        reach_sq = self._melee_reach_sq_for_weapon(weapon_data)
        dx = abs(int(getattr(reactor_ts, "grid_x", 0) or 0) - int(target_gx))
        dy = abs(int(getattr(reactor_ts, "grid_y", 0) or 0) - int(target_gy))
        return max(dx, dy) <= int(reach_sq)

    def _eligible_opportunity_reactors(self, mover_ts, *, from_gx: int, from_gy: int, to_gx: int, to_gy: int):
        out = []
        mover_id = str(getattr(mover_ts, "token_id", "") or "")
        for rid, rts in (getattr(self.state, "tokens", {}) or {}).items():
            if str(rid) == mover_id:
                continue
            if not self._tokens_are_hostile(rts, mover_ts):
                continue
            if not self._token_can_take_reaction(rts):
                continue
            wd, weapon_ref = self._token_melee_weapon_data(rts)
            if not wd or not self._weapon_supports_opportunity_attack(wd):
                continue
            in_before = self._token_in_weapon_reach(rts, int(from_gx), int(from_gy), wd)
            in_after = self._token_in_weapon_reach(rts, int(to_gx), int(to_gy), wd)
            if in_before and not in_after:
                out.append((rts, wd, weapon_ref))
        out.sort(key=lambda tup: (0 if self._is_player_controlled_token(tup[0]) else 1, str(getattr(tup[0], "token_id", "") or "")))
        return out

    def _arm_pc_opportunity_attack(self, reactor_ts, target_ts, weapon_data: dict, weapon_ref: str) -> bool:
        if reactor_ts is None or target_ts is None:
            return False
        if getattr(self.state, "pending_attack", None) or getattr(self, "_awaiting_damage", None):
            return False
        player_id = str(getattr(reactor_ts, "player_id", "") or "").strip()
        character_id = str(getattr(reactor_ts, "character_id", "") or "").strip()
        if not player_id:
            return False
        damage_expr = str((weapon_data or {}).get("damage", "1") or "1")
        pending_attack_id = uuid.uuid4().hex
        payload = {
            "pending_attack_id": pending_attack_id,
            "encounter_id": str(getattr(self, "encounter_id", "") or ""),
            "attacker_token_id": str(getattr(reactor_ts, "token_id", "") or ""),
            "attacker_name": str(getattr(reactor_ts, "display_name", "") or ""),
            "attacker_character_id": character_id,
            "player_id": player_id,
            "target_token_id": str(getattr(target_ts, "token_id", "") or ""),
            "target_name": str(getattr(target_ts, "display_name", "") or ""),
            "target_character_id": str(getattr(target_ts, "character_id", "") or ""),
            "weapon_ref": str(weapon_ref or (weapon_data or {}).get("item_id") or "unarmed"),
            "weapon_name": str((weapon_data or {}).get("name", weapon_ref) or weapon_ref),
            "damage_expr": damage_expr,
            "attack_kind": "weapon",
            "expires_in_sec": 45,
            "is_reaction": True,
            "reaction_kind": "opportunity_attack",
        }
        if not self.server.register_pending_attack(payload):
            return False
        self.state.pending_attack = payload
        self._consume_reaction(reactor_ts, reason="opportunity_attack")
        try:
            self.server.post_message(
                player_id,
                f"Reaction available: Opportunity Attack vs {getattr(target_ts, 'display_name', 'target')}.",
                "warn",
                ttl_seconds=45,
                data={"type": "REACTION_OPPORTUNITY_ATTACK", "pending_attack_id": pending_attack_id},
            )
        except Exception:
            pass
        try:
            print(f"[REACTION_REQUEST] OA {getattr(reactor_ts,'display_name','')} -> {getattr(target_ts,'display_name','')} request_id={pending_attack_id}")
        except Exception:
            pass
        try:
            self.campaign_logger.combat(
                "reaction_requested",
                reaction_kind="opportunity_attack",
                request_id=str(pending_attack_id),
                reactor_token_id=str(getattr(reactor_ts, "token_id", "") or ""),
                reactor_name=str(getattr(reactor_ts, "display_name", "") or ""),
                target_token_id=str(getattr(target_ts, "token_id", "") or ""),
                target_name=str(getattr(target_ts, "display_name", "") or ""),
                player_id=str(player_id),
            )
        except Exception:
            pass
        return True

    def _resolve_npc_opportunity_attack(self, reactor_ts, target_ts, weapon_data: dict, weapon_ref: str) -> None:
        if reactor_ts is None or target_ts is None:
            return
        mode = "normal"
        try:
            cond = attack_mode_from_conditions(reactor_ts, target_ts, weapon_data, attack_kind="weapon") or {}
            mode = str(cond.get("mode", "normal") or "normal")
        except Exception:
            mode = "normal"
        if mode == "advantage":
            rolls = [random.randint(1, 20), random.randint(1, 20)]
            chosen = choose_d20(rolls, "adv")
        elif mode == "disadvantage":
            rolls = [random.randint(1, 20), random.randint(1, 20)]
            chosen = choose_d20(rolls, "dis")
        else:
            rolls = [random.randint(1, 20)]
            chosen = choose_d20(rolls, "normal")
        try:
            bonus = int((weapon_data or {}).get("attack_bonus", 0) or 0)
        except Exception:
            bonus = 0
        hit, total_attack, is_nat20, is_nat1 = resolve_attack(
            d20=int(chosen),
            attacker_mod=int(getattr(reactor_ts, "attack_modifier", 0) or 0),
            target_ac=int(getattr(target_ts, "ac", 10) or 10),
            weapon_attack_bonus=int(bonus),
        )
        self._consume_reaction(reactor_ts, reason="opportunity_attack")
        try:
            print(f"[REACTION] OA {getattr(reactor_ts,'display_name','')} -> {getattr(target_ts,'display_name','')}: d20={int(chosen)} mode={mode} total={int(total_attack)} vs AC={int(getattr(target_ts,'ac',10) or 10)} => {'HIT' if hit else 'MISS'}")
        except Exception:
            pass
        try:
            self.campaign_logger.combat(
                "reaction_resolved",
                reaction_kind="opportunity_attack",
                reactor_token_id=str(getattr(reactor_ts, "token_id", "") or ""),
                reactor_name=str(getattr(reactor_ts, "display_name", "") or ""),
                target_token_id=str(getattr(target_ts, "token_id", "") or ""),
                target_name=str(getattr(target_ts, "display_name", "") or ""),
                result=("HIT" if hit else "MISS"),
                d20=int(chosen),
                mode=str(mode),
                total_attack=int(total_attack),
                target_ac=int(getattr(target_ts, "ac", 10) or 10),
            )
        except Exception:
            pass
        if not hit:
            return
        damage_expr = str((weapon_data or {}).get("damage", "1") or "1")
        if is_nat20:
            dmg, breakdown = roll_damage_crit(damage_expr)
        else:
            dmg, breakdown = roll_damage(damage_expr)
        dmg, breakdown = self._maybe_apply_savage_attacker(reactor_ts, damage_expr, int(dmg), str(breakdown), crit=bool(is_nat20), is_melee=True, source_kind="reaction")
        try:
            self.apply_damage_to_token(
                target_ts,
                int(dmg),
                pending_attack_id=f"reaction:{str(getattr(reactor_ts, 'token_id', '') or '')}:{str(getattr(target_ts, 'token_id', '') or '')}",
                damage_type=str((weapon_data or {}).get("damage_type", "") or ""),
                source_kind="reaction",
                source_meta={
                    "damage_type": str((weapon_data or {}).get("damage_type", "") or ""),
                    "tags": list((weapon_data or {}).get("tags", []) or []),
                    "weapon": dict(weapon_data or {}),
                    "reaction_kind": "opportunity_attack",
                },
            )
        except Exception:
            pass
        self._apply_sentinel_stop(reactor_ts, target_ts)
        try:
            self.campaign_logger.event(
                "REACTION_DAMAGE",
                reaction_kind="opportunity_attack",
                reactor_token_id=str(getattr(reactor_ts, "token_id", "") or ""),
                reactor_name=str(getattr(reactor_ts, "display_name", "") or ""),
                target_token_id=str(getattr(target_ts, "token_id", "") or ""),
                target_name=str(getattr(target_ts, "display_name", "") or ""),
                damage_expr=str(damage_expr),
                damage=int(dmg),
                breakdown=str(breakdown),
                damage_type=str((weapon_data or {}).get("damage_type", "") or ""),
            )
        except Exception:
            pass

    def _handle_opportunity_attacks_for_move(self, mover_ts, *, from_gx: int, from_gy: int, to_gx: int, to_gy: int) -> None:
        if mover_ts is None:
            return
        reactors = self._eligible_opportunity_reactors(mover_ts, from_gx=int(from_gx), from_gy=int(from_gy), to_gx=int(to_gx), to_gy=int(to_gy))
        if not reactors:
            return
        player_prompted = False
        for reactor_ts, weapon_data, weapon_ref in reactors:
            if self._is_player_controlled_token(reactor_ts):
                if player_prompted:
                    continue
                player_prompted = self._arm_pc_opportunity_attack(reactor_ts, mover_ts, weapon_data, weapon_ref)
            else:
                self._resolve_npc_opportunity_attack(reactor_ts, mover_ts, weapon_data, weapon_ref)

    def _set_active_turn(self, token_id: str) -> None:
        """
        DM-authoritative turn transition.

        Phase 5.1 foundation:
          - emit TURN_END for previous active token (semantic hook)
          - emit TURN_START for new active token (semantic hook + deterministic replay fields)
          - optionally capture a checkpoint at TURN_START (fast rewind)
        """

        prev_active = getattr(self.state, "active_token_id", None)
        new_active = token_id or None

        # --- TURN_END event (previous token) ---
        try:
            prev_active = getattr(self.state, "active_token_id", None)
            if prev_active and prev_active != new_active:
                self._history_append_and_apply({
                    "type": "TURN_END",
                    "active_token_id": prev_active,
                    "round_number": int(getattr(self.state, "round_number", 1) or 1),
                    "current_turn_index": int(getattr(self.state, "current_turn_index", 0) or 0),
                })
        except Exception:
            pass

        # Clear one-turn barbarian reckless attack state at the start of that actor's next turn.
        try:
            if new_active:
                new_ts = self.state.tokens.get(str(new_active))
                if new_ts and bool(getattr(new_ts, "reckless_attack_active", False)):
                    setattr(new_ts, "reckless_attack_active", False)
                    if getattr(new_ts, "character_id", "") and getattr(self, "server_client", None):
                        sheet = self.server_client.get_character_sheet(getattr(new_ts, "character_id", ""))
                        if isinstance(sheet, dict) and sheet:
                            combat_sheet = sheet.setdefault("combat", {}) if isinstance(sheet.get("combat"), dict) else {}
                            combat_sheet["reckless_attack_active"] = False
                            for mk in ("patient_defense_active", "flurry_of_blows_active", "step_of_the_wind_mode"):
                                combat_sheet[mk] = False if mk != "step_of_the_wind_mode" else ""
                            try:
                                self.server_client.upsert_character_sheet(getattr(new_ts, "character_id", ""), sheet)
                            except Exception:
                                pass
                if new_ts:
                    for mk, mv in (("patient_defense_active", False), ("flurry_of_blows_active", False), ("step_of_the_wind_mode", "")):
                        setattr(new_ts, mk, mv)
        except Exception:
            pass

        # BX1: hazards at turn end (previous active token)
        try:
            if prev_active and prev_active != new_active:
                self._apply_hazards_for_token(str(prev_active), trigger="turn_end")
        except Exception:
            pass


        # BX5.5: damaging runtime clouds at turn end (previous active token)
        try:
            if prev_active and prev_active != new_active:
                self._apply_runtime_cloud_damage_for_token(str(prev_active), trigger="turn_end")
        except Exception:
            pass

        # BX5.2: tick runtime fog zones (duration in turns)
        try:
            zones = list(getattr(self.state, "runtime_fog_zones", []) or [])
            print(f"[CLOUD] Tick start -> total={len(zones)}")

            if zones:
                kept = []
                for z in zones:
                    if not isinstance(z, dict):
                        continue

                    kind = str(z.get("kind", "cloud"))
                    cx = z.get("cx")
                    cy = z.get("cy")
                    ttl = z.get("ttl_turns", None)

                    print(f"[CLOUD] Before tick -> {kind} @({cx},{cy}) ttl={ttl}")

                    if ttl is None:
                        kept.append(z)
                        print(f"[CLOUD] No TTL (permanent cloud) kept")
                        continue

                    try:
                        ttl_i = int(ttl)
                    except Exception:
                        ttl_i = 0

                    ttl_i -= 1

                    if ttl_i > 0:
                        z["ttl_turns"] = ttl_i
                        kept.append(z)
                        print(f"[CLOUD] After tick -> {kind} @({cx},{cy}) ttl={ttl_i}")
                    else:
                        print(f"[CLOUD] Expired {kind} @({cx},{cy})")
                        try:
                            self.campaign_logger.event(
                                "CLOUD_EXPIRE",
                                kind=kind,
                                cx=int(cx) if cx is not None else None,
                                cy=int(cy) if cy is not None else None,
                            )
                        except Exception:
                            pass

                self.state.runtime_fog_zones = kept
                print(f"[CLOUD] Tick end -> remaining={len(kept)}")
                try:
                    self._redraw_runtime_cloud_overlays()
                except Exception:
                    pass
        except Exception as e:
            print(f"[CLOUD] Tick error: {e}")

        # --- Core state switch ---
        self.state.active_token_id = new_active

        # Clear any DM overlays from previous active/selection before switching turn visuals
        try:
            for tok in list(getattr(self, "token_items", [])):
                tok.hide_movement_range(self.scene)
                tok.hide_attack_range(self.scene)
        except Exception:
            pass

        # Reset per-turn flags
        for _tid, _ts in self.state.tokens.items():
            _ts.has_acted_this_turn = False
        if new_active and new_active in self.state.tokens:
            self.state.tokens[new_active].has_acted_this_turn = False

        # Reset movement for NEW active token at start of its turn
        if new_active and new_active in self.state.tokens:
            ts = self.state.tokens[new_active]
            base = int(getattr(ts, "base_movement", getattr(ts, "movement", 0)) or 0)
            ts.base_movement = base
            ts.movement_remaining = self._effective_speed_for_token(ts)
            ts.reaction_available = True
            try:
                self.campaign_logger.combat(
                    "reaction_refreshed",
                    token_id=str(new_active),
                    name=str(getattr(ts, "display_name", new_active) or new_active),
                )
            except Exception:
                pass

        # Do NOT auto-show movement overlay on turn change.
        # Overlays are selection-driven only.
        try:
            if getattr(self, "selected_token_id", None) and self.selected_token_id == new_active:
                self._refresh_selected_token_overlays()  # if you have one; see below
        except Exception:
            pass

        # Clear transients on any turn/active change (prevents stale cross-turn actions)
        if getattr(self.state, "pending_attack", None):
            self.state.pending_attack = None
        if getattr(self.state, "pending_damage", None):
            self.state.pending_damage = None

        # Visually mark the active token on the scene
        self._update_active_token_highlight()

        # --- TURN_START event (new token) + checkpoint ---
        try:
            if new_active and new_active in self.state.tokens:
                ts = self.state.tokens[new_active]
                self._history_append_and_apply({
                    "type": "TURN_START",
                    "initiative_active": bool(getattr(self.state, "initiative_active", False)),
                    "active_token_id": new_active,
                    "round_number": int(getattr(self.state, "round_number", 1) or 1),
                    "current_turn_index": int(getattr(self.state, "current_turn_index", 0) or 0),
                    "movement_remaining": int(getattr(ts, "movement_remaining", 0) or 0),
                    "checkpoint": True,
                })
        except Exception:
            pass


        # BX1: hazards at turn start (new active token)
        try:
            if new_active and new_active in self.state.tokens:
                self._apply_hazards_for_token(str(new_active), trigger="turn_start")
        except Exception:
            pass


        # BX5.5: damaging runtime clouds at turn start (new active token)
        try:
            if new_active and new_active in self.state.tokens:
                self._apply_runtime_cloud_damage_for_token(str(new_active), trigger="turn_start")
        except Exception:
            pass

        # Nudge the view to repaint (helps on some systems)
        try:
            self.scene.update()
        except Exception:
            pass

    def _rebuild_initiative_order(self) -> None:
        """
        Sort by initiative desc, stable tie-break by token_id.
        Only includes tokens that have an initiative value.
        """
        vals = getattr(self.state, "initiative_values", {}) or {}
        items = [(tid, int(vals[tid])) for tid in vals.keys() if tid in self.state.tokens]
        items.sort(key=lambda x: (-x[1], x[0]))
        self.state.initiative_order = [tid for tid, _ in items]

        # Keep current_turn_index in range
        if not self.state.initiative_order:
            self.state.current_turn_index = 0
            self.state.active_token_id = None
            return

        self.state.current_turn_index = max(0, min(int(getattr(self.state, "current_turn_index", 0)), len(self.state.initiative_order) - 1))
        self._set_active_turn(self.state.initiative_order[self.state.current_turn_index])

    def update_initiative_panel(self) -> None:
        if not getattr(self, "initiative_panel", None):
            return

        order = list(getattr(self.state, "initiative_order", []) or [])
        active = str(getattr(self.state, "active_token_id", "") or "")
        active_idx = int(getattr(self.state, "current_turn_index", 0) or 0)
        round_no = int(getattr(self.state, "round_number", 1) or 1)
        initiative_active = bool(getattr(self.state, "initiative_active", False))

        def _short(tid: str) -> str:
            tid = str(tid or "")
            return tid[:8] if len(tid) > 8 else tid

        # Build display lines
        lines = []
        for i, tid in enumerate(order):
            ts = self.state.tokens.get(tid)
            name = ts.display_name if ts else str(tid)
            marker = "▶" if (initiative_active and str(tid) == active) else " "
            lines.append(f"{marker} {i+1:02d}. {name}  [{_short(tid)}]")

        if not lines:
            lines = ["—"]

        # Button enables
        enable_roll = True
        enable_start = (not initiative_active) and bool(order)
        enable_turn_controls = initiative_active and bool(order)
        enable_end_encounter = initiative_active

        status = "Inactive"
        if initiative_active and order:
            ts = self.state.tokens.get(active)
            nm = ts.display_name if ts else active
            status = f"Round {round_no} • Turn {active_idx+1}/{len(order)} • Active: {nm}"

        # History cursor gating (Undo/Redo)
        hr = getattr(self, "history_runtime", None)
        hist = getattr(hr, "history", None) if hr else None
        cur = int(getattr(hist, "cursor", 0) or 0) if hist else 0
        n = len(getattr(hist, "events", []) or []) if hist else 0

        enable_undo = bool(cur > 0)
        enable_redo = bool(cur < n)

        self.initiative_panel.render(
            status=status,
            order_lines=lines,
            enable_roll=enable_roll,
            enable_start=enable_start,
            enable_turn_controls=enable_turn_controls,
            enable_end_encounter=enable_end_encounter,
            enable_undo=enable_undo,
            enable_redo=enable_redo,
        )

    def _roll_initiative_for(self, token_ids: list[str]) -> None:
        """
        v1: d20 + initiative_modifier (default 0)
        Stores in state.initiative_values and TokenState.initiative.
        """
        if not token_ids:
            return

        if not hasattr(self.state, "initiative_values") or self.state.initiative_values is None:
            self.state.initiative_values = {}

        for tid in token_ids:
            ts = self.state.tokens.get(tid)
            if not ts:
                continue
            mod = int(getattr(ts, "initiative_modifier", 0) or 0)
            if bool(getattr(ts, "initiative_advantage", False)):
                r1 = random.randint(1, 20)
                r2 = random.randint(1, 20)
                roll = max(r1, r2)
                total = roll + mod
                ts.initiative = total
                self.state.initiative_values[tid] = total
                try:
                    self.campaign_logger.combat("initiative_roll", token_id=tid, name=ts.display_name, roll=roll, mod=mod, total=total, mode="advantage", rolls=[r1, r2])
                except Exception:
                    pass
            else:
                roll = random.randint(1, 20)
                total = roll + mod
                ts.initiative = total
                self.state.initiative_values[tid] = total
                try:
                    self.campaign_logger.combat("initiative_roll", token_id=tid, name=ts.display_name, roll=roll, mod=mod, total=total)
                except Exception:
                    pass

        self._rebuild_initiative_order()
        self.update_initiative_panel()

    # ---------- UI callbacks (wired from InitiativePanelWidget) ----------

    def ui_roll_initiative_all(self) -> None:
        self._roll_initiative_for(list(self.state.tokens.keys()))

    def ui_roll_initiative_pcs(self) -> None:
        pcs = [tid for tid, ts in self.state.tokens.items() if getattr(ts, "side", "") == "player"]
        self._roll_initiative_for(pcs)

    def ui_roll_initiative_npcs(self) -> None:
        npcs = [tid for tid, ts in self.state.tokens.items() if getattr(ts, "side", "") != "player"]
        self._roll_initiative_for(npcs)

    def ui_roll_initiative_selected(self) -> None:
        self._roll_initiative_for(self._selected_token_ids())

    def ui_start_initiative_encounter(self) -> None:
        if not getattr(self.state, "initiative_order", None):
            self._set_hud_status("No initiative order. Roll initiative first.")
            self.update_combat_hud()
            return

        self.state.initiative_active = True
        self.state.round_number = 1
        self.state.current_turn_index = 0

        first_id = self.state.initiative_order[0]
        self._set_active_turn(first_id)

        # Ensure history base exists (first time only)
        if hasattr(self, "history_runtime") and self.history_runtime:
            self.history_runtime.capture_base_if_needed(self.state)

            ev = self.history_runtime.make_event(
                "TURN_START",
                payload={
                    "round": int(self.state.round_number),
                    "turn_index": int(self.state.current_turn_index),
                    "token_id": str(self.state.active_token_id or ""),
                },
                checkpoint=True,
                encounter_id=str(getattr(self, "encounter_id", "") or ""),
            )
            self.history_runtime.append_event(self.state, ev)

        try:
            self.campaign_logger.combat(
                "turn_start",
                round=int(self.state.round_number),
                token_id=self.state.active_token_id,
            )
        except Exception:
            pass

        # Phase 3.1 hook
        self.on_turn_start(self.state.active_token_id)

        self._set_hud_status(f"Initiative started. Round {self.state.round_number}.")
        self.update_combat_hud()
        self.update_initiative_panel()

    def ui_end_turn(self) -> None:
        if not self._initiative_active():
            return

        order = getattr(self.state, "initiative_order", []) or []
        if not order:
            return

        prev_id = self.state.active_token_id

        # Log turn end first (external log; separate from replay history)
        try:
            self.campaign_logger.combat(
                "turn_end",
                round=int(getattr(self.state, "round_number", 1) or 1),
                token_id=prev_id,
            )
        except Exception:
            pass

        # End-of-turn effects hook (conditions later)
        self.on_turn_end(prev_id)

        # Clear transients on turn transition (prevents stale cross-turn actions)
        if getattr(self.state, "pending_attack", None):
            self.state.pending_attack = None
        if getattr(self.state, "pending_damage", None):
            self.state.pending_damage = None

        # Record TURN_END checkpoint on the *post-end-effects* state
        if hasattr(self, "history_runtime") and self.history_runtime:
            ev_end = self.history_runtime.make_event(
                "TURN_END",
                payload={
                    "round": int(getattr(self.state, "round_number", 1) or 1),
                    "turn_index": int(getattr(self.state, "current_turn_index", 0) or 0),
                    "token_id": str(prev_id or ""),
                },
                checkpoint=True,
                encounter_id=str(getattr(self, "encounter_id", "") or ""),
            )
            self.history_runtime.append_event(self.state, ev_end)

        # Advance index / round
        self.state.current_turn_index = int(getattr(self.state, "current_turn_index", 0) or 0) + 1
        if self.state.current_turn_index >= len(order):
            self.state.current_turn_index = 0
            self.state.round_number = int(getattr(self.state, "round_number", 1) or 1) + 1

        # Activate next
        next_id = order[self.state.current_turn_index]
        self._set_active_turn(next_id)

        # Start-of-turn effects hook
        self.on_turn_start(self.state.active_token_id)

        # Log turn start (external log)
        try:
            self.campaign_logger.combat(
                "turn_start",
                round=int(getattr(self.state, "round_number", 1) or 1),
                token_id=self.state.active_token_id,
            )
        except Exception:
            pass

        # Record TURN_START checkpoint on the *post-start-effects* state
        if hasattr(self, "history_runtime") and self.history_runtime:
            ev_start = self.history_runtime.make_event(
                "TURN_START",
                payload={
                    "round": int(getattr(self.state, "round_number", 1) or 1),
                    "turn_index": int(getattr(self.state, "current_turn_index", 0) or 0),
                    "token_id": str(self.state.active_token_id or ""),
                },
                checkpoint=True,
                encounter_id=str(getattr(self, "encounter_id", "") or ""),
            )
            self.history_runtime.append_event(self.state, ev_start)

        self._set_hud_status(f"Turn advanced. Round {self.state.round_number}.")
        self.update_combat_hud()
        self.update_initiative_panel()

    def ui_prev_turn(self) -> None:
        if not self._initiative_active():
            return
        order = getattr(self.state, "initiative_order", []) or []
        if not order:
            return

        self.state.current_turn_index = (int(self.state.current_turn_index) - 1) % len(order)
        self._set_active_turn(order[self.state.current_turn_index])

        # Admin move: record event + checkpoint (so replay matches reality)
        if hasattr(self, "history_runtime") and self.history_runtime:
            ev = self.history_runtime.make_event(
                "TURN_SET_ADMIN",
                payload={
                    "direction": "prev",
                    "round": int(getattr(self.state, "round_number", 1) or 1),
                    "turn_index": int(getattr(self.state, "current_turn_index", 0) or 0),
                    "token_id": str(self.state.active_token_id or ""),
                },
                checkpoint=True,
                encounter_id=str(getattr(self, "encounter_id", "") or ""),
            )
            self.history_runtime.append_event(self.state, ev)

        self._set_hud_status("Moved to previous turn (admin).")
        self.update_combat_hud()
        self.update_initiative_panel()

    def ui_next_turn(self) -> None:
        if not self._initiative_active():
            return
        order = getattr(self.state, "initiative_order", []) or []
        if not order:
            return

        self.state.current_turn_index = (int(self.state.current_turn_index) + 1) % len(order)
        self._set_active_turn(order[self.state.current_turn_index])

        # Admin move: record event + checkpoint (so replay matches reality)
        if hasattr(self, "history_runtime") and self.history_runtime:
            ev = self.history_runtime.make_event(
                "TURN_SET_ADMIN",
                payload={
                    "direction": "next",
                    "round": int(getattr(self.state, "round_number", 1) or 1),
                    "turn_index": int(getattr(self.state, "current_turn_index", 0) or 0),
                    "token_id": str(self.state.active_token_id or ""),
                },
                checkpoint=True,
                encounter_id=str(getattr(self, "encounter_id", "") or ""),
            )
            self.history_runtime.append_event(self.state, ev)

        self._set_hud_status("Moved to next turn (admin).")
        self.update_combat_hud()
        self.update_initiative_panel()

    def ui_history_undo(self) -> None:
        hr = getattr(self, "history_runtime", None)
        hist = getattr(hr, "history", None) if hr else None
        if not hr or not hist or getattr(hist, "base_snapshot", None) is None:
            self._set_hud_status("Undo unavailable: no history base snapshot.")
            self.update_combat_hud()
            return

        cur = int(getattr(hist, "cursor", 0) or 0)
        if cur <= 0:
            self._set_hud_status("Nothing to undo.")
            self.update_combat_hud()
            return

        new_cursor = cur - 1

        print(f"[HISTORY] UNDO: cursor {cur} -> {new_cursor}")
        try:
            self.campaign_logger.combat("history_undo", cursor_from=cur, cursor_to=new_cursor)
        except Exception:
            pass

        self._replay_to_cursor(new_cursor, reason="UNDO")

        # Force graphics re-sync (HP bars + dead sprites + overlays)
        self._post_replay_ui_sync()

        self._set_hud_status(f"Undo → step {new_cursor}")
        self.update_combat_hud()
        self.update_initiative_panel()


    def ui_history_redo(self) -> None:
        hr = getattr(self, "history_runtime", None)
        hist = getattr(hr, "history", None) if hr else None
        if not hr or not hist or getattr(hist, "base_snapshot", None) is None:
            self._set_hud_status("Redo unavailable: no history base snapshot.")
            self.update_combat_hud()
            return

        cur = int(getattr(hist, "cursor", 0) or 0)
        events = list(getattr(hist, "events", []) or [])
        if cur >= len(events):
            self._set_hud_status("Nothing to redo.")
            self.update_combat_hud()
            return

        new_cursor = cur + 1

        print(f"[HISTORY] REDO: cursor {cur} -> {new_cursor}")
        try:
            self.campaign_logger.combat("history_redo", cursor_from=cur, cursor_to=new_cursor)
        except Exception:
            pass

        self._replay_to_cursor(new_cursor, reason="REDO")

        # Force graphics re-sync (HP bars + dead sprites + overlays)
        self._post_replay_ui_sync()

        self._set_hud_status(f"Redo → step {new_cursor}")
        self.update_combat_hud()
        self.update_initiative_panel()

    def _update_active_token_highlight(self) -> None:
        """
        Visually highlight the active token on the board.
        Uses a pen outline so it is obvious even with reused sprites.
        """
        active_id = getattr(self.state, "active_token_id", None)

        for item in self.scene.items():
            token_id = getattr(item, "token_id", None)
            if not token_id:
                continue

            # Reset any previous highlight
            if hasattr(item, "set_active_highlight"):
                try:
                    item.set_active_highlight(False)
                except Exception:
                    pass

            # Apply highlight to active token
            if token_id == active_id and hasattr(item, "set_active_highlight"):
                item.set_active_highlight(True)

    def on_turn_start(self, token_id: str) -> None:
        """
        Deterministic turn start:
        - reset per-turn movement remaining
        - reset per-turn action usage
        - process start-of-turn engine-owned condition hooks
        """
        if not token_id or token_id not in self.state.tokens:
            return

        ts = self.state.tokens.get(token_id)
        if not ts:
            return

        ts.has_acted_this_turn = False

        try:
            mv = int(getattr(ts, "movement", 30) or 30)
        except Exception:
            mv = 30
        ts.base_movement = mv
        ts.movement_remaining = self._effective_speed_for_token(ts)

        self._process_condition_turn_hook(token_id, timing="start")
        self._maybe_open_death_save_for_token(token_id)

        try:
            self.campaign_logger.combat(
                "turn_start_state",
                token_id=token_id,
                name=getattr(ts, "display_name", token_id),
                movement_remaining=int(ts.movement_remaining),
                has_acted=bool(ts.has_acted_this_turn),
                round=int(getattr(self.state, "round_number", 1) or 1),
                statuses=list(getattr(ts, "statuses", []) or []),
            )
        except Exception:
            pass

    def on_turn_end(self, token_id: str) -> None:
        """
        End-of-turn engine-owned condition processing.
        Durations decrement at end of the affected token's turn.
        """
        if not token_id or token_id not in self.state.tokens:
            return
        self._process_condition_turn_hook(token_id, timing="end")
        self._tick_spell_state_for_token(token_id, timing="end")
        return
    
    def _set_active_turn_admin(self, token_id: str) -> None:
        # purely for inspection / admin, no status ticking
        self.state.active_token_id = token_id or None
        # Clear transients on any turn/active change (prevents stale cross-turn actions)
        if getattr(self.state, "pending_attack", None):
            self.state.pending_attack = None
        if getattr(self.state, "pending_damage", None):
            self.state.pending_damage = None
        self._update_active_token_highlight()
        try:
            self.scene.update()
        except Exception:
            pass

    def apply_end_of_turn_damage(self, token_id: str, amount: int, *, source: str) -> None:
        """
        Centralized damage entry for status ticks (burn/poison/etc).
        Must log and must be deterministic.
        """
        if not token_id or token_id not in self.state.tokens:
            return
        ts = self.state.tokens[token_id]
        dmg = int(amount)

        # Use your existing damage pipeline
        self.apply_damage_to_token(ts, dmg, encounter_id=getattr(self, "encounter_id", ""), pending_attack_id=f"status:{source}", source_kind="status")

        try:
            self.campaign_logger.combat("status_damage", token_id=token_id, name=ts.display_name, source=source, amount=dmg)
        except Exception:
            pass

    def _get_selected_pc_token_id(self):
        selected = self.scene.selectedItems()
        if not selected:
            return None
        item = selected[0]
        if not isinstance(item, DraggableToken):
            return None

        ts = self.state.tokens.get(getattr(item, "token_id", ""))
        if not ts:
            return None

        # PC-safe gating (matches your side/kind conventions)
        if getattr(ts, "side", "") == "player" or getattr(ts, "kind", "") == "pc":
            return ts.token_id
        return None

    def _pick_default_spell_id(self):
        # If you later want "known_spells" per character, plug it in here.
        # For now, just pick first spell in spells_db.
        if not self.spells_db:
            return ""
        return next(iter(self.spells_db.keys()))

    def _push_aoe_template_to_player_view(self):
        if self.player_view_window is None:
            return
        if not self.player_view_window.isVisible():
            return

        if not self._aoe_active:
            setattr(self.player_view_window, "template_payload", None)
            return

        caster_id = self._get_selected_pc_token_id()
        if not caster_id:
            setattr(self.player_view_window, "template_payload", None)
            return

        spell = self.spells_db.get(self._aoe_spell_id or "", None)
        if not spell:
            setattr(self.player_view_window, "template_payload", None)
            return

        # choose current target cell
        target_cell = self._aoe_locked_cell if self._aoe_locked else self._aoe_target_cell
        if not target_cell:
            setattr(self.player_view_window, "template_payload", None)
            return

        payload = {
            "caster_token_id": caster_id,
            "spell_id": self._aoe_spell_id,
            "spell_name": spell.get("name", self._aoe_spell_id),
            "target_cell": target_cell,
            "template": (((spell.get("targeting") or {}).get("template")) or {}),
            "locked": bool(self._aoe_locked),
        }
        setattr(self.player_view_window, "template_payload", payload)

    def eventFilter(self, obj, event):
        # Mouse move on the map view -> update target cell if AoE preview active
        if self._aoe_active and event.type() == QEvent.MouseMove:
            try:
                pos = event.pos()
                sp = self.view.mapToScene(pos)
                gx = int(sp.x() // GRID_SIZE)
                gy = int(sp.y() // GRID_SIZE)

                # Only update if inside map bounds (scene rect)
                r = self.scene.sceneRect()
                if 0 <= sp.x() <= r.width() and 0 <= sp.y() <= r.height():
                    self._aoe_target_cell = (gx, gy)
                    self.refresh_player_view()
            except Exception:
                pass
            return False

        return super().eventFilter(obj, event)

    def keyPressEvent(self, event):
        key = event.key()

        # Toggle AoE preview (T)
        if key == Qt.Key_T:
            caster_id = self._get_selected_pc_token_id()
            if not caster_id:
                print("[AOE] Select a PC token first.")
                return

            if not self.spells_db:
                print("[AOE] No spells loaded (spells.json missing/empty).")
                return

            if not self._aoe_active:
                self._aoe_active = True
                if not self._aoe_spell_id:
                    self._aoe_spell_id = self._pick_default_spell_id()
                print(f"[AOE] ON: {self._aoe_spell_id}")
            else:
                self._aoe_active = False
                self._aoe_target_cell = None
                if self.player_view_window is not None:
                    setattr(self.player_view_window, "template_payload", None)
                print("[AOE] OFF")

            self.refresh_player_view()
            return

        # Cycle spells while AoE is active ([ and ])
        if self._aoe_active and key in (Qt.Key_BracketLeft, Qt.Key_BracketRight):
            ids = list(self.spells_db.keys())
            if not ids:
                return
            if self._aoe_spell_id not in ids:
                self._aoe_spell_id = ids[0]
            else:
                i = ids.index(self._aoe_spell_id)
                if key == Qt.Key_BracketRight:
                    i = (i + 1) % len(ids)
                else:
                    i = (i - 1) % len(ids)
                self._aoe_spell_id = ids[i]
            print(f"[AOE] Spell: {self._aoe_spell_id}")
            self.refresh_player_view()
            return

        # Escape clears template preview quickly
        if key == Qt.Key_Escape and self._aoe_active:
            self._aoe_active = False
            self._aoe_target_cell = None
            if self.player_view_window is not None:
                setattr(self.player_view_window, "template_payload", None)
            print("[AOE] OFF")
            self.refresh_player_view()
            return

        super().keyPressEvent(event)

    def _on_aoe_spell_combo_changed(self, idx: int):
        if idx < 0:
            return
        spell_id = self.aoe_spell_combo.currentData()
        if spell_id:
            self._aoe_spell_id = spell_id
            if self._aoe_active:
                self.refresh_player_view()

    def _get_known_template_spells_for_selected_pc(self):
        pc_token_id = self._get_selected_pc_token_id()
        if not pc_token_id:
            return []

        ts = self.state.tokens.get(pc_token_id)
        if not ts or not getattr(ts, "character_id", ""):
            return []

        # Load character sheet JSON
        char_id = ts.character_id
        path = os.path.join(self.campaign_path, "characters", f"{char_id}.json")
        if not os.path.exists(path):
            # if you store sheets differently, adjust path here
            return []

        try:
            with open(path, "r", encoding="utf-8") as f:
                cj = json.load(f)
        except Exception:
            return []

        known = cj.get("known_spells", []) or ((cj.get("spellcasting", {}) or {}).get("known_spells", []) or [])
        out = []
        for sid in known:
            s = self.spells_db.get(sid)
            if not s:
                continue
            targeting = s.get("targeting", {}) or {}
            if targeting.get("kind") != "template":
                continue
            out.append((sid, s.get("name", sid)))
        return out

    def _refresh_aoe_spell_combo(self):
        self.aoe_spell_combo.blockSignals(True)
        self.aoe_spell_combo.clear()

        spells = self._get_known_template_spells_for_selected_pc()

        for sid, name in spells:
            self.aoe_spell_combo.addItem(name, sid)

        # Set current selection if present
        if self._aoe_spell_id:
            for i in range(self.aoe_spell_combo.count()):
                if self.aoe_spell_combo.itemData(i) == self._aoe_spell_id:
                    self.aoe_spell_combo.setCurrentIndex(i)
                    break
        else:
            if spells:
                self._aoe_spell_id = spells[0][0]
                self.aoe_spell_combo.setCurrentIndex(0)

        self.aoe_spell_combo.blockSignals(False)

    def build_sheet_backed_tokenstate(self, *, character_id: str, token_template: dict) -> TokenState:
        """
        Create a TokenState for a sheet-backed PC using the server character sheet.
        This is the correct place to honor sheet['equipped'] on initial spawn.
        """
        sheet = self.server.get_character_sheet(character_id)
        if not sheet or not isinstance(sheet, dict):
            raise RuntimeError(f"Character sheet not found for {character_id}")

        base_stats = sheet.get("base_stats", {}) if isinstance(sheet.get("base_stats", {}), dict) else {}
        resources  = sheet.get("resources", {}) if isinstance(sheet.get("resources", {}), dict) else {}
        equipped   = sheet.get("equipped", {}) if isinstance(sheet.get("equipped", {}), dict) else {}

        # Equipment: equipped is authoritative
        weapon_id = (
            str(equipped.get("weapon_id", "") or "").strip()
            or str(equipped.get("weapon", "") or "").strip()
            or str(base_stats.get("weapon_id", "") or "").strip()
            or str(base_stats.get("weapon", "") or "").strip()
            or str(token_template.get("weapon_id", "") or "").strip()
            or str(token_template.get("weapon", "") or "").strip()
        )

        armor_id = (
            str(equipped.get("armor_id", "") or "").strip()
            or str(equipped.get("armor", "") or "").strip()
            or str(base_stats.get("armor_id", "") or "").strip()
            or str(base_stats.get("armor", "") or "").strip()
            or str(token_template.get("armor_id", "") or "").strip()
            or str(token_template.get("armor", "") or "").strip()
        )

        if not weapon_id:
            weapon_id = "unarmed"

        # Create token state
        ts = TokenState(
            token_id="",  # caller should assign UUID
            display_name=sheet.get("name", token_template.get("display_name", "PC")),
            image_relpath=token_template.get("image", token_template.get("image_relpath", "")),
            grid_x=0,
            grid_y=0,
        )

        # Mark as sheet-backed
        ts.stat_source = "character_sheet"
        ts.character_id = character_id
        ts.side = "player"
        ts.kind = "pc"

        # Core stats
        def _as_int(val, default):
            try:
                return int(val)
            except Exception:
                return default

        ts.max_hp = _as_int(base_stats.get("max_hp", token_template.get("max_hp", 10)), 10)
        ts.hp = _as_int(resources.get("current_hp", ts.max_hp), ts.max_hp)
        ts.ac = _as_int(base_stats.get("ac", token_template.get("ac", 10)), 10)
        ts.movement = _as_int(base_stats.get("movement", token_template.get("movement", 30)), 30)
        ts.attack_modifier = _as_int(base_stats.get("attack_modifier", token_template.get("attack_modifier", 0)), 0)
        ts.vision_ft = _as_int(base_stats.get("vision_ft", token_template.get("vision_ft", 60)), 60)

        # B-X4: Vision Types / senses
        ts.vision_type = str(base_stats.get("vision_type", token_template.get("vision_type", "normal")) or "normal")
        ts.darkvision_ft = _as_int(base_stats.get("darkvision_ft", token_template.get("darkvision_ft", 0)), 0)
        ts.blindsight_ft = _as_int(base_stats.get("blindsight_ft", token_template.get("blindsight_ft", 0)), 0)
        ts.truesight_ft = _as_int(base_stats.get("truesight_ft", token_template.get("truesight_ft", 0)), 0)
        ts.tremorsense_ft = _as_int(base_stats.get("tremorsense_ft", token_template.get("tremorsense_ft", 0)), 0)
        ts.devils_sight_ft = _as_int(base_stats.get("devils_sight_ft", token_template.get("devils_sight_ft", 0)), 0)

        # Equipment (ids + legacy mirrors)
        ts.weapon_id = weapon_id
        ts.armor_id = armor_id
        ts.weapon = weapon_id
        ts.armor = armor_id

        return ts

    def _compute_template_cells(self, caster_ts, target_cell, template, cols, rows):
        """
        Returns list[(x,y)] of affected cells for radius/line/cone templates.
        Uses the same math as PlayerViewWindow (keep consistent).
        """
        import math

        ox, oy = int(caster_ts.grid_x), int(caster_ts.grid_y)
        tx, ty = int(target_cell[0]), int(target_cell[1])

        shape = str((template or {}).get("shape", "")).lower().strip()

        def circle_cells(cx, cy, r):
            out = []
            r2 = r * r
            for dx in range(-r, r + 1):
                for dy in range(-r, r + 1):
                    if dx*dx + dy*dy <= r2:
                        x = cx + dx
                        y = cy + dy
                        if 0 <= x < cols and 0 <= y < rows:
                            out.append((x, y))
            return out

        def bresenham(x0, y0, x1, y1):
            cells = []
            dx = abs(x1 - x0)
            dy = -abs(y1 - y0)
            sx = 1 if x0 < x1 else -1
            sy = 1 if y0 < y1 else -1
            err = dx + dy
            x, y = x0, y0
            while True:
                cells.append((x, y))
                if x == x1 and y == y1:
                    break
                e2 = 2 * err
                if e2 >= dy:
                    err += dy
                    x += sx
                if e2 <= dx:
                    err += dx
                    y += sy
            return cells

        def cone_cells():
            length = int((template or {}).get("cone_length_ft", 30) or 30) // 5
            angle = int((template or {}).get("cone_angle_deg", 90) or 90)
            length = max(1, length)
            half = math.radians(angle / 2.0)
            cos_thresh = math.cos(half)

            vx = tx - ox
            vy = ty - oy
            vlen = math.hypot(vx, vy)
            if vlen == 0:
                return []
            ux, uy = vx / vlen, vy / vlen

            out = []
            for dx in range(-length, length + 1):
                for dy in range(-length, length + 1):
                    x = ox + dx
                    y = oy + dy
                    if not (0 <= x < cols and 0 <= y < rows):
                        continue
                    dist = math.hypot(dx, dy)
                    if dist == 0 or dist > length:
                        continue
                    nx, ny = dx / dist, dy / dist
                    dot = nx * ux + ny * uy
                    if dot >= cos_thresh:
                        out.append((x, y))
                return out

            if shape == "radius":
                r_ft = int((template or {}).get("radius_ft", 15) or 15)
                r = max(1, r_ft // 5)
                return circle_cells(tx, ty, r)

            if shape == "line":
                length_ft = int((template or {}).get("line_length_ft", 30) or 30)
                length = max(1, length_ft // 5)
                dx = tx - ox
                dy = ty - oy
                dist = math.hypot(dx, dy)
                if dist == 0:
                    return []
                ux, uy = dx / dist, dy / dist
                ex = int(round(ox + ux * length))
                ey = int(round(oy + uy * length))
                cells = bresenham(ox, oy, ex, ey)
                return cells[: length + 1]

            if shape == "cone":
                return cone_cells()

            return []

        def _arm_aoe_damage_request(self):
            """
            Phase 4.4:
            When an AoE template is LOCKED, arm a damage request for the player portal.
            The player rolls damage once; we apply it to every affected token.
            """
            if not getattr(self, "_aoe_active", False):
                return
            if not getattr(self, "_aoe_locked", False):
                return

            # Must have a selected PC caster (never allow enemy AoE to appear / roll via portal)
            caster_token_id = self._get_selected_pc_token_id()
            if not caster_token_id:
                return

            caster_ts = self.state.tokens.get(caster_token_id)
            if not caster_ts:
                return

            # Locked target cell required
            target_cell = getattr(self, "_aoe_locked_cell", None)
            if not target_cell:
                return

            # Spell required
            spell_id = (getattr(self, "_aoe_spell_id", "") or "").strip()
            spell = self.spells_db.get(spell_id)
            if not spell:
                return

            # Damage expression for portal roll
            dmg_expr = str(spell.get("damage", "") or "").strip()
            if not dmg_expr:
                # No-damage spell: nothing to roll/apply
                self._set_hud_status(f"Spell '{spell_id}' has no damage; nothing to roll.", hold_sec=3.0)
                self.update_combat_hud()
                return

            # Compute affected cells from the template + then tokens inside those cells
            template = ((spell.get("targeting") or {}).get("template")) or {}
            affected_cells = self._compute_template_cells(
                caster_cell=(int(getattr(caster_ts, "grid_x", 0)), int(getattr(caster_ts, "grid_y", 0))),
                target_cell=(int(target_cell[0]), int(target_cell[1])),
                template=template,
            )
            target_token_ids = self._tokens_in_cells(affected_cells)

            if not target_token_ids:
                self._set_hud_status("AoE locked, but no tokens are inside the template.", hold_sec=3.0)
                self.update_combat_hud()
                return

            # Player id for portal routing
            player_id = (getattr(caster_ts, "player_id", "") or "").strip()
            if not player_id:
                # If you don’t store player_id on TokenState, fall back to character sheet player_id
                player_id = (getattr(caster_ts, "character_id", "") or "").strip()

            if not player_id:
                self._set_hud_status("Caster has no player_id; cannot request portal roll.", hold_sec=3.0)
                self.update_combat_hud()
                return

            # Create an "attack_id" for dedupe + matching the portal response
            import uuid, time
            attack_id = uuid.uuid4().hex

            self._awaiting_aoe_damage = {
                "attack_id": attack_id,
                "player_id": player_id,
                "encounter_id": str(getattr(self.state, "encounter_id", "") or ""),
                "caster_token_id": caster_ts.token_id,
                "spell_id": spell_id,
                "spell_name": str(spell.get("name", spell_id)),
                "target_cell": (int(target_cell[0]), int(target_cell[1])),
                "target_token_ids": list(target_token_ids),
                "damage_expr": dmg_expr,
                "crit": False,  # keep False for now; can add crit rules later
                "created_monotonic": time.monotonic(),
                "expires_in_sec": 90,
            }

            # Tell portal to roll damage (reuse NEED_DAMAGE flow)
            self.server.post_message(player_id,
                f"Cast {spell.get('name', spell_id)}. Roll damage: {dmg_expr}",
                "warn",
                ttl_seconds=60,
                data={
                    "type": "NEED_DAMAGE",
                    "attack_id": attack_id,
                    "damage_expr": dmg_expr,
                    "crit": False,
                    "weapon_name": str(spell.get("name", spell_id)),  # portal field name reused
                    "target_name": f"{len(target_token_ids)} targets",
                    "dedupe_key": f"NEED_DAMAGE:{attack_id}",
                    "aoe": True,
                },
            )

            self._set_hud_status(f"AoE armed: {spell.get('name', spell_id)} (waiting for damage roll).", hold_sec=3.0)
            self.update_combat_hud()

    def _tokens_in_cells(self, cells):
        """
        Returns token_ids whose TokenState.grid_x/grid_y is contained in cells.
        cells: iterable[(x,y)]
        """
        cell_set = set((int(x), int(y)) for (x, y) in (cells or []))
        out = []
        for token_id, ts in (self.state.tokens or {}).items():
            try:
                gx = int(getattr(ts, "grid_x", -9999))
                gy = int(getattr(ts, "grid_y", -9999))
                if (gx, gy) in cell_set:
                    out.append(token_id)
            except Exception:
                continue
        return out
    
    def on_token_moved(self, *args) -> None:
        """
        Supports BOTH callback signatures:
        NEW: (token_item, token_id, from_gx, from_gy, to_gx, to_gy)
        OLD: (token_id, from_gx, from_gy, to_gx, to_gy)

        Determinism rule:
        - Only COMMITTED moves emit SET_POSITION history events.
        - Rejected moves revert graphics and emit nothing.
        """
        tok_item = None

        if len(args) == 6:
            tok_item, token_id, from_gx, from_gy, to_gx, to_gy = args
        elif len(args) == 5:
            token_id, from_gx, from_gy, to_gx, to_gy = args
            tok_item = self._get_scene_token_item(token_id)
        else:
            return

        ts = self.state.tokens.get(token_id)
        if ts is None:
            return

        # ---- Turn gating: only active token may move ----
        if bool(getattr(self.state, "initiative_active", False)):
            active_id = getattr(self.state, "active_token_id", None)
            if active_id and token_id != active_id:
                if tok_item is not None:
                    tok_item._suppress_move_callback = True
                    tok_item.setPos(int(from_gx) * self.grid_size, int(from_gy) * self.grid_size)
                    tok_item._suppress_move_callback = False
                return
        # ---- Compute movement cost (engine rules: blocked + difficult terrain) ----
        scene_rect = None
        cols = rows = 0
        try:
            scene_rect = self.scene.sceneRect()
            cols = int(scene_rect.width() // self.grid_size)
            rows = int(scene_rect.height() // self.grid_size)
        except Exception:
            cols = rows = 0

        meta = getattr(self, "current_map_meta", {}) or {}

        # Track last legal (best-effort)
        try:
            if getattr(ts, "last_legal_gx", None) is None:
                ts.last_legal_gx = int(from_gx)
                ts.last_legal_gy = int(from_gy)
        except Exception:
            pass

        # Soft handling for blocked cells:
        # - Do not spend movement
        # - Do not update last_legal
        # - Commit position (DM-authoritative) but mark illegal so it can be reverted
        try:
            blocked_dest = is_blocked(meta, int(to_gx), int(to_gy))
        except Exception:
            blocked_dest = False

        if blocked_dest:
            try:
                ts.grid_x = int(to_gx)
                ts.grid_y = int(to_gy)
                ts.illegal_position = True
                ts.illegal_reason = "blocked"
                print(
                f"[MOVE][ILLEGAL] {getattr(ts, 'display_name', token_id)} "
                f"attempted blocked cell ({int(to_gx)},{int(to_gy)}); "
                f"last legal=({int(getattr(ts, 'last_legal_gx', from_gx))},{int(getattr(ts, 'last_legal_gy', from_gy))})"
            )
            except Exception:
                pass

            try:
                self.campaign_logger.event(
                    "ILLEGAL_MOVE_BLOCKED",
                    token_id=str(token_id),
                    token_name=getattr(ts, "display_name", token_id),
                    from_gx=int(from_gx), from_gy=int(from_gy),
                    to_gx=int(to_gx), to_gy=int(to_gy),
                    last_legal_gx=int(getattr(ts, "last_legal_gx", from_gx)),
                    last_legal_gy=int(getattr(ts, "last_legal_gy", from_gy)),
                )
            except Exception:
                pass

            # Emit a deterministic position event (cost 0) so replays reflect what happened.
            try:
                self._history_append_and_apply({
                    "type": "SET_POSITION",
                    "token_id": str(token_id),
                    "from_gx": int(from_gx),
                    "from_gy": int(from_gy),
                    "to_gx": int(to_gx),
                    "to_gy": int(to_gy),
                    "cost_sq": 0,
                    "cost_ft": 0,
                    "illegal": True,
                    "illegal_reason": "blocked",
                    "last_legal_gx": int(getattr(ts, "last_legal_gx", from_gx)),
                    "last_legal_gy": int(getattr(ts, "last_legal_gy", from_gy)),
                })
            except Exception:
                pass

            try:
                self.update_combat_hud()
            except Exception:
                pass
            try:
                self.refresh_player_view()
            except Exception:
                pass
            return

        # Compute minimal movement cost to destination
        cost_ft = None
        try:
            if cols > 0 and rows > 0:
                cost_ft = compute_min_cost_ft(
                    from_x=int(from_gx), from_y=int(from_gy),
                    to_x=int(to_gx), to_y=int(to_gy),
                    cols=int(cols), rows=int(rows),
                    meta=meta,
                    feet_per_square=5,
                )
        except Exception:
            cost_ft = None

        # Fallback: legacy cost if engine pathing is unavailable
        if cost_ft is None:
            dx = abs(int(to_gx) - int(from_gx))
            dy = abs(int(to_gy) - int(from_gy))
            cost_sq = max(dx, dy)
            cost_ft = int(cost_sq) * 5
        else:
            cost_sq = int(cost_ft) // 5

        ensure_movement_initialized(ts)
        remaining_before = int(getattr(ts, "movement_remaining", 0) or 0)
        if int(self._effective_speed_for_token(ts)) <= 0:
            if tok_item is not None:
                tok_item._suppress_move_callback = True
                tok_item.setPos(int(from_gx) * self.grid_size, int(from_gy) * self.grid_size)
                tok_item._suppress_move_callback = False
            self._set_hud_status(f"{getattr(ts, 'display_name', token_id)} cannot move while restrained/stunned.", hold_sec=3.0)
            return

        # ---- Enforce remaining movement (only in initiative mode) ----
        if bool(getattr(self.state, "initiative_active", False)) and int(cost_ft) > remaining_before:
            if tok_item is not None:
                tok_item._suppress_move_callback = True
                tok_item.setPos(int(from_gx) * self.grid_size, int(from_gy) * self.grid_size)
                tok_item._suppress_move_callback = False
            return

        # ---- Commit state changes ----


        spend_movement(ts, cost_sq, feet_per_square=5)

        ts.grid_x = int(to_gx)
        ts.grid_y = int(to_gy)

        # Clear illegal marker and update last legal position
        try:
            ts.illegal_position = False
            ts.illegal_reason = ""
            ts.last_legal_gx = int(to_gx)
            ts.last_legal_gy = int(to_gy)
        except Exception:
            pass


        # BX3: Elevation / Falling (RAW-ish)
        # If elevation decreases by >= 10 ft in a single committed move, apply falling damage.
        # This applies to BOTH voluntary movement and forced repositioning (same resolution path).
        try:
            from engine.elevation_engine import get_drop_edge_drop_ft, falling_damage_dice, falling_save_dc
            from engine.hazard_engine import roll_dice
            meta_now = getattr(self, "current_map_meta", {}) or {}
            drop_ft = int(get_drop_edge_drop_ft(meta_now, int(from_gx), int(from_gy), int(to_gx), int(to_gy)) or 0)
            # BX3.2: Falling triggers only when crossing a painted drop edge.
            # (Elevation deltas alone are treated as ramps/slopes unless a drop edge is authored.)
            if drop_ft >= 10:
                dmg_expr = falling_damage_dice(drop_ft)
                dmg_total, rolls, mod = roll_dice(dmg_expr)
                dmg_total = int(dmg_total or 0)

                # PCs roll their own falling save through the portal; NPCs resolve internally.
                pending_id = f"fall:{drop_ft}ft:{from_gx},{from_gy}->{to_gx},{to_gy}"
                dc = int(falling_save_dc(drop_ft) or 10)

                if self._is_player_controlled_token(ts):
                    req_id = self._register_pc_deferred_damage_save_request(
                        ts,
                        ability="DEX",
                        dc=int(dc),
                        mode="normal",
                        label="Fall Save",
                        context={
                            "kind": "fall_save",
                            "source_kind": "fall",
                            "drop_ft": int(drop_ft),
                            "from_gx": int(from_gx),
                            "from_gy": int(from_gy),
                            "to_gx": int(to_gx),
                            "to_gy": int(to_gy),
                        },
                        deferred_effect={
                            "base_damage": int(dmg_total),
                            "save_on_success": "half",
                            "pending_attack_id": pending_id,
                            "source_kind": "fall",
                            "damage_type": "fall",
                        },
                    )
                    if req_id:
                        try:
                            print(f"[FALL_SAVE_REQUEST] {getattr(ts, 'display_name', token_id)} drop_ft={int(drop_ft)} expr={dmg_expr} dc={int(dc)} request_id={req_id}")
                        except Exception:
                            pass
                        try:
                            self.campaign_logger.combat(
                                "save_requested",
                                request_id=str(req_id),
                                token_id=str(token_id),
                                name=str(getattr(ts, 'display_name', token_id) or token_id),
                                player_id=str(getattr(ts, 'player_id', '') or ''),
                                character_id=str(getattr(ts, 'character_id', '') or ''),
                                ability="DEX",
                                dc=int(dc),
                                mode="normal",
                                label="Fall Save",
                                source_kind="fall",
                            )
                        except Exception:
                            pass
                    else:
                        try:
                            print(f"[FALL] save request failed; applying direct damage to {getattr(ts, 'display_name', token_id)}")
                        except Exception:
                            pass
                        self.apply_damage_to_token(ts, int(dmg_total), pending_attack_id=pending_id, damage_type="fall", source_kind="fall")
                else:
                    try:
                        bonus = int(getattr(ts, "fall_save_bonus", 0) or 0)
                    except Exception:
                        bonus = 0
                    save_roll, save_rolls, _ = roll_dice("1d20")
                    save_total = int(save_roll) + int(bonus)
                    saved = bool(save_total >= dc)
                    npc_damage = int(dmg_total // 2) if saved else int(dmg_total)

                    try:
                        print(
                            f"[FALL] {getattr(ts, 'display_name', token_id)} "
                            f"drop_ft={int(drop_ft)} expr={dmg_expr} rolls={list(rolls)} "
                            f"save=({int(save_roll)}+{int(bonus)}={int(save_total)} vs DC {int(dc)}) "
                            f"-> dmg={int(npc_damage)}"
                        )
                    except Exception:
                        pass

                    try:
                        self.campaign_logger.event(
                            "FALL_DAMAGE",
                            token_id=str(token_id),
                            token_name=getattr(ts, "display_name", token_id),
                            from_gx=int(from_gx), from_gy=int(from_gy),
                            to_gx=int(to_gx), to_gy=int(to_gy),
                            drop_ft=int(drop_ft),
                            damage_expr=str(dmg_expr),
                            rolls=list(rolls),
                            save_d20=int(save_roll),
                            save_bonus=int(bonus),
                            save_total=int(save_total),
                            dc=int(dc),
                            saved=bool(saved),
                            damage=int(npc_damage),
                        )
                    except Exception:
                        pass

                    if npc_damage > 0:
                        try:
                            self.apply_damage_to_token(ts, int(npc_damage), pending_attack_id=pending_id, damage_type="fall", source_kind="fall")
                        except Exception:
                            pass
        except Exception:
            pass

        remaining_after = int(getattr(ts, "movement_remaining", 0) or 0)

        try:
            print(f"[MOVE] {getattr(ts, 'display_name', token_id)} cost_ft={int(cost_ft)} remaining {int(remaining_before)} -> {int(remaining_after)}")
        except Exception:
            pass
        try:
            self.campaign_logger.event(
                "MOVE_SPENT",
                token_id=str(token_id),
                token_name=getattr(ts, 'display_name', token_id),
                from_gx=int(from_gx), from_gy=int(from_gy),
                to_gx=int(to_gx), to_gy=int(to_gy),
                cost_ft=int(cost_ft),
                remaining_before=int(remaining_before),
                remaining_after=int(remaining_after),
            )
        except Exception:
            pass

        # Phase D6: opportunity attacks when a token leaves melee reach.
        try:
            self._handle_opportunity_attacks_for_move(
                ts,
                from_gx=int(from_gx),
                from_gy=int(from_gy),
                to_gx=int(to_gx),
                to_gy=int(to_gy),
            )
        except Exception as e:
            try:
                print(f"[REACTION] opportunity attack handling error: {e}")
            except Exception:
                pass

        # If this is the active token, refresh movement overlay
        if bool(getattr(self.state, "initiative_active", False)) and getattr(self.state, "active_token_id", None) == token_id:
            try:
                self._refresh_active_token_movement_overlay()
            except Exception:
                pass

        # --- DM attack overlay refresh: keep centered while moving ---
        try:
            if tok_item is not None and tok_item.isSelected():
                weapon_ref = (getattr(ts, "weapon_id", "") or getattr(ts, "weapon", "") or "").strip()
                weapon_data = self.get_weapon_data(weapon_ref)
                tok_item.hide_attack_range(self.scene)
                if weapon_data:
                    tok_item.show_attack_range(self.scene, weapon_data)
        except Exception:
            pass

        # --- Phase 5: deterministic movement event ---
        try:
            self._history_append_and_apply({
                "type": "SET_POSITION",
                "token_id": str(token_id),
                "from_gx": int(from_gx),
                "from_gy": int(from_gy),
                "to_gx": int(to_gx),
                "to_gy": int(to_gy),
                "cost_sq": int(cost_sq),
                "cost_ft": int(cost_ft),
                "movement_remaining_before": int(remaining_before),
                "movement_remaining_after": int(remaining_after),
            })
        except Exception as e:
            print(f"[HISTORY] Failed to append SET_POSITION: {e}")

        # --- Rebuild overlays through selection pipeline if active token ---
        initiative_on = bool(getattr(self.state, "initiative_active", False))
        active_id = getattr(self.state, "active_token_id", None)

        if tok_item is not None and initiative_on and active_id and token_id == active_id:
            try:
                tok_item.setSelected(True)
            except Exception:
                pass
            try:
                self.process_token_selection(tok_item)
            except Exception:
                pass

        
        # BX1: hazards on enter
        try:
            self._apply_hazards_for_token(str(token_id), trigger="enter")
        except Exception:
            pass

        try:
            self.refresh_player_view()
        except Exception:
            pass
    

    def _resolve_engine_damage_save(self, ts, *, ability: str, dc: int, mode: str = "normal", label: str = "", context: dict | None = None) -> dict:
        rules = getattr(self, "rules", None)
        if rules is None:
            rules = RulesRegistry.get("default")
        return roll_engine_save_result(
            actor=ts,
            rules=rules,
            ability_key=str(ability or ""),
            dc=int(dc),
            mode=str(mode or "normal"),
            label=str(label or "Saving Throw"),
            context=dict(context or {}),
        )

    def _maybe_apply_damage_save(self, ts, *, base_damage: int, source_payload: dict, label: str, context: dict | None = None) -> tuple[int, dict | None]:
        src = dict(source_payload or {})
        ability = normalize_ability_key(src.get("save_ability", ""))
        if not ability:
            return int(base_damage), None
        try:
            dc = int(src.get("save_dc", 10) or 10)
        except Exception:
            dc = 10
        mode = str(src.get("save_mode", "normal") or "normal")
        save_result = self._resolve_engine_damage_save(
            ts,
            ability=ability,
            dc=dc,
            mode=mode,
            label=label,
            context=dict(context or {}),
        )
        success_mode = str(src.get("save_on_success", "none") or "none")
        if bool(getattr(ts, "evasion", False)) and ability in {"dex", "dexterity"}:
            success_mode = "none" if bool(save_result.get("success", False)) else "half"
        final_damage = compute_damage_after_save(
            int(base_damage),
            bool(save_result.get("success", False)),
            success_mode,
        )
        try:
            self.campaign_logger.combat(
                "save_resolved",
                token_id=str(getattr(ts, "token_id", "") or ""),
                token_name=str(getattr(ts, "display_name", getattr(ts, "token_id", "Token")) or "Token"),
                ability=str(save_result.get("ability", ability)),
                dc=int(save_result.get("dc", dc) or dc),
                mode=str(save_result.get("mode", mode) or mode),
                d20=int(save_result.get("chosen", 0) or 0),
                modifier=int(save_result.get("modifier", 0) or 0),
                total=int(save_result.get("total", 0) or 0),
                success=bool(save_result.get("success", False)),
                label=str(label or "Saving Throw"),
                source_kind=str((context or {}).get("source_kind", "engine_auto") or "engine_auto"),
                damage_before=int(base_damage),
                damage_after=int(final_damage),
            )
        except Exception:
            pass
        return int(final_damage), save_result

    # ---------------- Hazards (B-X1) ----------------
    def _apply_hazards_for_token(self, token_id: str, *, trigger: str) -> None:
        """
        BX1: Typed hazards resolved on enter / turn_start / turn_end.
        PCs roll portal saves when save metadata exists; NPCs resolve locally.
        """
        token_id = str(token_id or "").strip()
        if not token_id:
            return

        ts = (getattr(self.state, "tokens", {}) or {}).get(token_id)
        if ts is None:
            return

        meta = getattr(self, "current_map_meta", {}) or {}
        try:
            gx = int(getattr(ts, "grid_x", 0) or 0)
            gy = int(getattr(ts, "grid_y", 0) or 0)
        except Exception:
            return

        trig_in = str(trigger or "enter").strip().lower()
        if trig_in not in ("enter", "turn_start", "turn_end"):
            trig_in = "enter"

        try:
            resolved = resolve_hazards(meta, gx, gy, trigger=trig_in)
        except Exception:
            resolved = []

        if not resolved:
            return

        for r in resolved:
            try:
                base_damage = int(r.get("damage_total", 0) or 0)
            except Exception:
                base_damage = 0
            if base_damage <= 0:
                continue

            hazard_type = str(r.get("hazard_type", "hazard") or "hazard")
            dmg_expr = str(r.get("damage_expr", "1") or "1")
            trig = str(r.get("trigger", trig_in) or trig_in)
            source_payload = dict(r.get("source", {}) or {})
            ability = normalize_ability_key(source_payload.get("save_ability", ""))
            pending_id = f"hazard:{hazard_type}:{trig}:{gx},{gy}:{dmg_expr}"

            if ability and self._is_player_controlled_token(ts):
                try:
                    dc = int(source_payload.get("save_dc", 10) or 10)
                except Exception:
                    dc = 10
                mode = str(source_payload.get("save_mode", "normal") or "normal")
                req_id = self._register_pc_deferred_damage_save_request(
                    ts,
                    ability=ability,
                    dc=dc,
                    mode=mode,
                    label=f"{hazard_type.title()} Save",
                    context={
                        "kind": "hazard_save",
                        "source_kind": "hazard",
                        "hazard_type": str(hazard_type),
                        "trigger": str(trig),
                        "gx": int(gx),
                        "gy": int(gy),
                    },
                    deferred_effect={
                        "base_damage": int(base_damage),
                        "save_on_success": str(source_payload.get("save_on_success", "none") or "none"),
                        "pending_attack_id": pending_id,
                        "source_kind": "hazard",
                        "damage_type": str(hazard_type),
                    },
                )
                if req_id:
                    try:
                        print(f"[HAZARD_SAVE_REQUEST] {getattr(ts, 'display_name', token_id)} @({gx},{gy}) trigger={trig} type={hazard_type} ability={ability} dc={dc} request_id={req_id}")
                    except Exception:
                        pass
                    continue

            dmg = int(base_damage)
            if ability:
                dmg, _save_result = self._maybe_apply_damage_save(
                    ts,
                    base_damage=int(base_damage),
                    source_payload=source_payload,
                    label=f"{hazard_type.title()} Save",
                    context={
                        "source_kind": "hazard",
                        "hazard_type": str(hazard_type),
                        "trigger": str(trig),
                        "gx": int(gx),
                        "gy": int(gy),
                    },
                )

            try:
                print(f"[HAZARD] {getattr(ts, 'display_name', token_id)} @({gx},{gy}) trigger={trig} type={hazard_type} expr={dmg_expr} -> dmg={int(dmg)}")
            except Exception:
                pass

            try:
                self.campaign_logger.event(
                    "HAZARD_TRIGGERED",
                    token_id=str(token_id),
                    token_name=getattr(ts, "display_name", token_id),
                    gx=int(gx),
                    gy=int(gy),
                    trigger=str(trig),
                    hazard_type=str(hazard_type),
                    damage_expr=str(dmg_expr),
                    damage=int(dmg),
                    rolls=r.get("rolls", []),
                    mod=int(r.get("mod", 0) or 0),
                )
            except Exception:
                pass

            try:
                self.apply_damage_to_token(ts, int(dmg), pending_attack_id=pending_id, damage_type=str(hazard_type), source_kind="hazard")
            except Exception:
                pass

    # ---------------- Runtime Clouds (B-X5.5) ----------------
    def _apply_runtime_cloud_damage_for_token(self, token_id: str, *, trigger: str) -> None:
        """Apply save-based or direct runtime cloud effects. PCs roll portal saves when configured."""
        token_id = str(token_id or "").strip()
        if not token_id:
            return

        ts = (getattr(self.state, "tokens", {}) or {}).get(token_id)
        if ts is None:
            return

        trig = str(trigger or "turn_start").strip().lower()
        if trig not in ("turn_start", "turn_end"):
            return

        try:
            tx = int(getattr(ts, "grid_x", 0) or 0)
            ty = int(getattr(ts, "grid_y", 0) or 0)
        except Exception:
            return

        zones = list(getattr(self.state, "runtime_fog_zones", []) or [])
        if not zones:
            return

        try:
            from engine.hazard_engine import roll_dice
        except Exception:
            roll_dice = None
        if roll_dice is None:
            return

        for z in zones:
            if not isinstance(z, dict):
                continue
            kind = str(z.get("kind", "cloud") or "cloud").strip().lower()
            dmg_expr = str(z.get("damage", "") or "").strip()
            if not dmg_expr:
                continue
            dmg_type = str(z.get("damage_type", kind) or kind).strip().lower()
            z_trigs = z.get("triggers", None)
            if isinstance(z_trigs, (list, tuple)) and z_trigs:
                trigs = [str(t).strip().lower() for t in z_trigs if str(t).strip()]
            else:
                trigs = [str(z.get("trigger", "turn_start") or "turn_start").strip().lower()]
            if trig not in trigs:
                continue
            try:
                cx = int(z.get("cx"))
                cy = int(z.get("cy"))
                r = int(z.get("r", 0) or 0)
            except Exception:
                continue
            if r <= 0:
                continue
            dx = tx - cx
            dy = ty - cy
            if (dx * dx + dy * dy) > (r * r):
                continue

            total, rolls, mod = roll_dice(dmg_expr)
            try:
                base_damage = int(total)
            except Exception:
                base_damage = 0
            if base_damage <= 0:
                continue

            ability = normalize_ability_key(z.get("save_ability", ""))
            pending_id = f"cloud:{kind}:{dmg_type}:{trig}:{cx},{cy}:r{r}:{dmg_expr}"
            if ability and self._is_player_controlled_token(ts):
                try:
                    dc = int(z.get("save_dc", 10) or 10)
                except Exception:
                    dc = 10
                mode = str(z.get("save_mode", "normal") or "normal")
                req_id = self._register_pc_deferred_damage_save_request(
                    ts,
                    ability=ability,
                    dc=dc,
                    mode=mode,
                    label=f"{kind.replace('_', ' ').title()} Save",
                    context={
                        "kind": "cloud_save",
                        "source_kind": "cloud",
                        "cloud_kind": str(kind),
                        "damage_type": str(dmg_type),
                        "trigger": str(trig),
                        "cx": int(cx),
                        "cy": int(cy),
                        "r": int(r),
                    },
                    deferred_effect={
                        "base_damage": int(base_damage),
                        "save_on_success": str(z.get("save_on_success", "none") or "none"),
                        "pending_attack_id": pending_id,
                        "source_kind": "cloud",
                        "damage_type": str(dmg_type),
                    },
                )
                if req_id:
                    try:
                        print(f"[CLOUD_SAVE_REQUEST] {getattr(ts, 'display_name', token_id)} @({tx},{ty}) trigger={trig} kind={kind} ability={ability} dc={dc} request_id={req_id}")
                    except Exception:
                        pass
                    continue

            dmg = int(base_damage)
            if ability:
                dmg, _save_result = self._maybe_apply_damage_save(
                    ts,
                    base_damage=int(base_damage),
                    source_payload={
                        "save_ability": ability,
                        "save_dc": z.get("save_dc", 10),
                        "save_mode": z.get("save_mode", "normal"),
                        "save_on_success": z.get("save_on_success", "none"),
                    },
                    label=f"{kind.replace('_', ' ').title()} Save",
                    context={
                        "source_kind": "cloud",
                        "cloud_kind": str(kind),
                        "damage_type": str(dmg_type),
                        "trigger": str(trig),
                        "cx": int(cx),
                        "cy": int(cy),
                        "r": int(r),
                    },
                )
            try:
                print(f"[CLOUD_DMG] {getattr(ts,'display_name',token_id)} @({tx},{ty}) trigger={trig} kind={kind} type={dmg_type} expr={dmg_expr} -> dmg={dmg}")
            except Exception:
                pass
            try:
                self.campaign_logger.event(
                    "CLOUD_DAMAGE",
                    token_id=str(token_id),
                    token_name=getattr(ts, "display_name", token_id),
                    gx=int(tx),
                    gy=int(ty),
                    trigger=str(trig),
                    cloud_kind=str(kind),
                    damage_type=str(dmg_type),
                    damage_expr=str(dmg_expr),
                    damage=int(dmg),
                    rolls=list(rolls),
                    mod=int(mod),
                    cx=int(cx),
                    cy=int(cy),
                    r=int(r),
                )
            except Exception:
                pass
            try:
                self.apply_damage_to_token(
                    ts,
                    int(dmg),
                    pending_attack_id=pending_id,
                    damage_type=str(dmg_type),
                    source_kind="cloud",
                )
            except Exception:
                pass

    def on_token_cover_override(self, tok_item, token_id: str, tier: str) -> None:
        """DM-side manual cover override. UI dispatch only; cover math remains in engine."""
        token_id = str(token_id or "").strip()
        tier = str(tier or "none").strip().lower()
        if tier not in ("none", "half", "three_quarters", "total"):
            tier = "none"

        ts = self.state.tokens.get(token_id)
        if ts is None:
            return

        try:
            setattr(ts, "cover_override", tier)
        except Exception:
            pass

        try:
            self.campaign_logger.combat(
                "token_cover_override_set",
                token_id=token_id,
                token_name=getattr(ts, "display_name", token_id),
                cover_override=tier,
            )
        except Exception:
            pass

        try:
            self.update_combat_hud()
        except Exception:
            pass
        try:
            self.refresh_player_view()
        except Exception:
            pass

    def _get_scene_token_item(self, token_id: str):
        # Prefer token_items (fast), but fall back to scanning the scene (robust)
        tok = next((t for t in self.token_items if getattr(t, "token_id", None) == token_id), None)
        if tok is not None:
            return tok

        try:
            for it in self.scene.items():
                if getattr(it, "token_id", None) == token_id:
                    return it
        except Exception:
            pass
        return None

    def _refresh_active_token_movement_overlay(self) -> None:
        if not bool(getattr(self.state, "initiative_active", False)):
            return
        active_id = getattr(self.state, "active_token_id", None)
        if not active_id or active_id not in self.state.tokens:
            return

        ts = self.state.tokens[active_id]

        # Find the actual scene token
        tok = next((t for t in self.token_items if getattr(t, "token_id", None) == active_id), None)
        if tok is None:
            return

        # Clear movement overlays from all tokens (movement only; keep attack overlays if you want)
        for it in list(self.token_items):
            try:
                it.hide_movement_range(self.scene)
            except Exception:
                pass
        # Set movement to remaining and draw (render-only).
        tok.movement = int(ts.movement_remaining) if ts.movement_remaining is not None else 0

        try:
            # Compute reachable cells using engine rules (blocked + difficult terrain).
            scene_rect = self.scene.sceneRect()
            cols = int(scene_rect.width() // self.grid_size)
            rows = int(scene_rect.height() // self.grid_size)
            meta = getattr(self, "current_map_meta", {}) or {}
            reachable = compute_reachable_cells(
                start_x=int(getattr(ts, "grid_x", 0) or 0),
                start_y=int(getattr(ts, "grid_y", 0) or 0),
                move_ft=int(tok.movement),
                cols=int(cols),
                rows=int(rows),
                meta=meta,
                feet_per_square=5,
            )
        except Exception:
            reachable = None

        tok.show_movement_range(self.scene, reachable_cells=reachable)


    
    def _close_rest_status_dialog(self):
        dlg = getattr(self, "_rest_status_dialog", None)
        timer = getattr(self, "_rest_status_timer", None)
        self._rest_status_dialog = None
        self._rest_status_label = None
        self._rest_status_timer = None
        try:
            if timer:
                timer.stop()
        except Exception:
            pass
        try:
            if dlg:
                dlg.close()
        except Exception:
            pass

    def _refresh_rest_status_dialog(self):
        dlg = getattr(self, "_rest_status_dialog", None)
        label = getattr(self, "_rest_status_label", None)
        if dlg is None or label is None or not getattr(self, "server_client", None):
            return
        data = self.server_client.rest_control_status() or {}
        if not data or not data.get("active") or str(data.get("rest_type") or "") != "short_rest":
            label.setText("No active short rest.")
            return
        participants = data.get("participants") if isinstance(data.get("participants"), list) else []
        lines = ["Short Rest Status", ""]
        for p in participants:
            cid = str((p or {}).get("character_id") or "")
            done = bool((p or {}).get("done", False))
            lines.append(f"{'✔' if done else '✖'} {cid} — {'Done' if done else 'Not done'}")
        lines.append("")
        lines.append(f"Done: {int(data.get('done_count') or 0)}/{int(data.get('participant_count') or 0)}")
        label.setText("\n".join(lines))

    def _open_short_rest_status_dialog(self):
        self._close_rest_status_dialog()
        dlg = QDialog(self)
        dlg.setWindowTitle("Short Rest Status")
        dlg.setModal(False)
        layout = QVBoxLayout(dlg)
        label = QLabel(dlg)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        label.setMinimumWidth(280)
        layout.addWidget(label)
        dlg.setLayout(layout)
        self._rest_status_dialog = dlg
        self._rest_status_label = label
        timer = QTimer(self)
        timer.setInterval(1500)
        timer.timeout.connect(self._refresh_rest_status_dialog)
        self._rest_status_timer = timer
        self._refresh_rest_status_dialog()
        timer.start()
        dlg.show()

    def _show_rest_control_result(self, result: dict, *, success_text: str, failure_text: str):
        if result and result.get("ok"):
            extra = ""
            if result.get("participant_count") is not None:
                extra = f" ({int(result.get('participant_count') or 0)} participants)"
            elif isinstance(result.get("participants"), list):
                extra = f" ({len(result.get('participants') or [])} participants)"
            self._set_hud_status(success_text + extra, hold_sec=4.0)
        else:
            self._set_hud_status(failure_text, hold_sec=4.0)

    def start_short_rest(self):
        if not getattr(self, "server_client", None):
            self._set_hud_status("No server client configured for short rest.", hold_sec=4.0)
            return
        result = self.server_client.rest_control_start("short_rest")
        self._show_rest_control_result(result, success_text="Short rest started", failure_text="Failed to start short rest")
        if result and result.get("ok"):
            self._open_short_rest_status_dialog()
        try:
            self.campaign_logger.event("SHORT_REST_START", participants=result.get("participants", []))
        except Exception:
            pass

    def resolve_short_rest(self):
        if not getattr(self, "server_client", None):
            self._set_hud_status("No server client configured for short rest.", hold_sec=4.0)
            return
        result = self.server_client.rest_control_resolve("short_rest")
        self._show_rest_control_result(result, success_text="Short rest resolved", failure_text="Failed to resolve short rest")
        if result and result.get("ok"):
            self._close_rest_status_dialog()
        try:
            self.campaign_logger.event("SHORT_REST_RESOLVE", participants=result.get("participants", []))
        except Exception:
            pass

    def cancel_short_rest(self):
        if not getattr(self, "server_client", None):
            self._set_hud_status("No server client configured for short rest.", hold_sec=4.0)
            return
        result = self.server_client.rest_control_cancel("short_rest")
        self._show_rest_control_result(result, success_text="Short rest cancelled", failure_text="Failed to cancel short rest")
        if result and result.get("ok"):
            self._close_rest_status_dialog()
        try:
            self.campaign_logger.event("SHORT_REST_CANCEL")
        except Exception:
            pass

    def start_long_rest(self):
        if not getattr(self, "server_client", None):
            self._set_hud_status("No server client configured for long rest.", hold_sec=4.0)
            return
        result = self.server_client.rest_control_start("long_rest")
        self._show_rest_control_result(result, success_text="Long rest started", failure_text="Failed to start long rest")
        try:
            self.campaign_logger.event("LONG_REST_START", participants=result.get("participants", []))
        except Exception:
            pass

    def resolve_long_rest(self):
        if not getattr(self, "server_client", None):
            self._set_hud_status("No server client configured for long rest.", hold_sec=4.0)
            return
        result = self.server_client.rest_control_resolve("long_rest")
        self._show_rest_control_result(result, success_text="Long rest resolved", failure_text="Failed to resolve long rest")
        if result and result.get("ok"):
            try:
                self.sync_pc_sheets_now()
            except Exception:
                pass
        try:
            self.campaign_logger.event("LONG_REST_RESOLVE", participants=result.get("participants", []))
        except Exception:
            pass

    def cancel_long_rest(self):
        if not getattr(self, "server_client", None):
            self._set_hud_status("No server client configured for long rest.", hold_sec=4.0)
            return
        result = self.server_client.rest_control_cancel("long_rest")
        self._show_rest_control_result(result, success_text="Long rest cancelled", failure_text="Failed to cancel long rest")
        try:
            self.campaign_logger.event("LONG_REST_CANCEL")
        except Exception:
            pass

    def grant_party_level_up(self):
        if not getattr(self, "server_client", None):
            self._set_hud_status("No server client configured for level up.", hold_sec=4.0)
            return
        result = self.server_client.levelup_grant_party()
        if result and result.get("ok"):
            self._set_hud_status(f"Granted level up to {int(result.get('count', 0) or 0)} active character(s).", hold_sec=4.0)
        else:
            self._set_hud_status("Failed to grant party level up.", hold_sec=4.0)

    def grant_selected_pc_level_up(self):
        if not getattr(self, "server_client", None):
            self._set_hud_status("No server client configured for level up.", hold_sec=4.0)
            return

        selected_character_id = ""
        ts = self.get_selected_tokenstate()
        if ts and self._is_player_controlled_token(ts):
            selected_character_id = str(getattr(ts, "character_id", "") or "").strip()

        data = self.server_client.levelup_active_characters() or {}
        chars = data.get("characters") if isinstance(data.get("characters"), list) else []
        if not chars:
            self._set_hud_status("No active player characters are online for level up.", hold_sec=4.0)
            return

        labels = []
        mapping = {}
        default_index = 0
        for i, row in enumerate(chars):
            cid = str((row or {}).get("character_id") or "").strip()
            name = str((row or {}).get("display_name") or cid).strip()
            klass = str((row or {}).get("class_name") or "").strip()
            level = int((row or {}).get("level") or 1)
            player_id = str((row or {}).get("player_id") or "").strip()
            label = f"{name} [{cid}]"
            if klass:
                label += f" — L{level} {klass}"
            if player_id:
                label += f" — {player_id}"
            labels.append(label)
            mapping[label] = cid
            if selected_character_id and cid == selected_character_id:
                default_index = i

        choice, ok = QInputDialog.getItem(
            self,
            "Grant Selected PC Level Up",
            "Choose an active player character:",
            labels,
            default_index,
            False,
        )
        if not ok or not choice:
            return

        character_id = str(mapping.get(choice) or "").strip()
        if not character_id:
            self._set_hud_status("No character selected for level up.", hold_sec=4.0)
            return

        result = self.server_client.levelup_grant_character(character_id)
        if result and result.get("ok"):
            self._set_hud_status(f"Granted level up to {character_id}.", hold_sec=4.0)
        else:
            self._set_hud_status("Failed to grant selected PC level up.", hold_sec=4.0)

    def _enforce_active_turn(self, token_id: str, *, action_name: str) -> bool:
        """
        Returns True if allowed, False if blocked.
        Used by movement + attack arming + auto-resolve.
        """
        try:
            if not bool(getattr(self.state, "initiative_active", False)):
                return True
            active = getattr(self.state, "active_token_id", None)
            if not active:
                return True
            if str(token_id) == str(active):
                return True

            active_name = active
            try:
                if active in self.state.tokens:
                    active_name = self.state.tokens[active].display_name
            except Exception:
                pass

            self._set_hud_status(f"Blocked: {action_name} is only allowed on active turn ({active_name}).")
            self.update_combat_hud()
            return False
        except Exception:
            # In doubt, allow (prevents hard-locking the UI due to edge errors)
            return True
        
    def _enforce_action_available(self, token_id: str, *, action_name: str) -> bool:
        """
        Phase 5 decision: NO action-per-turn limit.
        Always allow actions; turn gating (active turn) is handled elsewhere.
        """
        return True

    def _consume_action(self, token_id: str, *, action_name: str) -> None:
        """
        Phase 5 decision: NO action-per-turn limit.
        No-op, but kept for call-site compatibility.
        """
        return

    def on_turn_start(self, token_id: str) -> None:
        """
        Deterministic turn start:
        - reset per-turn movement remaining
        - reset per-turn action usage
        - process start-of-turn engine-owned condition hooks
        """
        if not token_id or token_id not in self.state.tokens:
            return

        ts = self.state.tokens.get(token_id)
        if not ts:
            return

        ts.has_acted_this_turn = False

        try:
            mv = int(getattr(ts, "movement", 30) or 30)
        except Exception:
            mv = 30

        ts.base_movement = mv
        ts.movement_remaining = self._effective_speed_for_token(ts)

        self._process_condition_turn_hook(token_id, timing="start")
        self._maybe_open_death_save_for_token(token_id)

        try:
            self.campaign_logger.combat(
                "turn_start_state",
                token_id=token_id,
                name=getattr(ts, "display_name", token_id),
                movement_remaining=int(ts.movement_remaining),
                has_acted=bool(ts.has_acted_this_turn),
                round=int(getattr(self.state, "round_number", 1) or 1),
                statuses=list(getattr(ts, "statuses", []) or []),
            )
        except Exception:
            pass

    def ui_end_initiative_encounter(self) -> None:
        """
        Back-compat alias for InitiativePanel wiring.
        Some refactors renamed the underlying method; keep this stable.
        """
        # If you already have the real implementation under a different name, forward to it.
        fn = getattr(self, "ui_end_initiative", None)
        if callable(fn):
            fn()
            return

        fn = getattr(self, "ui_end_encounter", None)
        if callable(fn):
            fn()
            return

        # Fallback: implement hard reset inline (safe)
        self.state.initiative_active = False
        self.state.initiative_order = []
        self.state.initiative_values = {}
        self.state.current_turn_index = 0
        self.state.round_number = 1
        self.state.active_token_id = None

        self.state.pending_attack = None
        self.state.pending_damage = None

        self._set_hud_status("Initiative ended (free play).")
        try:
            self._update_active_token_highlight()
            self.scene.update()
        except Exception:
            pass

        try:
            self.update_combat_hud()
        except Exception:
            pass
        try:
            self.update_initiative_panel()
        except Exception:
            pass

    def ui_heal_selected_pc_to_full(self, tok_item=None) -> None:
        """
        ADMIN/TEST TOOL: Heal a token to full immediately.

        Behavior:
        - If token is sheet-backed (has character_id), the SERVER is authoritative:
            1) read sheet to determine max_hp
            2) POST /set_hp with current_hp=max_hp (temp_hp=0)
            3) sync TokenState (hp/max_hp) from server response
        - If token is not sheet-backed, heal locally in EncounterState only.
        """
        # ---- Resolve token item (prefer right-clicked tok_item) ----
        tok = tok_item
        if tok is None:
            try:
                tok = self._get_selected_token_item() if hasattr(self, "_get_selected_token_item") else None
            except Exception:
                tok = None

        if tok is None:
            self._set_hud_status("No token selected.")
            self.update_combat_hud()
            return

        token_id = (getattr(tok, "token_id", "") or "").strip()
        if not token_id:
            self._set_hud_status("Selected token has no token_id.")
            self.update_combat_hud()
            return

        ts = getattr(self, "state", None) and self.state.tokens.get(token_id)
        if not ts:
            self._set_hud_status("Selected token missing in state.")
            self.update_combat_hud()
            return

        char_id = (getattr(ts, "character_id", "") or "").strip()

        # ---- Sheet-backed: SERVER AUTHORITATIVE ----
        if char_id:
            if not getattr(self, "server_client", None):
                self._set_hud_status("No server_client configured; cannot heal sheet-backed token.")
                self.update_combat_hud()
                return

            # Pull sheet so we don't rely on possibly-stale TokenState defaults.
            try:
                sheet = self.server_client.get_character_sheet(char_id) or {}
            except Exception as e:
                print("[DM][HEAL] get_character_sheet exception:", e)
                self._set_hud_status("Heal failed (could not read sheet).")
                self.update_combat_hud()
                return

            # Determine max_hp from likely schema locations (resources first).
            mx = None
            try:
                resources = sheet.get("resources", {}) or {}
                mx = resources.get("max_hp", None)
                if mx is None:
                    # optional fallbacks if you have different schemas
                    mx = (sheet.get("stats", {}) or {}).get("max_hp", None)
                if mx is None:
                    mx = (sheet.get("base_stats", {}) or {}).get("max_hp", None)
                if mx is None:
                    mx = getattr(ts, "max_hp", 10)
                mx = max(1, int(mx))
            except Exception:
                mx = max(1, int(getattr(ts, "max_hp", 10) or 10))

            try:
                j = self.server_client.set_character_hp(
                    char_id,
                    current_hp=mx,          # IMPORTANT: keyword-only
                    temp_hp=0,
                    token_id=token_id,
                    encounter_id=getattr(self.state, "encounter_id", "") or "",
                    reason="dm_admin_heal_full",
                )
            except Exception as e:
                print("[DM][HEAL] set_character_hp exception:", e)
                self._set_hud_status("Heal failed (server error).")
                self.update_combat_hud()
                return

            if not (isinstance(j, dict) and j.get("ok")):
                print(f"[DM][HEAL] ADMIN set_hp failed: {j!r}")
                self._set_hud_status("Heal failed (server rejected).")
                self.update_combat_hud()
                return

            # Sync local TokenState from server response
            try:
                ts.max_hp = int(j.get("max_hp", mx) or mx)
                ts.hp = int(j.get("current_hp", mx) or mx)
                ts.hp = max(0, min(ts.hp, ts.max_hp))
            except Exception:
                ts.max_hp = mx
                ts.hp = mx

            print(f"[DM][HEAL] ADMIN sheet {char_id} -> {ts.hp}/{ts.max_hp}")
            try:
                self.campaign_logger.combat(
                    "dm_admin_heal_full",
                    token_id=token_id,
                    character_id=char_id,
                    hp=int(ts.hp),
                    max_hp=int(ts.max_hp),
                )
            except Exception:
                pass

            # Optional verify (only for sheet-backed)
            try:
                verify = self.server_client.get_character_sheet(char_id) or {}
                vhp = ((verify.get("resources", {}) or {}).get("current_hp"))
                print(f"[DM][HEAL][VERIFY] {char_id} resources.current_hp={vhp}")
            except Exception:
                pass

        # ---- Not sheet-backed: LOCAL ONLY ----
        else:
            max_hp_local = max(1, int(getattr(ts, "max_hp", 10) or 10))
            ts.max_hp = max_hp_local
            ts.hp = max_hp_local
            print(f"[DM][HEAL] local token {token_id} -> {ts.hp}/{ts.max_hp}")
            try:
                self.campaign_logger.combat(
                    "dm_heal_full_local",
                    token_id=token_id,
                    hp=int(ts.hp),
                    max_hp=int(ts.max_hp),
                )
            except Exception:
                pass

        # ---- Push state -> scene + repaint ----
        try:
            self.apply_state_hp_to_scene_token(token_id)
        except Exception as e:
            print("[DM][HEAL] apply_state_hp_to_scene_token failed:", e)

        try:
            self.refresh_player_view()
        except Exception:
            pass

        self._set_hud_status("ADMIN heal to full applied.")
        self.update_combat_hud()
    
    def ui_heal_token_to_full(self, tok_item) -> None:
        """
        DM convenience (LOCAL): set this token's hp to max_hp in EncounterState,
        then push to the scene token + player view.

        This does NOT modify the server character sheet.
        """
        if tok_item is None:
            self._set_hud_status("No token.")
            self.update_combat_hud()
            return

        token_id = getattr(tok_item, "token_id", "") or ""
        ts = self.state.tokens.get(token_id)
        if not ts:
            self._set_hud_status("Token missing in state.")
            self.update_combat_hud()
            return

        max_hp = int(getattr(ts, "max_hp", 10) or 10)
        ts.hp = max_hp

        # Push into scene + repaint
        try:
            self.apply_state_hp_to_scene_token(token_id)
        except Exception:
            pass

        try:
            self.refresh_player_view()
        except Exception:
            pass

        try:
            self.campaign_logger.combat("dm_heal_full", token_id=token_id, name=getattr(ts, "display_name", token_id), hp=max_hp)
        except Exception:
            pass

        self._set_hud_status(f"Healed {getattr(ts, 'display_name', 'token')} to full ({max_hp}).", hold_sec=2.5)
        self.update_combat_hud()

    def on_turn_start(self, token_id: str) -> None:
        """
        Deterministic turn start:
        - reset per-turn movement remaining
        - reset per-turn action usage
        - process start-of-turn engine-owned condition hooks
        """
        if not token_id or token_id not in self.state.tokens:
            return

        ts = self.state.tokens.get(token_id)
        if not ts:
            return

        ts.has_acted_this_turn = False

        try:
            mv = int(getattr(ts, "movement", 30) or 30)
        except Exception:
            mv = 30
        ts.base_movement = mv
        ts.movement_remaining = self._effective_speed_for_token(ts)

        self._process_condition_turn_hook(token_id, timing="start")
        self._maybe_open_death_save_for_token(token_id)

        try:
            self.campaign_logger.combat(
                "turn_start_state",
                token_id=token_id,
                name=getattr(ts, "display_name", token_id),
                movement_remaining=int(ts.movement_remaining),
                has_acted=bool(ts.has_acted_this_turn),
                round=int(getattr(self.state, "round_number", 1) or 1),
                statuses=list(getattr(ts, "statuses", []) or []),
            )
        except Exception:
            pass

    def dm_heal_token_to_full(self, token_id: str) -> None:
        ts = self.state.tokens.get(token_id)
        if not ts:
            return

        # local / NPC
        if getattr(ts, "stat_source", "") != "character_sheet" or not getattr(ts, "character_id", ""):
            ts.hp = int(getattr(ts, "max_hp", 10) or 10)
            self.apply_state_hp_to_scene_token(token_id)
            self.refresh_player_view()
            return

        # sheet-backed: heal via server by sending negative damage
        try:
            cur = int(getattr(ts, "hp", 0) or 0)
            mx = int(getattr(ts, "max_hp", 10) or 10)
            delta = mx - cur
            if delta <= 0:
                return

            j = self.server_client.apply_damage_to_character(ts.character_id, -delta, token_id=token_id)
            print("[HEAL][SERVER]", ts.character_id, "->", j)

            # sync back into TokenState from server response
            if isinstance(j, dict):
                hp = j.get("current_hp")
                mx2 = j.get("max_hp")
                if hp is not None:
                    ts.hp = int(hp)
                if mx2 is not None:
                    ts.max_hp = int(mx2)

            self.apply_state_hp_to_scene_token(token_id)
            self.refresh_player_view()
        except Exception as e:
            print("[HEAL] failed:", e)

    def _on_map_meta_dock_visibility_changed(self, visible: bool):
        # When hidden: disable painting so tokens can be selected/moved.
        # When shown: only enable if the checkbox is checked.
        try:
            if self.map_metadata_editor is None:
                return
            if not visible:
                self.map_metadata_editor.enable_on_map(False)
                return
            # visible = True
            self.map_metadata_editor.enable_on_map(self.map_metadata_editor.is_painting_enabled())
        except Exception:
            pass

    def _ensure_runtime_cloud_store(self) -> list:
        """Return the authoritative runtime cloud list stored on EncounterState."""
        if not hasattr(self.state, "runtime_fog_zones") or self.state.runtime_fog_zones is None:
            self.state.runtime_fog_zones = []
        if not isinstance(self.state.runtime_fog_zones, list):
            self.state.runtime_fog_zones = []
        return self.state.runtime_fog_zones


    def _spawn_runtime_cloud(self, *, kind: str, cx: int, cy: int, r: int, density: float, ttl_turns: int, extra: Optional[Dict[str, Any]] = None) -> None:
        zones = self._ensure_runtime_cloud_store()
        z = {
            "kind": str(kind),
            "cx": int(cx),
            "cy": int(cy),
            "r": int(r),
            "density": float(density),
            "ttl_turns": int(ttl_turns),
        }
        if isinstance(extra, dict) and extra:
            try:
                z.update(extra)
            except Exception:
                pass
        zones.append(z)

        try:
            print(f"[CLOUD] Spawned {kind} @({cx},{cy}) r={r} ttl={ttl_turns}")
        except Exception:
            pass

        try:
            self.campaign_logger.event("CLOUD_SPAWN", kind=str(kind), cx=int(cx), cy=int(cy), r=int(r), ttl=int(ttl_turns))
        except Exception:
            pass

        # Push updates visually
        try:
            self.refresh_player_view()
        except Exception:
            pass
        try:
            self.redraw_overlays()
        except Exception:
            pass

    def _clear_runtime_cloud_overlays(self) -> None:
        items = getattr(self, "_runtime_cloud_overlay_items", None)
        if not items:
            self._runtime_cloud_overlay_items = []
            return
        try:
            for it in list(items):
                try:
                    self.scene.removeItem(it)
                except Exception:
                    pass
        finally:
            self._runtime_cloud_overlay_items = []


    def _redraw_runtime_cloud_overlays(self) -> None:
        """
        DM-only visualization of runtime fog zones (clouds) on the main scene.
        This is purely a render layer; state remains in EncounterState.runtime_fog_zones.
        """
        try:
            from PyQt5.QtGui import QPen, QColor, QBrush
            from PyQt5.QtWidgets import QGraphicsEllipseItem
        except Exception:
            return

        self._clear_runtime_cloud_overlays()

        zones = getattr(self.state, "runtime_fog_zones", []) or []
        if not isinstance(zones, list) or not zones:
            return

        try:
            from ui.constants import GRID_SIZE
        except Exception:
            GRID_SIZE = 64

        overlay_items = []
        for z in zones:
            if not isinstance(z, dict):
                continue
            try:
                cx = int(z.get("cx"))
                cy = int(z.get("cy"))
                r = int(z.get("r", 3) or 3)
            except Exception:
                continue

            kind = str(z.get("kind", "cloud") or "cloud").lower().strip()
            ttl = z.get("ttl_turns", None)

            # Color scheme (DM only): distinct but not blinding
            if kind == "smoke":
                pen = QPen(QColor(160, 160, 160, 180))
                brush = QBrush(QColor(120, 120, 120, 50))
            elif kind == "gas":
                pen = QPen(QColor(120, 200, 120, 180))
                brush = QBrush(QColor(80, 170, 80, 45))
            else:
                pen = QPen(QColor(200, 200, 80, 180))
                brush = QBrush(QColor(170, 170, 60, 45))

            # Ellipse in scene coords. Center at cell center, radius in cells.
            # r is in cells.
            radius_px = float(r) * float(GRID_SIZE)
            center_x = (cx + 0.5) * float(GRID_SIZE)
            center_y = (cy + 0.5) * float(GRID_SIZE)

            it = QGraphicsEllipseItem(
                center_x - radius_px,
                center_y - radius_px,
                2.0 * radius_px,
                2.0 * radius_px,
            )
            it.setPen(pen)
            it.setBrush(brush)
            it.setZValue(65)  # above terrain overlays, below token UI if you want

            # Tooltip for confidence + TTL
            try:
                tip = f"{kind} r={r}"
                if ttl is not None:
                    tip += f" ttl={ttl}"
                it.setToolTip(tip)
            except Exception:
                pass

            overlay_items.append(it)
            try:
                self.scene.addItem(it)
            except Exception:
                pass

        self._runtime_cloud_overlay_items = overlay_items

    # ------------------------------------------------------------
    # Phase C9: Sheet-backed token hydration (server-authoritative)
    # ------------------------------------------------------------
    def _start_sheet_sync_timer(self) -> None:
        try:
            # Poll every 2s; server client is lightweight and cache-aware.
            self._sheet_sync_timer = QTimer(self)
            self._sheet_sync_timer.setInterval(2000)
            self._sheet_sync_timer.timeout.connect(self._sheet_sync_tick)
            self._sheet_sync_timer.start()
        except Exception as e:
            print("[SHEET_SYNC] Failed to start timer:", e)

    def _sheet_sync_tick(self) -> None:
        try:
            if not getattr(self, "state", None) or not getattr(self, "server_client", None):
                return
            considered, updated = sync_sheet_backed_tokens(self.state, self.server_client, only_if_changed=True)
            if not updated:
                return
            # ---- Propagate updated TokenState -> scene token items (Phase C10 stabilization) ----
            # The sync service mutates EncounterState.tokens, but scene token items may hold stale
            # cached fields (weapon ids, hp bars, overlays). Keep the scene in sync cheaply.
            try:
                for token_id, ts in (getattr(self.state, "tokens", {}) or {}).items():
                    if getattr(ts, "stat_source", "") != "character_sheet":
                        continue
                    if not getattr(ts, "character_id", ""):
                        continue

                    item = self._get_scene_token_item(str(token_id))
                    if item is None:
                        continue

                    prev_weapon = str(getattr(item, "weapon_id", "") or getattr(item, "weapon", "") or "").strip()
                    new_weapon = str(getattr(ts, "weapon_id", "") or getattr(ts, "weapon", "") or "").strip()

                    # Core visuals + combat fields
                    item.hp = int(getattr(ts, "hp", getattr(item, "hp", 0)) or 0)
                    item.max_hp = int(getattr(ts, "max_hp", getattr(item, "max_hp", 10)) or 10)
                    item.ac = int(getattr(ts, "ac", getattr(item, "ac", 10)) or 10)
                    item.attack_modifier = int(getattr(ts, "attack_modifier", getattr(item, "attack_modifier", 0)) or 0)
                    item.vision_ft = int(getattr(ts, "vision_ft", getattr(item, "vision_ft", 60)) or 60)
                    item.side = getattr(ts, "side", getattr(item, "side", "enemy"))

                    new_wid = str(getattr(ts, "weapon_id", "") or "")
                    new_aid = str(getattr(ts, "armor_id", "") or "")
                    # Avoid clobbering a valid scene loadout if the sheet payload is missing equipment ids
                    item.weapon_id = new_wid
                    item.armor_id = new_aid
                    # Legacy mirrors (some UI/engine paths still read these)
                    new_w = str(getattr(ts, "weapon", "") or "")
                    new_a = str(getattr(ts, "armor", "") or "")
                    item.weapon = new_w or new_wid
                    item.armor = new_a or new_aid

                    try:
                        item.update_hp_bar()
                    except Exception:
                        pass

                    # If equipment changed, invalidate cached overlays
                    if prev_weapon != new_weapon:
                        try:
                            item._cached_weapon_data = None
                        except Exception:
                            pass
                        try:
                            sc = item.scene()
                            if sc is not None:
                                item.hide_attack_range(sc)
                        except Exception:
                            pass

                        # If currently selected, redraw attack range immediately
                        try:
                            if item.isSelected():
                                weapon_data = self.get_weapon_data(new_weapon)
                                if weapon_data:
                                    item._cached_weapon_data = weapon_data
                                    item.show_attack_range(self.scene, weapon_data)
                        except Exception:
                            pass
            except Exception:
                pass

            # Light UI refresh (do not force expensive redraws)
            try:
                if getattr(self, "encounter_window", None):
                    self.encounter_window.refresh_token_list()
            except Exception:
                pass
            try:
                self.refresh_player_view()
            except Exception:
                pass
        except Exception as e:
            print("[SHEET_SYNC] tick error:", e)
        print(f"[SHEET_SYNC] considered={considered} updated={updated}")
        for token_id, ts in (getattr(self.state, "tokens", {}) or {}).items():
            if getattr(ts, "stat_source", "") == "character_sheet":
                print("[SHEET_SYNC_TOKEN]", token_id, getattr(ts, "character_id", ""), getattr(ts, "weapon_id", ""))

            
    def sync_pc_sheets_now(self) -> None:
        try:
            if not getattr(self, "state", None) or not getattr(self, "server_client", None):
                return
            considered, updated = sync_sheet_backed_tokens(self.state, self.server_client, only_if_changed=False)
            print(f"[SHEET_SYNC] manual: considered={considered} updated={updated}")
            try:
                if getattr(self, "encounter_window", None):
                    self.encounter_window.refresh_token_list()
            except Exception:
                pass
        except Exception as e:
            print("[SHEET_SYNC] manual error:", e)

    def _normalize_weapon_record(self, w: dict) -> dict:
        if not isinstance(w, dict):
            return {}

        out = dict(w)

        wtype = str(
            out.get("type")
            or out.get("weapon_type")
            or ""
        ).strip().lower()

        if not wtype:
            props = [str(x).strip().lower() for x in (out.get("properties") or [])]
            if "melee" in props:
                wtype = "melee"
            elif "ranged" in props:
                wtype = "ranged"

        if wtype not in ("melee", "ranged"):
            wtype = "melee"

        raw_range = out.get("range_ft", out.get("range", None))
        try:
            rng = int(raw_range)
        except Exception:
            rng = 5 if wtype == "melee" else 30

        out["type"] = wtype
        out["range_ft"] = rng
        out["range"] = rng
        return out