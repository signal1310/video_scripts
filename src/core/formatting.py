"""CUI·GUI 공용 수치·시간·키프레임·용량 서식 포맷터 모듈"""

from src.core.config import DEFAULT_SIZE_UNIT, DisplayConfig
from src.core.messages import LABEL_UNMEASURED
from src.core.models import BYTES_PER_KB, MB_PER_GB, SECONDS_PER_MINUTE

_SIZE_ZERO_DISPLAY_FACTOR: float = 0.5
_MIN_DECIMALS: int = 0
_MAX_DECIMALS: int = 4
_MB_PER_TB: float = MB_PER_GB * float(BYTES_PER_KB)
_KB_PER_MB: float = float(BYTES_PER_KB)


def clamp_decimals(decimals: int) -> int:
    """소수점 자릿수를 허용 범위(0~4) 내 정수로 정규화"""
    return max(_MIN_DECIMALS, min(decimals, _MAX_DECIMALS))


def format_float_precision(
    value: float,
    decimals: int,
    *,
    trim_whole: bool = False,
    thousands: bool = False,
    signed: bool = False,
) -> str:
    """지정한 소수점 자릿수와 정수화·부호·천 단위 구분 옵션으로 실수 문자열 생성"""
    prec = clamp_decimals(decimals)
    comma = "," if thousands else ""
    sign_spec = "+" if signed else ""
    raw = format(value, f"{sign_spec}{comma}.{prec}f")

    if raw in (f"-0.{'0' * prec}", f"+0.{'0' * prec}", "-0", "+0"):
        raw = f"0.{'0' * prec}" if prec > 0 else "0"

    if trim_whole and prec > 0 and "." in raw:
        head, frac = raw.split(".", 1)
        if frac == "0" * len(frac):
            return head
    return raw


def format_fps(fps: float, display: DisplayConfig) -> str:
    """설정 정밀도와 정수 생략 규칙을 반영해 FPS 문자열 생성"""
    return format_float_precision(
        fps,
        display.fps_decimals,
        trim_whole=display.fps_trim_whole,
    )


def format_duration_hms(seconds: float) -> str:
    """초 단위 재생 시간을 'MM:SS' 또는 'HH:MM:SS' 형식으로 변환"""
    total_sec = max(round(seconds), 0)
    mins, secs = divmod(total_sec, SECONDS_PER_MINUTE)
    hours, mins = divmod(mins, SECONDS_PER_MINUTE)
    if hours > 0:
        return f"{hours:02d}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def format_duration_seconds(seconds: float, display: DisplayConfig) -> str:
    """설정 정밀도를 반영해 CUI용 초 단위 재생 시간 문자열 생성"""
    return format_float_precision(seconds, display.duration_decimals)


def format_ratio(ratio: float, display: DisplayConfig) -> str:
    """설정 정밀도를 반영해 비트레이트 비율 문자열 생성"""
    return format_float_precision(ratio, display.ratio_decimals)


def format_keyframe_interval(
    kf: float | None,
    display: DisplayConfig,
    *,
    defective: bool = False,
    show_warn: bool = False,
) -> str:
    """키프레임 간격 수치 또는 미측정 레이블 생성"""
    if kf is None or kf < 0:
        return LABEL_UNMEASURED
    formatted = format_float_precision(kf, display.keyframe_decimals)
    if show_warn and defective:
        return f"{formatted} ⚠"
    return formatted


def _resolve_auto_size_scale(size_mb: float) -> tuple[float, str]:
    abs_mb = abs(size_mb)
    if abs_mb >= _MB_PER_TB:
        return size_mb / _MB_PER_TB, "TB"
    if abs_mb >= MB_PER_GB:
        return size_mb / MB_PER_GB, "GB"
    if abs_mb >= 1.0 or abs_mb == 0.0:
        return size_mb, "MB"
    return size_mb * _KB_PER_MB, "KB"


def format_size_header(
    base_title: str,
    size_unit: str,
    *,
    multiline: bool = False,
) -> str:
    """용량 표기 단위 설정에 맞춰 CUI·GUI 공통 용량 열 헤더 문자열 생성"""
    if size_unit == "auto":
        return f"\n{base_title}" if multiline else base_title
    unit_tag = "GB" if size_unit == "gb" else "MB"
    sep = "\n" if multiline else " "
    return f"{base_title}{sep}({unit_tag})"


def format_size_mb(
    size_mb: float,
    decimals: int,
    *,
    unit: str = DEFAULT_SIZE_UNIT,
    thousands: bool = True,
    signed: bool = False,
    show_unit: bool | None = None,
) -> str:
    """MB 기준 용량을 고정(mb·gb) 또는 유닉스식 자동 스케일(auto) 문자열로 생성"""
    prec = clamp_decimals(decimals)
    if unit == "auto":
        scaled_val, suffix = _resolve_auto_size_scale(size_mb)
    elif unit == "gb":
        scaled_val, suffix = size_mb / MB_PER_GB, "GB"
    else:
        scaled_val, suffix = size_mb, ""

    effective_show_unit = (unit == "auto") if show_unit is None else show_unit
    zero_cutoff = _SIZE_ZERO_DISPLAY_FACTOR * (10.0**-prec)
    if signed and abs(scaled_val) < zero_cutoff:
        num_str = format_float_precision(
            0.0, prec, thousands=thousands, signed=False
        )
        if unit == "auto":
            suffix = "MB"
    else:
        num_str = format_float_precision(
            scaled_val, prec, thousands=thousands, signed=signed
        )
    return f"{num_str} {suffix}" if (suffix and effective_show_unit) else num_str
