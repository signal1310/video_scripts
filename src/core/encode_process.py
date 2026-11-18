"""FFmpeg 인코딩 자식 프로세스의 실행, 진행 줄 전달, 일시중지와 재개, 우선순위, 취소를 담당하는 모듈

- 진행 줄은 stdout 읽기 스레드에서 콜백으로 전달하며, 워커 스레드에서 Qt 객체에 접근하지 않음
- 취소는 threading.Event를 세운 뒤 프로세스를 kill하고 종료를 기다림(AGENTS.md 2절 병렬 규칙)
"""

import subprocess
import sys
import threading
from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import psutil

STDERR_TAIL_LINES: int = 40
PROCESS_WAIT_TIMEOUT_SEC: float = 10.0
# - 설정 값(high, above_normal, normal, below_normal, low)을 psutil 우선순위 상수 이름으로 매핑
PRIORITY_CONSTANT_NAMES: dict[str, str] = {
    "high": "HIGH_PRIORITY_CLASS",
    "above_normal": "ABOVE_NORMAL_PRIORITY_CLASS",
    "normal": "NORMAL_PRIORITY_CLASS",
    "below_normal": "BELOW_NORMAL_PRIORITY_CLASS",
    "low": "IDLE_PRIORITY_CLASS",
}


@dataclass(frozen=True, slots=True)
class ProcessOutcome:
    """프로세스 종료 결과. cancelled가 True면 사용자 취소로 끝난 것"""

    returncode: int | None
    cancelled: bool
    stderr_tail: tuple[str, ...]


def resolve_priority_value(name: str) -> int:
    """우선순위 설정 값을 psutil 우선순위 상수로 바꿈. 알 수 없는 값은 보통 우선순위"""
    constant_name = PRIORITY_CONSTANT_NAMES.get(name, PRIORITY_CONSTANT_NAMES["normal"])
    value = getattr(psutil, constant_name, None)
    if value is None:
        raise OSError(f"이 운영체제에서 지원하지 않는 우선순위입니다: {name}")
    return int(value)


def _creation_flags() -> int:
    return subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0


class EncodeProcess:
    """FFmpeg 인코딩 한 건의 자식 프로세스 수명 관리"""

    def __init__(
        self,
        args: Sequence[str],
        *,
        on_progress_line: Callable[[str], None] | None = None,
    ) -> None:
        self._args: list[str] = list(args)
        self._on_progress_line = on_progress_line
        self._process: subprocess.Popen[str] | None = None
        self._cancel_event = threading.Event()
        self._stderr_tail: deque[str] = deque(maxlen=STDERR_TAIL_LINES)
        self._readers: list[threading.Thread] = []

    @property
    def pid(self) -> int | None:
        """자식 프로세스 번호. 시작 전이면 None"""
        return self._process.pid if self._process is not None else None

    def start(self) -> None:
        """프로세스를 띄우고 stdout(진행 줄)과 stderr(끝부분 보관) 읽기 스레드를 시작"""
        if self._process is not None:
            raise RuntimeError("이미 시작된 인코딩 프로세스입니다")
        self._process = subprocess.Popen(
            self._args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_creation_flags(),
        )
        self._readers = [
            threading.Thread(target=self._pump_stdout, daemon=True),
            threading.Thread(target=self._pump_stderr, daemon=True),
        ]
        for reader in self._readers:
            reader.start()

    def _pump_stdout(self) -> None:
        process = self._process
        if process is None or process.stdout is None:
            return
        for line in process.stdout:
            if self._on_progress_line is not None:
                self._on_progress_line(line.rstrip("\r\n"))

    def _pump_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            self._stderr_tail.append(line.rstrip("\r\n"))

    def _psutil_process(self) -> psutil.Process:
        if self._process is None:
            raise RuntimeError("인코딩 프로세스가 아직 시작되지 않았습니다")
        return psutil.Process(self._process.pid)

    def pause(self) -> None:
        """프로세스를 일시 정지(psutil.suspend)"""
        self._psutil_process().suspend()

    def resume(self) -> None:
        """일시 정지한 프로세스를 다시 실행(psutil.resume)"""
        self._psutil_process().resume()

    def set_priority(self, name: str) -> None:
        """인코딩 프로세스의 우선순위를 설정 값에 맞춰 바꿈(다른 프로세스에는 영향 없음)"""
        self._psutil_process().nice(resolve_priority_value(name))

    def cancel(self) -> None:
        """취소 신호를 세우고 프로세스를 종료. 이미 끝났으면 아무 일도 하지 않음"""
        self._cancel_event.set()
        if self._process is not None and self._process.poll() is None:
            self._process.kill()

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        """프로세스 종료를 기다린 뒤(timeout None이면 무기한) 읽기 스레드 종료를 짧게 기다려 결과를 돌려줌"""
        if self._process is None:
            raise RuntimeError("인코딩 프로세스가 아직 시작되지 않았습니다")
        returncode = self._process.wait(timeout=timeout)
        for reader in self._readers:
            reader.join(timeout=PROCESS_WAIT_TIMEOUT_SEC)
        return ProcessOutcome(
            returncode=returncode,
            cancelled=self._cancel_event.is_set(),
            stderr_tail=tuple(self._stderr_tail),
        )
