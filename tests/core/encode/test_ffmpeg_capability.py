"""FFmpeg 버전과 인코더, 필터 목록 파싱, 사용 가능 여부 판정과 실행 실패 사유를 검증하는 단위 테스트"""

import unittest
from unittest import mock

from src.core.encode import ffmpeg_capability
from src.core.encode.ffmpeg_capability import (
    MIN_FFMPEG_VERSION,
    evaluate_capability,
    parse_ffmpeg_version,
    parse_listing_names,
    probe_ffmpeg_capability,
)

_VERSION_OUTPUT = "ffmpeg version 2024-12-26-git-fe04b93afa-full_build-www.gyan.dev Copyright (c) 2000-2024\n"
_ENCODER_OUTPUT = """Encoders:
 V....D hevc_nvenc           NVIDIA NVENC hevc encoder (codec hevc)
 V..... libx265              libx265 H.265 / HEVC (codec hevc)
"""
_FILTER_OUTPUT = """Filters:
 ..  libvmaf          VV->V      Calculate the VMAF between two video streams.
 T.  cropdetect       V->V       Auto-detect crop size.
"""


class TestVersionParsing(unittest.TestCase):
    """버전 문자열의 형식별 해석"""

    def test_numeric_and_n_prefixed_versions(self) -> None:
        """숫자형과 n 접두사 버전을 (주, 부) 튜플로 해석함을 검증"""
        self.assertEqual(parse_ffmpeg_version("ffmpeg version 7.0.2-full_build"), ("7.0.2-full_build", (7, 0)))
        self.assertEqual(parse_ffmpeg_version("ffmpeg version n6.1"), ("n6.1", (6, 1)))

    def test_date_builds_are_judged_by_the_5_1_release_date(self) -> None:
        """2023년 2월 이후 날짜형 빌드는 최소 버전 이상, 그 이전은 오래된 빌드(0.0)로 판정함을 검증"""
        self.assertEqual(parse_ffmpeg_version(_VERSION_OUTPUT)[1], MIN_FFMPEG_VERSION)
        self.assertEqual(parse_ffmpeg_version("ffmpeg version 2021-03-01-git-abc")[1], (0, 0))

    def test_unrecognized_output_gives_no_version(self) -> None:
        """버전 줄이 없으면 빈 문자열과 None을 돌려줌을 검증"""
        self.assertEqual(parse_ffmpeg_version("hello"), ("", None))


class TestListingAndJudgment(unittest.TestCase):
    """인코더, 필터 목록 파싱과 사용 가능 판정"""

    def test_listing_names_are_extracted_from_flag_rows(self) -> None:
        """인코더와 필터 줄에서 이름만 추출하고 머리말은 무시함을 검증"""
        self.assertEqual(parse_listing_names(_ENCODER_OUTPUT), frozenset({"hevc_nvenc", "libx265"}))
        self.assertEqual(parse_listing_names(_FILTER_OUTPUT), frozenset({"libvmaf", "cropdetect"}))

    def test_old_version_is_not_usable_with_reason(self) -> None:
        """최소 버전보다 낮으면 사용 불가이고 사유에 최소 버전이 들어감을 검증"""
        result = evaluate_capability("ff", "n4.4", (4, 4), frozenset(), frozenset())
        self.assertFalse(result.usable)
        self.assertIn("5.1", result.reason)

    def test_current_version_is_usable_and_reports_vmaf(self) -> None:
        """최소 버전 이상이면 사용 가능하고 libvmaf 유무를 필터 목록으로 알려줌을 검증"""
        result = evaluate_capability("ff", "7.0", (7, 0), frozenset({"hevc_nvenc"}), frozenset({"libvmaf"}))
        self.assertTrue(result.usable)
        self.assertEqual(result.reason, "")
        self.assertTrue(result.has_vmaf)
        self.assertFalse(evaluate_capability("ff", "7.0", (7, 0), frozenset(), frozenset()).has_vmaf)

    def test_unknown_version_is_usable_with_note(self) -> None:
        """버전을 알 수 없으면 사용은 허용하되 사유에 확인하지 못했음을 남김을 검증"""
        result = evaluate_capability("ff", "custom", None, frozenset(), frozenset())
        self.assertTrue(result.usable)
        self.assertIn("확인하지", result.reason)


class TestProbeFailures(unittest.TestCase):
    """실행 파일이 없거나 실행이 실패할 때의 사유"""

    def test_missing_configured_executable_is_reported_without_crash(self) -> None:
        """설정 경로의 파일이 없으면 예외 없이 사용 불가와 사유를 반환함을 검증"""
        result = probe_ffmpeg_capability("ffmpeg-custom-missing")
        self.assertFalse(result.usable)
        self.assertIn("실행에 실패", result.reason)

    def test_missing_path_entry_is_reported(self) -> None:
        """비운 설정에서 PATH에도 FFmpeg가 없으면 설정 안내 사유를 반환함을 검증"""
        with mock.patch.object(ffmpeg_capability.shutil, "which", return_value=None):
            result = probe_ffmpeg_capability("")
        self.assertFalse(result.usable)
        self.assertIn("찾을 수 없습니다", result.reason)

    def test_probe_collects_version_encoders_and_filters(self) -> None:
        """실행 출력을 조합해 능력 결과를 만드는지 검증(실행은 가짜 출력으로 대체)"""
        outputs = iter([_VERSION_OUTPUT, _ENCODER_OUTPUT, _FILTER_OUTPUT])
        with mock.patch.object(ffmpeg_capability.shutil, "which", return_value="ffmpeg-custom"), \
             mock.patch.object(ffmpeg_capability, "_run_text", side_effect=lambda _cmd: next(outputs)):
            result = probe_ffmpeg_capability("")
        self.assertTrue(result.usable)
        self.assertIn("hevc_nvenc", result.encoders)
        self.assertTrue(result.has_vmaf)


if __name__ == "__main__":
    unittest.main(verbosity=2)
