"""재인코딩 진행 창의 위젯 배치: 머리글과 전체 조작 버튼, 시간 줄, 전체 막대, 하단 칸, 진행과 결과 탭 전환

- 위젯과 배치만 만들고, 상태 갱신과 조작 처리는 진행 창이 맡음
- 머리글 버튼은 생성 때 동작을 받아 연결함
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.gui.dialogs.encode_pending_list import PendingQueueList
from src.gui.dialogs.encode_progress_banner import CloseBanner
from src.gui.dialogs.encode_progress_card import PROGRESS_STEPS
from src.gui.dialogs.encode_progress_chips import CardChips, SegmentSwitch
from src.gui.dialogs.encode_progress_panels import ResultList, TotalsPanel
from src.gui.dialogs.encode_summary_view import EncodeSummaryView, SummaryMedia
from src.gui.styles.icons import create_icon_button, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import set_state_property

SUMMARY_TAB_TEXT: str = "요약"
PROGRESS_TAB_TEXT: str = "진행"
RESULT_TAB_TEMPLATE: str = "결과 {count}"
SUMMARY_TAB_INDEX: int = 0
PROGRESS_TAB_INDEX: int = 1
RESULT_TAB_INDEX: int = 2
IDLE_TEXT: str = "진행 중인 영상이 없습니다. 결과는 결과 탭에서 볼 수 있습니다."
HEADER_BUTTON_PX: int = 32
PRIMARY_BUTTON_WIDTH_PX: int = 48
CAPSULE_RADIUS_PX: int = 16
CAPSULE_BUTTON_RADIUS_PX: int = 14
OVERALL_BAR_HEIGHT_PX: int = 8
START_NAME: str = "재인코딩 시작"
START_TIP: str = "재인코딩을 시작합니다. 시작 전 브리핑은 요약 탭에서 확인할 수 있습니다."
PAUSE_ALL_TIP: str = "진행 중인 모든 영상을 일시중지합니다."
RESUME_ALL_TIP: str = "일시중지한 영상을 다시 재인코딩합니다."
AFTER_CURRENT_TIP: str = "진행 중인 영상을 끝낸 뒤 멈춥니다."
STOP_ALL_TIP: str = "진행 중인 영상을 즉시 멈추고 대기로 되돌립니다. 확인 창을 한 번 거칩니다."


class ProgressController(Protocol):
    """진행 창이 쓰는 컨트롤러 신호와 제어 메서드. 구체 컨트롤러 모듈을 가져오지 않아 패키지 순환을 막음"""

    event_received: Any
    batch_finished: Any
    pause_changed: Any
    item_pause_changed: Any

    def pause_all(self) -> None:
        """전체 일시중지"""

    def resume_all(self) -> None:
        """전체 재개"""

    def cancel_all(self) -> None:
        """전체 즉시 중지"""

    def request_stop_after_current(self) -> None:
        """현재 영상 후 중지"""

    def pause_one(self, source: str) -> None:
        """영상 하나 일시중지"""

    def resume_one(self, source: str) -> None:
        """영상 하나 재개"""

    def cancel_one(self, source: str) -> None:
        """영상 하나 즉시 중지"""


class ProgressMedia(Protocol):
    """영상 열기, 미리 보기, 교체 대기 조작을 조정자가 맡는 인터페이스. 파일 확인과 확인 창도 조정자가 처리"""

    def open_file(self, path: str) -> None:
        """영상 파일을 기본 플레이어로 연다"""

    def preview_file(self, path: str) -> None:
        """영상 파일을 미리 보기 창으로 연다"""

    def reveal_leftover(self, source: str) -> bool:
        """임시 파일이 남은 행이면 그 파일을 탐색기에서 선택하고 참을 돌려줌. 그 밖의 행은 거짓"""
        ...

    def retry_replace(self, source: str) -> None:
        """교체 대기 항목 하나를 다시 배치"""

    def forfeit_replace(self, source: str) -> None:
        """교체 대기 항목 하나를 포기해 실패로 바꿈"""

    def trash_leftover(self, source: str) -> None:
        """같은 자리 임시 파일 때문에 실패한 항목이 쓰려던 임시 파일을 확인 뒤 휴지통으로 옮김"""

    def compare_files(self, source: str) -> None:
        """완료, 교체 대기 항목의 원본과 결과 파일을 화질 비교 창으로 엶"""


@dataclass(frozen=True, slots=True)
class ProgressActions:
    """머리글 버튼과 종료 예약 배너가 누르면 실행할 동작"""

    toggle_start: Callable[[], None]
    request_stop_after: Callable[[], None]
    stop_all: Callable[[], None]
    cancel_close: Callable[[], None]


@dataclass(frozen=True, slots=True)
class ProgressWidgets:
    """진행 창이 상태를 갱신할 위젯 묶음"""

    banner: CloseBanner
    header: QLabel
    elapsed: QLabel
    remaining: QLabel
    overall_bar: QProgressBar
    start_btn: QPushButton
    after_btn: QPushButton
    stop_all_btn: QPushButton
    totals: TotalsPanel
    tabs: CardChips
    results: ResultList
    idle: QLabel
    view_switch: SegmentSwitch
    pages: QStackedWidget
    summary: EncodeSummaryView
    pending: PendingQueueList


def _build_control_capsule(parent: QWidget, actions: ProgressActions) -> tuple[QFrame, QPushButton, QPushButton, QPushButton]:
    """상단 우측 3버튼 캡슐 제어 바를 만들고 버튼을 돌려줌"""
    capsule = QFrame(parent)
    capsule.setObjectName("EncodeControlCapsule")
    row = QHBoxLayout(capsule)
    row.setContentsMargins(2, 2, 2, 2)
    row.setSpacing(1)
    start_btn = create_icon_button(
        capsule,
        START_TIP,
        actions.toggle_start,
        accessible_name=START_NAME,
        fixed_size=HEADER_BUTTON_PX,
        object_name="EncodeControlPrimary",
    )
    start_btn.setMinimumWidth(PRIMARY_BUTTON_WIDTH_PX)
    set_button_icon(start_btn, "play", pad_right=0)
    after_btn = create_icon_button(
        capsule,
        AFTER_CURRENT_TIP,
        actions.request_stop_after,
        accessible_name="현재 영상 후 중지",
        fixed_size=HEADER_BUTTON_PX,
        object_name="EncodeControlSecondary",
    )
    stop_all_btn = create_icon_button(
        capsule,
        STOP_ALL_TIP,
        actions.stop_all,
        accessible_name="전체 중지",
        fixed_size=HEADER_BUTTON_PX,
        object_name="EncodeControlDanger",
    )
    set_button_icon(after_btn, "square", pad_right=0)
    set_button_icon(stop_all_btn, "ban", pad_right=0)
    row.addWidget(start_btn)
    row.addWidget(after_btn)
    row.addWidget(stop_all_btn)
    set_state_property(start_btn, "primary", True)
    after_btn.setEnabled(False)
    stop_all_btn.setEnabled(False)
    return capsule, start_btn, after_btn, stop_all_btn


def build_progress_widgets(
    parent: QWidget,
    actions: ProgressActions,
    media: SummaryMedia,
) -> ProgressWidgets:
    """진행 창 본문을 만들고 `parent`의 레이아웃으로 배치. 탭 전환은 여기서 쪽 전환까지 연결함"""
    banner = CloseBanner(parent)
    banner.stop_now_requested.connect(actions.stop_all)
    banner.cancel_requested.connect(actions.cancel_close)
    header = QLabel(parent)
    header.setToolTip("전체 작업 대상 중 현재까지 처리를 마친 영상 수입니다.")
    elapsed = QLabel(parent)
    elapsed.setToolTip("배치 전체를 시작한 뒤 흐른 시간입니다.")
    remaining = QLabel(parent)
    remaining.setToolTip("현재 전체 처리 속도를 기준으로 남은 영상을 모두 마칠 때까지 걸릴 예상 시간입니다.")
    overall_bar = QProgressBar(parent)
    overall_bar.setToolTip("전체 배치의 진행률입니다.")
    overall_bar.setRange(0, PROGRESS_STEPS)
    overall_bar.setTextVisible(False)
    overall_bar.setFixedHeight(OVERALL_BAR_HEIGHT_PX)
    capsule, start_btn, after_btn, stop_all_btn = _build_control_capsule(parent, actions)
    head = QHBoxLayout()
    head.addWidget(header, stretch=1)
    head.addWidget(capsule)
    timing = QHBoxLayout()
    timing.addWidget(elapsed)
    timing.addStretch(1)
    timing.addWidget(remaining)
    totals = TotalsPanel(parent)
    tabs = CardChips(parent)
    results = ResultList(parent)
    summary = EncodeSummaryView(media, parent)
    pending = PendingQueueList(parent)
    pages = QStackedWidget(parent)
    summary_scroll = QScrollArea(pages)
    summary_scroll.setWidgetResizable(True)
    summary_scroll.setFrameShape(QFrame.Shape.NoFrame)
    summary_scroll.setWidget(summary)
    progress_page = QWidget(pages)
    idle = QLabel(IDLE_TEXT, progress_page)
    idle.setWordWrap(True)
    progress_layout = QVBoxLayout(progress_page)
    progress_layout.setContentsMargins(0, 0, 0, 0)
    progress_layout.addWidget(pending)
    progress_layout.addWidget(tabs, stretch=1)
    progress_layout.addWidget(idle)
    view_switch = SegmentSwitch(parent)
    view_switch.addTab(SUMMARY_TAB_TEXT)
    view_switch.addTab(PROGRESS_TAB_TEXT)
    view_switch.addTab(RESULT_TAB_TEMPLATE.format(count=0))
    view_switch.setToolTip("요약, 진행 중인 영상 카드, 완료된 결과 목록 사이를 전환합니다.")
    pages.addWidget(summary_scroll)
    pages.addWidget(progress_page)
    pages.addWidget(results)
    view_switch.currentChanged.connect(pages.setCurrentIndex)
    layout = QVBoxLayout(parent)
    layout.addWidget(banner)
    layout.addLayout(head)
    layout.addLayout(timing)
    layout.addWidget(overall_bar)
    layout.addWidget(totals)
    layout.addWidget(view_switch)
    layout.addWidget(pages, stretch=1)
    return ProgressWidgets(
        banner=banner,
        header=header,
        elapsed=elapsed,
        remaining=remaining,
        overall_bar=overall_bar,
        start_btn=start_btn,
        after_btn=after_btn,
        stop_all_btn=stop_all_btn,
        totals=totals,
        tabs=tabs,
        results=results,
        idle=idle,
        view_switch=view_switch,
        pages=pages,
        summary=summary,
        pending=pending,
    )


def build_progress_window_qss(palette: ThemePalette) -> str:
    """진행 창 바탕과 진행 창에서 여는 확인 대화상자, 툴팁, 메뉴 QSS 문자열 반환

    - Windows 11 네이티브 스타일은 OS 색상 모드로 대화상자 버튼 글자색을 그려, 앱 테마와
      어긋나면 밝은 버튼에 흰 글자가 나오므로 확인 대화상자 색을 앱 테마로 고정함
    - 선택자를 QMessageBox로 한정해 진행 창의 아이콘 버튼과 칩 스타일은 건드리지 않음
    """
    return f"""
