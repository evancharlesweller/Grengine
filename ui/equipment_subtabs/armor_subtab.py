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


class ArmorInspectorPopup(QDialog):
    def __init__(self, armor, icon_path=None):
        super().__init__()
        self.setWindowTitle(armor.get("name", "Armor"))

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
            ("AC Bonus", "ac_bonus"),
            ("Type", "type")
        ]:
            value = armor.get(key, "N/A")
            layout.addWidget(QLabel(f"<b>{label}:</b> {value}"))

        self.setLayout(layout)


class ArmorSubTab(QWidget):
    def __init__(self, campaign_path):
        super().__init__()
        self.campaign_path = campaign_path
        self.icons_dir = os.path.join(campaign_path, "armors", "icons")
        os.makedirs(self.icons_dir, exist_ok=True)

        self.armors = []
        self.icon_source_path = None

        layout = QVBoxLayout()

        self.armor_list = QListWidget()
        layout.addWidget(self.armor_list)

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
        self.ac_bonus_input = QLineEdit()
        self.type_input = QLineEdit()
        form.addRow("Name:", self.name_input)
        form.addRow("AC Bonus:", self.ac_bonus_input)
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

        self.add_button.clicked.connect(self.add_armor)
        self.delete_button.clicked.connect(self.delete_armor)
        self.save_button.clicked.connect(self.save_armors)
        self.armor_list.itemClicked.connect(self.load_selected_armor)
        self.armor_list.itemDoubleClicked.connect(self.show_armor_popup)

        self.load_armors()

    def load_armors(self):
        items = load_items(self.campaign_path)
        self.armors = items.get("armors", [])
        self.refresh_list()

    def save_armors(self):
        items = load_items(self.campaign_path)
        items["armors"] = self.armors
        save_items(self.campaign_path, items)
        QMessageBox.information(self, "Saved", "Armors saved to items.json")

    def refresh_list(self):
        self.armor_list.clear()
        for armor in self.armors:
            name = armor.get("name", "Unnamed")
            item = QListWidgetItem(name)

            icon_name = armor.get("icon")
            if icon_name:
                icon_path = os.path.join(self.icons_dir, icon_name)
                if os.path.exists(icon_path):
                    item.setIcon(QIcon(icon_path))

            self.armor_list.addItem(item)

    def load_selected_armor(self, item):
        name = item.text()
        armor = next((a for a in self.armors if a.get("name") == name), None)
        if not armor:
            return

        self.name_input.setText(armor.get("name", ""))
        self.ac_bonus_input.setText(str(armor.get("ac_bonus", "")))
        self.type_input.setText(armor.get("type", ""))
        self.icon_path_input.setText(armor.get("icon", "") or "")
        self.icon_source_path = None  # selecting an existing item doesn't imply local source file

    def show_armor_popup(self, item):
        name = item.text()
        armor = next((a for a in self.armors if a.get("name") == name), None)
        if not armor:
            return

        icon_path = None
        icon_name = armor.get("icon")
        if icon_name:
            possible = os.path.join(self.icons_dir, icon_name)
            if os.path.exists(possible):
                icon_path = possible

        dialog = ArmorInspectorPopup(armor, icon_path)
        dialog.exec_()

    def add_armor(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Armor must have a name.")
            return

        armor = {
            "name": name,
            "ac_bonus": self.ac_bonus_input.text().strip(),
            "type": self.type_input.text().strip()
        }

        # icon handling
        icon_name = self.icon_path_input.text().strip()
        if icon_name:
            armor["icon"] = icon_name

            # Only copy if we have a source file
            if self.icon_source_path and os.path.exists(self.icon_source_path):
                dest_path = os.path.join(self.icons_dir, icon_name)
                try:
                    # avoid copying onto itself
                    if os.path.abspath(self.icon_source_path) != os.path.abspath(dest_path):
                        shutil.copy(self.icon_source_path, dest_path)
                except Exception as e:
                    QMessageBox.warning(self, "Warning", f"Failed to copy icon:\n{e}")

        self.armors.append(armor)
        self.refresh_list()
        self.clear_form()

    def delete_armor(self):
        current = self.armor_list.currentItem()
        if not current:
            return
        name = current.text()
        self.armors = [a for a in self.armors if a.get("name") != name]
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
        self.ac_bonus_input.clear()
        self.type_input.clear()
        self.icon_path_input.clear()
        self.icon_source_path = None
