import os

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QFileDialog, QWidget

from src.core.exception_rules import split_exception_tokens

# QSettings 식별자
UI_SETTINGS_ORG: str = "VideoClassifier"
UI_SETTINGS_APP: str = "Studio"

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

# 설정 다이얼로그 및 실시간 필터 바 공용 수치 입력 범위
THRESHOLD_BOUNDS: dict[str, tuple[float, float]] = {
    "over_bitrate_ratio": (1.01, 5.00),
    "low_bitrate_ratio": (0.10, 0.99),
    "keyframe_threshold": (0.5, 30.0),
    "small_footage_mb": (0.0, 500.0),
    "small_footage_min_saved_mb": (0.1, 50.0),
    "small_footage_cumulative_ratio": (0.01, 1.00),
    "small_footage_cumulative_mb": (10.0, 10000.0),
    "keyframe_1s_overhead_rate": (0.00, 0.50),
    "base_width": (320, 7680),
    "base_height": (240, 4320),
    "base_target_bitrate": (100, 50000),
    "area_scaling_exponent": (0.10, 1.50),
    "fps_scaling_exponent": (0.10, 1.50),
    "base_fps": (1.0, 240.0),
    "codec_weight_efficient": (0.10, 3.00),
    "codec_weight_legacy": (0.10, 5.00),
    "tabulate_filename_maxlen": (20, 300),
}


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
    "UI_SETTINGS_APP",
    "UI_SETTINGS_ORG",
    "WINDOW_DEFAULT_HEIGHT",
    "WINDOW_DEFAULT_WIDTH",
    "WINDOW_MIN_HEIGHT",
    "WINDOW_MIN_WIDTH",
    "WORKER_CANCEL_WAIT_MS",
    "WORKER_TERMINATE_WAIT_MS",
    "browse_directory",
    "get_app_settings",
    "split_exception_tokens",
]


def get_app_settings() -> QSettings:
    """애플리케이션 공용 QSettings 저장소 인스턴스를 반환."""
    return QSettings(UI_SETTINGS_ORG, UI_SETTINGS_APP)


def browse_directory(
    parent: QWidget | None, title: str, current_path: str = ""
) -> str:
    """공용 폴더 선택 다이얼로그를 실행하고 선택된 경로(취소 시 빈 문자열)를 반환."""
    start_dir = current_path.strip() if current_path and os.path.isdir(current_path.strip()) else ""
    return QFileDialog.getExistingDirectory(parent, title, start_dir)
