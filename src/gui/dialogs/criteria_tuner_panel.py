"""판별 기준(화질, 키프레임, 절약, 분당 절약) 실시간 조정 탭 패널 모듈"""

from dataclasses import replace

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QVBoxLayout,
    QWidget,
)

from src.core.config import (
    DEFAULT_SAVINGS,
    DEFAULT_THRESHOLDS,
    ClassifierConfig,
    SavingsConfig,
    ThresholdsConfig,
)
from src.gui.dialogs.setting_controls import (
    apply_inline_reset_button_icon,
    build_scroll_tab_shell,
)
from src.gui.dialogs.tuner_slider_row import TunerRowSpec, TunerSliderRow
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.gui.widgets.settings_group_card import SettingsGroupCard

_TUNER_DEBOUNCE_MS: int = 120



_QUALITY_SPECS: tuple[TunerRowSpec, ...] = (
    TunerRowSpec(
        key="over_bitrate_ratio",
        title="과화질 상한 배수",
        desc="목표 인코딩 코덱 기준 최적 BR(출력 BR)보다 이 배수 이상 비트레이트가 높은 영상을 과화질(용량 절약 대상)로 판정합니다.",
        hint="눈으로 구분하기 힘든 낭비 용량만 줄입니다. 낮출수록 압축 대상이 늘고, 높일수록 낭비가 확실한 영상만 남습니다.",
        section_cls=ThresholdsConfig,
        field_name="over_bitrate_ratio",
        setting_key="analysis.thresholds.over_bitrate_ratio",
        step=0.05,
        decimals=2,
        unit="x",
        slider_scale=100,
    ),
    TunerRowSpec(
        key="low_bitrate_ratio",
        title="저화질 하한 배수",
        desc="원본 코덱 기준 최적 BR 대비 이 배수 이하인 영상을 저화질로 분류해 과화질 재인코딩에서 보호합니다.",
        hint="이미 많이 압축된 영상이 더 뭉개지지 않게 지킵니다. 높일수록 보호받는 영상이 늘어납니다.",
        section_cls=ThresholdsConfig,
        field_name="low_bitrate_ratio",
        setting_key="analysis.thresholds.low_bitrate_ratio",
        step=0.05,
        decimals=2,
        unit="x",
        slider_scale=100,
    ),
    TunerRowSpec(
        key="keyframe_interval_sec",
        title="키프레임 간격 상한",
        desc="최대 키프레임(I-프레임) 간격이 이 초를 초과해 구간 이동이 끊기는 영상을 재인코딩 대상에 넣습니다.",
        hint="구간 이동 때 멈칫거리는 불편을 해결합니다. 낮출수록 촘촘한 키프레임을 요구해 대상이 늘어납니다.",
        section_cls=ThresholdsConfig,
        field_name="keyframe_interval_sec",
        setting_key="analysis.thresholds.keyframe_interval_sec",
        step=0.1,
        decimals=1,
        unit="초",
        slider_scale=10,
    ),
)

_SAVINGS_SPECS: tuple[TunerRowSpec, ...] = (
    TunerRowSpec(
        key="min_saved_mb",
        title="최소 총 절약량",
        desc="과화질이라도 예상 절약 용량이 이 값 미만이면 절약 효과가 작아 원본을 그대로 보존합니다.",
        hint="몇 MB만 아끼자고 재인코딩 시간을 쓰는 낭비를 막습니다. 높일수록 절약이 큰 파일만 남습니다.",
        section_cls=SavingsConfig,
        field_name="min_saved_mb",
        setting_key="analysis.savings.min_saved_mb",
        step=1.0,
        decimals=1,
        unit="MB",
        slider_scale=10,
    ),
    TunerRowSpec(
        key="min_mb_per_min",
        title="분당 최소 절약량",
        desc="선택한 절약 선별 기준(재생 시간 1분당 또는 예상 인코딩 시간 1분당)의 절약(MB/분)이 이 기준 미만이면 재인코딩 시간 대비 절약이 적어 제외합니다.",
        hint="길이에 비해 줄어드는 용량이 적은 작업을 걸러냅니다. 높일수록 짧은 시간 대비 절약이 큰 파일만 남습니다.",
        section_cls=SavingsConfig,
        field_name="min_mb_per_min",
        setting_key="analysis.savings.min_mb_per_min",
        step=0.5,
        decimals=1,
        unit="MB/분",
        slider_scale=10,
    ),
    TunerRowSpec(
        key="exempt_saved_mb",
        title="대용량 절약 예외 허용",
        desc="재생 시간 기준에서만 적용됩니다. 분당 절약이 낮더라도 총 예상 절약량이 이 값 이상이면 예외적으로 재인코딩 대상에 포함합니다.",
        hint="분당 절약은 낮아도 한 번에 크게 줄어드는 대용량 파일은 놓치지 않고 압축합니다. 체크를 끄면 쓰지 않습니다.",
        section_cls=SavingsConfig,
        field_name="exempt_saved_mb",
        setting_key="analysis.savings.exempt_saved_mb",
        step=5.0,
        decimals=1,
        unit="MB",
        slider_scale=1,
        optional_label="적용",
        optional_active_min=1.0,
    ),
)


