"""system_probe의 배속 추천 규칙과 FFmpeg, GPU 조회 경계(subprocess, 레지스트리) 검증"""

import subprocess
import sys
import types
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

from src.core.analysis.system_probe import (
    SystemSpeedRecommendation,
    detect_recommended_encode_speed,
    recommend_encode_speed_for_profile,
)
from src.core.config import DEFAULT_ENCODE_SPEED_FACTOR

_RTX_4070 = "NVIDIA GeForce RTX 4070"
_RTX_3080 = "NVIDIA GeForce RTX 3080"


def _recommend(codec: str, *, threads: int = 16, encoders: tuple[str, ...] = (), gpus: tuple[str, ...] = ()) -> SystemSpeedRecommendation:
    return recommend_encode_speed_for_profile(threads, frozenset(encoders), gpus, target_codec=codec)


class TestRecommendEncodeSpeed(unittest.TestCase):
    """코덱, 스레드 수, 인코더, GPU 조합에 따른 추천 배속 규칙을 검증"""

    def test_cpu_codecs_scale_with_threads_and_clamp(self) -> None:
        """CPU 코덱은 스레드 수에 비례하고 최소 0.4, 최대 5.0배속으로 제한됨을 검증"""
        cases = [
            ("libx265", 16, 1.9),
            ("libx264", 16, 3.8),
            ("libsvtav1", 4, 0.4),
            ("libx264", 64, 5.0),
            ("libx265", 0, 0.4),
            ("unknown_codec", 16, 1.9),
            ("h264_qsv", 16, 1.9),
        ]
        for codec, threads, expected in cases:
            with self.subTest(codec=codec, threads=threads):
                self.assertEqual(_recommend(codec, threads=threads).recommended_speed_factor, expected)
        self.assertIn("CPU 16스레드", _recommend("libx265").detail_summary)

    def test_nvenc_speed_follows_gpu_tier_and_codec_multiplier(self) -> None:
        """NVENC는 GPU 세대별 기본 배속에 코덱 배수를 곱하고, 세대 키워드 우선순위를 따름을 검증"""
        cases = [
            ("hevc_nvenc", _RTX_4070, 6.0),
            ("h264_nvenc", _RTX_4070, 7.5),
            ("av1_nvenc", _RTX_4070, 5.1),
            ("hevc_nvenc", _RTX_3080, 4.5),
            ("hevc_nvenc", "NVIDIA GeForce RTX 2060", 4.0),
            ("hevc_nvenc", "NVIDIA GeForce GTX 1060", 3.0),
            ("hevc_nvenc", "NVIDIA Quadro P2000", 4.0),
        ]
        for codec, gpu, expected in cases:
            with self.subTest(codec=codec, gpu=gpu):
                result = _recommend(codec, gpus=(gpu,))
                self.assertEqual(result.recommended_speed_factor, expected)
                self.assertIn(gpu, result.detail_summary)

    def test_nvenc_picks_matching_gpu_name_among_several_adapters(self) -> None:
        """어댑터가 여러 개면 NVENC 세대에 맞는 GPU 이름을 요약에 표시함을 검증"""
        result = _recommend("hevc_nvenc", gpus=("Intel(R) UHD Graphics 770", _RTX_4070))
        self.assertEqual(result.recommended_speed_factor, 6.0)
        self.assertIn(_RTX_4070, result.detail_summary)

    def test_nvenc_without_known_gpu_falls_back_to_ffmpeg_support_or_default(self) -> None:
        """GPU를 못 찾으면 FFmpeg NVENC 지원 여부로 4.0배속, 없으면 기본 배속을 유지함을 검증"""
        supported = _recommend("hevc_nvenc", encoders=("hevc_nvenc",))
        self.assertEqual(supported.recommended_speed_factor, 4.0)
        self.assertIn("FFmpeg NVENC 지원", supported.detail_summary)
        self.assertEqual(_recommend("h264_nvenc", encoders=("hevc_nvenc",)).recommended_speed_factor, 5.0)

        missing = _recommend("hevc_nvenc")
        self.assertEqual(missing.recommended_speed_factor, DEFAULT_ENCODE_SPEED_FACTOR)
        self.assertIn("미감지", missing.detail_summary)

    def test_av1_nvenc_requires_supported_gpu_or_encoder(self) -> None:
        """AV1 NVENC는 RTX 40 이상 GPU나 인코더가 있을 때만 추천하고 아니면 기본 배속을 유지함을 검증"""
        unsupported = _recommend("av1_nvenc", gpus=(_RTX_3080,))
        self.assertEqual(unsupported.recommended_speed_factor, DEFAULT_ENCODE_SPEED_FACTOR)
        self.assertIn("미감지", unsupported.detail_summary)

        with_encoder = _recommend("av1_nvenc", encoders=("av1_nvenc",), gpus=(_RTX_3080,))
        self.assertEqual(with_encoder.recommended_speed_factor, 3.8)

    def test_vendor_hardware_profiles(self) -> None:
        """Intel QSV, AMD AMF는 GPU 이름과 FFmpeg 인코더 순으로 판정하고 Intel Arc만 가산함을 검증"""
        cases: list[tuple[str, tuple[str, ...], tuple[str, ...], float, str]] = [
            ("hevc_qsv", (), ("Intel(R) Arc(TM) A770",), 4.0, "Intel(R) Arc(TM) A770"),
            ("hevc_qsv", (), ("Intel(R) UHD Graphics 770",), 3.0, "UHD Graphics"),
            ("hevc_amf", (), ("AMD Radeon RX 7800 XT",), 3.0, "AMD Radeon RX 7800 XT"),
            ("hevc_qsv", ("hevc_qsv",), (), 3.0, "FFmpeg 지원"),
            ("hevc_amf", ("h264_amf",), (), 3.0, "FFmpeg 지원"),
            ("hevc_qsv", ("hevc_amf",), (), DEFAULT_ENCODE_SPEED_FACTOR, "미감지"),
        ]
        for codec, encoders, gpus, expected, summary_part in cases:
            with self.subTest(codec=codec, gpus=gpus, encoders=encoders):
                result = _recommend(codec, encoders=encoders, gpus=gpus)
                self.assertEqual(result.recommended_speed_factor, expected)
                self.assertIn(summary_part, result.detail_summary)


