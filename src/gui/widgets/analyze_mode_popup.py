"""분할 분석 버튼 화살표에서 여는 분석 모드 선택 팝업 모듈"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.widgets.floating_popup import FloatingPopupFrame

_POPUP_PADDING_PX: int = 4
_POPUP_ICON_SIZE: int = 16


class AnalyzeModePopup(FloatingPopupFrame):
    """빠른 분석, 정밀 분석 중 하나를 고르는 팝업

    항목을 누르면 닫히고 해당 시그널을 방출하며, 열린 동안에도 배경 호버와 클릭은 막히지 않음
    """

    fast_selected = pyqtSignal()
    full_selected = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("AnalyzeModePopup")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(
            _POPUP_PADDING_PX, _POPUP_PADDING_PX, _POPUP_PADDING_PX, _POPUP_PADDING_PX
        )
        layout.setSpacing(0)
        self.btn_fast = self._create_item("빠른 분석")
        self.btn_full = self._create_item("정밀 분석")
        self.btn_fast.clicked.connect(self._on_fast_clicked)
        self.btn_full.clicked.connect(self._on_full_clicked)
        layout.addWidget(self.btn_fast)
        layout.addWidget(self.btn_full)

    def _create_item(self, text: str) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setObjectName("AnalyzeModeItem")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    def refresh_icons(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 항목 아이콘 색상 갱신"""
        self.btn_fast.setIcon(
            get_icon("zap", palette=palette, size=_POPUP_ICON_SIZE, pad_right=0)
        )
        self.btn_full.setIcon(
            get_icon("analyze", palette=palette, size=_POPUP_ICON_SIZE, pad_right=0)
        )

    def _on_fast_clicked(self) -> None:
        self.close()
        self.fast_selected.emit()

    def _on_full_clicked(self) -> None:
        self.close()
        self.full_selected.emit()
