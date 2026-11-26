"""FFprobe 실행, 스레드 풀 기반 병렬 프로빙 및 키프레임 간격 실측 모듈"""

import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from typing import Any, TypeVar

from src.core.analysis.file_manager import get_video_filenames, resolve_video_filepath
from src.core.analysis.storage_probe import plan_lanes
from src.core.command_history import STAGE_PROBING, command_scope
from src.core.encode_tag import ENCODE_TAG_KEY
from src.core.exceptions import (
    DomainInterruptedError,
    DomainRuntimeError,
    DomainTimeoutError,
    ErrorCode,
)
from src.core.messages import (
    MSG_ANALYSIS_CANCELLED,
    MSG_PROBING_CANCELLED,
    format_failure_omitted,
    format_probe_failure,
)
from src.core.models import VideoProps
from src.core.mp4_xtra import read_mp4_probe_info
from src.core.probe_parser import (
    KEYFRAME_PROBE_READ_WINDOW_SEC,
    SHORT_SMALL_FOOTAGE_DURATION_SEC,
    build_video_props,
    compress_probe_response,
    measure_keyframe_interval,
)
from src.core.workspace_paths import resolve_source_filepath

ProgressCallback = Callable[[int, int, str], None] | None
CancelCheck = Callable[[], bool] | None

_TTask = TypeVar("_TTask")
_TRes = TypeVar("_TRes")

logger = logging.getLogger(__name__)

PROBE_FAILURE_LABEL: str = "분석"
KEYFRAME_FAILURE_LABEL: str = "키프레임 측정"
MAX_FAILURE_LOG_LINES: int = 20

FFPROBE_TIMEOUT_SEC: float = 30.0
FFPROBE_EXECUTABLE_DEFAULT: str = "ffprobe"


def resolve_ffprobe_executable(configured_path: str) -> str:
    """설정 경로를 ffprobe 실행 파일로 쓰고, 비어 있으면 PATH의 기본 이름을 사용"""
    stripped = configured_path.strip()
    return stripped if stripped else FFPROBE_EXECUTABLE_DEFAULT
POOL_POLL_INTERVAL_SEC: float = 0.1
PROCESS_KILL_POLL_SEC: float = 0.05
PROCESS_REAP_TIMEOUT_SEC: float = 1.0
FFPROBE_STREAM_FORMAT_ENTRY_SPEC: str = (
    "stream=index,codec_type,codec_name,width,height,r_frame_rate,avg_frame_rate,bit_rate,duration,color_transfer,"
    "pix_fmt,bits_per_raw_sample,sample_rate,channels,bits_per_sample:"
    "stream_disposition=attached_pic:"
    "stream_tags=rotate,BPS,BPS-eng,DURATION:"
    "stream_side_data=rotation,side_data_type:"
    "format=bit_rate,duration,size:"
    f"format_tags={ENCODE_TAG_KEY},KEYWORDS,WM/Category"
)
FFPROBE_DEFAULT_EXTRA_ARGS: tuple[str, ...] = (
    "-show_entries",
    FFPROBE_STREAM_FORMAT_ENTRY_SPEC,
)
KEYFRAME_PACKET_ENTRY_SPEC: str = "packet=stream_index,pts_time,dts_time,flags"
FFPROBE_KEYFRAME_EXTRA_ARGS: tuple[str, ...] = (
    "-show_packets",
    "-show_entries",
    f"{FFPROBE_STREAM_FORMAT_ENTRY_SPEC}:{KEYFRAME_PACKET_ENTRY_SPEC}",
    "-read_intervals",
    f"%+{KEYFRAME_PROBE_READ_WINDOW_SEC}",
)

__all__ = [
    "FFPROBE_DEFAULT_EXTRA_ARGS",
    "FFPROBE_KEYFRAME_EXTRA_ARGS",
    "FFPROBE_STREAM_FORMAT_ENTRY_SPEC",
    "FFPROBE_TIMEOUT_SEC",
    "POOL_POLL_INTERVAL_SEC",
    "PROCESS_KILL_POLL_SEC",
    "PROCESS_REAP_TIMEOUT_SEC",
    "CancelCheck",
    "ProgressCallback",
    "extract_keyframe_interval",
    "extract_video_props",
    "populate_keyframes",
    "probe_directory",
    "probe_files",
    "run_ffprobe",
]

