"""판별 기준(1차 화질·키프레임, 2차 실익·효율) 실시간 조정 탭 패널 모듈"""

import math
from dataclasses import dataclass, replace
from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from src.core.config import (
    DEFAULT_BITRATE_MODEL,
    DEFAULT_EFFICIENCY,
    DEFAULT_THRESHOLDS,
    BitrateModelConfig,
    EfficiencyConfig,
    ThresholdsConfig,
)
from src.core.messages import format_low_bitrate_desc, format_over_bitrate_desc
from src.gui.dialogs.setting_controls import (
    NoWheelSlider,
    ScrollSafeDoubleSpinBox,
    apply_inline_reset_button_icon,
    apply_row_reset_icon,
    build_row_reset_button,
    build_scroll_tab_shell,
    format_setting_tooltip,
)
from src.gui.dialogs.setting_row import resolve_field_bounds, resolve_field_default
from src.gui.styles.icons import get_active_palette
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.gui.widgets.settings_group_card import SettingsGroupCard

_TUNER_DEBOUNCE_MS: int = 120
_SPIN_WIDTH: int = 96
_FLOAT_TOL: float = 1e-4
_EXAMPLE_MINUTES_REF: int = 10


def _format_tuner_example(
    key: str, val: float, bitrate_model: BitrateModelConfig
) -> str:
    if key == "over_bitrate_ratio":
        return f"현재 기준 예시: {format_over_bitrate_desc(bitrate_model, val)} (초과 시 과화질)"
    if key == "low_bitrate_ratio":
        return f"현재 기준 예시: {format_low_bitrate_desc(bitrate_model, val)} (이하 시 저화질)"
    if key == "keyframe_interval_sec":
        return f"현재 기준: 키프레임 간격 {val:.1f}초 초과 시 탐색 교정 대상"
    if key == "min_saved_mb":
        return f"현재 기준: 예상 절감 {val:.1f} MB 미만 파일은 원본 유지"
    if key == "min_mb_per_min":
        return (
            f"현재 기준: {_EXAMPLE_MINUTES_REF}분 영상 기준 "
            f"{val * _EXAMPLE_MINUTES_REF:.1f} MB 이상 줄어야 인코딩"
        )
    if key == "exempt_saved_mb":
        if val <= 0.0:
            return "현재 기준: 예외 미사용 (분당 최소 절감량 기준을 그대로 적용)"
        return f"현재 기준: 총 {val:.1f} MB 이상 절감 시 분당 효율 무관하게 포함"
    return ""


@dataclass(frozen=True, slots=True)
class _TunerRowSpec:
    """슬라이더·스핀박스 연동 튜너 행의 메타데이터 및 범위 명세"""

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


_QUALITY_SPECS: tuple[_TunerRowSpec, ...] = (
    _TunerRowSpec(
        key="over_bitrate_ratio",
        title="과화질 상한 배수",
        desc="기준(최적 BR)보다 이 배수 이상 비트레이트가 높은 영상을 과화질(용량 절감 대상)로 판정합니다.",
        hint="의도: 눈으로 구분하기 힘든 낭비 용량만 줄입니다. 낮출수록 더 많이 압축되고, 높일수록 기준이 엄격해집니다.",
        section_cls=ThresholdsConfig,
        field_name="over_bitrate_ratio",
        setting_key="analysis.thresholds.over_bitrate_ratio",
        step=0.05,
        decimals=2,
        unit="x",
        slider_scale=100,
    ),
    _TunerRowSpec(
        key="low_bitrate_ratio",
        title="저화질 하한 배수",
        desc="기준(최적 BR) 대비 이 배수 이하인 영상을 저화질로 분류해 과화질 재인코딩에서 보호합니다.",
        hint="의도: 이미 화질 여유가 적은 영상이 더 뭉개지지 않도록 지킵니다. 높일수록 저화질(보존/교정) 범위가 넓어집니다.",
        section_cls=ThresholdsConfig,
        field_name="low_bitrate_ratio",
        setting_key="analysis.thresholds.low_bitrate_ratio",
        step=0.05,
        decimals=2,
        unit="x",
        slider_scale=100,
    ),
    _TunerRowSpec(
        key="keyframe_interval_sec",
        title="키프레임 간격 상한",
        desc="최대 키프레임(I-프레임) 간격이 이 초를 초과해 탐색이 끊기는 영상을 교정 대상에 넣습니다.",
        hint="의도: 구간 이동 시 화면이 멈칫거리는 불편을 해결합니다. 낮출수록 더 촘촘한 키프레임 간격을 요구합니다.",
        section_cls=ThresholdsConfig,
        field_name="keyframe_interval_sec",
        setting_key="analysis.thresholds.keyframe_interval_sec",
        step=0.1,
        decimals=1,
        unit="초",
        slider_scale=10,
    ),
)

