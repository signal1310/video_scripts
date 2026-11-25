"""화질 비교 프레임 추출 실행과 비교 가능한 최근 결과 파일 조회 서비스

- 메모리 상주 PyAV 디코더를 먼저 쓰고, 지원하지 않는 코덱이나 실행기 주입 시 FFmpeg로 대체
- FFmpeg 경로는 무압축 BMP 임시 파일로 받아 4K에서도 압축, 해제 지연이 없고, 취소 시 실행 중 프로세스를 즉시 종료
- 같은 파일의 축소 비교는 한 번의 디코딩으로, 서로 다른 두 파일은 병렬 추출로 처리
- 파일 부재와 실행 오류를 구분한 예외로 올려 화면이 원인별 문구를 씀
- 파일 확인과 실행기 주입으로 실제 미디어 없이 단위 테스트할 수 있음
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from src.core.analysis.frame_compare import (
    FrameSource,
    build_downscale_pair_command,
    build_frame_extract_command,
    is_same_file_path,
    resolve_pending_paths,
    resolve_result_paths,
)
from src.core.analysis.prober import extract_video_props
from src.core.encode.encode_state import EncodeState
from src.core.models import ActiveFrame
from src.core.resolution_tiers import (
    DEFAULT_SCALE_ALGORITHM,
    ResolutionTier,
    normalize_scale_algorithm,
)
from src.service.encode.encode_history import EncodeHistory
from src.service.frame_compare_decoder import (
    DecodedFrame,
    PersistentFrameDecoder,
    crop_decoded_frame,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SEC: float = 0.05
PROCESS_TERMINATE_TIMEOUT_SEC: float = 3.0
FRAME_EXTRACT_TIMEOUT_SEC: float = 60.0
WORK_DIR_NAME: str = "video_scripts_frame_compare"
ORIGINAL_FRAME_NAME: str = "original.bmp"
COMPARED_FRAME_NAME: str = "compared.bmp"
PAIR_EXTRACT_WORKERS: int = 2
FILE_MISSING_TEXT: str = "파일이 이동되었거나 삭제되었는지 확인해 주세요."
FFMPEG_ERROR_TEMPLATE: str = "FFmpeg로 프레임을 추출하지 못했습니다: {reason}"
PROBE_ERROR_TEMPLATE: str = "영상 정보를 읽지 못했습니다: {reason}"
CANCEL_MESSAGE: str = "화질 비교 프레임 추출이 취소되었습니다"
COMPARABLE_STATES: frozenset[EncodeState] = frozenset({EncodeState.DONE, EncodeState.REPLACE_PENDING})

FrameRunner = Callable[[list[str], threading.Event], tuple[int, str]]
DecoderFactory = Callable[[str], PersistentFrameDecoder]


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
    """같은 시점에서 추출한 두 프레임

    - 메모리 디코더 경로는 RGB24 프레임을, FFmpeg 경로는 무압축 BMP 바이트를 채움
    """

    original_bytes: bytes
    compared_bytes: bytes
    original_frame: DecodedFrame | None = None
    compared_frame: DecodedFrame | None = None


def _failure_reason(exc: BaseException) -> str:
    """예외 메시지의 첫 비어 있지 않은 줄, 없으면 예외 이름"""
    for line in str(exc).splitlines():
        if line.strip():
            return line.strip()
    return exc.__class__.__name__


def _cropped_frame(frame: DecodedFrame, crop: ActiveFrame | None) -> DecodedFrame:
    """활성 화면 크롭이 있으면 그 영역만 남긴 프레임, 없으면 원본 프레임"""
    return frame if crop is None else crop_decoded_frame(frame, crop)


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

    - 실행기를 주입하면 FFmpeg 없이도 단위 테스트할 수 있고, 이때는 메모리 디코더를 쓰지 않음
    - FFmpeg 경로의 임시 BMP는 요청마다 만든 폴더에 쓰고 끝나면 지움
    - 메모리 디코더는 파일이 바뀌면 닫고 다시 열며, `close()`로 열린 컨테이너를 모두 해제
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
        decoder_factory: DecoderFactory | None = None,
        scale_algorithm: str = DEFAULT_SCALE_ALGORITHM,
    ) -> None:
        self._ffmpeg_executable: str = ffmpeg_executable
        self._ffprobe_executable: str = ffprobe_executable
        self._work_dir: str = work_dir
        self._history: EncodeHistory | None = history
        self._runner: FrameRunner | None = runner
        self._timeout_sec: float = timeout_sec
        self._decoder_factory: DecoderFactory | None = decoder_factory
        self._scale_algorithm: str = normalize_scale_algorithm(scale_algorithm)
        self._decoders: dict[str, tuple[tuple[int, int], PersistentFrameDecoder]] = {}
        self._decoder_lock = threading.Lock()

    @property
    def scale_algorithm(self) -> str:
        """비교 프레임 왕복에 쓰는 축소 보간 방식"""
        return self._scale_algorithm

    @property
    def ffmpeg_executable(self) -> str:
        """프레임 추출에 쓰는 FFmpeg 실행 파일"""
        return self._ffmpeg_executable

    @property
    def ffprobe_executable(self) -> str:
        """파일 프로빙에 쓰는 FFprobe 실행 파일"""
        return self._ffprobe_executable

    def probe(self, path: str) -> FrameSourceInfo:
        """비교 대상 파일의 프로빙 정보. 파일이 없거나 읽지 못하면 예외"""
        return probe_source(path, self._ffprobe_executable)

    def comparable_result(self, source_path: str) -> tuple[str, str] | None:
        """원본 경로의 최근 완료, 교체 대기 결과에서 비교 가능한 경로 쌍"""
        if self._history is None:
            return None
        return resolve_comparable_result(self._history, source_path)

    def close(self) -> None:
        """열어 둔 메모리 디코더의 컨테이너와 파일 핸들을 모두 닫음"""
        with self._decoder_lock:
            decoders = [decoder for _, decoder in self._decoders.values()]
            self._decoders.clear()
        for decoder in decoders:
            decoder.close()

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
        """같은 시점의 원본과 비교 대상 프레임을 반환

        - 파일이 없으면 `FrameCompareFileMissing`, 실행 실패는 `FrameCompareExecutionError`
        - 메모리 디코더를 먼저 쓰고, 지원하지 않는 코덱이나 회전 영상이면 FFmpeg 경로로 대체
        - 같은 파일의 축소 비교는 한 번의 디코딩으로, 다른 두 파일은 병렬로 추출
        - 취소되면 `InterruptedError`
        """
        for source in (original, compared):
            if not os.path.isfile(source.path):
                raise FrameCompareFileMissing(FILE_MISSING_TEXT)
        if self._runner is None:
            try:
                return self._extract_with_decoders(
                    original,
                    compared,
                    time_sec=time_sec,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    cancel_event=cancel_event,
                )
            except (InterruptedError, FrameCompareFileMissing):
                raise
            except Exception as error:
                logger.warning(
                    "메모리 프레임 디코더를 쓰지 못해 FFmpeg로 대체(%s)", _failure_reason(error)
                )
                self._close_decoders_for(original.path, compared.path)
        os.makedirs(self._work_dir, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=self._work_dir, prefix="frame_") as temp_dir:
            original_output = str(Path(temp_dir, ORIGINAL_FRAME_NAME))
            compared_output = str(Path(temp_dir, COMPARED_FRAME_NAME))
            tier = compared.tier
            if tier is not None and is_same_file_path(original.path, compared.path):
                return self._extract_downscale_pair(
                    compared,
                    tier,
                    time_sec=time_sec,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    original_output=original_output,
                    compared_output=compared_output,
                    cancel_event=cancel_event,
                )
            return self._extract_parallel(
                original,
                compared,
                time_sec=time_sec,
                frame_width=frame_width,
                frame_height=frame_height,
                original_output=original_output,
                compared_output=compared_output,
                cancel_event=cancel_event,
            )

    def _extract_with_decoders(
        self,
        original: FrameSource,
        compared: FrameSource,
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
    ) -> ExtractedFrames:
        """메모리 디코더로 두 프레임을 읽음. 같은 파일 축소 비교는 왕복 프레임을 함께 만듦"""
        tier = compared.tier
        if tier is not None and is_same_file_path(original.path, compared.path):
            original_frame, compared_frame = self._decoder_for(original.path).decode_pair(
                time_sec,
                tier=tier,
                frame_width=frame_width,
                frame_height=frame_height,
                cancel_event=cancel_event,
                crop=compared.crop,
                algorithm=self._scale_algorithm,
            )
            return ExtractedFrames(b"", b"", original_frame, compared_frame)
        first = self._decoder_for(original.path)
        second = self._decoder_for(compared.path)
        if first is second:
            frame = first.decode(time_sec, cancel_event=cancel_event)
            return ExtractedFrames(
                b"",
                b"",
                _cropped_frame(frame, original.crop),
                _cropped_frame(frame, compared.crop),
            )
        with ThreadPoolExecutor(PAIR_EXTRACT_WORKERS) as pool:
            futures = (
                pool.submit(first.decode, time_sec, cancel_event=cancel_event, crop=original.crop),
                pool.submit(second.decode, time_sec, cancel_event=cancel_event, crop=compared.crop),
            )
            return ExtractedFrames(b"", b"", futures[0].result(), futures[1].result())

    def _decoder_for(self, path: str) -> PersistentFrameDecoder:
        """경로별 디코더를 재사용하고 파일 크기나 수정 시각이 바뀌면 새로 엶"""
        try:
            stat = os.stat(path)
        except OSError as error:
            raise FrameCompareFileMissing(FILE_MISSING_TEXT) from error
        signature = (stat.st_size, stat.st_mtime_ns)
        previous: PersistentFrameDecoder | None = None
        with self._decoder_lock:
            cached = self._decoders.get(path)
            if cached is not None and cached[0] == signature:
                return cached[1]
            if cached is not None:
                previous = self._decoders.pop(path, cached)[1]
            factory = self._decoder_factory
            decoder = PersistentFrameDecoder(path) if factory is None else factory(path)
            self._decoders[path] = (signature, decoder)
        if previous is not None:
            previous.close()
        return decoder

    def _close_decoders_for(self, *paths: str) -> None:
        """지정한 경로의 디코더를 닫고 캐시에서 지움, 오류 뒤 다시 열어 재시도하게 함"""
        with self._decoder_lock:
            decoders = [
                self._decoders.pop(path, (None, None))[1] for path in paths if path in self._decoders
            ]
        for decoder in decoders:
            if decoder is not None:
                decoder.close()

    def _extract_downscale_pair(
        self,
        source: FrameSource,
        tier: ResolutionTier,
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        original_output: str,
        compared_output: str,
        cancel_event: threading.Event,
    ) -> ExtractedFrames:
        """축소 비교의 두 프레임을 한 번의 디코딩으로 추출"""
        args = build_downscale_pair_command(
            ffmpeg_executable=self._ffmpeg_executable,
            source=source,
            tier=tier,
            time_sec=time_sec,
            original_output=original_output,
            compared_output=compared_output,
            frame_width=frame_width,
            frame_height=frame_height,
            algorithm=self._scale_algorithm,
        )
        returncode, stderr = self._run(args, cancel_event)
        self._ensure_output(returncode, stderr, original_output)
        self._ensure_output(returncode, stderr, compared_output)
        return ExtractedFrames(
            original_bytes=Path(original_output).read_bytes(),
            compared_bytes=Path(compared_output).read_bytes(),
        )

    def _extract_parallel(
        self,
        original: FrameSource,
        compared: FrameSource,
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        original_output: str,
        compared_output: str,
        cancel_event: threading.Event,
    ) -> ExtractedFrames:
        """서로 다른 두 파일을 병렬로 추출하고, 한쪽 실패나 취소 시 다른 쪽도 멈춤

        - 공유 취소 신호를 함께 써서 두 FFmpeg 프로세스가 같이 종료됨
        """
        with ThreadPoolExecutor(PAIR_EXTRACT_WORKERS) as pool:
            futures = (
                pool.submit(
                    self._extract_one,
                    original,
                    time_sec=time_sec,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    output_path=original_output,
                    cancel_event=cancel_event,
                ),
                pool.submit(
                    self._extract_one,
                    compared,
                    time_sec=time_sec,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    output_path=compared_output,
                    cancel_event=cancel_event,
                ),
            )
            try:
                original_bytes = futures[0].result()
                compared_bytes = futures[1].result()
            except BaseException:
                cancel_event.set()
                raise
        return ExtractedFrames(original_bytes=original_bytes, compared_bytes=compared_bytes)

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
        """프레임 한 장을 추출해 무압축 BMP 바이트로 읽음"""
        args = build_frame_extract_command(
            ffmpeg_executable=self._ffmpeg_executable,
            source=source,
            time_sec=time_sec,
            output_path=output_path,
            frame_width=frame_width,
            frame_height=frame_height,
            algorithm=self._scale_algorithm,
        )
        returncode, stderr = self._run(args, cancel_event)
        self._ensure_output(returncode, stderr, output_path)
        return Path(output_path).read_bytes()

    def _run(self, args: list[str], cancel_event: threading.Event) -> tuple[int, str]:
        """주입된 실행기나 기본 FFmpeg 실행기로 명령을 실행"""
        runner = self._runner
        if runner is not None:
            return runner(args, cancel_event)
        return run_ffmpeg_frame(args, cancel_event, timeout_sec=self._timeout_sec)

    def _ensure_output(self, returncode: int, stderr: str, output_path: str) -> None:
        """종료 코드와 출력 파일을 확인하고 실패 사유를 예외로 올림"""
        if returncode == 0 and os.path.isfile(output_path):
            return
        reason = (
            _failure_reason(RuntimeError(stderr.strip()))
            if stderr.strip()
            else f"종료 코드 {returncode}"
        )
        raise FrameCompareExecutionError(FFMPEG_ERROR_TEMPLATE.format(reason=reason))