class _FakeRegistry:
    """GPU 어댑터 클래스 키를 흉내내는 최소 winreg 대체 객체"""

    HKEY_LOCAL_MACHINE = object()

    def __init__(self, devices: dict[str, Any], *, class_key_error: bool = False) -> None:
        self._devices = devices
        self._class_key_error = class_key_error

    @contextmanager
    def OpenKey(self, parent: Any, name: str) -> Generator[Any]:
        if parent is self.HKEY_LOCAL_MACHINE:
            if self._class_key_error:
                raise OSError("class key missing")
            yield self
            return
        device = self._devices[name]
        if isinstance(device, OSError):
            raise device
        yield device

    def QueryInfoKey(self, _key: Any) -> tuple[int, int, int]:
        return len(self._devices), 0, 0

    def EnumKey(self, _key: Any, index: int) -> str:
        return list(self._devices)[index]

    def QueryValueEx(self, device: Any, _name: str) -> tuple[Any, int]:
        return device, 1


@contextmanager
def _patched_system(
    *,
    ffmpeg_stdout: str | None = None,
    ffmpeg_error: Exception | None = None,
    ffmpeg_returncode: int = 0,
    ffmpeg_path: str | None = "ffmpeg",
    registry: _FakeRegistry | None = None,
    cpu_count: int | None = 16,
) -> Generator[MagicMock]:
    run = MagicMock(return_value=subprocess.CompletedProcess([], ffmpeg_returncode, stdout=ffmpeg_stdout or ""))
    if ffmpeg_error is not None:
        run.side_effect = ffmpeg_error
    modules: dict[str, Any] = {}
    if registry is not None:
        modules["winreg"] = types.SimpleNamespace(
            HKEY_LOCAL_MACHINE=registry.HKEY_LOCAL_MACHINE,
            OpenKey=registry.OpenKey,
            QueryInfoKey=registry.QueryInfoKey,
            EnumKey=registry.EnumKey,
            QueryValueEx=registry.QueryValueEx,
        )
    with (
        patch("src.core.analysis.system_probe.shutil.which", return_value=ffmpeg_path),
        patch("src.core.analysis.system_probe.subprocess.run", run),
        patch("src.core.analysis.system_probe.os.cpu_count", return_value=cpu_count),
        patch.object(sys, "platform", "win32" if registry else "linux"),
        patch.dict(sys.modules, modules),
    ):
        yield run


