"""상단 작업 경로 표시와 입력, 분석 모드 분할 버튼, 상단 영역 접기 버튼 툴바 모듈"""

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import ClassVar

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import (
    QFocusEvent,
    QKeyEvent,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QToolTip,
    QWidget,
)

from src.core.encode.encode_batch_book import ACTIVE_CHIP_STATES, EncodeChipStatus
from src.core.encode.encode_start_state import StartState
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS, format_tooltip_with_shortcut
from src.core.settings.ui_state import DEFAULT_ANALYZE_MODE
from src.core.workspace_paths import (
    are_all_valid_dirs,
    format_root_dirs,
    normalize_path_separators,
    parse_root_dirs,
)
from src.gui.constants import (
    HEADER_PADDING_X_PX,
    HEADER_PADDING_Y_PX,
    HEADER_SPACING_PX,
    ON_ACCENT_COLOR,
    PATH_EMPTY_TEXT,
    TOOLBAR_ICON_BTN_SIZE,
    TOOLBAR_ICON_SIZE_PX,
    make_path_font,
)
from src.gui.styles.icons import (
    create_icon_button,
    get_active_palette,
    get_icon,
    set_button_icon,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import set_state_property
from src.gui.theme import ThemeAwareMixin
from src.gui.widgets.analyze_mode_popup import FRAME_MODE, AnalyzeModePopup
from src.gui.widgets.folder_path_button import FolderPathButton
from src.gui.widgets.header_encode_button import (
    HeaderEncodeButton,
    create_encode_arrow_label,
    set_encode_arrow_color,
)

_MODE_POPUP_GAP_Y: int = 3
_ANALYZE_GAP_BEFORE_PX: int = 8
_ANALYZE_GAP_AFTER_PX: int = 6
_SCAN_OPTION_GAP_PX: int = 12
_SEGMENT_DIVIDER_WIDTH_PX: int = 1
_MODE_ARROW_WIDTH_PX: int = 26
_MODE_ICON_SIZE_PX: int = 14
_MODE_ICON_GAP_PX: int = 7
_MODE_ARROW_ICON_SIZE_PX: int = 12
_CANCEL_TOOLTIP_BASE: str = "분석 중단\n현재 진행 중인 분석 작업을 즉시 중단합니다."
_COLLAPSE_TOOLTIP_BASE: str = "상단 영역 접기\n상단 영역을 접습니다."
TOP_AREA_COLLAPSE_ICON: str = "panel-top-close"
_PATH_HINT_BASE: str = "경로 직접 입력: 클릭하면 작업 폴더 경로를 입력할 수 있습니다."
_PATH_EDIT_HINT: str = "Enter로 확정하고 Esc로 취소합니다."
_BROWSE_TOOLTIP: str = "작업 폴더 선택\n고른 폴더로 현재 작업 폴더 목록을 바꿉니다."
_ADD_DIR_TOOLTIP_BASE: str = "작업 폴더 추가\n현재 작업 폴더를 유지하고 다른 작업 폴더를 함께 추가합니다."
_OPEN_EXPLORER_TOOLTIP: str = "탐색기에서 작업 폴더 열기\n첫 번째 작업 폴더를 탐색기로 엽니다."
_REFRESH_TOOLTIP_BASE: str = "새로고침\n현재 작업 폴더를 다시 읽어 표를 새로고침합니다."
_RECURSIVE_TOOLTIP_BASE: str = "하위 폴더 포함\n작업 폴더 아래의 모든 하위 폴더 영상을 함께 분석합니다."
_ENCODE_START_TOOLTIP: str = "인코딩 시작\n인코딩 창을 엽니다. 시작 전 브리핑은 요약 탭에서 확인하고 시작할 수 있습니다."
_ENCODE_ACTIVE_TOOLTIP: str = "인코딩 진행 중\n진행 중인 인코딩 창을 엽니다."
_PATH_NOT_FOUND_MESSAGE: str = "입력한 경로를 폴더로 찾을 수 없습니다. 올바른 폴더 경로를 입력해 주세요."


@dataclass(frozen=True)
class _ModeSpec:
    """분석 모드별 버튼 문구, 아이콘 이름, 툴팁"""

    label: str
    icon: str
    tooltip: str


class SplitAnalyzeButton(QFrame, ThemeAwareMixin):
    """빠른 분석과 정밀 분석을 선택 실행하는 분할(Split) 버튼 위젯

    본체 클릭 시 현재 선택된 분석 모드 즉시 실행, 드롭다운 화살표로 모드 전환
    """

    MODE_FAST = DEFAULT_ANALYZE_MODE
    MODE_FULL = "full"
    MODE_FRAME = FRAME_MODE
    _MODE_SPECS: ClassVar[dict[str, _ModeSpec]] = {
        MODE_FAST: _ModeSpec(
            "빠른 분석",
            "zap",
            "키프레임 측정을 생략하고 해상도와 비트레이트를 빠르게 분석합니다.",
        ),
        MODE_FULL: _ModeSpec(
            "정밀 분석",
            "analyze",
            "모든 영상의 키프레임 간격과 검은 여백을 측정해 권장 처리를 판정합니다.",
        ),
        MODE_FRAME: _ModeSpec(
            "화질 분석",
            "film",
            "검은 여백과 축소 후보까지 측정합니다. 축소 후보는 출력 해상도에 자동 반영됩니다.",
        ),
    }

    analyze_fast_requested = pyqtSignal()
    analyze_full_requested = pyqtSignal()
    analyze_frame_requested = pyqtSignal()
    mode_changed = pyqtSignal(str)
    frame_min_tier_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SplitAnalyzeFrame")
        self._mode = self.MODE_FAST

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.btn_main = QPushButton(self)
        self.btn_main.setObjectName("SplitMainButton")
        self.btn_main.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_main.clicked.connect(self._on_main_clicked)

        self.btn_arrow = QPushButton(self)
        self.btn_arrow.setObjectName("SplitArrowButton")
        self.btn_arrow.setFixedWidth(_MODE_ARROW_WIDTH_PX)
        self.btn_arrow.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_arrow.setToolTip(
            "분석 모드를 선택합니다: 빠른 분석 / 정밀 분석 / 화질 분석"
        )
        self.btn_arrow.setAccessibleName("분석 모드 선택")
        self.btn_arrow.clicked.connect(self._on_arrow_clicked)

        layout.addWidget(self.btn_main)
        layout.addWidget(self.btn_arrow)

        self._mode_popup = AnalyzeModePopup(self)
        self._mode_popup.fast_selected.connect(self._select_fast_mode)
        self._mode_popup.full_selected.connect(self._select_full_mode)
        self._mode_popup.frame_selected.connect(self._select_frame_mode)
        self._mode_popup.min_tier_changed.connect(self._on_min_tier_changed)

        self._palette: ThemePalette | None = None
        # 스캔 모드 코드를 받아 예상 문구를 반환
        self._estimate_provider: Callable[[str], str] | None = None
        # 화질 분석 측정 대상의 저장 값을 반환
        self._min_tier_provider: Callable[[], str] | None = None
        self.set_mode(self.MODE_FAST)
        self.init_theme_listener()

    def set_estimate_provider(self, provider: Callable[[str], str]) -> None:
        """팝업을 열 때 모드별 예상 문구를 얻는 콜백 등록"""
        self._estimate_provider = provider

    def set_min_tier_provider(self, provider: Callable[[], str]) -> None:
        """팝업을 열 때 화질 분석 측정 대상 값을 얻는 콜백 등록"""
        self._min_tier_provider = provider

    def set_frame_min_tier(self, tier_key: str) -> None:
        """화질 분석 측정 대상 슬라이더 값을 저장 값으로 맞춤"""
        self._mode_popup.set_min_tier(tier_key)

    @property
    def current_mode(self) -> str:
        """현재 선택된 스캔 모드 반환"""
        return self._mode

    def set_mode(self, mode: str, *, emit_signal: bool = False) -> None:
        """스캔 버튼 모드 전환 및 아이콘, 툴팁 갱신"""
        target_mode = mode if mode in self._MODE_SPECS else self.MODE_FAST
        changed = self._mode != target_mode
        # 비활성 시 QSS가 텍스트 색을 text_muted로 바꾸므로 아이콘도 같은 토큰으로 맞춤
        palette = self._palette or get_active_palette()
        spec = self._MODE_SPECS[target_mode]
        self._mode = target_mode
        self.btn_main.setText(spec.label)
        set_button_icon(
            self.btn_main,
            spec.icon,
            color=palette.text_on_accent,
            size=_MODE_ICON_SIZE_PX,
            pad_right=_MODE_ICON_GAP_PX,
            disabled_color=palette.text_muted,
        )
        self.btn_main.setToolTip(spec.tooltip)
        set_button_icon(
            self.btn_arrow,
            "chevron-down",
            color=palette.text_on_accent,
            size=_MODE_ARROW_ICON_SIZE_PX,
            pad_right=0,
            disabled_color=palette.text_muted,
        )
        if emit_signal and changed:
            self.mode_changed.emit(self._mode)

    def setEnabled(self, enabled: bool) -> None:
        """분할 버튼 전체 및 내부 메인, 화살표 버튼 활성화 상태 동기화"""
        super().setEnabled(enabled)
        self.btn_main.setEnabled(enabled)
        self.btn_arrow.setEnabled(enabled)

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트 기반 버튼 아이콘 색상 갱신"""
        self._palette = palette
        self.set_mode(self._mode)

    def _on_main_clicked(self) -> None:
        if self._mode == self.MODE_FRAME:
            self.analyze_frame_requested.emit()
        elif self._mode == self.MODE_FULL:
            self.analyze_full_requested.emit()
        else:
            self.analyze_fast_requested.emit()

    def _on_arrow_clicked(self) -> None:
        # 마우스 클릭은 팝업 기반의 토글 영역이 먼저 처리하므로, 여기서는 키보드로 연 경우의 닫기만 담당
        if self._mode_popup.isVisible():
            self._mode_popup.close()
            return
        popup = self._mode_popup
        popup.refresh_icons(self._palette or get_active_palette())
        popup.set_current_mode(self._mode)
        if self._min_tier_provider is not None:
            popup.set_min_tier(self._min_tier_provider())
        if self._estimate_provider is not None:
            popup.set_estimates(
                {mode: self._estimate_provider(mode) for mode in self._MODE_SPECS}
            )
        popup.setMinimumWidth(self.width())
        popup.set_toggle_rect(
            QRect(self.btn_arrow.mapToGlobal(QPoint(0, 0)), self.btn_arrow.size())
        )
        # 팝업이 분할 버튼보다 넓으면 오른쪽 끝을 맞춰 펼침. 첫 열림은 표시 뒤에야 폭이 확정되므로 표시한 폭으로 위치를 다시 맞춤
        gap_y = self.height() + _MODE_POPUP_GAP_Y
        popup.show_clamped_at(self.mapToGlobal(QPoint(0, gap_y)))
        pos_x = min(0, self.width() - popup.width())
        if pos_x != 0:
            popup.show_clamped_at(self.mapToGlobal(QPoint(pos_x, gap_y)))

    def _select_fast_mode(self) -> None:
        self.set_mode(self.MODE_FAST, emit_signal=True)

    def _select_full_mode(self) -> None:
        self.set_mode(self.MODE_FULL, emit_signal=True)

    def _select_frame_mode(self) -> None:
        self.set_mode(self.MODE_FRAME, emit_signal=True)

    def _on_min_tier_changed(self, tier_key: str) -> None:
        """측정 대상 변경을 알리고, 팝업 우측 대상 요약 문구를 새 기준으로 다시 표시"""
        self.frame_min_tier_changed.emit(tier_key)
        if self._estimate_provider is not None:
            self._mode_popup.set_frame_estimate(self._estimate_provider(FRAME_MODE))


def _make_segment_divider(parent: QWidget) -> QFrame:
    """분할 그룹 안의 버튼 사이에 두는 얇은 구분선 생성"""
    divider = QFrame(parent)
    divider.setObjectName("SegmentDivider")
    divider.setFixedWidth(_SEGMENT_DIVIDER_WIDTH_PX)
    return divider


class PathLineEdit(QLineEdit):
    """작업 폴더 경로 입력창. Esc 입력은 편집 취소, 포커스 이탈은 별도 신호로 알림"""

    edit_cancelled = pyqtSignal()
    focus_lost = pyqtSignal()

    def keyPressEvent(self, a0: QKeyEvent | None) -> None:
        """Esc 입력 시 편집을 취소하고 그 외 키는 기본 처리"""
        if a0 is not None and a0.key() == Qt.Key.Key_Escape:
            self.edit_cancelled.emit()
            return
        super().keyPressEvent(a0)

    def focusOutEvent(self, a0: QFocusEvent | None) -> None:
        """포커스 이탈을 알리고, 입력 내용은 버리지 않고 유지"""
        super().focusOutEvent(a0)
        self.focus_lost.emit()


class HeaderBar(QFrame, ThemeAwareMixin):
    """작업 경로 표시와 선택, 분석 실행, 상단 영역 접기를 제공하는 헤더 바 위젯"""

    browse_clicked = pyqtSignal()
    add_dir_clicked = pyqtSignal()
    recursive_toggled = pyqtSignal(bool)
    open_explorer_clicked = pyqtSignal()
    analyze_fast_clicked = pyqtSignal()
    analyze_full_clicked = pyqtSignal()
    analyze_frame_clicked = pyqtSignal()
    analyze_mode_changed = pyqtSignal(str)
    frame_min_tier_changed = pyqtSignal(str)
    encode_start_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()
    collapse_toggle_clicked = pyqtSignal()
    refresh_clicked = pyqtSignal()
    path_submitted = pyqtSignal(str)
    path_rejected = pyqtSignal(str)
    committed_path_changed = pyqtSignal(str)

    def __init__(
        self, initial_dir: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("HeaderBar")
        self._committed_path: str = initial_dir
        self._edit_path_key: str = DEFAULT_SHORTCUTS["file.edit_path"]
        self._refresh_key: str = DEFAULT_SHORTCUTS["file.refresh"]
        self._encode_start_key: str = DEFAULT_SHORTCUTS["encode.start"]
        self._encode_start_state: StartState | None = None
        self._encoding_active: bool = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(
            HEADER_PADDING_X_PX, HEADER_PADDING_Y_PX, HEADER_PADDING_X_PX, HEADER_PADDING_Y_PX
        )
        layout.setSpacing(HEADER_SPACING_PX)

        # 폴더 아이콘은 경로 입력 중에도 남도록 스택 바깥에 둠
        self.folder_icon = QLabel(self)
        self.folder_icon.setFixedSize(TOOLBAR_ICON_SIZE_PX, TOOLBAR_ICON_SIZE_PX)

        # 평소에는 전체 경로를 보여 주고, 클릭하면 경로 입력창으로 전환
        self.folder_name_btn = FolderPathButton(self)
        self.folder_name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_name_btn.setAccessibleName("작업 폴더 경로 직접 입력")
        # 긴 폴더 이름이 헤더의 최소 폭을 밀어내지 않도록 가로 최소 크기를 텍스트에 맞추지 않음
        self.folder_name_btn.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.folder_name_btn.clicked.connect(self.begin_path_edit)

        self.path_edit = PathLineEdit(initial_dir)
        self.path_edit.setFont(make_path_font())
        self.path_edit.setPlaceholderText(PATH_EMPTY_TEXT)
        self.path_edit.setAccessibleName("작업 폴더 경로")
        self.path_edit.returnPressed.connect(self._on_path_edit_finished)
        self.path_edit.edit_cancelled.connect(self.restore_committed_path)
        self.path_edit.focus_lost.connect(self._on_path_edit_focus_lost)
        self.path_edit.textEdited.connect(self._on_path_text_edited)
        self._set_path_invalid(None)

        self.path_stack = QStackedWidget(self)
        self.path_stack.addWidget(self.folder_name_btn)
        self.path_stack.addWidget(self.path_edit)

        # 작업 폴더 조작 버튼 3개는 하나의 분할 그룹으로 묶어 개별 버튼처럼 보이지 않게 함
        self.action_group = QFrame(self)
        self.action_group.setObjectName("ActionSegment")
        group_layout = QHBoxLayout(self.action_group)
        group_layout.setContentsMargins(0, 0, 0, 0)
        group_layout.setSpacing(0)
        self.btn_browse = create_icon_button(
            self.action_group,
            _BROWSE_TOOLTIP,
            self.browse_clicked.emit,
            accessible_name="작업 폴더 선택",
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
            object_name="SegmentButton",
        )
        self.btn_add_dir = create_icon_button(
            self.action_group,
            format_tooltip_with_shortcut(_ADD_DIR_TOOLTIP_BASE, DEFAULT_SHORTCUTS["file.add_dir"]),
            self.add_dir_clicked.emit,
            accessible_name="작업 폴더 추가",
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
            object_name="SegmentButton",
        )
        self.btn_open_explorer = create_icon_button(
            self.action_group,
            _OPEN_EXPLORER_TOOLTIP,
            self.open_explorer_clicked.emit,
            accessible_name="탐색기에서 작업 폴더 열기",
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
            object_name="SegmentButton",
        )
        group_layout.addWidget(self.btn_browse)
        group_layout.addWidget(_make_segment_divider(self.action_group))
        group_layout.addWidget(self.btn_add_dir)
        group_layout.addWidget(_make_segment_divider(self.action_group))
        group_layout.addWidget(self.btn_open_explorer)

        self.btn_recursive = create_icon_button(
            self,
            _RECURSIVE_TOOLTIP_BASE,
            self._on_recursive_clicked,
            accessible_name="하위 폴더 포함",
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
        )
        self.btn_recursive.setCheckable(True)

        # 폴더 아이콘 왼쪽에 두어 경로 묶음의 맨 앞에서 현재 폴더를 다시 읽는 동작임을 드러냄
        self.btn_refresh = create_icon_button(
            self,
            format_tooltip_with_shortcut(_REFRESH_TOOLTIP_BASE, self._refresh_key),
            self.refresh_clicked.emit,
            accessible_name="새로고침",
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
            object_name="TransparentIconButton",
        )

        layout.addWidget(self.btn_refresh)
        layout.addWidget(self.folder_icon)
        layout.addWidget(self.path_stack, stretch=1)
        layout.addWidget(self.action_group)
        # 하위 폴더 포함은 작업 폴더 조작이 아니라 분석 옵션이므로 분석 버튼 앞에 따로 묶음
        layout.addSpacing(_SCAN_OPTION_GAP_PX)
        layout.addWidget(self.btn_recursive)
        layout.addSpacing(_ANALYZE_GAP_BEFORE_PX)

        # 스캔 실행 스플릿 버튼 및 인플레이스 중단 버튼 (QStackedWidget)
        self.analyze_stack = QStackedWidget(self)
        self.analyze_stack.setObjectName("AnalyzeStack")

        self.split_analyze = SplitAnalyzeButton(self)
        self.split_analyze.analyze_fast_requested.connect(self.analyze_fast_clicked.emit)
        self.split_analyze.analyze_full_requested.connect(self.analyze_full_clicked.emit)
        self.split_analyze.analyze_frame_requested.connect(self.analyze_frame_clicked.emit)
        self.split_analyze.mode_changed.connect(self.analyze_mode_changed.emit)
        self.split_analyze.frame_min_tier_changed.connect(self.frame_min_tier_changed.emit)
        self.analyze_stack.addWidget(self.split_analyze)

        self.btn_cancel = QPushButton("분석 중단", self)
        self.btn_cancel.setObjectName("DangerButton")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setToolTip(
            format_tooltip_with_shortcut(_CANCEL_TOOLTIP_BASE, DEFAULT_SHORTCUTS["analyze.cancel"])
        )
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_clicked.emit)
        self.analyze_stack.addWidget(self.btn_cancel)

        self.analyze_stack.setCurrentIndex(0)
        layout.addWidget(self.analyze_stack)
        layout.addSpacing(_ANALYZE_GAP_AFTER_PX)

        # 인코딩 진입은 분석 버튼 옆의 화살표와 보조 버튼으로 표시
        # 화살표는 표시 전용이고, 버튼 문구가 인코딩 창을 연다는 뜻을 전달
        self.encode_arrow = create_encode_arrow_label(self)
        self.btn_encode_start = HeaderEncodeButton(self)
        self.btn_encode_start.setToolTip(
            format_tooltip_with_shortcut(_ENCODE_START_TOOLTIP, self._encode_start_key)
        )
        self.btn_encode_start.setEnabled(False)
        self.btn_encode_start.clicked.connect(self.encode_start_clicked.emit)
        layout.addWidget(self.encode_arrow)
        layout.addWidget(self.btn_encode_start)

        # 상단 영역 접기 버튼, 접힌 상태에서는 헤더가 숨겨지므로 탭 줄의 펼치기 버튼이 펼치기를 담당
        self.btn_collapse_top = create_icon_button(
            self,
            format_tooltip_with_shortcut(
                _COLLAPSE_TOOLTIP_BASE, DEFAULT_SHORTCUTS["view.toggle_top_area"]
            ),
            self.collapse_toggle_clicked.emit,
            accessible_name="상단 영역 접기",
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
            object_name="TopAreaCollapseButton",
        )
        set_button_icon(
            self.btn_collapse_top, TOP_AREA_COLLAPSE_ICON, size=TOOLBAR_ICON_SIZE_PX, pad_right=0
        )
        # - 접힌 줄의 펼치기 버튼과 같은 왼쪽 끝에 둠
        layout.insertWidget(0, self.btn_collapse_top)

        self._refresh_folder_name(initial_dir)
        self.init_theme_listener()

    @property
    def committed_path(self) -> str:
        """적용이 확정된 작업 폴더 경로 반환"""
        return self._committed_path

    def set_path(self, path: str) -> None:
        """확정 작업 폴더 경로와 입력창 텍스트, 폴더 이름 표시를 함께 갱신하고 변경을 알림"""
        self._committed_path = path
        self.path_edit.setText(path)
        self._set_path_invalid(None)
        self._refresh_folder_name(path)
        self._show_folder_name()
        self.committed_path_changed.emit(path)

    def begin_path_edit(self) -> None:
        """폴더 이름 대신 경로 입력창을 보이고 입력 포커스와 기존 경로 전체 선택"""
        self.path_stack.setCurrentWidget(self.path_edit)
        self.path_edit.setFocus()
        self.path_edit.selectAll()

    def release_path_focus(self) -> None:
        """경로 입력창이 포커스를 가진 경우 포커스를 해제해 포커스 이탈 처리를 일으킴"""
        self.path_edit.clearFocus()

    def restore_committed_path(self) -> None:
        """적용되지 않은 입력을 마지막 확정 경로로 되돌리고 폴더 이름 표시로 복귀"""
        self.path_edit.setText(self._committed_path)
        self._set_path_invalid(None)
        self._show_folder_name()

    def _show_folder_name(self) -> None:
        self.path_stack.setCurrentWidget(self.folder_name_btn)

    def _on_path_edit_focus_lost(self) -> None:
        """포커스 이탈 시 입력이 확정 경로와 같으면 폴더 이름으로 복귀하고, 바뀐 입력은 남겨 둠"""
        if self.path_edit.text().strip() == self._committed_path:
            self._show_folder_name()

    def _on_path_text_edited(self) -> None:
        """입력이 바뀌면 이전 오류 표시를 지움"""
        self._set_path_invalid(None)

    def _set_path_invalid(self, message: str | None) -> None:
        """입력창의 오류 상태와 툴팁을 설정, None이면 정상 상태로 되돌림"""
        set_state_property(self.path_edit, "invalid", message is not None)
        self.path_edit.setToolTip(message or _PATH_EDIT_HINT)

    @property
    def is_recursive(self) -> bool:
        """하위 폴더 포함 버튼의 선택 상태 반환"""
        return self.btn_recursive.isChecked()

    def set_recursive(self, enabled: bool, *, emit_signal: bool = False) -> None:
        """하위 폴더 포함 버튼 상태를 설정하고, 요청 시 변경 신호를 발생"""
        self.btn_recursive.setChecked(bool(enabled))
        if emit_signal:
            self.recursive_toggled.emit(bool(enabled))

    def _on_recursive_clicked(self) -> None:
        self.recursive_toggled.emit(self.btn_recursive.isChecked())

    def update_shortcut_tooltips(
        self,
        *,
        cancel_key: str,
        collapse_key: str,
        edit_path_key: str = "",
        add_dir_key: str = "",
        recursive_key: str = "",
        encode_key: str | None = None,
        refresh_key: str | None = None,
    ) -> None:
        """단축키 설정 변경 시 버튼과 경로 입력 툴팁의 단축키 표기를 갱신"""
        self._edit_path_key = edit_path_key
        if refresh_key is not None:
            self._refresh_key = refresh_key
        self.btn_refresh.setToolTip(
            format_tooltip_with_shortcut(_REFRESH_TOOLTIP_BASE, self._refresh_key)
        )
        if encode_key is not None:
            self._encode_start_key = encode_key
        self._refresh_encode_start_tooltip()
        self.btn_cancel.setToolTip(format_tooltip_with_shortcut(_CANCEL_TOOLTIP_BASE, cancel_key))
        self.btn_collapse_top.setToolTip(
            format_tooltip_with_shortcut(_COLLAPSE_TOOLTIP_BASE, collapse_key)
        )
        self.btn_add_dir.setToolTip(format_tooltip_with_shortcut(_ADD_DIR_TOOLTIP_BASE, add_dir_key))
        self.btn_recursive.setToolTip(
            format_tooltip_with_shortcut(_RECURSIVE_TOOLTIP_BASE, recursive_key)
        )
        self._refresh_folder_name(self._committed_path)

    def _refresh_folder_name(self, path: str) -> None:
        """경로 버튼 표시와 툴팁을 확정 경로 목록 기준으로 갱신"""
        self.folder_name_btn.set_path(path)
        hint = format_tooltip_with_shortcut(_PATH_HINT_BASE, self._edit_path_key)
        roots = parse_root_dirs(path)
        if not roots:
            self.folder_name_btn.setToolTip(hint)
            return
        listed = "\n".join(os.path.normpath(root) for root in roots)
        self.folder_name_btn.setToolTip(f"{listed}\n{hint}")

    def _on_path_edit_finished(self) -> None:
        roots = parse_root_dirs(normalize_path_separators(self.path_edit.text()))
        committed = format_root_dirs(parse_root_dirs(self._committed_path))
        if not roots or format_root_dirs(roots) == committed:
            self.restore_committed_path()
        elif not are_all_valid_dirs(roots):
            # - 잘못된 입력은 지우지 않고 남겨 두어 사용자가 바로 고칠 수 있게 함
            self._reject_path(_PATH_NOT_FOUND_MESSAGE)
            return
        else:
            self.path_submitted.emit(format_root_dirs(roots))
        self._show_folder_name()

    def _reject_path(self, message: str) -> None:
        """입력창을 오류 상태로 표시하고 입력창 아래에 안내를 띄운 뒤 하단 상태 표시줄에도 알림"""
        self._set_path_invalid(message)
        QToolTip.showText(
            self.path_edit.mapToGlobal(self.path_edit.rect().bottomLeft()),
            message,
            self.path_edit,
        )
        self.path_rejected.emit(message)

    def set_analyze_mode(self, mode: str) -> None:
        """헤더 분할 버튼의 스캔 모드 설정"""
        self.split_analyze.set_mode(mode)

    def set_frame_min_tier(self, tier_key: str) -> None:
        """화질 분석 측정 대상 저장 값을 분할 버튼 팝업에 반영"""
        self.split_analyze.set_frame_min_tier(tier_key)

    def set_encode_start_state(self, state: StartState) -> None:
        """인코딩 버튼을 메뉴 시작 항목과 같은 활성 상태와 사유로 맞춤"""
        self._encode_start_state = state
        self.btn_encode_start.setEnabled(state.enabled)
        self._refresh_encode_start_tooltip()

    def set_encode_status(self, status: EncodeChipStatus) -> None:
        """인코딩 버튼에 배치 상태와 진행률을 반영하고 툴팁을 갱신"""
        self._encoding_active = status.state in ACTIVE_CHIP_STATES
        self.btn_encode_start.set_status(status)
        self._refresh_encode_start_tooltip()

    def set_encode_target_count(self, count: int) -> None:
        """인코딩 대상 수를 버튼 배지에 반영"""
        self.btn_encode_start.set_target_count(count)

    def _refresh_encode_start_tooltip(self) -> None:
        """시작 버튼 툴팁에 지금 단축키와 안내 사유(진행 중 등)를 반영"""
        state = self._encode_start_state
        if self._encoding_active:
            text = _ENCODE_ACTIVE_TOOLTIP
        elif state is not None and state.reason:
            text = state.reason
        else:
            text = _ENCODE_START_TOOLTIP
        self.btn_encode_start.setToolTip(
            format_tooltip_with_shortcut(text, self._encode_start_key)
        )

    def set_min_tier_provider(self, provider: Callable[[], str]) -> None:
        """팝업을 열 때 화질 분석 측정 대상 값을 얻는 콜백 등록"""
        self.split_analyze.set_min_tier_provider(provider)

    @property
    def current_analyze_mode(self) -> str:
        """헤더 분할 버튼의 현재 스캔 모드 반환"""
        return self.split_analyze.current_mode

    def set_busy(self, busy: bool) -> None:
        """작업 진행 여부에 따라 컨트롤 활성화 상태를 제어하고, 스캔 버튼과 중단 버튼을 인플레이스 전환"""
        self.path_edit.setEnabled(not busy)
        self.folder_name_btn.setEnabled(not busy)
        self.btn_browse.setEnabled(not busy)
        self.btn_add_dir.setEnabled(not busy)
        self.btn_recursive.setEnabled(not busy)
        self.btn_open_explorer.setEnabled(not busy)
        self.btn_refresh.setEnabled(not busy)
        self.split_analyze.setEnabled(not busy)
        self.btn_encode_start.setEnabled(
            False
            if busy
            else (self._encode_start_state.enabled if self._encode_start_state is not None else False)
        )
        self.btn_cancel.setEnabled(busy)
        self.analyze_stack.setCurrentIndex(1 if busy else 0)

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 버튼 아이콘 색상을 갱신"""
        self.folder_name_btn.set_palette(palette)
        self.folder_icon.setPixmap(
            get_icon("folder", palette=palette, size=TOOLBAR_ICON_SIZE_PX).pixmap(
                TOOLBAR_ICON_SIZE_PX, TOOLBAR_ICON_SIZE_PX
            )
        )
        for button, icon_name in (
            (self.btn_browse, "folder-open"),
            (self.btn_add_dir, "folder-plus"),
            (self.btn_recursive, "folder-tree"),
            (self.btn_open_explorer, "external-link"),
            (self.btn_refresh, "rotate-cw"),
            (self.btn_collapse_top, TOP_AREA_COLLAPSE_ICON),
        ):
            set_button_icon(
                button, icon_name, palette=palette, size=TOOLBAR_ICON_SIZE_PX, pad_right=0
            )
        set_encode_arrow_color(self.encode_arrow, palette)
        self.btn_encode_start.apply_theme(palette)
        self.split_analyze.apply_theme(palette)
        set_button_icon(
            self.btn_cancel,
            "x-circle",
            color=ON_ACCENT_COLOR,
            size=_MODE_ICON_SIZE_PX,
            pad_right=_MODE_ICON_GAP_PX,
        )

    def set_collapse_button_visible(self, visible: bool) -> None:
        """상단 영역이 펼쳐진 동안에만 접기 버튼을 표시"""
        self.btn_collapse_top.setVisible(visible)
