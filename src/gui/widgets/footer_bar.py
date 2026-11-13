"""하단 진행률, 상태 표시줄 및 실제 분류·복원 액션 바 위젯 모듈"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QWidget,
)

from src.gui.constants import ON_ACCENT_COLOR
from src.gui.styles.icons import create_icon_button, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin


class FooterBar(QFrame, ThemeAwareMixin):
    """진행률 표시줄, 상태 안내 및 액션 버튼을 제공하는 하단 바 위젯"""

    unclassify_all_clicked = pyqtSignal()
    real_classify_clicked = pyqtSignal()
    logs_clicked = pyqtSignal()
    export_csv_clicked = pyqtSignal()

    _COMPACT_ICON_BTN_SIZE: int = 34

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FooterBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        self.lbl_status = QLabel("")
        self.lbl_status.setObjectName("StatusText")

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)

        layout.addWidget(self.lbl_status, stretch=1)
        layout.addWidget(self.progress_bar)

        self.btn_logs = create_icon_button(
            self,
            "작업 로그를 확인합니다. [Ctrl+L]",
            self.logs_clicked.emit,
            fixed_size=self._COMPACT_ICON_BTN_SIZE,
        )

        self.btn_export_csv = create_icon_button(
            self,
            "현재 테이블 결과를 CSV 파일로 내보냅니다. [Ctrl+E]",
            self.export_csv_clicked.emit,
            fixed_size=self._COMPACT_ICON_BTN_SIZE,
        )

        self.btn_unclassify_all = QPushButton("분류 복원")
        self.btn_unclassify_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_unclassify_all.setToolTip(
            "하위 분류 폴더로 이동된 파일들을 모두 작업 폴더로 복원합니다."
        )
        self.btn_unclassify_all.clicked.connect(self.unclassify_all_clicked.emit)

        self.btn_real_classify = QPushButton("폴더 분류 이동")
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

    def set_busy(self, busy: bool, status_msg: str = "") -> None:
        """작업 진행 여부에 따라 프로그레스 바 가시성, 버튼 활성화 상태 및 상태 메시지를 갱신"""
        self.progress_bar.setVisible(busy)
        if not busy:
            self.progress_bar.setValue(0)
        self.btn_export_csv.setEnabled(not busy)
        self.btn_unclassify_all.setEnabled(not busy)
        self.btn_real_classify.setEnabled(not busy)
        if status_msg:
            self.lbl_status.setText(status_msg)

    def set_action_availability(
        self, *, can_classify: bool, can_unclassify: bool
    ) -> None:
        """분류 이동·복원 실행 가능 여부에 따라 하단 액션 버튼 활성화 상태 갱신"""
        self.btn_real_classify.setEnabled(can_classify)
        self.btn_unclassify_all.setEnabled(can_unclassify)

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 버튼 아이콘 색상을 갱신"""
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
        set_button_icon(
            self.btn_real_classify,
            "folder",
            color=ON_ACCENT_COLOR,
            size=14,
            pad_right=7,
        )
