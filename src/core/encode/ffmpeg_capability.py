"""FFmpeg 실행 파일의 버전, 인코더, 필터 능력을 확인해 인코딩 사용 가능 여부와 사유를 만드는 모듈

- 텍스트 파싱 함수는 순수하며, 실행 함수는 subprocess 호출만 담당
- 최소 버전 5.1은 `-fps_mode` 옵션을 기준으로 함(결정 11)
"""

import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field

MIN_FFMPEG_VERSION: tuple[int, int] = (5, 1)
# - 날짜형 빌드(예: 2024-12-26-git-...)는 5.1 공개 시점(2023년 2월) 이후인지로 최소 버전을 판정
DATE_BUILD_MIN_YEAR_MONTH: tuple[int, int] = (2023, 2)
FFMPEG_PROBE_TIMEOUT_SEC: float = 10.0
_VERSION_PATTERN = re.compile(r"ffmpeg version\s+(\S+)")
_NUMERIC_VERSION_PATTERN = re.compile(r"^[nN]?(\d+)\.(\d+)")
_DATE_VERSION_PATTERN = re.compile(r"^(\d{4})-(\d{2})-\d{2}")
# - `ffmpeg -encoders`, `-filters` 목록 줄은 능력 플래그(2~6자) 뒤에 이름이 온다
_LISTING_ROW_PATTERN = re.compile(r"^\s[A-Z.]{2,6}\s+(\S+)", re.MULTILINE)
VMAF_FILTER_NAME: str = "libvmaf"


@dataclass(frozen=True, slots=True)
class FfmpegCapability:
    """FFmpeg 능력 확인 결과. usable이 False면 인코딩을 시작하지 않고 reason을 화면에 표시"""

    executable: str
    version_text: str
    version: tuple[int, int] | None
    encoders: frozenset[str] = field(default_factory=frozenset)
    filters: frozenset[str] = field(default_factory=frozenset)
    usable: bool = False
    reason: str = ""

    @property
    def has_vmaf(self) -> bool:
        """libvmaf 필터 사용 가능 여부(없으면 4단계에서 SSIM 대체)"""
        return VMAF_FILTER_NAME in self.filters


def parse_ffmpeg_version(version_output: str) -> tuple[str, tuple[int, int] | None]:
    """`ffmpeg -version` 출력에서 버전 문자열과 (주, 부) 버전 튜플을 얻는다

    - 날짜형 빌드는 최소 버전 기준 날짜 비교용으로 (연도, 월)을 그대로 돌려주지 않고 5.1 이상 여부만 표현할 수 있게 변환
    - 해석할 수 없으면 None
    """
    match = _VERSION_PATTERN.search(version_output)
    if match is None:
        return "", None
    text = match.group(1)
    numeric = _NUMERIC_VERSION_PATTERN.match(text)
    if numeric is not None:
        return text, (int(numeric.group(1)), int(numeric.group(2)))
    date = _DATE_VERSION_PATTERN.match(text)
    if date is not None:
        year_month = (int(date.group(1)), int(date.group(2)))
        return text, MIN_FFMPEG_VERSION if year_month >= DATE_BUILD_MIN_YEAR_MONTH else (0, 0)
    return text, None


def parse_listing_names(listing_output: str) -> frozenset[str]:
    """`ffmpeg -encoders` 또는 `-filters` 출력의 이름 목록을 집합으로 반환"""
    return frozenset(_LISTING_ROW_PATTERN.findall(listing_output))


def evaluate_capability(
    executable: str,
    version_text: str,
    version: tuple[int, int] | None,
    encoders: frozenset[str],
    filters: frozenset[str],
) -> FfmpegCapability:
    """확인된 값으로 사용 가능 여부와 사유를 결정한다. 버전을 알 수 없으면 사용 가능하되 사유에 기록"""
    if version == (0, 0):
        return FfmpegCapability(
            executable, version_text, version, encoders, filters,
            usable=False,
            reason="날짜형 빌드가 최소 버전 5.1보다 오래되었습니다. 최신 FFmpeg로 교체하세요",
        )
    if version is not None and version < MIN_FFMPEG_VERSION:
        major, minor = MIN_FFMPEG_VERSION
        return FfmpegCapability(
            executable, version_text, version, encoders, filters,
            usable=False,
            reason=f"FFmpeg {version[0]}.{version[1]}은 최소 버전 {major}.{minor}보다 낮습니다. 최신 FFmpeg로 교체하세요",
        )
    reason = "" if version is not None else "버전을 확인하지 못해 그대로 사용합니다"
    return FfmpegCapability(
        executable, version_text, version, encoders, filters, usable=True, reason=reason,
    )


def _run_text(command: list[str]) -> str:
    creationflags: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=FFMPEG_PROBE_TIMEOUT_SEC,
        check=False,
        creationflags=creationflags,
    )
    return completed.stdout + completed.stderr


def probe_ffmpeg_capability(ffmpeg_path: str = "") -> FfmpegCapability:
    """설정 경로(비우면 PATH)의 FFmpeg를 실행해 버전, 인코더, 필터를 확인한다

    - 실행 파일이 없거나 실행에 실패하면 사용 불가와 사유를 담은 결과 반환
    """
    executable = ffmpeg_path.strip() or "ffmpeg"
    resolved = shutil.which(executable) if not ffmpeg_path.strip() else executable
    if resolved is None:
        return FfmpegCapability(
            executable, "", None,
            usable=False,
            reason="FFmpeg를 찾을 수 없습니다. 설정에서 경로를 지정하거나 PATH에 추가하세요",
        )
    try:
        version_output = _run_text([resolved, "-hide_banner", "-version"])
        encoder_output = _run_text([resolved, "-hide_banner", "-encoders"])
        filter_output = _run_text([resolved, "-hide_banner", "-filters"])
    except (OSError, subprocess.SubprocessError) as exc:
        return FfmpegCapability(
            resolved, "", None,
            usable=False,
            reason=f"FFmpeg 실행에 실패했습니다: {exc}",
        )
    version_text, version = parse_ffmpeg_version(version_output)
    if not version_text:
        return FfmpegCapability(
            resolved, "", None,
            usable=False,
            reason="FFmpeg 버전 정보를 읽지 못했습니다. 올바른 FFmpeg 실행 파일인지 확인하세요",
        )
    return evaluate_capability(
        resolved,
        version_text,
        version,
        parse_listing_names(encoder_output),
        parse_listing_names(filter_output),
    )
