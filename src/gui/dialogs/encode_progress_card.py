"""재인코딩 진행 창의 영상 카드: 이름, 일시중지와 중지 버튼, 진행 막대, 수치, 순간 비트레이트 그래프

- 카드 하나는 진행 창의 탭 하나에 들어감. 탭 제목은 순서 번호와 줄인 파일 이름
- 화면 스레드에서만 쓴다
"""

from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QObject, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent, QMouseEvent
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

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
from src.gui.styles.icons import create_icon_button, get_icon, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.widgets.bitrate_graph import BitrateGraph
from src.service.encode.encode_job import EncodeJob

PROGRESS_STEPS: int = 1000
ICON_BUTTON_PX: int = 28
INFO_ICON_PX: int = 14
CARD_COLUMNS: int = 2
TAB_NAME_MAX_CHARS: int = 20
CARD_TITLE_MAX_CHARS: int = 28
CARD_CAPTIONS: tuple[str, ...] = (
    "진행 위치",
    "재인코딩 속도",
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
NO_VALUE_TIP: str = "재인코딩을 시작한 지 얼마 안 되었거나 이 영상에서는 측정할 수 없습니다."
NO_POSITION_TIP: str = (
    "진행 위치 시각을 알 수 없는 영상입니다(MKV, 커버 MP4). "
    "진행 막대와 남은 시간은 프레임 수로 계산했습니다. 예상 최종 크기는 계산하지 않습니다."
)
NO_EXPECTED_TIP: str = "진행 위치가 없어 예상 최종 크기를 계산하지 않았습니다."
ITEM_PAUSE_TIP: str = "이 영상을 일시중지합니다. 멈춘 동안 GPU 인코더 세션을 계속 사용합니다."
ITEM_RESUME_TIP: str = "멈춘 영상을 이어서 재인코딩합니다."
ITEM_STOP_TIP: str = "이 영상을 중지합니다. 대기로 돌아가며 확인 창을 한 번 거칩니다."
LEGEND_TIP: str = "실선: 순간 비트레이트\n점선: 최대 비트레이트\n빗금: 일시중지 구간"
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
) -> tuple[CardField, ...]:
    """진행 이벤트 값과 계획 입력으로 카드 칸 13개를 CARD_CAPTIONS 순서대로 만듦. 값이 없으면 정보 없음과 사유"""
    position_known = sample.out_time_sec is not None
    remaining = remaining_sec(elapsed_sec, fraction)
    total_mb = None if sample.total_size_bytes is None else bytes_to_mb(sample.total_size_bytes)
    projected_mb = None if projected_bytes is None else bytes_to_mb(projected_bytes)
    no_projection_tip = NO_VALUE_TIP if position_known else NO_EXPECTED_TIP
    return (
        CardField(
            f"{clock_text(sample.out_time_sec)} / {clock_text(job.source_duration_sec)}" if position_known else NO_VALUE_TEXT,
            "" if position_known else NO_POSITION_TIP,
        ),
        _field(_speed_text(sample), NO_VALUE_TIP),
        _field(None if sample.frame is None else frames_text(sample.frame), NO_VALUE_TIP),
        _field(None if sample.quality is None else quality_text(sample.quality), NO_VALUE_TIP),
        CardField(clock_text(elapsed_sec)),
        _field(None if remaining is None else clock_text(remaining), NO_VALUE_TIP),
        _field(None if total_mb is None else size_text(total_mb), NO_VALUE_TIP),
        _field(None if projected_mb is None else size_text(projected_mb), no_projection_tip),
        _field(None if job.expected_after_mb is None else size_text(job.expected_after_mb), NO_VALUE_TIP),
        CardField(quality_text(job.quality)),
        CardField(resolution_text(job.output_size)),
        CardField(encoder_label),
        _field(None if instant_kbps is None else kbps_text(instant_kbps), NO_VALUE_TIP),
    )