class CriteriaTunerPanel(QWidget, ThemeAwareMixin):
    """화질 임계값과 절약, 분당 절약 선별 기준을 실시간 조정하는 튜너 탭 위젯"""

    criteria_changed = pyqtSignal(object, object)

    def __init__(
        self,
        thresholds: ThresholdsConfig,
        savings: SavingsConfig,
        parent: QWidget | None = None,
        *,
        config: ClassifierConfig | None = None,
        show_hints: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("CriteriaTunerPanel")

        self._base_thresholds = thresholds
        self._base_savings = savings
        self._config: ClassifierConfig = config or ClassifierConfig()
        self._show_hints: bool = show_hints
        self._syncing: bool = False
        self._busy: bool = False
        self._rows: dict[str, TunerSliderRow] = {}
        self._cards: list[SettingsGroupCard] = []

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_TUNER_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._emit_criteria)

        self._init_ui()
        self.sync_from_config(thresholds, savings)
        self.init_theme_listener()

    def set_hints_visible(self, visible: bool) -> None:
        """모든 튜너 행의 쉬운 설명, 실시간 예시 표시 여부를 일괄 전환"""
        self._show_hints = visible
        for row in self._rows.values():
            row.set_hint_visible(visible)

    def set_config(self, config: ClassifierConfig) -> None:
        """기준 비트레이트 모델 변경을 모든 튜너 행의 실시간 예시에 반영"""
        self._config = config
        for row in self._rows.values():
            row.set_config(config)

    @property
    def rows(self) -> dict[str, TunerSliderRow]:
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
        specs: tuple[TunerRowSpec, ...],
    ) -> None:
        card = SettingsGroupCard(
            icon_name=icon_name,
            title=title,
            subtitle=subtitle,
        )
        self._cards.append(card)
        for spec in specs:
            row = TunerSliderRow(
                spec,
                config=self._config,
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
                desc_text="조정 즉시 목록, 대시보드에 반영됩니다. 마우스 오버 시 세부 설명을 봅니다.",
                reset_button_text="기본값 복원",
                reset_tooltip="화질, 키프레임 및 절약, 분당 절약 기준을 모두 기본값으로 되돌립니다.",
                on_reset=self.restore_defaults,
            )
        )

        self._add_tuner_group_card(
            body_layout,
            icon_name="sliders",
            title="화질, 키프레임",
            subtitle="과화질, 저화질 비율 및 구간 이동 기준",
            specs=_QUALITY_SPECS,
        )
        self._add_tuner_group_card(
            body_layout,
            icon_name="zap",
            title="절약, 분당 절약",
            subtitle="인코딩 ROI 미달 항목 제외",
            specs=_SAVINGS_SPECS,
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

    def current_savings(self) -> SavingsConfig:
        """현재 UI 입력값을 반영한 SavingsConfig 반환"""
        return replace(
            self._base_savings,
            min_saved_mb=self._rows["min_saved_mb"].value(),
            min_mb_per_min=self._rows["min_mb_per_min"].value(),
            exempt_saved_mb=self._rows["exempt_saved_mb"].value(),
        )

    def sync_from_config(
        self,
        thresholds: ThresholdsConfig,
        savings: SavingsConfig,
    ) -> None:
        """외부 설정 상태를 신호 방출 없이 모든 튜너 행에 동기화"""
        self._debounce_timer.stop()
        self._syncing = True
        try:
            self._base_thresholds = thresholds
            self._base_savings = savings
            self._rows["over_bitrate_ratio"].set_value(thresholds.over_bitrate_ratio)
            self._rows["low_bitrate_ratio"].set_value(thresholds.low_bitrate_ratio)
            self._rows["keyframe_interval_sec"].set_value(
                thresholds.keyframe_interval_sec
            )
            self._rows["min_saved_mb"].set_value(savings.min_saved_mb)
            self._rows["min_mb_per_min"].set_value(savings.min_mb_per_min)
            self._rows["exempt_saved_mb"].set_value(savings.exempt_saved_mb)
            self._update_reset_all_state()
        finally:
            self._syncing = False

    def restore_defaults(self) -> None:
        """모든 판별, 선별 기준을 기본값으로 복원하고 즉시 변경 신호 방출"""
        if self._busy:
            return
        t = replace(
            self._base_thresholds,
            over_bitrate_ratio=DEFAULT_THRESHOLDS.over_bitrate_ratio,
            low_bitrate_ratio=DEFAULT_THRESHOLDS.low_bitrate_ratio,
            keyframe_interval_sec=DEFAULT_THRESHOLDS.keyframe_interval_sec,
        )
        e = replace(
            self._base_savings,
            min_saved_mb=DEFAULT_SAVINGS.min_saved_mb,
            min_mb_per_min=DEFAULT_SAVINGS.min_mb_per_min,
            exempt_saved_mb=DEFAULT_SAVINGS.exempt_saved_mb,
        )
        self.sync_from_config(t, e)
        self._emit_criteria()

    def set_busy(self, busy: bool) -> None:
        """백그라운드 스캔, 분류 실행 중 튜너 조작 잠금 전환"""
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
            self.current_thresholds(), self.current_savings()
        )

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 상단 복원 버튼 아이콘 색상 갱신"""
        apply_inline_reset_button_icon(self.btn_reset_all, palette)
