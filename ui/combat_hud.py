# ui/combat_hud.py
from typing import Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
)


class CombatHudWidget(QWidget):
    """Minimal DM-side Combat HUD.

    Intentionally dumb/predictable:
      - Reflects armed attacker/target + EncounterState.pending_attack + awaiting damage state.
      - All actions delegate back to MainWindow via callbacks.
    """

    def __init__(
        self,
        *,
        on_arm_pc_attack,
        on_cancel_pending,
        on_force_resolve_npc,
        on_clear_selection,
        on_roll_death_save,
        on_cancel_awaiting_damage,  # NEW
        on_revert_illegal_move,  # NEW
    ):
        super().__init__()

        self._on_arm_pc_attack = on_arm_pc_attack
        self._on_cancel_pending = on_cancel_pending
        self._on_force_resolve_npc = on_force_resolve_npc
        self._on_clear_selection = on_clear_selection
        self._on_roll_death_save = on_roll_death_save
        self._on_cancel_awaiting_damage = on_cancel_awaiting_damage
        self._on_revert_illegal_move = on_revert_illegal_move

        root = QVBoxLayout()
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        self.setLayout(root)

        title = QLabel("Combat HUD")
        title.setAlignment(Qt.AlignLeft)
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        root.addWidget(title)

        self.status_lbl = QLabel("Idle")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("color: #444;")
        root.addWidget(self.status_lbl)

        root.addWidget(self._spacer_line())

        # --- State summary (armed + pending) ---
        self.attacker_lbl = QLabel("Attacker: —")
        self.weapon_lbl = QLabel("Weapon: —")
        self.target_lbl = QLabel("Target: —")
        self.cover_lbl = QLabel("Cover: —")
        self.pending_lbl = QLabel("Pending ID: —")
        self.expiry_lbl = QLabel("Pending TTL: —")

        for w in [self.attacker_lbl, self.weapon_lbl, self.target_lbl, self.cover_lbl, self.pending_lbl, self.expiry_lbl]:
            w.setStyleSheet("font-family: Consolas, monospace;")
            w.setWordWrap(True)
            root.addWidget(w)

        root.addWidget(self._spacer_line())

        # --- Awaiting Damage (Option B step 2) ---
        self.awaiting_hdr = QLabel("Awaiting Damage:")
        self.awaiting_hdr.setStyleSheet("font-weight: 600;")
        root.addWidget(self.awaiting_hdr)

        self.awaiting_lbl = QLabel("—")
        self.awaiting_lbl.setWordWrap(True)
        self.awaiting_lbl.setStyleSheet("font-family: Consolas, monospace;")
        root.addWidget(self.awaiting_lbl)

        self.awaiting_expiry_lbl = QLabel("Damage TTL: —")
        self.awaiting_expiry_lbl.setStyleSheet("font-family: Consolas, monospace;")
        root.addWidget(self.awaiting_expiry_lbl)

        self.cancel_awaiting_btn = QPushButton("Cancel Awaiting Damage")
        self.cancel_awaiting_btn.clicked.connect(self._on_cancel_awaiting_damage)
        root.addWidget(self.cancel_awaiting_btn)

        root.addWidget(self._spacer_line())

        # --- Buttons ---
        row1 = QHBoxLayout()
        self.arm_btn = QPushButton("Arm PC Attack")
        self.arm_btn.clicked.connect(self._on_arm_pc_attack)
        row1.addWidget(self.arm_btn)

        self.cancel_btn = QPushButton("Cancel Pending")
        self.cancel_btn.clicked.connect(self._on_cancel_pending)
        row1.addWidget(self.cancel_btn)
        root.addLayout(row1)

        row2 = QHBoxLayout()
        self.force_btn = QPushButton("Force Resolve NPC")
        self.force_btn.clicked.connect(self._on_force_resolve_npc)
        row2.addWidget(self.force_btn)

        self.clear_btn = QPushButton("Clear Selection")
        self.clear_btn.clicked.connect(self._on_clear_selection)
        row2.addWidget(self.clear_btn)
        root.addLayout(row2)

        row3 = QHBoxLayout()
        self.deathsave_btn = QPushButton("Roll Death Save")
        self.deathsave_btn.clicked.connect(self._on_roll_death_save)
        row3.addWidget(self.deathsave_btn)

        self.revert_illegal_btn = QPushButton("Revert Illegal Move")
        self.revert_illegal_btn.clicked.connect(self._on_revert_illegal_move)
        row3.addWidget(self.revert_illegal_btn)

        root.addLayout(row3)

        root.addStretch(1)

        # Keep a signature so frequent refreshes are cheap.
        self._last_render_sig = ""

    def _spacer_line(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        return sep

    @staticmethod
    def _fmt_secs(s: Optional[float]) -> str:
        """Format seconds as M:SS (or —)."""
        if s is None:
            return "—"
        try:
            s = float(s)
        except Exception:
            return "—"
        s = max(0.0, s)
        whole = int(round(s))
        m = whole // 60
        r = whole % 60
        return f"{m}:{r:02d}"

    def render(
        self,
        *,
        status: str,
        attacker_name: str,
        attacker_weapon: str,
        target_name: str,
        cover_text: str,
        pending_id: str,
        expires_in: Optional[float],
        awaiting_text: str,
        awaiting_expires_in: Optional[float],
        enable_arm: bool,
        enable_cancel: bool,
        enable_force: bool,
        enable_death_save: bool,
        enable_clear: bool,
        enable_cancel_awaiting: bool,
    ):
        """Render current combat state. Cheap to call often; no-ops if nothing changed."""

        # IMPORTANT: include awaiting fields in signature so HUD updates when only awaiting changes
        sig = "|".join(
            [
                status or "",
                attacker_name or "",
                attacker_weapon or "",
                target_name or "",
                cover_text or "",
                pending_id or "",
                str(int(expires_in)) if expires_in is not None else "None",
                awaiting_text or "",
                str(int(awaiting_expires_in)) if awaiting_expires_in is not None else "None",
                str(bool(enable_arm)),
                str(bool(enable_cancel)),
                str(bool(enable_force)),
                str(bool(enable_death_save)),
                str(bool(enable_clear)),
                str(bool(enable_cancel_awaiting)),
            ]
        )
        if sig == self._last_render_sig:
            return
        self._last_render_sig = sig

        self.status_lbl.setText(status or "")

        self.attacker_lbl.setText(f"Attacker: {attacker_name or '—'}")
        self.weapon_lbl.setText(f"Weapon: {attacker_weapon or '—'}")
        self.target_lbl.setText(f"Target: {target_name or '—'}")

        self.pending_lbl.setText(f"Pending ID: {pending_id or '—'}")
        self.expiry_lbl.setText(f"Pending TTL: {self._fmt_secs(expires_in)}")

        self.awaiting_lbl.setText(awaiting_text or "—")
        self.awaiting_expiry_lbl.setText(f"Damage TTL: {self._fmt_secs(awaiting_expires_in)}")

        self.arm_btn.setEnabled(bool(enable_arm))
        self.cancel_btn.setEnabled(bool(enable_cancel))
        self.force_btn.setEnabled(bool(enable_force))
        self.clear_btn.setEnabled(bool(enable_clear))
        self.deathsave_btn.setEnabled(bool(enable_death_save))

        self.cancel_awaiting_btn.setEnabled(bool(enable_cancel_awaiting))