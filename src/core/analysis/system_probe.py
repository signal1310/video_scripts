"""시스템 CPU, GPU, FFmpeg 인코더 및 목표 코덱 기반 예상 인코딩 배속 추천 프로브 모듈"""

import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

from src.core.command_history import STAGE_CAPABILITY, command_scope
from src.core.config import DEFAULT_ENCODE_CODEC, DEFAULT_ENCODE_SPEED_FACTOR
from src.core.encoder_specs import get_encode_codec_label

_FFMPEG_PROBE_TIMEOUT_SEC: float = 2.0
_DEFAULT_CPU_THREADS: int = 4
_CPU_SPEED_MIN: float = 0.4
_CPU_SPEED_MAX: float = 5.0
_WIN_DISPLAY_ADAPTER_CLASS_KEY: str = (
    r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"
)
_TARGET_HW_ENCODERS: frozenset[str] = frozenset(
    {
        "h264_nvenc",
        "hevc_nvenc",
        "av1_nvenc",
        "h264_qsv",
        "hevc_qsv",
        "av1_qsv",
        "h264_amf",
        "hevc_amf",
        "av1_amf",
    }
)

_CPU_CODEC_RATE_PER_THREAD: dict[str, float] = {
    "libx265": 0.12,
    "libx264": 0.24,
    "libsvtav1": 0.08,
}

_NVENC_GPU_TIERS: tuple[tuple[str, float, str], ...] = (
    ("rtx 50", 6.0, "RTX 50"),
    ("rtx 40", 6.0, "RTX 40"),
    ("rtx 30", 4.5, "RTX 30"),
    ("rtx 20", 4.0, "RTX 20"),
    ("gtx", 3.0, "GTX"),
    ("nvidia", 4.0, "NVIDIA"),
)
_NVENC_CODEC_MULTIPLIERS: dict[str, float] = {
    "hevc_nvenc": 1.0,
    "h264_nvenc": 1.25,
    "av1_nvenc": 0.85,
}
_AV1_NVENC_SUPPORTED_GPUS: tuple[str, ...] = ("rtx 50", "rtx 40")

_VENDOR_HW_PROFILES: dict[str, tuple[tuple[str, ...], str, float]] = {
    "hevc_qsv": (("intel(r) arc", "intel arc", "uhd graphics", "iris"), "_qsv", 3.0),
    "hevc_amf": (("radeon rx", "amd radeon"), "_amf", 3.0),
}
_INTEL_ARC_KEYWORDS: tuple[str, ...] = ("intel(r) arc", "intel arc")
_INTEL_ARC_QSV_SPEED: float = 4.0


@dataclass(frozen=True, slots=True)
class SystemSpeedRecommendation:
    """현재 시스템 사양과 목표 인코더 기반 1080p 30fps 예상 인코딩 배속 추천 결과"""

    recommended_speed_factor: float
    encoder_label: str
    detail_summary: str


def _recommend_cpu_codec_speed(
    target_codec: str, safe_threads: int
) -> SystemSpeedRecommendation:
    rate = _CPU_CODEC_RATE_PER_THREAD[target_codec]
    codec_label = get_encode_codec_label(target_codec)
    speed = min(_CPU_SPEED_MAX, max(_CPU_SPEED_MIN, round(safe_threads * rate, 1)))
    return SystemSpeedRecommendation(
        recommended_speed_factor=speed,
        encoder_label=codec_label,
        detail_summary=f"목표 {codec_label}, CPU {safe_threads}스레드 → {speed:.1f}배속 추천",
    )


def _match_nvenc_tier(gpu_names: tuple[str, ...]) -> tuple[float, str, str] | None:
    joined_lower = " | ".join(gpu_names).lower()
    for keyword, base_speed, tier_name in _NVENC_GPU_TIERS:
        if keyword in joined_lower:
            matched_name = next(
                (name for name in gpu_names if keyword in name.lower()),
                gpu_names[0],
            )
            return base_speed, tier_name, matched_name
    return None


def _recommend_nvenc_speed(
    target_codec: str,
    safe_threads: int,
    encoders: frozenset[str],
    gpu_names: tuple[str, ...],
) -> SystemSpeedRecommendation:
    codec_label = get_encode_codec_label(target_codec)
    tier = _match_nvenc_tier(gpu_names)
    joined_lower = " | ".join(gpu_names).lower()

    if target_codec == "av1_nvenc":
        has_av1_hw = "av1_nvenc" in encoders or any(
            kw in joined_lower for kw in _AV1_NVENC_SUPPORTED_GPUS
        )
        if not has_av1_hw:
            return SystemSpeedRecommendation(
                recommended_speed_factor=DEFAULT_ENCODE_SPEED_FACTOR,
                encoder_label=codec_label,
                detail_summary=(
                    "AV1 NVENC 지원 GPU(RTX 40+) 또는 인코더 미감지 → "
                    f"기본 {DEFAULT_ENCODE_SPEED_FACTOR:.1f}배속 유지"
                ),
            )

    mult = _NVENC_CODEC_MULTIPLIERS[target_codec]
    if tier is not None:
        base_speed, _tier_name, gpu_name = tier
        speed = round(base_speed * mult, 1)
        return SystemSpeedRecommendation(
            recommended_speed_factor=speed,
            encoder_label=codec_label,
            detail_summary=f"목표 {codec_label}, {gpu_name} (CPU {safe_threads}스레드) → {speed:.1f}배속 추천",
        )

    if target_codec in encoders or any(e.endswith("_nvenc") for e in encoders):
        speed = round(4.0 * mult, 1)
        return SystemSpeedRecommendation(
            recommended_speed_factor=speed,
            encoder_label=codec_label,
            detail_summary=f"목표 {codec_label}, FFmpeg NVENC 지원 → {speed:.1f}배속 추천",
        )

    return SystemSpeedRecommendation(
        recommended_speed_factor=DEFAULT_ENCODE_SPEED_FACTOR,
        encoder_label=codec_label,
        detail_summary=f"{codec_label} 하드웨어 미감지 → 기본 {DEFAULT_ENCODE_SPEED_FACTOR:.1f}배속 유지",
    )


