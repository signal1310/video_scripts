"""분할 분석 버튼 화살표에서 여는 분석 모드 선택 팝업 모듈"""

from collections.abc import Mapping

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from src.core.config import FRAME_ANALYSIS_MIN_TIER_ALL
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY, RESOLUTION_TIERS
from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.widgets.floating_popup import FloatingPopupFrame

_POPUP_PADDING_PX: int = 4
_POPUP_ICON_SIZE: int = 16
_POPUP_ROW_SPACING_PX: int = 12
_TIER_PANEL_MARGINS: tuple[int, int, int, int] = (8, 6, 8, 6)
_TIER_PANEL_SPACING: int = 4
# - 측정 대상 슬라이더의 8단계. 360p 미만은 대상 단계에 없으므로 480p부터 둠
FRAME_MIN_TIER_STEPS: tuple[str, ...] = (
    FRAME_ANALYSIS_MIN_TIER_ALL,
    *(tier.key for tier in RESOLUTION_TIERS if tier.key != "360p"),
)
FRAME_MODE: str = "frame"
_DEFAULT_MIN_TIER_INDEX: int = FRAME_MIN_TIER_STEPS.index("1080p")


def frame_min_tier_label(tier_key: str) -> str:
    """측정 대상 단계의 표시 문구. 전체 또는 `{규격} 이상`"""
    if tier_key == FRAME_ANALYSIS_MIN_TIER_ALL:
        return "전체"
    tier = RESOLUTION_TIER_BY_KEY.get(tier_key)
    return "전체" if tier is None else f"{tier.display} 이상"


