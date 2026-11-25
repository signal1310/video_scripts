"""인코딩 배치 실행 서비스: 대상 검사, 인코딩, 사후 게이트, 결과 배치, 기록 갱신

- 항목은 동시 실행 수만큼 워커 스레드에서 처리하며, 워커는 화면 객체에 접근하지 않음
- 상태와 진행 변화는 on_event 콜백으로 전달하고, 호출자가 화면 스레드로 옮김
- FFmpeg 프로세스, ffprobe 실행, 분석 캐시 저장소는 주입 가능. 단위 테스트는 가짜 구현을 씀
- 입력과 직렬화는 encode_job, 결과 배치는 encode_placement, 재개 준비는 encode_resume에 둠
"""

import dataclasses
import logging
import os
import shutil
import threading
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor

import psutil

from src.core.analysis.prober import resolve_ffprobe_executable
from src.core.command_history import STAGE_MAIN_ENCODE, command_scope
from src.core.config import RESULT_POLICY_ARCHIVE, RESULT_POLICY_SEPARATE
from src.core.encode.encode_gate import GatePurpose, GateVerdict, OutputSummary, judge_result, summarize_probe
from src.core.encode.encode_paths import (
    archive_path_of,
    choose_free_path,
    in_place_output_path,
    part_path_of,
    separate_output_path,
)
from src.core.encode.encode_plan import ACTION_DROP, DecodeMode, EncodePlan, choose_container
from src.core.encode.encode_process import ProcessOutcome
from src.core.encode.encode_progress import EarlyStopHit
from src.core.encode.encode_state import UNFINISHED_STATES, EncodeState
from src.core.encode.ffmpeg_command import (
    HW_DECODE_SOURCE_CODECS,
    build_ffmpeg_args,
    build_sample_ffmpeg_args,
    resolve_ffmpeg_executable,
)
from src.core.encode.power_state import prevent_sleep
from src.core.encode.quality_tuning import format_saving_adjustment
from src.core.encode.stream_layout import StreamLayout, parse_stream_layout
from src.core.encoder_specs import get_encode_codec_family
from src.core.exceptions import DomainInterruptedError, DomainRuntimeError, DomainTimeoutError
from src.service.analysis_store import AnalysisStore
from src.service.encode import encode_reasons as reasons
from src.service.encode.encode_early_stop import EarlyStopWatch
from src.service.encode.encode_events import (
    EVENT_LONG_PAUSE,
    EVENT_STATE,
    EncodeEvent,
    EventSink,
    ProgressRelay,
    main_phase_event,
)
from src.service.encode.encode_history import EncodeHistory, ItemRow, utc_now_iso
from src.service.encode.encode_job import EncodeJob, EncodeSettings, job_from_json, resolve_workers
from src.service.encode.encode_outcome import EncodeAttempt, ItemOutcome, build_finish_fields, outcome_with_tuning
from src.service.encode.encode_output_history import EncodeOutputHistory, query_known_tag_names, record_done_output
from src.service.encode.encode_path_claims import ResultPathClaims
from src.service.encode.encode_pause import PauseControl
from src.service.encode.encode_placement import place_result
from src.service.encode.encode_ports import ProbeFn, ProcessFactory, default_probe, default_process_factory
from src.service.encode.encode_quality_tuner import RetryWatch, tune_and_encode
from src.service.encode.encode_sample_predict import predict_saving_size
from src.service.encode.encode_tag_io import build_tagged_plan, merge_mp4_output_tags, write_result_tags