def _terminate_and_reap(proc: subprocess.Popen[bytes]) -> None:
    """실행 중인 ffprobe 자식 프로세스를 즉시 종료하고 파이프를 회수"""
    if proc.poll() is not None:
        return
    proc.kill()
    try:
        proc.communicate(timeout=PROCESS_REAP_TIMEOUT_SEC)
    except subprocess.TimeoutExpired:
        logging.getLogger(__name__).debug("ffprobe 프로세스 종료 대기 타임아웃")


def run_ffprobe(
    filepath: str,
    *,
    extra_args: tuple[str, ...] = FFPROBE_DEFAULT_EXTRA_ARGS,
    timeout_sec: float = FFPROBE_TIMEOUT_SEC,
    cancel_event: threading.Event | None = None,
    ffprobe: str = FFPROBE_EXECUTABLE_DEFAULT,
) -> dict[str, Any]:
    """ffprobe 서브프로세스를 타임아웃 및 워커 중단 신호 감시 하에 실행하고 JSON 출력을 파싱하여 반환"""
    cmd: list[str] = [ffprobe, "-v", "error", "-of", "json", *extra_args, filepath]
    creationflags: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    with command_scope(STAGE_PROBING, cmd, target=filepath, source_path=filepath) as handle:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        deadline = time.monotonic() + timeout_sec
        out = b""
        err = b""
        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise DomainInterruptedError(
                        MSG_PROBING_CANCELLED, code=ErrorCode.WORK_CANCELLED
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DomainTimeoutError(
                        f"ffprobe 응답 시간({timeout_sec:.0f}초)을 초과했습니다: {filepath}",
                        code=ErrorCode.PROBE_TIMEOUT,
                    )
                step = min(PROCESS_KILL_POLL_SEC, remaining)
                try:
                    out, err = proc.communicate(timeout=step)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            _terminate_and_reap(proc)
            raise
        finally:
            # - 취소나 시간 초과로 끝난 경우 회수한 종료 코드를 남기고, 그 전이면 None으로 둠
            handle.exit_code = proc.returncode

        if proc.returncode != 0:
            err_text = err.decode("utf-8", errors="replace").strip()
            raise DomainRuntimeError(
                f"ffprobe 실행에 실패했습니다 (code={proc.returncode}): {err_text}",
                code=ErrorCode.PROBE_FAILED,
            )

        parsed = json.loads(out.decode("utf-8", errors="replace"))
        if not isinstance(parsed, dict):
            raise DomainRuntimeError(
                "ffprobe 출력이 JSON 객체 형식이 아닙니다.",
                code=ErrorCode.PROBE_INVALID_OUTPUT,
            )
        return parsed


def _failure_reason(exc: BaseException) -> str:
    """예외 메시지의 첫 비어 있지 않은 줄을 실패 사유로 반환하고, 메시지가 없으면 예외 종류 이름 사용"""
    for line in str(exc).splitlines():
        if line.strip():
            return line.strip()
    return exc.__class__.__name__


def _measure_keyframe(
    filepath: str,
    duration: float,
    cancel_event: threading.Event | None,
    ffprobe: str = FFPROBE_EXECUTABLE_DEFAULT,
) -> tuple[float | None, str | None]:
    """키프레임 간격과 측정 실패 사유를 함께 반환, 짧은 영상은 측정 없이 전체 길이를 사용"""
    if 0 < duration <= SHORT_SMALL_FOOTAGE_DURATION_SEC:
        return duration, None
    try:
        probe: dict[str, Any] = run_ffprobe(
            filepath,
            extra_args=FFPROBE_KEYFRAME_EXTRA_ARGS,
            cancel_event=cancel_event,
            ffprobe=ffprobe,
        )
    except InterruptedError:
        raise
    except Exception as e:
        return None, _failure_reason(e)
    return measure_keyframe_interval(probe, duration), None


def extract_keyframe_interval(
    filepath: str,
    duration: float,
    cancel_event: threading.Event | None = None,
    ffprobe: str = FFPROBE_EXECUTABLE_DEFAULT,
) -> float | None:
    """영상 내 키프레임 간격(초) 평균값 실측

    짧은 소형 영상 기준 이하 길이는 프로빙 없이 전체 길이(duration) 반환
    ffprobe 실패 시 간격을 알 수 없으므로 None 반환
    """
    interval, _reason = _measure_keyframe(filepath, duration, cancel_event, ffprobe)
    return interval


def extract_video_probe(
    filepath: str,
    filename: str,
    include_keyframe_interval: bool,
    cancel_event: threading.Event | None = None,
    ffprobe: str = FFPROBE_EXECUTABLE_DEFAULT,
) -> tuple[VideoProps, dict[str, Any]]:
    """ffprobe를 호출해 단일 비디오의 메타데이터와 원본 응답을 함께 반환

    키프레임 실측이 필요하면 메타데이터와 같은 호출에서 패킷을 함께 읽어 추가 프로세스 생성을 생략
    """
    extra_args = FFPROBE_KEYFRAME_EXTRA_ARGS if include_keyframe_interval else FFPROBE_DEFAULT_EXTRA_ARGS
    probe: dict[str, Any] = run_ffprobe(
        filepath, extra_args=extra_args, cancel_event=cancel_event, ffprobe=ffprobe
    )
    filesize_bytes: int = os.path.getsize(filepath)
    props = build_video_props(filename, filesize_bytes, probe)
    try:
        mp4_info = read_mp4_probe_info(filepath)
    except (OSError, ValueError):
        mp4_info = None
    if mp4_info is not None and mp4_info.is_mp4_family:
        if mp4_info.has_encode_detail:
            props.has_encode_tag = True
        if mp4_info.keywords:
            props.encode_keywords = ";".join(mp4_info.keywords)
        if mp4_info.mdat_size is not None:
            props.fingerprint_lookup_size = mp4_info.mdat_size
    if include_keyframe_interval:
        props.keyframe_interval = measure_keyframe_interval(probe, props.duration)
    return props, probe


def extract_video_props(
    filepath: str,
    filename: str,
    include_keyframe_interval: bool,
    cancel_event: threading.Event | None = None,
    ffprobe: str = FFPROBE_EXECUTABLE_DEFAULT,
) -> VideoProps:
    """ffprobe를 호출해 단일 비디오의 해상도, 비트레이트, 회전, FPS 메타데이터 추출"""
    props, _probe = extract_video_probe(
        filepath, filename, include_keyframe_interval, cancel_event, ffprobe=ffprobe
    )
    return props


class _FailureReporter:
    """측정 실패를 상한까지 개별 경고로 남기고 나머지는 개수만 요약하는 기록기"""

    def __init__(self, label: str) -> None:
        self._label: str = label
        self._count: int = 0

    def record(self, filename: str, reason: str) -> None:
        """실패 1건을 기록, 상한 이내면 파일명과 사유를 경고로 출력"""
        self._count += 1
        if self._count <= MAX_FAILURE_LOG_LINES:
            logger.warning("%s", format_probe_failure(self._label, filename, reason))

    def finish(self) -> None:
        """상한을 넘긴 실패가 있으면 생략된 개수를 안내"""
        omitted = self._count - MAX_FAILURE_LOG_LINES
        if omitted > 0:
            logger.warning("%s", format_failure_omitted(self._label, omitted))


def _worker_probe_video(
    args: tuple[str, str, bool, str], cancel_event: threading.Event
) -> tuple[VideoProps | None, str, str | None, bytes | None]:
    target_root_dir, filename, include_keyframe, ffprobe = args
    filepath = resolve_source_filepath(target_root_dir, filename)
    try:
        props, probe = extract_video_probe(filepath, filename, include_keyframe, cancel_event, ffprobe=ffprobe)
        return props, filename, None, compress_probe_response(probe)
    except InterruptedError:
        raise
    except Exception as e:
        return None, filename, _failure_reason(e), None


def _worker_probe_keyframe(
    args: tuple[str, str, float, str], cancel_event: threading.Event
) -> tuple[str, float | None, str | None]:
    filepath, filename, duration, ffprobe = args
    interval, reason = _measure_keyframe(filepath, duration, cancel_event, ffprobe)
    return filename, interval, reason


def _run_cancellable_pool(
    tasks: list[_TTask],
    worker_fn: Callable[[_TTask, threading.Event], _TRes],
    on_item: Callable[[int, _TRes, int, int], None],
    is_cancelled: CancelCheck = None,
    *,
    lanes: Sequence[int] | None = None,
    lane_limits: Mapping[int, int] | None = None,
) -> None:
    """ThreadPoolExecutor에서 태스크를 병렬 실행하고 중단 요청 시 공유 이벤트로 실행 중인 ffprobe 회수

    - lanes는 태스크별 대기열(저장 장치) 번호, lane_limits는 대기열별 동시 실행 상한
    - 상한이 없는 대기열은 모든 작업을 한꺼번에 제출하고, 상한이 있으면 완료에 맞춰 다음 작업을 투입
    - 취소 시 아직 투입하지 않은 작업은 제출되지 않음
    """
    if not tasks:
        return

    total_tasks: int = len(tasks)
    cancel_event = threading.Event()
    limits: Mapping[int, int] = lane_limits or {}
    queues: dict[int, deque[int]] = {}
    for idx in range(total_tasks):
        queues.setdefault(lanes[idx] if lanes is not None else 0, deque()).append(idx)
    inflight: dict[int, int] = dict.fromkeys(queues, 0)
    running: dict[Future[_TRes], int] = {}
    exe = ThreadPoolExecutor()

    def _fill(lane: int) -> None:
        queue = queues[lane]
        cap = limits.get(lane)
        while queue and (cap is None or inflight[lane] < cap):
            idx = queue.popleft()
            running[exe.submit(worker_fn, tasks[idx], cancel_event)] = idx
            inflight[lane] += 1

    try:
        for lane in queues:
            _fill(lane)
        completed_count: int = 0

        while running:
            if is_cancelled is not None and is_cancelled():
                raise DomainInterruptedError(
                    MSG_ANALYSIS_CANCELLED, code=ErrorCode.WORK_CANCELLED
                )
            done, _ = wait(
                set(running),
                timeout=POOL_POLL_INTERVAL_SEC,
                return_when=FIRST_COMPLETED,
            )
            for fut in done:
                if is_cancelled is not None and is_cancelled():
                    raise DomainInterruptedError(
                        MSG_ANALYSIS_CANCELLED, code=ErrorCode.WORK_CANCELLED
                    )
                idx = running.pop(fut)
                lane = lanes[idx] if lanes is not None else 0
                inflight[lane] -= 1
                res = fut.result()
                completed_count += 1
                on_item(idx, res, completed_count, total_tasks)
                _fill(lane)
    except BaseException:
        # - 실행 중 워커는 폴링 주기 안에 ffprobe를 kill하고 반환하므로 wait=True여도 대기가 짧음
        cancel_event.set()
        exe.shutdown(wait=True, cancel_futures=True)
        raise
    else:
        exe.shutdown(wait=True)


def probe_directory(
    target_root_dir: str,
    include_keyframe_interval: bool = False,
    on_progress: ProgressCallback = None,
    is_cancelled: CancelCheck = None,
    *,
    recursive: bool = False,
    excluded_dirnames: Collection[str] = (),
    ffprobe: str = FFPROBE_EXECUTABLE_DEFAULT,
) -> list[VideoProps]:
    """작업 폴더 내 비디오 파일 대상 병렬 메타데이터 일괄 수집

    처리 상황을 실시간 진행률 콜백으로 전달
    """
    return probe_files(
        target_root_dir,
        get_video_filenames(
            target_root_dir,
            recursive=recursive,
            excluded_dirnames=excluded_dirnames,
        ),
        include_keyframe_interval,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
        ffprobe=ffprobe,
    )


def probe_files(
    target_root_dir: str,
    filenames: list[str],
    include_keyframe_interval: bool = False,
    on_progress: ProgressCallback = None,
    is_cancelled: CancelCheck = None,
    *,
    ffprobe: str = FFPROBE_EXECUTABLE_DEFAULT,
    raw_out: dict[str, bytes] | None = None,
) -> list[VideoProps]:
    """지정한 파일 목록만 병렬 프로빙하며 입력 순서대로 결과 반환(실패 파일 제외)

    raw_out을 넘기면 파일명별 압축 분석 응답(키프레임 패킷 목록 제외)을 채워 준다
    """
    if not filenames:
        return []

    tasks: list[tuple[str, str, bool, str]] = [
        (target_root_dir, fname, include_keyframe_interval, ffprobe) for fname in filenames
    ]
    ordered_results: list[VideoProps | None] = [None] * len(tasks)
    reporter = _FailureReporter(PROBE_FAILURE_LABEL)

    def _handle_probe_item(
        idx: int,
        item: tuple[VideoProps | None, str, str | None, bytes | None],
        completed: int,
        total: int,
    ) -> None:
        prop, filename, reason, raw = item
        if reason is not None:
            reporter.record(filename, reason)
        if raw is not None and raw_out is not None:
            raw_out[filename] = raw
        if on_progress:
            on_progress(completed, total, filename)
        ordered_results[idx] = prop

    lanes, limits = plan_lanes(
        [resolve_source_filepath(target_root_dir, fname) for fname in filenames],
        os.cpu_count() or 1,
    )
    _run_cancellable_pool(
        tasks,
        _worker_probe_video,
        _handle_probe_item,
        is_cancelled=is_cancelled,
        lanes=lanes,
        lane_limits=limits,
    )
    reporter.finish()
    return [prop for prop in ordered_results if prop is not None]


def populate_keyframes(
    video_prop_table: list[VideoProps],
    target_root_dir: str,
    on_progress: ProgressCallback = None,
    is_cancelled: CancelCheck = None,
    *,
    ffprobe: str = FFPROBE_EXECUTABLE_DEFAULT,
) -> None:
    """기존 VideoProps 목록에 키프레임 실측 간격 정보 증분 보강

    기측정 항목은 디스크 경로 추적과 워커 큐 제출을 생략하고 미측정 파일만 선별 프로빙
    """
    if not video_prop_table:
        return

    total_count = len(video_prop_table)
    pending_indices: list[int] = []
    tasks: list[tuple[str, str, float, str]] = []
    excluded_count = 0
    reporter = _FailureReporter(KEYFRAME_FAILURE_LABEL)

    for idx, vid in enumerate(video_prop_table):
        if vid.keyframe_interval is not None and vid.keyframe_interval > 0:
            excluded_count += 1
            if on_progress:
                on_progress(excluded_count, total_count, vid.filename)
            continue
        pending_indices.append(idx)
        tasks.append(
            (
                resolve_video_filepath(target_root_dir, vid),
                vid.filename,
                vid.duration,
                ffprobe,
            )
        )

    if not tasks:
        return

    def _handle_keyframe_item(
        pool_idx: int,
        item: tuple[str, float | None, str | None],
        completed: int,
        _pool_total: int,
    ) -> None:
        orig_idx = pending_indices[pool_idx]
        filename, interval, reason = item
        if reason is not None:
            reporter.record(filename, reason)
        if on_progress:
            on_progress(excluded_count + completed, total_count, filename)
        video_prop_table[orig_idx].keyframe_interval = interval

    lanes, limits = plan_lanes([task[0] for task in tasks], os.cpu_count() or 1)
    _run_cancellable_pool(
        tasks,
        _worker_probe_keyframe,
        _handle_keyframe_item,
        is_cancelled=is_cancelled,
        lanes=lanes,
        lane_limits=limits,
    )
    reporter.finish()