class AnalyzeModePopup(FloatingPopupFrame):
    """빠른 분석, 정밀 분석, 화질 분석 중 하나를 고르는 팝업

    항목을 누르면 닫히고 해당 시그널을 방출하며, 열린 동안에도 배경 호버와 클릭은 막히지 않음
    각 항목 오른쪽에는 분석 시작 전 예상값(대상 수, 예상 소요)을 표시
    화질 분석이 선택된 상태로 열면 같은 테두리 안에 측정 대상 슬라이더를 펼침
    """

    fast_selected = pyqtSignal()
    full_selected = pyqtSignal()
    frame_selected = pyqtSignal()
    min_tier_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        card, layout = self._build_card("AnalyzeModePopupCard", _POPUP_PADDING_PX)
        layout.setSpacing(0)
        self.btn_fast = self._create_item("빠른 분석")
        self.btn_full = self._create_item("정밀 분석 (키프레임 + 검은 여백)")
        self.btn_frame = self._create_item("화질 분석 (축소 후보 포함)")
        self.btn_fast.clicked.connect(self._on_fast_clicked)
        self.btn_full.clicked.connect(self._on_full_clicked)
        self.btn_frame.clicked.connect(self._on_frame_clicked)
        self.lbl_fast_estimate = self._create_estimate_label(card)
        self.lbl_full_estimate = self._create_estimate_label(card)
        self.lbl_frame_estimate = self._create_estimate_label(card)
        layout.addLayout(self._create_row(self.btn_fast, self.lbl_fast_estimate))
        layout.addLayout(self._create_row(self.btn_full, self.lbl_full_estimate))
        layout.addLayout(self._create_row(self.btn_frame, self.lbl_frame_estimate))
        self._tier_controls = FrameMinTierControls(card)
        self._tier_controls.min_tier_changed.connect(self.min_tier_changed.emit)
        layout.addWidget(self._tier_controls)
        self._tier_controls.setVisible(False)

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

    def set_estimates(self, estimates: Mapping[str, str]) -> None:
        """모드별 예상 문구를 항목 옆에 표시"""
        self.lbl_fast_estimate.setText(estimates.get("fast", ""))
        self.lbl_full_estimate.setText(estimates.get("full", ""))
        self.lbl_frame_estimate.setText(estimates.get("frame", ""))

    def set_current_mode(self, mode: str) -> None:
        """지금 선택된 모드에 따라 측정 대상 슬라이더 표시를 정함"""
        show_tiers = mode == FRAME_MODE
        self._tier_controls.setVisible(show_tiers)
        self.btn_frame.setProperty("selected", show_tiers)

    def set_min_tier(self, tier_key: str) -> None:
        """측정 대상 슬라이더 값을 규격 키로 맞춤"""
        self._tier_controls.set_tier(tier_key)

    def refresh_icons(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 항목 아이콘과 예상 문구 색상 갱신"""
        self.btn_fast.setIcon(
            get_icon("zap", palette=palette, size=_POPUP_ICON_SIZE, pad_right=0)
        )
        self.btn_full.setIcon(
            get_icon("analyze", palette=palette, size=_POPUP_ICON_SIZE, pad_right=0)
        )
        self.btn_frame.setIcon(
            get_icon("film", palette=palette, size=_POPUP_ICON_SIZE, pad_right=0)
        )
        muted_style = f"color: {palette.text_muted};"
        for label in (self.lbl_fast_estimate, self.lbl_full_estimate, self.lbl_frame_estimate):
            label.setStyleSheet(muted_style)
        self._tier_controls.apply_theme(palette)

    def _on_fast_clicked(self) -> None:
        self.close()
        self.fast_selected.emit()

    def _on_full_clicked(self) -> None:
        self.close()
        self.full_selected.emit()

    def _on_frame_clicked(self) -> None:
        self.close()
        self.frame_selected.emit()


class FrameMinTierControls(QWidget):
    """화질 분석 측정 대상 8단계 슬라이더와 대상 요약 문구

    - 저장 값은 규격 키이며 `all`은 전체, 나머지는 그 규격 이상을 뜻함
    """

    min_tier_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FrameMinTierPanel")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(*_TIER_PANEL_MARGINS)
        layout.setSpacing(_TIER_PANEL_SPACING)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(_POPUP_ROW_SPACING_PX)
        self.lbl_title = QLabel("측정 대상", self)
        self.lbl_title.setObjectName("SettingTitle")
        self.slider = QSlider(Qt.Orientation.Horizontal, self)
        self.slider.setRange(0, len(FRAME_MIN_TIER_STEPS) - 1)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(1)
        self.slider.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.slider.setTickInterval(1)
        self.slider.valueChanged.connect(self._on_slider_changed)
        self.lbl_value = QLabel("", self)
        self.lbl_value.setObjectName("FrameMinTierValue")
        row.addWidget(self.lbl_title)
        row.addWidget(self.slider, 1)
        row.addWidget(self.lbl_value)
        layout.addLayout(row)
        self.lbl_summary = QLabel("", self)
        self.lbl_summary.setObjectName("AnalyzeModeEstimate")
        self.lbl_summary.setWordWrap(True)
        layout.addWidget(self.lbl_summary)
        self.set_tier(FRAME_MIN_TIER_STEPS[-2])

    def set_tier(self, tier_key: str) -> None:
        """규격 키에 해당하는 단계로 슬라이더를 옮김. 모르는 값은 기본 단계"""
        index = FRAME_MIN_TIER_STEPS.index(tier_key) if tier_key in FRAME_MIN_TIER_STEPS else _DEFAULT_MIN_TIER_INDEX
        blocked = self.slider.blockSignals(True)
        self.slider.setValue(index)
        self.slider.blockSignals(blocked)
        self._refresh_texts()

    def tier_key(self) -> str:
        """현재 슬라이더 단계의 규격 키"""
        return FRAME_MIN_TIER_STEPS[self.slider.value()]

    def apply_theme(self, palette: ThemePalette) -> None:
        """보조 문구 색을 팔레트의 흐린 글자색으로 맞춤"""
        self.lbl_summary.setStyleSheet(f"color: {palette.text_muted};")

    def _on_slider_changed(self, _value: int) -> None:
        self._refresh_texts()
        self.min_tier_changed.emit(self.tier_key())

    def _refresh_texts(self) -> None:
        label = frame_min_tier_label(self.tier_key())
        self.lbl_value.setText(label)
        self.lbl_summary.setText(
            f"{label} 영상만 축소 후보를 측정합니다. 그보다 낮은 해상도는 검은 여백만 감지합니다."
            if self.tier_key() != FRAME_ANALYSIS_MIN_TIER_ALL
            else "모든 해상도 영상의 축소 후보를 측정합니다."
        )
