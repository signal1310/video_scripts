import itertools
import os
import statistics
from collections.abc import Callable, Iterator
from concurrent.futures import ProcessPoolExecutor
from typing import Any

import ffmpeg

from src.core.file_manager import get_video_filenames, resolve_video_filepath
from src.core.models import BYTES_PER_MB, VideoProps

ProgressCallback = Callable[[int, int, str], None] | None

DEFAULT_FPS: float = 30.0
DEFAULT_FPS_RATIO: str = "30/1"
KEYFRAME_PROBE_WINDOWS_SEC: tuple[float, ...] = (2.5, 5.5, 10.0)
SHORT_CLIP_DURATION_SEC: float = 1.0
BITS_PER_KBIT: int = 1000
BITS_PER_BYTE: int = 8
MIN_VIDEO_BITRATE_SHARE: float = 0.85
MIN_VIDEO_KBPS: int = 1
MIN_DIMENSION_PX: int = 1
HALF_CIRCLE_DEG: int = 180


def _safe_float(raw: Any, default: float = 0.0) -> float:
    """임의 값을 실수로 안전하게 파싱하며 실패 시 기본값을 반환."""
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


def _safe_int(raw: Any, default: int = 0) -> int:
    """임의 값을 정수로 안전하게 파싱하며 실패 시 기본값을 반환."""
    if raw is None or raw == "":
        return default
    try:
        return int(float(raw))
    except (ValueError, TypeError):
        return default


def _estimate_video_bps(total_bps: int, aud_bps: int) -> int:
    """컨테이너 총 비트레이트에서 오디오 비중을 제외하고 최소 비디오 비중(85%)을 보장."""
    if total_bps <= 0:
        return 0
    return max(total_bps - aud_bps, int(total_bps * MIN_VIDEO_BITRATE_SHARE))


def safe_dict(target: dict[str, Any] | None, key: str, default: Any = None) -> Any:
    """
    - 딕셔너리 객체에서 지정한 키를 조회.
    - 대상이 딕셔너리가 아니거나 해당 키의 값이 None인 경우에는 기본값을 반환.
    """
    if isinstance(target, dict) and key in target and target[key] is not None:
        return target[key]
    return default


def _parse_fps(fps_str: Any) -> float:
    """
    - ffprobe의 분수형 프레임레이트 문자열(예: '30000/1001', '60/1')을 실수(float)로 변환.
    """
    if not fps_str or not isinstance(fps_str, str):
        return DEFAULT_FPS
    try:
        if "/" in fps_str:
            num_str, den_str = fps_str.split("/", 1)
            num, den = float(num_str), float(den_str)
            if den <= 0 or num <= 0:
                return DEFAULT_FPS
            return num / den
        val = float(fps_str)
        return val if val > 0 else DEFAULT_FPS
    except (ValueError, ZeroDivisionError):
        return DEFAULT_FPS


def _get_rotate_type(vid_stream: dict[str, Any] | None) -> int:
    """
    - 비디오 스트림의 side_data_list(Display Matrix) 또는 tags 태그로부터 회전 각도를 추출함.
    - 시계 방향 회전 메타데이터가 감지되면 정수 각도(예: 90, 180, 270)를 반환하고, 없으면 0을 반환.
    """
    if not vid_stream:
        return 0

    side_data_list = safe_dict(vid_stream, "side_data_list", None)
    if isinstance(side_data_list, list):
        for dat in side_data_list:
            if isinstance(dat, dict) and "rotation" in dat:
                try:
                    return int(float(dat["rotation"]))
                except (ValueError, TypeError):
                    pass

    tags = safe_dict(vid_stream, "tags", None)
    if isinstance(tags, dict) and "rotate" in tags:
        try:
            return int(float(tags["rotate"]))
        except (ValueError, TypeError):
            pass

    return 0


