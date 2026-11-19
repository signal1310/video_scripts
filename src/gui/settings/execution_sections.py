"""설정 대화상자의 재인코딩 탭: FFmpeg 상태, 인코딩, 결과 파일, 실행, 재인코딩 태그 행과 ExecutionConfig, 인코딩 설정 읽기, 쓰기 모듈

- 값은 ExecutionConfig와 EncodeConfig 일부로 읽고 쓰며, 사용자가 바꾸면 changed 신호를 보냄(대화상자가 자동 저장에 연결)
- 목표 인코딩 방식을 바꾸면 추천 배속을 먼저 맞추고 한 번만 저장. 출력 형식 설명은 목표 인코딩 방식에서 만듦
- FFmpeg 상태는 탭이 처음 보일 때와 FFmpeg 경로를 바꾼 뒤에만 확인(실행 파일을 불필요하게 띄우지 않음)
- 재인코딩 태그 이름은 검증을 통과한 값만 반영하고, 검증에 실패하면 마지막 정상 값을 유지
"""

from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QShowEvent
from PyQt6.QtWidgets import (
    QComboBox,
    QFrame,
    QLabel,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QToolTip,
    QVBoxLayout,
    QWidget,
)

from src.core.analysis.system_probe import detect_recommended_encode_speed
from src.core.config import (
    CONCURRENT_JOBS_MAX,
    CONTAINER_POLICY_KEEP,
    CONTAINER_POLICY_MP4,
    ENCODE_CODEC_CHOICES,
    NAME_CONFLICT_NUMBER,
    NAME_CONFLICT_SKIP,
    RESULT_POLICY_ARCHIVE,
    RESULT_POLICY_REPLACE,
    RESULT_POLICY_SEPARATE,
    EncodeConfig,
    ExecutionConfig,
)
from src.core.encode.encode_concurrency import AUTO_JOBS_CPU, AUTO_JOBS_HARDWARE
from src.core.encode.ffmpeg_capability import MIN_FFMPEG_VERSION, FfmpegCapability, probe_ffmpeg_capability
from src.core.encode_tag import (
    ENCODE_TAG_MODE_HIDDEN,
    ENCODE_TAG_MODE_OFF,
    ENCODE_TAG_MODE_VISIBLE,
    normalize_tag_name,
)
from src.core.encoder_specs import ENCODE_FAMILY_LABELS, get_encode_codec_family, keep_container_block_reason
from src.gui.settings.collapsible_section import CollapsibleSection
from src.gui.settings.setting_controls import (
    SCROLL_BODY_MARGINS,
    SCROLL_BODY_SPACING,
    TAB_INNER_MARGINS,
    TAB_INNER_SPACING,
    ScrollSafeDoubleSpinBox,
)
from src.gui.settings.setting_row import (
    SettingRow,
    make_combo_row,
    make_int_spin_row,
    make_text_row,
    resolve_field_bounds,
    resolve_field_default,
)
from src.gui.settings.settings_field_bindings import FLOAT_SETTING_ROUND_DIGITS
from src.gui.settings.settings_group_card import SettingsGroupCard

