# ui/initiative_panel.py
from typing import List

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QFrame,
    QTextEdit,
)


class InitiativePanelWidget(QWidget):
    """
    DM-side initiative + turn order panel.
    Intentionally predictable and copy/paste-friendly: render() takes simple strings + enabled flags.
    """

    def __init__(
        self,
        *,
        on_roll_all,
        on_roll_pcs,
        on_roll_npcs,
        on_roll_selected,
        on_start_encounter,
        on_end_turn,
        on_prev_turn,
        on_next_turn,
        on_end_encounter,
        on_undo,
        on_redo,
    ):
        super().__init__()

        # Store callbacks (MUST store undo/redo too)
        self._on_roll_all = on_roll_all
        self._on_roll_pcs = on_roll_pcs
        self._on_roll_npcs = on_roll_npcs
        self._on_roll_selected = on_roll_selected
        self._on_start_encounter = on_start_encounter
        self._on_end_turn = on_end_turn
        self._on_prev_turn = on_prev_turn
        self._on_next_turn = on_next_turn
        self._on_end_encounter = on_end_encounter
        self._on_undo = on_undo
        self._on_redo = on_redo

        root = QVBoxLayout()
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)
        self.setLayout(root)

        title = QLabel("Initiative / Turn Order")
        title.setAlignment(Qt.AlignLeft)
        title.setStyleSheet("font-weight: 600; font-size: 14px;")
        root.addWidget(title)

        self.status_lbl = QLabel("Inactive")
        self.status_lbl.setWordWrap(True)
        self.status_lbl.setStyleSheet("color: #444;")
        root.addWidget(self.status_lbl)

        root.addWidget(self._spacer_line())

        # Order display
        self.order_box = QTextEdit()
        self.order_box.setReadOnly(True)
        self.order_box.setStyleSheet("font-family: Consolas, monospace;")
        self.order_box.setMinimumHeight(200)
        root.addWidget(self.order_box)

        root.addWidget(self._spacer_line())

        # Roll buttons
        roll_row1 = QHBoxLayout()
        self.roll_all_btn = QPushButton("Roll (All)")
        self.roll_all_btn.clicked.connect(self._on_roll_all)
        roll_row1.addWidget(self.roll_all_btn)

        self.roll_pcs_btn = QPushButton("Roll (PCs)")
        self.roll_pcs_btn.clicked.connect(self._on_roll_pcs)
        roll_row1.addWidget(self.roll_pcs_btn)
        root.addLayout(roll_row1)

        roll_row2 = QHBoxLayout()
        self.roll_npcs_btn = QPushButton("Roll (NPCs)")
        self.roll_npcs_btn.clicked.connect(self._on_roll_npcs)
        roll_row2.addWidget(self.roll_npcs_btn)

        self.roll_sel_btn = QPushButton("Roll (Selected)")
        self.roll_sel_btn.clicked.connect(self._on_roll_selected)
        roll_row2.addWidget(self.roll_sel_btn)
        root.addLayout(roll_row2)

        root.addWidget(self._spacer_line())

        # Turn controls
        ctrl_row1 = QHBoxLayout()
        self.start_btn = QPushButton("Start Encounter")
        self.start_btn.clicked.connect(self._on_start_encounter)
        ctrl_row1.addWidget(self.start_btn)

        self.end_turn_btn = QPushButton("End Turn")
        self.end_turn_btn.clicked.connect(self._on_end_turn)
        ctrl_row1.addWidget(self.end_turn_btn)
        root.addLayout(ctrl_row1)

        ctrl_row2 = QHBoxLayout()
        self.prev_btn = QPushButton("Prev")
        self.prev_btn.clicked.connect(self._on_prev_turn)
        ctrl_row2.addWidget(self.prev_btn)

        self.next_btn = QPushButton("Next")
        self.next_btn.clicked.connect(self._on_next_turn)
        ctrl_row2.addWidget(self.next_btn)

        self.end_enc_btn = QPushButton("End Encounter")
        self.end_enc_btn.clicked.connect(self._on_end_encounter)
        ctrl_row2.addWidget(self.end_enc_btn)
        root.addLayout(ctrl_row2)

        # History controls (Undo/Redo) on their own row
        ctrl_row3 = QHBoxLayout()
        self.undo_btn = QPushButton("Undo")
        if callable(self._on_undo):
            self.undo_btn.clicked.connect(self._on_undo)
        else:
            self.undo_btn.setEnabled(False)
        ctrl_row3.addWidget(self.undo_btn)

        self.redo_btn = QPushButton("Redo")
        if callable(self._on_redo):
            self.redo_btn.clicked.connect(self._on_redo)
        else:
            self.redo_btn.setEnabled(False)
        ctrl_row3.addWidget(self.redo_btn)

        ctrl_row3.addStretch(1)
        root.addLayout(ctrl_row3)

        root.addStretch(1)
        self._last_sig = ""

    def _spacer_line(self) -> QFrame:
        sep = QFrame()
        sep.setFrameShape(QFrame.HLine)
        sep.setFrameShadow(QFrame.Sunken)
        return sep


    def render(
        self,
        *,
        status: str,
        order_lines: List[str],
        enable_roll: bool,
        enable_start: bool,
        enable_turn_controls: bool,
        enable_end_encounter: bool,
        enable_undo: bool = False,
        enable_redo: bool = False,
    ):
        sig = "|".join(
            [
                status or "",
                "\n".join(order_lines or []),
                str(bool(enable_roll)),
                str(bool(enable_start)),
                str(bool(enable_turn_controls)),
                str(bool(enable_end_encounter)),
                str(bool(enable_undo)),
                str(bool(enable_redo)),
            ]
        )
        if sig == getattr(self, "_last_sig", ""):
            return
        self._last_sig = sig

        self.status_lbl.setText(status or "")
        self.order_box.setPlainText("\n".join(order_lines or ["—"]))

        self.roll_all_btn.setEnabled(enable_roll)
        self.roll_pcs_btn.setEnabled(enable_roll)
        self.roll_npcs_btn.setEnabled(enable_roll)
        self.roll_sel_btn.setEnabled(enable_roll)

        self.start_btn.setEnabled(enable_start)
        self.end_turn_btn.setEnabled(enable_turn_controls)
        self.prev_btn.setEnabled(enable_turn_controls)
        self.next_btn.setEnabled(enable_turn_controls)
        self.end_enc_btn.setEnabled(enable_end_encounter)

        # Undo/Redo depend on history cursor
        if hasattr(self, "undo_btn"):
            self.undo_btn.setEnabled(bool(enable_undo))
        if hasattr(self, "redo_btn"):
            self.redo_btn.setEnabled(bool(enable_redo))