def extract_keyframe_interval(filepath: str, duration: float) -> float:
    """
    - 영상 내 키프레임 간격(초)을 실측하여 평균값을 산출함.
    - 1차 탐색 범위를 2.5초로 두어, 2초 주기 표준 영상의 경우 단 1회의 호출만으로 신속히 측정.
    - 5초 이하의 초단편 영상에서 첫 프레임 외에 키프레임이 없는 경우, 영상 전체 길이(duration)를 키프레임 간격으로 확정함.
    """
    fallback_kf = float(KEYFRAME_PROBE_WINDOWS_SEC[-1])

    # - 1초 이하의 극단적 초단편 클립은 영상 자체 길이를 그대로 반환.
    if 0 < duration <= SHORT_CLIP_DURATION_SEC:
        return duration

    for lim in KEYFRAME_PROBE_WINDOWS_SEC:
        try:
            probe: dict[str, Any] = ffmpeg.probe(
                filepath,
                select_streams="v:0",
                skip_frame="nokey",
                show_entries="frame=pts_time,best_effort_timestamp_time",
                read_intervals=f"%+{lim}",
                of="json",
            )
        except Exception:
            return fallback_kf

        keyframes: list[float] = []
        for frame in probe.get("frames", []):
            ts = frame.get("pts_time") or frame.get("best_effort_timestamp_time")
            if ts is not None and ts != "N/A":
                try:
                    keyframes.append(float(ts))
                except (ValueError, TypeError):
                    continue

        # - 2개 이상의 키프레임이 발견되면 타임스탬프 간격들의 산술 평균을 계산함.
        if len(keyframes) >= 2:
            intervals: list[float] = [
                j - i for i, j in itertools.pairwise(keyframes) if j > i
            ]
            if intervals:
                return statistics.mean(intervals)

        # - 현재 탐색 범위(lim)가 영상 전체 길이 이상인데도 키프레임이 1개 이하라면, 영상 길이 자체를 간격으로 확정함.
        if 0 < duration <= lim:
            return float(duration)

    return fallback_kf


def extract_video_props(
    filepath: str, filename: str, include_keyframe_interval: bool
) -> VideoProps:
    """
    - ffprobe를 호출하여 단일 비디오 파일의 해상도, 비트레이트, 회전, 프레임레이트 메타데이터를 추출함.
    - 첨부된 앨범 커버 이미지(attached_pic) 스트림을 필터링하고 실제 재생 비디오 스트림을 선택.
    - MKV나 WebM 컨테이너처럼 스트림 헤더에 비트레이트가 비어있는 경우, 포맷 정보와 파일 크기로부터 비트레이트를 역산함.
    - 90도 또는 270도 회전 메타데이터가 적용된 영상은 실제 디스플레이 화면 비율에 맞추어 width와 height를 맞바꿈.
    """
    probe: dict[str, Any] = ffmpeg.probe(filepath)
    streams: list[dict[str, Any]] = probe.get("streams", [])
    fmt: dict[str, Any] = probe.get("format", {})

    vid_stream: dict[str, Any] | None = next(
        (
            s for s in streams
            if s.get("codec_type") == "video"
            and safe_dict(s.get("disposition"), "attached_pic", 0) != 1
        ),
        None,
    )
    if vid_stream is None:
        vid_stream = next(
            (s for s in streams if s.get("codec_type") == "video"), None
        )

    aud_stream: dict[str, Any] | None = next(
        (s for s in streams if s.get("codec_type") == "audio"), None
    )

    filesize_bytes: int = os.path.getsize(filepath)
    vid_size_mb: float = filesize_bytes / BYTES_PER_MB

    # - 재생 시간(duration)을 비디오 스트림 또는 컨테이너 포맷 레벨에서 추출함.
    raw_duration = (
        safe_dict(vid_stream, "duration", None)
        or safe_dict(fmt, "duration", None)
        or 0.0
    )
    duration: float = max(_safe_float(raw_duration, 0.0), 0.0)

    # - 오디오 스트림의 비트레이트 파싱.
    aud_bps: int = _safe_int(safe_dict(aud_stream, "bit_rate", 0), 0)
    aud_kbps: int = round(aud_bps / BITS_PER_KBIT)

    # - 비디오 스트림의 비트레이트를 우선 조회하고, 누락된 경우 총 비트레이트에서 오디오를 차감하여 역산함.
    vid_bps: int = _safe_int(safe_dict(vid_stream, "bit_rate", None), 0)

    if vid_bps <= 0:
        fmt_bps = _safe_int(safe_dict(fmt, "bit_rate", None), 0)
        vid_bps = _estimate_video_bps(fmt_bps, aud_bps)

    if vid_bps <= 0 and duration > 0:
        total_bps = int((filesize_bytes * BITS_PER_BYTE) / duration)
        vid_bps = _estimate_video_bps(total_bps, aud_bps)

    vid_kbps: int = max(round(vid_bps / BITS_PER_KBIT), MIN_VIDEO_KBPS)

    # - 해상도를 파싱하고, 90도 또는 270도 회전이 감지되면 실제 시청 화면 기준(가로/세로 반전)으로 정규화.
    raw_w: int = _safe_int(safe_dict(vid_stream, "width", 0), 0)
    raw_h: int = _safe_int(safe_dict(vid_stream, "height", 0), 0)
    rotate_type: int = _get_rotate_type(vid_stream)

    if abs(rotate_type) % HALF_CIRCLE_DEG != 0 and raw_w > 0 and raw_h > 0:
        width, height = raw_h, raw_w
    else:
        width, height = max(raw_w, MIN_DIMENSION_PX), max(raw_h, MIN_DIMENSION_PX)

    fps_str = (
        safe_dict(vid_stream, "avg_frame_rate", None)
        if safe_dict(vid_stream, "avg_frame_rate", "0/0") not in ("0/0", "0", None)
        else safe_dict(vid_stream, "r_frame_rate", DEFAULT_FPS_RATIO)
    )
    fps: float = _parse_fps(fps_str)
    codec: str = str(safe_dict(vid_stream, "codec_name", "unknown"))
    keyframe_interval: float | None = (
        extract_keyframe_interval(filepath, duration)
        if include_keyframe_interval
        else None
    )

    return VideoProps(
        filename=filename,
        width=width,
        height=height,
        rotate_type=rotate_type,
        fps=fps,
        vid_kbps=vid_kbps,
        aud_kbps=aud_kbps,
        vid_size_MB=vid_size_mb,
        duration=duration,
        codec=codec,
        keyframe_interval=keyframe_interval,
    )


