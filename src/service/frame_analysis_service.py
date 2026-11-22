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
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TypeVar

from src.core.analysis.frame_analysis import (
    FRAME_ANALYSIS_VERSION,
    ActiveRegion,
    TierScore,
    tier_scores_from_json,
    tier_scores_to_json,
)
from src.core.analysis.frame_analysis_runner import (
    FrameAnalysisResult,
    ProcessStderrRunner,
    StderrCommandRunner,
    analyze_frames,
)
from src.core.encode.ffmpeg_command import FFMPEG_EXECUTABLE_DEFAULT
from src.core.models import ActiveFrame

logger = logging.getLogger(__name__)

_T = TypeVar("_T")

FRAME_ANALYSIS_TABLE: str = "frame_analysis_cache"
ANALYSIS_TIMEOUT_SEC: float = 1800.0
SQLITE_BUSY_TIMEOUT_SEC: float = 5.0
WRITE_RETRY_ATTEMPTS: int = 5
WRITE_RETRY_BASE_SEC: float = 0.05
_LOOKUP_CHUNK_SIZE: int = 500
_LOCKED_MARKERS: tuple[str, ...] = ("locked", "busy")

FileKey = tuple[str, int, int]

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
)
_RECORD_COLUMNS: tuple[str, ...] = (
    "filename",
    "filesize_bytes",
    "mtime_ns",
    "active_width",
    "active_height",
    "active_x",
    "active_y",
    "has_black_bars",
    "metric_used",
    "vmaf_model",
    "tier_scores_json",
    "candidate_tier",
    "valid_segments",
    "total_segments",
    "unmeasurable",
    "is_vr_protected",
    "measured_at",
    "analysis_version",
)
_SELECT_COLUMNS: str = ", ".join(_RECORD_COLUMNS)
_PLACEHOLDERS: str = ", ".join("?" * len(_RECORD_COLUMNS))


@dataclass(frozen=True, slots=True)
class FrameAnalysisRecord:
    """화질 분석 캐시 한 행(파일 식별 키와 측정 결과)"""

    filename: str
    filesize_bytes: int
    mtime_ns: int
    active_width: int
    active_height: int
    active_x: int
    active_y: int
    has_black_bars: bool
    metric_used: str
    vmaf_model: str
    tier_scores: tuple[TierScore, ...]
    candidate_tier: str | None
    valid_segments: int
    total_segments: int
    unmeasurable: bool
    is_vr_protected: bool
    measured_at: str
    analysis_version: int

    @property
    def key(self) -> FileKey:
        """파일 식별 키(이름, 크기, 수정 시각) 반환"""
        return (self.filename, self.filesize_bytes, self.mtime_ns)

    @property
    def active_region(self) -> ActiveRegion:
        """저장된 값으로 복원한 활성 화면 영역"""
        return ActiveRegion(
            width=self.active_width,
            height=self.active_height,
            x=self.active_x,
            y=self.active_y,
            has_black_bars=self.has_black_bars,
        )


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_locked_error(error: sqlite3.OperationalError) -> bool:
    text = str(error).lower()
    return any(marker in text for marker in _LOCKED_MARKERS)


def _row_to_record(row: sqlite3.Row | tuple[object, ...]) -> FrameAnalysisRecord:
    values = dict(zip(_RECORD_COLUMNS, row, strict=True))
    candidate = values["candidate_tier"]
    return FrameAnalysisRecord(
        filename=str(values["filename"]),
        filesize_bytes=int(values["filesize_bytes"]),
        mtime_ns=int(values["mtime_ns"]),
        active_width=int(values["active_width"]),
        active_height=int(values["active_height"]),
        active_x=int(values["active_x"]),
        active_y=int(values["active_y"]),
        has_black_bars=bool(values["has_black_bars"]),
        metric_used=str(values["metric_used"]),
        vmaf_model=str(values["vmaf_model"]),
        tier_scores=tier_scores_from_json(str(values["tier_scores_json"])),
        candidate_tier=None if candidate is None else str(candidate),
        valid_segments=int(values["valid_segments"]),
        total_segments=int(values["total_segments"]),
        unmeasurable=bool(values["unmeasurable"]),
        is_vr_protected=bool(values["is_vr_protected"]),
        measured_at=str(values["measured_at"]),
        analysis_version=int(values["analysis_version"]),
    )


def _record_to_row(record: FrameAnalysisRecord) -> tuple[object, ...]:
    return (
        record.filename,
        record.filesize_bytes,
        record.mtime_ns,
        record.active_width,
        record.active_height,
        record.active_x,
        record.active_y,
        int(record.has_black_bars),
        record.metric_used,
        record.vmaf_model,
        tier_scores_to_json(record.tier_scores),
        record.candidate_tier,
        record.valid_segments,
        record.total_segments,
        int(record.unmeasurable),
        int(record.is_vr_protected),
        record.measured_at,
        record.analysis_version,
    )


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
                    record = _row_to_record(row)
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
                    _record_to_row(record),
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


