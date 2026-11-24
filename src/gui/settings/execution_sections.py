"""재인코딩 범주 페이지: FFmpeg 상태 배지, 인코딩, 결과 파일, 실행 및 태그, 고급 설정 모듈

- 값은 ExecutionConfig와 EncodeConfig 일부로 읽고 쓰며, 사용자가 바꾸면 changed 신호를 보냄(대화상자가 자동 저장에 연결)
- 목표 인코딩 방식을 바꾸면 추천 배속을 먼저 맞추고 한 번만 저장. 출력 형식 설명은 목표 인코딩 방식에서 만듦
- FFmpeg 상태는 페이지가 처음 보일 때와 FFmpeg 경로를 바꾼 뒤에만 확인(실행 파일을 불필요하게 띄우지 않음)
- 재인코딩 태그 이름은 검증을 통과한 값만 반영하고, 검증에 실패하면 마지막 정상 값을 유지
"""

from collections.abc import Callable

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from src.core.analysis.system_probe import detect_recommended_encode_speed
from src.core.config import (
    ENCODE_CODEC_CHOICES,
    RESULT_POLICY_ARCHIVE,
    RESULT_POLICY_SEPARATE,
    EncodeConfig,
    ExecutionConfig,
)
from src.core.encode.encoder_trial import SHARED_ENCODER_TRIAL_CACHE, EncoderTrialCache
from src.core.encode.ffmpeg_capability import FfmpegCapability, probe_ffmpeg_capability
from src.core.encode_tag import normalize_tag_name
from src.core.encoder_specs import get_encode_codec_family
from src.gui.settings.collapsible_section import CollapsibleSection
from src.gui.settings.execution_status import STATUS_OBJECT_ERROR, codec_status, ffmpeg_status
from src.gui.settings.execution_texts import (
    AFTER_COMPLETE_CHOICES,
    CONTAINER_CHOICES,
    NAME_CONFLICT_CHOICES,
    PREVENT_SLEEP_CHOICES,
    PRIORITY_CHOICES,
    RESULT_POLICY_CHOICES,
    SPEED_DESC,
    TAG_MODE_CHOICES,
    TOOLTIP_AFTER_COMPLETE,
    TOOLTIP_ARCHIVE_DIR,
    TOOLTIP_CODEC,
    TOOLTIP_CONCURRENT,
    TOOLTIP_FFMPEG,
    TOOLTIP_FFPROBE,
    TOOLTIP_NAME_CONFLICT,
    TOOLTIP_OUTPUT_DIR,
    TOOLTIP_PREVENT_SLEEP,
    TOOLTIP_PRIORITY,
    TOOLTIP_RESULT_POLICY,
    TOOLTIP_SPEED_DETECT,
    TOOLTIP_TAG_MODE,
    TOOLTIP_TAG_NAME,
    build_container_desc,
)
from src.gui.settings.setting_controls import (
    RESET_BUTTON_SIZE,
    RESET_ICON_SIZE,
    SCROLL_BODY_MARGINS,
    SCROLL_BODY_SPACING,
    TAB_INNER_MARGINS,
    TAB_INNER_SPACING,
    ScrollSafeDoubleSpinBox,
)
from src.gui.settings.setting_row import (
    PERCENT_SCALE,
    SettingRow,
    make_combo_row,
    make_float_spin_row,
    make_int_spin_row,
    make_percent_row,
    make_text_row,
    resolve_field_bounds,
    resolve_field_default,
)
from src.gui.settings.settings_field_bindings import FLOAT_SETTING_ROUND_DIGITS
from src.gui.settings.settings_group_card import SettingsGroupCard
from src.gui.styles.icons import create_icon_button, get_icon, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin

