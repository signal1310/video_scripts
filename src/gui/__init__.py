import sys
from typing import Optional
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from src.gui.main_window import BentoCard, MainWindow
from src.gui.models import VideoSortFilterProxyModel, VideoTableModel
from src.gui.settings_dialog import SettingsDialog
from src.gui.worker import ClassifierWorker
from src.classifier import VideoClassifier


def run_gui(
    classifier: Optional[VideoClassifier] = None,
    initial_dir: Optional[str] = None,
) -> int:
    """
    - PyQt6 QApplication 인스턴스를 초기화하고 메인 GUI 윈도우를 실행.
    """
    app = QApplication.instance()
    owns_app = False
    if app is None:
        app = QApplication(sys.argv)
        owns_app = True

    app.setFont(QFont("Malgun Gothic", 10))

    window = MainWindow(classifier=classifier, initial_dir=initial_dir)
    window.show()

    if owns_app:
        return app.exec()
    return 0


__all__ = [
    "MainWindow",
    "SettingsDialog",
    "BentoCard",
    "VideoTableModel",
    "VideoSortFilterProxyModel",
    "ClassifierWorker",
    "run_gui",
]
