"""환경설정 다이얼로그 내 단일 설정 행과 값 위젯 조립 모듈"""

from collections.abc import Callable
from dataclasses import MISSING, fields
from typing import Any, Protocol

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.gui.dialogs.setting_controls import (
    ScrollSafeComboBox,
    ScrollSafeDoubleSpinBox,
    apply_row_reset_icon,
    build_row_reset_button,
    format_setting_tooltip,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox


class SettingRow(QFrame, ThemeAwareMixin):
    """단일 설정 항목을 나타내는 행 위젯"""

    value_changed = pyqtSignal()

    def __init__(
        self,
        key: str,
        title: str,
        desc: str,
        editor: QWidget,
        default_val: Any,
        unit: str = "",
        on_reset: Callable[[], None] | None = None,
        value_widget: QWidget | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsCard")
        self.key = key
        self.title = title
        self.desc = desc
        self.editor = editor
        self._value_widget: QWidget = value_widget if value_widget is not None else editor
        self.default_val = default_val
        self.on_reset_custom = on_reset

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(10, 5, 8, 5)
        outer_layout.setSpacing(3)

        top_layout = QHBoxLayout()
        top_layout.setContentsMargins(0, 0, 0, 0)
        top_layout.setSpacing(6)

        # 1. 좌측 설정 제목 및 툴팁
        self.lbl_title = QLabel(title, self)
        self.lbl_title.setObjectName("SettingTitle")

        default_text = (
            f"{default_val}{(' ' + unit) if unit else ''}"
            if default_val is not None and str(default_val) != ""
            else ""
        )
        full_tooltip = format_setting_tooltip(
            desc,
            default_text,
            setting_key=key,
        )

        if full_tooltip:
            self.setToolTip(full_tooltip)
            self.lbl_title.setToolTip(full_tooltip)

        top_layout.addWidget(self.lbl_title, stretch=1)

        # 2. 우측 입력 컨트롤 및 기본값 복원 버튼
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(4)
        ctrl_row.addWidget(editor)

        self.btn_reset: QPushButton = build_row_reset_button(
            tooltip=f"이 항목을 기본값({default_val})으로 복원합니다.",
            on_clicked=self.reset_to_default,
            parent=self,
        )
        ctrl_row.addWidget(self.btn_reset)

        top_layout.addLayout(ctrl_row)
        outer_layout.addLayout(top_layout)

        self.lbl_hint = QLabel(desc, self)
        self.lbl_hint.setObjectName("SettingDesc")
        self.lbl_hint.setWordWrap(True)
        outer_layout.addWidget(self.lbl_hint)
        self.lbl_hint.setVisible(bool(desc))

        self._connect_editor_signals()
        self._update_reset_button_state()
        self.init_theme_listener()

    def set_hint_visible(self, visible: bool) -> None:
        """인라인 설정 설명 라벨 표시 여부를 전환"""
        self.lbl_hint.setVisible(bool(visible and self.desc))

    def _resolve_line_edit(self) -> QLineEdit | None:
        """직접 QLineEdit이거나 내부에 QLineEdit을 포함한 에디터에서 텍스트 위젯을 반환"""
        target = self._value_widget
        if isinstance(target, QLineEdit):
            return target
        if isinstance(target, (QSpinBox, CustomIntegerSpinBox, QDoubleSpinBox, QComboBox)):
            return None
        return target.findChild(QLineEdit)

    def _connect_editor_signals(self) -> None:
        """에디터 위젯 타입에 따라 리셋 버튼 상태 동기화 및 저장 시그널을 연결"""
        target = self._value_widget
        if isinstance(target, (QSpinBox, CustomIntegerSpinBox, QDoubleSpinBox)):
            target.valueChanged.connect(self._on_editor_changed)
            return
        if isinstance(target, QComboBox):
            target.currentIndexChanged.connect(self._on_editor_changed)
            return
        line_edit = self._resolve_line_edit()
        if line_edit is not None:
            line_edit.textChanged.connect(self._on_text_changed)
            line_edit.editingFinished.connect(self._on_editor_changed)

    def is_default_value(self) -> bool:
        """현재 입력값이 기본값과 일치하는지 여부를 확인"""
        if self.default_val is None:
            return True
        target = self._value_widget
        if isinstance(target, (QSpinBox, CustomIntegerSpinBox)):
            try:
                return int(target.value()) == int(self.default_val)
            except Exception:
                return False
        if isinstance(target, QDoubleSpinBox):
            try:
                return abs(float(target.value()) - float(self.default_val)) < 1e-4
            except Exception:
                return False
        if isinstance(target, QComboBox):
            return str(target.currentData()) == str(self.default_val)
        line_edit = self._resolve_line_edit()
        if line_edit is not None:
            return line_edit.text().strip() == str(self.default_val).strip()
        return False

    def _update_reset_button_state(self) -> None:
        """현재 값이 기본값과 다르면 리셋 버튼을 표시하고, 기본값이면 숨김 처리"""
        is_default = self.is_default_value()
        self.btn_reset.setVisible(not is_default)
        self.btn_reset.setEnabled(not is_default)

    def _on_text_changed(self, _text: str = "") -> None:
        """텍스트 입력 중 기본값 복원 버튼의 표시 상태만 즉시 갱신"""
        self._update_reset_button_state()

    def _on_editor_changed(self, *args: Any) -> None:
        self._update_reset_button_state()
        self.value_changed.emit()

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 리셋 버튼 아이콘을 갱신"""
        apply_row_reset_icon(self.btn_reset, palette)

    def reset_to_default(self) -> None:
        """해당 항목의 입력 필드를 기본값으로 복원"""
        target = self._value_widget
        if self.on_reset_custom is not None:
            self.on_reset_custom()
        elif isinstance(target, (QSpinBox, CustomIntegerSpinBox)):
            target.setValue(int(self.default_val))
        elif isinstance(target, QDoubleSpinBox):
            target.setValue(float(self.default_val))
        elif isinstance(target, QLineEdit):
            target.setText(str(self.default_val))
        elif isinstance(target, QComboBox):
            idx = target.findData(self.default_val)
            if idx >= 0:
                target.setCurrentIndex(idx)

        self._update_reset_button_state()
        if isinstance(target, QLineEdit) or self.on_reset_custom is not None:
            self.value_changed.emit()


class _RowContainer(Protocol):
    def add_row(self, row_widget: QWidget) -> None: ...


def _register_setting_row(
    rows: dict[str, SettingRow],
    container: QVBoxLayout | _RowContainer,
    key: str,
    title: str,
    desc: str,
    editor: QWidget,
    default_val: Any,
    unit: str = "",
) -> None:
    """SettingRow를 조립하여 설정 사전과 부모 카드/레이아웃에 등록"""
    row = SettingRow(
        key=key,
        title=title,
        desc=desc,
        editor=editor,
        default_val=default_val,
        unit=unit,
    )
    rows[key] = row
    if isinstance(container, QVBoxLayout):
        container.addWidget(row)
    else:
        container.add_row(row)


def resolve_field_default(section_cls: type[Any], field_name: str) -> Any:
    """데이터클래스 필드의 기본값 메타데이터를 조회해 반환"""
    for f in fields(section_cls):
        if f.name == field_name:
            if f.default is not MISSING:
                return f.default
            if f.default_factory is not MISSING:
                return f.default_factory()
            break
    raise KeyError(f"{section_cls.__name__}.{field_name} 기본값을 찾을 수 없습니다")


def resolve_field_bounds(
    section_cls: type[Any], field_name: str
) -> tuple[float, float]:
    """데이터클래스 필드의 허용 범위(bounds) 메타데이터를 조회해 반환"""
    for f in fields(section_cls):
        if f.name == field_name:
            bounds = f.metadata.get("bounds")
            if isinstance(bounds, tuple) and len(bounds) == 2:
                return (float(bounds[0]), float(bounds[1]))
            break
    raise KeyError(f"{section_cls.__name__}.{field_name} 범위 메타데이터가 없습니다")


def make_int_spin_row(
    rows: dict[str, SettingRow],
    layout: QVBoxLayout | _RowContainer,
    section_cls: type[Any],
    field_name: str,
    title: str,
    desc: str,
    step: int,
    unit: str = "",
    *,
    row_key: str | None = None,
) -> CustomIntegerSpinBox:
    """데이터클래스 메타데이터(기본값·범위)로 정수형 스핀박스 SettingRow를 생성·등록"""
    default_val = int(resolve_field_default(section_cls, field_name))
    min_val, max_val = resolve_field_bounds(section_cls, field_name)
    spin = CustomIntegerSpinBox(
        minimum=int(min_val),
        maximum=int(max_val),
        step=step,
        value=default_val,
        wheel_requires_focus=True,
    )
    if unit:
        spin.setSuffix(f" {unit}")
    spin.setFixedWidth(108)
    _register_setting_row(
        rows,
        layout,
        row_key or field_name,
        title,
        desc,
        spin,
        default_val,
        unit=unit,
    )
    return spin


def make_float_spin_row(
    rows: dict[str, SettingRow],
    layout: QVBoxLayout | _RowContainer,
    section_cls: type[Any],
    field_name: str,
    title: str,
    desc: str,
    step: float,
    decimals: int = 2,
    unit: str = "",
    *,
    row_key: str | None = None,
) -> QDoubleSpinBox:
    """데이터클래스 메타데이터(기본값·범위)로 실수형 스핀박스 SettingRow를 생성·등록"""
    default_val = float(resolve_field_default(section_cls, field_name))
    min_val, max_val = resolve_field_bounds(section_cls, field_name)
    spin = ScrollSafeDoubleSpinBox()
    spin.setRange(min_val, max_val)
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if unit:
        spin.setSuffix(f" {unit}")
    spin.setFixedWidth(108)
    _register_setting_row(
        rows,
        layout,
        row_key or field_name,
        title,
        desc,
        spin,
        default_val,
        unit=unit,
    )
    return spin


def make_text_row(
    rows: dict[str, SettingRow],
    layout: QVBoxLayout | _RowContainer,
    section_cls: type[Any],
    field_name: str,
    title: str,
    desc: str,
    width: int = 172,
    *,
    row_key: str | None = None,
) -> QLineEdit:
    """데이터클래스 메타데이터 기본값으로 문자열 QLineEdit SettingRow를 생성·등록"""
    default_val = str(resolve_field_default(section_cls, field_name))
    edit = QLineEdit()
    edit.setFixedWidth(width)
    _register_setting_row(
        rows,
        layout,
        row_key or field_name,
        title,
        desc,
        edit,
        default_val,
    )
    return edit


def make_combo_row(
    rows: dict[str, SettingRow],
    layout: QVBoxLayout | _RowContainer,
    section_cls: type[Any],
    field_name: str,
    title: str,
    desc: str,
    choices: tuple[tuple[str, Any], ...],
    *,
    row_key: str | None = None,
) -> QComboBox:
    """데이터클래스 메타데이터 기본값으로 선택형 QComboBox SettingRow를 생성·등록"""
    default_val = resolve_field_default(section_cls, field_name)
    combo = ScrollSafeComboBox()
    for label, val in choices:
        combo.addItem(label, val)
    combo.setFixedWidth(max(145, combo.sizeHint().width() + 10))
    _register_setting_row(
        rows,
        layout,
        row_key or field_name,
        title,
        desc,
        combo,
        default_val,
    )
    return combo
