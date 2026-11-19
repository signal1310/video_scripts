"""PyQt6 데스크톱 GUI 패키지 및 주요 UI 심볼 export"""

import sys

from PyQt6.QtWidgets import QApplication

from src.core.logging_setup import ensure_console_logging, ensure_file_logging
from src.core.settings.app_state import ensure_default_config_files
from src.gui.constants import make_ui_font
from src.gui.controllers.worker import ClassifierWorker
from src.gui.main_window import MainWindow
from src.gui.settings.dialogs.settings_dialog import SettingsDialog
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table_model import VideoTableModel
from src.gui.widgets import BentoCard
from src.service.session import ClassifierSession


def run_gui(
    classifier: ClassifierSession | None = None,
    initial_dir: str | None = None,
) -> int:
    """PyQt6 QApplication 인스턴스를 초기화하고 메인 GUI 윈도우를 실행"""
    app_inst = QApplication.instance()
    owns_app = False
    if isinstance(app_inst, QApplication):
        app = app_inst
    else:
        app = QApplication(sys.argv)
        owns_app = True

    app.setFont(make_ui_font())
    ensure_console_logging()
    ensure_file_logging()
    ensure_default_config_files()

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
