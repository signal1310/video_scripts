"""재인코딩 진행 창의 영상 카드: 이름, 일시중지와 중지 버튼, 진행 막대, 수치, 순간 비트레이트 그래프

- 카드 하나는 진행 창의 탭 하나에 들어감. 탭 제목은 순서 번호와 줄인 파일 이름
- 화면 스레드에서만 쓴다
"""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QLabel, QProgressBar, QVBoxLayout, QWidget

from src.core.encode.encode_progress_view import BitrateSeries
from src.gui.styles.icons import create_icon_button, get_icon, set_button_icon
from src.gui.widgets.bitrate_graph import BitrateGraph

PROGRESS_STEPS: int = 1000
ICON_BUTTON_PX: int = 28
INFO_ICON_PX: int = 14
CARD_COLUMNS: int = 2
TAB_NAME_MAX_CHARS: int = 20
CARD_CAPTIONS: tuple[str, ...] = ("진행 위치", "재인코딩 속도", "경과 시간", "남은 시간", "순간 비트레이트")
ITEM_PAUSE_TIP: str = "이 영상을 일시중지합니다. 멈춘 동안 GPU 인코더 세션을 계속 사용합니다."
ITEM_RESUME_TIP: str = "멈춘 영상을 이어서 재인코딩합니다."
ITEM_STOP_TIP: str = "이 영상을 중지합니다. 대기로 돌아가며 확인 창을 한 번 거칩니다."
LEGEND_TIP: str = "실선: 순간 비트레이트\n점선: 최대 비트레이트\n빗금: 일시중지 구간"
LONG_PAUSE_TEXT: str = (
    "멈춘 동안 GPU 인코더 세션을 계속 사용해 그래픽 드라이버나 다른 작업에 영향을 줄 수 있으며, "
    '오래 멈출 때는 <a href="stop-after">현재 영상 후 중지로 전환</a>을 권장합니다.'
)


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


class JobCard(QFrame):
    """작업 하나의 이름, 일시중지와 중지 버튼, 진행 막대, 수치, 비트레이트 그래프"""

    pause_toggled = pyqtSignal()
    stop_requested = pyqtSignal()
    stop_after_requested = pyqtSignal()

    def __init__(self, name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EncodeJobCard")
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
        title = QLabel(name)
        title.setMinimumWidth(0)
        head.addWidget(title, stretch=1)
        head.addWidget(self._pause_btn)
        head.addWidget(self._stop_btn)
        layout = QVBoxLayout(self)
        layout.addLayout(head)
        layout.addWidget(self._bar)
        layout.addLayout(grid)
        layout.addLayout(graph_head)
        layout.addWidget(self._graph)
        layout.addWidget(self._notice)

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

    def set_progress(self, fraction: float | None) -> None:
        """진행 막대 값. 측정 값이 없으면 0"""
        self._bar.setValue(0 if fraction is None else round(fraction * PROGRESS_STEPS))

    def set_values(self, position: str, speed: str, elapsed: str, remaining: str, kbps: str) -> None:
        """카드의 수치 문자열을 캡션 순서대로 설정"""
        for label, text in zip(self._values, (position, speed, elapsed, remaining, kbps), strict=True):
            label.setText(text)

    def refresh_graph(self, now_sec: float, max_kbps: float | None) -> None:
        """기록한 점과 현재 시각으로 그래프를 다시 그림"""
        self._graph.set_series(self.series.points(), max_kbps, now_sec)
