"""인코딩 진행 창의 영상 카드: 이름, 일시중지와 중지 버튼, 진행 막대, 수치, 순간 비트레이트 그래프

- 카드 하나는 진행 창의 탭 하나에 들어감. 탭 제목은 순서 번호와 줄인 파일 이름
- 화면 스레드에서만 쓴다
"""

from collections.abc import Callable
from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.encode.encode_progress import ProgressSample
from src.core.encode.encode_progress_values import (
    bytes_to_mb,
    frames_text,
    quality_text,
    resolution_text,
    size_text,
)
from src.core.encode.encode_progress_view import (
    NO_VALUE_TEXT,
    BitrateSeries,
    clock_text,
    kbps_text,
    remaining_sec,
    speed_text,
)
from src.core.quality_target import format_cq_value
from src.gui.dialogs.encode_graph_legend import GraphLegend
from src.gui.styles.icons import create_icon_button, get_icon, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.widgets.bitrate_graph import BitrateGraph
from src.service.encode.encode_events import (
    PHASE_RETRY,
    PHASE_SAMPLE_PREDICT,
    PHASE_SAMPLING,
    TuningPhase,
)
from src.service.encode.encode_job import EncodeJob

PROGRESS_STEPS: int = 1000
ICON_BUTTON_PX: int = 28
INFO_ICON_PX: int = 14
CARD_COLUMNS: int = 2
CARD_BAR_HEIGHT_PX: int = 8
CARD_BAR_RADIUS_PX: int = 1
CARD_GRID_HSPACING_PX: int = 10
# - 값 칸은 권장 폭을 무시하고 늘림 비율로 폭을 받되, 한 줄 값이 읽히는 최소 폭만 지킴
VALUE_LABEL_MIN_WIDTH_PX: int = 60
# - 본 인코딩 전 준비 단계. 이 단계의 진행 막대는 팔레트 강조 주황색으로 채워 본 인코딩과 구분
PRE_ENCODE_STAGES: tuple[str, ...] = (PHASE_SAMPLE_PREDICT, PHASE_SAMPLING, PHASE_RETRY)
TAB_NAME_MAX_CHARS: int = 20
CARD_TITLE_MAX_CHARS: int = 28
CARD_CAPTIONS: tuple[str, ...] = (
    "진행 위치",
    "인코딩 속도",
    "프레임 수",
    "순간 품질값",
    "경과 시간",
    "남은 시간",
    "출력 크기",
    "예상 최종 크기",
    "판정 예상 용량",
    "목표 품질값",
    "출력 해상도",
    "인코더",
    "순간 비트레이트",
)
# - TERMINOLOGY.md 6.8절과 11.1절 풀이를 재사용한 항목별 기본 툴팁
CAPTION_TIPS: tuple[str, ...] = (
    "전체 영상 길이 중 현재까지 인코딩이 진행된 시각입니다.",
    "원본 재생 시간 대비 인코딩 속도(배속)와 초당 처리 프레임 수(fps)입니다.",
    "지금까지 인코딩을 마친 화면(프레임)의 총 개수입니다.",
    "인코딩 중 실시간 품질값입니다. 장면 복잡도에 따라 목표 품질값 안팎에서 조금씩 바뀝니다.",
    "이 영상의 인코딩을 시작한 뒤 흐른 시간입니다. 일시중지한 시간은 빼고 셉니다.",
    "현재 인코딩 속도를 기준으로 남은 분량을 마치는 데 걸릴 예상 시간입니다.",
    "지금까지 임시 결과 파일에 기록된 현재 용량입니다.",
    "현재까지 쓴 크기를 진행률로 나눠 환산한 최종 출력 크기 추정입니다.",
    "분석 단계에서 계산한 예상 용량입니다. 키프레임 인코딩 영상은 이 값을 목표로 품질값을 맞춥니다.",
    "다른 프로그램의 CQ(NVENC), CRF(x264, x265)에 해당합니다. 낮을수록 고화질이며, 같은 값이어도 인코더에 따라 결과 크기와 화질이 다릅니다.",
    "결과 영상의 가로x세로 화소 크기입니다.",
    "실제 인코딩에 쓰는 인코더입니다.",
    "지금 인코딩 중인 구간의 초당 데이터량(kbps)입니다. 움직임이 많은 장면일수록 올라갑니다.",
)
SAMPLE_WAIT_TIP: str = "품질 시험 중에는 측정하지 않습니다. 본 인코딩이 시작되면 표시됩니다."
SAMPLE_POSITION_TIP: str = "목표 크기에 맞는 품질값을 찾기 위해 대표 구간 5개를 먼저 인코딩하고 있습니다."
SAMPLE_PREDICT_TIP: str = "본 인코딩 전에 예상 크기를 확인하기 위해 대표 구간을 먼저 인코딩하고 있습니다."
RETRY_POSITION_TIP: str = "이전 시도에서 예상 크기가 목표를 넘어 품질값을 높여 처음부터 다시 인코딩하고 있습니다."
GRAPH_TITLE_TIP: str = "시간에 따른 초당 데이터량(비트레이트) 변화를 보여 줍니다. 오른쪽 안내 아이콘에서 선 모양별 뜻을 확인할 수 있습니다."
NO_VALUE_TIP: str = "인코딩을 시작한 지 얼마 안 되었거나 이 영상에서는 측정할 수 없습니다."
NO_POSITION_TIP: str = (
    "진행 위치 시각을 알 수 없는 영상입니다(MKV, 커버 MP4). "
    "진행 막대와 남은 시간은 프레임 수로 계산했습니다. 예상 최종 크기는 계산하지 않습니다."
)
NO_EXPECTED_TIP: str = "진행 위치가 없어 예상 최종 크기를 계산하지 않았습니다."
ITEM_PAUSE_TIP: str = "이 영상을 일시중지합니다. 멈춘 동안 GPU 인코더 세션을 계속 사용합니다."
ITEM_RESUME_TIP: str = "멈춘 영상을 이어서 인코딩합니다."
ITEM_STOP_TIP: str = "이 영상을 중지합니다. 대기로 돌아가며 확인 창을 한 번 거칩니다."
LEGEND_TIP: str = "실선: 순간 비트레이트, 회색 실선: 원본\n파선: 최대 비트레이트\n점선: 최고값\n일점쇄선: 평균\n빗금: 일시중지 구간"
LONG_PAUSE_CAUSE: str = "멈춘 동안 GPU 인코더 세션을 계속 사용해 그래픽 드라이버나 다른 작업에 영향을 줄 수 있으며,"
LONG_PAUSE_TEXT: str = f'{LONG_PAUSE_CAUSE} 오래 멈출 때는 <a href="stop-after">현재 영상 후 중지로 전환</a>을 권장합니다.'


