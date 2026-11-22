"""PyQt6 데스크톱 GUI 패키지 및 주요 UI 심볼 export"""

import os
import sys

from PyQt6.QtWidgets import QApplication

from src.core.logging_setup import ensure_console_logging, ensure_file_logging
from src.core.settings.app_state import ensure_default_config_files
from src.gui.constants import make_ui_font
from src.gui.controllers.worker import ClassifierWorker
from src.gui.main_window import MainWindow
from src.gui.settings.settings_dialog import SettingsDialog
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_model import VideoTableModel
from src.gui.widgets import BentoCard
from src.service.session import ClassifierSession

MULTIMEDIA_HW_DECODE_ENV: str = "QT_FFMPEG_DECODING_HW_DEVICE_TYPES"


def configure_multimedia_environment() -> None:
    """Qt 멀티미디어 디코더 환경을 앱 시작 전에 맞춤

    - Windows Qt 6 FFmpeg의 d3d11va, dxva2 NV12 하드웨어 디코더가 일부 1440p H.264 영상의
      아래쪽 줄을 세로로 복제해 깨뜨리므로 소프트웨어 디코더를 기본으로 씀
    - 사용자가 환경 변수를 지정했으면 그 값을 그대로 씀
    """
    if sys.platform == "win32":
        os.environ.setdefault(MULTIMEDIA_HW_DECODE_ENV, "none")


configure_multimedia_environment()


def run_gui(
    classifier: ClassifierSession | None = None,
    initial_dir: str | None = None,
) -> int:
    """PyQt6 QApplication 인스턴스를 초기화하고 메인 GUI 윈도우를 실행"""
    configure_multimedia_environment()
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
    "configure_multimedia_environment",
    "run_gui",
]
