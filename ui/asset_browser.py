import os
import json
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QListWidget, QListWidgetItem, QTabWidget
from PyQt5.QtGui import QIcon
from PyQt5.QtCore import Qt, pyqtSignal


class AssetBrowser(QWidget):
    map_selected = pyqtSignal(str)
    token_selected = pyqtSignal(dict)  # emits {"template_id": "..."} for templates

    def __init__(self, campaign_path):
        super().__init__()
        self.campaign_path = campaign_path

        layout = QVBoxLayout()
        self.tabs = QTabWidget()

        self.map_list = QListWidget()
        self.map_list.itemDoubleClicked.connect(self.select_map)
        self.tabs.addTab(self.map_list, "Maps")

        self.token_list = QListWidget()
        self.token_list.itemDoubleClicked.connect(self.select_token)
        self.tabs.addTab(self.token_list, "Tokens")

        layout.addWidget(QLabel("Asset Browser"))
        layout.addWidget(self.tabs)
        self.setLayout(layout)

        self.load_assets()

    # Backwards-compat: allow MainWindow to call refresh()
    def refresh(self):
        self.load_assets()

    def _load_token_templates(self):
        """
        Supports BOTH tokens.json shapes:

        A) Legacy list-of-dicts:
           [
             {"template_id": "...", "name": "...", "icon": "..."},
             ...
           ]

        B) New dict wrapper:
           {"templates": { "<template_id>": { ... }, ... }}
           OR {"templates": [ {...}, {...} ]}
        """
        tokens_file = os.path.join(self.campaign_path, "tokens.json")
        if not os.path.exists(tokens_file):
            return []

        try:
            with open(tokens_file, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception:
            return []

        # Case A: list
        if isinstance(raw, list):
            # filter only dicts; ignore strings/garbage
            return [t for t in raw if isinstance(t, dict)]

        # Case B: dict
        if isinstance(raw, dict):
            templates = raw.get("templates", None)

            # templates stored as dict keyed by template_id
            if isinstance(templates, dict):
                out = []
                for tid, tdata in templates.items():
                    if not isinstance(tdata, dict):
                        continue
                    # ensure template_id exists
                    if not tdata.get("template_id"):
                        tdata = dict(tdata)
                        tdata["template_id"] = tid
                    out.append(tdata)
                return out

            # templates stored as list
            if isinstance(templates, list):
                return [t for t in templates if isinstance(t, dict)]

        return []

    def load_assets(self):
        maps_path = os.path.join(self.campaign_path, "maps")
        icons_path = os.path.join(self.campaign_path, "tokens")

        self.map_list.clear()
        self.token_list.clear()

        # ---- Maps ----
        if os.path.exists(maps_path):
            for file in os.listdir(maps_path):
                if file.lower().endswith((".png", ".jpg", ".bmp", ".jpeg")):
                    item = QListWidgetItem(file)
                    item.setData(Qt.UserRole, os.path.join(maps_path, file))
                    self.map_list.addItem(item)

        # ---- Token Templates ----
        templates = self._load_token_templates()
        for token in templates:
            name = token.get("name", "Unknown")
            template_id = str(token.get("template_id", "")).strip()
            icon_file = token.get("icon", "")

            # If no template_id, skip (can’t spawn safely)
            if not template_id:
                continue

            item = QListWidgetItem(name)

            icon_path = os.path.join(icons_path, icon_file) if icon_file else ""
            if icon_path and os.path.exists(icon_path):
                item.setIcon(QIcon(icon_path))

            item.setData(Qt.UserRole, {"template_id": template_id})
            self.token_list.addItem(item)

    def select_map(self, item):
        path = item.data(Qt.UserRole)
        self.map_selected.emit(path)

    def select_token(self, item):
        payload = item.data(Qt.UserRole) or {}
        template_id = payload.get("template_id", "")

        if not template_id:
            print("[ASSET] Token template missing template_id; cannot spawn.")
            return

        self.token_selected.emit({"template_id": template_id})