def tab_title(order: int, name: str) -> str:
    """탭 제목. 순서 번호를 앞에 붙이고, 긴 파일 이름은 앞뒤를 남기고 가운데를 줄임표로 줄임"""
    return f"{order}. {shorten_middle(name, TAB_NAME_MAX_CHARS)}"


def shorten_middle(text: str, max_chars: int) -> str:
    """max_chars 이하로 줄인 문자열. 앞쪽을 조금 더 남기고 가운데를 줄임표로 바꿈"""
    if len(text) <= max_chars:
        return text
    keep = max_chars - 1
    head = (keep + 1) // 2
    tail = keep - head
    return text[:head] + "…" + (text[-tail:] if tail > 0 else "")


@dataclass(frozen=True, slots=True)
class CardField:
    """카드 칸 하나의 표시 문구와, 값이 없을 때 툴팁으로 보일 사유(없으면 빈 문자열)"""

    text: str
    tip: str = ""


def card_fields(
    sample: ProgressSample,
    job: EncodeJob,
    elapsed_sec: float,
    fraction: float | None,
    instant_kbps: float | None,
    projected_bytes: int | None,
    encoder_label: str,
    phase: TuningPhase | None = None,
) -> tuple[CardField, ...]:
    """진행 이벤트 값과 계획 입력으로 카드 칸 13개를 CARD_CAPTIONS 순서대로 만듦. 값이 없으면 정보 없음과 사유"""
    position_known = sample.out_time_sec is not None
    remaining = remaining_sec(elapsed_sec, fraction)
    total_mb = None if sample.total_size_bytes is None else bytes_to_mb(sample.total_size_bytes)
    projected_mb = None if projected_bytes is None else bytes_to_mb(projected_bytes)
    no_projection_tip = NO_VALUE_TIP if position_known else NO_EXPECTED_TIP
    pos_field = _position_field(sample, job, position_known, phase)
    target_q_field = _target_quality_field(job, phase)
    return (
        pos_field,
        _field(_speed_text(sample), NO_VALUE_TIP, CAPTION_TIPS[1]),
        _field(None if sample.frame is None else frames_text(sample.frame), NO_VALUE_TIP, CAPTION_TIPS[2]),
        _field(None if sample.quality is None else quality_text(sample.quality), NO_VALUE_TIP, CAPTION_TIPS[3]),
        CardField(clock_text(elapsed_sec), CAPTION_TIPS[4]),
        _field(None if remaining is None else clock_text(remaining), NO_VALUE_TIP, CAPTION_TIPS[5]),
        _field(None if total_mb is None else size_text(total_mb), NO_VALUE_TIP, CAPTION_TIPS[6]),
        _field(None if projected_mb is None else size_text(projected_mb), no_projection_tip, CAPTION_TIPS[7]),
        _field(None if job.expected_after_mb is None else size_text(job.expected_after_mb), NO_VALUE_TIP, CAPTION_TIPS[8]),
        target_q_field,
        CardField(resolution_text(job.output_size), CAPTION_TIPS[10]),
        CardField(encoder_label, CAPTION_TIPS[11]),
        _field(None if instant_kbps is None else kbps_text(instant_kbps), NO_VALUE_TIP, CAPTION_TIPS[12]),
    )


