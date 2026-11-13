"""FFprobe 실행, 멀티프로세싱 병렬 프로빙 및 키프레임 간격 실측 모듈"""

import json
import logging
import multiprocessing
import os
import subprocess
import sys
import time
from collections.abc import Callable
from concurrent.futures import FIRST_COMPLETED, Future, ProcessPoolExecutor, wait
from typing import Any, Protocol, TypeVar

from src.core.exceptions import (
    DomainInterruptedError,
    DomainRuntimeError,
    DomainTimeoutError,
    ErrorCode,
)
from src.core.file_manager import get_video_filenames, resolve_video_filepath
from src.core.messages import MSG_ANALYSIS_CANCELLED, MSG_PROBING_CANCELLED
from src.core.models import VideoProps
from src.core.probe_parser import (
    KEYFRAME_PROBE_WINDOWS_SEC,
    SHORT_SMALL_FOOTAGE_DURATION_SEC,
    build_video_props,
    parse_mean_keyframe_interval,
)

ProgressCallback = Callable[[int, int, str], None] | None
CancelCheck = Callable[[], bool] | None

_TTask = TypeVar("_TTask")
_TRes = TypeVar("_TRes")


class _CancelEvent(Protocol):
    def is_set(self) -> bool: ...
    def set(self) -> None: ...


FFPROBE_TIMEOUT_SEC: float = 30.0
POOL_POLL_INTERVAL_SEC: float = 0.1
PROCESS_KILL_POLL_SEC: float = 0.05
PROCESS_REAP_TIMEOUT_SEC: float = 1.0

__all__ = [
    "FFPROBE_TIMEOUT_SEC",
    "POOL_POLL_INTERVAL_SEC",
    "PROCESS_KILL_POLL_SEC",
    "PROCESS_REAP_TIMEOUT_SEC",
    "CancelCheck",
    "ProgressCallback",
    "_init_prober_worker",
    "extract_keyframe_interval",
    "extract_video_props",
    "populate_keyframes",
    "probe_directory",
    "run_ffprobe",
]

_worker_cancel_event: _CancelEvent | None = None


def _init_prober_worker(cancel_event: _CancelEvent) -> None:
    """워커 프로세스 초기화 시 부모와 공유하는 중단 이벤트를 바인딩"""
    global _worker_cancel_event
    _worker_cancel_event = cancel_event


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
    extra_args: tuple[str, ...] = ("-show_format", "-show_streams"),
    timeout_sec: float = FFPROBE_TIMEOUT_SEC,
) -> dict[str, Any]:
    """ffprobe 서브프로세스를 타임아웃 및 워커 중단 신호 감시 하에 실행하고 JSON 출력을 파싱하여 반환"""
    cmd: list[str] = ["ffprobe", "-v", "error", "-of", "json", *extra_args, filepath]
    creationflags: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
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
            if _worker_cancel_event is not None and _worker_cancel_event.is_set():
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


def extract_keyframe_interval(filepath: str, duration: float) -> float:
    """영상 내 키프레임 간격(초) 평균값 실측

    짧은 소형 영상 기준 이하 길이는 추가 프로빙 없이 전체 길이(duration) 반환
    그 외 영상은 KEYFRAME_PROBE_WINDOWS_SEC 단계별 탐색으로 측정, 미검출 시 상한 또는 duration 반환
    """
    fallback_kf = float(KEYFRAME_PROBE_WINDOWS_SEC[-1])

    # - 1초 이하의 짧은 소형 영상은 영상 자체 길이를 그대로 반환
    if 0 < duration <= SHORT_SMALL_FOOTAGE_DURATION_SEC:
        return duration

    for lim in KEYFRAME_PROBE_WINDOWS_SEC:
        try:
            probe: dict[str, Any] = run_ffprobe(
                filepath,
                extra_args=(
                    "-select_streams",
                    "v:0",
                    "-skip_frame",
                    "nokey",
                    "-show_entries",
                    "frame=pts_time,best_effort_timestamp_time",
                    "-read_intervals",
                    f"%+{lim}",
                ),
            )
        except InterruptedError:
            raise
        except Exception:
            return fallback_kf

        mean_interval = parse_mean_keyframe_interval(probe)
        if mean_interval is not None:
            return mean_interval

        # - 현재 탐색 범위(lim)가 영상 전체 길이 이상인데도 키프레임이 1개 이하라면, 영상 길이 자체를 간격으로 확정함
        if 0 < duration <= lim:
            return float(duration)

    return fallback_kf


def extract_video_props(
    filepath: str, filename: str, include_keyframe_interval: bool
) -> VideoProps:
    """ffprobe를 호출해 단일 비디오의 해상도·비트레이트·회전·FPS 메타데이터 추출

    파싱 세부 로직은 probe_parser에 위임하며 필요 시 키프레임 간격 추가 실측
    """
    probe: dict[str, Any] = run_ffprobe(filepath)
    filesize_bytes: int = os.path.getsize(filepath)
    props = build_video_props(filename, filesize_bytes, probe)
    if include_keyframe_interval:
        props.keyframe_interval = extract_keyframe_interval(filepath, props.duration)
    return props


