"""재인코딩 배치 실행 서비스: 대상 검사, 인코딩, 사후 게이트, 결과 배치, 기록 갱신

- 항목은 동시 실행 수만큼 워커 스레드에서 처리하며, 워커는 Qt 객체에 접근하지 않음
- 상태와 진행 변화는 on_event 콜백으로 전달하고, 호출자가 화면 스레드로 옮김
- FFmpeg 프로세스, ffprobe 실행, 분석 캐시 저장소는 주입 가능. 단위 테스트는 가짜 구현을 씀
- 배치 시작 시점의 설정 사본(EncodeSettings)만 쓰므로 실행 중 설정 변경은 다음 배치부터 적용
"""

import dataclasses
import json
import logging
import os
import shutil
import threading
import time
from collections.abc import Callable, Collection, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import psutil

from src.core.config import (
    RESULT_POLICY_ARCHIVE,
    RESULT_POLICY_REPLACE,
    RESULT_POLICY_SEPARATE,
    ClassifierConfig,
    ExecutionConfig,
)
from src.core.encode_concurrency import resolve_concurrency
from src.core.encode_gate import (
    GatePurpose,
    GateVerdict,
    OutputSummary,
    judge_result,
    purpose_for_action,
    summarize_probe,
)
from src.core.encode_paths import (
    archive_path_of,
    choose_free_path,
    in_place_output_path,
    part_path_of,
    separate_output_path,
)
from src.core.encode_plan import ACTION_DROP, DecodeMode, EncodePlan, build_encode_plan, choose_container
from src.core.encode_process import EncodeProcess, ProcessOutcome
from src.core.encode_progress import (
    LONG_RUN_NOTICE_SEC,
    InstantBitrateTracker,
    PausableClock,
    ProgressParser,
    ProgressSample,
    needs_long_run_notice,
    projected_output_bytes,
    should_emit_progress,
)
from src.core.encode_state import UNFINISHED_STATES, EncodeState
from src.core.encode_tag import build_encode_tag
from src.core.encoder_specs import get_encoder_spec
from src.core.exceptions import DomainInterruptedError, DomainRuntimeError, DomainTimeoutError
from src.core.ffmpeg_command import (
    HW_DECODE_SOURCE_CODECS,
    build_ffmpeg_args,
    resolve_ffmpeg_executable,
)
from src.core.models import VideoEvaluation
from src.core.power_state import prevent_sleep
from src.core.prober import resolve_ffprobe_executable, run_ffprobe
from src.core.resolution_tiers import build_scale_expression
from src.core.stream_layout import StreamLayout, parse_stream_layout
from src.core.workspace_paths import resolve_source_filepath, split_logical_filename
from src.service.analysis_store import AnalysisStore
from src.service.encode_history import EncodeHistory, ItemRow, NewItem, utc_now_iso

logger = logging.getLogger(__name__)

DISK_SPACE_RATIO: float = 1.1
LAYOUT_PROBE_ARGS: tuple[str, ...] = ("-show_streams", "-show_format")
EVENT_STATE: str = "state"
EVENT_PROGRESS: str = "progress"
EVENT_LONG_PAUSE: str = "long_pause"
RECOVERABLE_ERRORS: tuple[type[Exception], ...] = (
    DomainRuntimeError,
    DomainTimeoutError,
    DomainInterruptedError,
    OSError,
    ValueError,
)


@dataclass(frozen=True, slots=True)
class EncodeSettings:
    """배치 하나에 고정되는 실행 설정 사본(화면 설정과 분리)"""

    encoder: str
    container_policy: str
    result_policy: str
    archive_dirname: str
    output_dirname: str
    name_conflict: str
    process_priority: str
    ffmpeg_path: str
    ffprobe_path: str
    concurrent_jobs: int
    prevent_sleep: bool
    after_complete: str

    @classmethod
    def from_config(cls, config: ClassifierConfig) -> "EncodeSettings":
        """현재 설정에서 사본을 만듦"""
        execution: ExecutionConfig = config.execution
        return cls(
            encoder=config.encode.encode_codec,
            container_policy=execution.container_policy,
            result_policy=execution.result_policy,
            archive_dirname=execution.archive_dirname,
            output_dirname=execution.output_dirname,
            name_conflict=execution.name_conflict,
            process_priority=execution.process_priority,
            ffmpeg_path=execution.ffmpeg_path,
            ffprobe_path=execution.ffprobe_path,
            concurrent_jobs=execution.concurrent_jobs,
            prevent_sleep=execution.prevent_sleep,
            after_complete=execution.after_complete,
        )

    def to_json(self) -> str:
        """작업 기록에 저장할 JSON 문자열"""
        return json.dumps(dataclasses.asdict(self), ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True, slots=True)
