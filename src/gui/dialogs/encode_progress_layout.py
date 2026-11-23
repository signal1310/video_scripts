"""재인코딩 진행 창의 위젯 배치: 머리글과 전체 조작 버튼, 시간 줄, 전체 막대, 하단 칸, 진행과 결과 탭 전환

- 위젯과 배치만 만들고, 상태 갱신과 조작 처리는 진행 창이 맡음
- 머리글 버튼은 생성 때 동작을 받아 연결함
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from src.gui.dialogs.encode_progress_banner import CloseBanner
from src.gui.dialogs.encode_progress_card import PROGRESS_STEPS
from src.gui.dialogs.encode_progress_chips import CardChips, SegmentSwitch
from src.gui.dialogs.encode_progress_panels import ResultList, TotalsPanel
from src.gui.styles.icons import create_icon_button, set_button_icon
from src.gui.styles.palettes import ThemePalette

PROGRESS_TAB_TEXT: str = "진행"
RESULT_TAB_TEMPLATE: str = "결과 {count}"
IDLE_TEXT: str = "진행 중인 영상이 없습니다. 결과는 결과 탭에서 볼 수 있습니다."
HEADER_BUTTON_PX: int = 32
OVERALL_BAR_HEIGHT_PX: int = 8
PAUSE_ALL_TIP: str = "진행 중인 모든 영상을 일시중지합니다."
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

    toggle_batch_pause: Callable[[], None]
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
    pause_all_btn: QPushButton
    after_btn: QPushButton
    stop_all_btn: QPushButton
    totals: TotalsPanel
    tabs: CardChips
    results: ResultList
    idle: QLabel
    view_switch: SegmentSwitch
    pages: QStackedWidget


def build_progress_widgets(parent: QWidget, actions: ProgressActions) -> ProgressWidgets:
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
    pause_all_btn = create_icon_button(
        parent, PAUSE_ALL_TIP, actions.toggle_batch_pause, accessible_name="전체 일시중지", fixed_size=HEADER_BUTTON_PX
    )
    after_btn = create_icon_button(
        parent, AFTER_CURRENT_TIP, actions.request_stop_after, accessible_name="현재 영상 후 중지", fixed_size=HEADER_BUTTON_PX
    )
    stop_all_btn = create_icon_button(
        parent, STOP_ALL_TIP, actions.stop_all, accessible_name="전체 중지", fixed_size=HEADER_BUTTON_PX
    )
    set_button_icon(pause_all_btn, "pause", pad_right=0)
    set_button_icon(after_btn, "square", pad_right=0)
    set_button_icon(stop_all_btn, "ban", pad_right=0)
    head = QHBoxLayout()
    head.addWidget(header, stretch=1)
    head.addWidget(pause_all_btn)
    head.addWidget(after_btn)
    head.addWidget(stop_all_btn)
    timing = QHBoxLayout()
    timing.addWidget(elapsed)
    timing.addStretch(1)
    timing.addWidget(remaining)
    totals = TotalsPanel(parent)
    tabs = CardChips(parent)
    results = ResultList(parent)
    pages = QStackedWidget(parent)
    progress_page = QWidget(pages)
    idle = QLabel(IDLE_TEXT, progress_page)
    idle.setWordWrap(True)
    progress_layout = QVBoxLayout(progress_page)
    progress_layout.setContentsMargins(0, 0, 0, 0)
    progress_layout.addWidget(tabs, stretch=1)
    progress_layout.addWidget(idle)
    view_switch = SegmentSwitch(parent)
    view_switch.addTab(PROGRESS_TAB_TEXT)
    view_switch.addTab(RESULT_TAB_TEMPLATE.format(count=0))
    view_switch.setToolTip("진행 중인 영상 카드와 완료된 결과 목록 사이를 전환합니다.")
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
        pause_all_btn=pause_all_btn,
        after_btn=after_btn,
        stop_all_btn=stop_all_btn,
        totals=totals,
        tabs=tabs,
        results=results,
        idle=idle,
        view_switch=view_switch,
        pages=pages,
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

