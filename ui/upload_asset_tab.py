import os
import shutil
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QComboBox, QPushButton,
    QLineEdit, QFileDialog, QMessageBox
)
from PyQt5.QtWidgets import QHBoxLayout, QListWidget

class UploadAssetTab(QWidget):
    def __init__(self, campaign_path, asset_browser):
        super().__init__()
        self.campaign_path = campaign_path
        self.asset_browser = asset_browser
        self.selected_file = None

        layout = QVBoxLayout()

        self.type_label = QLabel("Asset Type:")
        layout.addWidget(self.type_label)

        self.asset_type = QComboBox()
        self.asset_type.addItems(["Map", "Token"])
        layout.addWidget(self.asset_type)

        self.name_label = QLabel("Optional Name (or leave blank):")
        layout.addWidget(self.name_label)

        self.name_input = QLineEdit()
        layout.addWidget(self.name_input)

        self.file_btn = QPushButton("Choose File")
        self.file_btn.clicked.connect(self.pick_file)
        layout.addWidget(self.file_btn)

        self.upload_btn = QPushButton("Upload")
        self.upload_btn.clicked.connect(self.upload_asset)
        layout.addWidget(self.upload_btn)

        # Deletion UI
        layout.addWidget(QLabel("Delete Existing Asset:"))

        self.asset_list = QListWidget()
        layout.addWidget(self.asset_list)

        self.delete_btn = QPushButton("Delete Selected Asset")
        self.delete_btn.clicked.connect(self.delete_asset)
        layout.addWidget(self.delete_btn)

        # Update list when asset type changes
        self.asset_type.currentIndexChanged.connect(self.refresh_asset_list)
        self.refresh_asset_list()


        self.setLayout(layout)

    def pick_file(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Choose Asset File", "", "Image Files (*.png *.jpg *.bmp)")
        if filepath:
            self.selected_file = filepath
            self.file_btn.setText(os.path.basename(filepath))

    def upload_asset(self):
        if not self.selected_file:
            QMessageBox.warning(self, "No File", "Please select a file to upload.")
            return

        asset_type = self.asset_type.currentText().lower()
        folder = "maps" if asset_type == "map" else "tokens"
        target_dir = os.path.join(self.campaign_path, folder)

        os.makedirs(target_dir, exist_ok=True)

        # Get destination filename
        original_filename = os.path.basename(self.selected_file)
        name_override = self.name_input.text().strip()
        filename = f"{name_override}.png" if name_override else original_filename

        dest_path = os.path.join(target_dir, filename)

        try:
            shutil.copy(self.selected_file, dest_path)
            QMessageBox.information(self, "Success", f"{asset_type.capitalize()} uploaded successfully.")
            self.asset_browser.refresh()
            self.reset_form()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to upload asset:\n{e}")

    def reset_form(self):
        self.selected_file = None
        self.name_input.clear()
        self.file_btn.setText("Choose File")

    def refresh_asset_list(self):
        folder = "maps" if self.asset_type.currentText().lower() == "map" else "tokens"
        target_dir = os.path.join(self.campaign_path, folder)
        self.asset_list.clear()
        if os.path.exists(target_dir):
            for f in os.listdir(target_dir):
                if f.lower().endswith((".png", ".jpg", ".bmp", ".jpeg")):
                    self.asset_list.addItem(f)
    
    def delete_asset(self):
        selected_item = self.asset_list.currentItem()
        if not selected_item:
            QMessageBox.warning(self, "No Selection", "Please select an asset to delete.")
            return

        asset_type = self.asset_type.currentText().lower()
        folder = "maps" if asset_type == "map" else "tokens"
        target_dir = os.path.join(self.campaign_path, folder)
        file_path = os.path.join(target_dir, selected_item.text())

        try:
            os.remove(file_path)
            QMessageBox.information(self, "Deleted", f"{selected_item.text()} has been deleted.")
            self.refresh_asset_list()
            self.asset_browser.refresh()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to delete asset:\n{e}")