def _position_field(
    sample: ProgressSample, job: EncodeJob, position_known: bool, phase: TuningPhase | None
) -> CardField:
    if phase is not None and phase.stage == PHASE_SAMPLE_PREDICT:
        return CardField(
            f"사전 예측 ({phase.segment_done}/{phase.segment_total})",
            f"{SAMPLE_PREDICT_TIP}\n대표 구간 {phase.segment_done}/{phase.segment_total}을 마쳤습니다.",
        )
    if phase is not None and phase.stage == PHASE_SAMPLING:
        return CardField(
            f"품질 시험 {phase.sample_pass}/{phase.sample_total} ({phase.segment_done}/{phase.segment_total})",
            f"{SAMPLE_POSITION_TIP}\n지금 {phase.sample_pass}/{phase.sample_total}번째 시험이며 "
            f"대표 구간 {phase.segment_done}/{phase.segment_total}을 마쳤습니다.",
        )
    if not position_known:
        return CardField(NO_VALUE_TEXT, NO_POSITION_TIP)
    base = f"{clock_text(sample.out_time_sec)} / {clock_text(job.source_duration_sec)}"
    if phase is not None and phase.retry_count > 0:
        return CardField(f"{base} (재시도 {phase.retry_count}/1)", RETRY_POSITION_TIP)
    return CardField(base, CAPTION_TIPS[0])


def _target_quality_field(job: EncodeJob, phase: TuningPhase | None) -> CardField:
    if phase is not None and phase.stage == PHASE_SAMPLE_PREDICT:
        return CardField(quality_text(job.quality), CAPTION_TIPS[9])
    if phase is not None and phase.stage == PHASE_SAMPLING:
        base_txt = quality_text(phase.baseline_quality)
        return CardField(
            f"계산 중 (기준 {base_txt})",
            f"판정 기준값 {base_txt}을 바탕으로 목표 크기에 맞는 품질값을 계산하고 있습니다.\n{CAPTION_TIPS[9]}",
        )
    if phase is not None and (phase.current_quality > phase.baseline_quality or phase.retry_count > 0):
        cur_txt = quality_text(phase.current_quality)
        base_txt = quality_text(phase.baseline_quality)
        reason = (
            f"재시도로 품질값을 {quality_text(phase.previous_quality)}에서 {cur_txt}로 높였습니다."
            if phase.stage == PHASE_RETRY and phase.previous_quality is not None
            else f"키프레임 인코딩 목표 크기에 맞추기 위해 품질 시험으로 품질값을 {base_txt}에서 {cur_txt}로 높였습니다."
        )
        return CardField(f"{cur_txt} (기준 {base_txt})", f"{reason}\n{CAPTION_TIPS[9]}")
    return CardField(quality_text(job.quality), CAPTION_TIPS[9])