_EFFICIENCY_SPECS: tuple[_TunerRowSpec, ...] = (
    _TunerRowSpec(
        key="min_saved_mb",
        title="최소 총 절감량",
        desc="과화질이라도 예상 절감 용량이 이 값 미만이면 실익이 작아 원본을 그대로 유지합니다.",
        hint="의도: 몇 MB만 아끼자고 인코딩 시간을 쓰는 비효율을 막습니다. 높일수록 체감 절감이 큰 파일만 남습니다.",
        section_cls=EfficiencyConfig,
        field_name="min_saved_mb",
        setting_key="analysis.efficiency.min_saved_mb",
        step=1.0,
        decimals=1,
        unit="MB",
        slider_scale=10,
    ),
    _TunerRowSpec(
        key="min_mb_per_min",
        title="분당 최소 절감량",
        desc="영상 1분당 예상 절감량(시간 가성비)이 이 기준 미만이면 긴 작업 시간 대비 이득이 적어 제외합니다.",
        hint="의도: 재생 시간이 긴 영상에서 효율이 낮은 작업을 걸러냅니다. 높일수록 짧은 시간 대비 이득이 큰 파일만 남습니다.",
        section_cls=EfficiencyConfig,
        field_name="min_mb_per_min",
        setting_key="analysis.efficiency.min_mb_per_min",
        step=0.5,
        decimals=1,
        unit="MB/분",
        slider_scale=10,
    ),
    _TunerRowSpec(
        key="exempt_saved_mb",
        title="대용량 절감 예외 허용",
        desc="분당 효율이 낮더라도 총 예상 절감량이 이 값 이상이면 예외적으로 인코딩 대상에 포함합니다.",
        hint="의도: 긴 영상처럼 분당 효율은 낮아도 한 번에 크게 줄어드는 대용량 파일은 놓치지 않고 압축합니다.",
        section_cls=EfficiencyConfig,
        field_name="exempt_saved_mb",
        setting_key="analysis.efficiency.exempt_saved_mb",
        step=5.0,
        decimals=1,
        unit="MB",
        slider_scale=1,
        optional_label="적용",
        optional_active_min=1.0,
    ),
)


