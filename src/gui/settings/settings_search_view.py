"""통합 검색의 전체 결과 페이지 위젯 모듈

- 검색어와 맞는 설정, 단축키 항목을 범주 이름과 함께 목록으로 보여 주고, 누르면 해당 위치로 이동 요청을 보냄
"""

from collections.abc import Callable, Mapping

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from src.gui.settings.settings_search import SearchEntry

NO_RESULT_TEXT: str = "일치하는 설정이 없습니다."
RESULT_BUTTON_OBJECT_NAME: str = "SettingsResultButton"


class SearchResultsView(QWidget):
    """전체 결과 목록을 스크롤로 보여 주는 페이지"""

    row_activated = pyqtSignal(int, str)
    shortcut_activated = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        content = QWidget(self.scroll_area)
        self._layout = QVBoxLayout(content)
        self._layout.setContentsMargins(0, 2, 4, 4)
        self._layout.setSpacing(4)
        self.scroll_area.setWidget(content)
        layout.addWidget(self.scroll_area, stretch=1)

    def show_results(
        self,
        matches: list[SearchEntry],
        shortcuts: list[tuple[str, str, str]],
        total: int,
        category_labels: Mapping[int, str],
    ) -> None:
        """설정과 단축키 일치 항목을 제목, 범주 이름과 함께 표시"""
        self.clear()
        if total == 0:
            self._add_label(NO_RESULT_TEXT)
            return
        self._add_label(f"전체 결과 {total}개", title=True)
        for entry in matches:
            label = category_labels.get(entry.category, "")
            tooltip = entry.text
            self._add_button(
                f"{entry.title}  ({label})",
                tooltip,
                lambda _checked=False, page=entry.category, key=entry.key: self.row_activated.emit(page, key),
            )
        for key, name, group in shortcuts:
            self._add_button(
                f"{name}  (단축키 / {group})",
                f"{name} 단축키를 설정합니다.",
                lambda _checked=False, label=name: self.shortcut_activated.emit(label),
                key=key,
            )

    def clear(self) -> None:
        """결과 목록의 위젯을 즉시 분리해 없애고 지연 삭제 예약"""
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

    def _add_label(self, text: str, *, title: bool = False) -> None:
        label = QLabel(text, self)
        if title:
            label.setObjectName("GroupHeaderTitle")
        else:
            label.setObjectName("SettingDesc")
        label.setWordWrap(True)
        self._layout.addWidget(label)

    def _add_button(
        self,
        text: str,
        tooltip: str,
        on_clicked: Callable[[bool], None],
        *,
        key: str = "",
    ) -> None:
        button = QPushButton(text, self)
        button.setObjectName(RESULT_BUTTON_OBJECT_NAME)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(tooltip)
        if key:
            button.setProperty("resultKey", key)
        button.clicked.connect(on_clicked)
        self._layout.addWidget(button)
