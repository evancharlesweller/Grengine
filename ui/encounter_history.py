# ui/encounter_history.py
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, List, Optional
import copy


Event = Dict[str, Any]


class EncounterHistory:
    """
    Deterministic history:
      current_state = base_snapshot + replay(events[:cursor])

    v1 event types supported:
      - SET_POSITION
      - APPLY_DAMAGE
      - SET_ACTIVE_TURN (optional for later wiring)
    """

    def __init__(self) -> None:
        self.base_snapshot: Optional[Dict[str, Any]] = None
        self.events: List[Event] = []
        self.cursor: int = 0  # number of events applied

        # --- Phase 5.1: turn-boundary checkpoints (fast rewind) ---
        # Each checkpoint is (cursor_index, snapshot_dict).
        # A checkpoint snapshot represents the encounter state AFTER applying
        # all events up to that cursor.
        self.checkpoints: List[tuple[int, Dict[str, Any]]] = []

    def clear(self) -> None:
        self.base_snapshot = None
        self.events = []
        self.cursor = 0
        self.checkpoints = []

    def capture_base(self, state: Any) -> None:
        """
        Stores a deep-copy snapshot of the encounter state (dataclass-friendly).
        """
        self.base_snapshot = _snapshot_state(state)
        self.events = []
        self.cursor = 0
        self.checkpoints = []

    def capture_checkpoint(self, state: Any) -> None:
        """Capture a checkpoint for the *current* state at the *current* cursor."""
        if self.base_snapshot is None:
            return
        cur = int(self.cursor)
        # De-dupe: only keep the latest checkpoint at a given cursor
        if self.checkpoints and self.checkpoints[-1][0] == cur:
            self.checkpoints[-1] = (cur, _snapshot_state(state))
            return
        self.checkpoints.append((cur, _snapshot_state(state)))

    def append(self, event: Event, *, advance_cursor: bool = True) -> None:
        # If user rewound and then does a new action, truncate future history
        if self.cursor < len(self.events):
            self.events = self.events[: self.cursor]
            # Drop checkpoints that point into the truncated future
            self.checkpoints = [(c, s) for (c, s) in self.checkpoints if c <= self.cursor]
        self.events.append(copy.deepcopy(event))
        if advance_cursor:
            self.cursor = len(self.events)

    def replay_to(self, state: Any, cursor: int) -> None:
        """
        Mutates `state` in-place to match base + events[:cursor].
        """
        if self.base_snapshot is None:
            return

        cursor = max(0, min(int(cursor), len(self.events)))

        # Restore from closest checkpoint <= cursor, else from base
        start_cursor = 0
        start_snapshot = self.base_snapshot
        for c, snap in reversed(self.checkpoints or []):
            if c <= cursor:
                start_cursor = c
                start_snapshot = snap
                break

        if start_snapshot is None:
            return

        _restore_state_into(state, start_snapshot)

        for ev in self.events[start_cursor:cursor]:
            _apply_event(state, ev)

        self.cursor = cursor


def _snapshot_state(state: Any) -> Dict[str, Any]:
    # If state is a dataclass, asdict gives a nested structure; deepcopy to be safe.
    if is_dataclass(state):
        return copy.deepcopy(asdict(state))
    # Fallback: attempt deepcopy of __dict__
    return copy.deepcopy(getattr(state, "__dict__", {}))


def _restore_state_into(state: Any, snap: Dict[str, Any]) -> None:
    """
    Restore snapshot dict into EncounterState/TokenState structure.
    This assumes your EncounterState and TokenState are dataclasses with fields
    matching the snapshot keys (which asdict() provides).
    """
    # Restore EncounterState top-level fields
    for k, v in snap.items():
        if k == "tokens":
            continue
        try:
            setattr(state, k, copy.deepcopy(v))
        except Exception:
            pass

    # Restore tokens as TokenState objects in existing dict
    tokens_snap = snap.get("tokens") or {}
    # state.tokens is Dict[str, TokenState]
    state.tokens.clear()
    for token_id, td in tokens_snap.items():
        # TokenState class is available from the already-constructed objects in your runtime.
        # We can reconstruct by grabbing the class from any existing TokenState, or import.
        # Safer: import here.
        from ui.encounter_state import TokenState  # local import avoids circulars
        state.tokens[token_id] = TokenState(**copy.deepcopy(td))


def _apply_event(state: Any, ev: Event) -> None:
    t = str(ev.get("type", "")).upper().strip()

    if t == "SET_POSITION":
        token_id = ev.get("token_id", "")
        ts = state.tokens.get(token_id)
        if not ts:
            return
        to_x = int(ev.get("to_gx", ts.grid_x))
        to_y = int(ev.get("to_gy", ts.grid_y))
        ts.grid_x = to_x
        ts.grid_y = to_y

        # Movement remaining is authoritative from event (prevents drift)
        if "movement_remaining" in ev:
            mr = ev.get("movement_remaining", None)
            ts.movement_remaining = int(mr) if mr is not None else ts.movement_remaining

    elif t == "APPLY_DAMAGE":
        token_id = ev.get("token_id", "")
        ts = state.tokens.get(token_id)
        if not ts:
            return

        # Replay uses stored result (do NOT re-call server)
        if "hp_after" in ev:
            ts.hp = int(ev["hp_after"])
        if "max_hp_after" in ev:
            ts.max_hp = int(ev["max_hp_after"])
        if "death_state_after" in ev:
            ts.death_state = str(ev["death_state_after"])



    elif t == "DOOR_SET":
        door_id = str(ev.get("door_id", "") or "").strip()
        if not door_id:
            return
        try:
            is_open = bool(ev.get("is_open", False))
        except Exception:
            is_open = False
        # Ensure field exists
        if not hasattr(state, "door_state") or state.door_state is None:
            try:
                state.door_state = {}
            except Exception:
                return
        state.door_state[str(door_id)] = bool(is_open)

    elif t in ("TURN_START", "SET_ACTIVE_TURN"):
        # Phase 5.1: TURN_START is the semantic hook; keep SET_ACTIVE_TURN as legacy.
        state.initiative_active = bool(ev.get("initiative_active", True))
        state.current_turn_index = int(ev.get("current_turn_index", 0) or 0)
        state.round_number = int(ev.get("round_number", 1) or 1)
        state.active_token_id = ev.get("active_token_id", None)

        # Reset movement at turn start, deterministic.
        tok_id = state.active_token_id
        if tok_id and tok_id in state.tokens:
            from ui.encounter_state import reset_movement_for_turn
            reset_movement_for_turn(state.tokens[tok_id])

        # Apply optional per-token fields if present
        if tok_id and tok_id in state.tokens and "movement_remaining" in ev:
            mr = ev.get("movement_remaining", None)
            if mr is not None:
                state.tokens[tok_id].movement_remaining = int(mr)

    elif t == "TURN_END":
        # No direct state mutation required for replay (durations/ticks are explicit events).
        # This event exists so future systems can hang deterministic effects off it.
        return

    # Unknown event types are ignored (forward-compatible)
