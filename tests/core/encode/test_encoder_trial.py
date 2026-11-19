"""인코더 시험 인코딩의 명령 생성, 결과 판정, 실행 실패 처리, 캐시 동작을 검증(FFmpeg 없이 가짜 runner 사용)"""

import sys
import time
import unittest
from collections.abc import Callable

from src.core.encode.encoder_trial import (
    ENCODER_TRIAL_TIMEOUT_SEC,
    NOT_LISTED_REASON,
    TRIAL_FAILED_REASON,
    EncoderTrialCache,
    EncoderTrialResult,
    build_trial_command,
    check_encoder,
    judge_trial,
    run_encoder_trial,
    run_trial_process,
)
from src.core.encode.ffmpeg_capability import FfmpegCapability

_WAIT_LIMIT_SEC: float = 5.0
_WAIT_STEP_SEC: float = 0.01


def _capability(
    *,
    executable: str = "ffmpeg",
    version_text: str = "6.1",
    encoders: frozenset[str] = frozenset({"libx264", "hevc_nvenc"}),
) -> FfmpegCapability:
    return FfmpegCapability(executable, version_text, (6, 1), encoders=encoders, usable=True)


def _sync_spawn(task: Callable[[], None]) -> None:
    task()


class _RecordingRunner:
    """호출된 명령을 기록하고 정해진 종료 코드를 돌려주는 가짜 runner"""

    def __init__(self, returncode: int | None) -> None:
        self.returncode: int | None = returncode
        self.calls: list[tuple[list[str], float]] = []

    def __call__(self, command: list[str], timeout_sec: float) -> int | None:
        self.calls.append((command, timeout_sec))
        return self.returncode


class BuildTrialCommandTests(unittest.TestCase):
    """시험 명령이 합성 입력 1프레임을 지정 인코더로 보내고 출력을 버리는지 검증"""

    def test_command_encodes_one_synthetic_frame_and_discards_output(self) -> None:
        """시험 명령이 lavfi 입력 1프레임, 지정 인코더, null 출력으로 구성되는지 검증"""
        command = build_trial_command("C:/tools/ffmpeg.exe", "hevc_nvenc")
        self.assertEqual(command[0], "C:/tools/ffmpeg.exe")
        self.assertEqual(command[command.index("-c:v") + 1], "hevc_nvenc")
        self.assertEqual(command[command.index("-frames:v") + 1], "1")
        self.assertEqual(command[command.index("-i") - 1], "lavfi")
        self.assertEqual(command[-3:], ["-f", "null", "-"])
        self.assertIn("-nostdin", command)


class JudgeTrialTests(unittest.TestCase):
    """종료 코드별 시험 결과 판정을 검증"""

    def test_zero_exit_code_means_usable(self) -> None:
        """종료 코드 0이면 사용 가능이고 사유가 비어 있는지 검증"""
        self.assertEqual(judge_trial("libx264", 0), EncoderTrialResult("libx264", usable=True, reason=""))

    def test_non_zero_exit_code_gives_the_failure_reason(self) -> None:
        """종료 코드가 0이 아니면 실패 사유를 주는지 검증"""
        self.assertEqual(
            judge_trial("hevc_nvenc", 1),
            EncoderTrialResult("hevc_nvenc", usable=False, reason=TRIAL_FAILED_REASON),
        )

    def test_timeout_or_launch_failure_counts_as_failure(self) -> None:
        """시간 초과나 실행 실패(None)도 실패로 판정하는지 검증"""
        self.assertFalse(judge_trial("hevc_nvenc", None).usable)


class RunEncoderTrialTests(unittest.TestCase):
    """시험 실행이 명령을 만들어 runner에 넘기고 결과를 판정하는지 검증"""

    def test_runner_gets_the_trial_command_with_the_fixed_time_limit(self) -> None:
        """runner가 시험 명령과 명명 상수 시간 제한을 받는지 검증"""
        runner = _RecordingRunner(0)
        result = run_encoder_trial("ffmpeg", "libx264", runner)
        self.assertTrue(result.usable)
        command, timeout_sec = runner.calls[0]
        self.assertEqual(command, build_trial_command("ffmpeg", "libx264"))
        self.assertEqual(timeout_sec, ENCODER_TRIAL_TIMEOUT_SEC)