TAB_TITLE: str = "재인코딩"
_TAB_DESC: str = "변경 즉시 저장, 적용됩니다. 마우스 오버 시 세부 설명을 봅니다."
_PATH_EDIT_WIDTH: int = 260
_TEXT_EDIT_WIDTH: int = 172
_SPIN_STEP: int = 1
_SPEED_EDIT_WIDTH: int = 92
_AUTO_SUFFIX: str = " (자동)"
_STATUS_UNKNOWN_TEXT: str = "버전을 확인하지 못해 그대로 사용합니다"
_STATUS_OBJECT_ERROR: str = "ErrorHintLabel"
_STATUS_OBJECT_NORMAL: str = "SettingDesc"
_ADVANCED_TITLE: str = "고급 설정"
_ADVANCED_SUBTITLE: str = "(인코딩 우선순위, 같은 이름이 있을 때, 재인코딩 태그 이름)"
_CARD_RUN_TITLE: str = "실행"
_TAG_NAME_KEY: str = "encode_tag_name"
_CODEC_KEY: str = "encode.encode_codec"
_SPEED_KEY: str = "encode.encode_speed_factor"
_CONTAINER_KEY: str = "container_policy"
_RESULT_POLICY_KEY: str = "result_policy"
_ARCHIVE_KEY: str = "archive_dirname"
_OUTPUT_KEY: str = "output_dirname"
_SPEED_TITLE: str = "기준 인코딩 배속 (1080p 30fps)"
_SPEED_DESC: str = (
    "1080p 30fps 영상을 영상 길이의 몇 배 속도로 인코딩하는지 나타냅니다. 2.0배속이면 10분 영상에 약 5분이 걸립니다. "
    "해상도와 FPS가 큰 영상은 화소 처리량에 비례해 더 오래 걸리는 것으로 계산해 표와 하단 합계의 예상 시간에 반영합니다. 모르면 시스템 추천을 누르세요."
)
_TOOLTIP_SPEED_DETECT: str = (
    "현재 PC의 CPU 스레드와 GPU 하드웨어 인코더(NVENC/QSV/AMF) 지원 여부를 조회해\n"
    "1080p 30fps 기준 추천 인코딩 배속을 자동으로 입력합니다."
)
# - 출력 형식 설명에서 MP4로 바뀌는 대표 확장자. 나머지 형식은 설명 끝에 함께 묶음
_CONVERTED_FORMAT_LABELS: tuple[tuple[str, str], ...] = (
    ("avi", "AVI"),
    ("wmv", "WMV"),
    ("flv", "FLV"),
    ("webm", "WebM"),
    ("ts", "TS"),
)

_RESULT_POLICY_CHOICES: tuple[tuple[str, str], ...] = (
    ("원본 보관 후 교체 (기본)", RESULT_POLICY_ARCHIVE),
    ("별도 폴더에 저장", RESULT_POLICY_SEPARATE),
    ("원본 교체 (되돌릴 수 없음)", RESULT_POLICY_REPLACE),
)
_CONTAINER_CHOICES: tuple[tuple[str, str], ...] = (
    ("원본 형식 유지 (기본)", CONTAINER_POLICY_KEEP),
    ("항상 MP4", CONTAINER_POLICY_MP4),
)
_PREVENT_SLEEP_CHOICES: tuple[tuple[str, bool], ...] = (
    ("사용 (기본)", True),
    ("사용 안 함", False),
)
_AFTER_COMPLETE_CHOICES: tuple[tuple[str, str], ...] = (
    ("없음 (기본)", "none"),
    ("절전", "sleep"),
    ("시스템 종료", "shutdown"),
    ("앱 종료", "exit"),
)
_PRIORITY_CHOICES: tuple[tuple[str, str], ...] = (
    ("높음", "high"),
    ("보통 초과", "above_normal"),
    ("보통 (기본)", "normal"),
    ("보통 미만", "below_normal"),
    ("낮음", "low"),
)
_NAME_CONFLICT_CHOICES: tuple[tuple[str, str], ...] = (
    ("건너뜀", NAME_CONFLICT_SKIP),
    ("번호 붙여 저장 (기본)", NAME_CONFLICT_NUMBER),
)
_TAG_MODE_CHOICES: tuple[tuple[str, str], ...] = (
    ("태그와 숨은 기록 (기본)", ENCODE_TAG_MODE_VISIBLE),
    ("숨은 기록만", ENCODE_TAG_MODE_HIDDEN),
    ("기록 안 함", ENCODE_TAG_MODE_OFF),
)

