"""인코더를 합성 입력 1프레임으로 시험 인코딩해 이 PC에서 쓸 수 있는지 판정하는 모듈

- FFmpeg 인코더 목록 포함만으로는 그래픽 장치나 드라이버가 없어도 사용 가능으로 보이므로 시험 결과를 함께 씀(결정 11)
- 명령 생성과 결과 판정은 순수 함수이고, 실제 실행은 주입된 runner가 맡아 FFmpeg 없이 테스트할 수 있음
- 시험은 데몬 스레드에서 돌리고 결과는 (실행 파일, 버전, 인코더) 조합별로 캐시함. 화면 스레드는 캐시만 읽음
"""

import subprocess
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass

from src.core.encode.ffmpeg_capability import FfmpegCapability

ENCODER_TRIAL_TIMEOUT_SEC: float = 20.0
# - 하드웨어 인코더는 최소 해상도 제한이 있어 작은 크기는 피함
TRIAL_SOURCE_SIZE: str = "1280x720"
TRIAL_PIXEL_FORMAT: str = "yuv420p"
NOT_LISTED_REASON: str = "FFmpeg에 이 인코더가 없습니다"
TRIAL_FAILED_REASON: str = "시험 인코딩에 실패했습니다. 그래픽 드라이버와 장치를 확인하세요"

# - 명령과 시간 제한을 받아 종료 코드를 돌려줌. 시간 초과나 실행 실패는 None
TrialRunner = Callable[[list[str], float], int | None]
_TrialKey = tuple[str, str, str]


@dataclass(frozen=True, slots=True)
class EncoderTrialResult:
    """인코더 하나의 사용 가능 여부와 사유. usable이 False면 reason을 화면에 표시"""

    encoder: str
    usable: bool
    reason: str


def build_trial_command(ffmpeg: str, encoder: str) -> list[str]:
    """합성 입력 1프레임을 지정 인코더로 인코딩하고 출력은 버리는 명령을 만듦"""
    return [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-v",
        "error",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=size={TRIAL_SOURCE_SIZE}:rate=1",
        "-frames:v",
        "1",
        "-c:v",
        encoder,
        "-pix_fmt",
        TRIAL_PIXEL_FORMAT,
        "-f",
        "null",
        "-",
    ]


def judge_trial(encoder: str, returncode: int | None) -> EncoderTrialResult:
    """종료 코드가 0일 때만 사용 가능으로 판정. 시간 초과와 실행 실패(None)는 실패"""
    if returncode == 0:
        return EncoderTrialResult(encoder, usable=True, reason="")
    return EncoderTrialResult(encoder, usable=False, reason=TRIAL_FAILED_REASON)


def run_encoder_trial(ffmpeg: str, encoder: str, runner: TrialRunner) -> EncoderTrialResult:
    """시험 명령을 runner로 실행하고 결과를 판정함"""
    returncode = runner(build_trial_command(ffmpeg, encoder), ENCODER_TRIAL_TIMEOUT_SEC)
    return judge_trial(encoder, returncode)


def run_trial_process(command: list[str], timeout_sec: float) -> int | None:
    """시험 명령을 실행하고 종료 코드를 돌려줌. 시간 초과와 실행 실패는 None"""
    creationflags: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        completed = subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout_sec,
            check=False,
            creationflags=creationflags,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.returncode


def _start_daemon_thread(task: Callable[[], None]) -> None:
    """시험을 데몬 스레드에서 실행. 앱 종료를 시험 시간 제한만큼 붙잡지 않도록 데몬으로 둠"""
    threading.Thread(target=task, name="encoder-trial", daemon=True).start()


def _key(capability: FfmpegCapability, encoder: str) -> _TrialKey:
    return capability.executable, capability.version_text, encoder


class EncoderTrialCache:
    """시험 결과 캐시. lookup은 결과가 없으면 None이고, request는 시험을 백그라운드에서 한 번만 시작"""

    def __init__(
        self,
        runner: TrialRunner = run_trial_process,
        spawn: Callable[[Callable[[], None]], None] = _start_daemon_thread,
    ) -> None:
        self._runner: TrialRunner = runner
        self._spawn: Callable[[Callable[[], None]], None] = spawn
        self._lock = threading.Lock()
        self._results: dict[_TrialKey, EncoderTrialResult] = {}
        self._pending: set[_TrialKey] = set()

    def lookup(self, capability: FfmpegCapability, encoder: str) -> EncoderTrialResult | None:
        """완료된 시험 결과를 돌려줌. 시험 전이거나 진행 중이면 None"""
        with self._lock:
            return self._results.get(_key(capability, encoder))

    def request(self, capability: FfmpegCapability, encoder: str) -> None:
        """결과가 없고 진행 중이 아니면 시험을 시작. 이미 있으면 아무것도 하지 않음"""
        key = _key(capability, encoder)
        with self._lock:
            if key in self._results or key in self._pending:
                return
            self._pending.add(key)
        self._spawn(lambda: self._run(key, capability.executable, encoder))

    def _run(self, key: _TrialKey, executable: str, encoder: str) -> None:
        result = run_encoder_trial(executable, encoder, self._runner)
        with self._lock:
            self._results[key] = result
            self._pending.discard(key)


def check_encoder(capability: FfmpegCapability, encoder: str, cache: EncoderTrialCache) -> EncoderTrialResult | None:
    """인코더 목록과 시험 결과로 사용 가능 여부를 돌려줌. 시험 전이면 None이고 시험을 시작함

    - 호출자는 FFmpeg 자체가 사용 가능한지(capability.usable)를 먼저 확인해야 함
    """
    if encoder not in capability.encoders:
        return EncoderTrialResult(encoder, usable=False, reason=NOT_LISTED_REASON)
    result = cache.lookup(capability, encoder)
    if result is None:
        cache.request(capability, encoder)
        return cache.lookup(capability, encoder)
    return result


SHARED_ENCODER_TRIAL_CACHE: EncoderTrialCache = EncoderTrialCache()
