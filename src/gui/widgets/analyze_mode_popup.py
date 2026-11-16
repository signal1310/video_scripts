"""분할 분석 버튼 화살표에서 여는 분석 모드 선택 팝업 모듈"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.widgets.floating_popup import FloatingPopupFrame

_POPUP_PADDING_PX: int = 4
_POPUP_ICON_SIZE: int = 16
_POPUP_ROW_SPACING_PX: int = 12


class AnalyzeModePopup(FloatingPopupFrame):
    """빠른 분석, 정밀 분석 중 하나를 고르는 팝업

    항목을 누르면 닫히고 해당 시그널을 방출하며, 열린 동안에도 배경 호버와 클릭은 막히지 않음
    각 항목 오른쪽에는 분석 시작 전 예상값(대상 수, 예상 소요)을 표시
    """

    fast_selected = pyqtSignal()
    full_selected = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        card, layout = self._build_card("AnalyzeModePopupCard", _POPUP_PADDING_PX)
        layout.setSpacing(0)
        self.btn_fast = self._create_item("빠른 분석")
        self.btn_full = self._create_item("정밀 분석")
        self.btn_fast.clicked.connect(self._on_fast_clicked)
        self.btn_full.clicked.connect(self._on_full_clicked)
        self.lbl_fast_estimate = self._create_estimate_label(card)
        self.lbl_full_estimate = self._create_estimate_label(card)
        layout.addLayout(self._create_row(self.btn_fast, self.lbl_fast_estimate))
        layout.addLayout(self._create_row(self.btn_full, self.lbl_full_estimate))

    def _create_item(self, text: str) -> QPushButton:
        btn = QPushButton(text, self)
        btn.setObjectName("AnalyzeModeItem")
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        return btn

    @staticmethod
    def _create_estimate_label(parent: QWidget) -> QLabel:
        label = QLabel("", parent)
        label.setObjectName("AnalyzeModeEstimate")
        label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # 라벨 위 클릭도 항목 버튼 클릭으로 처리되도록 마우스 이벤트를 통과시킴
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        return label

    @staticmethod
    def _create_row(btn: QPushButton, estimate: QLabel) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(_POPUP_ROW_SPACING_PX)
        row.addWidget(btn, 1)
        row.addWidget(estimate)
        return row

    def set_estimates(self, fast_text: str, full_text: str) -> None:
        """두 모드의 예상 문구를 항목 옆에 표시"""
        self.lbl_fast_estimate.setText(fast_text)
        self.lbl_full_estimate.setText(full_text)

    def refresh_icons(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 항목 아이콘과 예상 문구 색상 갱신"""
        self.btn_fast.setIcon(
            get_icon("zap", palette=palette, size=_POPUP_ICON_SIZE, pad_right=0)
        )
        self.btn_full.setIcon(
            get_icon("analyze", palette=palette, size=_POPUP_ICON_SIZE, pad_right=0)
        )
        muted_style = f"color: {palette.text_muted};"
        self.lbl_fast_estimate.setStyleSheet(muted_style)
        self.lbl_full_estimate.setStyleSheet(muted_style)

    def _on_fast_clicked(self) -> None:
        self.close()
        self.fast_selected.emit()

    def _on_full_clicked(self) -> None:
        self.close()
        self.full_selected.emit()
