"""화질 비교 창의 로딩, 오류, 단축키 실행 알림 표시

- 프레임이 이미 있으면 기준 시간이 지나도 알약 배지만 띄워 보고 있던 영상을 유지
- 프레임이 없거나 오류일 때만 캔버스 전체 오버레이를 씀
- 단축키 실행 결과는 캔버스 위쪽 가운데의 반투명 HUD로 잠깐 표시
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer
from PyQt6.QtWidgets import QLabel, QWidget

from src.gui.dialogs.frame_compare_layout import ERROR_TITLE, LOADING_TEXT, CompareStage

LOADING_OVERLAY_DELAY_MS: int = 250
NOTICE_DURATION_MS: int = 1200
NOTICE_MARGIN_PX: int = 10
NOTICE_GAP_PX: int = 6


class CompareNoticeToast(QLabel):
    """캔버스 위쪽 가운데에 실행 결과를 잠깐 보여 주는 안내

    - 마우스 사건을 통과시켜 아래 캔버스 조작을 막지 않음
    - 접힘 상단 오버레이와 로딩 알약을 비켜 놓음
    """

    def __init__(self, parent: QWidget, get_top: Callable[[], int]) -> None:
        super().__init__("", parent)
        self.setObjectName("CompareNoticeToast")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._get_top = get_top
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(NOTICE_DURATION_MS)
        self._timer.timeout.connect(self.hide)
        parent.installEventFilter(self)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """부모 캔버스 크기가 바뀌면 가운데 위치를 다시 잡음"""
        if a1 is not None and a1.type() == QEvent.Type.Resize:
            self.reposition()
        return super().eventFilter(a0, a1)

    def show_notice(self, text: str) -> None:
        """문구를 바로 덮어쓰고 표시 시간을 처음부터 다시 잼"""
        self.setText(text)
        self.reposition()
        self.show()
        self.raise_()
        self._timer.start()

    def reposition(self) -> None:
        """캔버스 위쪽 가운데로 옮김"""
        parent = self.parentWidget()
        if parent is None:
            return
        self.adjustSize()
        self.move(max(0, (parent.width() - self.width()) // 2), self._get_top())


class CompareStatusIndicator(QObject):
    """추출 진행과 오류를 알약 배지와 전체 오버레이로 나눠 표시"""

    def __init__(self, stage: CompareStage, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stage = stage
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(LOADING_OVERLAY_DELAY_MS)
        self._timer.timeout.connect(self.reveal)
        self._notice = CompareNoticeToast(stage.canvas, self._notice_top)

    def show_loading(self) -> None:
        """추출 시작, 프레임이 있으면 화면을 유지하고 기준 시간이 지나야 표시"""
        if self._stage.canvas.has_image():
            self._timer.start()
            return
        self.reveal()

    def reveal(self) -> None:
        """기준 시간이 지난 로딩을 알약 배지나 빈 화면 안내로 표시"""
        self._timer.stop()
        if self._stage.canvas.has_image():
            self._stage.pill.show()
            self._stage.pill.raise_()
            return
        self._show_message(LOADING_TEXT)

    def hide(self) -> None:
        """타이머와 알약 배지, 오버레이를 즉시 숨김"""
        self._timer.stop()
        self._stage.overlay.hide()
        self._stage.pill.hide()

    def show_error(self, body: str) -> None:
        """오류 사유를 캔버스 전체 오버레이에 표시"""
        self._show_message(ERROR_TITLE, body)

    def show_notice(self, text: str) -> None:
        """단축키 실행 결과를 캔버스 위쪽 가운데에 잠깐 표시"""
        self._notice.show_notice(text)

    def _notice_top(self) -> int:
        """알림을 놓을 위쪽 여백, 접힘 오버레이와 로딩 알약 아래"""
        top = self._stage.canvas.top_inset() + NOTICE_MARGIN_PX
        pill = self._stage.pill
        if not pill.isHidden():
            top = max(top, pill.y() + pill.height() + NOTICE_GAP_PX)
        return top

    def _show_message(self, title: str, body: str = "") -> None:
        """제목과 본문을 오버레이에 넣고 알약 배지는 숨김"""
        self._stage.pill.hide()
        self._stage.title.setText(title)
        self._stage.body.setText(body)
        self._stage.overlay.show()
        self._stage.overlay.raise_()