def _worker_probe_video(args: tuple[str, str, bool]) -> tuple[VideoProps | None, str]:
    target_root_dir, filename, include_keyframe = args
    filepath = os.path.join(target_root_dir, filename)
    try:
        props = extract_video_props(filepath, filename, include_keyframe)
        return props, filename
    except InterruptedError:
        raise
    except Exception as e:
        return None, f"{filename} (분석 실패: {e})"


def _worker_probe_keyframe(
    args: tuple[str, str, float | None, float]
) -> tuple[str, float | None]:
    filepath, filename, current_interval, duration = args
    # - 이미 키프레임이 측정되어 있는 파일은 중복 프로빙 없이 기존 값을 그대로 유지
    if current_interval is not None and current_interval > 0:
        return f"Skipped {filename} (이미 키프레임 확인됨)", current_interval

    return filename, extract_keyframe_interval(filepath, duration)


def _run_cancellable_pool(
    tasks: list[_TTask],
    worker_fn: Callable[[_TTask], _TRes],
    on_item: Callable[[int, _TRes, int, int], None],
    is_cancelled: CancelCheck = None,
) -> None:
    """spawn 컨텍스트 기반 ProcessPoolExecutor에서 태스크를 병렬 실행하고 중단 요청 시 워커 이벤트를 전파"""
    if not tasks:
        return

    total_tasks: int = len(tasks)
    ctx = multiprocessing.get_context("spawn")
    cancel_event = ctx.Event()
    exe = ProcessPoolExecutor(
        mp_context=ctx,
        initializer=_init_prober_worker,
        initargs=(cancel_event,),
    )
    pending: set[Future[_TRes]] = set()
    try:
        future_to_idx: dict[Future[_TRes], int] = {
            exe.submit(worker_fn, task): idx for idx, task in enumerate(tasks)
        }
        pending = set(future_to_idx.keys())
        completed_count: int = 0

        while pending:
            if is_cancelled is not None and is_cancelled():
                raise DomainInterruptedError(
                    MSG_ANALYSIS_CANCELLED, code=ErrorCode.WORK_CANCELLED
                )
            done, pending = wait(
                pending,
                timeout=POOL_POLL_INTERVAL_SEC,
                return_when=FIRST_COMPLETED,
            )
            for fut in done:
                if is_cancelled is not None and is_cancelled():
                    raise DomainInterruptedError(
                        MSG_ANALYSIS_CANCELLED, code=ErrorCode.WORK_CANCELLED
                    )
                idx = future_to_idx[fut]
                res = fut.result()
                completed_count += 1
                on_item(idx, res, completed_count, total_tasks)
    except BaseException:
        cancel_event.set()
        for fut in pending:
            fut.cancel()
        exe.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        exe.shutdown(wait=True)


def probe_directory(
    target_root_dir: str,
    include_keyframe_interval: bool = False,
    on_progress: ProgressCallback = None,
    is_cancelled: CancelCheck = None,
) -> list[VideoProps]:
    """지정 디렉터리 내 모든 비디오 파일 대상 멀티프로세싱 메타데이터 일괄 수집

    처리 상황을 실시간 진행률 콜백으로 전달
    """
    filenames = get_video_filenames(target_root_dir)
    if not filenames:
        return []

    tasks: list[tuple[str, str, bool]] = [
        (target_root_dir, fname, include_keyframe_interval) for fname in filenames
    ]
    ordered_results: list[VideoProps | None] = [None] * len(tasks)

    def _handle_probe_item(
        idx: int,
        item: tuple[VideoProps | None, str],
        completed: int,
        total: int,
    ) -> None:
        prop, log = item
        if on_progress:
            on_progress(completed, total, log)
        ordered_results[idx] = prop

    _run_cancellable_pool(
        tasks, _worker_probe_video, _handle_probe_item, is_cancelled=is_cancelled
    )
    return [prop for prop in ordered_results if prop is not None]


def populate_keyframes(
    video_prop_table: list[VideoProps],
    target_root_dir: str,
    on_progress: ProgressCallback = None,
    is_cancelled: CancelCheck = None,
) -> None:
    """기존 VideoProps 목록에 키프레임 실측 간격 정보 증분 보강

    분류 하위 폴더로 이동된 파일도 실제 경로를 추적해 프로빙
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

    def _handle_keyframe_item(
        idx: int,
        item: tuple[str, float | None],
        completed: int,
        total: int,
    ) -> None:
        log, interval = item
        if on_progress:
            on_progress(completed, total, log)
        video_prop_table[idx].keyframe_interval = interval

    _run_cancellable_pool(
        tasks, _worker_probe_keyframe, _handle_keyframe_item, is_cancelled=is_cancelled
    )
