"""파일 로그를 기본 프로그램으로 여는 GUI 동작 모듈"""

import os

from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QMessageBox, QWidget

from src.core.logging_setup import default_log_file_path


def open_log_file(parent: QWidget | None) -> None:
    """애플리케이션 로그 파일을 기본 프로그램으로 열고, 파일이 없거나 열 수 없으면 안내"""
    path = default_log_file_path()
    if not os.path.isfile(path):
        QMessageBox.information(
            parent,
            "로그 파일 열기",
            "아직 저장된 로그 파일이 없습니다.\n\n안내: 작업을 한 번 실행하면 로그 파일이 만들어집니다.",
        )
        return
    if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
        QMessageBox.warning(
            parent,
            "로그 파일 열기",
            "로그 파일을 열지 못했습니다.\n\n안내: 기본으로 연결된 텍스트 편집기가 있는지 확인해 주세요.",
        )
