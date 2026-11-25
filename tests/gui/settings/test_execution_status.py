"""인코딩 탭 상태 문구 계산(FFmpeg 상태, 목표 인코딩 방식 상태)의 분기를 검증"""

import unittest
from collections.abc import Callable

from src.core.encode.encoder_trial import NOT_LISTED_REASON, TRIAL_FAILED_REASON, EncoderTrialCache
from src.core.encode.ffmpeg_capability import FfmpegCapability
from src.gui.settings.execution_status import (
    STATUS_OBJECT_ERROR,
    STATUS_OBJECT_NORMAL,
    CodecStatus,
    codec_status,
    ffmpeg_status,
)
from src.gui.settings.execution_texts import (
    CODEC_AVAILABLE_TEXT,
    CODEC_CHECKING_TEXT,
    CODEC_UNAVAILABLE_PREFIX,
)


def _capability(
    *,
    usable: bool = True,
    version: tuple[int, int] | None = (6, 1),
    encoders: frozenset[str] = frozenset(),
    reason: str = "",
) -> FfmpegCapability:
    return FfmpegCapability("ffmpeg", "ffmpeg version", version, encoders=encoders, usable=usable, reason=reason)


def _cache(returncode: int | None) -> EncoderTrialCache:
    """시험을 바로 끝내고 지정한 종료 코드를 결과로 쓰는 캐시"""

    def runner(_command: list[str], _timeout_sec: float) -> int | None:
        return returncode

    return EncoderTrialCache(runner=runner, spawn=_run_now)


def _run_now(task: Callable[[], None]) -> None:
    task()


class FfmpegStatusTests(unittest.TestCase):
    """FFmpeg 확인 결과별 상태 문구와 객체 이름을 검증"""

    def test_usable_version_shows_major_and_minor(self) -> None:
        """버전을 아는 사용 가능 FFmpeg는 버전을 보여 주는지 검증"""
        self.assertEqual(ffmpeg_status(_capability()), (STATUS_OBJECT_NORMAL, "사용 가능: FFmpeg 6.1"))

    def test_usable_without_version_says_it_is_used_as_is(self) -> None:
        """버전을 모르는 FFmpeg는 그대로 쓴다는 안내를 보여 주는지 검증"""
        self.assertEqual(
            ffmpeg_status(_capability(version=None)),
            (STATUS_OBJECT_NORMAL, "사용 가능: 버전을 확인하지 못해 그대로 사용합니다"),
        )

    def test_unusable_shows_reason_in_error_style(self) -> None:
        """사용할 수 없는 FFmpeg는 사유를 오류 스타일로 보여 주는지 검증"""
        self.assertEqual(
            ffmpeg_status(_capability(usable=False, version=None, reason="찾을 수 없음")),
            (STATUS_OBJECT_ERROR, "사용할 수 없음: 찾을 수 없음"),
        )


class CodecStatusTests(unittest.TestCase):
    """목표 인코딩 방식의 이 PC 사용 가능 여부 상태 줄을 검증"""

    def test_not_probed_yet_gives_empty_text(self) -> None:
        """FFmpeg 확인 전에는 문구를 비우고 시험을 시작하지 않는지 검증"""
        self.assertEqual(codec_status(None, "libx264", _cache(0)), CodecStatus(STATUS_OBJECT_NORMAL, "", pending=False))

    def test_encoder_missing_from_listing_is_unusable_with_the_listing_reason(self) -> None:
        """인코더가 FFmpeg 목록에 없으면 목록 사유를 오류 스타일로 보여 주는지 검증"""
        capability = _capability(encoders=frozenset({"libx264"}))
        self.assertEqual(
            codec_status(capability, "hevc_nvenc", _cache(0)),
            CodecStatus(STATUS_OBJECT_ERROR, f"{CODEC_UNAVAILABLE_PREFIX}{NOT_LISTED_REASON}", pending=False),
        )

    def test_listed_encoder_that_passes_the_trial_is_available(self) -> None:
        """목록에 있고 시험 인코딩에 성공하면 사용 가능을 보여 주는지 검증"""
        capability = _capability(encoders=frozenset({"libx264"}))
        self.assertEqual(
            codec_status(capability, "libx264", _cache(0)),
            CodecStatus(STATUS_OBJECT_NORMAL, CODEC_AVAILABLE_TEXT, pending=False),
        )

    def test_listed_encoder_that_fails_the_trial_gives_the_trial_reason(self) -> None:
        """목록에 있어도 시험 인코딩에 실패하면 시험 실패 사유를 오류 스타일로 보여 주는지 검증"""
        capability = _capability(encoders=frozenset({"hevc_nvenc"}))
        self.assertEqual(
            codec_status(capability, "hevc_nvenc", _cache(1)),
            CodecStatus(STATUS_OBJECT_ERROR, f"{CODEC_UNAVAILABLE_PREFIX}{TRIAL_FAILED_REASON}", pending=False),
        )

    def test_trial_in_progress_shows_checking_and_asks_for_a_recheck(self) -> None:
        """시험이 끝나기 전에는 확인 중을 보이고 다시 확인이 필요하다고 알리는지 검증"""
        capability = _capability(encoders=frozenset({"hevc_nvenc"}))
        still_running = EncoderTrialCache(runner=lambda _c, _t: 0, spawn=lambda _task: None)
        self.assertEqual(
            codec_status(capability, "hevc_nvenc", still_running),
            CodecStatus(STATUS_OBJECT_NORMAL, CODEC_CHECKING_TEXT, pending=True),
        )

    def test_unusable_ffmpeg_passes_its_reason_through(self) -> None:
        """FFmpeg를 쓸 수 없으면 FFmpeg 사유를 사용 불가 문구에 그대로 넣고 시험하지 않는지 검증"""
        capability = _capability(usable=False, version=None, reason="찾을 수 없음")
        self.assertEqual(
            codec_status(capability, "libx264", _cache(0)),
            CodecStatus(STATUS_OBJECT_ERROR, f"{CODEC_UNAVAILABLE_PREFIX}찾을 수 없음", pending=False),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
