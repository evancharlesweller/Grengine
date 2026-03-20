# ui/encounter_state.py
from dataclasses import dataclass, field
from typing import Dict, Optional, Set, Tuple, Literal, List, Any

Side = Literal["player", "enemy", "ally", "neutral"]
DeathState = Literal["alive", "down", "stable", "dead"]

@dataclass
class TokenState:
    token_id: str
    display_name: str
    image_relpath: str
    grid_x: int
    grid_y: int

    template_id: str = ""

    hp: int = 10
    max_hp: int = 10

    # Death / dying state
    dead_image_relpath: str = ""   # optional per-token override (relative to campaign)
    death_state: str = "alive"     # "alive" | "down" | "stable" | "dead"
    death_save_successes: int = 0
    death_save_failures: int = 0

    ac: int = 10

    # Preferred id fields
    weapon_id: str = ""
    armor_id: str = ""

    # Legacy / compatibility (may store id OR name depending on current code)
    weapon: str = ""
    armor: str = ""

    movement: int = 30
    base_movement: int = 30
    movement_remaining: Optional[int] = None
    attack_modifier: int = 0
    side: Side = "enemy"

    abilities: Dict[str, int] = field(default_factory=dict)      # e.g. {"DEX": 16}
    proficiency_bonus: int = 0                                   # e.g. 2
    save_proficiencies: list[str] = field(default_factory=list)  # e.g. ["DEX","WIS"]

    kind: str = "npc"
    player_id: str = ""
    character_id: str = ""
    stat_source: str = "template"

    vision_ft: int = 60

    # B-X4: Vision Types / senses (5e-aligned; optional)
    # vision_type is informational; perception_engine reads explicit range fields.
    vision_type: str = "normal"  # normal|darkvision|blindsight|truesight|tremorsense|devils_sight
    darkvision_ft: int = 0
    blindsight_ft: int = 0
    truesight_ft: int = 0
    tremorsense_ft: int = 0
    devils_sight_ft: int = 0

    has_acted_this_turn: bool = False
    movement_remaining: int = 0
    base_movement: int = 30

    # --- Phase 3: Initiative ---
    initiative: Optional[int] = None          # rolled total (d20 + mod)
    initiative_modifier: int = 0              # v1 default 0; wire later if you add DEX/mod fields
    has_acted_this_turn: bool = False         # turn-gating helper

    statuses: list[dict] = field(default_factory=list)

    # Phase D3: typed damage support
    damage_resistances: list[str] = field(default_factory=list)
    damage_vulnerabilities: list[str] = field(default_factory=list)
    damage_immunities: list[str] = field(default_factory=list)
    damage_profile: Dict[str, Any] = field(default_factory=dict)

    # Phase D6: reaction economy
    reaction_available: bool = True

    def __post_init__(self):
        # Back-compat: if not explicitly set, derive from movement
        if not getattr(self, "base_movement", 0):
            self.base_movement = int(getattr(self, "movement", 0) or 0)

        if not hasattr(self, "movement_remaining") or self.movement_remaining is None:
            self.movement_remaining = int(self.base_movement or 0)

@dataclass
class EncounterState:
    campaign_path: str
    map_relpath: Optional[str] = None
    tokens: Dict[str, TokenState] = field(default_factory=dict)
    fog_explored: Set[Tuple[int, int]] = field(default_factory=set)
    overlay: Optional[dict] = None

    # BX2: door_id -> is_open (encounter override). If missing, fall back to meta default.
    door_state: Dict[str, bool] = field(default_factory=dict)

    # BX5.2: runtime mediums (e.g., smoke/gas clouds) spawned during encounter
    runtime_fog_zones: List[Dict[str, Any]] = field(default_factory=list)

    pending_attack: Optional[dict] = None  # per-encounter
    # NOTE: pending_damage is currently injected dynamically by MainWindow; leaving as-is.

    # --- Phase 3: Initiative / Turn System ---
    initiative_active: bool = False
    initiative_order: list[str] = field(default_factory=list)         # token_ids sorted
    initiative_values: Dict[str, int] = field(default_factory=dict)   # token_id -> initiative total
    current_turn_index: int = 0
    round_number: int = 1
    active_token_id: Optional[str] = None

# ui/encounter_state.py

def ensure_movement_initialized(ts, *, feet_per_square: int = 5) -> None:
    if getattr(ts, "movement_remaining", None) is None:
        ts.movement_remaining = int(getattr(ts, "movement", 30) or 30)
    if not getattr(ts, "base_movement", None):
        ts.base_movement = int(getattr(ts, "movement", 30) or 30)

def reset_movement_for_turn(ts: "TokenState") -> None:
    ts.movement_remaining = int(ts.base_movement or 0)

def spend_movement(ts: "TokenState", squares: int, feet_per_square: int = 5) -> None:
    ensure_movement_initialized(ts)
    cost_ft = int(squares) * int(feet_per_square)
    ts.movement_remaining = max(0, int(ts.movement_remaining) - cost_ft)