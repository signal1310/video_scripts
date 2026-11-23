"""GUI 공통 타이포그래피, 창 크기, 임계값 범위, 폴더 선택 및 지연 호출 유틸리티 모듈"""

import os
from collections.abc import Callable

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QFileDialog, QWidget

from src.core.config import collect_setting_bounds

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

# 미리 보기, 화질 비교 창 공용 재생 알림 문구
PLAY_NOTICE: str = "재생"
PAUSE_NOTICE: str = "일시중지"
FRAME_BACKWARD_NOTICE: str = "이전 프레임"
FRAME_FORWARD_NOTICE: str = "다음 프레임"
MUTE_NOTICE: str = "음소거"
UNMUTE_NOTICE: str = "음소거 해제"
_SEEK_NOTICE_TEMPLATE: str = "구간 이동 {offset}초"
_RATE_NOTICE_TEMPLATE: str = "재생 배속: {rate:g}배"
_NOTICE_MS_PER_SECOND: int = 1000

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
    "FRAME_BACKWARD_NOTICE",
    "FRAME_FORWARD_NOTICE",
    "HEADER_PADDING_X_PX",
    "MAX_LOG_HISTORY",
    "MUTE_NOTICE",
    "ON_ACCENT_COLOR",
    "PAUSE_NOTICE",
    "PLAY_NOTICE",
    "THRESHOLD_BOUNDS",
    "TOOLBAR_ICON_BTN_SIZE",
    "TOOLBAR_ICON_SIZE_PX",
    "UNMUTE_NOTICE",
    "WINDOW_DEFAULT_HEIGHT",
    "WINDOW_DEFAULT_WIDTH",
    "WORKER_CANCEL_WAIT_MS",
    "WORKER_TERMINATE_WAIT_MS",
    "browse_directory",
    "call_later",
    "make_path_font",
    "make_ui_font",
    "rate_notice",
    "seek_notice",
]


def seek_notice(delta_ms: int) -> str:
    """구간 이동 폭과 방향을 초 단위 문구로 만듦"""
    sign = "+" if delta_ms > 0 else "-"
    return _SEEK_NOTICE_TEMPLATE.format(offset=f"{sign}{abs(delta_ms) // _NOTICE_MS_PER_SECOND}")


def rate_notice(rate: float) -> str:
    """바뀐 재생 배속의 문구"""
    return _RATE_NOTICE_TEMPLATE.format(rate=rate)


def browse_directory(
    parent: QWidget | None, title: str, current_path: str = ""
) -> str:
    """공용 폴더 선택 다이얼로그를 실행하고 선택된 경로(취소 시 빈 문자열)를 반환"""
    start_dir = current_path.strip() if current_path and os.path.isdir(current_path.strip()) else ""
    return QFileDialog.getExistingDirectory(parent, title, start_dir)


def call_later(owner: QObject, delay_ms: int, callback: Callable[[], object]) -> None:
    """소유 객체의 자식 단발 타이머로 지연 호출을 예약

    - 소유 객체가 지워지면 타이머도 함께 지워져 콜백이 죽은 객체에 닿지 않음
    - 실행 뒤 타이머를 지워 예약 객체가 쌓이지 않게 함
    """
    timer = QTimer(owner)
    timer.setSingleShot(True)

    def fire() -> None:
        timer.deleteLater()
        callback()

    timer.timeout.connect(fire)
    timer.start(delay_ms)
