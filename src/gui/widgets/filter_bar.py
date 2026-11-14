"""검색 필터, 열 필터 해제 및 예외 규칙 목록 진입 컨트롤 바 위젯 모듈"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QWidget,
)

from src.gui.styles.icons import get_active_palette, get_icon, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

if TYPE_CHECKING:
    from src.classifier import VideoClassifier


__all__ = ["FilterTuningBar"]


class FilterTuningBar(QFrame, ThemeAwareMixin):
    """검색어 입력, 예외 목록 진입 및 열 필터 일괄 해제를 1줄로 제공하는 컨트롤 바 위젯"""

    filter_changed = pyqtSignal()
    exceptions_changed = pyqtSignal()
    exception_editor_requested = pyqtSignal()
    clear_column_filters_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FilterBar")
        self._updating: bool = False
        self._active_tokens: list[str] = []
        self._disabled_count: int = 0

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(8)

        self.search_edit = QLineEdit()
        self._action_search = self.search_edit.addAction(
            get_icon("search", palette=get_active_palette(), size=14, pad_right=7),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_edit.setPlaceholderText("파일명, 코덱, 권장 작업·사유 실시간 검색...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(220)
        self.search_edit.textChanged.connect(self.filter_changed.emit)

        self.btn_edit_exceptions = QPushButton("예외 목록", self)
        self.btn_edit_exceptions.setCursor(Qt.CursorShape.PointingHandCursor)
        set_button_icon(
            self.btn_edit_exceptions,
            "ban",
            palette=get_active_palette(),
            size=14,
            pad_right=6,
        )
        self.btn_edit_exceptions.clicked.connect(self.exception_editor_requested.emit)

        self.btn_clear_col_filters = QPushButton("", self)
        self.btn_clear_col_filters.setObjectName("ClearColumnFiltersButton")
        self.btn_clear_col_filters.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear_col_filters.setToolTip("적용된 모든 열 헤더 필터를 해제합니다.")
        self.btn_clear_col_filters.setVisible(False)
        self.btn_clear_col_filters.clicked.connect(
            self.clear_column_filters_requested.emit
        )

        self.lbl_filter_count = QLabel("표시: 0 / 0개")
        self.lbl_filter_count.setObjectName("SectionLabel")
        self.lbl_filter_count.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.lbl_filter_count.setMinimumWidth(110)

        row.addWidget(self.search_edit, stretch=1)
        row.addWidget(self.btn_edit_exceptions)
        row.addWidget(self.btn_clear_col_filters)
        row.addSpacing(2)
        row.addWidget(self.lbl_filter_count)

        self._refresh_exception_button()
        self.init_theme_listener()

    def _refresh_exception_button(self) -> None:
        active = len(self._active_tokens)
        disabled = self._disabled_count
        if active > 0 and disabled > 0:
            label = f"예외 목록 ({active} · 비활성 {disabled})"
        elif active > 0:
            label = f"예외 목록 ({active})"
        elif disabled > 0:
            label = f"예외 목록 (비활성 {disabled})"
        else:
            label = "예외 목록"
        self.btn_edit_exceptions.setText(label)

        tip_lines = ["제외할 파일명 키워드·조건식 목록을 편집합니다. (Ctrl+Shift+E)"]
        if self._active_tokens:
            preview = ", ".join(self._active_tokens[:5])
            if len(self._active_tokens) > 5:
                preview += f" 외 {len(self._active_tokens) - 5}개"
            tip_lines.append(f"• 활성 규칙 ({active}개): {preview}")
        if disabled > 0:
            tip_lines.append(f"• 비활성 규칙: {disabled}개")
        self.btn_edit_exceptions.setToolTip("\n".join(tip_lines))

    def set_active_column_filter_count(self, count: int) -> None:
        """활성화된 열 헤더 동적 필터 개수에 따라 일괄 해제 배지 버튼을 갱신"""
        if count > 0:
            self.btn_clear_col_filters.setText(f"열 필터 {count}개 ×")
            self.btn_clear_col_filters.setVisible(True)
        else:
            self.btn_clear_col_filters.setText("")
            self.btn_clear_col_filters.setVisible(False)

    def set_disabled_exception_count(self, count: int) -> None:
        """비활성화된 예외 규칙 건수를 예외 목록 버튼 라벨·툴팁에 반영"""
        self._disabled_count = max(0, count)
        self._refresh_exception_button()

    def set_busy(self, busy: bool) -> None:
        """백그라운드 작업 중 예외 규칙 편집 버튼을 잠가 스레드 레이스 컨디션을 방지"""
        self.btn_edit_exceptions.setEnabled(not busy)

    def get_exception_tokens(self) -> list[str]:
        """현재 등록된 활성 예외 규칙 토큰 리스트 복사본을 순서대로 반환"""
        return list(self._active_tokens)

    def set_exception_tokens(self, tokens: list[str]) -> None:
        """활성 예외 규칙 토큰 목록을 갱신하고 변경 시그널을 발행"""
        self._active_tokens = [tok.strip() for tok in tokens if tok.strip()]
        self._refresh_exception_button()
        if not self._updating:
            self.exceptions_changed.emit()

    def sync_filter_state(
        self,
        *,
        search: str,
        exception_tokens: list[str],
    ) -> None:
        """저장된 검색·예외 규칙 상태를 툴바 컨트롤에 시그널 없이 반영"""
        self._updating = True
        try:
            self.search_edit.blockSignals(True)
            try:
                self.search_edit.setText(search)
            finally:
                self.search_edit.blockSignals(False)
            self._active_tokens = [
                tok.strip() for tok in exception_tokens if tok.strip()
            ]
            self._refresh_exception_button()
        finally:
            self._updating = False

    def apply_to_classifier(self, classifier: VideoClassifier) -> None:
        """현재 UI의 예외 규칙 목록을 VideoClassifier 인스턴스에 반영"""
        classifier.set_exception_tokens(self.get_exception_tokens())

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 검색창 및 예외 목록 버튼 아이콘 색상을 갱신"""
        if self._action_search is not None:
            self._action_search.setIcon(
                get_icon("search", palette=palette, size=14, pad_right=7)
            )
        set_button_icon(
            self.btn_edit_exceptions,
            "ban",
            palette=palette,
            size=14,
            pad_right=6,
        )
