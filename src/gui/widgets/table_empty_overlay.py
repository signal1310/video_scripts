"""테이블 뷰포트 위에 겹쳐 표시하는 빈 상태 안내 오버레이 위젯 모듈"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.gui.widgets.header_bar import SplitAnalyzeButton

EMPTY_MODE_NO_DATA: str = "no_data"
EMPTY_MODE_FILTERED: str = "filtered_empty"

_ACTION_BUTTON_WIDTH_PX: int = 150
_GUIDE_MAX_WIDTH_PX: int = 520
_STEP_BADGE_WIDTH_PX: int = 84
_CONTENT_MARGIN_X_PX: int = 20
_CONTENT_MARGIN_Y_PX: int = 12
_CONTENT_SPACING_PX: int = 6
_BLOCK_GAP_PX: int = 4
_GUIDE_MARGIN_X_PX: int = 14
_GUIDE_MARGIN_Y_PX: int = 10
_GUIDE_SPACING_PX: int = 5
_STEP_SPACING_PX: int = 8

_GUIDE_STEPS: tuple[tuple[str, str], ...] = (
    ("1. 폴더 분석", "작업 폴더를 선택하고 [빠른 분석] 또는 [정밀 분석]을 실행합니다."),
    (
        "2. 기준 비교",
        "과화질(여유 용량 큰 파일)과 키프레임 간격 초과만 선별해 예상 절약을 미리 계산합니다.",
    ),
    (
        "3. 판정 확인",
        "재인코딩 대상과 판정 사유를 표에서 확인합니다. 파일은 바꾸지 않습니다.",
    ),
)


class TableEmptyOverlay(QWidget):
    """표시할 행이 없을 때 데이터 없음 안내 또는 필터 결과 없음 안내를 보여 주는 오버레이

    - 데이터가 없으면 사용 순서 카드와 분석 분할 버튼을, 필터 결과가 없으면 필터 초기화 버튼을 표시
    - 뷰포트 자식으로 두어 휠과 트랙패드 가로 스크롤 이벤트가 뷰포트로 자연스럽게 전파됨
    """

    analyze_fast_requested = pyqtSignal()
    analyze_full_requested = pyqtSignal()
    analyze_mode_changed = pyqtSignal(str)
    reset_filters_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("TableEmptyOverlay")
        self.mode: str = EMPTY_MODE_NO_DATA

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_vp = self.scroll_area.viewport()
        if scroll_vp is not None:
            scroll_vp.setObjectName("TableEmptyOverlay")

        content = QWidget(self.scroll_area)
        content.setObjectName("TableEmptyOverlay")
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(
            _CONTENT_MARGIN_X_PX, _CONTENT_MARGIN_Y_PX, _CONTENT_MARGIN_X_PX, _CONTENT_MARGIN_Y_PX
        )
        content_layout.setSpacing(_CONTENT_SPACING_PX)
        content_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._lbl_title = QLabel("", content)
        self._lbl_title.setObjectName("EmptyStateTitle")
        self._lbl_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        self._lbl_desc = QLabel("", content)
        self._lbl_desc.setObjectName("EmptyStateDesc")
        self._lbl_desc.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._lbl_desc.setWordWrap(True)
        self._lbl_desc.setMaximumWidth(_GUIDE_MAX_WIDTH_PX)
        self._lbl_desc.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)

        self.guide_card = self._build_guide_card(content)

        self.btn_analyze = SplitAnalyzeButton(content)
        self.btn_analyze.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_analyze.analyze_fast_requested.connect(self.analyze_fast_requested.emit)
        self.btn_analyze.analyze_full_requested.connect(self.analyze_full_requested.emit)
        self.btn_analyze.mode_changed.connect(self.analyze_mode_changed.emit)

        self.btn_action = QPushButton("필터 초기화", content)
        self.btn_action.setObjectName("PrimaryButton")
        self.btn_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_action.setFixedWidth(_ACTION_BUTTON_WIDTH_PX)
        self.btn_action.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.btn_action.clicked.connect(self.reset_filters_requested.emit)

        center = Qt.AlignmentFlag.AlignCenter
        content_layout.addWidget(self._lbl_title)
        content_layout.addWidget(self._lbl_desc)
        content_layout.addSpacing(_BLOCK_GAP_PX)
        content_layout.addWidget(self.guide_card, alignment=center)
        content_layout.addSpacing(_BLOCK_GAP_PX)
        content_layout.addWidget(self.btn_analyze, alignment=center)
        content_layout.addWidget(self.btn_action, alignment=center)

        self.scroll_area.setWidget(content)
        outer_layout.addWidget(self.scroll_area)

    @staticmethod
    def _build_guide_card(parent: QWidget) -> QFrame:
        card = QFrame(parent)
        card.setObjectName("EmptyGuideCard")
        card.setMaximumWidth(_GUIDE_MAX_WIDTH_PX)
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(
            _GUIDE_MARGIN_X_PX, _GUIDE_MARGIN_Y_PX, _GUIDE_MARGIN_X_PX, _GUIDE_MARGIN_Y_PX
        )
        layout.setSpacing(_GUIDE_SPACING_PX)
        for badge_text, step_desc in _GUIDE_STEPS:
            row = QHBoxLayout()
            row.setContentsMargins(0, 0, 0, 0)
            row.setSpacing(_STEP_SPACING_PX)
            badge = QLabel(badge_text, card)
            badge.setObjectName("EmptyStepBadge")
            badge.setFixedWidth(_STEP_BADGE_WIDTH_PX)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            desc = QLabel(step_desc, card)
            desc.setObjectName("EmptyStepDesc")
            desc.setWordWrap(True)
            row.addWidget(badge, alignment=Qt.AlignmentFlag.AlignTop)
            row.addWidget(desc, stretch=1)
            layout.addLayout(row)
        return card

    def apply_state(self, total_count: int) -> None:
        """전체 행 수에 따라 데이터 없음 또는 필터 결과 없음 안내로 문구와 컨트롤 표시를 전환"""
        if total_count == 0:
            self.mode = EMPTY_MODE_NO_DATA
            self._lbl_title.setText("화질 유지, 용량 절약 대상 자동 선별")
            self._lbl_desc.setText(
                "눈으로 구분하기 힘든 과도한 비트레이트 영상과 구간 이동이 느린 영상만 골라 "
                "재인코딩 대상으로 판정해 표에 보여 줍니다."
            )
            self.guide_card.show()
            self.btn_analyze.show()
            self.btn_action.hide()
            return
        self.mode = EMPTY_MODE_FILTERED
        self._lbl_title.setText("필터 조건에 맞는 영상이 없습니다")
        self._lbl_desc.setText(
            f"전체 {total_count:,}개 영상 중 현재 검색어 및 열 필터 조건과 일치하는 항목이 없습니다."
        )
        self.guide_card.hide()
        self.btn_analyze.hide()
        self.btn_action.show()

    def set_analyze_mode(self, mode: str) -> None:
        """안내 화면 분할 버튼의 분석 모드를 시그널 없이 동기화"""
        self.btn_analyze.set_mode(mode, emit_signal=False)

    def set_actions_enabled(self, enabled: bool) -> None:
        """안내 화면의 분석 버튼과 필터 초기화 버튼 활성화 여부 설정"""
        self.btn_analyze.setEnabled(enabled)
        self.btn_action.setEnabled(enabled)
