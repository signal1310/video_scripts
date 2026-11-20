"""완료 후 동작 카운트다운 안내 창: 화면 타이머가 1초마다 줄이고, 0이 되면 실행을 알리며, 취소하면 실행하지 않음

- 창은 비모달로 열어 메인 창과 진행 창을 계속 쓸 수 있게 함
- 취소 버튼, Esc, 창 닫기는 모두 취소로 봄
- 카운트 상태는 core의 CompletionCountdown이 맡고, 이 모듈은 표시와 타이머만 맡음
"""

from PyQt6.QtCore import QObject, QTimer, pyqtBoundSignal, pyqtSignal
from PyQt6.QtWidgets import QMessageBox, QWidget

from src.core.encode.power_state import (
    AFTER_COMPLETE_EXIT,
    AFTER_COMPLETE_SHUTDOWN,
    AFTER_COMPLETE_SLEEP,
    CompletionCountdown,
)

COUNTDOWN_TICK_MS: int = 1000
CANCEL_BUTTON_TEXT: str = "취소"
HIBERNATE_SENTENCE: str = "최대 절전으로 바뀔 수 있습니다."
COUNTDOWN_TITLES: dict[str, str] = {
    AFTER_COMPLETE_SLEEP: "완료 후 절전",
    AFTER_COMPLETE_SHUTDOWN: "완료 후 시스템 종료",
    AFTER_COMPLETE_EXIT: "완료 후 앱 종료",
}


def countdown_text(action: str, remaining: int, hibernate_fallback: bool) -> str:
    """카운트다운 본문. 최대 절전 문장은 절전 요청이 최대 절전으로 처리될 수 있을 때만 붙임"""
    if action == AFTER_COMPLETE_SLEEP:
        text = f"{remaining}초 뒤 PC를 절전합니다. 취소하면 그대로 둡니다."
        return f"{text} {HIBERNATE_SENTENCE}" if hibernate_fallback else text
    if action == AFTER_COMPLETE_SHUTDOWN:
        return f"{remaining}초 뒤 PC를 끕니다. 저장하지 않은 작업이 있으면 취소해 주세요."
    return f"{remaining}초 뒤 앱을 끝냅니다. 취소하면 앱을 계속 엽니다."


class CountdownPrompt(QObject):
    """완료 후 동작의 카운트다운 창. due는 0에 닿아 실행해야 할 때, cancelled는 취소될 때 한 번 보냄"""

    due = pyqtSignal()
    cancelled = pyqtSignal()

    def __init__(self, action: str, seconds: int, *, hibernate_fallback: bool, parent: QWidget) -> None:
        super().__init__(parent)
        self._action: str = action
        self._hibernate_fallback: bool = hibernate_fallback
        self._countdown: CompletionCountdown = CompletionCountdown(seconds)
        self._ended: bool = False
        self._box: QMessageBox = QMessageBox(parent)
        self._box.setWindowTitle(COUNTDOWN_TITLES[action])
        cancel = self._box.addButton(CANCEL_BUTTON_TEXT, QMessageBox.ButtonRole.RejectRole)
        self._box.setDefaultButton(cancel)
        self._box.setEscapeButton(cancel)
        self._box.finished.connect(self._on_box_finished)
        self._timer: QTimer = QTimer(self)
        self._timer.setInterval(COUNTDOWN_TICK_MS)
        self._timer.timeout.connect(self.tick)
        self._refresh()

    @property
    def remaining(self) -> int:
        """남은 초"""
        return self._countdown.remaining

    def start(self) -> None:
        """창을 띄우고 1초 타이머를 시작"""
        self._box.open()
        self._timer.start()

    def tick(self) -> None:
        """1초가 지난 것으로 보고 표시를 갱신하거나, 0에 닿으면 실행 신호를 보냄"""
        if self._ended:
            return
        if self._countdown.tick():
            self._finish_with(self.due)
        else:
            self._refresh()

    def cancel(self) -> None:
        """카운트다운을 취소. 이미 실행 신호를 보냈거나 취소되었으면 아무 것도 하지 않음"""
        if self._ended:
            return
        self._countdown.cancel()
        self._finish_with(self.cancelled)

    def _finish_with(self, signal: pyqtBoundSignal) -> None:
        self._ended = True
        self._timer.stop()
        self._box.hide()
        signal.emit()

    def _on_box_finished(self, _result: int) -> None:
        self.cancel()

    def _refresh(self) -> None:
        self._box.setText(countdown_text(self._action, self._countdown.remaining, self._hibernate_fallback))
