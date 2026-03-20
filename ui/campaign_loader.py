import os
from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QComboBox, QInputDialog, QMessageBox
)
from ui.items_db import ensure_items_db

class CampaignLoader(QDialog):
    def __init__(self, campaign_root="campaigns/"):
        super().__init__()
        self.setWindowTitle("Select Campaign")
        self.campaign_root = campaign_root
        self.selected_campaign = None

        layout = QVBoxLayout()

        self.label = QLabel("Choose a campaign to load:")
        layout.addWidget(self.label)

        self.combo = QComboBox()
        self.refresh_campaigns()
        layout.addWidget(self.combo)

        btn_layout = QHBoxLayout()
        load_btn = QPushButton("Load Campaign")
        new_btn = QPushButton("New Campaign")
        btn_layout.addWidget(load_btn)
        btn_layout.addWidget(new_btn)

        layout.addLayout(btn_layout)
        self.setLayout(layout)

        load_btn.clicked.connect(self.load_campaign)
        new_btn.clicked.connect(self.create_campaign)

    def refresh_campaigns(self):
        os.makedirs(self.campaign_root, exist_ok=True)
        campaigns = [
            f for f in os.listdir(self.campaign_root)
            if os.path.isdir(os.path.join(self.campaign_root, f))
        ]
        self.combo.clear()
        self.combo.addItems(campaigns)

    def load_campaign(self):
        selected = self.combo.currentText()
        if selected:
            self.selected_campaign = os.path.join(self.campaign_root, selected)
            self.accept()

    def create_campaign(self):
        name, ok = QInputDialog.getText(self, "New Campaign", "Enter campaign name:")
        if not (ok and name):
            return

        path = os.path.join(self.campaign_root, name)
        try:
            # Locked structure
            for d in ["maps", "tokens", "portraits", "encounters", "shops", "characters", "logs"]:
                os.makedirs(os.path.join(path, d), exist_ok=True)

            # Ensure items.json exists/valid (this only creates items.json; no folders)
            ensure_items_db(path)

            # Ensure tokens.json exists in the canonical format TokenManager expects (list)
            tokens_path = os.path.join(path, "tokens.json")
            if not os.path.exists(tokens_path):
                with open(tokens_path, "w", encoding="utf-8") as f:
                    f.write("[]\n")

            # Minimal campaign metadata
            campaign_json = os.path.join(path, "campaign.json")
            if not os.path.exists(campaign_json):
                with open(campaign_json, "w", encoding="utf-8") as f:
                    f.write('{\n  "name": "%s",\n  "ruleset_id": "default",\n  "grid_ft": 5,\n  "notes": ""\n}\n' % name)

            # Create log file
            log_path = os.path.join(path, "logs", "campaign_log.jsonl")
            open(log_path, "a", encoding="utf-8").close()

            self.refresh_campaigns()
            index = self.combo.findText(name)
            if index >= 0:
                self.combo.setCurrentIndex(index)

        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to create campaign:\n{e}")

