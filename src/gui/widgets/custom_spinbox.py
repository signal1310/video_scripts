"""직접 입력·방향키·마우스 휠·연속 증감을 지원하는 커스텀 스핀박스 모듈"""

from PyQt6.QtCore import QEvent, QLocale, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QDoubleValidator, QIntValidator, QKeyEvent, QWheelEvent
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

PAGE_STEP_MULTIPLIER: int = 10
AUTO_REPEAT_DELAY_MS: int = 350
AUTO_REPEAT_INTERVAL_MS: int = 60
SPIN_CHEVRON_SIZE: int = 10
OPTIONAL_MIN_BOUND: float = -1_000_000_000.0
OPTIONAL_MAX_BOUND: float = 1_000_000_000.0
_PARTIAL_NUMERIC_TOKENS: frozenset[str] = frozenset({"-", ".", "-.", "+"})


class _BaseCustomSpinBox(QFrame, ThemeAwareMixin):
    """커스텀 스핀박스의 공통 프레임·접두사·접미사·스텝 버튼·키/휠 동작 기반 클래스"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CustomSpinBox")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._suffix: str = ""
        self._prefix: str = ""
        self._build_chrome()
        self.init_theme_listener()

    def _build_chrome(self) -> None:
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(4, 2, 2, 2)
        main_layout.setSpacing(2)

        self.prefix_label = QLabel(self)
        self.prefix_label.setObjectName("SpinPrefix")
        self.prefix_label.setVisible(False)
        main_layout.addWidget(self.prefix_label)

        self.edit = QLineEdit(self)
        self.edit.setObjectName("CustomSpinBoxEdit")
        self.edit.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.edit.installEventFilter(self)
        main_layout.addWidget(self.edit, stretch=1)

        self.suffix_label = QLabel(self)
        self.suffix_label.setObjectName("SpinSuffix")
        self.suffix_label.setVisible(False)
        main_layout.addWidget(self.suffix_label)

        btn_col = QVBoxLayout()
        btn_col.setContentsMargins(0, 0, 0, 0)
        btn_col.setSpacing(1)

        pal = get_active_palette()
        self.btn_up = self._create_step_button("SpinStepUp", "chevron-up", pal)
        self.btn_up.clicked.connect(self.stepUp)
        self.btn_down = self._create_step_button("SpinStepDown", "chevron-down", pal)
        self.btn_down.clicked.connect(self.stepDown)

        btn_col.addWidget(self.btn_up)
        btn_col.addWidget(self.btn_down)
        main_layout.addLayout(btn_col)

    def _create_step_button(
        self, obj_name: str, icon_name: str, palette: ThemePalette
    ) -> QPushButton:
        btn = QPushButton(self)
        btn.setObjectName(obj_name)
        btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        btn.setIcon(get_icon(icon_name, palette=palette, size=SPIN_CHEVRON_SIZE))
        btn.setAutoRepeat(True)
        btn.setAutoRepeatDelay(AUTO_REPEAT_DELAY_MS)
        btn.setAutoRepeatInterval(AUTO_REPEAT_INTERVAL_MS)
        return btn

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
        """단위(singleStep)만큼 값 1단계 증가"""
        self._step_by(1)

    def stepDown(self) -> None:
        """단위(singleStep)만큼 값 1단계 감소"""
        self._step_by(-1)

    def _step_by(self, steps: int) -> None:
        raise NotImplementedError

    def _handle_step_key(self, key: int) -> bool:
        if key == Qt.Key.Key_Up:
            self._step_by(1)
            return True
        if key == Qt.Key.Key_Down:
            self._step_by(-1)
            return True
        if key == Qt.Key.Key_PageUp:
            self._step_by(PAGE_STEP_MULTIPLIER)
            return True
        if key == Qt.Key.Key_PageDown:
            self._step_by(-PAGE_STEP_MULTIPLIER)
            return True
        return False

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """내부 입력란 포커스 상태에서 방향키·PageUp/Down 증감 전달"""
        if (
            a0 is self.edit
            and isinstance(a1, QKeyEvent)
            and a1.type() == QEvent.Type.KeyPress
            and self.isEnabled()
            and self._handle_step_key(a1.key())
        ):
            a1.accept()
            return True
        return super().eventFilter(a0, a1)

    def wheelEvent(self, event: QWheelEvent | None) -> None:
        """마우스 휠 스크롤 방향에 따라 값 증감"""
        if event is None or not self.isEnabled():
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
        if self.isEnabled() and self._handle_step_key(event.key()):
            event.accept()
            return
        super().keyPressEvent(event)

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 상하 스텝 버튼 아이콘 색상을 갱신"""
        self.btn_up.setIcon(get_icon("chevron-up", palette=palette, size=SPIN_CHEVRON_SIZE))
        self.btn_down.setIcon(get_icon("chevron-down", palette=palette, size=SPIN_CHEVRON_SIZE))