class RunTrialProcessTests(unittest.TestCase):
    """실제 프로세스 실행 도우미의 종료 코드, 시간 초과, 실행 실패 처리를 검증(FFmpeg 대신 파이썬 사용)"""

    def test_exit_code_is_returned(self) -> None:
        """정상 종료하면 종료 코드를 돌려주는지 검증"""
        self.assertEqual(run_trial_process([sys.executable, "-c", "pass"], 30.0), 0)

    def test_timeout_returns_none(self) -> None:
        """제한 시간을 넘으면 None을 돌려주는지 검증"""
        self.assertIsNone(run_trial_process([sys.executable, "-c", "import time; time.sleep(30)"], 0.2))

    def test_missing_executable_returns_none(self) -> None:
        """실행 파일이 없으면 예외 없이 None을 돌려주는지 검증"""
        self.assertIsNone(run_trial_process(["__no_such_encoder_trial_binary__"], 5.0))


class CheckEncoderTests(unittest.TestCase):
    """인코더 목록과 시험 캐시를 합친 사용 가능 여부 판정을 검증"""

    def test_encoder_missing_from_listing_is_unusable_without_a_trial(self) -> None:
        """목록에 없는 인코더는 시험하지 않고 목록 사유로 사용 불가인지 검증"""
        runner = _RecordingRunner(0)
        cache = EncoderTrialCache(runner=runner, spawn=_sync_spawn)
        result = check_encoder(_capability(), "av1_nvenc", cache)
        self.assertEqual(result, EncoderTrialResult("av1_nvenc", usable=False, reason=NOT_LISTED_REASON))
        self.assertEqual(runner.calls, [])

    def test_listed_encoder_gives_none_until_the_trial_finishes(self) -> None:
        """시험이 백그라운드에서 끝나기 전에는 None을 주고, 시험을 한 번만 시작하는지 검증"""
        runner = _RecordingRunner(0)
        cache = EncoderTrialCache(runner=runner, spawn=lambda task: None)
        capability = _capability()
        self.assertIsNone(check_encoder(capability, "libx264", cache))
        self.assertIsNone(check_encoder(capability, "libx264", cache))
        self.assertEqual(runner.calls, [])

    def test_listed_encoder_gives_the_trial_result_once_done(self) -> None:
        """시험이 끝난 뒤에는 실패 사유가 담긴 결과를 주고 다시 시험하지 않는지 검증"""
        runner = _RecordingRunner(1)
        cache = EncoderTrialCache(runner=runner, spawn=_sync_spawn)
        capability = _capability()
        self.assertEqual(
            check_encoder(capability, "hevc_nvenc", cache),
            EncoderTrialResult("hevc_nvenc", usable=False, reason=TRIAL_FAILED_REASON),
        )
        check_encoder(capability, "hevc_nvenc", cache)
        self.assertEqual(len(runner.calls), 1)


class EncoderTrialCacheTests(unittest.TestCase):
    """시험 캐시의 키 구성, 중복 시작 방지, 백그라운드 시작을 검증"""

    def test_result_is_keyed_by_executable_version_and_encoder(self) -> None:
        """같은 인코더라도 실행 파일이나 버전이 다르면 다시 시험하는지 검증"""
        runner = _RecordingRunner(0)
        cache = EncoderTrialCache(runner=runner, spawn=_sync_spawn)
        cache.request(_capability(), "libx264")
        cache.request(_capability(), "libx264")
        self.assertEqual(len(runner.calls), 1)
        cache.request(_capability(executable="D:/ffmpeg.exe"), "libx264")
        cache.request(_capability(version_text="7.0"), "libx264")
        self.assertEqual(len(runner.calls), 3)
        self.assertIsNotNone(cache.lookup(_capability(), "libx264"))
        self.assertIsNone(cache.lookup(_capability(), "hevc_nvenc"))

    def test_default_spawn_runs_the_trial_on_a_background_thread(self) -> None:
        """기본 시작 방식이 백그라운드 스레드에서 시험을 끝내 결과를 캐시에 남기는지 검증"""
        runner = _RecordingRunner(0)
        cache = EncoderTrialCache(runner=runner)
        capability = _capability()
        cache.request(capability, "libx264")
        waited = 0.0
        while cache.lookup(capability, "libx264") is None and waited < _WAIT_LIMIT_SEC:
            time.sleep(_WAIT_STEP_SEC)
            waited += _WAIT_STEP_SEC
        result = cache.lookup(capability, "libx264")
        self.assertTrue(result is not None and result.usable)


if __name__ == "__main__":
    unittest.main(verbosity=2)