_TOOLTIP_FFMPEG: str = (
    "재인코딩에 쓰는 ffmpeg 실행 파일의 경로입니다. 비워 두면 PATH에서 찾습니다. "
    f"찾지 못하거나 버전이 {MIN_FFMPEG_VERSION[0]}.{MIN_FFMPEG_VERSION[1]}보다 낮으면 "
    "아래 FFmpeg 상태에 사유가 나오고 재인코딩을 시작할 수 없습니다."
)
_TOOLTIP_FFPROBE: str = "영상 정보를 읽는 ffprobe 실행 파일의 경로입니다. 비워 두면 PATH에서 찾습니다."
_TOOLTIP_CODEC: str = (
    "실제 재인코딩에 쓰는 인코더입니다. 판정(출력 BR 비율, 과화질), 예상 용량, 예상 시간도 이 값을 따릅니다. "
    "방식을 바꾸면 현재 PC에 맞는 1080p 예상 배속을 다시 추천합니다."
)
_TOOLTIP_RESULT_POLICY: str = (
    "재인코딩 결과를 원본과 어떻게 놓을지 정합니다. 결과는 검사를 통과한 뒤에만 옮깁니다. "
    "원본 보관 후 교체: 원본을 보관 폴더로 옮긴 뒤 결과를 원래 이름으로 둡니다(기본). "
    "별도 폴더에 저장: 원본은 그대로 두고 결과를 별도 출력 폴더에 저장합니다. "
    "원본 교체: 원본을 결과로 바로 바꾸며 원본은 보관되지 않아 되돌릴 수 없습니다."
)
_TOOLTIP_ARCHIVE_DIR: str = (
    "원본 보관 후 교체일 때 원본을 옮겨 둘 폴더 이름입니다. 원본 영상과 같은 폴더 안에 만듭니다. "
    "기본값은 _원본보관입니다."
)
_TOOLTIP_OUTPUT_DIR: str = (
    "별도 폴더에 저장할 때 결과를 모을 폴더 이름입니다. 작업 폴더 바로 아래에 만들고 원본의 하위 폴더 구조를 "
    "유지합니다. 기본값은 _재인코딩결과입니다."
)
_TOOLTIP_CONCURRENT: str = (
    f"동시에 인코딩할 영상 수입니다. 0이면 인코더에 맞춰 자동으로 정하며, 하드웨어 인코더는 {AUTO_JOBS_HARDWARE}개, "
    f"소프트웨어 인코더는 {AUTO_JOBS_CPU}개입니다. 수를 늘려도 빨라지지 않을 수 있고, 메모리와 CPU 사용이 늘어납니다. "
    f"최대 {CONCURRENT_JOBS_MAX}개입니다."
)
_TOOLTIP_PREVENT_SLEEP: str = (
    "재인코딩 중 PC가 절전 모드로 들어가지 않게 합니다. 화면 끄기는 막지 않습니다. "
    "사용 안 함이면 긴 재인코딩 중 PC가 절전으로 들어갈 수 있습니다."
)
_TOOLTIP_AFTER_COMPLETE: str = (
    "모든 재인코딩이 끝난 뒤 할 일입니다. 없음: 아무것도 하지 않습니다. 절전: PC를 절전 모드로 바꿉니다. "
    "최대 절전이 켜져 있으면 최대 절전으로 들어갈 수 있습니다. 시스템 종료: PC를 끕니다. 앱 종료: 이 프로그램을 끕니다."
)
_TOOLTIP_PRIORITY: str = (
    "재인코딩 프로세스의 CPU 우선순위입니다. 높을수록 인코딩이 빨라질 수 있지만 다른 프로그램이 느려질 수 있습니다. "
    "이름은 Windows 작업 관리자의 우선순위 단계와 같습니다."
)
_TOOLTIP_NAME_CONFLICT: str = (
    "결과 파일 이름이 이미 있을 때의 처리입니다. 건너뜀: 그 영상은 재인코딩하지 않고 건너뜀으로 남깁니다. "
    "번호 붙여 저장: 파일 이름 뒤에 번호를 붙여 저장하며, 기존 파일은 덮어쓰지 않습니다."
)
_TOOLTIP_TAG_MODE: str = (
    "결과 파일에 재인코딩 표시를 남기는 방식입니다. 태그와 숨은 기록: 탐색기 태그와 숨은 기록을 모두 남깁니다(기본). "
    "숨은 기록만: 탐색기 태그 칸에는 보이지 않고 숨은 기록만 남깁니다. "
    "기록 안 함: 재인코딩 태그와 숨은 기록을 남기지 않으며, 같은 영상은 재인코딩 이력으로만 가려냅니다. "
    "FFmpeg가 쓰는 인코더 정보 태그는 그대로 남습니다."
)
_TOOLTIP_TAG_NAME: str = (
    "탐색기 태그 칸에 보이는 키워드입니다. 탐색기에서 검색하거나 묶어 볼 때 쓰입니다. "
    "공백을 뺀 1자 이상, 64자 이하이며 세미콜론(;)과 제어 문자는 쓸 수 없습니다. "
    "바꾸기 전에 쓴 이름으로 된 재인코딩 기록도 계속 인식합니다. 기본값은 re_encoded입니다."
)


