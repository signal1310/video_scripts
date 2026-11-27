"""GUI 공통 타이포그래피, 창 크기, 임계값 범위, 폴더 선택 및 지연 호출 유틸리티 모듈"""

import os
from collections.abc import Callable, Iterable

from PyQt6.QtCore import QObject, QTimer
from PyQt6.QtGui import QAction, QActionGroup, QFont
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
HEADER_PADDING_Y_PX: int = 10
# 헤더 한 줄 안의 위젯 사이 간격(접힌 줄도 같은 간격으로 폴더 아이콘과 이름을 띄움)
HEADER_SPACING_PX: int = 10
# 작업 폴더 이름 글꼴 크기와 글자 좌우 안쪽 여백(펼친 헤더와 접힌 줄이 같은 크기, 두께와 시작 위치를 씀)
FOLDER_NAME_FONT_PT: int = 11
FOLDER_NAME_PAD_X_PX: int = 8
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

# 좁게 보기 표시 상태를 표, 헤더, 델리게이트가 함께 읽는 QSS 동적 속성 키와 값
COMPACT_PROPERTY: str = "compact"
COMPACT_VALUE_ON: str = "true"
COMPACT_VALUE_OFF: str = "false"

# 표 열 프리셋 키와 표시 이름(TERMINOLOGY.md 6.17절 승인 문구), widgets와 table이 함께 씀
PRESET_DEFAULT: str = "default"
PRESET_QUALITY: str = "quality"
PRESET_SPECS: str = "specs"
PRESET_ALL: str = "all"
PRESET_CUSTOM: str = "custom"
CUSTOM_PRESET_LABEL: str = "사용자 지정"
COLUMN_PRESETS: dict[str, str] = {
    PRESET_DEFAULT: "기본",
    PRESET_QUALITY: "화질, 해상도",
    PRESET_SPECS: "기술 사양",
    PRESET_ALL: "전체",
}


def preset_label(preset_key: str) -> str:
    """프리셋 키의 표시 이름, 사용자 지정과 알 수 없는 키는 사용자 지정으로 표시"""
    return COLUMN_PRESETS.get(preset_key, CUSTOM_PRESET_LABEL)


def clear_radio_checks(group: QActionGroup, actions: Iterable[QAction]) -> None:
    """배타적 라디오 그룹에서 모든 항목의 체크를 잠시 해제

    - 배타 그룹은 체크된 항목을 직접 해제할 수 없으므로 잠시 배타를 풀었다가 되돌림
    """
    group.setExclusive(False)
    for action in actions:
        action.setChecked(False)
    group.setExclusive(True)


__all__ = [
    "APP_WINDOW_TITLE",
    "CARD_BORDER_PX",
    "COLUMN_PRESETS",
    "COMPACT_PROPERTY",
    "COMPACT_VALUE_OFF",
    "COMPACT_VALUE_ON",
    "CSV_PREVIEW_REFRESH_DEBOUNCE_MS",
    "CSV_PREVIEW_ROW_LIMIT",
    "CUSTOM_PRESET_LABEL",
    "FILTER_DEBOUNCE_MS",
    "FOLDER_NAME_FONT_PT",
    "FOLDER_NAME_PAD_X_PX",
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
    "HEADER_PADDING_Y_PX",
    "HEADER_SPACING_PX",
    "MAX_LOG_HISTORY",
    "MUTE_NOTICE",
    "ON_ACCENT_COLOR",
    "PAUSE_NOTICE",
    "PLAY_NOTICE",
    "PRESET_ALL",
    "PRESET_CUSTOM",
    "PRESET_DEFAULT",
    "PRESET_QUALITY",
    "PRESET_SPECS",
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
    "clear_radio_checks",
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
