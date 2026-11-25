"""판별 기준 튜너 한 행의 슬라이더, 스핀박스, 초기화 버튼과 실시간 예시 문구 모듈"""

import math
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.config import (
    SAVINGS_BASIS_ENCODE,
    ClassifierConfig,
)
from src.core.messages import format_low_bitrate_desc, format_over_bitrate_desc
from src.gui.settings.setting_controls import (
    NoWheelSlider,
    ScrollSafeDoubleSpinBox,
    apply_row_reset_icon,
    build_row_reset_button,
    format_setting_tooltip,
)
from src.gui.settings.setting_row import resolve_field_bounds, resolve_field_default
from src.gui.styles.icons import get_active_palette
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

_SPIN_WIDTH: int = 96
_FLOAT_TOL: float = 1e-4
_EXAMPLE_MINUTES_REF: int = 10


def _format_tuner_example(key: str, val: float, config: ClassifierConfig) -> str:
    if key == "over_bitrate_ratio":
        return f"현재 기준 예시: {format_over_bitrate_desc(config, val)} (초과 시 과화질)"
    if key == "low_bitrate_ratio":
        return f"현재 기준 예시: {format_low_bitrate_desc(config, val)} (이하 시 저화질)"
    if key == "keyframe_interval_sec":
        return f"현재 기준: 키프레임 간격 {val:.1f}초 초과 시 인코딩 대상"
    if key == "min_saved_mb":
        return f"현재 기준: 예상 절약 {val:.1f} MB 미만 파일은 원본 보존"
    if key == "min_mb_per_min":
        if config.savings.selection_basis == SAVINGS_BASIS_ENCODE:
            return f"현재 기준(예상 인코딩 시간 기준): 예상 인코딩 시간 1분당 {val:.1f} MB 이상 절약해야 인코딩"
        return (
            f"현재 기준(재생 시간 기준): {_EXAMPLE_MINUTES_REF}분 영상 기준 "
            f"{val * _EXAMPLE_MINUTES_REF:.1f} MB 이상 줄어야 인코딩"
        )
    if key == "exempt_saved_mb":
        if val <= 0.0:
            return "현재 기준: 예외 미사용 (분당 최소 절약량 기준을 그대로 적용)"
        return f"현재 기준: 총 {val:.1f} MB 이상 절약 시 분당 절약 무관하게 포함"
    return ""


@dataclass(frozen=True, slots=True)
class TunerRowSpec:
    """슬라이더, 스핀박스 연동 튜너 행의 메타데이터 및 범위 명세"""

    key: str
    title: str
    desc: str
    hint: str
    section_cls: type[Any]
    field_name: str
    setting_key: str
    step: float
    decimals: int
    unit: str
    slider_scale: int
    optional_label: str = ""
    optional_active_min: float | None = None