class TestDetectRecommendedSpeed(unittest.TestCase):
    """실제 시스템 조회 경계를 가짜로 바꿔 detect_recommended_encode_speed의 조합을 검증"""

    def test_cpu_count_defaults_to_four_threads_when_unknown(self) -> None:
        """CPU 스레드 수를 알 수 없으면 기본 4스레드로 계산함을 검증"""
        with _patched_system(cpu_count=None, ffmpeg_path=None):
            result = detect_recommended_encode_speed("libx265")
        self.assertIn("CPU 4스레드", result.detail_summary)
        self.assertEqual(result.recommended_speed_factor, 0.5)

    def test_ffmpeg_encoder_listing_keeps_only_target_hardware_encoders(self) -> None:
        """인코더 목록에서 목표 하드웨어 인코더만 인식하고 짧은 줄, 비대상 인코더는 무시함을 검증"""
        listing = (
            " V....D libx264              libx264 H.264\n"
            " V....D hevc_nvenc           NVIDIA NVENC hevc encoder\n"
            "short\n"
            " V....D h264_vaapi           VAAPI H.264\n"
            "V av1_nvenc\n"
        )
        with _patched_system(ffmpeg_stdout=listing) as run:
            found = detect_recommended_encode_speed("hevc_nvenc")
            self.assertIn("FFmpeg NVENC 지원", found.detail_summary)
            self.assertIn("FFmpeg NVENC 지원", detect_recommended_encode_speed("av1_nvenc").detail_summary)
            self.assertEqual(run.call_args.args[0], ["ffmpeg", "-hide_banner", "-encoders"])

        with _patched_system(ffmpeg_stdout=" V....D h264_vaapi VAAPI\n"):
            self.assertIn("미감지", detect_recommended_encode_speed("hevc_nvenc").detail_summary)

    def test_ffmpeg_probe_failures_are_treated_as_no_encoders(self) -> None:
        """ffmpeg가 없거나 실행에 실패하거나 비정상 종료해도 예외 없이 인코더 없음으로 처리함을 검증"""
        listing = " V....D hevc_nvenc NVENC\n"
        failures: list[dict[str, Any]] = [
            {"ffmpeg_path": None, "ffmpeg_stdout": listing},
            {"ffmpeg_error": OSError("boom")},
            {"ffmpeg_error": subprocess.TimeoutExpired("ffmpeg", 2.0)},
            {"ffmpeg_stdout": listing, "ffmpeg_returncode": 1},
        ]
        for options in failures:
            with self.subTest(options=list(options)), _patched_system(**options):
                self.assertIn("미감지", detect_recommended_encode_speed("hevc_nvenc").detail_summary)

    def test_windows_gpu_names_come_from_numbered_adapter_keys_only(self) -> None:
        """레지스트리에서 숫자 이름 어댑터 키의 비어 있지 않은 드라이버 설명만 GPU로 인식함을 검증"""
        registry = _FakeRegistry(
            {
                "0000": f"  {_RTX_4070}  ",
                "0001": OSError("access denied"),
                "0002": "   ",
                "Properties": "Should Be Ignored RTX 5090",
            }
        )
        with _patched_system(registry=registry, ffmpeg_path=None):
            result = detect_recommended_encode_speed("hevc_nvenc")
        self.assertEqual(result.recommended_speed_factor, 6.0)
        self.assertIn(_RTX_4070, result.detail_summary)
        self.assertNotIn("5090", result.detail_summary)

    def test_registry_failure_or_other_platform_means_no_gpu(self) -> None:
        """레지스트리 조회 실패나 비Windows 플랫폼에서는 GPU 없음으로 처리함을 검증"""
        with _patched_system(registry=_FakeRegistry({}, class_key_error=True), ffmpeg_path=None):
            self.assertIn("미감지", detect_recommended_encode_speed("hevc_nvenc").detail_summary)
        with _patched_system(ffmpeg_path=None):
            self.assertIn("미감지", detect_recommended_encode_speed("hevc_nvenc").detail_summary)


if __name__ == "__main__":
    unittest.main()
