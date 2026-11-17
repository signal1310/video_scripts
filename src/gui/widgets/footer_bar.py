"""하단 진행률, 상태 표시줄 및 실제 분류, 복원 액션 바 위젯 모듈"""

import html
from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.shortcuts import DEFAULT_SHORTCUTS, format_tooltip_with_shortcut
from src.gui.footer_summary import FooterSummaryGroup, FooterSummarySpec
from src.gui.styles.icons import (
    create_icon_button,
    get_active_palette,
    set_button_icon,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.gui.widgets.progress_fill_bar import FillProgressBar

_SCOPE_SELECTED: str = "selected"
_SCOPE_VISIBLE: str = "visible"
_PROGRESS_MAX_PERCENT: int = 100
_LOGS_TOOLTIP_BASE: str = "작업 기록을 엽니다."
_UNCLASSIFY_TOOLTIP_BASE: str = "하위 분류 폴더로 이동된 파일들을 모두 작업 폴더로 복원합니다."
_REAL_CLASSIFY_TOOLTIP_BASE: str = "인코딩 대상 영상들을 분류 하위 폴더로 실제 이동합니다."


class _ToggleSummaryLabel(QLabel):
    """표시와 선택 요약이 함께 있을 때 좌클릭으로 상세 범위를 전환하는 라벨"""

    def __init__(self, on_click: Callable[[], None], parent: QWidget | None = None) -> None:
        super().__init__("", parent)
        self._on_click = on_click
        self._toggleable: bool = False
        self.setObjectName("FooterSummaryText")

    def set_toggleable(self, enabled: bool) -> None:
        """전환 가능 여부에 따라 포인터 커서 표시를 맞춤"""
        self._toggleable = enabled
        self.setCursor(
            Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor
        )

    def mousePressEvent(self, ev: QMouseEvent | None) -> None:
        """좌클릭 시 푸터 요약 상세 범위 전환"""
        if self._toggleable and ev is not None and ev.button() == Qt.MouseButton.LeftButton:
            self._on_click()
            ev.accept()
            return
        super().mousePressEvent(ev)


class FooterBar(QFrame, ThemeAwareMixin):
    """구간 합계 요약, 진행률 표시줄, 상태 안내 및 액션 버튼을 제공하는 하단 바 위젯"""

    unclassify_all_clicked = pyqtSignal()
    real_classify_clicked = pyqtSignal()
    logs_clicked = pyqtSignal()

    _COMPACT_ICON_BTN_SIZE: int = 34

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FooterBar")
        self._palette: ThemePalette = get_active_palette()
        self._summary_spec: FooterSummarySpec | None = None
        self._detail_scope: str = _SCOPE_SELECTED

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        left_slot = QWidget(self)
        left_slot.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        left_col = QVBoxLayout(left_slot)
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(2)

        self.lbl_summary = _ToggleSummaryLabel(self._toggle_summary_scope, left_slot)
        self.lbl_summary.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.lbl_summary.setVisible(False)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("StatusText")
        self.lbl_status.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.lbl_status.setVisible(False)

        left_col.addWidget(self.lbl_summary)
        left_col.addWidget(self.lbl_status)

        self.progress_bar = FillProgressBar()
        self.progress_bar.set_palette(self._palette)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)

        layout.addWidget(left_slot, stretch=1)
        layout.addWidget(self.progress_bar)

        self.btn_logs = create_icon_button(
            self,
            format_tooltip_with_shortcut(_LOGS_TOOLTIP_BASE, DEFAULT_SHORTCUTS["view.show_logs"]),
            self.logs_clicked.emit,
            accessible_name="작업 기록",
            fixed_size=self._COMPACT_ICON_BTN_SIZE,
        )

        self.btn_unclassify_all = QPushButton("작업 폴더로 복원")
        self.btn_unclassify_all.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.btn_unclassify_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_unclassify_all.setToolTip(_UNCLASSIFY_TOOLTIP_BASE)
        self.btn_unclassify_all.clicked.connect(self.unclassify_all_clicked.emit)

        self.btn_real_classify = QPushButton("분류 폴더로 이동")
        self.btn_real_classify.setObjectName("SuccessButton")
        self.btn_real_classify.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Preferred)
        self.btn_real_classify.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_real_classify.setToolTip(_REAL_CLASSIFY_TOOLTIP_BASE)
        self.btn_real_classify.clicked.connect(self.real_classify_clicked.emit)

        layout.addWidget(self.btn_logs)
        layout.addWidget(self.btn_unclassify_all)
        layout.addWidget(self.btn_real_classify)

        self.init_theme_listener()

    @staticmethod
    def _render_group_html(group: FooterSummaryGroup, p: ThemePalette) -> str:
        """범위 라벨과 지표 쌍을 한 줄 HTML로 렌더링"""
        scope_html = f"<span style='color:{p.accent_blue};'>{html.escape(group.scope_label)}</span>"
        if not group.metrics:
            return scope_html
        sep = f"<span style='color:{p.text_muted};'>,&nbsp;&nbsp;</span>"
        metric_parts = [
            f"<span style='color:{p.text_secondary};'>{html.escape(label)}</span> "
            f"<span style='color:{p.text_primary};'>{html.escape(value)}</span>"
            for label, value in group.metrics
        ]
        return scope_html + sep + sep.join(metric_parts)

    def _toggle_summary_scope(self) -> None:
        if self._summary_spec is None or len(self._summary_spec.groups) < 2:
            return
        self._detail_scope = (
            _SCOPE_VISIBLE if self._detail_scope == _SCOPE_SELECTED else _SCOPE_SELECTED
        )
        self._render_summary_view(self._summary_spec, self._palette)

    def _render_summary_view(self, spec: FooterSummarySpec, p: ThemePalette) -> None:
        groups = spec.groups
        if not groups:
            return
        if len(groups) == 1:
            body_html = self._render_group_html(groups[0], p)
            tooltip = spec.tooltip
            toggleable = False
        else:
            visible_group, selected_group = groups[0], groups[1]
            detail_group, alt_group = (
                (visible_group, selected_group)
                if self._detail_scope == _SCOPE_VISIBLE
                else (selected_group, visible_group)
            )
            alt_html = (
                f"<span style='color:{p.text_muted};'>"
                f"{html.escape(alt_group.scope_label)}&nbsp;&nbsp;/&nbsp;&nbsp;</span>"
            )
            body_html = alt_html + self._render_group_html(detail_group, p)
            toggle_hint = f"• 클릭하면 {alt_group.scope_label} 상세 합계로 전환합니다."
            tooltip = f"{spec.tooltip}\n{toggle_hint}" if spec.tooltip else toggle_hint
            toggleable = True
        self.lbl_summary.set_toggleable(toggleable)
        self.lbl_summary.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_summary.setText(body_html)
        self.lbl_summary.setToolTip(tooltip)
        self.lbl_summary.setVisible(True)

    def set_summary_spec(self, spec: FooterSummarySpec) -> None:
        """범위 그룹 명세를 테마 색상이 반영된 한 줄 요약 라벨로 렌더링"""
        if not spec.groups:
            self._summary_spec = None
            self._detail_scope = _SCOPE_SELECTED
            self.lbl_summary.set_toggleable(False)
            self.lbl_summary.setTextFormat(Qt.TextFormat.PlainText)
            self.lbl_summary.setText("")
            self.lbl_summary.setToolTip("")
            self.lbl_summary.setVisible(False)
            return
        if len(spec.groups) < 2:
            self._detail_scope = _SCOPE_SELECTED
        self._summary_spec = spec
        self._render_summary_view(spec, self._palette)

    def update_shortcut_tooltips(
        self,
        *,
        logs_key: str,
        classify_key: str = "",
        unclassify_key: str = "",
    ) -> None:
        """단축키 설정 변경을 하단 버튼 툴팁의 단축키 표기에 반영"""
        self.btn_logs.setToolTip(format_tooltip_with_shortcut(_LOGS_TOOLTIP_BASE, logs_key))
        self.btn_unclassify_all.setToolTip(
            format_tooltip_with_shortcut(_UNCLASSIFY_TOOLTIP_BASE, unclassify_key)
        )
        self.btn_real_classify.setToolTip(
            format_tooltip_with_shortcut(_REAL_CLASSIFY_TOOLTIP_BASE, classify_key)
        )

    def set_status_text(self, text: str) -> None:
        """진행 상태 안내 문구를 갱신하고 빈 문자열이면 보조 줄을 숨김"""
        self.lbl_status.setText(text)
        self.lbl_status.setVisible(bool(text))

    def set_progress(self, value: int, detail: str = "", tooltip: str = "") -> None:
        """진행률 값, 막대 안 문구, 툴팁을 설정하며 무한 진행 상태를 해제"""
        self.progress_bar.setRange(0, _PROGRESS_MAX_PERCENT)
        self.progress_bar.setValue(value)
        self.progress_bar.setFormat(detail)
        self.progress_bar.setToolTip(tooltip)

    def set_progress_pending(self) -> None:
        """완료 개수를 아직 알 수 없는 구간을 무한 진행 막대로 표시"""
        self.progress_bar.setRange(0, 0)

    def set_busy(self, busy: bool, status_msg: str = "") -> None:
        """작업 진행 여부에 따라 프로그레스 바 가시성, 버튼 활성화 상태 및 상태 메시지를 갱신"""
        self.progress_bar.setVisible(busy)
        if not busy:
            self.progress_bar.setValue(0)
        self.btn_unclassify_all.setEnabled(not busy)
        self.btn_real_classify.setEnabled(not busy)
        if status_msg:
            self.set_status_text(status_msg)

    def set_action_availability(
        self, *, can_classify: bool, can_unclassify: bool
    ) -> None:
        """분류 이동, 복원 실행 가능 여부에 따라 하단 액션 버튼 활성화 상태 갱신"""
        self.btn_real_classify.setEnabled(can_classify)
        self.btn_unclassify_all.setEnabled(can_unclassify)

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 버튼 아이콘 및 요약 라벨 색상을 갱신"""
        self._palette = palette
        self.progress_bar.set_palette(palette)
        if self._summary_spec is not None and self._summary_spec.groups:
            self._render_summary_view(self._summary_spec, palette)
        set_button_icon(
            self.btn_logs,
            "file-text",
            palette=palette,
            size=15,
            pad_right=0,
        )
        set_button_icon(
            self.btn_unclassify_all,
            "rotate-ccw",
            palette=palette,
            size=14,
            pad_right=7,
        )
        # 비활성 QSS 텍스트 색(text_muted)과 아이콘 색을 맞추기 위해 상태별 색을 함께 지정
        set_button_icon(
            self.btn_real_classify,
            "folder",
            color=palette.text_on_accent,
            size=14,
            pad_right=7,
            disabled_color=palette.text_muted,
        )