class EncodeJob:
    """영상 한 개의 계획 입력. 판정 결과와 원본 식별 값을 묶은 사본"""

    filename: str
    root_dir: str
    source_path: str
    source_size: int
    source_mtime_ns: int
    source_duration_sec: float
    source_ext: str
    fps: float
    quality: float
    purpose: GatePurpose
    output_size: tuple[int, int] | None
    scale_filter: str | None
    maxrate_kbps: int | None


def job_to_json(job: EncodeJob) -> str:
    """계획 입력을 작업 기록용 JSON으로 직렬화"""
    payload: dict[str, object] = {
        "filename": job.filename,
        "root_dir": job.root_dir,
        "source_path": job.source_path,
        "source_size": job.source_size,
        "source_mtime_ns": job.source_mtime_ns,
        "source_duration_sec": job.source_duration_sec,
        "source_ext": job.source_ext,
        "fps": job.fps,
        "quality": job.quality,
        "purpose": job.purpose.value,
        "output_size": None if job.output_size is None else list(job.output_size),
        "scale_filter": job.scale_filter,
        "maxrate_kbps": job.maxrate_kbps,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def job_from_json(text: str) -> EncodeJob:
    """작업 기록의 JSON에서 계획 입력을 복원"""
    raw: dict[str, Any] = json.loads(text)
    size = raw["output_size"]
    return EncodeJob(
        filename=str(raw["filename"]),
        root_dir=str(raw["root_dir"]),
        source_path=str(raw["source_path"]),
        source_size=int(raw["source_size"]),
        source_mtime_ns=int(raw["source_mtime_ns"]),
        source_duration_sec=float(raw["source_duration_sec"]),
        source_ext=str(raw["source_ext"]),
        fps=float(raw["fps"]),
        quality=float(raw["quality"]),
        purpose=GatePurpose(str(raw["purpose"])),
        output_size=None if size is None else (int(size[0]), int(size[1])),
        scale_filter=None if raw["scale_filter"] is None else str(raw["scale_filter"]),
        maxrate_kbps=None if raw["maxrate_kbps"] is None else int(raw["maxrate_kbps"]),
    )


def jobs_from_evaluations(
    evaluations: Iterable[VideoEvaluation], root_spec: str
) -> list[EncodeJob]:
    """인코딩 대상 판정만 골라 계획 입력을 만듦. 파일을 읽지 못하면 경고 후 제외"""
    jobs: list[EncodeJob] = []
    for ev in evaluations:
        if not ev.is_encode_target:
            continue
        logical = ev.video.filename
        source = resolve_source_filepath(root_spec, logical)
        root, _relative = split_logical_filename(root_spec, logical)
        try:
            stat = os.stat(source)
        except OSError as error:
            logger.warning("재인코딩 대상 파일 정보를 읽지 못해 제외합니다: %s (%s)", logical, error)
            continue
        tier = ev.target_tier
        output_size: tuple[int, int] | None = None
        scale_filter: str | None = None
        maxrate: int | None = None
        if ev.is_downscaled and ev.target_width is not None and ev.target_height is not None and tier is not None:
            output_size = (ev.target_width, ev.target_height)
            scale_filter = build_scale_expression(tier)
            maxrate = tier.max_bitrate_kbps
        jobs.append(
            EncodeJob(
                filename=logical,
                root_dir=root,
                source_path=source,
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_duration_sec=ev.video.duration,
                source_ext=os.path.splitext(source)[1].lstrip(".").lower(),
                fps=ev.video.fps,
                quality=ev.applied_cq,
                purpose=purpose_for_action(ev.smart_action),
                output_size=output_size,
                scale_filter=scale_filter,
                maxrate_kbps=maxrate,
            )
        )
    return jobs


def create_batch(history: EncodeHistory, settings: EncodeSettings, root_dir: str, jobs: Sequence[EncodeJob]) -> int:
    """설정 사본과 대상 항목으로 새 배치를 기록하고 배치 번호를 반환"""
    items = [
        NewItem(
            source_path=job.source_path,
            source_size=job.source_size,
            source_mtime_ns=job.source_mtime_ns,
            plan_json=job_to_json(job),
        )
        for job in jobs
    ]
    return history.create_batch(root_dir, settings.to_json(), items)


@dataclass(frozen=True, slots=True)
class ResumeReport:
    """이어서 재인코딩 준비 결과 개수"""

    resumable: int
    excluded: int
    removed_parts: int


@dataclass(frozen=True, slots=True)
class EncodeEvent:
    """화면에 전달할 진행, 상태, 안내 이벤트"""

    kind: str
    batch_id: int
    source_path: str
    state: EncodeState | None = None
    reason: str = ""
    sample: ProgressSample | None = None
    instant_kbps: float | None = None
    projected_bytes: int | None = None
    elapsed_sec: float = 0.0


class EncodeProcessLike(Protocol):
    """인코딩 프로세스의 실행과 제어 인터페이스(EncodeProcess와 동일 시그니처)"""

    def start(self) -> None:
        """프로세스를 시작"""
        ...

    def pause(self) -> None:
        """프로세스를 일시중지"""
        ...

    def resume(self) -> None:
        """일시중지한 프로세스를 재개"""
        ...

    def set_priority(self, name: str) -> None:
        """설정 우선순위 이름을 프로세스에 적용"""
        ...

    def cancel(self) -> None:
        """프로세스를 취소(종료)"""
        ...

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        """종료를 기다려 결과를 반환"""
        ...


ProcessFactory = Callable[[Sequence[str], Callable[[str], None]], EncodeProcessLike]
ProbeFn = Callable[[str, tuple[str, ...], threading.Event], dict[str, Any]]
EventSink = Callable[[EncodeEvent], None]


@dataclass(slots=True)
class _ActiveItem:
    batch_id: int
    process: EncodeProcessLike
    clock: PausableClock


@dataclass(frozen=True, slots=True)
class _Outcome:
    """항목 한 건의 최종 상태와 사유, 실패 시 FFmpeg 로그 끝부분"""

    state: EncodeState
    reason: str = ""
    stderr_tail: str = ""
    decode_retry: bool = False
    output_size: int | None = None


@dataclass(frozen=True, slots=True)
class _Attempt:
    """인코딩 실행 한 번의 결과. 실패 결과가 있으면 failure에 담음"""

    cancelled: bool = False
    decode_retry: bool = False
    stderr_tail: str = ""
    failure: _Outcome | None = None


class _ProgressRelay:
    """FFmpeg 진행 줄을 샘플로 바꾸고 0.5초 주기로 이벤트를 보내는 중계"""

    def __init__(
        self,
        *,
        emit: EventSink,
        batch_id: int,
        source_path: str,
        duration_sec: float,
        elapsed: Callable[[], float],
        clock: Callable[[], float],
    ) -> None:
        self._emit: EventSink = emit
        self._batch_id: int = batch_id
        self._source_path: str = source_path
        self._duration_sec: float = duration_sec
        self._elapsed: Callable[[], float] = elapsed
        self._clock: Callable[[], float] = clock
        self._parser: ProgressParser = ProgressParser()
        self._tracker: InstantBitrateTracker = InstantBitrateTracker()
        self._last_emit: float | None = None

    def feed(self, line: str) -> None:
        sample = self._parser.feed(line)
        if sample is None:
            return
        now = self._clock()
        if not should_emit_progress(now, self._last_emit):
            return
        self._last_emit = now
        self._emit(
            EncodeEvent(
                kind=EVENT_PROGRESS,
                batch_id=self._batch_id,
                source_path=self._source_path,
                sample=sample,
                instant_kbps=self._tracker.add(sample.out_time_sec, sample.total_size_bytes),
                projected_bytes=projected_output_bytes(sample, self._duration_sec),
                elapsed_sec=self._elapsed(),
            )
        )


def _default_probe(ffprobe: str) -> ProbeFn:
    def probe(path: str, args: tuple[str, ...], cancel: threading.Event) -> dict[str, Any]:
        return run_ffprobe(path, extra_args=args, cancel_event=cancel, ffprobe=ffprobe)

    return probe


def _expected_stream_count(plan: EncodePlan) -> int:
    return sum(1 for action in plan.streams if action.action != ACTION_DROP)


class EncodeRunner:
    """배치 하나의 대기 항목을 실행하고 결과를 배치한 뒤 기록을 갱신

    - run은 호출한 스레드를 점유하므로 화면에서는 워커 스레드에서 호출
    - pause_all, resume_all, cancel_all, request_stop_after_current는 다른 스레드에서 호출 가능
    """

    def __init__(
        self,
        history: EncodeHistory,
        settings: EncodeSettings,
        *,
        probe: ProbeFn | None = None,
        process_factory: ProcessFactory | None = None,
        on_event: EventSink | None = None,
        store: AnalysisStore | None = None,
        cpu_count: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._history: EncodeHistory = history
        self._settings: EncodeSettings = settings
        self._ffmpeg: str = resolve_ffmpeg_executable(settings.ffmpeg_path)
        ffprobe_exe = resolve_ffprobe_executable(settings.ffprobe_path)
        self._probe: ProbeFn = probe if probe is not None else _default_probe(ffprobe_exe)
        self._factory: ProcessFactory = process_factory if process_factory is not None else _default_factory
        self._on_event: EventSink | None = on_event
        self._store: AnalysisStore | None = store
        self._cpu_count: int = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
        self._clock: Callable[[], float] = clock
        self._lock: threading.Lock = threading.Lock()
        self._abort: threading.Event = threading.Event()
        self._stop_requested: bool = False
        self._paused: bool = False
        self._active: dict[str, _ActiveItem] = {}
        self._timer: threading.Timer | None = None

    def run(self, batch_id: int) -> None:
        """배치의 대기 항목을 동시 실행하고, 교체 대기 항목을 다시 시도한 뒤 끝난 배치를 닫음"""
        with prevent_sleep(self._settings.prevent_sleep):
            pending = [row for row in self._history.list_items(batch_id) if row.state is EncodeState.PENDING]
            encoder_spec = get_encoder_spec(self._settings.encoder)
            workers = resolve_concurrency(
                self._settings.concurrent_jobs,
                is_hardware=encoder_spec is not None and encoder_spec.is_nvenc,
                cpu_count=self._cpu_count,
            )
            with ThreadPoolExecutor(workers) as pool:
                futures = [
                    pool.submit(self._process_item, batch_id, row, job_from_json(row.plan_json))
                    for row in pending
                ]
                for future in futures:
                    future.result()
            self.retry_replace_pending(batch_id)
            if not any(row.state in UNFINISHED_STATES for row in self._history.list_items(batch_id)):
                self._history.close_batch(batch_id)

    def request_stop_after_current(self) -> None:
        """진행 중인 항목은 끝내고 새 항목은 시작하지 않음(현재 영상 후 중지)"""
        with self._lock:
            self._stop_requested = True

    def cancel_all(self) -> None:
        """진행 중인 프로세스를 종료하고 해당 항목은 대기로 되돌림(즉시 중지)"""
        self._abort.set()
        with self._lock:
            for active in self._active.values():
                active.process.cancel()

    def pause_all(self) -> None:
        """진행 중인 모든 프로세스를 일시중지하고 장시간 안내 타이머를 시작"""
        with self._lock:
            if self._paused:
                return
            self._paused = True
            now = self._clock()
            for active in self._active.values():
                active.process.pause()
                active.clock.pause(now)
        self._schedule_long_pause_check()

    def resume_all(self) -> None:
        """일시중지한 프로세스를 모두 다시 실행"""
        with self._lock:
            if not self._paused:
                return
            self._paused = False
            now = self._clock()
            for active in self._active.values():
                active.process.resume()
                active.clock.resume(now)
            timer = self._timer
            self._timer = None
        if timer is not None:
            timer.cancel()

    def check_long_pause(self) -> None:
        """현재 일시중지 지속 시간이 안내 기준을 넘은 항목마다 안내 이벤트를 보냄(결정 16)"""
        with self._lock:
            if not self._paused:
                return
            now = self._clock()
            notices = [
                (active.batch_id, source)
                for source, active in self._active.items()
                if needs_long_run_notice(active.clock.current_pause_sec(now))
            ]
        for batch_id, source in notices:
            self._emit(EncodeEvent(kind=EVENT_LONG_PAUSE, batch_id=batch_id, source_path=source))

    def _schedule_long_pause_check(self) -> None:
        with self._lock:
            if not self._paused:
                return
            timer = threading.Timer(LONG_RUN_NOTICE_SEC, self._on_long_pause_timer)
            timer.daemon = True
            self._timer = timer
        timer.start()

    def _on_long_pause_timer(self) -> None:
        self.check_long_pause()
        with self._lock:
            still_paused = self._paused
        if still_paused:
            self._schedule_long_pause_check()

    def _process_item(self, batch_id: int, row: ItemRow, job: EncodeJob) -> None:
        if self._stop_requested or self._abort.is_set():
            return
        self._update(
            batch_id,
            row.source_path,
            {"state": EncodeState.RUNNING, "started_at": utc_now_iso(), "reason": ""},
        )
        try:
            outcome = self._encode_item(batch_id, job)
        except RECOVERABLE_ERRORS as error:
            if self._abort.is_set():
                outcome = _Outcome(EncodeState.PENDING)
            else:
                logger.warning("재인코딩 항목 처리 중 오류: %s (%s)", row.source_path, error)
                outcome = _Outcome(EncodeState.FAILED, f"처리 중 오류: {error}")
        self._finish(batch_id, row, job, outcome)

    def _encode_item(self, batch_id: int, job: EncodeJob) -> _Outcome:
        source = job.source_path
        if not self._unchanged(job):
            return _Outcome(EncodeState.SKIPPED, "원본 변경 또는 없음")
        if shutil.disk_usage(os.path.dirname(source)).free < job.source_size * DISK_SPACE_RATIO:
            return _Outcome(EncodeState.SKIPPED, "공간 부족")
        layout = parse_stream_layout(self._probe(source, LAYOUT_PROBE_ARGS, self._abort))
        primary = layout.primary_video
        if primary is None:
            return _Outcome(EncodeState.FAILED, "주 영상 스트림이 없음")
        if primary.is_interlaced:
            return _Outcome(EncodeState.SKIPPED, "인터레이스 원본")
        planned = self._plan(batch_id, job, layout)
        if isinstance(planned, _Outcome):
            return planned
        attempt = self._encode_with_fallback(batch_id, job, planned)
        if attempt.cancelled or self._abort.is_set():
            self._remove_part(planned.part_path)
            return _Outcome(EncodeState.PENDING)
        if attempt.failure is not None:
            self._remove_part(planned.part_path)
            return attempt.failure
        return self._gate_and_place(job, planned, attempt)

    def _plan(self, batch_id: int, job: EncodeJob, layout: StreamLayout) -> EncodePlan | _Outcome:
        """결과 경로와 인코딩 계획을 만든다. 같은 이름 건너뜀이나 기록 충돌이면 _Outcome을 돌려줌"""
        settings = self._settings
        source = job.source_path
        container = choose_container(job.source_ext, layout, settings.container_policy)
        if settings.result_policy == RESULT_POLICY_SEPARATE:
            candidate = separate_output_path(source, job.root_dir, settings.output_dirname, container)
        else:
            candidate = in_place_output_path(source, container)
        final = candidate
        if final != source:
            chosen = choose_free_path(candidate, settings.name_conflict, os.path.exists)
            if chosen is None:
                return _Outcome(EncodeState.SKIPPED, "같은 이름의 파일이 있음")
            final = chosen
        archive: str | None = None
        if settings.result_policy == RESULT_POLICY_ARCHIVE:
            archive = archive_path_of(source, settings.archive_dirname)
            if os.path.exists(archive):
                return _Outcome(EncodeState.FAILED, "보관 폴더에 같은 이름의 파일이 있음")
        part = part_path_of(final)
        if os.path.exists(part):
            return _Outcome(EncodeState.FAILED, "임시 파일이 남아 있음(정리 필요)")
        plan = build_encode_plan(
            source_path=source,
            part_path=part,
            output_path=final,
            source_ext=job.source_ext,
            encoder=settings.encoder,
            quality=job.quality,
            fps=job.fps,
            layout=layout,
            container_policy=settings.container_policy,
            tag_value=build_encode_tag(settings.encoder, job.quality, datetime.now(timezone.utc)),
            output_size=job.output_size,
            scale_filter=job.scale_filter,
            maxrate_kbps=job.maxrate_kbps,
        )
        self._update(batch_id, source, {"part_path": part, "output_path": final, "archive_path": archive})
        return plan

    def _encode_with_fallback(self, batch_id: int, job: EncodeJob, plan: EncodePlan) -> _Attempt:
        """인코딩을 실행하고, 하드웨어 디코딩 실패는 CPU 디코딩으로 한 번 더 시도(결정 14)"""
        first = self._run_ffmpeg(batch_id, job, plan)
        if first.cancelled:
            return _Attempt(cancelled=True)
        if first.returncode == 0:
            return _Attempt()
        hw_source = plan.source_codec.lower() in HW_DECODE_SOURCE_CODECS
        if not (hw_source and plan.decode_mode is DecodeMode.SHANA_DEFAULT) or self._abort.is_set():
            return _Attempt(
                failure=_Outcome(
                    EncodeState.FAILED,
                    f"인코딩 실패(종료 코드 {first.returncode})",
                    "\n".join(first.stderr_tail),
                )
            )
        self._remove_part(plan.part_path)
        retry = self._run_ffmpeg(batch_id, job, dataclasses.replace(plan, decode_mode=DecodeMode.CPU_ONLY))
        if retry.cancelled:
            return _Attempt(cancelled=True)
        if retry.returncode == 0:
            return _Attempt(decode_retry=True)
        return _Attempt(
            decode_retry=True,
            failure=_Outcome(
                EncodeState.FAILED,
                f"인코딩 실패(CPU 디코딩 재시도 후 종료 코드 {retry.returncode})",
                "\n".join(retry.stderr_tail),
            ),
        )

    def _run_ffmpeg(self, batch_id: int, job: EncodeJob, plan: EncodePlan) -> ProcessOutcome:
        key = job.source_path
        relay = _ProgressRelay(
            emit=self._emit,
            batch_id=batch_id,
            source_path=key,
            duration_sec=job.source_duration_sec,
            elapsed=lambda: self._elapsed_of(key),
            clock=self._clock,
        )
        os.makedirs(os.path.dirname(plan.part_path), exist_ok=True)
        process = self._factory(build_ffmpeg_args(plan, self._ffmpeg), relay.feed)
        with self._lock:
            if self._abort.is_set():
                return ProcessOutcome(returncode=None, cancelled=True, stderr_tail=())
            process.start()
            active = _ActiveItem(batch_id=batch_id, process=process, clock=PausableClock(now=self._clock()))
            self._active[key] = active
            if self._paused:
                process.pause()
                active.clock.pause(self._clock())
        try:
            process.set_priority(self._settings.process_priority)
        except (OSError, psutil.Error) as error:
            logger.warning("인코딩 프로세스 우선순위를 바꾸지 못했습니다: %s", error)
        try:
            return process.wait()
        finally:
            with self._lock:
                self._active.pop(key, None)

    def _elapsed_of(self, source_path: str) -> float:
        with self._lock:
            active = self._active.get(source_path)
            if active is None:
                return 0.0
            return active.clock.elapsed(self._clock())

    def _gate_and_place(self, job: EncodeJob, plan: EncodePlan, attempt: _Attempt) -> _Outcome:
        """사후 게이트를 통과하면 배치하고, 아니면 결과를 지우고 판정에 맞는 상태를 돌려줌"""
        part = plan.part_path
        output = self._probe_output(part)
        verdict = judge_result(
            output=output,
            expected_stream_count=_expected_stream_count(plan),
            source_duration_sec=job.source_duration_sec,
            source_size_bytes=job.source_size,
            output_size_bytes=os.path.getsize(part) if os.path.exists(part) else 0,
            purpose=job.purpose,
        )
        if verdict is GateVerdict.PASS:
            return self._place(job, plan, attempt.decode_retry)
        self._remove_part(part)
        if verdict is GateVerdict.DISCARD_LARGER:
            return _Outcome(EncodeState.DISCARDED, "결과가 원본보다 큼", decode_retry=attempt.decode_retry)
        if verdict is GateVerdict.FAIL_TAG:
            return _Outcome(EncodeState.FAILED, "처리 표식 누락", decode_retry=attempt.decode_retry)
        return _Outcome(EncodeState.FAILED, "무결성 검사 실패", decode_retry=attempt.decode_retry)

    def _probe_output(self, part: str) -> OutputSummary | None:
        try:
            return summarize_probe(self._probe(part, LAYOUT_PROBE_ARGS, self._abort))
        except RECOVERABLE_ERRORS as error:
            logger.warning("결과 파일 검사 실패: %s (%s)", part, error)
            return None

    def _place(self, job: EncodeJob, plan: EncodePlan, decode_retry: bool) -> _Outcome:
        try:
            self._move_into_place(job, plan.part_path, plan.output_path)
        except OSError as error:
            return _Outcome(EncodeState.REPLACE_PENDING, f"교체 대기: {error}", decode_retry=decode_retry)
        return _Outcome(
            EncodeState.DONE,
            decode_retry=decode_retry,
            output_size=os.path.getsize(plan.output_path),
        )

    def _move_into_place(self, job: EncodeJob, part: str, final: str) -> None:
        """결과 임시 파일을 최종 경로로 배치하고, 실패해도 원본과 결과가 남도록 되돌림

        - 원본 보관: 원본을 보관 폴더로 옮긴 뒤 결과를 배치하고, 배치가 실패하면 원본을 되돌림
        - 별도 출력: 결과만 출력 폴더에 배치하고 원본은 건드리지 않음
        - 원본 교체: 결과를 배치한 뒤 경로가 다를 때만 원본을 지움
        - 이미 끝난 단계는 건너뛰므로 교체 대기 재시도에서 같은 함수를 다시 호출할 수 있음
        """
        source = job.source_path
        policy = self._settings.result_policy
        if os.path.exists(part):
            if policy == RESULT_POLICY_ARCHIVE and os.path.exists(source):
                archive = archive_path_of(source, self._settings.archive_dirname)
                os.makedirs(os.path.dirname(archive), exist_ok=True)
                os.replace(source, archive)
                try:
                    os.replace(part, final)
                except OSError:
                    os.replace(archive, source)
                    raise
            else:
                os.makedirs(os.path.dirname(final), exist_ok=True)
                os.replace(part, final)
        if policy == RESULT_POLICY_REPLACE and final != source and os.path.exists(source):
            os.remove(source)
        os.utime(final, ns=(time.time_ns(), job.source_mtime_ns))

    def retry_replace_pending(self, batch_id: int) -> None:
        """교체 대기 항목을 한 번 더 배치. 여전히 실패하면 사유만 갱신하고 대기 상태를 유지"""
        for row in self._history.list_items(batch_id):
            if row.state is not EncodeState.REPLACE_PENDING or row.output_path is None:
                continue
            job = job_from_json(row.plan_json)
            try:
                self._move_into_place(job, row.part_path or "", row.output_path)
            except OSError as error:
                self._update(batch_id, row.source_path, {"reason": f"교체 대기: {error}"})
                continue
            self._update(
                batch_id,
                row.source_path,
                {
                    "state": EncodeState.DONE,
                    "reason": "",
                    "part_path": None,
                    "ended_at": utc_now_iso(),
                    "output_size": os.path.getsize(row.output_path),
                },
            )
            self._forget_analysis_row(job)

    def _finish(self, batch_id: int, row: ItemRow, job: EncodeJob, outcome: _Outcome) -> None:
        fields: dict[str, object] = {
            "state": outcome.state,
            "reason": outcome.reason,
            "stderr_tail": outcome.stderr_tail,
            "decode_retry": outcome.decode_retry,
        }
        if outcome.state is EncodeState.PENDING:
            fields["started_at"] = None
            fields["part_path"] = None
        else:
            fields["ended_at"] = utc_now_iso()
        if outcome.state is EncodeState.DONE:
            fields["part_path"] = None
        if outcome.output_size is not None:
            fields["output_size"] = outcome.output_size
        self._history.update_item(batch_id, row.source_path, fields)
        self._emit(EncodeEvent(EVENT_STATE, batch_id, row.source_path, state=outcome.state, reason=outcome.reason))
        if outcome.state is EncodeState.DONE:
            self._forget_analysis_row(job)

    def _forget_analysis_row(self, job: EncodeJob) -> None:
        """원본 행을 분석 캐시에서 지워 다음 분석이 결과 파일을 새로 읽게 함"""
        if self._store is not None:
            self._store.apply([(job.filename, job.source_size, job.source_mtime_ns)], [])

    def _update(self, batch_id: int, source_path: str, fields: Mapping[str, object]) -> None:
        self._history.update_item(batch_id, source_path, fields)
        state = fields.get("state")
        if isinstance(state, EncodeState):
            self._emit(EncodeEvent(EVENT_STATE, batch_id, source_path, state=state))

    def _emit(self, event: EncodeEvent) -> None:
        if self._on_event is not None:
            self._on_event(event)

    def _unchanged(self, job: EncodeJob) -> bool:
        try:
            stat = os.stat(job.source_path)
        except OSError:
            return False
        return stat.st_size == job.source_size and stat.st_mtime_ns == job.source_mtime_ns

    def _remove_part(self, part: str) -> None:
        if part and os.path.exists(part):
            os.remove(part)


def _default_factory(args: Sequence[str], on_line: Callable[[str], None]) -> EncodeProcessLike:
    return EncodeProcess(args, on_progress_line=on_line)


def prepare_resume(history: EncodeHistory, batch_id: int) -> ResumeReport:
    """이어서 재인코딩 전에 진행 중 항목을 대기로 되돌리고, 원본이 바뀐 항목과 기록된 임시 파일을 정리

    - 기록에 경로가 있는 .part만 지우고, 기록에 없는 .part는 건드리지 않음(결정 16)
    - 원본 크기나 수정 시각이 기록과 다르면 건너뜀으로 바꿔 재개 대상에서 뺌
    - 원본을 보관 폴더로 옮긴 뒤 결과 배치 전에 끊긴 항목은 결과를 지우지 않고 교체 대기로 둠
    """
    for row in history.list_items(batch_id):
        if row.state not in (EncodeState.RUNNING, EncodeState.PENDING) or row.part_path is None:
            continue
        if _interrupted_after_archive(row):
            history.update_item(
                batch_id,
                row.source_path,
                {"state": EncodeState.REPLACE_PENDING, "reason": "중단으로 교체 미완료"},
            )
    history.reset_running(batch_id)
    resumable = 0
    excluded = 0
    removed = 0
    for row in history.list_items(batch_id):
        if row.state is not EncodeState.PENDING:
            continue
        stat_ok = _same_stat(row)
        if not stat_ok:
            history.update_item(batch_id, row.source_path, {"state": EncodeState.SKIPPED, "reason": "원본이 바뀌어 재개 제외"})
            excluded += 1
            continue
        if row.part_path is not None and os.path.exists(row.part_path):
            os.remove(row.part_path)
            removed += 1
        history.update_item(batch_id, row.source_path, {"part_path": None, "started_at": None})
        resumable += 1
    return ResumeReport(resumable=resumable, excluded=excluded, removed_parts=removed)


def _interrupted_after_archive(row: ItemRow) -> bool:
    """원본은 보관 폴더로 옮겨졌고 결과 임시 파일만 남은 상태인지 여부"""
    return (
        row.archive_path is not None
        and row.part_path is not None
        and os.path.exists(row.part_path)
        and os.path.exists(row.archive_path)
        and not os.path.exists(row.source_path)
    )


def _same_stat(row: ItemRow) -> bool:
    try:
        stat = os.stat(row.source_path)
    except OSError:
        return False
    return stat.st_size == row.source_size and stat.st_mtime_ns == row.source_mtime_ns


def find_unrecorded_parts(roots: Iterable[str], recorded: Collection[str]) -> list[str]:
    """작업 폴더 아래 기록에 없는 .part 파일 경로 목록(안내용, 삭제하지 않음)"""
    found: list[str] = []
    for root in roots:
        for directory, _dirs, names in os.walk(root):
            for name in names:
                path = os.path.join(directory, name)
                if name.endswith(".part") and path not in recorded:
                    found.append(path)
    return sorted(found)
