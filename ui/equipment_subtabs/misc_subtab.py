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


class MiscItemInspectorPopup(QDialog):
    def __init__(self, item, icon_path=None):
        super().__init__()
        self.setWindowTitle(item.get("name", "Misc Item"))

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
            ("Description", "description"),
            ("Type", "type")
        ]:
            value = item.get(key, "N/A")
            layout.addWidget(QLabel(f"<b>{label}:</b> {value}"))

        self.setLayout(layout)


class MiscSubTab(QWidget):
    def __init__(self, campaign_path):
        super().__init__()
        self.campaign_path = campaign_path
        self.icons_dir = os.path.join(campaign_path, "misc", "icons")
        os.makedirs(self.icons_dir, exist_ok=True)

        self.misc_items = []
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
        self.description_input = QLineEdit()
        self.type_input = QLineEdit()
        form.addRow("Name:", self.name_input)
        form.addRow("Description:", self.description_input)
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
        self.save_button.clicked.connect(self.save_misc_items)
        self.item_list.itemClicked.connect(self.load_selected_item)
        self.item_list.itemDoubleClicked.connect(self.show_item_popup)

        self.load_misc_items()

    def load_misc_items(self):
        items = load_items(self.campaign_path)
        self.misc_items = items.get("misc_items", [])
        self.refresh_list()

    def save_misc_items(self):
        items = load_items(self.campaign_path)
        items["misc_items"] = self.misc_items
        save_items(self.campaign_path, items)
        QMessageBox.information(self, "Saved", "Misc items saved to items.json")

    def refresh_list(self):
        self.item_list.clear()
        for it in self.misc_items:
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
        data = next((i for i in self.misc_items if i.get("name") == name), None)
        if not data:
            return

        self.name_input.setText(data.get("name", ""))
        self.description_input.setText(data.get("description", ""))
        self.type_input.setText(data.get("type", ""))
        self.icon_path_input.setText(data.get("icon", "") or "")
        self.icon_source_path = None

    def show_item_popup(self, item):
        name = item.text()
        data = next((i for i in self.misc_items if i.get("name") == name), None)
        if not data:
            return

        icon_path = None
        icon_name = data.get("icon")
        if icon_name:
            possible = os.path.join(self.icons_dir, icon_name)
            if os.path.exists(possible):
                icon_path = possible

        dialog = MiscItemInspectorPopup(data, icon_path)
        dialog.exec_()

    def add_item(self):
        name = self.name_input.text().strip()
        if not name:
            QMessageBox.warning(self, "Missing Name", "Misc item must have a name.")
            return

        item = {
            "name": name,
            "description": self.description_input.text().strip(),
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

        self.misc_items.append(item)
        self.refresh_list()
        self.clear_form()

    def delete_item(self):
        current = self.item_list.currentItem()
        if not current:
            return
        name = current.text()
        self.misc_items = [i for i in self.misc_items if i.get("name") != name]
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
        self.description_input.clear()
        self.type_input.clear()
        self.icon_path_input.clear()
        self.icon_source_path = None
