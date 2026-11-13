from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from src.core.config import DEFAULT_CONFIGS, ClassifierConfig
from src.core.exception_rules import (
    parse_exception_token,
    split_exception_tokens,
    validate_exception_token,
)
from src.core.messages import format_over_bitrate_desc
from src.gui.constants import (
    FILTER_DEBOUNCE_MS,
    THRESHOLD_BOUNDS,
)
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

if TYPE_CHECKING:
    from src.classifier import VideoClassifier


__all__ = ["FilterTuningBar"]


class FilterTuningBar(QFrame, ThemeAwareMixin):
    """
    - 검색, 필터링, 임계값 조정 및 예외 규칙 입력을 제공하는 컨트롤 바 위젯.
    """

    filter_changed = pyqtSignal()
    thresholds_changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FilterBar")
        self._updating: bool = False
        self._user_editing_exc: bool = False
        self._base_cfg: ClassifierConfig = ClassifierConfig()
        self._exc_debounce_timer = QTimer(self)
        self._exc_debounce_timer.setSingleShot(True)
        self._exc_debounce_timer.setInterval(FILTER_DEBOUNCE_MS)
        self._exc_debounce_timer.timeout.connect(self._on_spin_or_text_changed)

        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(14, 10, 14, 10)
        main_layout.setSpacing(8)

        # 1행: 검색 및 필터링
        row_top = QHBoxLayout()
        row_top.setSpacing(10)

        self.search_edit = QLineEdit()
        self._action_search = self.search_edit.addAction(
            get_icon("search", palette=get_active_palette(), size=14, pad_right=7),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.search_edit.setPlaceholderText("파일명, 코덱 실시간 검색...")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.setMinimumWidth(220)
        self.search_edit.textChanged.connect(self.filter_changed.emit)

        lbl_zone = QLabel("화질 등급:")
        lbl_zone.setObjectName("SectionLabel")
        self.combo_zone = QComboBox()
        self.combo_zone.addItem("전체", "ALL")
        self.combo_zone.addItem("과화질", "OVER")
        self.combo_zone.addItem("적정화질", "OPTIMAL")
        self.combo_zone.addItem("저화질", "LOW")
        self.combo_zone.setFixedWidth(100)
        self.combo_zone.currentIndexChanged.connect(self.filter_changed.emit)

        lbl_target = QLabel("표시 대상:")
        lbl_target.setObjectName("SectionLabel")
        self.combo_target = QComboBox()
        self.combo_target.addItem("전체", "ALL")
        self.combo_target.addItem("인코딩 대상", "ENCODE_ONLY")
        self.combo_target.addItem("원본 보존", "PASS_ONLY")
        self.combo_target.addItem("키프레임 불량", "DEFECT_KF")
        self.combo_target.setFixedWidth(120)
        self.combo_target.currentIndexChanged.connect(self.filter_changed.emit)

        self.lbl_filter_count = QLabel("표시: 0 / 0개")
        self.lbl_filter_count.setObjectName("SectionLabel")
        self.lbl_filter_count.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.lbl_filter_count.setMinimumWidth(110)

        row_top.addWidget(self.search_edit, stretch=1)
        row_top.addWidget(lbl_zone)
        row_top.addWidget(self.combo_zone)
        row_top.addSpacing(4)
        row_top.addWidget(lbl_target)
        row_top.addWidget(self.combo_target)
        row_top.addSpacing(6)
        row_top.addWidget(self.lbl_filter_count)
        main_layout.addLayout(row_top)

        # 2행: 임계값 조정 및 예외 규칙
        row_bottom = QHBoxLayout()
        row_bottom.setSpacing(10)

        lbl_over = QLabel("과화질 배수:")
        lbl_over.setObjectName("SectionLabel")
        self.spin_over_ratio = QDoubleSpinBox()
        self.spin_over_ratio.setRange(*THRESHOLD_BOUNDS["over_bitrate_ratio"])
        self.spin_over_ratio.setSingleStep(0.05)
        self.spin_over_ratio.setDecimals(2)
        self.spin_over_ratio.setSuffix("x")
        self.spin_over_ratio.setValue(float(DEFAULT_CONFIGS["THRESHOLD_OPTIMAL_BITRATE_RATE"]))
        self.spin_over_ratio.setFixedWidth(85)
        self.spin_over_ratio.valueChanged.connect(self._on_spin_or_text_changed)

        lbl_kf = QLabel("KF 상한:")
        lbl_kf.setObjectName("SectionLabel")
        self.spin_kf_limit = QDoubleSpinBox()
        self.spin_kf_limit.setRange(*THRESHOLD_BOUNDS["keyframe_threshold"])
        self.spin_kf_limit.setSingleStep(0.1)
        self.spin_kf_limit.setDecimals(1)
        self.spin_kf_limit.setSuffix("s")
        self.spin_kf_limit.setValue(float(DEFAULT_CONFIGS["THRESHOLD_KEYFRAME_INTERVAL"]))
        self.spin_kf_limit.setFixedWidth(80)
        self.spin_kf_limit.valueChanged.connect(self._on_spin_or_text_changed)

        lbl_small = QLabel("소형 기준:")
        lbl_small.setObjectName("SectionLabel")
        self.spin_small_mb = QDoubleSpinBox()
        self.spin_small_mb.setRange(*THRESHOLD_BOUNDS["small_footage_mb"])
        self.spin_small_mb.setSingleStep(5.0)
        self.spin_small_mb.setDecimals(1)
        self.spin_small_mb.setSuffix("MB")
        self.spin_small_mb.setValue(float(DEFAULT_CONFIGS["SMALL_FOOTAGE_SIZE_MB"]))
        self.spin_small_mb.setFixedWidth(92)
        self.spin_small_mb.valueChanged.connect(self._on_spin_or_text_changed)

        self._default_exc_tooltip = (
            "파일명 키워드 또는 파이썬 조건식(예: v.duration < 10, v.fps >= 60)을 쉼표로 구분해 입력하면 "
            "인코딩에서 제외하고 원본 보존으로 분류합니다."
        )
        lbl_exc = QLabel("예외 규칙:")
        lbl_exc.setObjectName("SectionLabel")
        self.edit_exceptions = QLineEdit()
        self._action_exc = self.edit_exceptions.addAction(
            get_icon("ban", palette=get_active_palette(), size=14, pad_right=7),
            QLineEdit.ActionPosition.LeadingPosition,
        )
        self.edit_exceptions.setPlaceholderText(
            "예외 키워드(sample, 무손실) 또는 조건식(v.duration < 10) 입력..."
        )
        self.edit_exceptions.setClearButtonEnabled(True)
        self.edit_exceptions.setToolTip(self._default_exc_tooltip)
        self.edit_exceptions.textEdited.connect(self._on_exceptions_text_edited)
        self.edit_exceptions.textChanged.connect(self._on_exceptions_text_changed)
        self.edit_exceptions.returnPressed.connect(self._flush_exceptions_immediately)

        row_bottom.addWidget(lbl_over)
        row_bottom.addWidget(self.spin_over_ratio)
        row_bottom.addSpacing(4)
        row_bottom.addWidget(lbl_kf)
        row_bottom.addWidget(self.spin_kf_limit)
        row_bottom.addSpacing(4)
        row_bottom.addWidget(lbl_small)
        row_bottom.addWidget(self.spin_small_mb)
        row_bottom.addSpacing(8)
        row_bottom.addWidget(lbl_exc)
        row_bottom.addWidget(self.edit_exceptions, stretch=1)
        main_layout.addLayout(row_bottom)

        self._update_dynamic_tooltips()
        self.init_theme_listener()

    def set_busy(self, busy: bool) -> None:
        """
        - 백그라운드 작업 중 임계값 및 예외 규칙 컨트롤을 잠가 스레드 레이스 컨디션을 방지.
        """
        enabled = not busy
        self.spin_over_ratio.setEnabled(enabled)
        self.spin_kf_limit.setEnabled(enabled)
        self.spin_small_mb.setEnabled(enabled)
        self.edit_exceptions.setEnabled(enabled)

    def get_exception_tokens(self) -> list[str]:
        """현재 예외 규칙 입력란에 등록된 토큰 리스트를 순서대로 반환."""
        return split_exception_tokens(self.edit_exceptions.text())

    def set_exception_tokens(self, tokens: list[str]) -> None:
        """예외 규칙 입력란을 지정된 토큰 리스트로 갱신."""
        self.edit_exceptions.setText(", ".join(tokens))

    def _validate_exceptions_ui(self) -> list[str]:
        errors: list[str] = []
        for token in self.get_exception_tokens():
            _, err = validate_exception_token(token)
            if err:
                errors.append(err)

        is_invalid = "true" if errors else "false"
        if self.edit_exceptions.property("invalid") != is_invalid:
            self.edit_exceptions.setProperty("invalid", is_invalid)
            style = self.edit_exceptions.style()
            if style is not None:
                style.unpolish(self.edit_exceptions)
                style.polish(self.edit_exceptions)
        if errors:
            self.edit_exceptions.setToolTip("\n".join(errors))
        else:
            self.edit_exceptions.setToolTip(self._default_exc_tooltip)
        return errors

    def _on_exceptions_text_edited(self, _text: str) -> None:
        self._user_editing_exc = True
        self._validate_exceptions_ui()
        if not self._updating:
            self._exc_debounce_timer.start()

    def _on_exceptions_text_changed(self, _text: str) -> None:
        if self._user_editing_exc:
            self._user_editing_exc = False
            return
        self._exc_debounce_timer.stop()
        self._validate_exceptions_ui()
        self._on_spin_or_text_changed()

    def _flush_exceptions_immediately(self) -> None:
        self._exc_debounce_timer.stop()
        self._validate_exceptions_ui()
        self._on_spin_or_text_changed()

    def _update_dynamic_tooltips(self) -> None:
        self.spin_over_ratio.setToolTip(
            f"최적 비트레이트 대비 과화질 판정 배수 "
            f"({format_over_bitrate_desc(self._base_cfg, self.spin_over_ratio.value())}, "
            f"기본 {DEFAULT_CONFIGS['THRESHOLD_OPTIMAL_BITRATE_RATE']}x)"
        )
        self.spin_kf_limit.setToolTip(
            "허용 가능한 최대 키프레임 간격 "
            f"(초과 시 1초 키프레임 교정 대상, 기본 {DEFAULT_CONFIGS['THRESHOLD_KEYFRAME_INTERVAL']}초)"
        )
        self.spin_small_mb.setToolTip(
            f"소형 영상으로 판별할 용량 기준 (기본 {DEFAULT_CONFIGS['SMALL_FOOTAGE_SIZE_MB']} MB)"
        )

    def _on_spin_or_text_changed(self) -> None:
        self._update_dynamic_tooltips()
        if not self._updating:
            self.thresholds_changed.emit()

    def sync_from_config(self, cfg: ClassifierConfig) -> None:
        """
        - ClassifierConfig 설정 객체의 임계값을 스핀박스 컨트롤에 동기화.
        """
        self._base_cfg = cfg
        self._updating = True
        try:
            self.spin_over_ratio.setValue(cfg.over_bitrate_ratio)
            self.spin_kf_limit.setValue(cfg.keyframe_threshold)
            self.spin_small_mb.setValue(cfg.small_footage_mb)
        finally:
            self._updating = False
        self._update_dynamic_tooltips()

    def apply_to_classifier(self, classifier: VideoClassifier) -> None:
        """
        - 현재 UI의 스핀박스 임계값과 예외 규칙들을 VideoClassifier 인스턴스에 반영.
        """
        classifier.set_over_bitrate_ratio(self.spin_over_ratio.value())
        classifier.set_keyframe_threshold(self.spin_kf_limit.value())
        classifier.set_small_footage_mb(self.spin_small_mb.value())

        classifier.clear_exception_rules()
        for token in self.get_exception_tokens():
            rule_fn = parse_exception_token(token)
            if rule_fn is not None:
                classifier.add_exception_rule(rule_fn)

    def apply_theme(self, palette: ThemePalette) -> None:
        """
        - 활성 테마 팔레트에 맞춰 입력창 액션 아이콘 색상을 갱신.
        """
        if self._action_search is not None:
            self._action_search.setIcon(
                get_icon("search", palette=palette, size=14, pad_right=7)
            )
        if self._action_exc is not None:
            self._action_exc.setIcon(
                get_icon("ban", palette=palette, size=14, pad_right=7)
            )