def _recommend_vendor_hw_speed(
    target_codec: str,
    safe_threads: int,
    encoders: frozenset[str],
    gpu_names: tuple[str, ...],
) -> SystemSpeedRecommendation:
    codec_label = get_encode_codec_label(target_codec)
    gpu_keywords, enc_suffix, default_speed = _VENDOR_HW_PROFILES[target_codec]
    joined_lower = " | ".join(gpu_names).lower()
    matched_gpu = next(
        (name for name in gpu_names if any(kw in name.lower() for kw in gpu_keywords)),
        None,
    )
    has_ffmpeg_enc = target_codec in encoders or any(
        e.endswith(enc_suffix) for e in encoders
    )

    if matched_gpu is not None:
        speed = (
            _INTEL_ARC_QSV_SPEED
            if target_codec == "hevc_qsv"
            and any(kw in joined_lower for kw in _INTEL_ARC_KEYWORDS)
            else default_speed
        )
        return SystemSpeedRecommendation(
            recommended_speed_factor=speed,
            encoder_label=codec_label,
            detail_summary=f"목표 {codec_label}, {matched_gpu} (CPU {safe_threads}스레드) → {speed:.1f}배속 추천",
        )
    if has_ffmpeg_enc:
        return SystemSpeedRecommendation(
            recommended_speed_factor=default_speed,
            encoder_label=codec_label,
            detail_summary=f"목표 {codec_label}, FFmpeg 지원 → {default_speed:.1f}배속 추천",
        )
    return SystemSpeedRecommendation(
        recommended_speed_factor=DEFAULT_ENCODE_SPEED_FACTOR,
        encoder_label=codec_label,
        detail_summary=f"{codec_label} 하드웨어 미감지 → 기본 {DEFAULT_ENCODE_SPEED_FACTOR:.1f}배속 유지",
    )


def recommend_encode_speed_for_profile(
    cpu_threads: int,
    encoders: frozenset[str],
    gpu_names: tuple[str, ...] = (),
    target_codec: str = DEFAULT_ENCODE_CODEC,
) -> SystemSpeedRecommendation:
    """목표 코덱, CPU 스레드 수, FFmpeg 인코더, GPU 어댑터를 조합해 1080p 기준 배속 산출"""
    safe_threads = max(1, int(cpu_threads))
    if target_codec in _CPU_CODEC_RATE_PER_THREAD:
        return _recommend_cpu_codec_speed(target_codec, safe_threads)
    if target_codec in _NVENC_CODEC_MULTIPLIERS:
        return _recommend_nvenc_speed(target_codec, safe_threads, encoders, gpu_names)
    if target_codec in _VENDOR_HW_PROFILES:
        return _recommend_vendor_hw_speed(target_codec, safe_threads, encoders, gpu_names)
    return _recommend_cpu_codec_speed("libx265", safe_threads)


def _probe_ffmpeg_encoders() -> frozenset[str]:
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        return frozenset()
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with command_scope(STAGE_CAPABILITY, [ffmpeg_bin, "-hide_banner", "-encoders"]) as handle:
        try:
            proc = subprocess.run(
                [ffmpeg_bin, "-hide_banner", "-encoders"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=_FFMPEG_PROBE_TIMEOUT_SEC,
                creationflags=creationflags,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            logging.getLogger(__name__).debug("ffmpeg 인코더 목록 조회 생략", exc_info=True)
            return frozenset()
        handle.exit_code = proc.returncode

    if proc.returncode != 0:
        return frozenset()
    found: set[str] = set()
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[1] in _TARGET_HW_ENCODERS:
            found.add(parts[1])
    return frozenset(found)


def _probe_windows_gpu_names() -> tuple[str, ...]:
    names: list[str] = []
    if sys.platform == "win32":
        import winreg

        try:
            with winreg.OpenKey(
                winreg.HKEY_LOCAL_MACHINE, _WIN_DISPLAY_ADAPTER_CLASS_KEY
            ) as cls_key:
                sub_count, _, _ = winreg.QueryInfoKey(cls_key)
                for idx in range(sub_count):
                    sub_name = winreg.EnumKey(cls_key, idx)
                    if not sub_name.isdigit():
                        continue
                    try:
                        with winreg.OpenKey(cls_key, sub_name) as dev_key:
                            desc, _ = winreg.QueryValueEx(dev_key, "DriverDesc")
                            if isinstance(desc, str) and desc.strip():
                                names.append(desc.strip())
                    except OSError:
                        continue
        except OSError:
            logging.getLogger(__name__).debug(
                "Windows GPU 레지스트리 조회 생략", exc_info=True
            )
    return tuple(names)


def detect_recommended_encode_speed(
    target_codec: str = DEFAULT_ENCODE_CODEC,
) -> SystemSpeedRecommendation:
    """현재 시스템의 CPU, GPU, FFmpeg 인코더와 목표 코덱을 반영한 1080p 30fps 추천 배속 반환"""
    cpu_threads = os.cpu_count() or _DEFAULT_CPU_THREADS
    encoders = _probe_ffmpeg_encoders()
    gpu_names = _probe_windows_gpu_names()
    return recommend_encode_speed_for_profile(
        cpu_threads, encoders, gpu_names, target_codec=target_codec
    )
