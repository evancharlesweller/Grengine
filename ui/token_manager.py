import os
import json
import shutil
import uuid
import requests
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QIcon
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QPushButton,
    QHBoxLayout, QLineEdit, QMessageBox, QFormLayout,
    QFileDialog, QListWidgetItem, QComboBox, QSpinBox, QCheckBox
)
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtWidgets import QInputDialog
from ui.items_db import load_items


class TokenManagerTab(QWidget):
    tokens_updated = pyqtSignal()
    def __init__(self, campaign_path, server_client=None):
        super().__init__()
        self.campaign_path = campaign_path
        self.server_client = server_client
        self.tokens_file = os.path.join(campaign_path, "tokens.json")
        self.icon_folder = os.path.join(campaign_path, "tokens")
        os.makedirs(self.icon_folder, exist_ok=True)

        self.tokens = []
        self._selected_index = None

        layout = QVBoxLayout()

        self.token_list = QListWidget()
        layout.addWidget(self.token_list)

        button_row = QHBoxLayout()
        self.update_button = QPushButton("Update")
        self.add_button = QPushButton("Add")
        self.delete_button = QPushButton("Delete")
        self.save_button = QPushButton("Save")
        button_row.addWidget(self.update_button)
        button_row.addWidget(self.add_button)
        button_row.addWidget(self.delete_button)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)
        self.btn_create_sheet = QPushButton("Create/Link Sheet for Selected Token")
        self.btn_create_sheet.clicked.connect(self.create_or_link_sheet_for_selected_token)
        button_row.addWidget(self.btn_create_sheet)


        form = QFormLayout()

        # --- Core token fields ---
        self.name_input = QLineEdit()
        self.hp_input = QLineEdit()
        self.ac_input = QLineEdit()
        self.movement_input = QLineEdit()
        self.attack_mod_input = QLineEdit()

        self.weapon_input = QComboBox()
        self.armor_input = QComboBox()

        self.icon_path_input = QLineEdit()
        self.icon_path_input.setReadOnly(True)
        self.pick_icon_button = QPushButton("Select Image")
        self.pick_icon_button.clicked.connect(self.select_icon)

        form.addRow("Display Name:", self.name_input)
        form.addRow("Max HP:", self.hp_input)
        form.addRow("AC:", self.ac_input)
        form.addRow("Movement (ft):", self.movement_input)
        form.addRow("Attack Modifier:", self.attack_mod_input)
        form.addRow("Weapon:", self.weapon_input)
        form.addRow("Armor:", self.armor_input)

        icon_layout = QHBoxLayout()
        icon_layout.addWidget(self.icon_path_input)
        icon_layout.addWidget(self.pick_icon_button)
        form.addRow("Token Image:", icon_layout)

        # --- PC + vision + side fields ---
        self.is_pc_checkbox = QCheckBox("PC Token (linked to player)")

        self.side_input = QComboBox()
        self.side_input.addItems(["player", "enemy", "neutral", "ally"])

        self.vision_input = QSpinBox()
        self.vision_input.setRange(0, 500)
        self.vision_input.setValue(60)

        # B-X4: Vision Types / senses
        self.vision_type_input = QComboBox()
        self.vision_type_input.addItems([
            "normal",
            "darkvision",
            "blindsight",
            "truesight",
            "tremorsense",
            "devils_sight",
        ])
        self.vision_type_input.setCurrentText("normal")

        def _mk_range_spin(default: int = 0) -> QSpinBox:
            sb = QSpinBox()
            sb.setRange(0, 500)
            sb.setValue(int(default))
            return sb

        self.darkvision_input = _mk_range_spin(0)
        self.blindsight_input = _mk_range_spin(0)
        self.truesight_input = _mk_range_spin(0)
        self.tremorsense_input = _mk_range_spin(0)
        self.devils_sight_input = _mk_range_spin(0)

        self.stat_source_input = QComboBox()
        self.stat_source_input.addItems(["template", "character_sheet"])
        self.stat_source_input.setCurrentText("character_sheet")

        self.player_id_input = QLineEdit()
        self.character_id_input = QLineEdit()

        form.addRow(self.is_pc_checkbox)
        form.addRow("Side:", self.side_input)
        form.addRow("Vision (ft):", self.vision_input)
        form.addRow("Vision Type:", self.vision_type_input)
        form.addRow("Darkvision (ft):", self.darkvision_input)
        form.addRow("Blindsight (ft):", self.blindsight_input)
        form.addRow("Truesight (ft):", self.truesight_input)
        form.addRow("Tremorsense (ft):", self.tremorsense_input)
        form.addRow("Devil's Sight (ft):", self.devils_sight_input)
        form.addRow("Stat Source:", self.stat_source_input)
        form.addRow("Player ID (PC only):", self.player_id_input)
        form.addRow("Character ID (optional):", self.character_id_input)

        layout.addLayout(form)
        self.setLayout(layout)

        # Wiring
        self.token_list.itemClicked.connect(self.load_selected_token)
        self.add_button.clicked.connect(self.add_token)
        self.delete_button.clicked.connect(self.delete_token)
        self.save_button.clicked.connect(self.save_tokens)
        self.update_button.clicked.connect(self.update_token)

        self.is_pc_checkbox.toggled.connect(self._set_pc_fields_visible)

        # Initial load
        self.load_equipment_options()
        self.load_tokens()

        self._set_pc_fields_visible(self.is_pc_checkbox.isChecked())

    # -----------------------------
    # Helpers
    # -----------------------------
    def _safe_int(self, s: str, default: int = 0) -> int:
        try:
            return int(str(s).strip())
        except Exception:
            return default

    def _set_pc_fields_visible(self, visible: bool):
        self.player_id_input.setVisible(visible)
        self.character_id_input.setVisible(visible)
        self.stat_source_input.setVisible(visible)

    def _combo_set_by_item_id_or_name(self, combo: QComboBox, item_id: str, name: str):
        """
        Prefer selecting by item_id stored in Qt.UserRole.
        Fallback to selecting by displayed name (legacy).
        """
        if item_id:
            for i in range(combo.count()):
                if combo.itemData(i, Qt.UserRole) == item_id:
                    combo.setCurrentIndex(i)
                    return
        if name:
            idx = combo.findText(name)
            if idx >= 0:
                combo.setCurrentIndex(idx)
                return
        combo.setCurrentIndex(0)

    def _combo_selected_item_id(self, combo: QComboBox) -> str:
        data = combo.currentData(Qt.UserRole)
        return (data or "").strip()

    def _combo_selected_name(self, combo: QComboBox) -> str:
        return (combo.currentText() or "").strip()

    # -----------------------------
    # Equipment + tokens loading
    # -----------------------------
    def load_equipment_options(self):
        """
        Populate Weapon/Armor dropdowns from items.json.
        Store item_id in Qt.UserRole, show name as visible text.
        """
        self.weapon_input.clear()
        self.armor_input.clear()

        items = load_items(self.campaign_path)
        weapons = items.get("weapons", []) or []
        armors = items.get("armors", []) or []

        # Blank option
        self.weapon_input.addItem("", "")
        self.armor_input.addItem("", "")

        for w in weapons:
            name = (w.get("name") or "").strip()
            item_id = (w.get("item_id") or w.get("id") or "").strip()
            if name:
                self.weapon_input.addItem(name, item_id or name)  # if no id, fall back to name

        for a in armors:
            name = (a.get("name") or "").strip()
            item_id = (a.get("item_id") or a.get("id") or "").strip()
            if name:
                self.armor_input.addItem(name, item_id or name)

    def load_tokens(self):
        tokens_file = os.path.join(self.campaign_path, "tokens.json")

        # ---- 1) Load raw JSON ----
        raw = []
        if os.path.exists(tokens_file):
            try:
                with open(tokens_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
            except Exception:
                raw = []
        else:
            raw = []

        # ---- 2) Normalize to list[dict] ----
        tokens = []
        changed = False

        # Case A: legacy list
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict):
                    tokens.append(entry)
                else:
                    # drop strings/garbage
                    changed = True

        # Case B: wrapper dict ({"templates": {...}} or {"templates": [...]})
        elif isinstance(raw, dict):
            templates = raw.get("templates", None)

            # templates as dict keyed by template_id
            if isinstance(templates, dict):
                for tid, tdata in templates.items():
                    if not isinstance(tdata, dict):
                        changed = True
                        continue
                    if not tdata.get("template_id"):
                        tdata = dict(tdata)
                        tdata["template_id"] = tid
                        changed = True
                    tokens.append(tdata)

            # templates as list
            elif isinstance(templates, list):
                for entry in templates:
                    if isinstance(entry, dict):
                        tokens.append(entry)
                    else:
                        changed = True
            else:
                # unknown shape
                tokens = []
                changed = True

            # If you normalize from dict-wrapper to list, you’ll be saving list below
            changed = True

        else:
            tokens = []
            changed = True

        # ---- 3) Ensure template_id exists for all dict tokens ----
        for t in tokens:
            if not t.get("template_id"):
                t["template_id"] = uuid.uuid4().hex
                changed = True

        # ---- 4) Save repaired file (canonical list-of-dicts) ----
        if changed:
            try:
                with open(tokens_file, "w", encoding="utf-8") as f:
                    json.dump(tokens, f, indent=2)
            except Exception as e:
                print(f"[TOKEN] Failed to repair tokens.json: {e}")

        # ---- 5) Continue with your existing UI population using `tokens` ----
        self.tokens = tokens  # or whatever your class uses


    def save_tokens(self, silent: bool = False):
        try:
            with open(self.tokens_file, "w", encoding="utf-8") as f:
                json.dump(self.tokens, f, indent=2)

            if not silent:
                QMessageBox.information(self, "Saved", "Tokens saved successfully.")

            self.refresh_list()

            # Emit ONLY after successful save
            self.tokens_updated.emit()

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save tokens:\n{e}")

    def refresh_list(self):
        self.token_list.clear()
        for token in self.tokens:
            item = QListWidgetItem(token.get("name", "(unnamed)"))
            item.setData(Qt.UserRole, token.get("template_id", ""))

            icon_path = os.path.join(self.icon_folder, token.get("icon", ""))
            if os.path.exists(icon_path):
                item.setIcon(QIcon(icon_path))
            self.token_list.addItem(item)

    # -----------------------------
    # UI selection/editing
    # -----------------------------
    def load_selected_token(self, item):
        template_id = item.data(Qt.UserRole)
        self._selected_index = None
        token = None
        for i, t in enumerate(self.tokens):
            if t.get("template_id") == template_id:
                self._selected_index = i
                token = t
                break
        if not token:
            return

        self.name_input.setText(token.get("name", ""))
        self.hp_input.setText(str(token.get("max_hp", "")))
        self.ac_input.setText(str(token.get("ac", "")))
        self.movement_input.setText(str(token.get("movement", "")))
        self.attack_mod_input.setText(str(token.get("attack_modifier", 0)))
        self.icon_path_input.setText(token.get("icon", ""))

        # Prefer ID selection; fallback to legacy name
        self._combo_set_by_item_id_or_name(
            self.weapon_input,
            token.get("weapon_id", ""),
            token.get("weapon", "")
        )
        self._combo_set_by_item_id_or_name(
            self.armor_input,
            token.get("armor_id", ""),
            token.get("armor", "")
        )

        # PC fields
        is_pc = (token.get("kind", "npc") == "pc")
        self.is_pc_checkbox.setChecked(is_pc)
        self.side_input.setCurrentText(token.get("side", "enemy"))
        self.vision_input.setValue(self._safe_int(token.get("vision_ft", 60), 60))
        self.vision_type_input.setCurrentText(str(token.get("vision_type", "normal") or "normal"))
        self.darkvision_input.setValue(self._safe_int(token.get("darkvision_ft", 0), 0))
        self.blindsight_input.setValue(self._safe_int(token.get("blindsight_ft", 0), 0))
        self.truesight_input.setValue(self._safe_int(token.get("truesight_ft", 0), 0))
        self.tremorsense_input.setValue(self._safe_int(token.get("tremorsense_ft", 0), 0))
        self.devils_sight_input.setValue(self._safe_int(token.get("devils_sight_ft", 0), 0))
        self.stat_source_input.setCurrentText(token.get("stat_source", "template"))
        self.player_id_input.setText(token.get("player_id", ""))
        self.character_id_input.setText(token.get("character_id", ""))

    def add_token(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing name", "Token must have a display name.")
            return

        max_hp = self._safe_int(self.hp_input.text(), 0)
        ac = self._safe_int(self.ac_input.text(), 0)
        movement = self._safe_int(self.movement_input.text(), 30)
        attack_mod = self._safe_int(self.attack_mod_input.text(), 0)

        kind = "pc" if self.is_pc_checkbox.isChecked() else "npc"
        side = self.side_input.currentText()
        vision_ft = int(self.vision_input.value())
        vision_type = str(self.vision_type_input.currentText() or "normal").strip()

        darkvision_ft = int(self.darkvision_input.value())
        blindsight_ft = int(self.blindsight_input.value())
        truesight_ft = int(self.truesight_input.value())
        tremorsense_ft = int(self.tremorsense_input.value())
        devils_sight_ft = int(self.devils_sight_input.value())

        stat_source = self.stat_source_input.currentText()
        player_id = self.player_id_input.text().strip()
        character_id = self.character_id_input.text().strip()

        if kind == "pc" and not player_id:
            QMessageBox.warning(
                self,
                "Missing Player ID",
                "This token is marked as a PC but has no Player ID.\n"
                "You can still save it, but linking/syncing won’t work until you add one."
            )

        weapon_id = self._combo_selected_item_id(self.weapon_input)
        armor_id = self._combo_selected_item_id(self.armor_input)
        weapon_name = self._combo_selected_name(self.weapon_input)
        armor_name = self._combo_selected_name(self.armor_input)

        new_token = {
            "template_id": uuid.uuid4().hex,
            "name": name,
            "max_hp": max_hp,
            "ac": ac,
            "movement": movement,
            "attack_modifier": attack_mod,
            "icon": self.icon_path_input.text(),

            # NEW canonical references (IDs)
            "weapon_id": weapon_id,
            "armor_id": armor_id,

            # Legacy fields (keep during migration)
            "weapon": weapon_name,
            "armor": armor_name,

            # PC/vision/linking
            "kind": kind,
            "side": side,
            "vision_ft": vision_ft,
            "vision_type": vision_type,
            "darkvision_ft": darkvision_ft,
            "blindsight_ft": blindsight_ft,
            "truesight_ft": truesight_ft,
            "tremorsense_ft": tremorsense_ft,
            "devils_sight_ft": devils_sight_ft,
            "stat_source": stat_source,
            "player_id": player_id,
            "character_id": character_id,
        }

        self.tokens.append(new_token)
        self.refresh_list()
        self.clear_form()

    def update_token(self):
        if self._selected_index is None or self._selected_index < 0 or self._selected_index >= len(self.tokens):
            current = self.token_list.currentItem()
            template_id = current.data(Qt.UserRole) if current else ""
            self._selected_index = None
            if template_id:
                for i, tok in enumerate(self.tokens):
                    if tok.get("template_id") == template_id:
                        self._selected_index = i
                        break
        if self._selected_index is None or self._selected_index < 0 or self._selected_index >= len(self.tokens):
            QMessageBox.warning(self, "No selection", "Select a token in the list to update.")
            return

        existing_id = self.tokens[self._selected_index].get("template_id", "") or uuid.uuid4().hex

        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing name", "Token must have a display name.")
            return

        max_hp = self._safe_int(self.hp_input.text(), 0)
        ac = self._safe_int(self.ac_input.text(), 0)
        movement = self._safe_int(self.movement_input.text(), 30)
        attack_mod = self._safe_int(self.attack_mod_input.text(), 0)

        kind = "pc" if self.is_pc_checkbox.isChecked() else "npc"

        weapon_id = self._combo_selected_item_id(self.weapon_input)
        armor_id = self._combo_selected_item_id(self.armor_input)
        weapon_name = self._combo_selected_name(self.weapon_input)
        armor_name = self._combo_selected_name(self.armor_input)

        updated = {
            "template_id": existing_id,
            "name": name,
            "max_hp": max_hp,
            "ac": ac,
            "movement": movement,
            "attack_modifier": attack_mod,
            "icon": self.icon_path_input.text(),

            # NEW canonical references (IDs)
            "weapon_id": weapon_id,
            "armor_id": armor_id,

            # Legacy fields (keep during migration)
            "weapon": weapon_name,
            "armor": armor_name,

            "kind": kind,
            "side": self.side_input.currentText(),
            "vision_ft": int(self.vision_input.value()),
            "vision_type": str(self.vision_type_input.currentText() or "normal").strip(),
            "darkvision_ft": int(self.darkvision_input.value()),
            "blindsight_ft": int(self.blindsight_input.value()),
            "truesight_ft": int(self.truesight_input.value()),
            "tremorsense_ft": int(self.tremorsense_input.value()),
            "devils_sight_ft": int(self.devils_sight_input.value()),
            "stat_source": self.stat_source_input.currentText(),
            "player_id": self.player_id_input.text().strip(),
            "character_id": self.character_id_input.text().strip(),
        }

        self.tokens[self._selected_index] = updated
        self.refresh_list()
        QMessageBox.information(self, "Updated", "Token updated. Click Save to write to tokens.json.")

    def delete_token(self):
        current = self.token_list.currentItem()
        if not current:
            return

        template_id = current.data(Qt.UserRole)
        if not template_id:
            return

        self.tokens = [t for t in self.tokens if t.get("template_id") != template_id]
        self.refresh_list()
        self.clear_form()

    def select_icon(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose Token Image",
            "",
            "Image Files (*.png *.jpg *.bmp)"
        )
        if not file_path:
            return

        dest_name = os.path.basename(file_path)
        dest_path = os.path.join(self.icon_folder, dest_name)

        try:
            if os.path.abspath(file_path) != os.path.abspath(dest_path):
                shutil.copy2(file_path, dest_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to copy token image:\n{e}")
            return

        self.icon_path_input.setText(dest_name)

    def clear_form(self):
        self.name_input.clear()
        self.hp_input.clear()
        self.ac_input.clear()
        self.movement_input.clear()
        self.attack_mod_input.clear()

        self.weapon_input.setCurrentIndex(0)
        self.armor_input.setCurrentIndex(0)

        self.icon_path_input.clear()

        self.is_pc_checkbox.setChecked(False)
        self.side_input.setCurrentText("enemy")
        self.vision_input.setValue(60)
        self.vision_type_input.setCurrentText("normal")
        self.darkvision_input.setValue(0)
        self.blindsight_input.setValue(0)
        self.truesight_input.setValue(0)
        self.tremorsense_input.setValue(0)
        self.devils_sight_input.setValue(0)
        self.stat_source_input.setCurrentText("character_sheet")
        self.player_id_input.clear()
        self.character_id_input.clear()

    def _selected_token_index(self) -> int:
        """
        Returns the index into self.tokens for the currently selected token in the list.
        We store template_id in each QListWidgetItem(Qt.UserRole), so we match on that.
        """
        item = self.token_list.currentItem()
        if not item:
            return -1

        template_id = (item.data(Qt.UserRole) or "").strip()
        if not template_id:
            return -1

        for i, t in enumerate(self.tokens):
            if (t.get("template_id") or "").strip() == template_id:
                return i

        return -1

    def _post_or_get_character(self, character_id: str, payload: dict) -> dict:
        """
        Create character on server. If already exists (409), fetch it.
        Returns the character JSON dict (or {} if failed).
        """
        base = "http://127.0.0.1:8000"

        try:
            r = requests.post(f"{base}/characters", json=payload, timeout=3)

            # Created OK
            if r.ok:
                return r.json()

            # Already exists -> fetch it
            if r.status_code == 409:
                g = requests.get(f"{base}/characters/{character_id}", timeout=3)
                if g.ok:
                    return g.json()
                else:
                    print("[SHEET] 409 then GET failed:", g.status_code, g.text)
                    return {}

            print("[SHEET] Create failed:", r.status_code, r.text)
            return {}

        except Exception as e:
            print("[SHEET] Create/Get exception:", e)
            return {}
    
    def create_or_link_sheet_for_selected_token(self):
        idx = self._selected_token_index()
        if idx < 0 or idx >= len(self.tokens):
            QMessageBox.warning(self, "No Selection", "Select a token first.")
            return

        tok = self.tokens[idx]

        # Suggested defaults
        default_character_id = (tok.get("character_id") or tok.get("player_id") or "").strip()
        if not default_character_id:
            default_character_id = (tok.get("name") or "pc").strip().lower().replace(" ", "_")

        # Ask for character_id
        character_id, ok = QInputDialog.getText(
            self,
            "Character Sheet",
            "character_id (must match server file id):",
            text=str(default_character_id)
        )
        if not ok or not character_id.strip():
            return
        character_id = character_id.strip()

        # Ask for player_id
        default_player_id = (tok.get("player_id") or character_id).strip()
        player_id, ok = QInputDialog.getText(
            self,
            "Character Sheet",
            "player_id (owner / account name for player):",
            text=str(default_player_id)
        )
        if not ok or not player_id.strip():
            return
        player_id = player_id.strip()

        payload = {
            "character_id": character_id,
            "player_id": player_id,
            "base_stats": {
                "max_hp": int(tok.get("max_hp", 10)),
                "ac": int(tok.get("ac", 10)),
                "movement": int(tok.get("movement", 30)),
                "attack_modifier": int(tok.get("attack_modifier", 0)),
                "weapon_id": (tok.get("weapon_id") or "").strip(),
                "armor_id": (tok.get("armor_id") or "").strip(),
                "vision_ft": int(tok.get("vision_ft", 60)),
            },
            "resources": {
                "current_hp": int(tok.get("max_hp", 10))
            }
        }

        sheet = self._post_or_get_character(character_id, payload)
        if not sheet:
            QMessageBox.critical(self, "Failed", "Could not create or fetch character sheet from server.")
            return

        # Make this token sheet-backed
        tok["stat_source"] = "character_sheet"
        tok["player_id"] = player_id
        tok["character_id"] = character_id

        # Recommended for your spawn gating logic
        tok["kind"] = tok.get("kind") or "pc"
        tok["side"] = tok.get("side") or "player"

        # Optional: hydrate template with server authoritative stats
        bs = sheet.get("base_stats", {}) if isinstance(sheet, dict) else {}
        if isinstance(bs, dict):
            if "max_hp" in bs: tok["max_hp"] = int(bs["max_hp"])
            if "ac" in bs: tok["ac"] = int(bs["ac"])
            if "movement" in bs: tok["movement"] = int(bs["movement"])
            if "attack_modifier" in bs: tok["attack_modifier"] = int(bs["attack_modifier"])
            if "vision_ft" in bs: tok["vision_ft"] = int(bs["vision_ft"])
            if bs.get("weapon_id"): tok["weapon_id"] = str(bs.get("weapon_id"))
            if bs.get("armor_id"): tok["armor_id"] = str(bs.get("armor_id"))

        # Save triggers tokens_updated.emit() already
        self.save_tokens(silent=True)

        QMessageBox.information(
            self,
            "Sheet Linked",
            f"Token is now sheet-backed.\nplayer_id={player_id}\ncharacter_id={character_id}"
        )