def _speed_text(sample: ProgressSample) -> str | None:
    """인코딩 속도 칸: 배속과 초당 프레임 수를 함께 씀. 배속이 없으면 값 없음"""
    if sample.speed is None:
        return None
    if sample.fps is None:
        return speed_text(sample.speed)
    return f"{speed_text(sample.speed)}, {round(sample.fps)}fps"


def _field(text: str | None, missing_tip: str, caption_tip: str = "") -> CardField:
    """값이 있으면 항목 설명 툴팁을, 없으면 정보 없음과 사유 툴팁"""
    return CardField(NO_VALUE_TEXT, missing_tip) if text is None else CardField(text, caption_tip)


def bar_style(palette: ThemePalette, *, pre_encode: bool) -> str:
    """진행 막대 채움 스타일. 본 인코딩 전 준비 단계는 강조 주황, 본 인코딩은 기본 채움색"""
    fill = palette.accent_orange if pre_encode else palette.progress_fill
    return (
        f"QProgressBar {{ background: {palette.progress_track_bg}; border: none;"
        f" border-radius: {CARD_BAR_RADIUS_PX}px; }}"
        f"QProgressBar::chunk {{ background: {fill}; border-radius: {CARD_BAR_RADIUS_PX}px; }}"
    )


def field_columns() -> tuple[int, ...]:
    """카드 격자의 값 열 번호. 캡션 열은 내용 폭, 값 열은 남는 폭을 나눠 13pt에서도 한 줄에 들어감"""
    return tuple(column * 2 + 1 for column in range(CARD_COLUMNS))


