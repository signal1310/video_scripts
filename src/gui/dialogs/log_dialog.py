from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QClipboard
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin, ThemeManager, build_stylesheet


class LogDialog(QDialog, ThemeAwareMixin):
    """
    - 백그라운드 분석/분류 작업 중 기록된 로그 이력을 조회·복사·초기화하는 대화상자.
    """

    def __init__(
        self,
        logs: list[str],
        on_clear: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("작업 로그")
        self.resize(680, 440)
        self.setMinimumSize(460, 300)
        self._on_clear = on_clear

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)

        lbl_title = QLabel("작업 로그 이력", self)
        lbl_title.setObjectName("GroupHeaderTitle")
        layout.addWidget(lbl_title)

        self.text_edit = QPlainTextEdit(self)
        self.text_edit.setObjectName("LogViewer")
        self.text_edit.setReadOnly(True)
        self.text_edit.setPlainText("\n".join(logs) if logs else "기록된 로그가 없습니다.")
        layout.addWidget(self.text_edit, stretch=1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)

        self.btn_copy = QPushButton("전체 복사", self)
        self.btn_copy.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_copy.clicked.connect(self._copy_logs)

        self.btn_clear = QPushButton("로그 비우기", self)
        self.btn_clear.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_clear.clicked.connect(self._clear_logs)

        self.btn_close = QPushButton("닫기", self)
        self.btn_close.setObjectName("PrimaryButton")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self.close)

        btn_row.addWidget(self.btn_copy)
        btn_row.addWidget(self.btn_clear)
        btn_row.addWidget(self.btn_close)
        layout.addLayout(btn_row)

        self.setStyleSheet(build_stylesheet(ThemeManager().current_palette))
        self.init_theme_listener()

    def set_logs(self, logs: list[str]) -> None:
        self.text_edit.setPlainText("\n".join(logs) if logs else "기록된 로그가 없습니다.")
        sb = self.text_edit.verticalScrollBar()
        if sb is not None:
            sb.setValue(sb.maximum())

    def _copy_logs(self) -> None:
        cb: QClipboard | None = QApplication.clipboard()
        if cb is not None:
            cb.setText(self.text_edit.toPlainText())

    def _clear_logs(self) -> None:
        if self._on_clear is not None:
            self._on_clear()
        self.text_edit.setPlainText("기록된 로그가 없습니다.")

    def apply_theme(self, palette: ThemePalette) -> None:
        self.setStyleSheet(build_stylesheet(palette))
