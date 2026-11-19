"""FFmpeg `-progress pipe:1` 출력의 파싱, 순간 비트레이트, 예상 최종 크기, 일시중지를 제외한 경과 시간 계산 모듈

- 순수 계산만 포함하며 프로세스나 시간 원본(monotonic)은 호출자가 주입
- 진행 블록은 `key=value` 줄들이 `progress=continue|end`로 끝나는 단위
"""

from collections import deque
from dataclasses import dataclass

PROGRESS_END_MARK: str = "end"
INSTANT_BITRATE_WINDOW_SEC: float = 1.0
PROGRESS_EMIT_INTERVAL_SEC: float = 0.5
LONG_RUN_NOTICE_SEC: float = 600.0
MICROSECONDS_PER_SECOND: float = 1_000_000.0
BITS_PER_BYTE: int = 8
KBIT: float = 1000.0
_HISTORY_MAX_SAMPLES: int = 64
EARLY_STOP_MIN_PROGRESS: float = 0.10
EARLY_STOP_MARGIN: float = 1.20
EARLY_STOP_RECHECK_SEC: float = 1.0


@dataclass(frozen=True, slots=True)
class ProgressSample:
    """진행 블록 한 개의 값. 측정 불가 값은 None"""

    frame: int | None
    fps: float | None
    quality: float | None
    bitrate_kbps: float | None
    total_size_bytes: int | None
    out_time_sec: float | None
    speed: float | None
    finished: bool