class JobCard(QFrame):
    """작업 하나의 이름, 일시중지와 중지 버튼, 진행 막대, 수치, 비트레이트 그래프

    - activated: 카드 더블클릭, preview_requested: 카드에 포커스가 있을 때 스페이스바
    """

    pause_toggled = pyqtSignal()
    stop_requested = pyqtSignal()
    stop_after_requested = pyqtSignal()
    activated = pyqtSignal()
    preview_requested = pyqtSignal()

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EncodeJobCard")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.installEventFilter(self)
        self._paused: bool = False
        self.series: BitrateSeries = BitrateSeries()
        self._pause_btn = create_icon_button(
            self,
            ITEM_PAUSE_TIP,
            lambda: self.pause_toggled.emit(),
            accessible_name="영상 일시중지",
            fixed_size=ICON_BUTTON_PX,
        )
        self._stop_btn = create_icon_button(
            self,
            ITEM_STOP_TIP,
            lambda: self.stop_requested.emit(),
            accessible_name="영상 중지",
            fixed_size=ICON_BUTTON_PX,
        )
        set_button_icon(self._pause_btn, "pause", pad_right=0)
        set_button_icon(self._stop_btn, "x", pad_right=0)
        self._bar = QProgressBar(self)
        self._bar.setRange(0, PROGRESS_STEPS)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(CARD_BAR_HEIGHT_PX)
        self._bar.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._values: tuple[QLabel, ...] = tuple(QLabel(self) for _ in CARD_CAPTIONS)
        self._pre_encode: bool = False
        self._applied_pre_encode: bool | None = None
        self._palette: ThemePalette | None = None
        grid = QGridLayout()
        grid.setHorizontalSpacing(CARD_GRID_HSPACING_PX)
        for index, caption in enumerate(CARD_CAPTIONS):
            row, column = divmod(index, CARD_COLUMNS)
            cap_label = QLabel(caption, self)
            cap_label.setToolTip(CAPTION_TIPS[index])
            self._values[index].setToolTip(CAPTION_TIPS[index])
            # - 캡션은 내용 폭만 쓰고 값은 늘림 비율로 남는 폭을 받음
            # - 값 칸은 글자 폭을 최소 폭으로 잡지 않아, 긴 값이 카드 최소 폭과 창 폭을 밀어내지 않게 함
            cap_label.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Preferred)
            self._values[index].setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)
            self._values[index].setMinimumWidth(VALUE_LABEL_MIN_WIDTH_PX)
            grid.addWidget(cap_label, row, column * 2)
            grid.addWidget(self._values[index], row, column * 2 + 1)
        for column in field_columns():
            grid.setColumnStretch(column, 1)
        self._graph = BitrateGraph(self)
        self._legend = GraphLegend(self)
        info = QLabel(self)
        info.setPixmap(get_icon("info", size=INFO_ICON_PX).pixmap(INFO_ICON_PX, INFO_ICON_PX))
        info.setToolTip(LEGEND_TIP)
        info.setAccessibleName("그래프 범례")
        graph_title = QLabel("순간 비트레이트", self)
        graph_title.setToolTip(GRAPH_TITLE_TIP)
        graph_head = QHBoxLayout()
        graph_head.addWidget(graph_title)
        graph_head.addStretch(1)
        graph_head.addWidget(info)
        self._retry_notice = QLabel(self)
        self._retry_notice.setObjectName("EncodeRetryNotice")
        self._retry_notice.setWordWrap(True)
        self._retry_notice.setVisible(False)
        self._notice = QLabel(LONG_PAUSE_TEXT, self)
        self._notice.setTextFormat(Qt.TextFormat.RichText)
        self._notice.setWordWrap(True)
        self._notice.setVisible(False)
        self._notice.linkActivated.connect(lambda _href: self.stop_after_requested.emit())
        head = QHBoxLayout()
        title = QLabel(shorten_middle(name, CARD_TITLE_MAX_CHARS), self)
        title.setToolTip(f"{name}\n더블클릭하면 재생하고, 스페이스바를 누르면 미리 봅니다.")
        head.addWidget(title, stretch=1)
        head.addWidget(self._pause_btn)
        head.addWidget(self._stop_btn)
        layout = QVBoxLayout(self)
        layout.addLayout(head)
        layout.addWidget(self._bar)
        layout.addWidget(self._retry_notice)
        layout.addLayout(grid)
        layout.addLayout(graph_head)
        layout.addWidget(self._graph)
        layout.addWidget(self._legend)
        layout.addWidget(self._notice)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """카드 본문 왼쪽 더블클릭은 영상 열기 요청. 버튼이 받은 더블클릭은 버튼 쪽에서 끝남"""
        if (
            a1 is not None
            and a1.type() == QEvent.Type.MouseButtonDblClick
            and isinstance(a1, QMouseEvent)
            and a1.button() == Qt.MouseButton.LeftButton
        ):
            self.activated.emit()
            return True
        return super().eventFilter(a0, a1)

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        """카드에 포커스가 있을 때 스페이스바는 미리 보기 요청"""
        if event is not None and event.key() == Qt.Key.Key_Space:
            self.preview_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    @property
    def is_paused(self) -> bool:
        """이 항목이 일시중지 상태이면 참"""
        return self._paused

    def set_paused(self, paused: bool) -> None:
        """일시중지 여부에 맞춰 버튼 아이콘과 툴팁을 바꾸고, 재개하면 장시간 안내를 숨김"""
        self._paused = paused
        set_button_icon(self._pause_btn, "play" if paused else "pause", pad_right=0)
        self._pause_btn.setToolTip(ITEM_RESUME_TIP if paused else ITEM_PAUSE_TIP)
        if not paused:
            self._notice.setVisible(False)

    def set_pause_enabled(self, enabled: bool) -> None:
        """개별 일시중지와 재개 버튼을 켜거나 끔. 전체 일시중지 중에는 끔"""
        self._pause_btn.setEnabled(enabled)

    def set_long_pause(self, visible: bool) -> None:
        """멈춘 시간이 기준을 넘었을 때 안내를 보이거나 숨김"""
        self._notice.setVisible(visible)

    def set_retry_notice(self, text: str) -> None:
        """품질값 상향 재시도 중 안내를 보이거나 숨김"""
        self._retry_notice.setText(text)
        self._retry_notice.setVisible(bool(text))

    def set_progress(self, fraction: float | None, tip: str = "") -> None:
        """진행 막대 값. 측정 값이 없으면 0이고, 사유가 있으면 툴팁으로 보임"""
        self._bar.setValue(0 if fraction is None else round(fraction * PROGRESS_STEPS))
        self._bar.setToolTip(tip)

    def set_fields(self, fields: tuple[CardField, ...]) -> None:
        """카드 칸 문구와 사유 툴팁을 캡션 순서대로 설정"""
        for label, field in zip(self._values, fields, strict=True):
            label.setText(field.text)
            label.setToolTip(field.tip)

    def set_pre_encode(self, pre_encode: bool) -> None:
        """본 인코딩 전 준비 단계 여부에 따라 진행 막대 채움색을 주황과 기본색 사이에서 바꿈

        - 같은 값으로 이미 칠해 두었으면 스타일시트를 다시 파싱하지 않음. 진행 갱신마다 불림
        """
        self._pre_encode = pre_encode
        palette = self._palette
        if palette is None or pre_encode == self._applied_pre_encode:
            return
        self._bar.setStyleSheet(bar_style(palette, pre_encode=pre_encode))
        self._applied_pre_encode = pre_encode

    def apply_palette(self, palette: ThemePalette) -> None:
        """카드 바탕은 내용 층 색, 테두리는 구분선 색으로 두어 창 바탕과 층을 나눔"""
        self._palette = palette
        self.setStyleSheet(
            f"QFrame#EncodeJobCard {{ background: {palette.bg_card}; border: 1px solid {palette.border_subtle};"
            f" border-radius: 8px; }}"
        )
        self._bar.setStyleSheet(bar_style(palette, pre_encode=self._pre_encode))
        self._applied_pre_encode = self._pre_encode
        bg, fg, bd = palette.badge_pending
        self._retry_notice.setStyleSheet(
            f"QLabel#EncodeRetryNotice {{ background: {bg}; color: {fg}; border: 1px solid {bd};"
            " border-radius: 6px; padding: 6px 8px; }"
        )
        self._legend.apply_palette(palette)

    def refresh_graph(self, now_sec: float, max_kbps: float | None, original_kbps: float | None) -> None:
        """기록한 점과 현재 시각으로 그래프를 다시 그림"""
        self._graph.set_series(self.series.points(), max_kbps, original_kbps, now_sec)
        self._legend.set_entries(self._graph.legend_entries())


