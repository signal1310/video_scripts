
from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from src.gui.constants import ON_ACCENT_COLOR, TOOLBAR_ICON_BTN_SIZE
from src.gui.styles.icons import create_icon_button, get_icon, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin


class SplitScanButton(QFrame):
    """
    - 빠른 스캔(기본값)과 정밀 분석을 실행할 수 있는 분할(Split) 실행 버튼 컴포넌트.
    - 본체 클릭 시 현재 선택된 스캔 모드를 즉시 실행하고, 드롭다운 화살표를 통해 모드를 전환.
    """

    MODE_FAST = "fast"
    MODE_FULL = "full"

    scan_fast_requested = pyqtSignal()
    scan_full_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SplitScanFrame")
        self._mode = self.MODE_FAST

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.btn_main = QPushButton("빠른 스캔", self)
        self.btn_main.setObjectName("SplitMainButton")
        self.btn_main.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_main.clicked.connect(self._on_main_clicked)

        self.btn_arrow = QPushButton(self)
        self.btn_arrow.setObjectName("SplitArrowButton")
        self.btn_arrow.setFixedWidth(26)
        self.btn_arrow.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_arrow.setToolTip("스캔 모드 선택: 빠른 스캔 / 정밀 분석")
        self.btn_arrow.clicked.connect(self._on_arrow_clicked)

        layout.addWidget(self.btn_main)
        layout.addWidget(self.btn_arrow)

        self._palette: ThemePalette | None = None
        self.set_mode(self.MODE_FAST)

    @property
    def current_mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        if mode == self.MODE_FULL:
            self._mode = self.MODE_FULL
            self.btn_main.setText("정밀 분석")
            set_button_icon(
                self.btn_main, "scan", color=ON_ACCENT_COLOR, size=14, pad_right=7
            )
            self.btn_main.setToolTip("모든 영상의 키프레임 간격을 실측하여 분류 처방 산출")
        else:
            self._mode = self.MODE_FAST
            self.btn_main.setText("빠른 스캔")
            set_button_icon(
                self.btn_main, "zap", color=ON_ACCENT_COLOR, size=14, pad_right=7
            )
            self.btn_main.setToolTip(
                "키프레임 측정을 생략하고 해상도 및 비트레이트를 빠르게 스캔"
            )
        set_button_icon(
            self.btn_arrow, "chevron-down", color=ON_ACCENT_COLOR, size=12, pad_right=0
        )

    def setEnabled(self, enabled: bool) -> None:
        super().setEnabled(enabled)
        self.btn_main.setEnabled(enabled)
        self.btn_arrow.setEnabled(enabled)

    def apply_theme(self, palette: ThemePalette) -> None:
        self._palette = palette
        self.set_mode(self._mode)

    def _on_main_clicked(self) -> None:
        if self._mode == self.MODE_FULL:
            self.scan_full_requested.emit()
        else:
            self.scan_fast_requested.emit()

    def _on_arrow_clicked(self) -> None:
        from src.gui.theme import ThemeManager

        p = self._palette or ThemeManager().current_palette
        menu = QMenu(self)
        menu.setMinimumWidth(self.width())
        act_fast = menu.addAction(
            get_icon("zap", palette=p, size=16, pad_right=0),
            "빠른 스캔",
        )
        act_full = menu.addAction(
            get_icon("scan", palette=p, size=16, pad_right=0),
            "정밀 분석",
        )
        menu_w = max(menu.sizeHint().width(), self.width())
        pos_x = 0 if menu_w == self.width() else self.width() - menu_w
        pos = self.mapToGlobal(QPoint(pos_x, self.height() + 3))
        action = menu.exec(pos)
        if action == act_fast:
            self.set_mode(self.MODE_FAST)
        elif action == act_full:
            self.set_mode(self.MODE_FULL)


class HeaderBar(QFrame, ThemeAwareMixin):
    """
    - 작업 경로 선택, 스캔 실행 및 환경설정 진입을 제공하는 헤더 바 위젯.
    """

    browse_clicked = pyqtSignal()
    open_explorer_clicked = pyqtSignal()
    scan_fast_clicked = pyqtSignal()
    scan_full_clicked = pyqtSignal()
    cancel_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()

    def __init__(
        self, initial_dir: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("HeaderBar")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        path_lbl = QLabel("작업 폴더:")
        path_lbl.setObjectName("SectionLabel")
        self.path_edit = QLineEdit(initial_dir)
        self.path_edit.setPlaceholderText(
            "작업 폴더 경로를 선택하거나 입력하세요..."
        )
        self.path_edit.setReadOnly(True)

        self.btn_browse = create_icon_button(
            self,
            "작업 폴더 선택",
            self.browse_clicked.emit,
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
        )

        self.btn_open_explorer = create_icon_button(
            self,
            "현재 작업 폴더를 파일 탐색기에서 열기",
            self.open_explorer_clicked.emit,
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
        )

        layout.addWidget(path_lbl)
        layout.addWidget(self.path_edit, stretch=1)
        layout.addWidget(self.btn_browse)
        layout.addWidget(self.btn_open_explorer)
        layout.addSpacing(8)

        # 스캔 실행 스플릿 버튼 및 인플레이스 중단 버튼 (QStackedWidget)
        self.scan_stack = QStackedWidget(self)
        self.scan_stack.setObjectName("ScanStack")

        self.split_scan = SplitScanButton(self)
        self.split_scan.scan_fast_requested.connect(self.scan_fast_clicked.emit)
        self.split_scan.scan_full_requested.connect(self.scan_full_clicked.emit)
        self.scan_stack.addWidget(self.split_scan)

        self.btn_cancel = QPushButton("분석 중단", self)
        self.btn_cancel.setObjectName("DangerButton")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setToolTip("현재 진행 중인 분석 작업을 즉시 중단합니다 [Esc]")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_clicked.emit)
        self.scan_stack.addWidget(self.btn_cancel)

        self.scan_stack.setCurrentIndex(0)
        layout.addWidget(self.scan_stack)
        layout.addSpacing(6)

        # 환경설정 버튼 (아이콘 전용 미니멀 버튼)
        self.btn_settings = create_icon_button(
            self,
            "환경설정 및 기본값 관리",
            self.settings_clicked.emit,
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
            object_name="SettingsButton",
        )
        layout.addWidget(self.btn_settings)

        self.init_theme_listener()

    def set_scan_mode(self, mode: str) -> None:
        self.split_scan.set_mode(mode)

    @property
    def current_scan_mode(self) -> str:
        return self.split_scan.current_mode

    def set_busy(self, busy: bool) -> None:
        """
        - 작업 진행 여부에 따라 컨트롤 활성화 상태를 제어하고, 스캔 버튼과 중단 버튼을 인플레이스 전환.
        """
        self.btn_browse.setEnabled(not busy)
        self.btn_open_explorer.setEnabled(not busy)
        self.split_scan.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self.scan_stack.setCurrentIndex(1 if busy else 0)

    def apply_theme(self, palette: ThemePalette) -> None:
        """
        - 활성 테마 팔레트에 맞춰 버튼 아이콘 색상을 갱신.
        """
        set_button_icon(self.btn_browse, "folder", palette=palette, size=16, pad_right=0)
        set_button_icon(self.btn_open_explorer, "folder-open", palette=palette, size=16, pad_right=0)
        self.split_scan.apply_theme(palette)
        set_button_icon(
            self.btn_cancel, "x-circle", color=ON_ACCENT_COLOR, size=14, pad_right=7
        )
        set_button_icon(self.btn_settings, "settings", palette=palette, size=16, pad_right=0)
