"""작업 기록 창: 로그 탭(조회, 복사, 비우기)과 내보내기 탭을 묶은 비모달 대화상자 모듈"""

from collections.abc import Callable
from enum import Enum
from typing import ClassVar

from PyQt6.QtGui import QClipboard
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QPlainTextEdit,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from src.gui.dialogs.export_panel import ExportPanel
from src.gui.styles.icons import create_icon_button, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin, ThemeManager, build_stylesheet

_ACTION_BUTTON_SIZE: int = 34
_ICON_SIZE_PX: int = 15
_EMPTY_LOG_TEXT: str = "작업을 시작하면 진행 내용과 결과가 여기에 표시됩니다."
_LOG_TAB_TITLE: str = "로그"
_EXPORT_TAB_TITLE: str = "내보내기"
_COPY_TOOLTIP: str = "로그 전체를 클립보드에 복사합니다."
_CLEAR_TOOLTIP: str = "화면의 로그만 비웁니다. 로그 파일은 유지됩니다."
_OPEN_FILE_TOOLTIP: str = "로그 파일을 기본 프로그램으로 엽니다."


class LogTab(Enum):
    """작업 기록 창의 탭 종류"""

    LOG = "log"
    EXPORT = "export"


class LogPanel(QWidget):
    """작업 로그 이력을 보여 주고 복사, 비우기, 로그 파일 열기 동작을 제공하는 패널"""

    def __init__(
        self,
        logs: list[str],
        on_clear: Callable[[], None] | None,
        on_open_log_file: Callable[[], None],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_clear: Callable[[], None] | None = on_clear

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setObjectName("LogViewer")
        self.text_edit.setReadOnly(True)
        self.set_logs(logs)
        layout.addWidget(self.text_edit, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)
        self.btn_copy = create_icon_button(
            self,
            _COPY_TOOLTIP,
            self._copy_logs,
            accessible_name="전체 복사",
            fixed_size=_ACTION_BUTTON_SIZE,
        )
        self.btn_clear = create_icon_button(
            self,
            _CLEAR_TOOLTIP,
            self._clear_logs,
            accessible_name="로그 비우기",
            fixed_size=_ACTION_BUTTON_SIZE,
        )
        self.btn_open_file = create_icon_button(
            self,
            _OPEN_FILE_TOOLTIP,
            on_open_log_file,
            accessible_name="로그 파일 열기",
            fixed_size=_ACTION_BUTTON_SIZE,
        )
        for btn in (self.btn_copy, self.btn_clear, self.btn_open_file):
            btn_row.addWidget(btn)
        layout.addLayout(btn_row)

    def set_logs(self, logs: list[str]) -> None:
        """로그 텍스트 전체 갱신 및 스크롤 최하단 이동"""
        self.text_edit.setPlainText("\n".join(logs) if logs else _EMPTY_LOG_TEXT)
        sb = self.text_edit.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 동작 버튼 아이콘을 갱신"""
        for btn, icon_name in (
            (self.btn_copy, "copy"),
            (self.btn_clear, "trash"),
            (self.btn_open_file, "external-link"),
        ):
            set_button_icon(btn, icon_name, palette=palette, size=_ICON_SIZE_PX, pad_right=0)

    def _copy_logs(self) -> None:
        cb: QClipboard | None = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.text_edit.toPlainText())

    def _clear_logs(self) -> None:
        if self._on_clear is not None:
            self._on_clear()
        self.text_edit.setPlainText(_EMPTY_LOG_TEXT)


class LogDialog(QDialog, ThemeAwareMixin):
    """로그 탭과 내보내기 탭을 묶은 비모달 작업 기록 창"""

    _TITLE: str = "작업 기록"
    _TAB_INDEX: ClassVar[dict[LogTab, int]] = {LogTab.LOG: 0, LogTab.EXPORT: 1}

    def __init__(
        self,
        logs: list[str],
        on_clear: Callable[[], None] | None,
        on_open_log_file: Callable[[], None],
        on_status: Callable[[str], None],
        *,
        busy: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle(self._TITLE)
        self.resize(760, 520)
        self.setMinimumSize(520, 360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.tabs = QTabWidget(self)
        self.tabs.setObjectName("SettingsTabs")
        self.log_panel = LogPanel(logs, on_clear, on_open_log_file, self)
        self.export_panel = ExportPanel(on_status, self)
        self.export_panel.set_busy(busy)
        self.tabs.addTab(self.log_panel, _LOG_TAB_TITLE)
        self.tabs.addTab(self.export_panel, _EXPORT_TAB_TITLE)
        layout.addWidget(self.tabs, stretch=1)

        self.apply_theme(ThemeManager().current_palette)
        self.init_theme_listener()

    @property
    def text_edit(self) -> QPlainTextEdit:
        """로그 탭의 텍스트 뷰 반환"""
        return self.log_panel.text_edit

    def set_logs(self, logs: list[str]) -> None:
        """로그 탭 내용을 갱신"""
        self.log_panel.set_logs(logs)

    def select_tab(self, tab: LogTab) -> None:
        """지정한 탭을 활성화"""
        self.tabs.setCurrentIndex(self._TAB_INDEX[tab])

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트 기반 대화상자 스타일시트와 패널 아이콘 재적용"""
        self.setStyleSheet(build_stylesheet(palette))
        self.log_panel.apply_theme(palette)
        self.export_panel.apply_theme(palette)
