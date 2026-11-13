"""GUI 공통 타이포그래피, 창 크기, 임계값 범위 및 폴더 선택 유틸리티 모듈"""

import os

from PyQt6.QtWidgets import QFileDialog, QWidget

from src.core.config import collect_setting_bounds

# 공통 타이포그래피
FONT_UI_FAMILY: str = "Malgun Gothic"
FONT_MONO_FAMILY: str = "Consolas"
FONT_UI_PT: int = 10
FONT_TABLE_BADGE_PT: int = 9
FONT_STACK_UI: str = f'"Pretendard", "{FONT_UI_FAMILY}", "Segoe UI", sans-serif'
FONT_STACK_MONO: str = f'"{FONT_MONO_FAMILY}", monospace'

# 메인 윈도우 및 공통 툴바/타이머 상수
WINDOW_DEFAULT_WIDTH: int = 1540
WINDOW_DEFAULT_HEIGHT: int = 880
WINDOW_MIN_WIDTH: int = 1180
WINDOW_MIN_HEIGHT: int = 680
TOOLBAR_ICON_BTN_SIZE: int = 36
FILTER_DEBOUNCE_MS: int = 250
WORKER_CANCEL_WAIT_MS: int = 3000
WORKER_TERMINATE_WAIT_MS: int = 1000
MAX_LOG_HISTORY: int = 2000

# 강조 배경 위 아이콘/텍스트 공통 전경색
ON_ACCENT_COLOR: str = "#FFFFFF"

# 설정 다이얼로그 및 실시간 필터 바 공용 수치 입력 범위 (코어 스키마 메타데이터 단일 원천)
THRESHOLD_BOUNDS: dict[str, tuple[float, float]] = collect_setting_bounds()


__all__ = [
    "FILTER_DEBOUNCE_MS",
    "FONT_MONO_FAMILY",
    "FONT_STACK_MONO",
    "FONT_STACK_UI",
    "FONT_TABLE_BADGE_PT",
    "FONT_UI_FAMILY",
    "FONT_UI_PT",
    "MAX_LOG_HISTORY",
    "ON_ACCENT_COLOR",
    "THRESHOLD_BOUNDS",
    "TOOLBAR_ICON_BTN_SIZE",
    "WINDOW_DEFAULT_HEIGHT",
    "WINDOW_DEFAULT_WIDTH",
    "WINDOW_MIN_HEIGHT",
    "WINDOW_MIN_WIDTH",
    "WORKER_CANCEL_WAIT_MS",
    "WORKER_TERMINATE_WAIT_MS",
    "browse_directory",
]


def browse_directory(
    parent: QWidget | None, title: str, current_path: str = ""
) -> str:
    """공용 폴더 선택 다이얼로그를 실행하고 선택된 경로(취소 시 빈 문자열)를 반환"""
    start_dir = current_path.strip() if current_path and os.path.isdir(current_path.strip()) else ""
    return QFileDialog.getExistingDirectory(parent, title, start_dir)
