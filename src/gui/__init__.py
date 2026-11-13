import sys

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QApplication

from src.classifier import VideoClassifier
from src.gui.constants import FONT_UI_FAMILY, FONT_UI_PT
from src.gui.dialogs.settings_dialog import SettingsDialog
from src.gui.main_window import MainWindow
from src.gui.proxy_model import VideoSortFilterProxyModel
from src.gui.table_model import VideoTableModel
from src.gui.widgets import BentoCard
from src.gui.worker import ClassifierWorker


def run_gui(
    classifier: VideoClassifier | None = None,
    initial_dir: str | None = None,
) -> int:
    """
    - PyQt6 QApplication 인스턴스를 초기화하고 메인 GUI 윈도우를 실행.
    """
    app_inst = QApplication.instance()
    owns_app = False
    if isinstance(app_inst, QApplication):
        app = app_inst
    else:
        app = QApplication(sys.argv)
        owns_app = True

    app.setFont(QFont(FONT_UI_FAMILY, FONT_UI_PT))

    window = MainWindow(classifier=classifier, initial_dir=initial_dir)
    window.show()

    if owns_app:
        return app.exec()
    return 0


__all__ = [
    "BentoCard",
    "ClassifierWorker",
    "MainWindow",
    "SettingsDialog",
    "VideoSortFilterProxyModel",
    "VideoTableModel",
    "run_gui",
]
