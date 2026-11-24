"""화질 분석 배치 실행과 결과 캐시(SQLite) 저장소, 서비스 모듈

- 캐시는 분석 캐시와 같은 `video_cache.sqlite3`의 독립 테이블 `frame_analysis_cache`에 두어 분석 캐시 비우기, 스키마 재생성과 무관하게 보존
- 스레드별 연결, 잠금 재시도, `close()` 정리 방식은 `encode_output_history.py`와 같음
- 영상 1개를 마칠 때마다 즉시 저장해 중단되어도 완료분을 잃지 않음
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import TypeVar

from src.core.analysis.frame_analysis import (
    DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    FrameAnalysisThresholds,
    analysis_version_for_mode,
    apply_no_bars_ratio,
    is_usable_analysis_version,
    is_vr_protected,
    rejudge_candidate_tier,
    resolve_crop_frame,
)
from src.core.analysis.frame_analysis_runner import (
    FrameAnalysisResult,
    ProcessStderrRunner,
    StderrCommandRunner,
    analyze_frames,
)
from src.core.config import DEFAULT_FRAME_ANALYSIS_MODE
from src.core.encode.ffmpeg_command import FFMPEG_EXECUTABLE_DEFAULT
from src.core.models import ActiveFrame
from src.core.quality_probe import METRIC_VMAF, resolve_metric_name
from src.service.frame_analysis_record import (
    RECORD_COLUMNS,
    FileKey,
    FrameAnalysisRecord,
    FrameAnalysisRequest,
    record_from_result,
    record_to_row,
    result_from_record,
    row_to_record,
)

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

FRAME_ANALYSIS_TABLE: str = "frame_analysis_cache"
FRAME_ANALYSIS_VR_TABLE: str = "frame_analysis_vr_override"
FRAME_ANALYSIS_CROP_TABLE: str = "frame_analysis_crop_exclude"
ANALYSIS_TIMEOUT_SEC: float = 1800.0
SQLITE_BUSY_TIMEOUT_SEC: float = 5.0
WRITE_RETRY_ATTEMPTS: int = 5
WRITE_RETRY_BASE_SEC: float = 0.05
# - 영상 단위와 영상 내부 라운드 병렬 워커 수(이름 있는 상수를 위치 인자로 넘김)
VIDEO_ANALYSIS_WORKERS: int = 4
INNER_ROUND_WORKERS_SINGLE: int = 4
INNER_ROUND_WORKERS_FEW: int = 2
FEW_VIDEO_LIMIT: int = 3
_LOOKUP_CHUNK_SIZE: int = 500
_LOCKED_MARKERS: tuple[str, ...] = ("locked", "busy")

_SCHEMA_STATEMENTS: tuple[str, ...] = (
    f"CREATE TABLE IF NOT EXISTS {FRAME_ANALYSIS_TABLE} (\n"
    "  filename TEXT NOT NULL,\n"
    "  filesize_bytes INTEGER NOT NULL,\n"
    "  mtime_ns INTEGER NOT NULL,\n"
    "  active_width INTEGER NOT NULL,\n"
    "  active_height INTEGER NOT NULL,\n"
    "  active_x INTEGER NOT NULL,\n"
    "  active_y INTEGER NOT NULL,\n"
    "  has_black_bars INTEGER NOT NULL,\n"
    "  metric_used TEXT NOT NULL,\n"
    "  vmaf_model TEXT NOT NULL,\n"
    "  tier_scores_json TEXT NOT NULL,\n"
    "  candidate_tier TEXT,\n"
    "  valid_segments INTEGER NOT NULL,\n"
    "  total_segments INTEGER NOT NULL,\n"
    "  unmeasurable INTEGER NOT NULL,\n"
    "  is_vr_protected INTEGER NOT NULL,\n"
    "  measured_at TEXT NOT NULL,\n"
    "  analysis_version INTEGER NOT NULL,\n"
    "  PRIMARY KEY (filename, filesize_bytes, mtime_ns)\n"
    ") WITHOUT ROWID",
    f"CREATE TABLE IF NOT EXISTS {FRAME_ANALYSIS_VR_TABLE} (\n"
    "  source_path TEXT PRIMARY KEY,\n"
    "  manual_vr INTEGER NOT NULL\n"
    ") WITHOUT ROWID",
    f"CREATE TABLE IF NOT EXISTS {FRAME_ANALYSIS_CROP_TABLE} (\n"
    "  source_path TEXT PRIMARY KEY,\n"
    "  exclude_crop INTEGER NOT NULL\n"
    ") WITHOUT ROWID",
)
_SELECT_COLUMNS: str = ", ".join(RECORD_COLUMNS)
_PLACEHOLDERS: str = ", ".join("?" * len(RECORD_COLUMNS))


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_locked_error(error: sqlite3.OperationalError) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _LOCKED_MARKERS)


class FrameAnalysisStore:
    """화질 분석 결과 캐시의 스레드 안전 SQLite 저장소

    - 스레드마다 연결을 하나씩 두고 잠금 오류는 지수 간격으로 재시도
    - 다른 테이블을 만들거나 지우지 않아 분석 캐시 비우기, 재생성과 독립적
    """

    def __init__(self, db_path: str, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._db_path: str = db_path
        self._sleep: Callable[[float], None] = sleep
        self._lock: threading.Lock = threading.Lock()
        self._connections: dict[int, sqlite3.Connection] = {}

    def load_many(self, keys: Sequence[FileKey]) -> dict[FileKey, FrameAnalysisRecord]:
        """파일 식별 키 목록에 해당하는 캐시 행을 조회"""
        wanted = set(keys)
        if not wanted:
            return {}
        names = list(dict.fromkeys(name for name, _size, _mtime in keys))

        def _select(conn: sqlite3.Connection) -> dict[FileKey, FrameAnalysisRecord]:
            found: dict[FileKey, FrameAnalysisRecord] = {}
            for start in range(0, len(names), _LOOKUP_CHUNK_SIZE):
                chunk = names[start : start + _LOOKUP_CHUNK_SIZE]
                placeholders = ", ".join("?" * len(chunk))
                rows = conn.execute(
                    f"SELECT {_SELECT_COLUMNS} FROM {FRAME_ANALYSIS_TABLE} WHERE filename IN ({placeholders})",
                    chunk,
                ).fetchall()
                for row in rows:
                    record = row_to_record(row)
                    if record.key in wanted:
                        found[record.key] = record
            return found

        return self._run(_select)

    def save(self, record: FrameAnalysisRecord) -> None:
        """캐시 행을 저장하며 같은 키가 있으면 덮어씀"""

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    f"INSERT OR REPLACE INTO {FRAME_ANALYSIS_TABLE} ({_SELECT_COLUMNS}) VALUES ({_PLACEHOLDERS})",
                    record_to_row(record),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        self._run(_write)

    def count(self) -> int:
        """저장된 행 수 반환"""

        def _count(conn: sqlite3.Connection) -> int:
            return int(conn.execute(f"SELECT COUNT(*) FROM {FRAME_ANALYSIS_TABLE}").fetchone()[0])

        return self._run(_count)

    def load_vr_overrides(self, paths: Sequence[str]) -> frozenset[str]:
        """수동 VR 보호로 저장된 전체 경로 집합 조회"""

        def _select(conn: sqlite3.Connection) -> frozenset[str]:
            keys = list(dict.fromkeys(path for path in paths if path))
            if not keys:
                return frozenset()
            placeholders = ", ".join("?" * len(keys))
            rows = conn.execute(
                f"SELECT source_path FROM {FRAME_ANALYSIS_VR_TABLE} "
                f"WHERE manual_vr = 1 AND source_path IN ({placeholders})",
                keys,
            ).fetchall()
            return frozenset(str(row[0]) for row in rows)

        return self._run(_select)

    def set_vr_override(self, *, source_path: str, filename: str, manual_vr: bool) -> None:
        """수동 VR 보호를 저장하고 그 영상의 캐시 결과를 지워 다시 분석하게 함

        - 보호 여부에 따라 활성 화면과 축소 후보가 달라지므로 낡은 측정 결과를 남기지 않음
        """

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if manual_vr:
                    conn.execute(
                        f"INSERT OR REPLACE INTO {FRAME_ANALYSIS_VR_TABLE} (source_path, manual_vr) VALUES (?, 1)",
                        (source_path,),
                    )
                else:
                    conn.execute(
                        f"DELETE FROM {FRAME_ANALYSIS_VR_TABLE} WHERE source_path = ?",
                        (source_path,),
                    )
                conn.execute(f"DELETE FROM {FRAME_ANALYSIS_TABLE} WHERE filename = ?", (filename,))
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        self._run(_write)

    def load_crop_exclusions(self, paths: Sequence[str]) -> frozenset[str]:
        """영상별 검은 여백 잘라내기 제외로 저장된 전체 경로 집합 조회"""

        def _select(conn: sqlite3.Connection) -> frozenset[str]:
            keys = list(dict.fromkeys(path for path in paths if path))
            if not keys:
                return frozenset()
            placeholders = ", ".join("?" * len(keys))
            rows = conn.execute(
                f"SELECT source_path FROM {FRAME_ANALYSIS_CROP_TABLE} "
                f"WHERE exclude_crop = 1 AND source_path IN ({placeholders})",
                keys,
            ).fetchall()
            return frozenset(str(row[0]) for row in rows)

        return self._run(_select)

    def set_crop_exclusion(self, source_path: str, excluded: bool) -> None:
        """영상별 검은 여백 잘라내기 제외를 저장. 분석 캐시는 지우지 않음

        - 활성 화면과 축소 후보는 그대로 유효하며 판정 입력에서만 잘라내기를 건너뜀
        """
        if not source_path:
            return

        def _write(conn: sqlite3.Connection) -> None:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if excluded:
                    conn.execute(
                        f"INSERT OR REPLACE INTO {FRAME_ANALYSIS_CROP_TABLE}"
                        " (source_path, exclude_crop) VALUES (?, 1)",
                        (source_path,),
                    )
                else:
                    conn.execute(
                        f"DELETE FROM {FRAME_ANALYSIS_CROP_TABLE} WHERE source_path = ?",
                        (source_path,),
                    )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise

        self._run(_write)

    def close(self) -> None:
        """열려 있는 모든 스레드의 연결을 닫음"""
        with self._lock:
            for conn in self._connections.values():
                conn.close()
            self._connections.clear()
    def _run(self, action: Callable[[sqlite3.Connection], _T]) -> _T:
        attempt = 0
        while True:
            try:
                return action(self._connection())
            except sqlite3.OperationalError as error:
                if not _is_locked_error(error) or attempt + 1 >= WRITE_RETRY_ATTEMPTS:
                    raise
                self._sleep(WRITE_RETRY_BASE_SEC * (2**attempt))
                attempt += 1

    def _connection(self) -> sqlite3.Connection:
        ident = threading.get_ident()
        with self._lock:
            existing = self._connections.get(ident)
        if existing is not None:
            return existing
        os.makedirs(os.path.dirname(self._db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(
            self._db_path,
            timeout=SQLITE_BUSY_TIMEOUT_SEC,
            check_same_thread=False,
            isolation_level=None,
        )
        try:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
        except BaseException:
            conn.close()
            raise
        with self._lock:
            self._connections[ident] = conn
        return conn


ProgressCallback = Callable[[int, int, str, FrameAnalysisResult], None]
StageProgressCallback = Callable[[str, str, int, int], None]


def _unique_requests(requests: Sequence[FrameAnalysisRequest]) -> list[FrameAnalysisRequest]:
    """같은 파일 식별 키의 중복 요청을 하나로 줄임"""
    return list({request.key: request for request in requests}.values())


def _expected_vr(request: FrameAnalysisRequest) -> bool:
    """이 요청 영상이 지금 규칙에서 VR 보호 대상인지"""
    return is_vr_protected(
        request.width,
        request.height,
        has_vr_metadata=request.has_vr_metadata,
        manual_vr=request.manual_vr,
    )


def _is_reusable(
    record: FrameAnalysisRecord | None,
    request: FrameAnalysisRequest,
    metric_name: str,
    analysis_version: int,
    *,
    crop_only: bool = False,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
) -> bool:
    """캐시 행이 지금 측정 규칙과 같아 다시 재지 않고 쓸 수 있는지

    - 검은 여백 감지는 전체 화질 분석 행이나 감지 전용 행을 모두 재사용해 기존 점수를 보존
    - 전체 화질 분석은 같은 방식 버전과 같은 지표 행만 재사용하며 감지 전용 행은 다시 잼
    - 기준이 바뀌어 저장된 점수로 다시 판정한 축소 후보가 저장 값과 다르면 다시 잼
    """
    if record is None or record.is_vr_protected != _expected_vr(request):
        return False
    if crop_only:
        return is_usable_analysis_version(record.analysis_version, analysis_version)
    if record.analysis_version != analysis_version or record.metric_used != metric_name:
        return False
    if not record.tier_scores:
        return True
    return (
        rejudge_candidate_tier(
            record.tier_scores,
            record.candidate_tier,
            use_vmaf=metric_name == METRIC_VMAF,
            thresholds=thresholds,
        )
        == record.candidate_tier
    )


def _rejudged_result(
    record: FrameAnalysisRecord,
    request: FrameAnalysisRequest,
    thresholds: FrameAnalysisThresholds,
    *,
    keep_candidate: bool = False,
) -> FrameAnalysisResult:
    """캐시 결과를 현재 축소 후보 기준과 검은 여백 면적 상한으로 다시 판정

    - 저장된 규격 점수는 그대로 두고 후보만 다시 고름
    - 활성 화면 면적이 현재 상한을 넘으면 전체 프레임으로 되돌림
    - keep_candidate면 감지 전용 실행처럼 후보를 그대로 보존
    """
    base = result_from_record(record)
    region = apply_no_bars_ratio(
        base.active_region, request.width, request.height, thresholds=thresholds
    )
    return FrameAnalysisResult(
        active_region=region,
        metric_used=base.metric_used,
        vmaf_model=base.vmaf_model,
        tier_scores=base.tier_scores,
        candidate_tier=(
            base.candidate_tier
            if keep_candidate
            else rejudge_candidate_tier(
                base.tier_scores,
                base.candidate_tier,
                use_vmaf=base.metric_used == METRIC_VMAF,
                thresholds=thresholds,
            )
        ),
        valid_segment_count=base.valid_segment_count,
        total_segment_count=base.total_segment_count,
        unmeasurable=base.unmeasurable,
        is_vr_protected=base.is_vr_protected,
        analysis_version=base.analysis_version,
    )


def _inner_round_workers(video_count: int, *, runner_injected: bool) -> int:
    """영상 수와 실행기 주입 여부로 영상 내부 라운드 병렬 워커 수 결정

    - 주입 실행기는 테스트나 다른 실행 정책을 따르므로 직렬로 둠
    - 영상이 1개면 유휴 워커를 라운드에 쓰고, 소수면 절반만 씀
    """
    if runner_injected:
        return 1
    if video_count <= 1:
        return INNER_ROUND_WORKERS_SINGLE
    if video_count <= FEW_VIDEO_LIMIT:
        return INNER_ROUND_WORKERS_FEW
    return 1


class FrameAnalysisService:
    """화질 분석 배치 실행과 캐시 재사용을 조율하는 서비스

    - 캐시가 현재 측정 규칙 버전과 같으면 다시 재지 않고 재사용
    - 영상 1개를 마칠 때마다 캐시에 저장해 취소되어도 완료분을 보존
    - 실행기 주입으로 FFmpeg 없이 단위 테스트할 수 있음
    """

    def __init__(
        self,
        store: FrameAnalysisStore,
        *,
        work_dir: str,
        ffmpeg_executable: str = FFMPEG_EXECUTABLE_DEFAULT,
        has_vmaf: bool = False,
        runner: StderrCommandRunner | None = None,
        timeout_sec: float = ANALYSIS_TIMEOUT_SEC,
        now: Callable[[], str] = _utc_now_iso,
        mode: str = DEFAULT_FRAME_ANALYSIS_MODE,
        video_workers: int = VIDEO_ANALYSIS_WORKERS,
        thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    ) -> None:
        self._store: FrameAnalysisStore = store
        self._work_dir: str = work_dir
        self._ffmpeg_executable: str = ffmpeg_executable
        self._has_vmaf: bool = has_vmaf
        self._runner: StderrCommandRunner | None = runner
        self._timeout_sec: float = timeout_sec
        self._now: Callable[[], str] = now
        self._mode: str = mode
        self._analysis_version: int = analysis_version_for_mode(mode)
        self._video_workers: int = max(1, video_workers)
        self._thresholds: FrameAnalysisThresholds = thresholds

    def analyze_batch(
        self,
        requests: Sequence[FrameAnalysisRequest],
        *,
        force: bool = False,
        crop_only: bool = False,
        crop_only_names: frozenset[str] = frozenset(),
        cancel_event: threading.Event | None = None,
        on_progress: ProgressCallback | None = None,
        on_stage: StageProgressCallback | None = None,
    ) -> dict[str, FrameAnalysisResult]:
        """요청 목록을 영상 단위 워커 풀로 분석하고 파일명별 결과를 반환. 취소되면 `InterruptedError`

        - crop_only면 1단계 검은 여백 스캔만 실행하고, 이미 있는 전체 화질 분석 행은 그대로 재사용
        - crop_only_names에 든 영상은 전체 실행에서도 검은 여백만 감지
        """
        unique = _unique_requests(requests)
        total = len(unique)
        cached = {} if force else self._store.load_many([request.key for request in unique])
        results: dict[str, FrameAnalysisResult] = {}
        metric_name = resolve_metric_name(self._has_vmaf)
        os.makedirs(self._work_dir, exist_ok=True)
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("화질 분석이 취소되었습니다")
        runner_injected = self._runner is not None
        inner_workers = _inner_round_workers(total, runner_injected=runner_injected)
        exec_runner = self._runner_for(cancel_event)

        def _stage_progress(filename: str, stage: str, done: int, stage_total: int) -> None:
            if on_stage is not None:
                on_stage(filename, stage, done, stage_total)

        def _analyze_one(
            item: tuple[int, FrameAnalysisRequest],
        ) -> tuple[FrameAnalysisRequest, FrameAnalysisResult]:
            index, request = item
            request_crop_only = crop_only or request.filename in crop_only_names
            record = cached.get(request.key)
            if record is not None and _is_reusable(
                record,
                request,
                metric_name,
                self._analysis_version,
                crop_only=request_crop_only,
                thresholds=self._thresholds,
            ):
                return request, _rejudged_result(
                    record, request, self._thresholds, keep_candidate=request_crop_only
                )

            def _stage_callback(stage: str, done: int, stage_total: int) -> None:
                _stage_progress(request.filename, stage, done, stage_total)

            result = analyze_frames(
                source_path=request.path,
                width=request.width,
                height=request.height,
                duration_sec=request.duration_sec,
                work_dir=self._work_dir,
                runner=exec_runner,
                ffmpeg_executable=self._ffmpeg_executable,
                has_vmaf=self._has_vmaf,
                has_vr_metadata=request.has_vr_metadata,
                manual_vr=request.manual_vr,
                mode=self._mode,
                cancel_event=cancel_event,
                inner_workers=inner_workers,
                on_stage=_stage_callback,
                log_prefix=f"{index}_",
                prefer_pyav=not runner_injected,
                crop_only=request_crop_only,
                thresholds=self._thresholds,
            )
            self._store.save(record_from_result(request, result, measured_at=self._now()))
            return request, result

        items = list(enumerate(unique, start=1))
        if self._video_workers <= 1 or total <= 1:
            for completed, item in enumerate(items, start=1):
                request, result = _analyze_one(item)
                results[request.filename] = result
                if on_progress is not None:
                    on_progress(completed, total, request.filename, result)
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("화질 분석이 취소되었습니다")
            return results
        with ThreadPoolExecutor(self._video_workers) as pool:
            futures = [pool.submit(_analyze_one, item) for item in items]
            completed = 0
            try:
                for future in as_completed(futures):
                    request, result = future.result()
                    completed += 1
                    results[request.filename] = result
                    if on_progress is not None:
                        on_progress(completed, total, request.filename, result)
                    if cancel_event is not None and cancel_event.is_set():
                        raise InterruptedError("화질 분석이 취소되었습니다")
            except BaseException:
                for future in futures:
                    future.cancel()
                raise
        return results

    def count_cached(
        self,
        requests: Sequence[FrameAnalysisRequest],
        *,
        force: bool = False,
        crop_only: bool = False,
        crop_only_names: frozenset[str] = frozenset(),
    ) -> int:
        """이번 요청 중 지금 캐시로 재사용할 수 있는 건수"""
        unique = _unique_requests(requests)
        if force or not unique:
            return 0
        cached = self._store.load_many([request.key for request in unique])
        metric_name = resolve_metric_name(self._has_vmaf)
        return sum(
            1
            for request in unique
            if _is_reusable(
                cached.get(request.key),
                request,
                metric_name,
                self._analysis_version,
                crop_only=crop_only or request.filename in crop_only_names,
                thresholds=self._thresholds,
            )
        )

    def load_active_frames(
        self, requests: Sequence[FrameAnalysisRequest], *, crop_enabled: bool = True
    ) -> dict[str, ActiveFrame]:
        """잘라내기 확정 영상만 파일명별 활성 화면으로 반환

        - 설정 끔, 영상별 제외, VR 보호, 검은 여백 없음이면 전체 프레임 유지로 보고 넣지 않음
        """
        records = self._store.load_many([request.key for request in requests])
        excluded = self._store.load_crop_exclusions([request.path for request in requests])
        frames: dict[str, ActiveFrame] = {}
        for request in requests:
            record = records.get(request.key)
            if record is None or not is_usable_analysis_version(
                record.analysis_version, self._analysis_version
            ):
                continue
            frame = resolve_crop_frame(
                apply_no_bars_ratio(
                    record.active_region,
                    request.width,
                    request.height,
                    thresholds=self._thresholds,
                ),
                crop_enabled=crop_enabled,
                excluded=request.path in excluded,
                is_vr_protected=record.is_vr_protected,
            )
            if frame is not None:
                frames[request.filename] = frame
        return frames

    def load_results(self, requests: Sequence[FrameAnalysisRequest]) -> dict[str, FrameAnalysisResult]:
        """현재 작업 폴더의 캐시된 화질 분석 결과를 표시용으로 한 번에 읽음"""
        records = self._store.load_many([request.key for request in requests])
        results: dict[str, FrameAnalysisResult] = {}
        for request in requests:
            record = records.get(request.key)
            if record is None or not is_usable_analysis_version(
                record.analysis_version, self._analysis_version
            ):
                continue
            results[request.filename] = result_from_record(record)
        return results

    def close(self) -> None:
        """캐시 저장소 연결을 닫음"""
        self._store.close()

    def _runner_for(self, cancel_event: threading.Event | None) -> StderrCommandRunner:
        if self._runner is not None:
            return self._runner
        return ProcessStderrRunner(cancel_event=cancel_event, timeout_sec=self._timeout_sec)
