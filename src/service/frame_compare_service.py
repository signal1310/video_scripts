"""화질 비교 프레임 추출 실행과 비교 가능한 최근 결과 파일 조회 서비스

- 프레임 추출은 FFmpeg를 임시 PNG 파일로 받아 취소 시 실행 중 프로세스를 즉시 종료
- 파일 부재와 FFmpeg 실행 오류를 구분한 예외로 올려 화면이 원인별 문구를 씀
- 파일 확인과 실행기 주입으로 실제 미디어 없이 단위 테스트할 수 있음
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.core.analysis.frame_compare import (
    FrameSource,
    build_frame_extract_command,
    resolve_pending_paths,
    resolve_result_paths,
)
from src.core.analysis.prober import extract_video_props
from src.core.encode.encode_state import EncodeState
from src.service.encode.encode_history import EncodeHistory

POLL_INTERVAL_SEC: float = 0.05
PROCESS_TERMINATE_TIMEOUT_SEC: float = 3.0
FRAME_EXTRACT_TIMEOUT_SEC: float = 60.0
WORK_DIR_NAME: str = "video_scripts_frame_compare"
FILE_MISSING_TEXT: str = "파일이 이동되었거나 삭제되었는지 확인해 주세요."
FFMPEG_ERROR_TEMPLATE: str = "FFmpeg로 프레임을 추출하지 못했습니다: {reason}"
PROBE_ERROR_TEMPLATE: str = "영상 정보를 읽지 못했습니다: {reason}"
CANCEL_MESSAGE: str = "화질 비교 프레임 추출이 취소되었습니다"
COMPARABLE_STATES: frozenset[EncodeState] = frozenset({EncodeState.DONE, EncodeState.REPLACE_PENDING})

FrameRunner = Callable[[list[str], threading.Event], tuple[int, str]]


class FrameCompareFileMissing(RuntimeError):
    """비교에 쓸 파일이 없을 때"""


class FrameCompareExecutionError(RuntimeError):
    """FFmpeg나 ffprobe 실행이 실패했을 때"""


@dataclass(frozen=True, slots=True)
class FrameSourceInfo:
    """비교 대상 파일 한 개의 프로빙 결과와 파일 식별 값"""

    path: str
    filename: str
    width: int
    height: int
    duration_sec: float
    fps: float
    size_bytes: int
    mtime_ns: int


@dataclass(frozen=True, slots=True)
class ExtractedFrames:
    """같은 시점에서 추출한 원본과 비교 대상 PNG 바이트"""

    original_png: bytes
    compared_png: bytes


def _failure_reason(exc: BaseException) -> str:
    """예외 메시지의 첫 비어 있지 않은 줄, 없으면 예외 이름"""
    for line in str(exc).splitlines():
        if line.strip():
            return line.strip()
    return exc.__class__.__name__


def resolve_comparable_result(history: EncodeHistory, source_path: str) -> tuple[str, str] | None:
    """원본 경로의 가장 최근 완료, 교체 대기 항목에서 비교 가능한 원본과 결과 경로 쌍"""
    row = history.latest_item_for_source(source_path)
    if row is None or row.state not in COMPARABLE_STATES:
        return None
    if row.state is EncodeState.DONE:
        return resolve_result_paths(row.source_path, row.output_path, row.archive_path)
    return resolve_pending_paths(row.source_path, row.part_path)


def probe_source(path: str, ffprobe_executable: str) -> FrameSourceInfo:
    """비교 대상 파일의 해상도, 길이, FPS와 파일 식별 값을 읽음"""
    if not os.path.isfile(path):
        raise FrameCompareFileMissing(FILE_MISSING_TEXT)
    try:
        stat = os.stat(path)
        props = extract_video_props(
            path,
            os.path.basename(path),
            False,
            ffprobe=ffprobe_executable,
        )
    except OSError as error:
        raise FrameCompareFileMissing(FILE_MISSING_TEXT) from error
    except Exception as error:
        raise FrameCompareExecutionError(PROBE_ERROR_TEMPLATE.format(reason=_failure_reason(error))) from error
    return FrameSourceInfo(
        path=path,
        filename=os.path.basename(path),
        width=props.width,
        height=props.height,
        duration_sec=props.duration,
        fps=props.fps,
        size_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    """실행 중인 자식 프로세스를 종료하고 회수"""
    if proc.poll() is None:
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=PROCESS_TERMINATE_TIMEOUT_SEC)


def run_ffmpeg_frame(
    args: list[str],
    cancel_event: threading.Event,
    *,
    timeout_sec: float = FRAME_EXTRACT_TIMEOUT_SEC,
) -> tuple[int, str]:
    """FFmpeg 프레임 추출을 취소 감시와 제한 시간 아래 실행하고 종료 코드와 표준 오류를 반환

    - 표준 오류는 임시 파일로 받아 파이프 교착 없이 읽음
    - 취소 신호를 받으면 실행 중 프로세스를 kill하고 `InterruptedError`를 냄
    """
    creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    deadline = time.monotonic() + timeout_sec
    with tempfile.TemporaryFile() as stderr_file:
        proc = subprocess.Popen(
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=stderr_file,
            creationflags=creationflags,
        )
        try:
            returncode = _wait_frame_process(proc, cancel_event, deadline, timeout_sec)
        except BaseException:
            _terminate_process(proc)
            raise
        stderr_file.seek(0)
        return returncode, stderr_file.read().decode("utf-8", errors="replace")


def _wait_frame_process(
    proc: subprocess.Popen[bytes],
    cancel_event: threading.Event,
    deadline: float,
    timeout_sec: float,
) -> int:
    """프레임 추출 프로세스 종료를 기다리며 취소와 제한 시간을 확인"""
    while True:
        if cancel_event.is_set():
            raise InterruptedError(CANCEL_MESSAGE)
        if time.monotonic() >= deadline:
            raise TimeoutError(f"프레임 추출 시간({timeout_sec:.0f}초)을 초과했습니다")
        try:
            return proc.wait(timeout=POLL_INTERVAL_SEC)
        except subprocess.TimeoutExpired:
            continue


class FrameCompareService:
    """비교 대상 프로빙, 최근 완료 결과 조회와 동일 시점 프레임 추출을 맡는 서비스

    - 실행기를 주입하면 FFmpeg 없이도 단위 테스트할 수 있음
    - 임시 PNG는 요청마다 만든 폴더에 쓰고 끝나면 지움
    """

    def __init__(
        self,
        *,
        ffmpeg_executable: str,
        ffprobe_executable: str,
        work_dir: str,
        history: EncodeHistory | None = None,
        runner: FrameRunner | None = None,
        timeout_sec: float = FRAME_EXTRACT_TIMEOUT_SEC,
    ) -> None:
        self._ffmpeg_executable: str = ffmpeg_executable
        self._ffprobe_executable: str = ffprobe_executable
        self._work_dir: str = work_dir
        self._history: EncodeHistory | None = history
        self._runner: FrameRunner | None = runner
        self._timeout_sec: float = timeout_sec

    def probe(self, path: str) -> FrameSourceInfo:
        """비교 대상 파일의 프로빙 정보. 파일이 없거나 읽지 못하면 예외"""
        return probe_source(path, self._ffprobe_executable)

    def comparable_result(self, source_path: str) -> tuple[str, str] | None:
        """원본 경로의 최근 완료, 교체 대기 결과에서 비교 가능한 경로 쌍"""
        if self._history is None:
            return None
        return resolve_comparable_result(self._history, source_path)

    def extract_pair(
        self,
        original: FrameSource,
        compared: FrameSource,
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
    ) -> ExtractedFrames:
        """같은 시점의 원본과 비교 대상 프레임을 PNG 바이트로 반환

        - 파일이 없으면 `FrameCompareFileMissing`, FFmpeg 실패는 `FrameCompareExecutionError`
        - 취소되면 `InterruptedError`
        """
        for source in (original, compared):
            if not os.path.isfile(source.path):
                raise FrameCompareFileMissing(FILE_MISSING_TEXT)
        os.makedirs(self._work_dir, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self._work_dir, prefix="frame_") as temp_dir:
            original_png = self._extract_one(
                original,
                time_sec=time_sec,
                frame_width=frame_width,
                frame_height=frame_height,
                output_path=str(Path(temp_dir, "original.png")),
                cancel_event=cancel_event,
            )
            compared_png = self._extract_one(
                compared,
                time_sec=time_sec,
                frame_width=frame_width,
                frame_height=frame_height,
                output_path=str(Path(temp_dir, "compared.png")),
                cancel_event=cancel_event,
            )
        return ExtractedFrames(original_png=original_png, compared_png=compared_png)

    def _extract_one(
        self,
        source: FrameSource,
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        output_path: str,
        cancel_event: threading.Event,
    ) -> bytes:
        """프레임 한 장을 추출해 PNG 바이트로 읽음"""
        args = build_frame_extract_command(
            ffmpeg_executable=self._ffmpeg_executable,
            source=source,
            time_sec=time_sec,
            output_path=output_path,
            frame_width=frame_width,
            frame_height=frame_height,
        )
        runner = self._runner or (lambda command, event: run_ffmpeg_frame(command, event, timeout_sec=self._timeout_sec))
        returncode, stderr = runner(args, cancel_event)
        if returncode != 0 or not os.path.isfile(output_path):
            reason = _failure_reason(RuntimeError(stderr.strip())) if stderr.strip() else f"종료 코드 {returncode}"
            raise FrameCompareExecutionError(FFMPEG_ERROR_TEMPLATE.format(reason=reason))
        return Path(output_path).read_bytes()
