"""인코딩 중 절전 방지와 완료 후 동작(절전, 시스템 종료, 앱 종료) 모듈

- 절전 방지는 SetThreadExecutionState로 설정하며 호출한 스레드에 적용됨. 배치를 실행하는 스레드에서 쓸 것
- 화면 끄기는 막지 않음(ES_DISPLAY_REQUIRED를 쓰지 않음)
- 카운트다운 상태는 화면 타이머가 1초마다 tick을 부르는 순수 상태이며, 실행 함수는 호출자가 주입
"""

import ctypes
import subprocess
import sys
from collections.abc import Callable, Generator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

AFTER_COMPLETE_NONE: str = "none"
AFTER_COMPLETE_SLEEP: str = "sleep"
AFTER_COMPLETE_SHUTDOWN: str = "shutdown"
AFTER_COMPLETE_EXIT: str = "exit"
AFTER_COMPLETE_COUNTDOWN_SEC: int = 30
IS_WINDOWS: bool = sys.platform == "win32"
ES_CONTINUOUS: int = 0x80000000
ES_SYSTEM_REQUIRED: int = 0x00000001
SLEEP_COMMAND: tuple[str, ...] = ("rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0")
SHUTDOWN_COMMAND: tuple[str, ...] = ("shutdown", "/s", "/t", "0")
SYSTEM_POWER_CAPABILITIES_SIZE: int = 76

Runner = Callable[[Sequence[str]], object]


@dataclass(frozen=True, slots=True)
class PowerCapabilities:
    """절전 문구 판별에 쓰는 전원 능력 값"""

    standby_s3: bool
    hibernate_file: bool


class _BatteryReportingScale(ctypes.Structure):
    _fields_ = [("Granularity", ctypes.c_uint32), ("Capacity", ctypes.c_uint32)]


class SystemPowerCapabilitiesStructure(ctypes.Structure):
    """GetPwrCapabilities의 SYSTEM_POWER_CAPABILITIES 구조체

    - 필드 전체를 두어 API가 쓰는 크기와 맞춤. 줄이면 API가 바깥 메모리를 덮어씀
    """

    _fields_ = [
        ("PowerButtonPresent", ctypes.c_ubyte),
        ("SleepButtonPresent", ctypes.c_ubyte),
        ("LidPresent", ctypes.c_ubyte),
        ("SystemS1", ctypes.c_ubyte),
        ("SystemS2", ctypes.c_ubyte),
        ("SystemS3", ctypes.c_ubyte),
        ("SystemS4", ctypes.c_ubyte),
        ("SystemS5", ctypes.c_ubyte),
        ("HiberFilePresent", ctypes.c_ubyte),
        ("FullWake", ctypes.c_ubyte),
        ("VideoDimPresent", ctypes.c_ubyte),
        ("ApmPresent", ctypes.c_ubyte),
        ("UpsPresent", ctypes.c_ubyte),
        ("ThermalControl", ctypes.c_ubyte),
        ("ProcessorThrottle", ctypes.c_ubyte),
        ("ProcessorMinThrottle", ctypes.c_ubyte),
        ("ProcessorMaxThrottle", ctypes.c_ubyte),
        ("FastSystemS4", ctypes.c_ubyte),
        ("spare2", ctypes.c_ubyte * 3),
        ("DiskSpinDown", ctypes.c_ubyte),
        ("spare3", ctypes.c_ubyte * 8),
        ("SystemBatteriesPresent", ctypes.c_ubyte),
        ("BatteriesAreShortTerm", ctypes.c_ubyte),
        ("BatteryScale", _BatteryReportingScale * 3),
        ("AcOnLineWake", ctypes.c_uint32),
        ("SoftLidWake", ctypes.c_uint32),
        ("RtcWake", ctypes.c_uint32),
        ("MinDeviceWakeState", ctypes.c_uint32),
        ("DefaultLowLatencyWake", ctypes.c_uint32),
    ]


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


def read_power_capabilities() -> PowerCapabilities | None:
    """전원 능력을 읽기만 함(절전을 실행하지 않음). Windows가 아니거나 조회에 실패하면 None"""
    if not IS_WINDOWS:
        return None
    raw = SystemPowerCapabilitiesStructure()
    if not ctypes.windll.powrprof.GetPwrCapabilities(ctypes.byref(raw)):
        return None
    return PowerCapabilities(standby_s3=bool(raw.SystemS3), hibernate_file=bool(raw.HiberFilePresent))


def sleep_falls_back_to_hibernate(caps: PowerCapabilities | None) -> bool:
    """S3 절전을 쓸 수 없고 최대 절전 파일이 있으면 절전 요청이 최대 절전으로 처리될 수 있음

    - Windows의 절전 대체 동작을 따른 판별이며, 이 프로그램은 실제 절전을 실행해 확인하지 않음
    """
    return caps is not None and caps.hibernate_file and not caps.standby_s3


def system_action_command(action: str) -> tuple[str, ...] | None:
    """완료 후 시스템 동작의 명령. 절전과 종료만 명령이 있고 나머지는 None"""
    if action == AFTER_COMPLETE_SLEEP:
        return SLEEP_COMMAND
    if action == AFTER_COMPLETE_SHUTDOWN:
        return SHUTDOWN_COMMAND
    return None


class CompletionCountdown:
    """완료 후 동작의 남은 초를 세는 순수 상태. 화면 타이머가 1초마다 tick을 부름"""

    def __init__(self, seconds: int) -> None:
        self._remaining: int = seconds
        self._cancelled: bool = False

    @property
    def remaining(self) -> int:
        """남은 초"""
        return self._remaining

    @property
    def cancelled(self) -> bool:
        """취소되었는지 여부"""
        return self._cancelled

    def tick(self) -> bool:
        """1초 줄이고, 이번 틱에서 0에 닿아 실행해야 하면 참을 돌려줌. 취소되었거나 이미 끝났으면 거짓"""
        if self._cancelled or self._remaining <= 0:
            return False
        self._remaining -= 1
        return self._remaining == 0

    def cancel(self) -> None:
        """카운트다운을 취소. 이후 tick은 실행을 알리지 않음"""
        self._cancelled = True


def execute_after_complete(action: str, *, runner: Runner, exit_app: Callable[[], object]) -> bool:
    """완료 후 동작을 실행. 절전과 종료는 명령을, 앱 종료는 종료 함수를 부르고, 없음은 아무 것도 하지 않음

    - 실행 여부를 돌려줌. 명령과 종료 함수는 주입받아 테스트에서 실제로 실행하지 않음
    """
    command = system_action_command(action)
    if command is not None:
        runner(command)
        return True
    if action == AFTER_COMPLETE_EXIT:
        exit_app()
        return True
    return False


def run_command(command: Sequence[str]) -> object:
    """시스템 명령을 창 없이 실행하고 결과를 그대로 돌려줌"""
    creation_flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    return subprocess.run(list(command), check=False, creationflags=creation_flags)