def apply_card_phase(
    card: JobCard,
    job: EncodeJob,
    encoder_label: str,
    phase: TuningPhase,
    *,
    set_chip_phase: Callable[[str, str], None],
    set_chip_progress: Callable[[float], None],
    set_chip_pre_encode: Callable[[bool], None],
) -> None:
    """품질 시험, 본 인코딩, 재시도 단계 이벤트를 카드와 진행 칩에 반영"""
    if phase.stage == PHASE_SAMPLE_PREDICT:
        frac = 0.0 if phase.segment_total <= 0 else phase.segment_done / phase.segment_total
        card.set_progress(frac, "사전 예측 진행률입니다.")
        card.set_retry_notice("")
        set_chip_phase("사전 예측", SAMPLE_PREDICT_TIP)
        set_chip_progress(frac)
    elif phase.stage == PHASE_SAMPLING:
        frac = 0.0 if phase.segment_total <= 0 else phase.segment_done / phase.segment_total
        card.set_progress(frac, "품질 시험 진행률입니다.")
        card.set_retry_notice("")
        set_chip_phase(
            f"품질 시험 {phase.sample_pass}/{phase.sample_total}",
            f"품질 시험 {phase.sample_pass}/{phase.sample_total} 확인 중: 목표 크기에 맞는 품질값을 정하기 위해 대표 구간 5개를 먼저 시험 인코딩하고 있습니다.",
        )
        set_chip_progress(frac)
    elif phase.stage == PHASE_RETRY and phase.previous_quality is not None:
        prev_txt = format_cq_value(phase.previous_quality)
        cur_txt = format_cq_value(phase.current_quality)
        notice = f"예상 크기가 목표를 넘어 품질값을 {prev_txt}에서 {cur_txt}로 높여 다시 인코딩하고 있습니다. (재시도 {phase.retry_count}/1)"
        card.set_retry_notice(notice)
        set_chip_phase(f"재시도 {phase.retry_count}/1", notice)
    else:
        card.set_retry_notice("")
        set_chip_phase("", "")
    card.set_pre_encode(phase.stage in PRE_ENCODE_STAGES)
    set_chip_pre_encode(phase.stage in PRE_ENCODE_STAGES)
    empty = ProgressSample(
        frame=None, fps=None, quality=None, bitrate_kbps=None,
        total_size_bytes=None, out_time_sec=None, speed=None, finished=False,
    )
    card.set_fields(card_fields(empty, job, 0.0, None, None, None, encoder_label, phase=phase))
