"""화질 비교 뷰의 로딩, 오류 표시와 공용 토스트가 비켜야 할 상단 여백

- 프레임이 이미 있으면 기준 시간이 지나도 알약 배지만 띄워 보고 있던 영상을 유지
- 프레임이 없거나 오류일 때만 캔버스 전체 오버레이를 씀
- 단축키 실행 결과는 부모 창의 공용 토스트가 표시하므로 이 모듈은 그 자리만 계산
"""

from __future__ import annotations

from PyQt6.QtCore import QObject, QTimer

from src.gui.dialogs.frame_compare_layout import ERROR_TITLE, LOADING_TEXT, CompareStage

LOADING_OVERLAY_DELAY_MS: int = 250
NOTICE_MARGIN_PX: int = 10
NOTICE_GAP_PX: int = 6


class CompareStatusIndicator(QObject):
    """추출 진행과 오류를 알약 배지와 전체 오버레이로 나눠 표시"""

    def __init__(self, stage: CompareStage, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._stage = stage
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(LOADING_OVERLAY_DELAY_MS)
        self._timer.timeout.connect(self.reveal)

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

    def notice_top_offset(self) -> int:
        """공용 토스트를 놓을 위쪽 여백, 접힘 오버레이와 로딩 알약 아래"""
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
