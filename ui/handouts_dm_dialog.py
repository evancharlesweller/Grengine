from typing import Any, Dict, List, Optional

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
)


class HandoutsDMPushDialog(QDialog):
    """DM-side handout authoring + template storage + push to players/party."""

    def __init__(self, parent=None, *, server_client=None):
        super().__init__(parent)
        self.setWindowTitle("Handouts (DM Push)")
        self.setMinimumSize(980, 620)
        self.server = server_client

        self._players_index: List[Dict[str, Any]] = []
        self._templates: List[Dict[str, Any]] = []
        self._active_template_id: str = ""

        root = QVBoxLayout(self)

        # ---- Top: target controls ----
        top = QHBoxLayout()
        self.chk_party = QCheckBox("Send to Party")
        self.chk_party.stateChanged.connect(self._on_party_toggled)
        top.addWidget(self.chk_party)

        top.addSpacing(12)
        top.addWidget(QLabel("Player:"))
        self.cmb_player = QComboBox()
        self.cmb_player.currentIndexChanged.connect(self._on_player_changed)
        top.addWidget(self.cmb_player, 1)

        top.addWidget(QLabel("Character:"))
        self.cmb_character = QComboBox()
        top.addWidget(self.cmb_character, 1)

        self.btn_refresh = QPushButton("Refresh")
        self.btn_refresh.clicked.connect(self.refresh)
        top.addWidget(self.btn_refresh)

        self.btn_send = QPushButton("Send")
        self.btn_send.clicked.connect(self._send)
        self.btn_send.setDefault(True)
        top.addWidget(self.btn_send)

        root.addLayout(top)

        # ---- Main split: templates list vs editor ----
        split = QSplitter(Qt.Horizontal)

        left = QWidget()
        left_l = QVBoxLayout(left)
        left_l.setContentsMargins(0, 0, 0, 0)
        left_l.addWidget(QLabel("Stored Templates"))
        self.list_templates = QListWidget()
        self.list_templates.itemSelectionChanged.connect(self._on_template_selected)
        left_l.addWidget(self.list_templates, 1)

        left_btns = QHBoxLayout()
        self.btn_new = QPushButton("New")
        self.btn_new.clicked.connect(self._new_template)
        left_btns.addWidget(self.btn_new)
        self.btn_delete = QPushButton("Delete")
        self.btn_delete.clicked.connect(self._delete_template)
        left_btns.addWidget(self.btn_delete)
        left_l.addLayout(left_btns)

        right = QWidget()
        right_l = QVBoxLayout(right)
        right_l.setContentsMargins(0, 0, 0, 0)

        # Title / kind
        title_row = QHBoxLayout()
        title_row.addWidget(QLabel("Title:"))
        self.ed_title = QLineEdit()
        title_row.addWidget(self.ed_title, 1)
        title_row.addSpacing(8)
        title_row.addWidget(QLabel("Kind:"))
        self.cmb_kind = QComboBox()
        self.cmb_kind.addItems(["handout", "readable", "image"])
        title_row.addWidget(self.cmb_kind)
        right_l.addLayout(title_row)

        # Language (optional, used for readable gating in portal)
        lang_row = QHBoxLayout()
        lang_row.addWidget(QLabel("Language:"))
        self.ed_language = QLineEdit()
        self.ed_language.setPlaceholderText("e.g., Common, Elvish (leave blank for none)")
        lang_row.addWidget(self.ed_language, 1)
        right_l.addLayout(lang_row)
        # Unreadable behavior (when language is set but player can't read it)
        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Unreadable:"))
        self.cmb_unreadable = QComboBox()
        self.cmb_unreadable.addItems(["blocked", "scramble"])
        self.cmb_unreadable.setToolTip("When the handout has a language the player cannot read: blocked = show 'unreadable'; scramble = show garbled text.")
        mode_row.addWidget(self.cmb_unreadable, 1)
        right_l.addLayout(mode_row)


        self.ed_body = QPlainTextEdit()
        self.ed_body.setPlaceholderText("Handout text...")
        right_l.addWidget(self.ed_body, 1)

        save_row = QHBoxLayout()
        save_row.addStretch(1)
        self.btn_save = QPushButton("Save Template")
        self.btn_save.clicked.connect(self._save_template)
        save_row.addWidget(self.btn_save)
        right_l.addLayout(save_row)

        split.addWidget(left)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        root.addWidget(split, 1)

        self.refresh()

    # --------------------------------------------------------
    # Data loading
    # --------------------------------------------------------
    def refresh(self):
        self._players_index = []
        self._templates = []

        if self.server is None:
            QMessageBox.warning(self, "Server", "ServerClient not available on MainWindow.")
            return

        # Players
        try:
            self._players_index = self.server.fetch_players_index() or []
        except Exception:
            self._players_index = []

        self.cmb_player.blockSignals(True)
        self.cmb_player.clear()
        self.cmb_player.addItem("(select)", "")
        for p in self._players_index:
            pid = (p.get("player_id") or "").strip()
            if pid:
                self.cmb_player.addItem(pid, pid)
        self.cmb_player.blockSignals(False)

        self._on_player_changed()

        # Templates
        try:
            self._templates = self.server.fetch_handout_templates() or []
        except Exception:
            self._templates = []

        self._rebuild_templates_list(select_id=self._active_template_id)
        self._on_party_toggled()

    def _rebuild_templates_list(self, *, select_id: str = ""):
        self.list_templates.blockSignals(True)
        self.list_templates.clear()
        for tpl in self._templates:
            tid = str(tpl.get("template_id", "") or "")
            title = str(tpl.get("title", "Untitled") or "Untitled")
            item = QListWidgetItem(title)
            item.setData(Qt.UserRole, tid)
            self.list_templates.addItem(item)
            if select_id and tid == select_id:
                item.setSelected(True)
        self.list_templates.blockSignals(False)

        # If nothing selected, pick first
        if self.list_templates.count() > 0 and not self.list_templates.selectedItems():
            self.list_templates.setCurrentRow(0)

    # --------------------------------------------------------
    # UI events
    # --------------------------------------------------------
    def _on_party_toggled(self):
        party = self.chk_party.isChecked()
        self.cmb_player.setEnabled(not party)
        self.cmb_character.setEnabled(not party)

    def _on_player_changed(self):
        pid = self.cmb_player.currentData() or ""
        chars = []
        for p in self._players_index:
            if (p.get("player_id") or "") == pid:
                chars = p.get("characters", []) or []
                break
        self.cmb_character.clear()
        self.cmb_character.addItem("(any)", "")
        for c in chars:
            cid = (c.get("character_id") or "").strip()
            name = (c.get("display_name") or cid).strip()
            if cid:
                self.cmb_character.addItem(f"{name} [{cid}]", cid)

    def _on_template_selected(self):
        items = self.list_templates.selectedItems() or []
        if not items:
            return
        tid = items[0].data(Qt.UserRole) or ""
        self._load_template(tid)

    # --------------------------------------------------------
    # Template operations
    # --------------------------------------------------------
    def _find_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        for t in self._templates:
            if str(t.get("template_id", "")) == str(template_id):
                return t
        return None

    def _load_template(self, template_id: str):
        tpl = self._find_template(template_id)
        if not tpl:
            return
        self._active_template_id = str(tpl.get("template_id", "") or "")
        self.ed_title.setText(str(tpl.get("title", "") or ""))
        kind = str(tpl.get("kind", "handout") or "handout")
        idx = self.cmb_kind.findText(kind)
        self.cmb_kind.setCurrentIndex(idx if idx >= 0 else 0)
        self.ed_body.setPlainText(str(tpl.get("text", "") or ""))
        try:
            pl = tpl.get("payload", {}) if isinstance(tpl.get("payload", {}), dict) else {}
            self.ed_language.setText(str(pl.get("language", "") or ""))
            um = str(pl.get("unreadable_mode", "blocked") or "blocked").strip().lower()
            if hasattr(self, "cmb_unreadable"):
                idx2 = self.cmb_unreadable.findText(um)
                self.cmb_unreadable.setCurrentIndex(idx2 if idx2 >= 0 else 0)
        except Exception:
            self.ed_language.setText("")
        if hasattr(self, "cmb_unreadable"):
            self.cmb_unreadable.setCurrentIndex(0)
            if hasattr(self, "cmb_unreadable"):
                self.cmb_unreadable.setCurrentIndex(0)

    def _new_template(self):
        self._active_template_id = ""
        self.ed_title.setText("")
        self.cmb_kind.setCurrentIndex(0)
        self.ed_body.setPlainText("")
        self.ed_language.setText("")
        self.list_templates.clearSelection()

    def _save_template(self):
        if self.server is None:
            return
        payload = {
            "template_id": self._active_template_id,
            "title": (self.ed_title.text() or "").strip(),
            "kind": (self.cmb_kind.currentText() or "handout").strip(),
            "text": self.ed_body.toPlainText() or "",
            "payload": ({"language": (self.ed_language.text() or "").strip(), "unreadable_mode": (self.cmb_unreadable.currentText() if hasattr(self, "cmb_unreadable") else "blocked")} if (self.ed_language.text() or "").strip() else {}),
        }
        if not payload["title"]:
            payload["title"] = "Untitled Handout"
        ok = self.server.upsert_handout_template(payload)
        if not ok:
            QMessageBox.warning(self, "Save", "Failed to save template.")
            return
        # Refresh templates and keep selection
        self._active_template_id = payload.get("template_id") or self._active_template_id
        self.refresh()

    def _delete_template(self):
        if self.server is None:
            return
        tid = self._active_template_id
        if not tid:
            # try selected
            items = self.list_templates.selectedItems() or []
            if items:
                tid = items[0].data(Qt.UserRole) or ""
        tid = (tid or "").strip()
        if not tid:
            return
        if QMessageBox.question(self, "Delete", "Delete selected template?") != QMessageBox.Yes:
            return
        ok = self.server.delete_handout_template(tid)
        if not ok:
            QMessageBox.warning(self, "Delete", "Failed to delete template.")
            return
        self._active_template_id = ""
        self.refresh()

    # --------------------------------------------------------
    # Send
    # --------------------------------------------------------
    def _send(self):
        if self.server is None:
            return
        title = (self.ed_title.text() or "").strip() or "Handout"
        body = self.ed_body.toPlainText() or ""
        kind = (self.cmb_kind.currentText() or "handout").strip()
        lang = (self.ed_language.text() or "").strip()
        umode = (self.cmb_unreadable.currentText() if hasattr(self, "cmb_unreadable") else "blocked")
        extra_payload = {"language": lang, "unreadable_mode": umode} if lang else {}

        if not body and kind != "image":
            QMessageBox.warning(self, "Send", "Handout body is empty.")
            return

        # Determine recipients
        if self.chk_party.isChecked():
            # party = all known players (pins)
            sent = 0
            for p in self._players_index:
                pid = (p.get("player_id") or "").strip()
                if not pid:
                    continue
                if self.server.push_handout(to_player_id=pid, to_character_id="", title=title, body=body, kind=kind, payload=extra_payload):
                    sent += 1
            QMessageBox.information(self, "Send", f"Sent to party: {sent} player(s).")
            return

        pid = (self.cmb_player.currentData() or "").strip()
        cid = (self.cmb_character.currentData() or "").strip()
        if not pid:
            QMessageBox.warning(self, "Send", "Select a player or choose 'Send to Party'.")
            return
        ok = self.server.push_handout(to_player_id=pid, to_character_id=cid, title=title, body=body, kind=kind, payload=extra_payload)
        if ok:
            QMessageBox.information(self, "Send", "Handout sent.")
        else:
            QMessageBox.warning(self, "Send", "Failed to send handout.")