PAGE_TITLE: str = "재인코딩"
_PATH_EDIT_WIDTH: int = 260
_TEXT_EDIT_WIDTH: int = 172
_SPIN_STEP: int = 1
_SPEED_EDIT_WIDTH: int = 92
_SPEED_ROW_SPACING: int = 4
_AUTO_SUFFIX: str = " (자동)"
_ADVANCED_TITLE: str = "고급 설정"
_ADVANCED_SUBTITLE: str = "(FFmpeg 경로, 자동 품질 조절, 실행 세부)"
_CARD_ENCODE_TITLE: str = "인코딩"
_CARD_RESULT_TITLE: str = "결과 파일"
_CARD_RUN_TITLE: str = "실행 및 태그"
_CARD_FFMPEG_TITLE: str = "FFmpeg / FFprobe 경로"
_CARD_AUTO_TITLE: str = "자동 품질 조절 및 중단 기준"
_CARD_DETAIL_TITLE: str = "실행 및 파일 세부"
_TAG_NAME_KEY: str = "execution.encode_tag_name"
_CODEC_KEY: str = "analysis.encode.encode_codec"
_SPEED_KEY: str = "analysis.encode.encode_speed_factor"
_CONTAINER_KEY: str = "execution.container_policy"
_RESULT_POLICY_KEY: str = "execution.result_policy"
_ARCHIVE_KEY: str = "execution.archive_dirname"
_OUTPUT_KEY: str = "execution.output_dirname"
_SPEED_TITLE: str = "기준 인코딩 배속 (1080p 30fps)"
_SPEED_BUTTON_LABEL: str = "시스템 자동 감지"
_SPEED_BUTTON_ICON: str = "search"
_TRIAL_POLL_INTERVAL_MS: int = 200
_STATUS_PENDING_TEXT: str = "FFmpeg 상태: 확인 전"
_STATUS_TOOLTIP: str = "FFmpeg 상태를 다시 확인하고 고급 설정을 엽니다."
_BASELINE_LINK_TEXT: str = "판별 기준에서 변경"
_TOOLTIP_PROTECT_DELTA: str = (
    "키프레임을 교정할 때 목표 크기에 맞추려고 품질값을 올릴 수 있는 최대 폭입니다. "
    "값이 클수록 품질을 더 낮춰 크기를 줄이며, 기본값은 6.0입니다."
)
_TOOLTIP_SAMPLE_DISCARD: str = (
    "본 인코딩 전에 대표 구간을 먼저 인코딩해 예상 크기를 재고, "
    "예상 크기가 원본의 이 비율을 넘으면 본 인코딩을 건너뜁니다. 기본값은 105 %입니다."
)
_TOOLTIP_EARLY_STOP: str = (
    "인코딩 중 진행률로 환산한 예상 크기가 원본의 이 비율을 넘으면 인코딩을 멈추고 결과를 버립니다. "
    "원본은 그대로 두며, 기본값은 120 %입니다."
)


