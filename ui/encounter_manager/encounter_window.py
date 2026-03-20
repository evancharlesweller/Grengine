import os
from PyQt5.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton, QScrollArea, QFormLayout, QDialog
from PyQt5.QtCore import Qt

class EncounterWindow(QDialog):
    def __init__(self, token_data):
        super().__init__()
        self.setWindowTitle("Encounter Manager")
        self.setMinimumSize(400, 600)
        self.token_data = token_data  # List of dicts: name, hp, max_hp, x, y

        layout = QVBoxLayout()

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        content = QWidget()
        form_layout = QFormLayout()

        self.hp_inputs = {}

        for token in self.token_data:
            display_name = f"{token.get('name', 'Unknown')} @ ({token.get('x', '?')}, {token.get('y', '?')})"
            name_label = QLabel(display_name)
            hp_field = QLineEdit(str(token.get("hp", 10)))
            hp_field.setFixedWidth(60)
            form_layout.addRow(name_label, hp_field)
            self.hp_inputs[token["name"] + f"_{token['x']}_{token['y']}"] = hp_field

        content.setLayout(form_layout)
        scroll.setWidget(content)
        layout.addWidget(scroll)

        self.save_button = QPushButton("Save HP Changes")
        self.save_button.clicked.connect(self.save_changes)
        layout.addWidget(self.save_button)

        self.setLayout(layout)

    def save_changes(self):
        for token in self.token_data:
            key = token["name"] + f"_{token['x']}_{token['y']}"
            if key in self.hp_inputs:
                try:
                    token["hp"] = int(self.hp_inputs[key].text())
                except ValueError:
                    pass