class CustomIntegerSpinBox(_BaseCustomSpinBox):
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
        self._min: int = minimum
        self._max: int = maximum
        self._step: int = max(step, 1)
        self._value: int = max(minimum, min(value, maximum))
        super().__init__(parent)

        self._validator = QIntValidator(self._min, self._max, self)
        self.edit.setValidator(self._validator)
        self.edit.textChanged.connect(self._on_text_changed)
        self.edit.editingFinished.connect(self._on_editing_finished)
        self.setValue(self._value)

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

    def _step_by(self, steps: int) -> None:
        self.setValue(self._value + self._step * steps)

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


class OptionalNumberSpinBox(_BaseCustomSpinBox):
    """빈 값(None)과 정수·실수 증감을 함께 지원하는 필터용 커스텀 스핀박스"""

    valueChanged = pyqtSignal(object)
    textEdited = pyqtSignal(str)
    returnPressed = pyqtSignal()

    def __init__(
        self,
        *,
        decimals: int = 2,
        step: float = 1.0,
        minimum: float = OPTIONAL_MIN_BOUND,
        maximum: float = OPTIONAL_MAX_BOUND,
        default_anchor: float | None = None,
        placeholder: str = "",
        parent: QWidget | None = None,
    ) -> None:
        self._decimals: int = max(0, int(decimals))
        self._step: float = step if step > 0 else 1.0
        self._min: float = minimum
        self._max: float = maximum
        self._default_anchor: float | None = default_anchor
        self._value: float | None = None
        super().__init__(parent)

        validator = QDoubleValidator(self._min, self._max, self._decimals, self)
        validator.setLocale(QLocale.c())
        validator.setNotation(QDoubleValidator.Notation.StandardNotation)
        self._validator = validator

        self.edit.setValidator(self._validator)
        self.edit.setClearButtonEnabled(True)
        if placeholder:
            self.edit.setPlaceholderText(placeholder)
        self.edit.textChanged.connect(self._on_text_changed)
        self.edit.textEdited.connect(self.textEdited)
        self.edit.editingFinished.connect(self._on_editing_finished)
        self.edit.returnPressed.connect(self.returnPressed)

    def _normalize_number(self, val: float) -> float:
        clamped = max(self._min, min(float(val), self._max))
        rounded = round(clamped, self._decimals)
        if rounded == 0.0:
            return 0.0
        return float(rounded)

    def _format_number(self, val: float) -> str:
        norm = self._normalize_number(val)
        if self._decimals == 0:
            return str(round(norm))
        text = f"{norm:.{self._decimals}f}".rstrip("0").rstrip(".")
        return "0" if text in {"", "-0"} else text

    def value(self) -> float | None:
        """현재 입력 수치 반환 (빈 입력 시 None)"""
        return self._value

    def setValue(self, val: float | None) -> None:
        """선택적 수치 설정 및 텍스트·시그널 동기화"""
        if val is None:
            changed = self._value is not None or bool(self.edit.text())
            self._value = None
            self.edit.blockSignals(True)
            self.edit.setText("")
            self.edit.blockSignals(False)
            if changed:
                self.valueChanged.emit(None)
            return

        normalized = self._normalize_number(val)
        formatted = self._format_number(normalized)
        changed = self._value != normalized or self.edit.text() != formatted
        self._value = normalized
        self.edit.blockSignals(True)
        self.edit.setText(formatted)
        self.edit.blockSignals(False)
        if changed:
            self.valueChanged.emit(normalized)

    def setDefaultAnchor(self, anchor: float | None) -> None:
        """빈 상태에서 첫 스텝 동작 시 시작 기준값 설정"""
        self._default_anchor = anchor

    def setSingleStep(self, step: float) -> None:
        """1회 증감 단위 설정"""
        self._step = step if step > 0 else 1.0

    def singleStep(self) -> float:
        """1회 증감 단위 반환"""
        return self._step

    def _step_by(self, steps: int) -> None:
        if self._value is None:
            base = self._default_anchor if self._default_anchor is not None else 0.0
            target = base if abs(steps) == 1 else base + self._step * steps
        else:
            target = self._value + self._step * steps
        self.setValue(target)

    def _on_text_changed(self, text: str) -> None:
        stripped = text.strip()
        if not stripped:
            if self._value is not None:
                self._value = None
                self.valueChanged.emit(None)
            return
        if stripped in _PARTIAL_NUMERIC_TOKENS:
            return
        try:
            parsed = float(stripped)
        except ValueError:
            return
        normalized = self._normalize_number(parsed)
        if self._value != normalized:
            self._value = normalized
            self.valueChanged.emit(normalized)

    def _on_editing_finished(self) -> None:
        stripped = self.edit.text().strip()
        if not stripped or stripped in _PARTIAL_NUMERIC_TOKENS:
            self.setValue(None)
            return
        try:
            parsed = float(stripped)
        except ValueError:
            self.setValue(None)
            return
        self.setValue(parsed)
