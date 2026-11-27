"""정밀 결정 결과 캐시: 표 구성, 키, 행 변환, 저장소

- 표는 `video_cache.sqlite3`의 `sample_tuner_cache`이며 원본 내용 키(크기, 지문), 인코더, 기준 품질값,
  계획 해시, 목표 기준, 판 번호로 한 행을 식별
- 이름이나 폴더가 바뀌어도 같은 내용이면 적중하도록 지문을 쓰고, 지문을 읽지 못하면 캐시를 건너뜀
- 이전 판의 경로 키 표는 판 번호가 달라 어차피 쓰지 않으므로 내용 키 구성으로 다시 만듬
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.core.analysis.file_fingerprint import compute_fingerprint
from src.core.evaluation.sample_tuner import (
    SAMPLE_TUNER_VERSION,
    QualityProbe,
    TuneResult,
)

logger = logging.getLogger(__name__)

SAMPLE_TUNER_TABLE_NAME: str = "sample_tuner_cache"
SQLITE_BUSY_TIMEOUT_SEC: float = 5.0
CACHE_COLUMNS: tuple[str, ...] = (
    "filesize_bytes",
    "fingerprint",
    "encoder",
    "baseline_quality",
    "plan_hash",
    "target_key",
    "tuner_version",
    "best_quality",
    "converged",
    "reason",
    "probes_json",
    "measured_at",
)
# - 캐시 키 열: 원본 내용 키(크기, 지문), 인코더, 기준 품질값, 크롭/축소 계획, 목표 기준, 판 번호
KEY_COLUMNS: tuple[str, ...] = (
    "filesize_bytes",
    "fingerprint",
    "encoder",
    "baseline_quality",
    "plan_hash",
    "target_key",
    "tuner_version",
)
_CACHE_TABLE_SQL: str = (
    f"CREATE TABLE IF NOT EXISTS {SAMPLE_TUNER_TABLE_NAME} (\n"
    "  filesize_bytes INTEGER NOT NULL,\n"
    "  fingerprint TEXT NOT NULL,\n"
    "  encoder TEXT NOT NULL,\n"
    "  baseline_quality REAL NOT NULL,\n"
    "  plan_hash TEXT NOT NULL,\n"
    "  target_key TEXT NOT NULL,\n"
    "  tuner_version INTEGER NOT NULL,\n"
    "  best_quality REAL NOT NULL,\n"
    "  converged INTEGER NOT NULL,\n"
    "  reason TEXT NOT NULL,\n"
    "  probes_json TEXT NOT NULL,\n"
    "  measured_at TEXT NOT NULL,\n"
    "  PRIMARY KEY (filesize_bytes, fingerprint, encoder,\n"
    "    baseline_quality, plan_hash, target_key, tuner_version)\n"
    ")"
)
_DDL_RETRY_ATTEMPTS: int = 2


def utc_now_iso() -> str:
    """측정 시각을 UTC ISO 문자열로 반환"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True, slots=True)
class TuneJob:
    """정밀 결정 한 영상의 입력: 원본 식별 값, 인코더, 기준 품질값, 지표 기준 화소 크기

    - source_fingerprint는 내용 지문이며, 비어 있으면 캐시 조회 때 파일에서 계산함
    """

    source_path: str
    source_size: int
    source_mtime_ns: int
    source_duration_sec: float
    encoder: str
    baseline_quality: float
    width: int
    height: int
    source_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class TuneCacheKey:
    """캐시 키: 원본 내용 키(크기, 지문), 인코더, 기준 품질값, 크롭/축소 계획, 목표 기준, 판 번호"""

    filesize_bytes: int
    fingerprint: str
    encoder: str
    baseline_quality: float
    plan_hash: str
    target_key: str
    tuner_version: int = SAMPLE_TUNER_VERSION

    @property
    def values(self) -> tuple[object, ...]:
        """SQL 키 열 순서의 값 튜플"""
        return (
            self.filesize_bytes,
            self.fingerprint,
            self.encoder,
            self.baseline_quality,
            self.plan_hash,
            self.target_key,
            self.tuner_version,
        )


@dataclass(frozen=True, slots=True)
class SampleTuneRecord:
    """캐시 한 행: 키와 탐색 결과(고른 품질값, 프로브, 수렴 여부, 사유)"""

    key: TuneCacheKey
    best_quality: float
    converged: bool
    reason: str
    probes: tuple[QualityProbe, ...]
    measured_at: str

    @property
    def result(self) -> TuneResult:
        """저장 값을 탐색 결과로 복원"""
        return TuneResult(
            best_quality=self.best_quality,
            probes=self.probes,
            converged=self.converged,
            reason=self.reason,
        )


def probes_to_json(probes: Sequence[QualityProbe]) -> str:
    """프로브 목록을 캐시 저장용 JSON으로 직렬화"""
    return json.dumps(
        [
            {
                "quality": probe.quality,
                "vmaf": probe.vmaf,
                "ssim": probe.ssim,
                "bitrate_kbps": probe.bitrate_kbps,
            }
            for probe in probes
        ],
        ensure_ascii=False,
    )


