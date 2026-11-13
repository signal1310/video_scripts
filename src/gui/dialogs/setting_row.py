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

from src.gui.styles.icons import get_active_palette, get_icon
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
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsCard")
        self.key = key
        self.title = title
        self.desc = desc
        self.editor = editor
        self.default_val = default_val
        self.on_reset_custom = on_reset

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        # 1. 좌측 설정 제목 및 툴팁
        self.lbl_title = QLabel(title)
        self.lbl_title.setObjectName("SettingTitle")

        tooltip_lines: list[str] = []
        if desc:
            tooltip_lines.append(desc)
        if default_val is not None and str(default_val) != "":
            tooltip_lines.append(f"• 기본값: {default_val}{(' ' + unit) if unit else ''}")
        if key:
            tooltip_lines.append(f"• 설정 키: {key}")
        full_tooltip = "\n".join(tooltip_lines)

        if full_tooltip:
            self.setToolTip(full_tooltip)
            self.lbl_title.setToolTip(full_tooltip)

        layout.addWidget(self.lbl_title, stretch=1)

        # 2. 우측 입력 컨트롤 및 기본값 복원 버튼
        ctrl_row = QHBoxLayout()
        ctrl_row.setSpacing(8)
        ctrl_row.addWidget(editor)

        self.btn_reset = QPushButton()
        sp = self.btn_reset.sizePolicy()
        sp.setRetainSizeWhenHidden(True)
        self.btn_reset.setSizePolicy(sp)
        self.btn_reset.setIcon(
            get_icon("rotate-ccw", palette=get_active_palette(), size=14)
        )
        self.btn_reset.setObjectName("ResetButton")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.setToolTip(f"이 항목을 기본값({default_val})으로 복원합니다.")
        self.btn_reset.setFixedSize(28, 28)
        self.btn_reset.clicked.connect(self.reset_to_default)
        ctrl_row.addWidget(self.btn_reset)

        layout.addLayout(ctrl_row)

        self._connect_editor_signals()
        self._update_reset_button_state()
        self.init_theme_listener()

    def _resolve_line_edit(self) -> QLineEdit | None:
        """직접 QLineEdit이거나 내부에 QLineEdit을 포함한 에디터에서 텍스트 위젯을 반환"""
        if isinstance(self.editor, QLineEdit):
            return self.editor
        return self.editor.findChild(QLineEdit)

    def _connect_editor_signals(self) -> None:
        """에디터 위젯 타입에 따라 리셋 버튼 상태 동기화 및 저장 시그널을 연결"""
        if isinstance(self.editor, (QSpinBox, CustomIntegerSpinBox, QDoubleSpinBox)):
            self.editor.valueChanged.connect(self._on_editor_changed)
            return
        if isinstance(self.editor, QComboBox):
            self.editor.currentIndexChanged.connect(self._on_editor_changed)
            return
        line_edit = self._resolve_line_edit()
        if line_edit is not None:
            line_edit.textChanged.connect(self._on_text_changed)
            line_edit.editingFinished.connect(self._on_editor_changed)

    def is_default_value(self) -> bool:
        """현재 입력값이 기본값과 일치하는지 여부를 확인"""
        if self.default_val is None:
            return True
        if isinstance(self.editor, (QSpinBox, CustomIntegerSpinBox)):
            try:
                return int(self.editor.value()) == int(self.default_val)
            except Exception:
                return False
        if isinstance(self.editor, QDoubleSpinBox):
            try:
                return abs(float(self.editor.value()) - float(self.default_val)) < 1e-4
            except Exception:
                return False
        if isinstance(self.editor, QComboBox):
            return str(self.editor.currentData()) == str(self.default_val)
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
        self.btn_reset.setIcon(
            get_icon("rotate-ccw", palette=palette, size=14)
        )

    def reset_to_default(self) -> None:
        """해당 항목의 입력 필드를 기본값으로 복원"""
        if self.on_reset_custom is not None:
            self.on_reset_custom()
        elif isinstance(self.editor, (QSpinBox, CustomIntegerSpinBox)):
            self.editor.setValue(int(self.default_val))
        elif isinstance(self.editor, QDoubleSpinBox):
            self.editor.setValue(float(self.default_val))
        elif isinstance(self.editor, QLineEdit):
            self.editor.setText(str(self.default_val))
        elif isinstance(self.editor, QComboBox):
            idx = self.editor.findData(self.default_val)
            if idx >= 0:
                self.editor.setCurrentIndex(idx)

        self._update_reset_button_state()
        if isinstance(self.editor, QLineEdit) or self.on_reset_custom is not None:
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


def _resolve_field_default(section_cls: type[Any], field_name: str) -> Any:
    for f in fields(section_cls):
        if f.name == field_name:
            if f.default is not MISSING:
                return f.default
            if f.default_factory is not MISSING:
                return f.default_factory()
            break
    raise KeyError(f"{section_cls.__name__}.{field_name} 기본값을 찾을 수 없습니다")


def _resolve_field_bounds(
    section_cls: type[Any], field_name: str
) -> tuple[float, float]:
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
    default_val = int(_resolve_field_default(section_cls, field_name))
    min_val, max_val = _resolve_field_bounds(section_cls, field_name)
    spin = CustomIntegerSpinBox(
        minimum=int(min_val),
        maximum=int(max_val),
        step=step,
        value=default_val,
    )
    if unit:
        spin.setSuffix(f" {unit}")
    spin.setFixedWidth(140)
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
    default_val = float(_resolve_field_default(section_cls, field_name))
    min_val, max_val = _resolve_field_bounds(section_cls, field_name)
    spin = QDoubleSpinBox()
    spin.setRange(min_val, max_val)
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
    if unit:
        spin.setSuffix(f" {unit}")
    spin.setFixedWidth(140)
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
    width: int = 240,
    *,
    row_key: str | None = None,
) -> QLineEdit:
    """데이터클래스 메타데이터 기본값으로 문자열 QLineEdit SettingRow를 생성·등록"""
    default_val = str(_resolve_field_default(section_cls, field_name))
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