def attach_execution_tab(
    tab_widget: QTabWidget, cfg: ExecutionConfig, encode: EncodeConfig
) -> "ExecutionTabPanel":
    """설정 대화상자 탭 위젯 끝에 재인코딩 탭을 추가하고 패널을 돌려줌"""
    panel = ExecutionTabPanel(cfg, encode, tab_widget)
    tab_widget.addTab(panel, TAB_TITLE)
    return panel


def build_container_desc(family: str) -> str:
    """목표 인코딩 방식 계열에서 출력 형식 설명 문장을 만듦. 원본 형식이 바뀌는 형식과 사유는 결정 8 규칙 함수에서 가져옴"""
    converted = [
        f"{label}({reason})"
        for ext, label in _CONVERTED_FORMAT_LABELS
        if (reason := keep_container_block_reason(ext, family)) is not None
    ]
    converted_text = ", ".join(converted) if converted else "없음"
    return (
        f"결과 파일의 형식입니다. 현재 목표 인코딩 방식({ENCODE_FAMILY_LABELS.get(family, family)}) 기준입니다. "
        "원본 형식 유지(기본): MP4, MOV, MKV 원본은 같은 형식으로 저장하고, 그 밖의 형식은 MP4로 저장합니다. "
        f"MP4로 바뀌는 형식(원본 형식 유지일 때): {converted_text}. "
        "항상 MP4: 모든 결과를 MP4로 저장합니다. "
        "비트맵 자막(PGS, VobSub 등)이 있는 영상은 두 방식 모두 MKV로 저장합니다. MP4에 저장할 수 없기 때문입니다."
    )


