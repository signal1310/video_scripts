"""직접 입력·방향키·마우스 휠·연속 증감을 지원하는 커스텀 정수 스핀박스 모듈"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QIntValidator, QKeyEvent, QWheelEvent
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin


class CustomIntegerSpinBox(QFrame, ThemeAwareMixin):
    """정수 입력 및 상하 증감 버튼을 제공하는 커스텀 스핀박스 위젯

    직접 입력, 키보드 방향키, 마우스 휠 및 버튼 연속 증감 지원
    """

    valueChanged = pyqtSignal(int)
    textChanged = pyqtSignal(str)

    def __init__(
        self,
        minimum: int = 0,
        maximum: int = 999999,
        step: int = 1,
        value: int = 0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CustomSpinBox")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._min: int = minimum
        self._max: int = maximum
        self._step: int = max(step, 1)
        self._value: int = max(minimum, min(value, maximum))
        self._suffix: str = ""
        self._prefix: str = ""

        self._init_ui()
        self.setValue(self._value)
        self.init_theme_listener()

    def _init_ui(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(4, 2, 2, 2)
        main_layout.setSpacing(2)

        self.prefix_label = QLabel()
        self.prefix_label.setObjectName("SpinPrefix")
        self.prefix_label.setVisible(False)
        main_layout.addWidget(self.prefix_label)

        self.edit = QLineEdit(self)
        self.edit.setObjectName("CustomSpinBoxEdit")
        self.edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._validator = QIntValidator(self._min, self._max, self)
        self.edit.setValidator(self._validator)
        self.edit.textChanged.connect(self._on_text_changed)
        self.edit.editingFinished.connect(self._on_editing_finished)
        main_layout.addWidget(self.edit, stretch=1)

        self.suffix_label = QLabel()
        self.suffix_label.setObjectName("SpinSuffix")
        self.suffix_label.setVisible(False)
        main_layout.addWidget(self.suffix_label)

        # 우측 세로 스텝 버튼 (SVG Chevron-Up / Chevron-Down)
        btn_col = QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(1)

        pal = get_active_palette()
        self.btn_up = QPushButton(self)
        self.btn_up.setObjectName("SpinStepUp")
        self.btn_up.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_up.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_up.setIcon(get_icon("chevron-up", palette=pal, size=10))
        self.btn_up.setAutoRepeat(True)
        self.btn_up.setAutoRepeatDelay(350)
        self.btn_up.setAutoRepeatInterval(60)
        self.btn_up.clicked.connect(self.stepUp)

        self.btn_down = QPushButton(self)
        self.btn_down.setObjectName("SpinStepDown")
        self.btn_down.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_down.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_down.setIcon(get_icon("chevron-down", palette=pal, size=10))
        self.btn_down.setAutoRepeat(True)
        self.btn_down.setAutoRepeatDelay(350)
        self.btn_down.setAutoRepeatInterval(60)
        self.btn_down.clicked.connect(self.stepDown)

        btn_col.addWidget(self.btn_up)
        btn_col.addWidget(self.btn_down)
        main_layout.addLayout(btn_col)

    def value(self) -> int:
        """현재 정수값 반환"""
        return self._value

    def setValue(self, val: int) -> None:
        """범위 내로 보정된 정수값 설정 및 변경 시그널 방출"""
        clamped = max(self._min, min(int(val), self._max))
        if self._value != clamped or self.edit.text() != str(clamped):
            self._value = clamped
            self.edit.blockSignals(True)
            self.edit.setText(str(clamped))
            self.edit.blockSignals(False)
            self.valueChanged.emit(clamped)
            self.textChanged.emit(str(clamped))

    def setRange(self, minimum: int, maximum: int) -> None:
        """허용 최소·최대값 범위 설정 및 현재값 재보정"""
        self._min = minimum
        self._max = maximum
        self._validator.setRange(minimum, maximum)
        self.setValue(self._value)

    def setMinimum(self, minimum: int) -> None:
        """최소 허용값 설정"""
        self.setRange(minimum, self._max)

    def setMaximum(self, maximum: int) -> None:
        """최대 허용값 설정"""
        self.setRange(self._min, maximum)

    def minimum(self) -> int:
        """최소 허용값 반환"""
        return self._min

    def maximum(self) -> int:
        """최대 허용값 반환"""
        return self._max

    def setSingleStep(self, step: int) -> None:
        """1회 증감 단위 설정"""
        self._step = max(int(step), 1)

    def singleStep(self) -> int:
        """1회 증감 단위 반환"""
        return self._step

    def setSuffix(self, suffix: str) -> None:
        """입력값 우측 접미사 텍스트 설정 및 표시 토글"""
        self._suffix = suffix
        if suffix:
            self.suffix_label.setText(suffix)
            self.suffix_label.setVisible(True)
        else:
            self.suffix_label.setVisible(False)

    def suffix(self) -> str:
        """현재 접미사 텍스트 반환"""
        return self._suffix

    def setPrefix(self, prefix: str) -> None:
        """입력값 좌측 접두사 텍스트 설정 및 표시 토글"""
        self._prefix = prefix
        if prefix:
            self.prefix_label.setText(prefix)
            self.prefix_label.setVisible(True)
        else:
            self.prefix_label.setVisible(False)

    def prefix(self) -> str:
        """현재 접두사 텍스트 반환"""
        return self._prefix

    def stepUp(self) -> None:
        """단위(singleStep)만큼 값 증가"""
        self.setValue(self._value + self._step)

    def stepDown(self) -> None:
        """단위(singleStep)만큼 값 감소"""
        self.setValue(self._value - self._step)

    def _on_text_changed(self, text: str) -> None:
        if not text or text == "-":
            return
        try:
            val = int(text)
            clamped = max(self._min, min(val, self._max))
            if self._value != clamped:
                self._value = clamped
                self.valueChanged.emit(clamped)
                self.textChanged.emit(str(clamped))
        except ValueError:
            pass

    def _on_editing_finished(self) -> None:
        text = self.edit.text().strip()
        try:
            val = int(text) if text else self._min
        except ValueError:
            val = self._min
        self.setValue(val)

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        """마우스 휠 스크롤 방향에 따라 값 증감"""
        if event is None:
            return
        if event.angleDelta().y() > 0:
            self.stepUp()
        elif event.angleDelta().y() < 0:
            self.stepDown()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """Up/Down/PageUp/PageDown 키 입력에 따른 값 증감 처리"""
        if event is None:
            return
        key = event.key()
        if key == Qt.Key.Key_Up:
            self.stepUp()
            event.accept()
        elif key == Qt.Key.Key_Down:
            self.stepDown()
            event.accept()
        elif key == Qt.Key.Key_PageUp:
            self.setValue(self._value + self._step * 10)
            event.accept()
        elif key == Qt.Key.Key_PageDown:
            self.setValue(self._value - self._step * 10)
            event.accept()
        else:
            super().keyPressEvent(event)

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 상하 스텝 버튼 아이콘 색상을 갱신"""
        self.btn_up.setIcon(get_icon("chevron-up", palette=palette, size=10))
        self.btn_down.setIcon(get_icon("chevron-down", palette=palette, size=10))
