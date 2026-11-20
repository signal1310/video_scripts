"""완료 후 동작 흐름: 정상 종료한 배치의 동작을 카운트다운 안내 뒤에 실행

- 호출 조건(정상 종료, 중지 요청 없음)은 조정자가 판단하고, 이 모듈은 카운트다운과 실행만 맡음
- 실행 함수, 종료 함수, 전원 능력 조회는 주입받음. 테스트에서 실제 절전과 시스템 종료를 하지 않게 하기 위함
"""

from collections.abc import Callable

from PyQt6.QtWidgets import QWidget

from src.core.encode.power_state import (
    AFTER_COMPLETE_COUNTDOWN_SEC,
    AFTER_COMPLETE_NONE,
    AFTER_COMPLETE_SLEEP,
    PowerCapabilities,
    Runner,
    execute_after_complete,
    read_power_capabilities,
    run_command,
    sleep_falls_back_to_hibernate,
)
from src.gui.dialogs.encode_countdown_dialog import CountdownPrompt


class CompletionFlow:
    """완료 후 동작 카운트다운 안내를 마지막으로 시작한 것 하나만 참조함. 배치는 한 번에 하나만 끝나므로 겹치지 않음"""

    def __init__(
        self,
        parent: QWidget,
        *,
        exit_app: Callable[[], object],
        runner: Runner = run_command,
        read_caps: Callable[[], PowerCapabilities | None] = read_power_capabilities,
    ) -> None:
        self._parent: QWidget = parent
        self._exit_app: Callable[[], object] = exit_app
        self._runner: Runner = runner
        self._read_caps: Callable[[], PowerCapabilities | None] = read_caps
        self._prompt: CountdownPrompt | None = None

    @property
    def prompt(self) -> CountdownPrompt | None:
        """마지막으로 시작한 카운트다운 안내. 없으면 None"""
        return self._prompt

    def start(self, action: str) -> None:
        """완료 후 동작을 카운트다운과 함께 예약. 동작이 없으면 아무 것도 하지 않음"""
        if action == AFTER_COMPLETE_NONE:
            return
        fallback = action == AFTER_COMPLETE_SLEEP and sleep_falls_back_to_hibernate(self._read_caps())
        prompt = CountdownPrompt(
            action,
            AFTER_COMPLETE_COUNTDOWN_SEC,
            hibernate_fallback=fallback,
            parent=self._parent,
        )
        prompt.due.connect(lambda: self._run(action))
        self._prompt = prompt
        prompt.start()

    def _run(self, action: str) -> None:
        execute_after_complete(action, runner=self._runner, exit_app=self._exit_app)