class ExecutionTabPanel(QWidget):
    """재인코딩 탭 본문. 값은 ExecutionConfig와 EncodeConfig 일부로 읽고 쓰며, 사용자가 바꾸면 changed를 보냄"""

    changed = pyqtSignal()


    def __init__(
        self,
        cfg: ExecutionConfig,
        encode: EncodeConfig,
        parent: QWidget | None = None,
        probe: Callable[[str], FfmpegCapability] = probe_ffmpeg_capability,
    ) -> None:
        super().__init__(parent)
        self._probe: Callable[[str], FfmpegCapability] = probe
        self._loading: bool = True
        self._probed_path: str | None = None
        self._tag_name: str = cfg.encode_tag_name
        self.rows: dict[str, SettingRow] = {}
        self._build_ui()
        self._loading = False
        self.load(cfg, encode)

    @property
    def row_keys(self) -> frozenset[str]:
        """행이 있는 설정 키 집합(ExecutionConfig 필드와 인코딩 설정 두 개)"""
        return frozenset(self.rows)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(*TAB_INNER_MARGINS)
        root.setSpacing(TAB_INNER_SPACING)
        lbl_desc = QLabel(_TAB_DESC, self)
        lbl_desc.setObjectName("SettingDesc")
        lbl_desc.setWordWrap(True)
        root.addWidget(lbl_desc)

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

        card_ffmpeg = self._new_card(body_layout, "film", "FFmpeg 상태")
        self.lbl_status = QLabel("", card_ffmpeg)
        self.lbl_status.setWordWrap(True)
        card_ffmpeg.add_row(self.lbl_status)
        self.edit_ffmpeg = make_text_row(
            self.rows, card_ffmpeg, ExecutionConfig, "ffmpeg_path", "FFmpeg 경로", _TOOLTIP_FFMPEG,
            width=_PATH_EDIT_WIDTH,
        )
        self.edit_ffprobe = make_text_row(
            self.rows, card_ffmpeg, ExecutionConfig, "ffprobe_path", "FFprobe 경로", _TOOLTIP_FFPROBE,
            width=_PATH_EDIT_WIDTH,
        )
        self.edit_ffmpeg.editingFinished.connect(self._on_ffmpeg_editing_finished)

        card_encode = self._new_card(body_layout, "gauge", "인코딩")
        self.combo_encode_codec = make_combo_row(
            self.rows, card_encode, EncodeConfig, "encode_codec", "목표 인코딩 방식", _TOOLTIP_CODEC,
            ENCODE_CODEC_CHOICES, row_key=_CODEC_KEY,
        )
        self.spin_encode_speed, self.btn_detect_encode_speed = self._build_speed_row(card_encode)

        card_result = self._new_card(body_layout, "folder", "결과 파일")
        self.combo_result_policy = make_combo_row(
            self.rows, card_result, ExecutionConfig, _RESULT_POLICY_KEY, "결과 파일 처리", _TOOLTIP_RESULT_POLICY,
            _RESULT_POLICY_CHOICES,
        )
        self.edit_archive_dir = make_text_row(
            self.rows, card_result, ExecutionConfig, _ARCHIVE_KEY, "원본 보관 폴더 이름", _TOOLTIP_ARCHIVE_DIR,
            width=_TEXT_EDIT_WIDTH,
        )
        self.edit_output_dir = make_text_row(
            self.rows, card_result, ExecutionConfig, _OUTPUT_KEY, "별도 출력 폴더 이름", _TOOLTIP_OUTPUT_DIR,
            width=_TEXT_EDIT_WIDTH,
        )
        self.combo_container = make_combo_row(
            self.rows, card_result, ExecutionConfig, _CONTAINER_KEY, "출력 형식", "",
            _CONTAINER_CHOICES,
        )

        card_run = self._new_card(body_layout, "zap", _CARD_RUN_TITLE)
        self.spin_concurrent = make_int_spin_row(
            self.rows, card_run, ExecutionConfig, "concurrent_jobs", "동시 재인코딩 수", _TOOLTIP_CONCURRENT,
            _SPIN_STEP,
        )
        self.spin_concurrent.valueChanged.connect(self._on_concurrent_value_changed)
        self.combo_prevent_sleep = make_combo_row(
            self.rows, card_run, ExecutionConfig, "prevent_sleep", "절전 방지", _TOOLTIP_PREVENT_SLEEP,
            _PREVENT_SLEEP_CHOICES,
        )
        self.combo_after_complete = make_combo_row(
            self.rows, card_run, ExecutionConfig, "after_complete", "완료 후 동작", _TOOLTIP_AFTER_COMPLETE,
            _AFTER_COMPLETE_CHOICES,
        )

        card_tag = self._new_card(body_layout, "file-text", "재인코딩 태그")
        self.combo_tag_mode = make_combo_row(
            self.rows, card_tag, ExecutionConfig, "encode_tag_mode", "재인코딩 태그 기록", _TOOLTIP_TAG_MODE,
            _TAG_MODE_CHOICES,
        )
        section_advanced = CollapsibleSection(title=_ADVANCED_TITLE, subtitle=_ADVANCED_SUBTITLE, is_expanded=False)
        card_advanced_run = SettingsGroupCard(icon_name="zap", title=_CARD_RUN_TITLE, subtitle="")
        self.combo_priority = make_combo_row(
            self.rows, card_advanced_run, ExecutionConfig, "process_priority", "인코딩 우선순위", _TOOLTIP_PRIORITY,
            _PRIORITY_CHOICES,
        )
        self.combo_name_conflict = make_combo_row(
            self.rows, card_advanced_run, ExecutionConfig, "name_conflict", "같은 이름이 있을 때", _TOOLTIP_NAME_CONFLICT,
            _NAME_CONFLICT_CHOICES,
        )
        section_advanced.add_widget(card_advanced_run)
        card_tag_name = SettingsGroupCard(icon_name="sliders", title="재인코딩 태그 이름", subtitle="")
        self.edit_tag_name = make_text_row(
            self.rows, card_tag_name, ExecutionConfig, _TAG_NAME_KEY, "재인코딩 태그 이름", _TOOLTIP_TAG_NAME,
            width=_TEXT_EDIT_WIDTH,
        )
        self.lbl_tag_error = QLabel("", card_tag_name)
        self.lbl_tag_error.setObjectName(_STATUS_OBJECT_ERROR)
        self.lbl_tag_error.setWordWrap(True)
        self.lbl_tag_error.setVisible(False)
        card_tag_name.add_row(self.lbl_tag_error)
        section_advanced.add_widget(card_tag_name)
        body_layout.addWidget(section_advanced)
        body_layout.addStretch(1)

        self.rows[_CODEC_KEY].value_changed.connect(self._on_codec_row_changed)
        self.rows[_RESULT_POLICY_KEY].value_changed.connect(self._on_result_policy_changed)
        self.rows[_TAG_NAME_KEY].value_changed.connect(self._on_tag_name_edited)
        for key, row in self.rows.items():
            if key not in (_CODEC_KEY, _RESULT_POLICY_KEY, _TAG_NAME_KEY):
                row.value_changed.connect(self._on_row_changed)

    def _build_speed_row(self, card: SettingsGroupCard) -> tuple[ScrollSafeDoubleSpinBox, QPushButton]:
        """기준 인코딩 배속 행을 만들고 시스템 추천 버튼을 연결"""
        speed_default = float(resolve_field_default(EncodeConfig, "encode_speed_factor"))
        speed_min, speed_max = resolve_field_bounds(EncodeConfig, "encode_speed_factor")
        speed_box = QWidget()
        speed_layout = QVBoxLayout(speed_box)
        speed_layout.setContentsMargins(0, 0, 0, 0)
        speed_layout.setSpacing(4)

        spin = ScrollSafeDoubleSpinBox()
        spin.setRange(speed_min, speed_max)
        spin.setSingleStep(0.5)
        spin.setDecimals(1)
        spin.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        spin.setSuffix(" 배속")
        spin.setFixedWidth(_SPEED_EDIT_WIDTH)
        button = QPushButton("시스템 추천")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setToolTip(_TOOLTIP_SPEED_DETECT)
        button.clicked.connect(self._on_detect_speed_clicked)
        speed_layout.addWidget(spin)
        speed_layout.addWidget(button)

        row = SettingRow(
            key=_SPEED_KEY,
            title=_SPEED_TITLE,
            desc=_SPEED_DESC,
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
            self._tag_name = cfg.encode_tag_name
            self.edit_tag_name.setText(cfg.encode_tag_name)
            self.lbl_tag_error.setVisible(False)
            self._on_concurrent_value_changed(cfg.concurrent_jobs)
            self._refresh_result_rows()
            self._refresh_container_desc()
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
        )

    def encode_values(self) -> dict[str, object]:
        """목표 인코딩 방식과 기준 배속을 EncodeConfig 필드 이름으로 돌려줌"""
        return {
            "encode_codec": str(self.combo_encode_codec.currentData()),
            "encode_speed_factor": round(self.spin_encode_speed.value(), FLOAT_SETTING_ROUND_DIGITS),
        }

    def set_hints_visible(self, visible: bool) -> None:
        """쉬운 설명 보기 설정에 따라 이 탭의 설명 줄을 켜거나 끔"""
        for row in self.rows.values():
            row.set_hint_visible(visible)

    def refresh_ffmpeg_status(self) -> None:
        """FFmpeg 경로를 확인해 상태 문구를 갱신. 사용할 수 없으면 사유를 오류 색으로 표시"""
        path = self.edit_ffmpeg.text().strip()
        capability = self._probe(path)
        self._probed_path = path
        if not capability.usable:
            self._set_status(_STATUS_OBJECT_ERROR, f"사용할 수 없음: {capability.reason}")
        elif capability.version is None:
            self._set_status(_STATUS_OBJECT_NORMAL, f"사용 가능: {_STATUS_UNKNOWN_TEXT}")
        else:
            major, minor = capability.version
            self._set_status(_STATUS_OBJECT_NORMAL, f"사용 가능: FFmpeg {major}.{minor}")

    def _set_status(self, object_name: str, text: str) -> None:
        self.lbl_status.setObjectName(object_name)
        self.lbl_status.setText(text)
        style = self.lbl_status.style()
        if style is not None:
            style.unpolish(self.lbl_status)
            style.polish(self.lbl_status)

    def showEvent(self, event: QShowEvent | None) -> None:
        """탭이 보일 때 FFmpeg 경로가 마지막 확인 때와 다르면 다시 확인하고, 출력 형식 설명을 현재 방식에 맞춤"""
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
        self._on_row_changed()

    def _refresh_result_rows(self) -> None:
        """결과 파일 처리 방식에 쓰는 폴더 이름 행만 보여 줌(원본 교체는 폴더 이름이 필요 없음)"""
        policy = str(self.combo_result_policy.currentData())
        self.rows[_ARCHIVE_KEY].setVisible(policy == RESULT_POLICY_ARCHIVE)
        self.rows[_OUTPUT_KEY].setVisible(policy == RESULT_POLICY_SEPARATE)

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


def _select_data(combo: QComboBox, value: object) -> None:
    """데이터 값에 맞는 콤보 항목을 고름. 목록에 없는 값이면 현재 선택을 유지"""
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)
