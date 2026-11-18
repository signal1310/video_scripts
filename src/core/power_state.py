"""재인코딩 중 절전 방지와 완료 후 시스템 동작(절전, 종료) 실행 모듈

- 절전 방지는 SetThreadExecutionState로 설정하며 호출한 스레드에 적용됨. 배치를 실행하는 스레드에서 쓸 것
- 화면 끄기는 막지 않음(ES_DISPLAY_REQUIRED를 쓰지 않음)
- 완료 후 "앱 종료"는 화면 계층이 처리하므로 여기서는 시스템 명령만 다룸
"""

import ctypes
import subprocess
import sys
import threading
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager

AFTER_COMPLETE_NONE: str = "none"
AFTER_COMPLETE_SLEEP: str = "sleep"
AFTER_COMPLETE_SHUTDOWN: str = "shutdown"
AFTER_COMPLETE_EXIT: str = "exit"
AFTER_COMPLETE_COUNTDOWN_SEC: float = 30.0
IS_WINDOWS: bool = sys.platform == "win32"
ES_CONTINUOUS: int = 0x80000000
ES_SYSTEM_REQUIRED: int = 0x00000001
SLEEP_COMMAND: tuple[str, ...] = ("rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0")
SHUTDOWN_COMMAND: tuple[str, ...] = ("shutdown", "/s", "/t", "0")

Runner = Callable[[Sequence[str]], object]


def _set_execution_state(flags: int) -> bool:
    """실행 상태 플래그를 설정하며 Windows가 아니면 False를 반환"""
    if not IS_WINDOWS:
        return False
    return bool(ctypes.windll.kernel32.SetThreadExecutionState(flags))


@contextmanager
def prevent_sleep(enabled: bool) -> Generator[None]:
    """블록 동안 시스템 절전을 막고 끝나면 원래 상태(ES_CONTINUOUS만)로 되돌림"""
    if not enabled:
        yield
        return
    _set_execution_state(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)
    try:
        yield
    finally:
        _set_execution_state(ES_CONTINUOUS)


def system_action_command(action: str) -> tuple[str, ...] | None:
    """완료 후 시스템 동작의 명령. 절전과 종료만 명령이 있고 나머지는 None"""
    if action == AFTER_COMPLETE_SLEEP:
        return SLEEP_COMMAND
    if action == AFTER_COMPLETE_SHUTDOWN:
        return SHUTDOWN_COMMAND
    return None


def run_after_complete(
    action: str,
    *,
    cancel: threading.Event,
    countdown_sec: float = AFTER_COMPLETE_COUNTDOWN_SEC,
    runner: Runner | None = None,
) -> bool:
    """완료 후 동작을 카운트다운 뒤 실행. 카운트다운 중 취소되면 실행하지 않고 False 반환

    - 명령이 없는 동작(없음, 앱 종료)은 실행하지 않고 False 반환
    """
    command = system_action_command(action)
    if command is None:
        return False
    if cancel.wait(countdown_sec):
        return False
    execute: Runner = runner if runner is not None else _run_command
    execute(command)
    return True


def _run_command(command: Sequence[str]) -> object:
    """시스템 명령을 창 없이 실행하고 결과를 그대로 돌려줌"""
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run(list(command), check=False, creationflags=creation_flags)