class _TunerSliderRow(QFrame, ThemeAwareMixin):
    """QSlider · QDoubleSpinBox · 개별 초기화 버튼으로 구성된 튜너 행 위젯"""

    value_committed = pyqtSignal(bool)

    def __init__(
        self,
        spec: _TunerRowSpec,
        bitrate_model: BitrateModelConfig | None = None,
        *,
        show_hint: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsCard")
        self.spec = spec
        self._bitrate_model: BitrateModelConfig = (
            bitrate_model or DEFAULT_BITRATE_MODEL
        )
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
        """쉬운 설명·실시간 적용 예시 라벨의 표시 여부를 전환"""
        self._show_hint = visible
        self.lbl_hint.setVisible(bool(visible and self.spec.hint))
        self.lbl_example.setVisible(bool(visible and self.lbl_example.text()))

    def set_bitrate_model(self, bitrate_model: BitrateModelConfig) -> None:
        """기준 비트레이트 모델 갱신 및 실시간 예시 라벨 재계산"""
        self._bitrate_model = bitrate_model
        self._update_example_text()

    def _update_example_text(self) -> None:
        text = _format_tuner_example(self.spec.key, self.value(), self._bitrate_model)
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
        """외부 설정 동기화를 위해 시그널 방출 없이 슬라이더·스핀박스 값 반영"""
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
        """작업 실행 여부에 따라 슬라이더·스핀박스·초기화 버튼 잠금 상태 갱신"""
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


class CriteriaTunerPanel(QWidget, ThemeAwareMixin):
    """화질 임계값과 실익·시간 효율 선별 기준을 실시간 조정하는 튜너 탭 위젯"""

    criteria_changed = pyqtSignal(object, object)

    def __init__(
        self,
        thresholds: ThresholdsConfig,
        efficiency: EfficiencyConfig,
        parent: QWidget | None = None,
        *,
        bitrate_model: BitrateModelConfig | None = None,
        show_hints: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CriteriaTunerPanel")

        self._base_thresholds = thresholds
        self._base_efficiency = efficiency
        self._bitrate_model: BitrateModelConfig = (
            bitrate_model or DEFAULT_BITRATE_MODEL
        )
        self._show_hints: bool = show_hints
        self._syncing: bool = False
        self._busy: bool = False
        self._rows: dict[str, _TunerSliderRow] = {}
        self._cards: list[SettingsGroupCard] = []

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_TUNER_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._emit_criteria)

        self._init_ui()
        self.sync_from_config(thresholds, efficiency)
        self.init_theme_listener()

    def set_hints_visible(self, visible: bool) -> None:
        """모든 튜너 행의 쉬운 설명·실시간 예시 표시 여부를 일괄 전환"""
        self._show_hints = visible
        for row in self._rows.values():
            row.set_hint_visible(visible)

    def set_bitrate_model(self, bitrate_model: BitrateModelConfig) -> None:
        """기준 비트레이트 모델 변경을 모든 튜너 행의 실시간 예시에 반영"""
        self._bitrate_model = bitrate_model
        for row in self._rows.values():
            row.set_bitrate_model(bitrate_model)

    @property
    def rows(self) -> dict[str, _TunerSliderRow]:
        """키별 튜너 행 위젯 매핑 반환"""
        return self._rows

    @property
    def cards(self) -> list[SettingsGroupCard]:
        """패널 내 그룹 카드 목록 반환"""
        return self._cards

    def _add_tuner_group_card(
        self,
        body_layout: QVBoxLayout,
        *,
        icon_name: str,
        title: str,
        subtitle: str,
        specs: tuple[_TunerRowSpec, ...],
    ) -> None:
        card = SettingsGroupCard(
            icon_name=icon_name,
            title=title,
            subtitle=subtitle,
        )
        self._cards.append(card)
        for spec in specs:
            row = _TunerSliderRow(
                spec,
                bitrate_model=self._bitrate_model,
                show_hint=self._show_hints,
                parent=card,
            )
            row.value_committed.connect(self._on_row_committed)
            self._rows[spec.key] = row
            card.add_row(row)
        body_layout.addWidget(card)

    def _init_ui(self) -> None:
        self.scroll_area, _body, body_layout, self.lbl_desc, self.btn_reset_all = (
            build_scroll_tab_shell(
                self,
                desc_text="조정 즉시 목록·대시보드에 반영됩니다. 마우스 오버 시 세부 설명을 봅니다.",
                reset_button_text="기본값 복원",
                reset_tooltip="1차 화질·키프레임 및 2차 실익·효율 기준을 모두 기본값으로 되돌립니다.",
                on_reset=self.restore_defaults,
            )
        )

        self._add_tuner_group_card(
            body_layout,
            icon_name="sliders",
            title="1차 화질 · 키프레임",
            subtitle="과화질·저화질 비율 및 탐색 기준",
            specs=_QUALITY_SPECS,
        )
        self._add_tuner_group_card(
            body_layout,
            icon_name="zap",
            title="2차 실익 · 시간 효율",
            subtitle="인코딩 ROI 미달 항목 제외",
            specs=_EFFICIENCY_SPECS,
        )
        body_layout.addStretch(1)

        # 편리한 직접 참조 별칭 유지
        self.spin_over_ratio: QDoubleSpinBox = self._rows["over_bitrate_ratio"].spin
        self.spin_low_ratio: QDoubleSpinBox = self._rows["low_bitrate_ratio"].spin
        self.spin_kf_threshold: QDoubleSpinBox = self._rows["keyframe_interval_sec"].spin
        self.spin_min_saved: QDoubleSpinBox = self._rows["min_saved_mb"].spin
        self.spin_min_mb_per_min: QDoubleSpinBox = self._rows["min_mb_per_min"].spin
        self.spin_exempt_saved: QDoubleSpinBox = self._rows["exempt_saved_mb"].spin
        self.chk_exempt_saved: QCheckBox | None = self._rows["exempt_saved_mb"].chk_enable

    def current_thresholds(self) -> ThresholdsConfig:
        """현재 UI 입력값을 ThresholdsConfig로 반환"""
        return replace(
            self._base_thresholds,
            over_bitrate_ratio=self._rows["over_bitrate_ratio"].value(),
            low_bitrate_ratio=self._rows["low_bitrate_ratio"].value(),
            keyframe_interval_sec=self._rows["keyframe_interval_sec"].value(),
        )

    def current_efficiency(self) -> EfficiencyConfig:
        """현재 UI 입력값을 반영한 EfficiencyConfig 반환"""
        return replace(
            self._base_efficiency,
            min_saved_mb=self._rows["min_saved_mb"].value(),
            min_mb_per_min=self._rows["min_mb_per_min"].value(),
            exempt_saved_mb=self._rows["exempt_saved_mb"].value(),
        )

    def sync_from_config(
        self,
        thresholds: ThresholdsConfig,
        efficiency: EfficiencyConfig,
    ) -> None:
        """외부 설정 상태를 신호 방출 없이 모든 튜너 행에 동기화"""
        self._debounce_timer.stop()
        self._syncing = True
        try:
            self._base_thresholds = thresholds
            self._base_efficiency = efficiency
            self._rows["over_bitrate_ratio"].set_value(thresholds.over_bitrate_ratio)
            self._rows["low_bitrate_ratio"].set_value(thresholds.low_bitrate_ratio)
            self._rows["keyframe_interval_sec"].set_value(
                thresholds.keyframe_interval_sec
            )
            self._rows["min_saved_mb"].set_value(efficiency.min_saved_mb)
            self._rows["min_mb_per_min"].set_value(efficiency.min_mb_per_min)
            self._rows["exempt_saved_mb"].set_value(efficiency.exempt_saved_mb)
            self._update_reset_all_state()
        finally:
            self._syncing = False

    def restore_defaults(self) -> None:
        """모든 판별·선별 기준을 기본값으로 복원하고 즉시 변경 신호 방출"""
        if self._busy:
            return
        t = replace(
            self._base_thresholds,
            over_bitrate_ratio=DEFAULT_THRESHOLDS.over_bitrate_ratio,
            low_bitrate_ratio=DEFAULT_THRESHOLDS.low_bitrate_ratio,
            keyframe_interval_sec=DEFAULT_THRESHOLDS.keyframe_interval_sec,
        )
        e = replace(
            self._base_efficiency,
            min_saved_mb=DEFAULT_EFFICIENCY.min_saved_mb,
            min_mb_per_min=DEFAULT_EFFICIENCY.min_mb_per_min,
            exempt_saved_mb=DEFAULT_EFFICIENCY.exempt_saved_mb,
        )
        self.sync_from_config(t, e)
        self._emit_criteria()

    def set_busy(self, busy: bool) -> None:
        """백그라운드 스캔·분류 실행 중 튜너 조작 잠금 전환"""
        self._busy = busy
        for row in self._rows.values():
            row.set_busy(busy)
        self._update_reset_all_state()

    def _update_reset_all_state(self) -> None:
        has_custom = any(not row.is_default_value() for row in self._rows.values())
        self.btn_reset_all.setEnabled((not self._busy) and has_custom)

    def _on_row_committed(self, immediate: bool) -> None:
        if self._syncing:
            return
        self._update_reset_all_state()
        if immediate:
            self._debounce_timer.stop()
            self._emit_criteria()
        else:
            self._debounce_timer.start()

    def _emit_criteria(self) -> None:
        if self._syncing or self._busy:
            return
        self.criteria_changed.emit(
            self.current_thresholds(), self.current_efficiency()
        )

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 상단 복원 버튼 아이콘 색상 갱신"""
        apply_inline_reset_button_icon(self.btn_reset_all, palette)