def probes_from_json(text: str) -> tuple[QualityProbe, ...]:
    """캐시 JSON을 프로브 목록으로 복원. 형식이 다르면 빈 튜플"""
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return ()
    if not isinstance(data, list):
        return ()
    probes: list[QualityProbe] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        quality = item.get("quality")
        if not isinstance(quality, (int, float)):
            continue
        vmaf = item.get("vmaf")
        ssim = item.get("ssim")
        bitrate = item.get("bitrate_kbps")
        probes.append(
            QualityProbe(
                quality=float(quality),
                vmaf=float(vmaf) if isinstance(vmaf, (int, float)) else None,
                ssim=float(ssim) if isinstance(ssim, (int, float)) else None,
                bitrate_kbps=float(bitrate) if isinstance(bitrate, (int, float)) else 0.0,
            )
        )
    return tuple(probes)


def source_fingerprint(job: TuneJob) -> str | None:
    """정밀 결정 대상의 내용 지문. 이미 있으면 그 값을, 없으면 파일을 읽어 계산

    - 읽지 못하면 None이며 호출자는 캐시를 건너뛰고 탐색만 실행
    """
    if job.source_fingerprint:
        return job.source_fingerprint
    try:
        return compute_fingerprint(job.source_path, job.source_size)
    except OSError:
        return None


def tune_cache_key(job: TuneJob, *, baseline_quality: float, plan_hash: str, target_key: str) -> TuneCacheKey | None:
    """정밀 결정 캐시 키. 내용 지문을 구하지 못하면 None"""
    fingerprint = source_fingerprint(job)
    if fingerprint is None:
        return None
    return TuneCacheKey(
        filesize_bytes=job.source_size,
        fingerprint=fingerprint,
        encoder=job.encoder,
        baseline_quality=baseline_quality,
        plan_hash=plan_hash,
        target_key=target_key,
    )


def _cache_table_is_current(conn: sqlite3.Connection) -> bool:
    """표 열 구성이 현재와 같은지"""
    columns = [str(row[1]) for row in conn.execute(f"PRAGMA table_info({SAMPLE_TUNER_TABLE_NAME})")]
    return columns == list(CACHE_COLUMNS)


def _ensure_cache_table(conn: sqlite3.Connection) -> None:
    """정밀 결정 캐시 표를 현재 구성으로 맞춤

    - 표가 없으면 만들고, 이전 판의 경로 키 표면 내용 키 구성으로 다시 만듬(옛 행은 판이 달라 버림)
    - 표를 지우고 만드는 DDL을 한 BEGIN IMMEDIATE 안에서 끝내, 다른 스레드가 중간 상태(표 없음)를
      보지 않게 함. 그래도 동시 연결과 부딪히면 호출자가 한 번 더 시도함
    """
    if _cache_table_is_current(conn):
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(f"DROP TABLE IF EXISTS {SAMPLE_TUNER_TABLE_NAME}")
        conn.execute(_CACHE_TABLE_SQL)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _as_int(value: object) -> int:
    """저장 값을 정수로 읽음. 숫자가 아니면 0"""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return 0
    return 0