def _speed_text(sample: ProgressSample) -> str | None:
    """재인코딩 속도 칸: 배속과 초당 프레임 수를 함께 씀. 배속이 없으면 값 없음"""
    if sample.speed is None:
        return None
    if sample.fps is None:
        return speed_text(sample.speed)
    return f"{speed_text(sample.speed)}, {round(sample.fps)}fps"


def _field(text: str | None, tip: str) -> CardField:
    """값이 있으면 그대로, 없으면 정보 없음과 사유 툴팁"""
    return CardField(NO_VALUE_TEXT, tip) if text is None else CardField(text)


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
        self._bar = QProgressBar()
        self._bar.setRange(0, PROGRESS_STEPS)
        self._bar.setTextVisible(False)
        self._bar.setFixedHeight(8)
        self._values: tuple[QLabel, ...] = tuple(QLabel() for _ in CARD_CAPTIONS)
        grid = QGridLayout()
        for index, caption in enumerate(CARD_CAPTIONS):
            row, column = divmod(index, CARD_COLUMNS)
            self._values[index].setWordWrap(True)
            grid.addWidget(QLabel(caption), row, column * 2)
            grid.addWidget(self._values[index], row, column * 2 + 1)
        self._graph = BitrateGraph(self)
        self._legend = QLabel()
        self._legend.setObjectName("EncodeGraphLegend")
        self._legend.setWordWrap(True)
        info = QLabel()
        info.setPixmap(get_icon("info", size=INFO_ICON_PX).pixmap(INFO_ICON_PX, INFO_ICON_PX))
        info.setToolTip(LEGEND_TIP)
        info.setAccessibleName("그래프 범례")
        graph_head = QHBoxLayout()
        graph_head.addWidget(QLabel("순간 비트레이트"))
        graph_head.addStretch(1)
        graph_head.addWidget(info)
        self._notice = QLabel(LONG_PAUSE_TEXT)
        self._notice.setTextFormat(Qt.TextFormat.RichText)
        self._notice.setWordWrap(True)
        self._notice.setVisible(False)
        self._notice.linkActivated.connect(lambda _href: self.stop_after_requested.emit())
        head = QHBoxLayout()
        title = QLabel(shorten_middle(name, CARD_TITLE_MAX_CHARS))
        title.setToolTip(name)
        head.addWidget(title, stretch=1)
        head.addWidget(self._pause_btn)
        head.addWidget(self._stop_btn)
        layout = QVBoxLayout(self)
        layout.addLayout(head)
        layout.addWidget(self._bar)
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

    def set_progress(self, fraction: float | None, tip: str = "") -> None:
        """진행 막대 값. 측정 값이 없으면 0이고, 사유가 있으면 툴팁으로 보임"""
        self._bar.setValue(0 if fraction is None else round(fraction * PROGRESS_STEPS))
        self._bar.setToolTip(tip)

    def set_fields(self, fields: tuple[CardField, ...]) -> None:
        """카드 칸 문구와 사유 툴팁을 캡션 순서대로 설정"""
        for label, field in zip(self._values, fields, strict=True):
            label.setText(field.text)
            label.setToolTip(field.tip)

    def apply_palette(self, palette: ThemePalette) -> None:
        """카드 바탕은 내용 층 색, 테두리는 구분선 색으로 두어 창 바탕과 층을 나눔"""
        self.setStyleSheet(
            f"QFrame#EncodeJobCard {{ background: {palette.bg_card}; border: 1px solid {palette.border_subtle};"
            f" border-radius: 8px; }}"
            f"QLabel#EncodeGraphLegend {{ color: {palette.text_muted}; background: transparent; }}"
        )

    def refresh_graph(self, now_sec: float, max_kbps: float | None, original_kbps: float | None) -> None:
        """기록한 점과 현재 시각으로 그래프를 다시 그림"""
        self._graph.set_series(self.series.points(), max_kbps, original_kbps, now_sec)
        self._legend.setText(self._graph.legend_text())