EncodeProgressWindow {{
    background: {palette.bg_base};
}}
QFrame#EncodeControlCapsule {{
    background: {palette.bg_elevated};
    border: 1px solid {palette.border_subtle};
    border-radius: {CAPSULE_RADIUS_PX}px;
}}
QPushButton#EncodeControlPrimary,
QPushButton#EncodeControlSecondary,
QPushButton#EncodeControlDanger {{
    background: transparent;
    border: none;
    border-radius: {CAPSULE_BUTTON_RADIUS_PX}px;
    padding: 0px;
}}
QPushButton#EncodeControlPrimary[primary="true"] {{
    background: {palette.action_primary_bg};
    border: 1px solid {palette.action_primary_border};
}}
QPushButton#EncodeControlPrimary[primary="true"]:hover {{
    background: {palette.action_primary_hover};
}}
QPushButton#EncodeControlPrimary[primary="false"]:hover,
QPushButton#EncodeControlSecondary:hover {{
    background: {palette.bg_hover};
}}
QPushButton#EncodeControlDanger:hover {{
    background: {palette.action_danger_hover};
}}
QPushButton#EncodeControlPrimary:disabled,
QPushButton#EncodeControlSecondary:disabled,
QPushButton#EncodeControlDanger:disabled {{
    background: transparent;
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QMessageBox {{
    background-color: {palette.bg_card};
}}
QMessageBox QLabel {{
    color: {palette.text_primary};
}}
QMessageBox QPushButton {{
    background-color: {palette.btn_bg};
    color: {palette.text_primary};
    border: 1px solid {palette.btn_border};
    border-radius: 6px;
    padding: 5px 14px;
    min-height: 22px;
}}
QMessageBox QPushButton:hover {{
    background-color: {palette.btn_hover};
}}
QMessageBox QPushButton:pressed {{
    background-color: {palette.bg_hover};
}}
QMessageBox QPushButton:disabled {{
    background-color: {palette.bg_elevated};
    color: {palette.text_muted};
    border-color: {palette.border_subtle};
}}
QToolTip {{
    background-color: {palette.bg_card};
    color: {palette.text_primary};
    border: 1px solid {palette.border_focus};
    border-radius: 4px;
    padding: 6px;
}}
QMenu {{
    background-color: {palette.bg_card};
    color: {palette.text_primary};
    border: 1px solid {palette.border_subtle};
    border-radius: 6px;
    padding: 4px;
}}
QMenu::item {{
    padding: 6px 20px 6px 10px;
    border-radius: 4px;
}}
QMenu::item:selected {{
    background-color: {palette.bg_selected};
    color: {palette.text_primary};
}}
QMenu::item:disabled {{
    color: {palette.text_muted};
    background-color: transparent;
}}
"""