def _as_float(value: object) -> float:
    """저장 값을 실수로 읽음. 숫자가 아니면 0.0"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _as_str(value: object) -> str:
    """저장 값을 문자열로 읽음. None이면 빈 문자열"""
    return "" if value is None else str(value)


def row_to_record(row: Sequence[object]) -> SampleTuneRecord:
    """SQLite 행 값을 캐시 레코드로 변환. 형식이 다른 값은 기본값으로 둠"""
    values = dict(zip(CACHE_COLUMNS, row, strict=True))
    key = TuneCacheKey(
        filesize_bytes=_as_int(values["filesize_bytes"]),
        fingerprint=_as_str(values["fingerprint"]),
        encoder=_as_str(values["encoder"]),
        baseline_quality=_as_float(values["baseline_quality"]),
        plan_hash=_as_str(values["plan_hash"]),
        target_key=_as_str(values["target_key"]),
        tuner_version=_as_int(values["tuner_version"]),
    )
    return SampleTuneRecord(
        key=key,
        best_quality=_as_float(values["best_quality"]),
        converged=bool(_as_int(values["converged"])),
        reason=_as_str(values["reason"]),
        probes=probes_from_json(_as_str(values["probes_json"])),
        measured_at=_as_str(values["measured_at"]),
    )


def record_to_row(record: SampleTuneRecord) -> tuple[object, ...]:
    """캐시 레코드를 SQLite 저장 값으로 변환. 열 순서는 `CACHE_COLUMNS`와 같음"""
    return (
        *record.key.values,
        record.best_quality,
        int(record.converged),
        record.reason,
        probes_to_json(record.probes),
        record.measured_at,
    )


def record_from_result(key: TuneCacheKey, result: TuneResult, *, measured_at: str | None = None) -> SampleTuneRecord:
    """탐색 결과를 캐시 레코드로 변환"""
    return SampleTuneRecord(
        key=key,
        best_quality=result.best_quality,
        converged=result.converged,
        reason=result.reason,
        probes=result.probes,
        measured_at=measured_at if measured_at is not None else utc_now_iso(),
    )


class SampleTunerCache:
    """품질값 정밀 결정 결과 SQLite 저장소

    - 화질 분석 캐시와 같은 DB 파일에 표만 덧붙여 옛 DB에서도 동작
    - 기준 품질값, 계획 해시, 목표 기준, 판 번호가 모두 같을 때만 재사용
    - 작업마다 연결을 열고 닫아 SQLite 동시 쓰기 잠금 교착을 피함
    """

    def __init__(self, db_path: str, *, sleep: Callable[[float], None] = time.sleep) -> None:
        self._db_path: str = db_path
        self._sleep: Callable[[float], None] = sleep
        self._disabled: bool = False

    @property
    def db_path(self) -> str:
        """저장소 DB 파일 경로 반환"""
        return self._db_path

    def _connect(self) -> sqlite3.Connection | None:
        """표를 준비한 연결을 열어 반환. DB를 열 수 없으면 None(캐시 없이 계속 진행)

        - 이전 판의 표(경로, 수정 시각 키)는 지우고 내용 키 구성으로 다시 만듬. 옛 행은 판 번호가
          달라 어차피 쓰지 않으므로 자연 만료로 봄
        - 표를 만들다 다른 스레드의 동시 DDL과 부딪히면 한 번 더 시도하고, 그래도 실패하면 이번만
          캐시를 건너뜀. 일시적 충돌로 캐시를 영구히 끄지 않음
        """
        if self._disabled:
            return None
        try:
            parent = os.path.dirname(self._db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(self._db_path, timeout=SQLITE_BUSY_TIMEOUT_SEC)
        except sqlite3.Error:
            self._disabled = True
            return None
        try:
            self._prepare_table(conn)
            conn.commit()
        except sqlite3.Error as error:
            conn.close()
            logger.warning("정밀 결정 캐시 표를 준비하지 못해 이번만 캐시를 건너뜁니다: %s", error)
            return None
        return conn

    def _prepare_table(self, conn: sqlite3.Connection) -> None:
        """캐시 표를 현재 구성으로 맞춤. 동시 DDL 충돌이면 한 번 다시 시도"""
        for attempt in range(_DDL_RETRY_ATTEMPTS):
            try:
                _ensure_cache_table(conn)
                return
            except sqlite3.Error:
                if attempt + 1 >= _DDL_RETRY_ATTEMPTS:
                    raise

    def load(self, key: TuneCacheKey) -> SampleTuneRecord | None:
        """키가 같은 캐시 행 반환. 없거나 읽을 수 없으면 None"""
        conn = self._connect()
        if conn is None:
            return None
        condition = " AND ".join(f"{name} = ?" for name in KEY_COLUMNS)
        try:
            row = conn.execute(
                f"SELECT {', '.join(CACHE_COLUMNS)} FROM {SAMPLE_TUNER_TABLE_NAME} WHERE {condition} LIMIT 1",
                list(key.values),
            ).fetchone()
        except sqlite3.Error:
            return None
        finally:
            conn.close()
        return None if row is None else row_to_record(row)

    def save(self, record: SampleTuneRecord) -> None:
        """탐색 결과를 캐시에 기록(같은 키는 교체)"""
        conn = self._connect()
        if conn is None:
            return
        placeholders = ", ".join("?" * len(CACHE_COLUMNS))
        try:
            with conn:
                conn.execute(
                    f"INSERT OR REPLACE INTO {SAMPLE_TUNER_TABLE_NAME} "
                    f"({', '.join(CACHE_COLUMNS)}) VALUES ({placeholders})",
                    list(record_to_row(record)),
                )
        except sqlite3.Error:
            pass
        finally:
            conn.close()

    def count(self) -> int:
        """저장된 캐시 행 수. 읽을 수 없으면 0"""
        conn = self._connect()
        if conn is None:
            return 0
        try:
            return _as_int(conn.execute(f"SELECT COUNT(*) FROM {SAMPLE_TUNER_TABLE_NAME}").fetchone()[0])
        except sqlite3.Error:
            return 0
        finally:
            conn.close()

    def clear(self) -> int:
        """캐시 행을 모두 지우고 지운 수를 반환"""
        conn = self._connect()
        if conn is None:
            return 0
        try:
            with conn:
                deleted = conn.execute(f"DELETE FROM {SAMPLE_TUNER_TABLE_NAME}").rowcount
            return int(deleted)
        except sqlite3.Error:
            return 0
        finally:
            conn.close()
