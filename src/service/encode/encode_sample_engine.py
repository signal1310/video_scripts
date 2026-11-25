"""대표 구간 병렬 시험 인코딩 실행 엔진(결정 29 B와 결정 33 공용)

- 본 인코딩 인자 빌더(`build_sample_ffmpeg_args`)를 그대로 받아 구간만 다르게 실행
- 병렬 샘플 프로세스 묶음을 `PauseControl` 항목 하나로 등록해 중지와 일시중지를 전파
- 산출물은 임시 폴더에만 쓰고 호출이 끝나면 지움
"""

import concurrent.futures
import os
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass

from src.core.command_history import STAGE_SAMPLE_ENCODE, command_scope
from src.core.encode.encode_plan import EncodePlan
from src.core.encode.encode_process import ProcessOutcome
from src.core.encode.quality_tuning import SAMPLE_PARALLEL_WORKERS
from src.service.encode.encode_pause import PauseControl
from src.service.encode.encode_ports import EncodeProcessLike, ProcessFactory

SampleArgsBuilder = Callable[..., list[str]]


@dataclass(frozen=True, slots=True)
class SamplePassOutcome:
    """샘플 구간 한 번의 실행 결과: 출력 바이트 합계, 성공 구간 수, 취소 여부"""

    total_bytes: int
    succeeded_segments: int
    segment_count: int
    cancelled: bool


class SampleGroupProcess:
    """병렬 샘플 프로세스 묶음을 PauseControl 항목 하나로 연결해 중지, 일시중지를 전파"""

    def __init__(self) -> None:
        self._lock: threading.Lock = threading.Lock()
        self._procs: set[EncodeProcessLike] = set()
        self._cancelled: bool = False
        self._paused: bool = False

    @property
    def is_cancelled(self) -> bool:
        """중지 요청을 받았으면 참"""
        with self._lock:
            return self._cancelled

    def register(self, proc: EncodeProcessLike) -> bool:
        """샘플 프로세스를 등록. 이미 중지됨이면 즉시 취소하고 거짓, 일시중지 중이면 바로 멈춤"""
        with self._lock:
            if self._cancelled:
                proc.cancel()
                return False
            self._procs.add(proc)
            if self._paused:
                proc.pause()
            return True

    def unregister(self, proc: EncodeProcessLike) -> None:
        """완료된 샘플 프로세스를 목록에서 제거"""
        with self._lock:
            self._procs.discard(proc)

    def start(self) -> None:
        """개별 샘플 프로세스가 시작을 맡으므로 묶음 시작은 동작 없음"""

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        """개별 샘플 스레드가 종료 대기를 맡음"""
        return ProcessOutcome(returncode=0, cancelled=self.is_cancelled, stderr_tail=())

    def cancel(self) -> None:
        """실행 중인 모든 샘플 프로세스에 즉시 중지 전달"""
        with self._lock:
            self._cancelled = True
            procs = list(self._procs)
        for proc in procs:
            proc.cancel()

    def pause(self) -> None:
        """실행 중인 모든 샘플 프로세스를 일시중지"""
        with self._lock:
            self._paused = True
            procs = list(self._procs)
        for proc in procs:
            proc.pause()

    def resume(self) -> None:
        """일시중지한 모든 샘플 프로세스를 재개"""
        with self._lock:
            self._paused = False
            procs = list(self._procs)
        for proc in procs:
            proc.resume()

    def set_priority(self, name: str) -> None:
        """개별 프로세스가 우선순위를 맡으므로 동작 없음"""

    @property
    def pid(self) -> int | None:
        """묶음 프로세스는 단일 PID가 없음"""
        return None

def run_sample_pass(
    *,
    batch_id: int,
    source_path: str,
    plan: EncodePlan,
    ffmpeg_exec: str,
    process_factory: ProcessFactory,
    abort: threading.Event,
    segments: tuple[tuple[float, float], ...],
    quality: float,
    build_args: SampleArgsBuilder,
    control: PauseControl | None = None,
    on_segment_done: Callable[[int], None] | None = None,
    temp_prefix: str = "sample_",
) -> SamplePassOutcome:
    """대표 구간들을 병렬로 인코딩하고 출력 바이트 합계와 성공 구간 수를 반환

    - 취소되면 `cancelled`가 참이고 그때까지의 바이트만 담음
    - 프로세스 실패 구간은 성공으로 세지 않고 0바이트로 둠
    - `on_segment_done`은 구간 하나가 끝날 때마다 완료 수를 받음
    """
    total_segs = len(segments)
    if abort.is_set() or total_segs == 0:
        return SamplePassOutcome(
            total_bytes=0,
            succeeded_segments=0,
            segment_count=total_segs,
            cancelled=abort.is_set(),
        )

    group = SampleGroupProcess()
    if control is not None and not control.launch(source_path, batch_id, group):
        return SamplePassOutcome(
            total_bytes=0,
            succeeded_segments=0,
            segment_count=total_segs,
            cancelled=True,
        )
    try:
        with tempfile.TemporaryDirectory(prefix=temp_prefix) as tmp_dir:

            def _encode_one(index_seg: tuple[int, tuple[float, float]]) -> tuple[bool, bool, int]:
                idx, (start_sec, seg_sec) = index_seg
                if abort.is_set() or group.is_cancelled:
                    return True, False, 0
                out_path = os.path.join(tmp_dir, f"seg_{idx}.mp4")
                args = build_args(
                    plan,
                    start_sec=start_sec,
                    duration_sec=seg_sec,
                    part_path=out_path,
                    quality=quality,
                    ffmpeg_executable=ffmpeg_exec,
                )
                proc = process_factory(args, lambda _line: None)
                if not group.register(proc):
                    return True, False, 0
                try:
                    with command_scope(STAGE_SAMPLE_ENCODE, args, target=source_path) as handle:
                        proc.start()
                        outcome = proc.wait()
                        handle.exit_code = None if outcome.cancelled else outcome.returncode
                finally:
                    group.unregister(proc)
                if outcome.cancelled or abort.is_set() or group.is_cancelled:
                    return True, False, 0
                if outcome.returncode != 0 or not os.path.exists(out_path):
                    return False, False, 0
                return False, True, os.path.getsize(out_path)

            total_bytes = 0
            succeeded = 0
            done_count = 0
            cancelled_any = False
            workers = min(SAMPLE_PARALLEL_WORKERS, total_segs, os.cpu_count() or 1)
            with concurrent.futures.ThreadPoolExecutor(workers) as pool:
                futures = [pool.submit(_encode_one, item) for item in enumerate(segments)]
                for future in concurrent.futures.as_completed(futures):
                    cancelled, ok, seg_bytes = future.result()
                    if cancelled:
                        cancelled_any = True
                        group.cancel()
                        continue
                    done_count += 1
                    if ok:
                        succeeded += 1
                        total_bytes += seg_bytes
                    if on_segment_done is not None:
                        on_segment_done(done_count)
            return SamplePassOutcome(
                total_bytes=total_bytes,
                succeeded_segments=succeeded,
                segment_count=total_segs,
                cancelled=cancelled_any,
            )
    finally:
        if control is not None:
            control.release(source_path)

