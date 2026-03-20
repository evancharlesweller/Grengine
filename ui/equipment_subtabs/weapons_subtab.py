import os
import shutil
from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QListWidget, QPushButton,
    QHBoxLayout, QLineEdit, QLabel, QMessageBox, QFormLayout,
    QFileDialog, QListWidgetItem, QDialog
)
from PyQt5.QtGui import QPixmap, QIcon
from ui.items_db import load_items, save_items


class WeaponInspectorPopup(QDialog):
    def __init__(self, weapon, icon_path=None):
        super().__init__()
        self.setWindowTitle(weapon.get("name", "Weapon"))

        layout = QVBoxLayout()

        if icon_path and os.path.exists(icon_path):
            icon_label = QLabel()
            pixmap = QPixmap(icon_path).scaled(
                256, 256,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation
            )
            icon_label.setPixmap(pixmap)
            layout.addWidget(icon_label)

        for label, key in [
            ("Name", "name"),
            ("Damage", "damage"),
            ("Ammo", "ammo"),
            ("Range", "range"),
            ("Type", "type")
        ]:
            value = weapon.get(key, "N/A")
            layout.addWidget(QLabel(f"<b>{label}:</b> {value}"))

        self.setLayout(layout)


class WeaponsSubTab(QWidget):
    def __init__(self, campaign_path):
        super().__init__()
        self.campaign_path = campaign_path

        self.icons_dir = os.path.join(self.campaign_path, "weapons", "icons")
        os.makedirs(self.icons_dir, exist_ok=True)

        self.weapons = []
        self.icon_source_path = None

        layout = QVBoxLayout()

        self.weapon_list = QListWidget()
        layout.addWidget(self.weapon_list)

        self.preview_icon = QLabel()
        self.preview_icon.setFixedSize(128, 128)
        layout.addWidget(self.preview_icon)

        button_row = QHBoxLayout()
        self.add_button = QPushButton("Add")
        self.delete_button = QPushButton("Delete")
        self.save_button = QPushButton("Save")
        button_row.addWidget(self.add_button)
        button_row.addWidget(self.delete_button)
        button_row.addWidget(self.save_button)
        layout.addLayout(button_row)

        form = QFormLayout()
        self.name_input = QLineEdit()
        self.damage_input = QLineEdit()
        self.ammo_input = QLineEdit()
        self.range_input = QLineEdit()
        self.type_input = QLineEdit()
        form.addRow("Name:", self.name_input)
        form.addRow("Damage:", self.damage_input)
        form.addRow("Ammo Type:", self.ammo_input)
        form.addRow("Range (ft):", self.range_input)
        form.addRow("Type:", self.type_input)

        self.icon_path_input = QLineEdit()
        self.icon_path_input.setReadOnly(True)
        self.pick_icon_button = QPushButton("Select Icon")
        self.pick_icon_button.clicked.connect(self.select_icon)
        icon_layout = QHBoxLayout()
        icon_layout.addWidget(self.icon_path_input)
        icon_layout.addWidget(self.pick_icon_button)
        form.addRow("Icon Image:", icon_layout)

        layout.addLayout(form)

        self.setLayout(layout)

        self.weapon_list.itemClicked.connect(self.load_selected_weapon)

        self.add_button.clicked.connect(self.add_weapon)
        self.delete_button.clicked.connect(self.delete_weapon)
        self.save_button.clicked.connect(self.save_weapons)

        self.load_weapons()

    def load_weapons(self):
        items = load_items(self.campaign_path)
        self.weapons = items.get("weapons", [])
        self.refresh_list()

    def save_weapons(self):
        items = load_items(self.campaign_path)
        items["weapons"] = self.weapons
        save_items(self.campaign_path, items)
        QMessageBox.information(self, "Saved", "Weapons saved to items.json")

    def refresh_list(self):
        self.weapon_list.clear()
        for weapon in self.weapons:
            name = weapon.get("name", "Unnamed")
            item = QListWidgetItem(name)

            icon_name = weapon.get("icon")
            if icon_name:
                icon_path = os.path.join(self.icons_dir, icon_name)
                if os.path.exists(icon_path):
                    item.setIcon(QIcon(icon_path))

            self.weapon_list.addItem(item)

    def load_selected_weapon(self, item):
        name = item.text()
        weapon = next((w for w in self.weapons if w.get("name") == name), None)
        if not weapon:
            return

        self.name_input.setText(weapon.get("name", ""))
        self.damage_input.setText(weapon.get("damage", ""))
        self.ammo_input.setText(weapon.get("ammo", ""))
        self.range_input.setText(str(weapon.get("range", "")))
        self.type_input.setText(weapon.get("type", ""))
        self.icon_path_input.setText(weapon.get("icon", "") or "")
        self.icon_source_path = None

        icon_filename = weapon.get("icon")
        if icon_filename:
            icon_path = os.path.join(self.icons_dir, icon_filename)
            if os.path.exists(icon_path):
                pixmap = QPixmap(icon_path).scaled(
                    128, 128,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation
                )
                self.preview_icon.setPixmap(pixmap)
            else:
                self.preview_icon.clear()
        else:
            self.preview_icon.clear()

    def add_weapon(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Weapon must have a name.")
            return

        weapon = {
            "name": name,
            "damage": self.damage_input.text().strip(),
            "ammo": self.ammo_input.text().strip(),
            "range": int(self.range_input.text()) if self.range_input.text().isdigit() else 5,
            "type": self.type_input.text().strip()
        }

        icon_name = self.icon_path_input.text().strip()
        if icon_name:
            weapon["icon"] = icon_name
            if self.icon_source_path and os.path.exists(self.icon_source_path):
                dest_path = os.path.join(self.icons_dir, icon_name)
                try:
                    if os.path.abspath(self.icon_source_path) != os.path.abspath(dest_path):
                        shutil.copy(self.icon_source_path, dest_path)
                except Exception as e:
                    QMessageBox.warning(self, "Warning", f"Failed to copy icon:\n{e}")

        self.weapons.append(weapon)
        self.refresh_list()
        self.clear_form()

    def delete_weapon(self):
        current = self.weapon_list.currentItem()
        if not current:
            return
        name = current.text()
        self.weapons = [w for w in self.weapons if w.get("name") != name]
        self.refresh_list()
        self.clear_form()

    def select_icon(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "Choose Icon", "", "Image Files (*.png *.jpg *.bmp)"
        )
        if file_path:
            self.icon_source_path = file_path
            self.icon_path_input.setText(os.path.basename(file_path))

    def clear_form(self):
        self.name_input.clear()
        self.damage_input.clear()
        self.ammo_input.clear()
        self.range_input.clear()
        self.type_input.clear()
        self.icon_path_input.clear()
        self.preview_icon.clear()
        self.icon_source_path = None