def _to_float(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        return None


def _to_int(raw: str) -> int | None:
    try:
        return int(raw)
    except ValueError:
        return None


def _bitrate_kbps(raw: str) -> float | None:
    """`1234.5kbits/s` 형식을 kbps로 변환, `N/A`는 None"""
    if not raw.endswith("kbits/s"):
        return None
    return _to_float(raw[: -len("kbits/s")])


def _speed(raw: str) -> float | None:
    """`1.25x` 형식의 배속을 실수로 변환, `N/A`는 None"""
    if not raw.endswith("x"):
        return None
    return _to_float(raw[:-1])


def _sample_from_block(values: dict[str, str]) -> ProgressSample:
    out_time_us = _to_int(values.get("out_time_us", ""))
    return ProgressSample(
        frame=_to_int(values.get("frame", "")),
        fps=_to_float(values.get("fps", "")),
        quality=_to_float(values.get("stream_0_0_q", "")),
        bitrate_kbps=_bitrate_kbps(values.get("bitrate", "")),
        total_size_bytes=_to_int(values.get("total_size", "")),
        out_time_sec=(out_time_us / MICROSECONDS_PER_SECOND) if out_time_us is not None else None,
        speed=_speed(values.get("speed", "")),
        finished=values.get("progress", "") == PROGRESS_END_MARK,
    )


class ProgressParser:
    """줄 단위 입력을 받아 진행 블록이 끝날 때마다 ProgressSample을 돌려주는 파서"""

    def __init__(self) -> None:
        self._block: dict[str, str] = {}

    def feed(self, line: str) -> ProgressSample | None:
        """한 줄을 넣고, 블록이 끝나면 샘플을 반환. 블록 밖의 줄과 `=`가 없는 줄은 무시"""
        key, sep, value = line.strip().partition("=")
        if not sep:
            return None
        self._block[key] = value
        if key != "progress":
            return None
        sample = _sample_from_block(self._block)
        self._block = {}
        return sample


class InstantBitrateTracker:
    """전체 출력 크기의 증가량으로 최근 구간 비트레이트(kbps)를 계산"""

    def __init__(self, window_sec: float = INSTANT_BITRATE_WINDOW_SEC) -> None:
        self._window_sec = window_sec
        self._points: deque[tuple[float, int]] = deque(maxlen=_HISTORY_MAX_SAMPLES)

    def add(self, out_time_sec: float | None, total_size_bytes: int | None) -> float | None:
        """시점과 누적 크기를 넣고, 창 길이만큼 앞선 점과의 차이로 kbps 계산(충분한 과거 점이 없으면 None)"""
        if out_time_sec is None or total_size_bytes is None:
            return None
        self._points.append((out_time_sec, total_size_bytes))
        latest_time, latest_size = self._points[-1]
        base = None
        for point_time, point_size in reversed(self._points):
            if latest_time - point_time >= self._window_sec:
                base = (point_time, point_size)
                break
        if base is None:
            return None
        delta_sec = latest_time - base[0]
        if delta_sec <= 0:
            return None
        delta_bits = (latest_size - base[1]) * BITS_PER_BYTE
        return delta_bits / delta_sec / KBIT


def progress_ratio(sample: ProgressSample, duration_sec: float) -> float | None:
    """진행 비율(0~1)을 `out_time`으로 계산. `out_time`이 없거나 0 이하이거나 길이가 0 이하이면 None

    - 프레임 수로 대체하지 않음. `out_time`이 N/A인 MKV와 커버 포함 MP4는 진행 비율을 모르므로 판정 대상에서 제외
    """
    if sample.out_time_sec is None or sample.out_time_sec <= 0 or duration_sec <= 0:
        return None
    return min(sample.out_time_sec / duration_sec, 1.0)


def projected_output_bytes(sample: ProgressSample, duration_sec: float) -> int | None:
    """현재까지의 누적 크기를 진행 비율로 나눠 예상 최종 크기(바이트) 산출. 진행 비율을 모르면 None"""
    if sample.total_size_bytes is None:
        return None
    progress = progress_ratio(sample, duration_sec)
    if progress is None or progress <= 0:
        return None
    return int(sample.total_size_bytes / progress)


@dataclass(frozen=True, slots=True)
class EarlyStopHit:
    """조기 중단 판정: 중단 시점의 진행 비율과 추정 최종 크기"""

    progress: float
    estimate_bytes: int


class EarlyStopJudge:
    """재확인 간격마다 진행 샘플의 추정 크기를 보고 원본 크기 기준으로 중단 여부를 판정(절약 목적 항목용)

    - 재확인 시각은 샘플의 경과 시간 기준이며, 조건을 보지 않은 재확인도 간격을 소비함(주기 타이머와 같음)
    - 진행 비율을 모르는 샘플(`out_time` 없음)은 판정하지 않음
    - 일시중지 제외 여부와 목적 구분은 호출자가 맡음
    """

    def __init__(
        self,
        *,
        source_size: int,
        duration_sec: float,
        min_progress: float = EARLY_STOP_MIN_PROGRESS,
        margin: float = EARLY_STOP_MARGIN,
        recheck_sec: float = EARLY_STOP_RECHECK_SEC,
    ) -> None:
        self._source_size: int = source_size
        self._duration_sec: float = duration_sec
        self._min_progress: float = min_progress
        self._margin: float = margin
        self._recheck_sec: float = recheck_sec
        self._next_check_sec: float = 0.0

    def observe(self, sample: ProgressSample, elapsed_sec: float) -> EarlyStopHit | None:
        """샘플 하나를 보고, 재확인 시각이면 판정하여 중단이 필요하면 결과를 돌려줌"""
        if elapsed_sec < self._next_check_sec:
            return None
        self._next_check_sec = elapsed_sec + self._recheck_sec
        progress = progress_ratio(sample, self._duration_sec)
        if progress is None or progress < self._min_progress:
            return None
        estimate = projected_output_bytes(sample, self._duration_sec)
        if estimate is None or estimate < self._source_size * self._margin:
            return None
        return EarlyStopHit(progress=progress, estimate_bytes=estimate)


class PausableClock:
    """일시중지 구간을 뺀 경과 시간을 계산. 시간 원본은 주입(모노토닉 시계)"""

    def __init__(self, now: float) -> None:
        self._started_at = now
        self._paused_total = 0.0
        self._paused_since: float | None = None

    def pause(self, now: float) -> None:
        """일시중지 시작 시점을 기록. 이미 멈춰 있으면 무시"""
        if self._paused_since is None:
            self._paused_since = now

    def resume(self, now: float) -> None:
        """일시중지 구간을 누적에 더함. 멈춘 적이 없으면 무시"""
        if self._paused_since is not None:
            self._paused_total += now - self._paused_since
            self._paused_since = None

    def elapsed(self, now: float) -> float:
        """일시중지 시간을 제외한 경과 시간(초)"""
        running_pause = (now - self._paused_since) if self._paused_since is not None else 0.0
        return max(0.0, now - self._started_at - self._paused_total - running_pause)

    def current_pause_sec(self, now: float) -> float:
        """현재 일시중지 구간의 지속 시간(초). 재생 중이면 0"""
        if self._paused_since is None:
            return 0.0
        return max(0.0, now - self._paused_since)


def needs_long_run_notice(current_pause_sec: float) -> bool:
    """현재 일시중지 지속 시간이 장시간 점유 안내 기준을 넘었는지 여부(결정 16)"""
    return current_pause_sec > LONG_RUN_NOTICE_SEC


def should_emit_progress(now: float, last_emit: float | None) -> bool:
    """GUI 전달 주기(0.5초) 기준으로 이번 샘플을 보낼지 판단"""
    return last_emit is None or now - last_emit >= PROGRESS_EMIT_INTERVAL_SEC
