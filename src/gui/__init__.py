"""PyQt6 데스크톱 GUI 패키지 및 주요 UI 심볼 export"""

import os
import sys

from PyQt6.QtWidgets import QApplication

from src.core.diagnostics.settings import resolve_developer_mode
from src.core.logging_setup import ensure_console_logging, ensure_file_logging
from src.core.settings.app_state import ensure_default_config_files, load_app_state
from src.gui.constants import make_ui_font
from src.gui.controllers.worker import ClassifierWorker
from src.gui.diagnostics_qt import start_gui_diagnostics, start_stall_watchdog_when_ready
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


def configured_developer_mode() -> bool:
    """저장된 설정 파일에서 개발자 모드 값을 읽음. 읽지 못하면 꺼진 상태"""
    return load_app_state().to_classifier_config().diagnostics.developer_mode


def run_gui(
    classifier: ClassifierSession | None = None,
    initial_dir: str | None = None,
) -> int:
    """PyQt6 QApplication 인스턴스를 초기화하고 메인 GUI 윈도우를 실행"""
    configure_multimedia_environment()
    # - QApplication 생성 중 메시지까지 파일에 남도록 로그 설정과 진단을 먼저 시작
    bootstrap_diagnostics()
    app_inst = QApplication.instance()
    owns_app = False
    if isinstance(app_inst, QApplication):
        app = app_inst
    else:
        app = QApplication(sys.argv)
        owns_app = True
    app.setFont(make_ui_font())
    ensure_default_config_files()

    window = MainWindow(classifier=classifier, initial_dir=initial_dir)
    window.show()

    # - 하트비트 QTimer는 QApplication이 생긴 뒤에만 울리므로 앱과 창이 준비된 뒤에 켬
    # - 이벤트 루프 직전에 켜야 창 구성에 걸린 시간을 화면 멈춤으로 잘못 세지 않음
    start_stall_watchdog_when_ready()

    if owns_app:
        return app.exec()
    return 0


def bootstrap_diagnostics(*, developer_mode: bool | None = None) -> None:
    """로그 파일과 콘솔 기록을 먼저 준비한 뒤 진단 런타임을 시작

    - 순서를 뒤집으면 시작 직후 기록이 파일 핸들러 없이 버려짐
    - 개발자 모드를 주지 않으면 로그 준비 뒤 저장된 설정과 환경 변수(VIDEO_SCRIPTS_DEV)로 정함
    - 여러 번 호출해도 각 설정이 중복 핸들러를 만들지 않음
    """
    ensure_console_logging()
    ensure_file_logging()
    if developer_mode is None:
        developer_mode = resolve_developer_mode(configured_developer_mode())
    start_gui_diagnostics(developer_mode=developer_mode)


__all__ = [
    "BentoCard",
    "ClassifierWorker",
    "MainWindow",
    "SettingsDialog",
    "VideoSortFilterProxyModel",
    "VideoTableModel",
    "bootstrap_diagnostics",
    "configure_multimedia_environment",
    "run_gui",
]