logger = logging.getLogger(__name__)
DISK_SPACE_RATIO: float = 1.1
LAYOUT_PROBE_ARGS: tuple[str, ...] = ("-show_streams", "-show_format")
RECOVERABLE_ERRORS: tuple[type[Exception], ...] = (
    DomainRuntimeError,
    DomainTimeoutError,
    DomainInterruptedError,
    OSError,
    ValueError,
)


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
        output_history: EncodeOutputHistory | None = None,
        cpu_count: int | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._history: EncodeHistory = history
        self._owns_output_history: bool = output_history is None
        self._output_history: EncodeOutputHistory = output_history or EncodeOutputHistory(history.db_path)
        self._settings: EncodeSettings = settings
        self._ffmpeg: str = resolve_ffmpeg_executable(settings.ffmpeg_path)
        self._probe: ProbeFn = probe if probe is not None else default_probe(resolve_ffprobe_executable(settings.ffprobe_path))
        self._factory: ProcessFactory = process_factory if process_factory is not None else default_process_factory
        self._on_event: EventSink | None = on_event
        self._store: AnalysisStore | None = store
        self._cpu_count: int = cpu_count if cpu_count is not None else (os.cpu_count() or 1)
        self._clock: Callable[[], float] = clock
        self._lock: threading.Lock = threading.Lock()
        self._abort: threading.Event = threading.Event()
        self._stop_requested: bool = False
        self._control: PauseControl = PauseControl(abort=self._abort, clock=clock, notify=self._notify_long_pause)
        self._claims: ResultPathClaims = ResultPathClaims()
        self._early_stops: dict[str, EarlyStopHit] = {}

    def run(self, batch_id: int) -> None:
        """배치의 대기 항목을 동시 실행하고, 중지하지 않았다면 교체 대기 항목을 다시 시도한 뒤 끝난 배치를 닫음"""
        try:
            with prevent_sleep(self._settings.prevent_sleep):
                workers = resolve_workers(self._settings, self._cpu_count)
                with ThreadPoolExecutor(workers) as pool:
                    self._run_pending(pool, batch_id)
                if not self._abort.is_set():
                    self.retry_replace_pending(batch_id)
                if not any(row.state in UNFINISHED_STATES for row in self._history.list_items(batch_id)):
                    self._history.close_batch(batch_id)
        finally:
            self._control.finish()
            if self._owns_output_history:
                self._output_history.close()

    def _run_pending(self, pool: ThreadPoolExecutor, batch_id: int) -> None:
        """대기 항목을 풀에 넣어 실행. 현재 영상 후 중지 때문에 시작하지 못한 항목은 예약이 풀리면 같은 배치에서 다시 넣음"""
        pending = [row for row in self._history.list_items(batch_id) if row.state is EncodeState.PENDING]
        while pending and not self._abort.is_set() and not self._stop_requested:
            futures = [(row, pool.submit(self._process_item, batch_id, row, job_from_json(row.plan_json))) for row in pending]
            pending = [row for row, future in futures if future.result()]

    def request_stop_after_current(self) -> None:
        """진행 중인 항목은 끝내고 새 항목은 시작하지 않음(현재 영상 후 중지)"""
        with self._lock:
            self._stop_requested = True

    def cancel_stop_after_current(self) -> None:
        """현재 영상 후 중지 예약을 취소해, 아직 시작하지 않은 항목부터 다시 시작하게 함"""
        with self._lock:
            self._stop_requested = False

    def cancel_all(self) -> None:
        """진행 중인 프로세스를 종료하고 해당 항목은 대기로 되돌림(즉시 중지)"""
        self._abort.set()
        self._control.cancel_all()

    def pause_all(self) -> None:
        """진행 중인 모든 프로세스를 일시중지하고 장시간 안내 타이머를 시작(일시중지 제어는 encode_pause가 맡음)"""
        self._control.pause_all()

    def pause_one(self, source: str) -> bool:
        """실행 중인 영상 하나만 일시중지하고, 멈춘 시간이 기준을 넘으면 안내 이벤트를 보냄. 적용하면 참"""
        return self._control.pause_one(source)

    def resume_one(self, source: str) -> bool:
        """개별 일시중지한 영상 하나를 다시 실행. 전체 일시중지 중이거나 해당 항목이 없으면 거짓"""
        return self._control.resume_one(source)

    def cancel_one(self, source: str) -> None:
        """실행 중인 영상 하나를 즉시 중지해 대기로 되돌리고, 나머지 항목은 계속 진행"""
        self._control.cancel_one(source)

    def resume_all(self) -> None:
        """일시중지한 프로세스를 모두 다시 실행. 전체 일시중지 뒤에는 개별 일시중지 항목도 함께 실행"""
        self._control.resume_all()

    def check_long_pause(self) -> None:
        """현재 일시중지 지속 시간이 안내 기준을 넘은 항목마다 안내 이벤트를 보냄(결정 16)"""
        self._control.check_long_pause()

    def _notify_long_pause(self, batch_id: int, source: str) -> None:
        self._emit(EncodeEvent(kind=EVENT_LONG_PAUSE, batch_id=batch_id, source_path=source))

    def _process_item(self, batch_id: int, row: ItemRow, job: EncodeJob) -> bool:
        """항목 하나를 실행. 현재 영상 후 중지 때문에 시작하지 않았으면 참을 돌려줌"""
        if self._abort.is_set():
            return False
        if self._stop_requested:
            return True
        self._update(batch_id, row.source_path, {
            "state": EncodeState.RUNNING, "started_at": utc_now_iso(), "reason": "",
        })
        try:
            try:
                outcome = self._encode_item(batch_id, job)
            except RECOVERABLE_ERRORS as error:
                if self._abort.is_set():
                    outcome = ItemOutcome(EncodeState.PENDING)
                else:
                    logger.warning("인코딩 항목 처리 중 오류: %s (%s)", row.source_path, error)
                    outcome = ItemOutcome(EncodeState.FAILED, reasons.processing_error(error))
            self._finish(batch_id, row, job, outcome)
        finally:
            self._claims.release(job.source_path)
        return False

    def _encode_item(self, batch_id: int, job: EncodeJob) -> ItemOutcome:
        source = job.source_path
        if not self._unchanged(job):
            return ItemOutcome(EncodeState.SKIPPED, reasons.SOURCE_CHANGED)
        if shutil.disk_usage(os.path.dirname(source)).free < job.source_size * DISK_SPACE_RATIO:
            return ItemOutcome(EncodeState.SKIPPED, reasons.NO_DISK_SPACE)
        layout = parse_stream_layout(self._probe(source, LAYOUT_PROBE_ARGS, self._abort))
        primary = layout.primary_video
        if primary is None:
            return ItemOutcome(EncodeState.FAILED, reasons.NO_PRIMARY_VIDEO)
        if primary.is_interlaced:
            return ItemOutcome(EncodeState.SKIPPED, reasons.INTERLACED)
        planned = self._plan(batch_id, job, layout)
        if isinstance(planned, ItemOutcome):
            return planned
        plan, source_rating = planned
        sampled_bytes: int | None = None
        adjusted_quality: float | None = None
        base_quality = job.quality
        if job.purpose is GatePurpose.SAVING:
            prediction = predict_saving_size(
                batch_id=batch_id, job=job, plan=plan, layout=layout, ffmpeg_exec=self._ffmpeg,
                process_factory=self._factory, abort=self._abort, build_args=build_sample_ffmpeg_args,
                control=self._control, emit=self._emit,
                discard_factor=self._settings.sample_saving_discard_factor)
            if prediction is None:
                return ItemOutcome(EncodeState.PENDING)
            if prediction.succeeded_segments == prediction.total_segments > 0:
                sampled_bytes = prediction.predicted_total_bytes
            if prediction.exceeds_original:
                return ItemOutcome(EncodeState.DISCARDED, reasons.SAMPLE_PREDICT_REASON, sample_predicted_bytes=sampled_bytes)
            if prediction.adjusted_quality is not None:
                # - 추가 샘플 없이 예측 비트레이트로 올린 품질값을 본 인코딩 계획에 반영
                adjusted_quality = prediction.adjusted_quality
                job = dataclasses.replace(job, quality=adjusted_quality)
                plan = dataclasses.replace(plan, quality=adjusted_quality)
            self._emit(main_phase_event(batch_id, source, job.quality, adjusted_quality=adjusted_quality))
        if job.purpose is GatePurpose.KEYFRAME_FIX:
            plan, attempt = tune_and_encode(
                batch_id=batch_id, job=job, plan=plan, layout=layout, ffmpeg_exec=self._ffmpeg,
                process_factory=self._factory, abort=self._abort, emit=self._emit, build_args=build_sample_ffmpeg_args,
                run_main=lambda active, watch: self._encode_with_fallback(batch_id, job, active, sink_override=watch),
                cancel_for_retry=lambda: self._control.cancel_if_running(job.source_path, lambda: None),
                remove_part=self._remove_part, control=self._control,
                quality_delta_max=self._settings.protect_cq_delta_max,
            )
        else:
            attempt = self._encode_with_fallback(batch_id, job, plan)
        if sampled_bytes is not None:
            attempt = dataclasses.replace(attempt, sample_predicted_bytes=sampled_bytes)
        if adjusted_quality is not None:
            # - 결과 사유 줄과 기록에 사전 예측으로 품질값을 올린 사실을 남김
            attempt = dataclasses.replace(
                attempt,
                tuned_quality=adjusted_quality,
                tuning_reason=format_saving_adjustment(base_quality, adjusted_quality),
            )
        if attempt.cancelled or self._abort.is_set():
            self._remove_part(plan.part_path)
            return ItemOutcome(EncodeState.PENDING)
        if attempt.early_stop is not None:
            self._remove_part(plan.part_path)
            return ItemOutcome(EncodeState.DISCARDED, reasons.EARLY_STOP_REASON,
                               early_stop=attempt.early_stop, sample_predicted_bytes=sampled_bytes)
        if attempt.failure is not None:
            self._remove_part(plan.part_path)
            return outcome_with_tuning(attempt.failure, attempt)
        return self._gate_and_place(job, plan, attempt, source_rating)

    def _plan(self, batch_id: int, job: EncodeJob, layout: StreamLayout) -> tuple[EncodePlan, int | None] | ItemOutcome:
        """결과 경로와 인코딩 계획을 만든다. 같은 이름 건너뜀이나 기록 충돌이면 ItemOutcome을 돌려줌"""
        settings = self._settings
        source = job.source_path
        container = choose_container(
            job.source_ext, layout, settings.container_policy, get_encode_codec_family(settings.encoder)
        )
        if settings.result_policy == RESULT_POLICY_SEPARATE:
            candidate = separate_output_path(source, job.root_dir, settings.output_dirname, container)
        else:
            candidate = in_place_output_path(source, container)
        final = candidate
        if final != source:
            chosen = choose_free_path(candidate, settings.name_conflict, os.path.exists)
            if chosen is None:
                return ItemOutcome(EncodeState.SKIPPED, reasons.NAME_EXISTS)
            final = chosen
        archive: str | None = None
        if settings.result_policy == RESULT_POLICY_ARCHIVE:
            archive = archive_path_of(source, settings.archive_dirname)
            if os.path.exists(archive):
                return ItemOutcome(EncodeState.FAILED, reasons.ARCHIVE_EXISTS)
        part = part_path_of(final)
        if os.path.exists(part):
            # - 같은 자리의 임시 파일은 이 실행이 만든 파일이 아니므로 임시 파일 경로는 기록하지 않고 결과 경로만 남김
            self._update(batch_id, source, {"output_path": final})
            return ItemOutcome(EncodeState.FAILED, reasons.PART_LEFT)
        if not self._claims.claim(source, (final, part)):
            return ItemOutcome(EncodeState.SKIPPED, reasons.PATH_IN_USE)
        plan, source_rating = build_tagged_plan(
            job=job, settings=settings, part_path=part, output_path=final, layout=layout,
            known_tag_names=query_known_tag_names(self._output_history),
        )
        self._update(batch_id, source, {"part_path": part, "output_path": final, "archive_path": archive})
        return plan, source_rating

    def _encode_with_fallback(
        self, batch_id: int, job: EncodeJob, plan: EncodePlan, *, sink_override: RetryWatch | None = None
    ) -> EncodeAttempt:
        """인코딩을 실행하고, 하드웨어 디코딩 실패는 CPU 디코딩으로 한 번 더 시도(결정 14)"""
        first = self._run_ffmpeg(batch_id, job, plan, sink_override=sink_override)
        early = self._take_early_stop(job.source_path)
        if early is not None:
            # - 중단 요청이 수락됐어도 종료 코드 0이면 결과가 완성된 것이므로 일반 사후 게이트로 보냄
            return EncodeAttempt() if first.returncode == 0 else EncodeAttempt(early_stop=early)
        if first.cancelled:
            return EncodeAttempt(cancelled=True)
        if first.returncode == 0:
            return EncodeAttempt()
        hw_source = plan.source_codec.lower() in HW_DECODE_SOURCE_CODECS
        if not (hw_source and plan.decode_mode is DecodeMode.SHANA_DEFAULT) or self._abort.is_set():
            return EncodeAttempt(failure=ItemOutcome(
                EncodeState.FAILED, reasons.encode_failed(first.returncode), "\n".join(first.stderr_tail),
            ))
        self._remove_part(plan.part_path)
        retry = self._run_ffmpeg(batch_id, job, dataclasses.replace(plan, decode_mode=DecodeMode.CPU_ONLY))
        if retry.cancelled:
            return EncodeAttempt(cancelled=True)
        if retry.returncode == 0:
            return EncodeAttempt(decode_retry=True)
        return EncodeAttempt(
            decode_retry=True,
            failure=ItemOutcome(EncodeState.FAILED, reasons.encode_retry_failed(retry.returncode),
                                "\n".join(retry.stderr_tail)),
        )

    def _run_ffmpeg(
        self, batch_id: int, job: EncodeJob, plan: EncodePlan, *, sink_override: RetryWatch | None = None
    ) -> ProcessOutcome:
        key = job.source_path
        sink: EventSink = self._emit if sink_override is None else sink_override
        if job.purpose is GatePurpose.SAVING:
            sink = EarlyStopWatch.for_job(
                forward=self._emit, job=job,
                request_stop=lambda hit: self._request_early_stop(key, hit),
                margin=self._settings.early_stop_margin)
        relay = ProgressRelay(
            emit=sink, batch_id=batch_id, source_path=key, duration_sec=job.source_duration_sec,
            elapsed=lambda: self._control.elapsed(key), clock=self._clock)
        os.makedirs(os.path.dirname(plan.part_path), exist_ok=True)
        args = build_ffmpeg_args(plan, self._ffmpeg)
        process = self._factory(args, relay.feed)
        if not self._control.launch(key, batch_id, process):
            return ProcessOutcome(returncode=None, cancelled=True, stderr_tail=())
        try:
            process.set_priority(self._settings.process_priority)
        except (OSError, psutil.Error) as error:
            logger.warning("인코딩 프로세스 우선순위를 바꾸지 못했습니다: %s", error)
        try:
            with command_scope(STAGE_MAIN_ENCODE, args, target=key) as handle:
                outcome = process.wait()
                handle.exit_code = None if outcome.cancelled else outcome.returncode
            return outcome
        finally:
            self._control.release(key)

    def _gate_and_place(
        self, job: EncodeJob, plan: EncodePlan, attempt: EncodeAttempt, source_rating: int | None = None
    ) -> ItemOutcome:
        """사후 게이트를 통과하면 배치하고, 아니면 결과를 지우고 판정에 맞는 상태를 돌려줌"""
        part = plan.part_path
        try:
            tag_exempt, tag_notice = write_result_tags(plan, source_rating=source_rating)
        except RECOVERABLE_ERRORS as error:
            logger.warning("결과 태그 기록 실패: %s (%s)", part, error)
            self._remove_part(part)
            return ItemOutcome(EncodeState.FAILED, reasons.tag_write_failed(error), decode_retry=attempt.decode_retry)
        output = self._probe_output(part, plan.container)
        output_bytes = os.path.getsize(part) if os.path.exists(part) else 0
        verdict = judge_result(
            output=output,
            expected_stream_count=_expected_stream_count(plan),
            source_duration_sec=job.source_duration_sec,
            source_size_bytes=job.source_size,
            output_size_bytes=output_bytes,
            purpose=job.purpose,
            tag_mode=plan.tag_mode,
            tag_name=plan.tag_name,
            tag_exempt=tag_exempt,
        )
        if verdict is GateVerdict.PASS:
            done_reason = tag_notice or attempt.tuning_reason
            return outcome_with_tuning(self._place(job, plan, attempt.decode_retry, done_reason), attempt)
        self._remove_part(part)
        state, reason = reasons.gate_failure(verdict)
        # - 폐기 항목은 지운 결과 크기를 기록해 원본보다 얼마나 컸는지 결과 목록에 보임
        measured = output_bytes if state is EncodeState.DISCARDED else None
        return outcome_with_tuning(
            ItemOutcome(state, reason, decode_retry=attempt.decode_retry, output_size=measured), attempt
        )

    def _probe_output(self, part: str, container: str) -> OutputSummary | None:
        try:
            summary = summarize_probe(self._probe(part, LAYOUT_PROBE_ARGS, self._abort))
            return merge_mp4_output_tags(part, container, summary)
        except RECOVERABLE_ERRORS as error:
            logger.warning("결과 파일 검사 실패: %s (%s)", part, error)
            return None

    def _place(self, job: EncodeJob, plan: EncodePlan, decode_retry: bool, reason: str = "") -> ItemOutcome:
        try:
            self._move_into_place(job, plan.part_path, plan.output_path)
        except OSError as error:
            return ItemOutcome(EncodeState.REPLACE_PENDING, reasons.replace_pending(error), decode_retry=decode_retry)
        record_done_output(
            self._output_history, output_path=plan.output_path, encoder=plan.encoder,
            quality=plan.quality, tag_mode=plan.tag_mode, tag_name=plan.tag_name)
        return ItemOutcome(EncodeState.DONE, reason=reason, decode_retry=decode_retry, output_size=os.path.getsize(plan.output_path))

    def _move_into_place(self, job: EncodeJob, part: str, final: str) -> None:
        """결과 임시 파일을 최종 경로로 배치(덮어쓰기 없음, 실패 시 원본 되돌림은 encode_placement가 담당)"""
        place_result(
            source=job.source_path, part=part, final=final,
            archive_dirname=self._settings.archive_dirname, policy=self._settings.result_policy,
            mtime_ns=job.source_mtime_ns)

    def retry_replace_pending(self, batch_id: int, only_source: str | None = None) -> None:
        """교체 대기 항목을 한 번 더 배치. 여전히 실패하면 사유만 갱신하고 대기 상태를 유지

        - only_source를 주면 그 원본 경로의 항목만 다시 배치(결과 목록의 행 단위 재시도)
        """
        try:
            for row in self._history.list_items(batch_id):
                if row.state is not EncodeState.REPLACE_PENDING or row.output_path is None:
                    continue
                if only_source is not None and row.source_path != only_source:
                    continue
                job = job_from_json(row.plan_json)
                try:
                    self._move_into_place(job, row.part_path or "", row.output_path)
                except OSError as error:
                    self._update(batch_id, row.source_path, {"reason": reasons.replace_pending(error)})
                    continue
                done = {"state": EncodeState.DONE, "reason": "", "part_path": None, "ended_at": utc_now_iso(), "output_size": os.path.getsize(row.output_path)}
                self._update(batch_id, row.source_path, done)
                record_done_output(
                    self._output_history, output_path=row.output_path, encoder=self._settings.encoder,
                    quality=row.tuned_quality if row.tuned_quality is not None else job.quality,
                    tag_mode=self._settings.encode_tag_mode, tag_name=self._settings.encode_tag_name)
                self._forget_analysis_row(job)
        finally:
            if self._owns_output_history:
                self._output_history.close()

    def forfeit_replace_pending(self, batch_id: int, source_path: str) -> bool:
        """교체 대기 항목을 실패로 바꿔 더 시도하지 않음. 원본과 결과 임시 파일은 그대로 둬 둘 다 남김

        - 교체 대기가 아니면 바꾸지 않고 거짓을 돌려줌
        """
        row = self._history.get_item(batch_id, source_path)
        if row is None or row.state is not EncodeState.REPLACE_PENDING:
            return False
        self._update(batch_id, source_path, {
            "state": EncodeState.FAILED, "reason": reasons.REPLACE_FORFEITED, "ended_at": utc_now_iso(),
        })
        return True

    def _finish(self, batch_id: int, row: ItemRow, job: EncodeJob, outcome: ItemOutcome) -> None:
        self._history.update_item(batch_id, row.source_path, build_finish_fields(outcome, utc_now_iso()))
        self._emit(EncodeEvent(EVENT_STATE, batch_id, row.source_path, state=outcome.state, reason=outcome.reason))
        if outcome.state is EncodeState.DONE:
            self._forget_analysis_row(job)

    def _forget_analysis_row(self, job: EncodeJob) -> None:
        """원본 행을 분석 캐시에서 지워 다음 분석이 결과 파일을 새로 읽게 함"""
        if self._store is not None:
            self._store.apply([(job.filename, job.source_size, job.source_mtime_ns)], [])

    def _update(self, batch_id: int, source_path: str, fields: Mapping[str, object]) -> None:
        self._history.update_item(batch_id, source_path, fields)
        if isinstance(state := fields.get("state"), EncodeState):
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

    def _request_early_stop(self, key: str, hit: EarlyStopHit) -> bool:
        """절약 목적 항목 하나만 조기 중단 요청. 일시중지, 즉시 중지 중이거나 실행 중이 아니면 거부(False)"""
        return self._control.cancel_if_running(key, lambda: self._record_early_stop(key, hit))

    def _record_early_stop(self, key: str, hit: EarlyStopHit) -> None:
        with self._lock:
            self._early_stops[key] = hit

    def _take_early_stop(self, key: str) -> EarlyStopHit | None:
        """항목의 조기 중단 기록을 꺼냄. 기록이 없으면 None"""
        with self._lock:
            return self._early_stops.pop(key, None)

    def _remove_part(self, part: str) -> None:
        if part and os.path.exists(part):
            os.remove(part)


