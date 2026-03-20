import os
import sys
import subprocess
from PyQt5.QtWidgets import QApplication, QDialog
from ui.main_window import MainWindow
from ui.campaign_loader import CampaignLoader

def start_roll_server():
    script_path = os.path.join("roll_server", "server_main.py")
    python_exe = sys.executable
    subprocess.Popen([python_exe, script_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def main():
    app = QApplication(sys.argv)

    campaign_selector = CampaignLoader()
    if campaign_selector.exec_() == QDialog.Accepted:
        campaign_path = campaign_selector.selected_campaign
        window = MainWindow(campaign_path=campaign_path)
        window.show()
        sys.exit(app.exec_())

if __name__ == "__main__":
    main()
