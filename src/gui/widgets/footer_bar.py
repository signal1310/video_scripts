"""하단 진행률, 상태 표시줄 및 실제 분류, 복원 액션 바 위젯 모듈"""

import html

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.gui.footer_summary import FooterSummarySpec
from src.gui.styles.icons import (
    create_icon_button,
    get_active_palette,
    set_button_icon,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin


class FooterBar(QFrame, ThemeAwareMixin):
    """구간 합계 요약, 진행률 표시줄, 상태 안내 및 액션 버튼을 제공하는 하단 바 위젯"""

    unclassify_all_clicked = pyqtSignal()
    real_classify_clicked = pyqtSignal()
    logs_clicked = pyqtSignal()
    export_csv_clicked = pyqtSignal()

    _COMPACT_ICON_BTN_SIZE: int = 34

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FooterBar")
        self._palette: ThemePalette = get_active_palette()
        self._summary_spec: FooterSummarySpec | None = None

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(2)

        self.lbl_summary = QLabel("")
        self.lbl_summary.setObjectName("FooterSummaryText")
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

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)

        layout.addLayout(left_col, stretch=1)
        layout.addWidget(self.progress_bar)

        self.btn_logs = create_icon_button(
            self,
            "작업 로그를 확인합니다. [Ctrl+L]",
            self.logs_clicked.emit,
            accessible_name="작업 로그",
            fixed_size=self._COMPACT_ICON_BTN_SIZE,
        )

        self.btn_export_csv = create_icon_button(
            self,
            "현재 테이블 결과를 CSV 파일로 내보냅니다. [Ctrl+E]",
            self.export_csv_clicked.emit,
            accessible_name="CSV 내보내기",
            fixed_size=self._COMPACT_ICON_BTN_SIZE,
        )

        self.btn_unclassify_all = QPushButton("작업 폴더로 복원")
        self.btn_unclassify_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_unclassify_all.setToolTip(
            "하위 분류 폴더로 이동된 파일들을 모두 작업 폴더로 복원합니다."
        )
        self.btn_unclassify_all.clicked.connect(self.unclassify_all_clicked.emit)

        self.btn_real_classify = QPushButton("분류 폴더로 이동")
        self.btn_real_classify.setObjectName("SuccessButton")
        self.btn_real_classify.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_real_classify.setToolTip(
            "인코딩 대상 영상들을 분류 하위 폴더로 실제 이동합니다."
        )
        self.btn_real_classify.clicked.connect(self.real_classify_clicked.emit)

        layout.addWidget(self.btn_logs)
        layout.addWidget(self.btn_export_csv)
        layout.addWidget(self.btn_unclassify_all)
        layout.addWidget(self.btn_real_classify)

        self.init_theme_listener()

    @staticmethod
    def _render_summary_html(spec: FooterSummarySpec, p: ThemePalette) -> str:
        scope_html = html.escape(spec.scope_label)
        if not spec.metrics:
            return f"<span style='color:{p.text_secondary};'>{scope_html}</span>"
        sep = f"<span style='color:{p.text_muted};'>,&nbsp;&nbsp;</span>"
        parts = [f"<span style='color:{p.accent_blue};'>{scope_html}</span>"]
        for label, value in spec.metrics:
            parts.append(
                f"<span style='color:{p.text_secondary};'>{html.escape(label)}</span> "
                f"<span style='color:{p.text_primary};'>{html.escape(value)}</span>"
            )
        return sep.join(parts)

    def set_summary_spec(self, spec: FooterSummarySpec) -> None:
        """범위, 지표 쌍 명세를 테마 색상이 반영된 계층형 요약 라벨로 렌더링"""
        if not spec.plain_text:
            self._summary_spec = None
            self.lbl_summary.setTextFormat(Qt.TextFormat.PlainText)
            self.lbl_summary.setText("")
            self.lbl_summary.setToolTip("")
            self.lbl_summary.setVisible(False)
            return
        self._summary_spec = spec
        self.lbl_summary.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_summary.setText(self._render_summary_html(spec, self._palette))
        self.lbl_summary.setToolTip(spec.tooltip)
        self.lbl_summary.setVisible(True)

    def set_status_text(self, text: str) -> None:
        """진행 상태 안내 문구를 갱신하고 빈 문자열이면 보조 줄을 숨김"""
        self.lbl_status.setText(text)
        self.lbl_status.setVisible(bool(text))

    def set_busy(self, busy: bool, status_msg: str = "") -> None:
        """작업 진행 여부에 따라 프로그레스 바 가시성, 버튼 활성화 상태 및 상태 메시지를 갱신"""
        self.progress_bar.setVisible(busy)
        if not busy:
            self.progress_bar.setValue(0)
        self.btn_export_csv.setEnabled(not busy)
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
        if self._summary_spec is not None and self._summary_spec.plain_text:
            self.lbl_summary.setText(
                self._render_summary_html(self._summary_spec, palette)
            )
        set_button_icon(
            self.btn_logs,
            "file-text",
            palette=palette,
            size=15,
            pad_right=0,
        )
        set_button_icon(
            self.btn_export_csv,
            "download",
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
