# encounter/history_runtime.py
from __future__ import annotations

import time
from typing import Any, Dict, Optional, Callable

from ui.encounter_history import EncounterHistory
from ui.encounter_state import EncounterState


class HistoryRuntime:
    """
    Wrapper around EncounterHistory to keep history mechanics out of MainWindow.

    Phase 5.1 goals:
      - standardized event shape
      - capture base snapshot once
      - capture checkpoints at turn boundaries (TURN_START / TURN_END)
      - replay to a cursor for deterministic debugging / undo foundation
    """

    def __init__(self) -> None:
        self.history = EncounterHistory()

    # --------------------------
    # Event helpers (Phase 5.1)
    # --------------------------
    def make_event(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        *,
        checkpoint: bool = False,
        encounter_id: str = "",
        t: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create a standardized event dict.
        """
        return {
            "type": str(event_type or "UNKNOWN"),
            "t": int(t if t is not None else time.time()),
            "encounter_id": str(encounter_id or ""),
            "checkpoint": bool(checkpoint),
            "payload": dict(payload or {}),
        }

    # --------------------------
    # Base + append + replay
    # --------------------------
    def capture_base_if_needed(self, state: EncounterState) -> None:
        if self.history.base_snapshot is None:
            self.history.capture_base(state)

    def append_event(
        self,
        state: EncounterState,
        ev: Dict[str, Any],
        *,
        advance_cursor: bool = True,
        on_after_apply: Optional[Callable[[], None]] = None,
    ) -> None:
        """
        Append an event and optionally capture a checkpoint.
        """
        self.capture_base_if_needed(state)

        # Defensive: ensure minimum required keys exist (in case callers bypass make_event)
        if "type" not in ev:
            ev["type"] = "UNKNOWN"
        if "t" not in ev:
            ev["t"] = int(time.time())
        if "payload" not in ev or not isinstance(ev.get("payload"), dict):
            ev["payload"] = {}

        self.history.append(ev, advance_cursor=advance_cursor)

        if bool(ev.get("checkpoint", False)):
            self.history.capture_checkpoint(state)

        if callable(on_after_apply):
            try:
                on_after_apply()
            except Exception:
                pass

    def replay_to_cursor(
        self,
        state: EncounterState,
        cursor: int,
        *,
        on_after_replay: Optional[Callable[[], None]] = None,
    ) -> None:
        """
        Rebuild state by replaying events from base snapshot up to 'cursor'.
        """
        if self.history.base_snapshot is None:
            return

        self.history.replay_to(state, cursor)

        if callable(on_after_replay):
            try:
                on_after_replay()
            except Exception:
                pass