def _worker_probe_video(args: tuple[str, str, bool]) -> tuple[VideoProps | None, str]:
    target_root_dir, filename, include_keyframe = args
    filepath = os.path.join(target_root_dir, filename)
    try:
        props = extract_video_props(filepath, filename, include_keyframe)
        return props, filename
    except Exception as e:
        return None, f"{filename} (분석 실패: {e})"


def _worker_probe_keyframe(
    args: tuple[str, str, float | None, float]
) -> tuple[str, float | None]:
    filepath, filename, current_interval, duration = args
    # - 이미 키프레임이 측정되어 있는 파일은 중복 프로빙 없이 기존 값을 그대로 유지.
    if current_interval is not None and current_interval > 0:
        return f"Skipped {filename} (이미 키프레임 확인됨)", current_interval

    return filename, extract_keyframe_interval(filepath, duration)


def probe_directory(
    target_root_dir: str,
    include_keyframe_interval: bool = False,
    on_progress: ProgressCallback = None,
) -> list[VideoProps]:
    """
    - 지정된 디렉터리 내 모든 비디오 파일에 대해 멀티프로세싱을 가동하여 메타데이터를 일괄 수집.
    - 처리 상황을 실시간 진행률 콜백으로 전달하여 사용자 피드백을 제공.
    """
    filenames = get_video_filenames(target_root_dir)
    if not filenames:
        return []

    tasks: list[tuple[str, str, bool]] = [
        (target_root_dir, fname, include_keyframe_interval) for fname in filenames
    ]
    total_tasks: int = len(tasks)
    results: list[VideoProps] = []

    exe = ProcessPoolExecutor()
    try:
        for i, (prop, log) in enumerate(exe.map(_worker_probe_video, tasks), 1):
            if on_progress:
                on_progress(i, total_tasks, log)
            if prop is not None:
                results.append(prop)
    except InterruptedError:
        exe.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        exe.shutdown(wait=True)

    return results


def populate_keyframes(
    video_prop_table: list[VideoProps],
    target_root_dir: str,
    on_progress: ProgressCallback = None,
) -> None:
    """
    - 기존에 생성된 VideoProps 목록에 키프레임 실측 간격 정보를 증분 보강.
    - 파일이 이미 분류 하위 폴더로 이동된 상태인 경우에도 실제 파일 경로를 정확히 추적하여 프로빙.
    """
    if not video_prop_table:
        return

    tasks: list[tuple[str, str, float | None, float]] = [
        (
            resolve_video_filepath(target_root_dir, vid),
            vid.filename,
            vid.keyframe_interval,
            vid.duration,
        )
        for vid in video_prop_table
    ]
    total_tasks: int = len(tasks)

    exe = ProcessPoolExecutor()
    try:
        iterator: Iterator[tuple[str, float | None]] = exe.map(
            _worker_probe_keyframe, tasks
        )
        for i, (vid, (log, interval)) in enumerate(
            zip(video_prop_table, iterator, strict=True), 1
        ):
            if on_progress:
                on_progress(i, total_tasks, log)
            vid.keyframe_interval = interval
    except InterruptedError:
        exe.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        exe.shutdown(wait=True)
