"""GUI 공통 타이포그래피, 창 크기, 임계값 범위 및 폴더 선택 유틸리티 모듈"""

import os

from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFileDialog, QWidget

from src.core.config import collect_setting_bounds
from src.core.ui_state import PREVIEW_BACKEND_BUILTIN, PREVIEW_BACKEND_QUICKLOOK

# 미리 보기 방식 표시 순서와 라벨, 편집 메뉴와 환경 설정이 공유
PREVIEW_BACKEND_LABELS: tuple[tuple[str, str], ...] = (
    (PREVIEW_BACKEND_BUILTIN, "내장 미리 보기"),
    (PREVIEW_BACKEND_QUICKLOOK, "QuickLook"),
)

# 공통 타이포그래피
FONT_UI_FAMILY: str = "Malgun Gothic"
FONT_MONO_FAMILY: str = "Consolas"
FONT_UI_PT: int = 10
FONT_TABLE_HEADER_PT: int = 9
FONT_TABLE_BADGE_PT: int = 9
FONT_STACK_UI: str = f'"Pretendard", "{FONT_UI_FAMILY}", "Segoe UI", sans-serif'
FONT_STACK_MONO: str = f'"{FONT_MONO_FAMILY}", monospace'

def make_ui_font(point_size: int = FONT_UI_PT) -> QFont:
    """힌팅 해제와 안티앨리어싱 우선 전략을 적용한 공용 UI 폰트 생성"""
    font = QFont(FONT_UI_FAMILY, point_size)
    font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
    return font


# 경로 표시 글꼴 대체 목록. 일부 한글 UI 글꼴은 역슬래시를 원화 기호로 그리므로 라틴 글꼴을 먼저 두고,
# 한글은 맑은 고딕이 맡도록 지정해 시스템 기본 대체 글꼴(굴림체) 선택을 막음
PATH_FONT_FAMILIES: tuple[str, ...] = ("Segoe UI", FONT_UI_FAMILY)

# 작업 폴더가 없을 때 경로 표시와 입력창에 공통으로 보이는 안내 문구
PATH_EMPTY_TEXT: str = "작업 폴더를 선택해 주세요"


def make_path_font(point_size: int = FONT_UI_PT, *, bold: bool = False) -> QFont:
    """경로 입력과 표시에 쓰는 글꼴 생성"""
    font = make_ui_font(point_size)
    font.setFamilies(list(PATH_FONT_FAMILIES))
    font.setBold(bold)
    return font


# 메인 윈도우 및 공통 툴바/타이머 상수
APP_WINDOW_TITLE: str = "Video Classifier"
WINDOW_DEFAULT_WIDTH: int = 1280
WINDOW_DEFAULT_HEIGHT: int = 820
TOOLBAR_ICON_BTN_SIZE: int = 36
TOOLBAR_ICON_SIZE_PX: int = 16
# 헤더 바 좌우 안쪽 여백과 카드 테두리 두께(접힌 탭 줄의 펼치기 버튼이 접기 버튼과 x 위치를 맞추는 기준)
HEADER_PADDING_X_PX: int = 16
CARD_BORDER_PX: int = 1
FILTER_DEBOUNCE_MS: int = 250
WORKER_CANCEL_WAIT_MS: int = 3000
WORKER_TERMINATE_WAIT_MS: int = 1000
MAX_LOG_HISTORY: int = 2000
CSV_PREVIEW_ROW_LIMIT: int = 100
CSV_PREVIEW_REFRESH_DEBOUNCE_MS: int = 150
SAVED_COLOR_THRESHOLD_MB: float = 0.05

# 강조 배경 위 아이콘/텍스트 공통 전경색
ON_ACCENT_COLOR: str = "#FFFFFF"

# 설정 다이얼로그 및 실시간 필터 바 공용 수치 입력 범위 (코어 스키마 메타데이터 기준 정의)
THRESHOLD_BOUNDS: dict[str, tuple[float, float]] = collect_setting_bounds()


__all__ = [
    "APP_WINDOW_TITLE",
    "CARD_BORDER_PX",
    "CSV_PREVIEW_REFRESH_DEBOUNCE_MS",
    "CSV_PREVIEW_ROW_LIMIT",
    "FILTER_DEBOUNCE_MS",
    "FONT_MONO_FAMILY",
    "FONT_STACK_MONO",
    "FONT_STACK_UI",
    "FONT_TABLE_BADGE_PT",
    "FONT_TABLE_HEADER_PT",
    "FONT_UI_FAMILY",
    "FONT_UI_PT",
    "HEADER_PADDING_X_PX",
    "MAX_LOG_HISTORY",
    "ON_ACCENT_COLOR",
    "THRESHOLD_BOUNDS",
    "TOOLBAR_ICON_BTN_SIZE",
    "TOOLBAR_ICON_SIZE_PX",
    "WINDOW_DEFAULT_HEIGHT",
    "WINDOW_DEFAULT_WIDTH",
    "WORKER_CANCEL_WAIT_MS",
    "WORKER_TERMINATE_WAIT_MS",
    "browse_directory",
    "make_path_font",
    "make_ui_font",
]


def browse_directory(
    parent: QWidget | None, title: str, current_path: str = ""
) -> str:
    """공용 폴더 선택 다이얼로그를 실행하고 선택된 경로(취소 시 빈 문자열)를 반환"""
    start_dir = current_path.strip() if current_path and os.path.isdir(current_path.strip()) else ""
    return QFileDialog.getExistingDirectory(parent, title, start_dir)
