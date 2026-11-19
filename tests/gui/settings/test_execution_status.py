"""재인코딩 탭 상태 문구 계산(FFmpeg 상태, 목표 인코딩 방식 상태)의 분기를 검증"""

import unittest

from src.core.encode.ffmpeg_capability import FfmpegCapability
from src.gui.settings.execution_status import STATUS_OBJECT_ERROR, STATUS_OBJECT_NORMAL, codec_status, ffmpeg_status
from src.gui.settings.execution_texts import CODEC_AVAILABLE_TEXT, CODEC_UNAVAILABLE_PREFIX


def _capability(
    *,
    usable: bool = True,
    version: tuple[int, int] | None = (6, 1),
    encoders: frozenset[str] = frozenset(),
    reason: str = "",
) -> FfmpegCapability:
    return FfmpegCapability("ffmpeg", "ffmpeg version", version, encoders=encoders, usable=usable, reason=reason)


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
    """목표 인코딩 방식의 이 PC 사용 가능 여부 문구를 검증"""

    def test_not_probed_yet_gives_empty_text(self) -> None:
        """FFmpeg 확인 전에는 문구를 비우는지 검증"""
        self.assertEqual(codec_status(None, "libx264"), (STATUS_OBJECT_NORMAL, ""))

    def test_encoder_in_listing_is_available(self) -> None:
        """FFmpeg 인코더 목록에 있는 목표 인코딩 방식은 사용 가능으로 보는지 검증"""
        capability = _capability(encoders=frozenset({"libx264"}))
        self.assertEqual(codec_status(capability, "libx264"), (STATUS_OBJECT_NORMAL, CODEC_AVAILABLE_TEXT))

    def test_encoder_missing_from_listing_gives_no_text_until_approved(self) -> None:
        """인코더가 목록에 없으면 승인 전 사유 문구 없이 빈 값인지 검증"""
        capability = _capability(encoders=frozenset({"libx264"}))
        self.assertEqual(codec_status(capability, "hevc_nvenc"), (STATUS_OBJECT_NORMAL, ""))

    def test_unusable_ffmpeg_passes_its_reason_through(self) -> None:
        """FFmpeg를 쓸 수 없으면 FFmpeg 사유를 이 PC 사용 불가 문구에 그대로 넣는지 검증"""
        capability = _capability(usable=False, version=None, reason="찾을 수 없음")
        self.assertEqual(
            codec_status(capability, "libx264"),
            (STATUS_OBJECT_ERROR, f"{CODEC_UNAVAILABLE_PREFIX}찾을 수 없음"),
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
