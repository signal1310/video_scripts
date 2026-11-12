from typing import Any, Callable, Dict, Optional, Tuple, Union
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

from src.core.config import DEFAULT_CONFIGS
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox


class SettingRow(QFrame, ThemeAwareMixin):
    """
    - 단일 설정 항목을 나타내는 행 위젯.
    """

    value_changed = pyqtSignal()

    def __init__(
        self,
        key: str,
        title: str,
        desc: str,
        editor: QWidget,
        default_val: Any,
        unit: str = "",
        on_reset: Optional[Callable[[], None]] = None,
        parent: Optional[QWidget] = None,
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

        tooltip_lines = []
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
        self.btn_reset.setToolTip(f"이 항목을 기본값({default_val})으로 복원")
        self.btn_reset.setFixedSize(28, 28)
        self.btn_reset.clicked.connect(self.reset_to_default)
        ctrl_row.addWidget(self.btn_reset)

        layout.addLayout(ctrl_row)

        if isinstance(editor, (QSpinBox, CustomIntegerSpinBox, QDoubleSpinBox)):
            editor.valueChanged.connect(self._on_editor_changed)
        elif isinstance(editor, QLineEdit):
            editor.textChanged.connect(self._on_editor_changed)
        elif isinstance(editor, QComboBox):
            editor.currentIndexChanged.connect(self._on_editor_changed)
        else:
            child_edit = editor.findChild(QLineEdit)
            if child_edit:
                child_edit.textChanged.connect(self._on_editor_changed)

        self._update_reset_button_state()
        self.init_theme_listener()

    def is_default_value(self) -> bool:
        """
        - 현재 입력값이 기본값과 일치하는지 여부를 확인.
        """
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
        if isinstance(self.editor, QLineEdit):
            return self.editor.text().strip() == str(self.default_val).strip()
        if isinstance(self.editor, QComboBox):
            return str(self.editor.currentData()) == str(self.default_val)
        child_edit = self.editor.findChild(QLineEdit)
        if child_edit:
            return child_edit.text().strip() == str(self.default_val).strip()
        return False

    def _update_reset_button_state(self) -> None:
        """
        - 현재 값이 기본값과 다르면 리셋 버튼을 표시하고, 기본값이면 숨김 처리.
        """
        is_default = self.is_default_value()
        self.btn_reset.setVisible(not is_default)
        self.btn_reset.setEnabled(not is_default)

    def _on_editor_changed(self, *args: Any) -> None:
        self._update_reset_button_state()
        self.value_changed.emit()

    def apply_theme(self, palette: ThemePalette) -> None:
        """
        - 활성 테마 팔레트에 맞춰 리셋 버튼 아이콘을 갱신.
        """
        self.btn_reset.setIcon(
            get_icon("rotate-ccw", palette=palette, size=14)
        )

    def reset_to_default(self) -> None:
        """
        - 해당 항목의 입력 필드를 기본값으로 복원.
        """
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

    def get_env_value(self) -> str:
        """
        - 현재 에디터 위젯의 값을 .env 저장용 문자열로 직렬화하여 반환.
        """
        if isinstance(self.editor, (QSpinBox, CustomIntegerSpinBox)):
            return str(self.editor.value())
        if isinstance(self.editor, QDoubleSpinBox):
            dec = self.editor.decimals()
            return f"{self.editor.value():.{dec}f}"
        if isinstance(self.editor, QLineEdit):
            return self.editor.text().strip() or str(self.default_val)
        return str(self.default_val)


def make_int_spin_row(
    rows: Dict[str, SettingRow],
    layout: QVBoxLayout,
    key: str,
    title: str,
    desc: str,
    min_val: int,
    max_val: int,
    step: int,
    unit: str = "",
) -> CustomIntegerSpinBox:
    """
    - 접미사 단위가 내장된 CustomIntegerSpinBox와 SettingRow를 생성하여 레이아웃 및 사전에 등록.
    """
    spin = CustomIntegerSpinBox(
        minimum=min_val,
        maximum=max_val,
        step=step,
        value=int(DEFAULT_CONFIGS[key]),
    )
    if unit:
        spin.setSuffix(f" {unit}")
    spin.setFixedWidth(140)

    row = SettingRow(
        key=key,
        title=title,
        desc=desc,
        editor=spin,
        default_val=int(DEFAULT_CONFIGS[key]),
        unit=unit,
    )
    rows[key] = row
    if hasattr(layout, "add_row"):
        layout.add_row(row)
    else:
        layout.addWidget(row)
    return spin


def make_float_spin_row(
    rows: Dict[str, SettingRow],
    layout: QVBoxLayout,
    key: str,
    title: str,
    desc: str,
    min_val: float,
    max_val: float,
    step: float,
    decimals: int = 2,
    unit: str = "",
) -> QDoubleSpinBox:
    """
    - 접미사 단위가 내장된 실수형 QDoubleSpinBox와 SettingRow를 생성하여 레이아웃 및 사전에 등록.
    """
    spin = QDoubleSpinBox()
    spin.setRange(min_val, max_val)
    spin.setSingleStep(step)
    spin.setDecimals(decimals)
    if unit:
        spin.setSuffix(f" {unit}")
    spin.setFixedWidth(140)

    row = SettingRow(
        key=key,
        title=title,
        desc=desc,
        editor=spin,
        default_val=float(DEFAULT_CONFIGS[key]),
        unit=unit,
    )
    rows[key] = row
    if hasattr(layout, "add_row"):
        layout.add_row(row)
    else:
        layout.addWidget(row)
    return spin


def make_text_row(
    rows: Dict[str, SettingRow],
    layout: QVBoxLayout,
    key: str,
    title: str,
    desc: str,
    width: int = 240,
) -> QLineEdit:
    """
    - 문자열 QLineEdit와 SettingRow를 생성하여 레이아웃 및 사전에 등록.
    """
    edit = QLineEdit()
    edit.setFixedWidth(width)
    row = SettingRow(
        key=key,
        title=title,
        desc=desc,
        editor=edit,
        default_val=str(DEFAULT_CONFIGS[key]),
    )
    rows[key] = row
    if hasattr(layout, "add_row"):
        layout.add_row(row)
    else:
        layout.addWidget(row)
    return edit
