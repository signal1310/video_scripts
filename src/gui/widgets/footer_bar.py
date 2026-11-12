from typing import Optional
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QWidget,
)

from src.gui.styles.icons import get_active_palette, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin


class FooterBar(QFrame, ThemeAwareMixin):
    """
    - 진행률 표시줄, 상태 안내 및 액션 버튼을 제공하는 하단 바 위젯.
    """

    unclassify_all_clicked = pyqtSignal()
    real_classify_clicked = pyqtSignal()

    # 하위 호환성 유지 시그널
    pseudo_classify_clicked = pyqtSignal()
    unmark_pseudo_clicked = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("FooterBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        self.lbl_status = QLabel("준비 완료")
        self.lbl_status.setObjectName("StatusText")

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedWidth(220)
        self.progress_bar.setVisible(False)

        layout.addWidget(self.lbl_status, stretch=1)
        layout.addWidget(self.progress_bar)

        self.btn_unclassify_all = QPushButton("분류 원복")
        set_button_icon(
            self.btn_unclassify_all,
            "rotate-ccw",
            palette=get_active_palette(),
            size=14,
            pad_right=7,
        )
        self.btn_unclassify_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_unclassify_all.setToolTip(
            "하위 분류 폴더로 이동된 파일들을 모두 루트 작업 경로로 복구"
        )
        self.btn_unclassify_all.clicked.connect(self.unclassify_all_clicked.emit)

        self.btn_real_classify = QPushButton("폴더 분류 이동")
        set_button_icon(
            self.btn_real_classify,
            "folder",
            color="#FFFFFF",
            size=14,
            pad_right=7,
        )
        self.btn_real_classify.setObjectName("SuccessButton")
        self.btn_real_classify.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_real_classify.setToolTip(
            "인코딩 대상 영상들을 목적 하위 폴더로 실제 이동"
        )
        self.btn_real_classify.clicked.connect(self.real_classify_clicked.emit)

        # 편의용 별칭
        self.btn_unclassify = self.btn_unclassify_all
        self.btn_classify = self.btn_real_classify

        layout.addWidget(self.btn_unclassify_all)
        layout.addWidget(self.btn_real_classify)

        self.init_theme_listener()

    def set_busy(self, busy: bool, status_msg: str = "") -> None:
        """
        - 작업 진행 여부에 따라 프로그레스 바 가시성, 버튼 활성화 상태 및 상태 메시지를 갱신.
        """
        self.progress_bar.setVisible(busy)
        if not busy:
            self.progress_bar.setValue(0)
        self.btn_unclassify_all.setEnabled(not busy)
        self.btn_real_classify.setEnabled(not busy)
        if status_msg:
            self.lbl_status.setText(status_msg)

    def apply_theme(self, palette: ThemePalette) -> None:
        """
        - 활성 테마 팔레트에 맞춰 버튼 아이콘 색상을 갱신.
        """
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
            color="#FFFFFF",
            size=14,
            pad_right=7,
        )