class TunerSliderRow(QFrame, ThemeAwareMixin):
    """QSlider, QDoubleSpinBox, 개별 초기화 버튼으로 구성된 튜너 행 위젯"""

    value_committed = pyqtSignal(bool)

    def __init__(
        self,
        spec: TunerRowSpec,
        config: ClassifierConfig | None = None,
        *,
        show_hint: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsCard")
        self.spec = spec
        self._config: ClassifierConfig = config or ClassifierConfig()
        self._show_hint: bool = show_hint
        self.default_val: float = float(
            resolve_field_default(spec.section_cls, spec.field_name)
        )
        self._min_val, self._max_val = resolve_field_bounds(
            spec.section_cls, spec.field_name
        )
        self._slider_min: float = (
            spec.optional_active_min
            if spec.optional_active_min is not None
            else self._min_val
        )
        self._last_active_val: float = (
            self.default_val if self.default_val > 0 else self._slider_min
        )
        self._syncing: bool = False
        self._busy: bool = False

        self._init_ui()
        self.init_theme_listener()
        self.set_value(self.default_val)

    def _init_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 5, 8, 5)
        root.setSpacing(4)

        tooltip_desc = (
            f"{self.spec.desc}\n{self.spec.hint}"
            if self.spec.hint
            else self.spec.desc
        )
        tooltip_text = format_setting_tooltip(
            tooltip_desc,
            self._format_value(self.default_val),
            bounds_text=f"{self._format_value(self._min_val)} ~ {self._format_value(self._max_val)}",
            setting_key=self.spec.setting_key,
        )
        self.setToolTip(tooltip_text)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        top_row.setSpacing(6)

        self.lbl_title = QLabel(self.spec.title)
        self.lbl_title.setObjectName("SettingTitle")
        self.lbl_title.setToolTip(tooltip_text)
        top_row.addWidget(self.lbl_title)
        top_row.addStretch(1)

        self.chk_enable: QCheckBox | None = None
        if self.spec.optional_label:
            chk = QCheckBox(self.spec.optional_label)
            chk.setChecked(self.default_val > 0)
            chk.toggled.connect(self._on_check_toggled)
            top_row.addWidget(chk)
            self.chk_enable = chk

        self.spin = ScrollSafeDoubleSpinBox()
        self.spin.setRange(self._min_val, self._max_val)
        self.spin.setSingleStep(self.spec.step)
        self.spin.setDecimals(self.spec.decimals)
        self.spin.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        if self.spec.unit:
            self.spin.setSuffix(f" {self.spec.unit}")
        self.spin.setFixedWidth(_SPIN_WIDTH)
        self.spin.valueChanged.connect(self._on_spin_changed)
        top_row.addWidget(self.spin)

        # - 기본값일 때 숨겨지더라도 스핀박스 가로 위치가 흔들리지 않도록 공간 유지
        self.btn_reset: QPushButton = build_row_reset_button(
            tooltip=f"이 항목을 기본값({self._format_value(self.default_val)})으로 복원합니다.",
            on_clicked=self.reset_to_default,
        )
        top_row.addWidget(self.btn_reset)
        root.addLayout(top_row)

        self.slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.slider.setRange(
            self._to_slider_pos(self._slider_min),
            self._to_slider_pos(self._max_val),
        )
        self.slider.setSingleStep(max(1, round(self.spec.step * self.spec.slider_scale)))
        self.slider.valueChanged.connect(self._on_slider_changed)
        self.slider.sliderReleased.connect(self._on_slider_released)
        root.addWidget(self.slider)

        self.lbl_hint = QLabel(self.spec.hint)
        self.lbl_hint.setObjectName("TunerHintLabel")
        self.lbl_hint.setWordWrap(True)
        root.addWidget(self.lbl_hint)

        self.lbl_example = QLabel("")
        self.lbl_example.setObjectName("HintExampleLabel")
        self.lbl_example.setWordWrap(True)
        root.addWidget(self.lbl_example)

        self.set_hint_visible(self._show_hint)
        self.apply_theme(get_active_palette())

    def set_hint_visible(self, visible: bool) -> None:
        """쉬운 설명, 실시간 적용 예시 라벨의 표시 여부를 전환"""
        self._show_hint = visible
        self.lbl_hint.setVisible(bool(visible and self.spec.hint))
        self.lbl_example.setVisible(bool(visible and self.lbl_example.text()))

    @property
    def key(self) -> str:
        """저장 파일과 같은 설정 키 반환"""
        return self.spec.setting_key

    @property
    def title(self) -> str:
        """행 제목 반환"""
        return self.spec.title

    @property
    def desc(self) -> str:
        """행 설명과 조정 방향 안내를 합친 문장 반환"""
        return f"{self.spec.desc} {self.spec.hint}".strip()

    @property
    def value_widget(self) -> ScrollSafeDoubleSpinBox:
        """값을 읽고 쓰는 스핀박스 반환(설정 행 공용 인터페이스)"""
        return self.spin

    @property
    def editor(self) -> ScrollSafeDoubleSpinBox:
        """행 편집 위젯 반환(설정 행 공용 인터페이스)"""
        return self.spin

    def set_config(self, config: ClassifierConfig) -> None:
        """기준 비트레이트 모델 갱신 및 실시간 예시 라벨 재계산"""
        self._config = config
        self._update_example_text()

    def _update_example_text(self) -> None:
        text = _format_tuner_example(self.spec.key, self.value(), self._config)
        self.lbl_example.setText(text)
        self.lbl_example.setVisible(bool(self._show_hint and text))

    def _format_value(self, value: float) -> str:
        unit = f" {self.spec.unit}" if self.spec.unit else ""
        return f"{value:.{self.spec.decimals}f}{unit}"

    def _to_slider_pos(self, value: float) -> int:
        clamped = max(self._slider_min, min(self._max_val, value))
        return round(clamped * self.spec.slider_scale)

    def _from_slider_pos(self, pos: int) -> float:
        return round(pos / self.spec.slider_scale, self.spec.decimals)

    def value(self) -> float:
        """현재 적용 중인 파라미터 값 반환"""
        if self.chk_enable is not None and not self.chk_enable.isChecked():
            return 0.0
        return round(float(self.spin.value()), self.spec.decimals)

    def is_default_value(self) -> bool:
        """현재 값이 데이터클래스 기본값과 일치하는지 여부 반환"""
        return math.isclose(self.value(), self.default_val, abs_tol=_FLOAT_TOL)

    def set_value(self, value: float) -> None:
        """외부 설정 동기화를 위해 시그널 방출 없이 슬라이더, 스핀박스 값 반영"""
        self._syncing = True
        try:
            clamped = max(self._min_val, min(self._max_val, float(value)))
            if self.chk_enable is not None:
                enabled = clamped > 0.0
                self.chk_enable.setChecked(enabled)
                if enabled:
                    self._last_active_val = max(self._slider_min, clamped)
                display_val = self._last_active_val if not enabled else clamped
                self.spin.setValue(display_val)
                self.slider.setValue(self._to_slider_pos(display_val))
            else:
                self.spin.setValue(clamped)
                self.slider.setValue(self._to_slider_pos(clamped))
            self._refresh_control_states()
        finally:
            self._syncing = False

    def set_busy(self, busy: bool) -> None:
        """작업 실행 여부에 따라 슬라이더, 스핀박스, 초기화 버튼 잠금 상태 갱신"""
        self._busy = busy
        self._refresh_control_states()

    def reset_to_default(self) -> None:
        """해당 행의 값을 데이터클래스 기본값으로 복원하고 즉시 변경 신호 방출"""
        self.set_value(self.default_val)
        self.value_committed.emit(True)

    def _refresh_control_states(self) -> None:
        opt_enabled = self.chk_enable is None or self.chk_enable.isChecked()
        editable = (not self._busy) and opt_enabled
        if self.chk_enable is not None:
            self.chk_enable.setEnabled(not self._busy)
        self.spin.setEnabled(editable)
        self.slider.setEnabled(editable)
        show_reset = not self.is_default_value()
        self.btn_reset.setVisible(show_reset)
        self.btn_reset.setEnabled(show_reset and not self._busy)
        self._update_example_text()

    def _on_check_toggled(self, checked: bool) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            if checked:
                restore = (
                    self._last_active_val
                    if self._last_active_val >= self._slider_min
                    else self.default_val
                )
                self.spin.setValue(restore)
                self.slider.setValue(self._to_slider_pos(restore))
            else:
                curr = float(self.spin.value())
                if curr >= self._slider_min:
                    self._last_active_val = curr
            self._refresh_control_states()
        finally:
            self._syncing = False
        self.value_committed.emit(True)

    def _on_slider_changed(self, pos: int) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            val = self._from_slider_pos(pos)
            self.spin.setValue(val)
            if val >= self._slider_min:
                self._last_active_val = val
            self._refresh_control_states()
        finally:
            self._syncing = False
        self.value_committed.emit(not self.slider.isSliderDown())

    def _on_slider_released(self) -> None:
        if not self._syncing:
            self.value_committed.emit(True)

    def _on_spin_changed(self, val: float) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            if self.chk_enable is not None and val == 0.0:
                self.chk_enable.setChecked(False)
            elif self.chk_enable is not None and not self.chk_enable.isChecked():
                self.chk_enable.setChecked(True)
            if val >= self._slider_min:
                self._last_active_val = float(val)
            self.slider.setValue(self._to_slider_pos(val))
            self._refresh_control_states()
        finally:
            self._syncing = False
        self.value_committed.emit(True)

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 개별 초기화 버튼 아이콘 색상 갱신"""
        apply_row_reset_icon(self.btn_reset, palette)
