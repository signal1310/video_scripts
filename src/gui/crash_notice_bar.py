"""이전 실행이 비정상 종료했음을 알리는 비차단 안내 막대 위젯

- 모달 대화상자를 띄우지 않고 메인 창 상단에 한 줄로 붙어 사고 보고서 폴더를 열 수 있게 함
- 버튼은 아이콘만 두고 접근성 이름과 툴팁으로 기능을 알림
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QWidget

from src.core.diagnostics.report import CrashReport
from src.gui.styles.icons import create_icon_button, get_icon, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

NOTICE_TEXT: str = "이전 실행이 정상적으로 끝나지 않았습니다. 비정상 종료 보고서를 저장했습니다."
NOTICE_TOOLTIP: str = "마지막 실행의 로그와 스택을 모아 둔 비정상 종료 보고서입니다."
OPEN_FOLDER_TOOLTIP: str = "이전 실행 비정상 종료 보고서 폴더를 엽니다."
OPEN_FOLDER_NAME: str = "비정상 종료 보고서 폴더 열기"
DISMISS_TOOLTIP: str = "이 안내를 닫습니다."
DISMISS_NAME: str = "안내 닫기"
_NOTICE_ICON_SIZE: int = 16
_NOTICE_MARGIN_X: int = 12
_NOTICE_MARGIN_Y: int = 8
_NOTICE_SPACING: int = 10
_NOTICE_BUTTON_SIZE: int = 28


class CrashNoticeBar(QFrame, ThemeAwareMixin):
    """비정상 종료 안내 문구, 보고서 폴더 열기 버튼, 닫기 버튼을 담은 가로 막대"""

    folder_requested = pyqtSignal()
    dismissed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CrashNoticeBar")
        # - QSS 배경색을 그리려면 스타일 배경 속성이 필요
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(_NOTICE_MARGIN_X, _NOTICE_MARGIN_Y, _NOTICE_MARGIN_X, _NOTICE_MARGIN_Y)
        layout.setSpacing(_NOTICE_SPACING)
        self._icon = QLabel(self)
        self._icon.setFixedSize(_NOTICE_ICON_SIZE, _NOTICE_ICON_SIZE)
        self._label = QLabel(NOTICE_TEXT, self)
        self._label.setObjectName("CrashNoticeText")
        self._label.setWordWrap(True)
        self._label.setToolTip(NOTICE_TOOLTIP)
        self._open_button = create_icon_button(
            self,
            OPEN_FOLDER_TOOLTIP,
            self.folder_requested.emit,
            accessible_name=OPEN_FOLDER_NAME,
            fixed_size=_NOTICE_BUTTON_SIZE,
        )
        set_button_icon(self._open_button, "folder-open", size=_NOTICE_ICON_SIZE, pad_right=0)
        self._dismiss_button = create_icon_button(
            self,
            DISMISS_TOOLTIP,
            self.dismiss,
            accessible_name=DISMISS_NAME,
            fixed_size=_NOTICE_BUTTON_SIZE,
        )
        set_button_icon(self._dismiss_button, "x", size=_NOTICE_ICON_SIZE, pad_right=0)
        layout.addWidget(self._icon)
        layout.addWidget(self._label, stretch=1)
        layout.addWidget(self._open_button)
        layout.addWidget(self._dismiss_button)
        self.init_theme_listener()

    def dismiss(self) -> None:
        """안내를 숨기고 닫힘을 알림"""
        self.hide()
        self.dismissed.emit()

    @property
    def open_button(self) -> QPushButton:
        """보고서 폴더 열기 버튼 반환"""
        return self._open_button

    @property
    def dismiss_button(self) -> QPushButton:
        """안내 닫기 버튼 반환"""
        return self._dismiss_button

    def apply_theme(self, palette: ThemePalette) -> None:
        """경고 색 아이콘을 현재 테마 팔레트에 맞춰 다시 그림"""
        icon = get_icon("alert-triangle", palette.accent_orange, _NOTICE_ICON_SIZE, palette)
        self._icon.setPixmap(icon.pixmap(_NOTICE_ICON_SIZE, _NOTICE_ICON_SIZE))
        set_button_icon(self._open_button, "folder-open", palette=palette, size=_NOTICE_ICON_SIZE, pad_right=0)
        set_button_icon(self._dismiss_button, "x", palette=palette, size=_NOTICE_ICON_SIZE, pad_right=0)


def attach_crash_notice(
    container: QWidget,
    layout: QHBoxLayout | None,
    on_open: Callable[[], None],
    *,
    parent: QWidget | None = None,
) -> CrashNoticeBar:
    """안내 막대를 만들어 열기 요청을 연결하고 컨테이너 레이아웃 맨 앞에 끼움"""
    bar = CrashNoticeBar(parent or container)
    bar.folder_requested.connect(on_open)
    if layout is not None:
        layout.insertWidget(0, bar)
    return bar


def attach_pending_crash_notice(
    container: QWidget,
    layout: Any,
    pending_report: CrashReport | None,
    on_open: Callable[[str], bool],
) -> CrashNoticeBar | None:
    """이번 시작에 준비된 사고 보고서가 있으면 메인 창 맨 위에 안내 막대를 붙임

    - 보고서가 없으면 아무것도 만들지 않아 평소 화면을 건드리지 않음
    - 보고서 폴더 열기는 호출자가 넘긴 함수로 처리해 이 위젯이 GUI 부트스트랩을 참조하지 않게 함
    """
    if pending_report is None:
        return None

    def _open_report() -> None:
        on_open(pending_report.directory)

    bar = attach_crash_notice(container, layout, _open_report)
    bar.show()
    return bar