@dataclass(frozen=True, slots=True)
class FrameAnalysisRequest:
    """화질 분석 대상 한 개의 파일 식별 값과 측정 입력"""

    filename: str
    path: str
    width: int
    height: int
    duration_sec: float
    filesize_bytes: int
    mtime_ns: int
    has_vr_metadata: bool = False
    manual_vr: bool = False

    @property
    def key(self) -> FileKey:
        """파일 식별 키(이름, 크기, 수정 시각) 반환"""
        return (self.filename, self.filesize_bytes, self.mtime_ns)


ProgressCallback = Callable[[int, int, str, FrameAnalysisResult], None]


def record_from_result(
    request: FrameAnalysisRequest,
    result: FrameAnalysisResult,
    *,
    measured_at: str,
) -> FrameAnalysisRecord:
    """분석 결과를 캐시 행으로 변환"""
    region = result.active_region
    return FrameAnalysisRecord(
        filename=request.filename,
        filesize_bytes=request.filesize_bytes,
        mtime_ns=request.mtime_ns,
        active_width=region.width,
        active_height=region.height,
        active_x=region.x,
        active_y=region.y,
        has_black_bars=region.has_black_bars,
        metric_used=result.metric_used,
        vmaf_model=result.vmaf_model,
        tier_scores=result.tier_scores,
        candidate_tier=result.candidate_tier,
        valid_segments=result.valid_segment_count,
        total_segments=result.total_segment_count,
        unmeasurable=result.unmeasurable,
        is_vr_protected=result.is_vr_protected,
        measured_at=measured_at,
        analysis_version=result.analysis_version,
    )


def result_from_record(record: FrameAnalysisRecord) -> FrameAnalysisResult:
    """캐시 행을 분석 결과로 복원"""
    return FrameAnalysisResult(
        active_region=record.active_region,
        metric_used=record.metric_used,
        vmaf_model=record.vmaf_model,
        tier_scores=record.tier_scores,
        candidate_tier=record.candidate_tier,
        valid_segment_count=record.valid_segments,
        total_segment_count=record.total_segments,
        unmeasurable=record.unmeasurable,
        is_vr_protected=record.is_vr_protected,
        analysis_version=record.analysis_version,
    )


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
    ) -> None:
        self._store: FrameAnalysisStore = store
        self._work_dir: str = work_dir
        self._ffmpeg_executable: str = ffmpeg_executable
        self._has_vmaf: bool = has_vmaf
        self._runner: StderrCommandRunner | None = runner
        self._timeout_sec: float = timeout_sec
        self._now: Callable[[], str] = now

    def analyze_batch(
        self,
        requests: Sequence[FrameAnalysisRequest],
        *,
        force: bool = False,
        cancel_event: threading.Event | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> dict[str, FrameAnalysisResult]:
        """요청 목록을 분석하고 파일명별 결과를 반환. 취소되면 `InterruptedError`"""
        unique = list({request.key: request for request in requests}.values())
        total = len(unique)
        cached = {} if force else self._store.load_many([request.key for request in unique])
        results: dict[str, FrameAnalysisResult] = {}
        for completed, request in enumerate(unique, start=1):
            if cancel_event is not None and cancel_event.is_set():
                raise InterruptedError("화질 분석이 취소되었습니다")
            record = cached.get(request.key)
            if record is not None and record.analysis_version == FRAME_ANALYSIS_VERSION:
                result = result_from_record(record)
            else:
                result = analyze_frames(
                    source_path=request.path,
                    width=request.width,
                    height=request.height,
                    duration_sec=request.duration_sec,
                    work_dir=self._work_dir,
                    runner=self._runner_for(cancel_event),
                    ffmpeg_executable=self._ffmpeg_executable,
                    has_vmaf=self._has_vmaf,
                    has_vr_metadata=request.has_vr_metadata,
                    manual_vr=request.manual_vr,
                    cancel_event=cancel_event,
                )
                self._store.save(record_from_result(request, result, measured_at=self._now()))
            results[request.filename] = result
            if on_progress is not None:
                on_progress(completed, total, request.filename, result)
        return results

    def load_active_frames(self, requests: Sequence[FrameAnalysisRequest]) -> dict[str, ActiveFrame]:
        """캐시에 있는 측정 결과로 파일명별 활성 화면 크기를 반환"""
        records = self._store.load_many([request.key for request in requests])
        frames: dict[str, ActiveFrame] = {}
        for request in requests:
            record = records.get(request.key)
            if record is None or record.analysis_version != FRAME_ANALYSIS_VERSION:
                continue
            frames[request.filename] = record.active_region.to_active_frame()
        return frames

    def close(self) -> None:
        """캐시 저장소 연결을 닫음"""
        self._store.close()

    def _runner_for(self, cancel_event: threading.Event | None) -> StderrCommandRunner:
        if self._runner is not None:
            return self._runner
        return ProcessStderrRunner(cancel_event=cancel_event, timeout_sec=self._timeout_sec)
