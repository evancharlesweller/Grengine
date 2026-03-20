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


class HealthItemInspectorPopup(QDialog):
    def __init__(self, item, icon_path=None):
        super().__init__()
        self.setWindowTitle(item.get("name", "Health Item"))

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
            ("Effect", "effect"),
            ("Type", "type")
        ]:
            value = item.get(key, "N/A")
            layout.addWidget(QLabel(f"<b>{label}:</b> {value}"))

        self.setLayout(layout)


class HealthSubTab(QWidget):
    def __init__(self, campaign_path):
        super().__init__()
        self.campaign_path = campaign_path
        self.icons_dir = os.path.join(campaign_path, "health", "icons")
        os.makedirs(self.icons_dir, exist_ok=True)

        self.health_items = []
        self.icon_source_path = None

        layout = QVBoxLayout()

        self.item_list = QListWidget()
        layout.addWidget(self.item_list)

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
        self.effect_input = QLineEdit()
        self.type_input = QLineEdit()
        form.addRow("Name:", self.name_input)
        form.addRow("Effect:", self.effect_input)
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

        self.add_button.clicked.connect(self.add_item)
        self.delete_button.clicked.connect(self.delete_item)
        self.save_button.clicked.connect(self.save_health_items)
        self.item_list.itemClicked.connect(self.load_selected_item)
        self.item_list.itemDoubleClicked.connect(self.show_item_popup)

        self.load_health_items()

    def load_health_items(self):
        items = load_items(self.campaign_path)
        self.health_items = items.get("health_items", [])
        self.refresh_list()

    def save_health_items(self):
        items = load_items(self.campaign_path)
        items["health_items"] = self.health_items
        save_items(self.campaign_path, items)
        QMessageBox.information(self, "Saved", "Health items saved to items.json")

    def refresh_list(self):
        self.item_list.clear()
        for it in self.health_items:
            name = it.get("name", "Unnamed")
            list_item = QListWidgetItem(name)

            icon_name = it.get("icon")
            if icon_name:
                icon_path = os.path.join(self.icons_dir, icon_name)
                if os.path.exists(icon_path):
                    list_item.setIcon(QIcon(icon_path))

            self.item_list.addItem(list_item)

    def load_selected_item(self, item):
        name = item.text()
        data = next((i for i in self.health_items if i.get("name") == name), None)
        if not data:
            return

        self.name_input.setText(data.get("name", ""))
        self.effect_input.setText(data.get("effect", ""))
        self.type_input.setText(data.get("type", ""))
        self.icon_path_input.setText(data.get("icon", "") or "")
        self.icon_source_path = None

    def show_item_popup(self, item):
        name = item.text()
        data = next((i for i in self.health_items if i.get("name") == name), None)
        if not data:
            return

        icon_path = None
        icon_name = data.get("icon")
        if icon_name:
            possible = os.path.join(self.icons_dir, icon_name)
            if os.path.exists(possible):
                icon_path = possible

        dialog = HealthItemInspectorPopup(data, icon_path)
        dialog.exec_()

    def add_item(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Health item must have a name.")
            return

        item = {
            "name": name,
            "effect": self.effect_input.text().strip(),
            "type": self.type_input.text().strip()
        }

        icon_name = self.icon_path_input.text().strip()
        if icon_name:
            item["icon"] = icon_name
            if self.icon_source_path and os.path.exists(self.icon_source_path):
                dest_path = os.path.join(self.icons_dir, icon_name)
                try:
                    if os.path.abspath(self.icon_source_path) != os.path.abspath(dest_path):
                        shutil.copy(self.icon_source_path, dest_path)
                except Exception as e:
                    QMessageBox.warning(self, "Warning", f"Failed to copy icon:\n{e}")

        self.health_items.append(item)
        self.refresh_list()
        self.clear_form()

    def delete_item(self):
        current = self.item_list.currentItem()
        if not current:
            return
        name = current.text()
        self.health_items = [i for i in self.health_items if i.get("name") != name]
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
        self.effect_input.clear()
        self.type_input.clear()
        self.icon_path_input.clear()
        self.icon_source_path = None