class ExecutionTabPanel(QWidget, ThemeAwareMixin):
    """재인코딩 범주 페이지 본문. 값은 ExecutionConfig와 EncodeConfig 일부로 읽고 쓰며, 사용자가 바꾸면 changed를 보냄"""

    changed = pyqtSignal()
    criteria_requested = pyqtSignal()
    result_policy_changed = pyqtSignal()

    def __init__(
        self,
        cfg: ExecutionConfig,
        encode: EncodeConfig,
        parent: QWidget | None = None,
        probe: Callable[[str], FfmpegCapability] = probe_ffmpeg_capability,
        trial: EncoderTrialCache = SHARED_ENCODER_TRIAL_CACHE,
    ) -> None:
        super().__init__(parent)
        self._probe: Callable[[str], FfmpegCapability] = probe
        self._trial: EncoderTrialCache = trial
        self._trial_poll: QTimer = QTimer(self)
        self._trial_poll.setSingleShot(True)
        self._trial_poll.setInterval(_TRIAL_POLL_INTERVAL_MS)
        self._trial_poll.timeout.connect(self._refresh_codec_status)
        self._loading: bool = True
        self._probed_path: str | None = None
        self._capability: FfmpegCapability | None = None
        self._tag_name: str = cfg.encode_tag_name
        self._policy_hidden: set[str] = set()
        self.rows: dict[str, SettingRow] = {}
        self.section_advanced: CollapsibleSection | None = None
        self._build_ui()
        self._loading = False
        self.load(cfg, encode)
        self.init_theme_listener()

    @property
    def row_keys(self) -> frozenset[str]:
        """행이 있는 설정 키 집합(ExecutionConfig 필드와 인코딩 설정 두 개)"""
        return frozenset(self.rows)

    @property
    def hidden_row_keys(self) -> frozenset[str]:
        """결과 파일 처리 방식 때문에 숨겨 둔 행 키 집합(검색 필터가 존중)"""
        return frozenset(self._policy_hidden)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*TAB_INNER_MARGINS)
        root.setSpacing(TAB_INNER_SPACING)

        header_row = QHBoxLayout()
        header_row.setContentsMargins(2, 0, 2, 0)
        header_row.setSpacing(8)
        header_row.addStretch(1)
        self.btn_ffmpeg_status = QPushButton(_STATUS_PENDING_TEXT, self)
        self.btn_ffmpeg_status.setObjectName("FfmpegStatusBadge")
        self.btn_ffmpeg_status.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_ffmpeg_status.setToolTip(_STATUS_TOOLTIP)
        self.btn_ffmpeg_status.setAccessibleName("FFmpeg 상태")
        self.btn_ffmpeg_status.clicked.connect(self._on_status_badge_clicked)
        header_row.addWidget(self.btn_ffmpeg_status)
        root.addLayout(header_row)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget(scroll)
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(*SCROLL_BODY_MARGINS)
        body_layout.setSpacing(SCROLL_BODY_SPACING)
        scroll.setWidget(body)
        root.addWidget(scroll, stretch=1)

        card_encode = self._new_card(body_layout, "gauge", _CARD_ENCODE_TITLE)
        self.combo_encode_codec = make_combo_row(
            self.rows, card_encode, EncodeConfig, "encode_codec", "목표 인코딩 방식", TOOLTIP_CODEC,
            ENCODE_CODEC_CHOICES, row_key=_CODEC_KEY,
        )
        self.lbl_codec_status = QLabel("", card_encode)
        self.lbl_codec_status.setWordWrap(True)
        card_encode.add_row(self.lbl_codec_status)
        self.spin_encode_speed, self.btn_detect_encode_speed = self._build_speed_row(card_encode)
        self.lbl_baseline = QLabel("", card_encode)
        self.lbl_baseline.setObjectName("SettingDesc")
        card_encode.add_row(self.lbl_baseline)
        self.btn_edit_criteria = QPushButton(_BASELINE_LINK_TEXT, card_encode)
        self.btn_edit_criteria.setObjectName("HelpLinkButton")
        self.btn_edit_criteria.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit_criteria.setToolTip("판별 기준 범주의 화질 기준값으로 이동합니다.")
        self.btn_edit_criteria.clicked.connect(self.criteria_requested.emit)
        link_row = QWidget(card_encode)
        link_layout = QHBoxLayout(link_row)
        link_layout.setContentsMargins(0, 0, 0, 0)
        link_layout.addWidget(self.btn_edit_criteria)
        link_layout.addStretch(1)
        card_encode.add_row(link_row)


        card_result = self._new_card(body_layout, "folder", _CARD_RESULT_TITLE)
        self.combo_result_policy = make_combo_row(
            self.rows, card_result, ExecutionConfig, "result_policy", "결과 파일 처리",
            TOOLTIP_RESULT_POLICY, RESULT_POLICY_CHOICES, row_key=_RESULT_POLICY_KEY,
        )
        self.edit_archive_dir = make_text_row(
            self.rows, card_result, ExecutionConfig, "archive_dirname", "원본 보관 폴더 이름",
            TOOLTIP_ARCHIVE_DIR, width=_TEXT_EDIT_WIDTH, row_key=_ARCHIVE_KEY,
        )
        self.edit_output_dir = make_text_row(
            self.rows, card_result, ExecutionConfig, "output_dirname", "별도 출력 폴더 이름",
            TOOLTIP_OUTPUT_DIR, width=_TEXT_EDIT_WIDTH, row_key=_OUTPUT_KEY,
        )
        self.combo_container = make_combo_row(
            self.rows, card_result, ExecutionConfig, "container_policy", "출력 형식", "",
            CONTAINER_CHOICES, row_key=_CONTAINER_KEY,
        )

        card_run = self._new_card(body_layout, "zap", _CARD_RUN_TITLE)
        self.spin_concurrent = make_int_spin_row(
            self.rows, card_run, ExecutionConfig, "concurrent_jobs", "동시 재인코딩 수",
            TOOLTIP_CONCURRENT, _SPIN_STEP, row_key="execution.concurrent_jobs",
        )
        self.spin_concurrent.valueChanged.connect(self._on_concurrent_value_changed)
        self.combo_prevent_sleep = make_combo_row(
            self.rows, card_run, ExecutionConfig, "prevent_sleep", "절전 방지",
            TOOLTIP_PREVENT_SLEEP, PREVENT_SLEEP_CHOICES, row_key="execution.prevent_sleep",
        )
        self.combo_after_complete = make_combo_row(
            self.rows, card_run, ExecutionConfig, "after_complete", "완료 후 동작",
            TOOLTIP_AFTER_COMPLETE, AFTER_COMPLETE_CHOICES, row_key="execution.after_complete",
        )
        self.combo_tag_mode = make_combo_row(
            self.rows, card_run, ExecutionConfig, "encode_tag_mode", "재인코딩 태그 기록",
            TOOLTIP_TAG_MODE, TAG_MODE_CHOICES, row_key="execution.encode_tag_mode",
        )

        section_advanced = CollapsibleSection(
            title=_ADVANCED_TITLE, subtitle=_ADVANCED_SUBTITLE, is_expanded=False
        )
        self.section_advanced = section_advanced
        card_paths = SettingsGroupCard(icon_name="terminal", title=_CARD_FFMPEG_TITLE)
        self.edit_ffmpeg = make_text_row(
            self.rows, card_paths, ExecutionConfig, "ffmpeg_path", "FFmpeg 경로",
            TOOLTIP_FFMPEG, width=_PATH_EDIT_WIDTH, row_key="execution.ffmpeg_path",
        )
        self.edit_ffprobe = make_text_row(
            self.rows, card_paths, ExecutionConfig, "ffprobe_path", "FFprobe 경로",
            TOOLTIP_FFPROBE, width=_PATH_EDIT_WIDTH, row_key="execution.ffprobe_path",
        )
        self.edit_ffmpeg.editingFinished.connect(self._on_ffmpeg_editing_finished)
        section_advanced.add_widget(card_paths)

        card_auto = SettingsGroupCard(icon_name="sliders", title=_CARD_AUTO_TITLE)
        self.spin_protect_delta = make_float_spin_row(
            self.rows, card_auto, ExecutionConfig, "protect_cq_delta_max", "품질값 자동 조절 한도",
            _TOOLTIP_PROTECT_DELTA, 0.5, 1, row_key="execution.protect_cq_delta_max",
        )
        self.spin_protect_delta.setPrefix("+")
        self.spin_sample_discard = make_percent_row(
            self.rows, card_auto, ExecutionConfig, "sample_saving_discard_factor", "사전 예측 중단 기준",
            _TOOLTIP_SAMPLE_DISCARD, 1, row_key="execution.sample_saving_discard_factor",
        )
        self.spin_early_stop = make_percent_row(
            self.rows, card_auto, ExecutionConfig, "early_stop_margin", "인코딩 중 자동 중단 기준",
            _TOOLTIP_EARLY_STOP, 5, row_key="execution.early_stop_margin",
        )
        section_advanced.add_widget(card_auto)

        card_detail = SettingsGroupCard(icon_name="file-text", title=_CARD_DETAIL_TITLE)
        self.combo_priority = make_combo_row(
            self.rows, card_detail, ExecutionConfig, "process_priority", "인코딩 우선순위",
            TOOLTIP_PRIORITY, PRIORITY_CHOICES, row_key="execution.process_priority",
        )
        self.combo_name_conflict = make_combo_row(
            self.rows, card_detail, ExecutionConfig, "name_conflict", "같은 이름이 있을 때",
            TOOLTIP_NAME_CONFLICT, NAME_CONFLICT_CHOICES, row_key="execution.name_conflict",
        )
        self.edit_tag_name = make_text_row(
            self.rows, card_detail, ExecutionConfig, "encode_tag_name", "재인코딩 태그 이름",
            TOOLTIP_TAG_NAME, width=_TEXT_EDIT_WIDTH, row_key=_TAG_NAME_KEY,
        )
        self.lbl_tag_error = QLabel("", card_detail)
        self.lbl_tag_error.setObjectName(STATUS_OBJECT_ERROR)
        self.lbl_tag_error.setWordWrap(True)
        self.lbl_tag_error.setVisible(False)
        card_detail.add_row(self.lbl_tag_error)
        section_advanced.add_widget(card_detail)
        body_layout.addWidget(section_advanced)
        body_layout.addStretch(1)

        self.rows[_CODEC_KEY].value_changed.connect(self._on_codec_row_changed)
        self.rows[_RESULT_POLICY_KEY].value_changed.connect(self._on_result_policy_changed)
        self.rows[_TAG_NAME_KEY].value_changed.connect(self._on_tag_name_edited)
        for key, row in self.rows.items():
            if key not in (_CODEC_KEY, _RESULT_POLICY_KEY, _TAG_NAME_KEY):
                row.value_changed.connect(self._on_row_changed)


    def _build_speed_row(self, card: SettingsGroupCard) -> tuple[ScrollSafeDoubleSpinBox, QPushButton]:
        """기준 인코딩 배속 행을 만듦. 값 입력과 시스템 자동 감지 아이콘 버튼을 한 줄에 둠"""
        speed_default = float(resolve_field_default(EncodeConfig, "encode_speed_factor"))
        speed_min, speed_max = resolve_field_bounds(EncodeConfig, "encode_speed_factor")
        speed_box = QWidget()
        speed_layout = QHBoxLayout(speed_box)
        speed_layout.setContentsMargins(0, 0, 0, 0)
        speed_layout.setSpacing(_SPEED_ROW_SPACING)

        spin = ScrollSafeDoubleSpinBox()
        spin.setRange(speed_min, speed_max)
        spin.setSingleStep(0.5)
        spin.setDecimals(1)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        spin.setSuffix(" 배속")
        spin.setFixedWidth(_SPEED_EDIT_WIDTH)
        button = create_icon_button(
            speed_box,
            TOOLTIP_SPEED_DETECT,
            self._on_detect_speed_clicked,
            accessible_name=_SPEED_BUTTON_LABEL,
            fixed_size=RESET_BUTTON_SIZE,
        )
        speed_layout.addWidget(spin)
        speed_layout.addWidget(button)

        row = SettingRow(
            key=_SPEED_KEY,
            title=_SPEED_TITLE,
            desc=SPEED_DESC,
            editor=speed_box,
            default_val=speed_default,
            unit="배속",
            on_reset=lambda: spin.setValue(speed_default),
            value_widget=spin,
        )
        self.rows[_SPEED_KEY] = row
        card.add_row(row)
        return spin, button

    @staticmethod
    def _new_card(layout: QVBoxLayout, icon_name: str, title: str) -> SettingsGroupCard:
        card = SettingsGroupCard(icon_name=icon_name, title=title, subtitle="")
        layout.addWidget(card)
        return card

    def apply_theme(self, palette: ThemePalette) -> None:
        """시스템 자동 감지 버튼과 상태 배지 아이콘을 현재 팔레트 색으로 갱신"""
        set_button_icon(
            self.btn_detect_encode_speed,
            _SPEED_BUTTON_ICON,
            palette=palette,
            size=RESET_ICON_SIZE,
            pad_right=0,
        )
        if self._capability is not None:
            self._set_status(self._capability)

    def set_baseline_cq(self, value: float) -> None:
        """인코딩 카드 하단에 현재 적용 화질 기준값을 표시"""
        self.lbl_baseline.setText(f"적용 화질 기준값: {value:.1f}")

    def load(self, cfg: ExecutionConfig, encode: EncodeConfig) -> None:
        """설정 값을 입력 위젯에 반영. 반영 중에는 changed를 보내지 않고 추천 배속도 바꾸지 않음"""
        was_loading = self._loading
        self._loading = True
        try:
            self.edit_ffmpeg.setText(cfg.ffmpeg_path)
            self.edit_ffprobe.setText(cfg.ffprobe_path)
            _select_data(self.combo_encode_codec, encode.encode_codec)
            self.spin_encode_speed.setValue(encode.encode_speed_factor)
            _select_data(self.combo_result_policy, cfg.result_policy)
            self.edit_archive_dir.setText(cfg.archive_dirname)
            self.edit_output_dir.setText(cfg.output_dirname)
            _select_data(self.combo_container, cfg.container_policy)
            self.spin_concurrent.setValue(cfg.concurrent_jobs)
            _select_data(self.combo_prevent_sleep, cfg.prevent_sleep)
            _select_data(self.combo_after_complete, cfg.after_complete)
            _select_data(self.combo_priority, cfg.process_priority)
            _select_data(self.combo_name_conflict, cfg.name_conflict)
            _select_data(self.combo_tag_mode, cfg.encode_tag_mode)
            self.spin_protect_delta.setValue(cfg.protect_cq_delta_max)
            self.spin_sample_discard.setValue(round(cfg.sample_saving_discard_factor * PERCENT_SCALE))
            self.spin_early_stop.setValue(round(cfg.early_stop_margin * PERCENT_SCALE))
            self._tag_name = cfg.encode_tag_name
            self.edit_tag_name.setText(cfg.encode_tag_name)
            self.lbl_tag_error.setVisible(False)
            self.set_baseline_cq(encode.baseline_cq)
            self._on_concurrent_value_changed(cfg.concurrent_jobs)
            self._refresh_result_rows()
            self._refresh_container_desc()
            self._refresh_codec_status()
        finally:
            self._loading = was_loading


    def read(self) -> ExecutionConfig:
        """현재 입력값을 ExecutionConfig로 돌려줌. 태그 이름은 마지막으로 검증된 값"""
        return ExecutionConfig(
            ffmpeg_path=self.edit_ffmpeg.text().strip(),
            ffprobe_path=self.edit_ffprobe.text().strip(),
            result_policy=str(self.combo_result_policy.currentData()),
            archive_dirname=self.edit_archive_dir.text().strip()
            or str(resolve_field_default(ExecutionConfig, "archive_dirname")),
            output_dirname=self.edit_output_dir.text().strip()
            or str(resolve_field_default(ExecutionConfig, "output_dirname")),
            container_policy=str(self.combo_container.currentData()),
            concurrent_jobs=self.spin_concurrent.value(),
            prevent_sleep=bool(self.combo_prevent_sleep.currentData()),
            after_complete=str(self.combo_after_complete.currentData()),
            process_priority=str(self.combo_priority.currentData()),
            name_conflict=str(self.combo_name_conflict.currentData()),
            encode_tag_mode=str(self.combo_tag_mode.currentData()),
            encode_tag_name=self._tag_name,
            protect_cq_delta_max=round(self.spin_protect_delta.value(), FLOAT_SETTING_ROUND_DIGITS),
            sample_saving_discard_factor=self.spin_sample_discard.value() / PERCENT_SCALE,
            early_stop_margin=self.spin_early_stop.value() / PERCENT_SCALE,
        )

    def encode_values(self) -> dict[str, object]:
        """목표 인코딩 방식과 기준 배속을 EncodeConfig 필드 이름으로 돌려줌"""
        return {
            "encode_codec": str(self.combo_encode_codec.currentData()),
            "encode_speed_factor": round(self.spin_encode_speed.value(), FLOAT_SETTING_ROUND_DIGITS),
        }

    def set_hints_visible(self, visible: bool) -> None:
        """쉬운 설명 보기 설정에 따라 이 페이지의 설명 줄을 켜거나 끔"""
        for row in self.rows.values():
            row.set_hint_visible(visible)

    def refresh_ffmpeg_status(self) -> None:
        """FFmpeg 경로를 확인해 상태 배지를 갱신. 사용할 수 없으면 고급 설정을 펼침"""
        path = self.edit_ffmpeg.text().strip()
        capability = self._probe(path)
        self._probed_path = path
        self._capability = capability
        self._set_status(capability)
        if not capability.usable and self.section_advanced is not None:
            self.section_advanced.set_expanded(True)
        self._refresh_codec_status()

    def _on_status_badge_clicked(self) -> None:
        """상태 배지를 누르면 지금 상태를 다시 확인하고 고급 설정을 펼침"""
        self.refresh_ffmpeg_status()
        if self.section_advanced is not None:
            self.section_advanced.set_expanded(True)

    def _set_status(self, capability: FfmpegCapability) -> None:
        object_name, text = ffmpeg_status(capability)
        self.btn_ffmpeg_status.setText(text)
        icon_name = "alert-triangle" if object_name == STATUS_OBJECT_ERROR else "check-circle"
        self.btn_ffmpeg_status.setIcon(get_icon(icon_name, size=RESET_ICON_SIZE))
        is_error = object_name == STATUS_OBJECT_ERROR
        self.btn_ffmpeg_status.setProperty("statusKind", "error" if is_error else "ok")
        self.btn_ffmpeg_status.setAccessibleName(f"FFmpeg 상태, {text}")
        _repolish(self.btn_ffmpeg_status)


    def _refresh_codec_status(self) -> None:
        """목표 인코딩 방식의 이 PC 사용 가능 여부 줄을 갱신하고, 문구가 없으면 줄을 숨김. 시험 중이면 끝날 때까지 주기적으로 다시 확인"""
        status = codec_status(self._capability, str(self.combo_encode_codec.currentData() or ""), self._trial)
        self.lbl_codec_status.setObjectName(status.object_name)
        self.lbl_codec_status.setText(status.text)
        self.lbl_codec_status.setVisible(bool(status.text))
        _repolish(self.lbl_codec_status)
        if status.pending:
            self._trial_poll.start()

    def showEvent(self, event: QShowEvent | None) -> None:
        """페이지가 보일 때 FFmpeg 경로가 마지막 확인 때와 다르면 다시 확인하고, 출력 형식 설명을 현재 방식에 맞춤"""
        if self.edit_ffmpeg.text().strip() != self._probed_path:
            self.refresh_ffmpeg_status()
        self._refresh_container_desc()
        super().showEvent(event)

    def _on_ffmpeg_editing_finished(self) -> None:
        if self.isVisible() and self.edit_ffmpeg.text().strip() != self._probed_path:
            self.refresh_ffmpeg_status()

    def _on_concurrent_value_changed(self, value: int) -> None:
        self.spin_concurrent.setSuffix(_AUTO_SUFFIX if value == 0 else "")

    def _on_codec_row_changed(self) -> None:
        """목표 인코딩 방식이 바뀌면 추천 배속과 출력 형식 설명을 맞추고 한 번만 저장"""
        if self._loading:
            return
        self._loading = True
        try:
            self._apply_recommended_speed(show_popup=False)
            self._refresh_container_desc()
            self._refresh_codec_status()
        finally:
            self._loading = False
        self._emit_changed()

    def _on_detect_speed_clicked(self) -> None:
        self._apply_recommended_speed(show_popup=True)

    def _apply_recommended_speed(self, *, show_popup: bool) -> None:
        codec = str(self.combo_encode_codec.currentData() or "")
        rec = detect_recommended_encode_speed(codec)
        self.btn_detect_encode_speed.setToolTip(rec.detail_summary)
        self.spin_encode_speed.setValue(rec.recommended_speed_factor)
        if show_popup:
            QToolTip.showText(
                self.btn_detect_encode_speed.mapToGlobal(self.btn_detect_encode_speed.rect().bottomLeft()),
                rec.detail_summary,
                self.btn_detect_encode_speed,
            )

    def _refresh_container_desc(self) -> None:
        codec = str(self.combo_encode_codec.currentData() or "")
        self.rows[_CONTAINER_KEY].set_desc(build_container_desc(get_encode_codec_family(codec)))

    def _on_result_policy_changed(self) -> None:
        self._refresh_result_rows()
        self.result_policy_changed.emit()
        self._on_row_changed()

    def _refresh_result_rows(self) -> None:
        """결과 파일 처리 방식에 쓰는 폴더 이름 행만 보여 줌(원본 교체는 폴더 이름이 필요 없음)"""
        policy = str(self.combo_result_policy.currentData())
        archive_visible = policy == RESULT_POLICY_ARCHIVE
        output_visible = policy == RESULT_POLICY_SEPARATE
        self.rows[_ARCHIVE_KEY].setVisible(archive_visible)
        self.rows[_OUTPUT_KEY].setVisible(output_visible)
        self._policy_hidden = {
            key
            for key, visible in ((_ARCHIVE_KEY, archive_visible), (_OUTPUT_KEY, output_visible))
            if not visible
        }

    def _on_row_changed(self) -> None:
        self._emit_changed()

    def _on_tag_name_edited(self) -> None:
        """태그 이름을 검증해 통과하면 저장 대상으로 반영하고, 실패하면 사유를 보이며 마지막 정상 값을 유지"""
        try:
            name = normalize_tag_name(self.edit_tag_name.text())
        except ValueError as error:
            self.lbl_tag_error.setText(str(error))
            self.lbl_tag_error.setVisible(True)
            return
        self.lbl_tag_error.setVisible(False)
        if name != self._tag_name:
            self._tag_name = name
            self._emit_changed()

    def _emit_changed(self) -> None:
        if not self._loading:
            self.changed.emit()


def _repolish(widget: QWidget) -> None:
    """objectName이나 속성 변경 뒤 스타일을 다시 적용해 오류 색과 기본 색이 바로 바뀌게 함"""
    style = widget.style()
    if style is not None:
        style.unpolish(widget)
        style.polish(widget)


def _select_data(combo: QComboBox, value: object) -> None:
    """데이터 값에 맞는 콤보 항목을 고름. 목록에 없는 값이면 현재 선택을 유지"""
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)

