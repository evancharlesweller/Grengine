import os
import json
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QListWidget,
    QComboBox, QListWidgetItem, QMessageBox, QToolTip
)
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt

from ui.items_db import resolve_item


class ShopPanel(QWidget):
    def __init__(self, campaign_path, send_item_callback):
        super().__init__()
        self.campaign_path = campaign_path
        self.send_item_callback = send_item_callback

        # items shown in UI are resolved full dicts
        self.items = []

        layout = QVBoxLayout()

        self.shop_selector = QComboBox()
        self.shop_selector.currentTextChanged.connect(self.load_shop_items)
        layout.addWidget(QLabel("Select Shop:"))
        layout.addWidget(self.shop_selector)

        self.item_list = QListWidget()
        self.item_list.itemClicked.connect(self.show_tooltip)
        layout.addWidget(self.item_list)

        send_row = QHBoxLayout()
        self.player_selector = QComboBox()  # populate externally later
        self.send_button = QPushButton("Send to Player")
        self.send_button.clicked.connect(self.send_item_to_player)
        send_row.addWidget(QLabel("To Player:"))
        send_row.addWidget(self.player_selector)
        send_row.addWidget(self.send_button)

        layout.addLayout(send_row)
        self.setLayout(layout)

        self.load_shop_list()

    def load_shop_list(self):
        shop_dir = os.path.join(self.campaign_path, "shops")
        os.makedirs(shop_dir, exist_ok=True)
        shops = [f[:-5] for f in os.listdir(shop_dir) if f.endswith(".json")]
        self.shop_selector.clear()
        self.shop_selector.addItems(shops)

        # Auto-load first shop if present
        if shops:
            self.load_shop_items(self.shop_selector.currentText())

    def load_shop_items(self, shop_name: str):
        self.items.clear()
        self.item_list.clear()

        if not shop_name:
            return

        shop_file = os.path.join(self.campaign_path, "shops", f"{shop_name}.json")
        if not os.path.exists(shop_file):
            return

        try:
            with open(shop_file, "r") as f:
                raw = json.load(f)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to load shop file:\n{e}")
            return

        # Supported shapes:
        # 1) ["item_id", "item_id2", ...]
        # 2) [{"item_id":"..."}, ...]
        # 3) full item dicts (legacy): [{"name":"...", ...}, ...]
        refs = []
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, str):
                    refs.append(entry)
                elif isinstance(entry, dict) and entry.get("item_id"):
                    refs.append(entry["item_id"])
                elif isinstance(entry, dict) and entry.get("id"):
                    refs.append(entry["id"])
                elif isinstance(entry, dict) and entry.get("name"):
                    # legacy full item dict — accept as-is
                    self.items.append(entry)
        else:
            QMessageBox.warning(self, "Shop Format", "Shop file must be a JSON list.")
            return

        # Resolve refs -> item dicts
        for ref in refs:
            item = resolve_item(self.campaign_path, ref)
            if item:
                self.items.append(item)
            else:
                print(f"[SHOP] Could not resolve item ref: {ref}")

        # Populate UI
        for item in self.items:
            name = item.get("name", item.get("id", "Unnamed"))
            list_item = QListWidgetItem(name)

            icon = item.get("icon")
            if icon:
                icon_path = os.path.join(self.campaign_path, "items", "icons", icon)
                if os.path.exists(icon_path):
                    list_item.setIcon(QIcon(icon_path))

            # store id/ref for lookup
            list_item.setData(Qt.UserRole, item.get("id", name))
            self.item_list.addItem(list_item)

    def show_tooltip(self, item_widget: QListWidgetItem):
        ref = item_widget.data(Qt.UserRole) or ""
        item_data = None

        # find by id first
        for it in self.items:
            if it.get("id") == ref:
                item_data = it
                break

        # fallback: find by name
        if item_data is None:
            name = item_widget.text()
            item_data = next((i for i in self.items if i.get("name") == name), None)

        if not item_data:
            return

        # make tooltip readable (skip huge nested dicts)
        lines = []
        for k, v in item_data.items():
            if isinstance(v, dict):
                continue
            lines.append(f"{k}: {v}")
        tooltip = "\n".join(lines)

        QToolTip.showText(self.mapToGlobal(self.item_list.pos()), tooltip)

    def send_item_to_player(self):
        current_item = self.item_list.currentItem()
        if not current_item:
            QMessageBox.warning(self, "No Item", "Select an item to send.")
            return

        player = self.player_selector.currentText()
        if not player:
            QMessageBox.warning(self, "No Player", "Select a player to receive the item.")
            return

        ref = current_item.data(Qt.UserRole) or ""
        item_data = None

        for it in self.items:
            if it.get("id") == ref:
                item_data = it
                break

        if item_data is None:
            # fallback by name
            name = current_item.text()
            item_data = next((i for i in self.items if i.get("name") == name), None)

        if not item_data:
            QMessageBox.warning(self, "Missing Item", "Could not resolve selected item.")
            return

        self.send_item_callback(player, item_data)
        QMessageBox.information(self, "Sent", f"{item_data.get('name','Item')} sent to {player}.")