"""판별 기준 범주 페이지(화질, 키프레임, 절약, 비트레이트 기준점, 고급 기준) 모듈"""

from dataclasses import replace

from PyQt6.QtCore import QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QDoubleSpinBox,
    QWidget,
)

from src.core.config import (
    DEFAULT_SAVINGS,
    DEFAULT_THRESHOLDS,
    SAVINGS_BASIS_CHOICES,
    ClassifierConfig,
    EncodeConfig,
    SavingsConfig,
    ThresholdsConfig,
)
from src.gui.settings.collapsible_section import CollapsibleSection
from src.gui.settings.setting_controls import (
    apply_inline_reset_button_icon,
    build_scroll_tab_shell,
)
from src.gui.settings.setting_row import SettingRow, make_combo_row
from src.gui.settings.settings_advanced_cards import (
    build_bitrate_base_card,
    build_codecs_card,
    build_criteria_margin_card,
    build_scaling_card,
)
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.settings.tuner_slider_row import TunerRowSpec, TunerSliderRow
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

_TUNER_DEBOUNCE_MS: int = 120
PAGE_TITLE: str = "판별 기준"


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
        desc="원본 코덱 기준 최적 BR 대비 이 배수 이하인 영상을 저화질로 판정해 과화질 인코딩에서 보호합니다.",
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
        desc="최대 키프레임(I-프레임) 간격이 이 초를 초과해 구간 이동이 끊기는 영상을 인코딩 대상에 넣습니다.",
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
        hint="몇 MB만 아끼자고 인코딩 시간을 쓰는 낭비를 막습니다. 높일수록 절약이 큰 파일만 남습니다.",
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
        desc="선택한 절약 선별 기준(재생 시간 1분당 또는 예상 인코딩 시간 1분당)의 절약(MB/분)이 이 기준 미만이면 인코딩 시간 대비 절약이 적어 제외합니다.",
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
        desc="재생 시간 기준에서만 적용됩니다. 분당 절약이 낮더라도 총 예상 절약량이 이 값 이상이면 예외적으로 인코딩 대상에 포함합니다.",
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
    """판별 기준 범주 페이지. 슬라이더와 기준점 입력을 한 화면에서 조정"""

    criteria_changed = pyqtSignal(object, object)
    changed = pyqtSignal()

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
        self._standard_rows: dict[str, SettingRow] = {}
        self._cards: list[SettingsGroupCard] = []
        self.section_advanced: CollapsibleSection | None = None

        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(_TUNER_DEBOUNCE_MS)
        self._debounce_timer.timeout.connect(self._emit_criteria)

        self._init_ui()
        self.sync_from_config(thresholds, savings)
        self.init_theme_listener()

    @property
    def rows(self) -> dict[str, TunerSliderRow]:
        """키별 튜너 슬라이더 행 위젯 매핑 반환"""
        return self._rows

    @property
    def standard_rows(self) -> dict[str, SettingRow]:
        """슬라이더가 아닌 기준점 입력 행 매핑 반환(설정 키 기준)"""
        return self._standard_rows

    @property
    def setting_rows(self) -> dict[str, SettingRow | TunerSliderRow]:
        """저장 키 기준으로 튜너 행과 기준점 입력 행을 합친 매핑 반환"""
        merged: dict[str, SettingRow | TunerSliderRow] = dict(self._standard_rows)
        for spec in (*_QUALITY_SPECS, *_SAVINGS_SPECS):
            merged[spec.setting_key] = self._rows[spec.key]
        return merged

    @property
    def cards(self) -> list[SettingsGroupCard]:
        """페이지 내 그룹 카드 목록 반환"""
        return self._cards

    def _add_tuner_rows(
        self,
        card: SettingsGroupCard,
        specs: tuple[TunerRowSpec, ...],
    ) -> None:
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


    def _init_ui(self) -> None:
        self.scroll_area, _body, body_layout, self.btn_reset_all = build_scroll_tab_shell(
            self,
            reset_button_text="기준 초기화",
            reset_tooltip="화질, 키프레임, 절약, 비트레이트 기준을 모두 기본값으로 되돌립니다.",
            on_reset=self.restore_defaults,
        )

        card_quality = SettingsGroupCard(
            icon_name="sliders",
            title="화질, 키프레임",
            subtitle="과화질, 저화질 비율 및 구간 이동 기준",
            help_anchor="ratio",
        )
        self._cards.append(card_quality)
        self._add_tuner_rows(card_quality, _QUALITY_SPECS)
        make_combo_row(
            self._standard_rows,
            card_quality,
            EncodeConfig,
            "force_legacy_compat",
            "구형 코덱 호환 인코딩",
            "하드웨어 디코딩을 지원하지 않는 구형 코덱(WMV3, VC-1, VP8, FLV1 등)을 "
            "절약 선별 없이 표준 코덱과 1초 키프레임으로 인코딩합니다.",
            (
                ("사용 안 함 (기본)", False),
                ("사용", True),
            ),
            row_key="analysis.encode.force_legacy_compat",
        )
        body_layout.addWidget(card_quality)

        card_savings = SettingsGroupCard(
            icon_name="zap",
            title="절약, 분당 절약",
            subtitle="인코딩 작업 대비 절약 기준",
            help_anchor="savings",
        )
        self._cards.append(card_savings)
        self.selection_basis_combo = make_combo_row(
            self._standard_rows,
            card_savings,
            SavingsConfig,
            "selection_basis",
            "절약 선별 기준",
            "과화질 영상 중 인코딩 작업 1분당 절약이 기준보다 적은 영상을 건너뜁니다. "
            "재생 시간 기준은 영상 재생 시간 1분당 절약을, 예상 인코딩 시간 기준은 인코딩에 걸릴 시간 1분당 절약을 봅니다. "
            "재생 시간 기준만 대용량 절약 예외를 적용합니다.",
            SAVINGS_BASIS_CHOICES,
            row_key="analysis.savings.selection_basis",
        )
        self._add_tuner_rows(card_savings, _SAVINGS_SPECS)
        body_layout.addWidget(card_savings)

        card_bitrate = build_bitrate_base_card(self._standard_rows)
        self._cards.append(card_bitrate)
        body_layout.addWidget(card_bitrate)

        self.section_advanced = CollapsibleSection(
            title="고급 설정",
            subtitle="(판별 여유, 코덱 가중치, 스케일링)",
            is_expanded=False,
        )
        for card in (
            build_criteria_margin_card(self._standard_rows),
            build_codecs_card(self._standard_rows),
            build_scaling_card(self._standard_rows),
        ):
            self._cards.append(card)
            self.section_advanced.add_widget(card)
        body_layout.addWidget(self.section_advanced)
        body_layout.addStretch(1)

        basis_key = "analysis.savings.selection_basis"
        for key, row in self._standard_rows.items():
            handler = (
                self._on_selection_basis_changed
                if key == basis_key
                else self._on_standard_row_changed
            )
            row.value_changed.connect(handler)

        # - 테스트와 외부 호출이 쓰는 직접 참조 별칭 유지
        self.spin_over_ratio: QDoubleSpinBox = self._rows["over_bitrate_ratio"].spin
        self.spin_low_ratio: QDoubleSpinBox = self._rows["low_bitrate_ratio"].spin
        self.spin_kf_threshold: QDoubleSpinBox = self._rows["keyframe_interval_sec"].spin
        self.spin_min_saved: QDoubleSpinBox = self._rows["min_saved_mb"].spin
        self.spin_min_mb_per_min: QDoubleSpinBox = self._rows["min_mb_per_min"].spin
        self.spin_exempt_saved: QDoubleSpinBox = self._rows["exempt_saved_mb"].spin
        self.chk_exempt_saved: QCheckBox | None = self._rows["exempt_saved_mb"].chk_enable


    def set_hints_visible(self, visible: bool) -> None:
        """모든 기준 행의 쉬운 설명, 실시간 예시 표시 여부를 일괄 전환"""
        self._show_hints = visible
        for row in self._rows.values():
            row.set_hint_visible(visible)
        for row in self._standard_rows.values():
            row.set_hint_visible(visible)

    def set_config(self, config: ClassifierConfig) -> None:
        """기준 비트레이트 모델 변경을 모든 튜너 행의 실시간 예시에 반영"""
        self._config = config
        self._update_examples()

    def current_thresholds(self) -> ThresholdsConfig:
        """현재 슬라이더 입력값을 ThresholdsConfig로 반환"""
        return replace(
            self._base_thresholds,
            over_bitrate_ratio=self._rows["over_bitrate_ratio"].value(),
            low_bitrate_ratio=self._rows["low_bitrate_ratio"].value(),
            keyframe_interval_sec=self._rows["keyframe_interval_sec"].value(),
        )

    def current_savings(self) -> SavingsConfig:
        """현재 슬라이더 입력값을 반영한 SavingsConfig 반환"""
        basis = self.selection_basis_combo.currentData()
        return replace(
            self._base_savings,
            min_saved_mb=self._rows["min_saved_mb"].value(),
            min_mb_per_min=self._rows["min_mb_per_min"].value(),
            exempt_saved_mb=self._rows["exempt_saved_mb"].value(),
            selection_basis=str(basis) if basis is not None else self._base_savings.selection_basis,
        )

    def sync_from_config(
        self,
        thresholds: ThresholdsConfig,
        savings: SavingsConfig,
    ) -> None:
        """외부 설정 상태를 신호 방출 없이 모든 기준 행에 동기화"""
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
            index = self.selection_basis_combo.findData(savings.selection_basis)
            if index >= 0:
                self.selection_basis_combo.setCurrentIndex(index)
            self._update_examples()
            self._update_reset_all_state()
        finally:
            self._syncing = False

    def restore_defaults(self) -> None:
        """23개 판별 기준을 모두 기본값으로 복원하고 즉시 변경 신호 방출"""
        if self._busy:
            return
        self._syncing = True
        try:
            # - 이후 조정이 엉뚱한 기준 위에 얹히지 않도록 기본값 사본도 함께 되돌림
            self._base_thresholds = DEFAULT_THRESHOLDS
            self._base_savings = DEFAULT_SAVINGS
            defaults = {
                "over_bitrate_ratio": DEFAULT_THRESHOLDS.over_bitrate_ratio,
                "low_bitrate_ratio": DEFAULT_THRESHOLDS.low_bitrate_ratio,
                "keyframe_interval_sec": DEFAULT_THRESHOLDS.keyframe_interval_sec,
                "min_saved_mb": DEFAULT_SAVINGS.min_saved_mb,
                "min_mb_per_min": DEFAULT_SAVINGS.min_mb_per_min,
                "exempt_saved_mb": DEFAULT_SAVINGS.exempt_saved_mb,
            }
            for key, value in defaults.items():
                self._rows[key].set_value(value)
            for row in self._standard_rows.values():
                row.reset_to_default()
            self._update_examples()
            self._update_reset_all_state()
        finally:
            self._syncing = False
        # - 설정 창은 criteria_changed로 설정 사본과 저장을 한 번에 처리하므로 changed는 보내지 않음
        self.criteria_changed.emit(self.current_thresholds(), self.current_savings())

    def set_busy(self, busy: bool) -> None:
        """백그라운드 작업 중 기준 조작 잠금 전환"""
        self._busy = busy
        for row in self._rows.values():
            row.set_busy(busy)
        for row in self._standard_rows.values():
            row.setEnabled(not busy)
        self._update_reset_all_state()

    def _update_reset_all_state(self) -> None:
        has_custom = any(
            not row.is_default_value() for row in self._rows.values()
        ) or any(not row.is_default_value() for row in self._standard_rows.values())
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

    def _on_standard_row_changed(self) -> None:
        if self._syncing or self._busy:
            return
        self._update_reset_all_state()
        self.changed.emit()

    def _on_selection_basis_changed(self) -> None:
        if self._syncing:
            return
        self._update_examples()
        self._update_reset_all_state()
        self.changed.emit()

    def _update_examples(self) -> None:
        basis = self.selection_basis_combo.currentData()
        savings = replace(
            self._config.savings,
            selection_basis=str(basis) if basis is not None else self._config.savings.selection_basis,
        )
        cfg = replace(self._config, savings=savings)
        for row in self._rows.values():
            row.set_config(cfg)

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 상단 복원 버튼 아이콘 색상 갱신"""
        apply_inline_reset_button_icon(self.btn_reset_all, palette)

