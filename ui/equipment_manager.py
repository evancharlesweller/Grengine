# ui/equipment_manager.py
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QPushButton, QMessageBox


class EquipmentManagerTab(QWidget):
    """
    TEMPORARY / LOCKED-STRUCTURE VERSION

    The previous EquipmentManagerTab imported legacy equipment subtabs:
      - ui.equipment_subtabs.weapons_subtab
      - ui.equipment_subtabs.armor_subtab
      - ui.equipment_subtabs.health_subtab
      - ui.equipment_subtabs.misc_subtab

    Those subtabs were designed for the old campaign layout and typically
    auto-created category folders on campaign load (weapons/, armors/, etc).

    This replacement intentionally avoids those imports so campaigns stay
    self-contained and folder creation is limited to the locked structure.
    """

    def __init__(self, campaign_path: str):
        super().__init__()
        self.campaign_path = campaign_path

        layout = QVBoxLayout()

        title = QLabel("Equipment Manager")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        layout.addWidget(title)

        layout.addWidget(QLabel(
            "This tab has been temporarily simplified to lock the campaign structure.\n\n"
            "Your project now uses a single unified database:\n"
            "  campaigns/<campaign>/items.json\n\n"
            "Legacy category folders (weapons/, armors/, health/, misc/) are no longer used.\n\n"
            "Next step: we’ll replace this placeholder with a proper items.json editor UI."
        ))

        btn = QPushButton("OK (placeholder)")
        btn.clicked.connect(self._info)
        layout.addWidget(btn)

        layout.addStretch(1)
        self.setLayout(layout)

    def _info(self):
        QMessageBox.information(
            self,
            "Equipment Manager",
            "Equipment Manager placeholder is active.\n\n"
            "No legacy folders will be created on campaign load."
        )
