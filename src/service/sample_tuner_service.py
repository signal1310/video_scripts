"""품질값 정밀 결정 실행 서비스: 대표 구간 샘플 인코딩, VMAF/SSIM 측정, 결과 캐시(결정 18)

- 판정은 `src/core/evaluation/sample_tuner.py`의 순수 이진 탐색이 맡고 이 모듈은 프로세스만 다룸
- 샘플 인코딩은 공용 엔진(`run_sample_pass`)과 본 인코딩 인자 빌더를 그대로 재사용
- 지표 명령은 `quality_probe`의 필터 구성과 로그 파서를 재사용하고 별도 파서를 두지 않음
- 취소는 `threading.Event`로 전달하며 실행기와 샘플 엔진이 자식 프로세스를 kill함
"""

import hashlib
import json
import os
import sqlite3
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.core.analysis.frame_analysis_runner import ProcessStderrRunner
from src.core.encode.encode_plan import EncodePlan
from src.core.encode.ffmpeg_command import FFMPEG_EXECUTABLE_DEFAULT, build_sample_ffmpeg_args
from src.core.encode.quality_tuning import sample_segments
from src.core.encoder_specs import get_encoder_spec
from src.core.evaluation.sample_tuner import (
    REASON_UNMEASURABLE,
    SAMPLE_TUNER_VERSION,
    TUNE_MAX_PROBES,
    DownscaleComparison,
    QualityProbe,
    TuneResult,
    TuneTarget,
    compare_downscale,
    search_quality,
    tune_bounds,
)
from src.core.paths import get_data_dir
from src.core.quality_probe import (
    LOG_NAME_SSIM,
    LOG_NAME_VMAF,
    METRIC_SSIM,
    METRIC_VMAF,
    VMAF_MODEL_DEFAULT,
    build_metric_filter,
    parse_ssim_stats,
    parse_vmaf_log,
    vmaf_model_for,
)
from src.core.sample_points import SegmentQuality, is_invalid_segment
from src.service.analysis_store import ANALYSIS_DB_FILENAME
from src.service.encode.encode_ports import ProcessFactory, default_process_factory
from src.service.encode.encode_sample_engine import run_sample_pass

SAMPLE_TUNER_TABLE_NAME: str = "sample_tuner_cache"
SAMPLE_TUNER_TEMP_PREFIX: str = "d18_tuner_"
# - 결정 18: 대표 구간 2~3개(공통 5지점에서 균등 선택)로 프로브당 비용을 묶음
TUNABLE_SEGMENT_COUNT: int = 3
BITS_PER_BYTE: int = 8
BITS_PER_KBIT: int = 1000
PLAN_HASH_HEX_DIGITS: int = 16
SQLITE_BUSY_TIMEOUT_SEC: float = 5.0
CACHE_COLUMNS: tuple[str, ...] = (
    "source_path",
    "filesize_bytes",
    "mtime_ns",
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
KEY_COLUMN_COUNT: int = 8
_CACHE_TABLE_SQL: str = (
    f"CREATE TABLE IF NOT EXISTS {SAMPLE_TUNER_TABLE_NAME} (\n"
    "  source_path TEXT NOT NULL,\n"
    "  filesize_bytes INTEGER NOT NULL,\n"
    "  mtime_ns INTEGER NOT NULL,\n"
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
    "  PRIMARY KEY (source_path, filesize_bytes, mtime_ns, encoder,\n"
    "    baseline_quality, plan_hash, target_key, tuner_version)\n"
    ")"
)


def _utc_now_iso() -> str:
    """측정 시각을 UTC ISO 문자열로 반환"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass(frozen=True, slots=True)
class TuneJob:
    """정밀 결정 한 영상의 입력: 원본 식별 값, 인코더, 기준 품질값, 측정 기준 크기"""

    source_path: str
    source_size: int
    source_mtime_ns: int
    source_duration_sec: float
    encoder: str
    baseline_quality: float
    width: int
    height: int


@dataclass(frozen=True, slots=True)
class TuneCacheKey:
    """캐시 키: 원본 식별 값, 인코더, 기준 품질값, 크롭/축소 계획, 목표 기준, 판 번호"""

    source_path: str
    filesize_bytes: int
    mtime_ns: int
    encoder: str
    baseline_quality: float
    plan_hash: str
    target_key: str
    tuner_version: int = SAMPLE_TUNER_VERSION

    @property
    def values(self) -> tuple[object, ...]:
        """SQL 키 열 순서의 값 튜플"""
        return (
            self.source_path,
            self.filesize_bytes,
            self.mtime_ns,
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


def plan_signature(plan: EncodePlan) -> str:
    """크롭/축소/비트레이트 상한 계획을 캐시 키용 짧은 해시로 변환"""
    parts = "|".join(
        (
            plan.crop_filter or "",
            plan.scale_filter or "",
            "" if plan.output_size is None else f"{plan.output_size[0]}x{plan.output_size[1]}",
            "" if plan.maxrate_kbps is None else str(plan.maxrate_kbps),
            plan.pixel_format,
        )
    )
    return hashlib.sha256(parts.encode("utf-8")).hexdigest()[:PLAN_HASH_HEX_DIGITS]


def tuning_segments(
    duration_sec: float,
    black_ratios: Sequence[float] | None = None,
) -> tuple[tuple[float, float], ...]:
    """공통 구간 규칙의 후보에서 대표 2~3구간을 고르고 검은 구간을 뺌

    - 20초 미만은 전체 1구간(공통 규칙), 그 밖은 10~90% 안 5지점에서 균등하게 3지점 선택
    - 검은 비율은 화질 분석 결과를 받아 쓰고, 모두 걸러지면 첫 구간을 남겨 측정 불가를 피함
    """
    segments = sample_segments(duration_sec)
    if len(segments) > TUNABLE_SEGMENT_COUNT:
        step = (len(segments) - 1) / (TUNABLE_SEGMENT_COUNT - 1)
        segments = tuple(segments[round(index * step)] for index in range(TUNABLE_SEGMENT_COUNT))
    if black_ratios is None:
        return segments
    kept: list[tuple[float, float]] = []
    for index, segment in enumerate(segments):
        ratio = black_ratios[index] if index < len(black_ratios) else 0.0
        sample = SegmentQuality(start_sec=segment[0], duration_sec=segment[1], black_ratio=ratio)
        if not is_invalid_segment(sample):
            kept.append(segment)
    return tuple(kept) if kept else segments[:1]


def probe_bitrate_kbps(total_bytes: int, sample_duration_sec: float) -> float:
    """샘플 바이트 합계를 샘플 길이로 나눈 비트레이트(kbps). 길이를 모르면 0"""
    if total_bytes <= 0 or sample_duration_sec <= 0:
        return 0.0
    return total_bytes * BITS_PER_BYTE / BITS_PER_KBIT / sample_duration_sec


def build_segment_metric_command(
    *,
    ffmpeg_executable: str,
    distorted_path: str,
    reference_path: str,
    start_sec: float,
    duration_sec: float,
    metric: str,
    model: str,
    log_name: str,
) -> list[str]:
    """샘플 구간 결과와 원본 같은 구간의 지표를 재는 FFmpeg 인자 목록

    - 기준 입력에만 시크와 길이를 붙여 원본 전체를 읽지 않음
    - 필터 문자열은 `quality_probe.build_metric_filter`를 그대로 씀
    """
    return [
        ffmpeg_executable,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        distorted_path,
        "-ss",
        f"{start_sec:.3f}",
        "-t",
        f"{duration_sec:.3f}",
        "-i",
        reference_path,
        "-lavfi",
        build_metric_filter(metric, model=model, log_name=log_name),
        "-f",
        "null",
        "-",
    ]


def _median(values: Sequence[float]) -> float | None:
    """측정값 목록의 중앙값. 값이 없으면 None"""
    ordered = sorted(values)
    if not ordered:
        return None
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2.0


def _measure_segment_metric(
    *,
    runner: Callable[[list[str], str], tuple[int, str]],
    command: list[str],
    work_dir: str,
    metric: str,
    log_name: str,
) -> float | None:
    """한 구간의 지표를 재고 로그를 파싱. 명령 실패나 로그 부재는 None

    - 파싱은 `quality_probe`의 로그 파서를 그대로 씀
    """
    log_path = os.path.join(work_dir, log_name)
    if os.path.exists(log_path):
        os.remove(log_path)
    if runner(command, work_dir)[0] != 0 or not os.path.exists(log_path):
        return None
    with open(log_path, encoding="utf-8", errors="replace") as handle:
        text = handle.read()
    if metric == METRIC_VMAF:
        return parse_vmaf_log(text)
    stats = parse_ssim_stats(text)
    return None if stats is None else stats[0]


def _measure_probe(
    *,
    plan: EncodePlan,
    segments: Sequence[tuple[float, float]],
    quality: float,
    ffmpeg_exec: str,
    process_factory: ProcessFactory,
    metric_runner: Callable[[list[str], str], tuple[int, str]],
    has_vmaf: bool,
    cancel_event: threading.Event | None,
) -> QualityProbe:
    """품질값 하나의 표본을 만듭니다: 구간별 샘플 인코딩 뒤 VMAF(없으면 SSIM) 중앙값

    - 산출물은 임시 폴더에만 쓰고 지표 측정이 끝나면 폴더째 지움
    - 취소되면 점수 없는 프로브를 돌려주고 다음 탐색이 멈추게 함
    - VMAF 측정이 모두 실패하면 같은 산출물로 SSIM을 재서 대체(결정 18의 지표 우선순위)
    """
    if not segments or quality <= 0:
        return QualityProbe(quality=quality)
    model = vmaf_model_for(plan.output_size[0], plan.output_size[1]) if plan.output_size else VMAF_MODEL_DEFAULT
    with tempfile.TemporaryDirectory(prefix=SAMPLE_TUNER_TEMP_PREFIX) as tmp_dir:
        outcome = run_sample_pass(
            batch_id=0,
            source_path=plan.source_path,
            plan=plan,
            ffmpeg_exec=ffmpeg_exec,
            process_factory=process_factory,
            abort=cancel_event if cancel_event is not None else threading.Event(),
            segments=tuple(segments),
            quality=quality,
            build_args=build_sample_ffmpeg_args,
            temp_prefix=SAMPLE_TUNER_TEMP_PREFIX,
            output_dir=tmp_dir,
        )
        if outcome.cancelled or outcome.succeeded_segments < outcome.segment_count:
            return QualityProbe(quality=quality)
        sample_duration = sum(segment[1] for segment in segments)
        vmaf_scores = (
            _metric_scores(
                segments=segments,
                tmp_dir=tmp_dir,
                source_path=plan.source_path,
                ffmpeg_exec=ffmpeg_exec,
                metric_runner=metric_runner,
                metric=METRIC_VMAF,
                model=model,
                cancel_event=cancel_event,
            )
            if has_vmaf
            else []
        )
        if cancel_event is not None and cancel_event.is_set():
            return QualityProbe(quality=quality)
        ssim_scores = (
            []
            if vmaf_scores
            else _metric_scores(
                segments=segments,
                tmp_dir=tmp_dir,
                source_path=plan.source_path,
                ffmpeg_exec=ffmpeg_exec,
                metric_runner=metric_runner,
                metric=METRIC_SSIM,
                model=model,
                cancel_event=cancel_event,
            )
        )
        return QualityProbe(
            quality=quality,
            vmaf=_median(vmaf_scores),
            ssim=_median(ssim_scores),
            bitrate_kbps=probe_bitrate_kbps(outcome.total_bytes, sample_duration),
        )


def _metric_scores(
    *,
    segments: Sequence[tuple[float, float]],
    tmp_dir: str,
    source_path: str,
    ffmpeg_exec: str,
    metric_runner: Callable[[list[str], str], tuple[int, str]],
    metric: str,
    model: str,
    cancel_event: threading.Event | None,
) -> list[float]:
    """구간별 지표 점수 목록. 취소되면 그때까지의 점수만 돌려줌"""
    log_name = LOG_NAME_VMAF if metric == METRIC_VMAF else LOG_NAME_SSIM
    scores: list[float] = []
    for index, (start_sec, duration_sec) in enumerate(segments):
        if cancel_event is not None and cancel_event.is_set():
            return scores
        score = _measure_segment_metric(
            runner=metric_runner,
            command=build_segment_metric_command(
                ffmpeg_executable=ffmpeg_exec,
                distorted_path=os.path.join(tmp_dir, f"tune_{index}.mp4"),
                reference_path=source_path,
                start_sec=start_sec,
                duration_sec=duration_sec,
                metric=metric,
                model=model,
                log_name=log_name,
            ),
            work_dir=tmp_dir,
            metric=metric,
            log_name=log_name,
        )
        if score is not None:
            scores.append(score)
    return scores


def tune_quality_for_video(
    *,
    job: TuneJob,
    plan: EncodePlan,
    ffmpeg_exec: str = FFMPEG_EXECUTABLE_DEFAULT,
    process_factory: ProcessFactory | None = None,
    cancel_event: threading.Event | None = None,
    target: TuneTarget | None = None,
    has_vmaf: bool = True,
    black_ratios: Sequence[float] | None = None,
    max_probes: int = TUNE_MAX_PROBES,
    delta_max: float | None = None,
    metric_runner: Callable[[list[str], str], tuple[int, str]] | None = None,
) -> TuneResult:
    """영상 한 개의 품질값 정밀 결정을 실제 샘플 인코딩과 지표 측정으로 수행

    - 구간 선택은 결정 19 공통 규칙(10~90%, 20초 미만 전체, 검은 구간 제외)을 따름
    - 취소되면 그때까지 통과한 최고값을 담고 `converged=False`로 돌려줌
    - `delta_max`가 없으면 저화질 보호 기본 한도(`PROTECT_CQ_DELTA_MAX`)를 씀
    - `metric_runner`를 주면 지표 실행기를 바꿔 끼움(단위 테스트가 FFmpeg 없이 검증)
    """
    resolved_target = target if target is not None else TuneTarget()
    spec = get_encoder_spec(job.encoder)
    integer_scale = spec is not None and spec.integer_scale
    baseline = job.baseline_quality
    low, high = tune_bounds(baseline, **({} if delta_max is None else {"delta_max": delta_max}))
    segments = tuning_segments(job.source_duration_sec, black_ratios)
    factory = process_factory if process_factory is not None else default_process_factory
    runner = metric_runner if metric_runner is not None else _metric_runner(cancel_event)

    def _measure(quality: float) -> QualityProbe:
        """품질값 하나를 실제로 재서 프로브로 돌려줌. 취소면 점수 없음"""
        if cancel_event is not None and cancel_event.is_set():
            return QualityProbe(quality=quality)
        return _measure_probe(
            plan=plan,
            segments=segments,
            quality=quality,
            ffmpeg_exec=ffmpeg_exec,
            process_factory=factory,
            metric_runner=runner,
            has_vmaf=has_vmaf,
            cancel_event=cancel_event,
        )

    return search_quality(
        _measure,
        low,
        high,
        resolved_target,
        max_probes=max_probes,
        integer_scale=integer_scale,
    )


def _metric_runner(cancel_event: threading.Event | None) -> Callable[[list[str], str], tuple[int, str]]:
    """지표 명령 실행기. 취소 신호를 넘겨 실행 중인 FFmpeg를 kill하게 함"""
    return ProcessStderrRunner(cancel_event=cancel_event)


def _as_int(value: object) -> int:
    """SQLite 값의 정수 변환. 형식이 다르면 0"""
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(float(value))
        except ValueError:
            return 0
    return 0


def _as_float(value: object) -> float:
    """SQLite 값의 실수 변환. 형식이 다르면 0.0"""
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return 0.0
    return 0.0


def _as_str(value: object) -> str:
    """SQLite 값의 문자열 변환. NULL은 빈 문자열"""
    return "" if value is None else str(value)


def compare_downscale_samples(
    *,
    job: TuneJob,
    original_plan: EncodePlan,
    candidate_plan: EncodePlan,
    baseline_quality: float,
    protect_quality: float,
    ffmpeg_exec: str = FFMPEG_EXECUTABLE_DEFAULT,
    process_factory: ProcessFactory | None = None,
    cancel_event: threading.Event | None = None,
    target: TuneTarget | None = None,
    has_vmaf: bool = True,
    black_ratios: Sequence[float] | None = None,
    metric_runner: Callable[[list[str], str], tuple[int, str]] | None = None,
) -> DownscaleComparison:
    """[원본(크롭) 해상도 + 보호 품질값]과 [축소 해상도 + 기준 품질값]을 같은 구간 샘플로 비교

    - 두 계획은 원본 경로가 같아야 하고, 비트레이트는 각 계획의 샘플 산출물 실측값으로 비교
    - 판정은 순수 함수 `compare_downscale`이 맡음
    """
    factory = process_factory if process_factory is not None else default_process_factory
    runner = metric_runner if metric_runner is not None else _metric_runner(cancel_event)
    segments = tuning_segments(job.source_duration_sec, black_ratios)
    original_probe = _measure_probe(
        plan=original_plan,
        segments=segments,
        quality=protect_quality,
        ffmpeg_exec=ffmpeg_exec,
        process_factory=factory,
        metric_runner=runner,
        has_vmaf=has_vmaf,
        cancel_event=cancel_event,
    )
    candidate_probe = _measure_probe(
        plan=candidate_plan,
        segments=segments,
        quality=baseline_quality,
        ffmpeg_exec=ffmpeg_exec,
        process_factory=factory,
        metric_runner=runner,
        has_vmaf=has_vmaf,
        cancel_event=cancel_event,
    )
    return compare_downscale(candidate_probe, original_probe, target)


class SampleTunerCache:
    """품질값 정밀 결정 결과 SQLite 저장소

    - 화질 분석 캐시와 같은 DB 파일에 `CREATE TABLE IF NOT EXISTS`로 표만 덧붙여 옛 DB에서도 동작
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
        """표를 준비한 연결을 열어 반환. DB를 열 수 없으면 None(캐시 없이 계속 진행)"""
        if self._disabled:
            return None
        try:
            parent = os.path.dirname(self._db_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            conn = sqlite3.connect(self._db_path, timeout=SQLITE_BUSY_TIMEOUT_SEC)
            conn.execute(_CACHE_TABLE_SQL)
            conn.commit()
            return conn
        except sqlite3.Error:
            self._disabled = True
            return None

    def load(self, key: TuneCacheKey) -> SampleTuneRecord | None:
        """키가 같은 캐시 행 반환. 없거나 읽을 수 없으면 None"""
        conn = self._connect()
        if conn is None:
            return None
        condition = " AND ".join(f"{name} = ?" for name in CACHE_COLUMNS[:KEY_COLUMN_COUNT])
        try:
            row = conn.execute(
                f"SELECT {', '.join(CACHE_COLUMNS)} FROM {SAMPLE_TUNER_TABLE_NAME} "
                f"WHERE {condition} LIMIT 1",
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


def row_to_record(row: Sequence[object]) -> SampleTuneRecord:
    """SQLite 행 값을 캐시 레코드로 변환. 형식이 다른 값은 기본값으로 둠"""
    values = dict(zip(CACHE_COLUMNS, row, strict=True))
    key = TuneCacheKey(
        source_path=_as_str(values["source_path"]),
        filesize_bytes=_as_int(values["filesize_bytes"]),
        mtime_ns=_as_int(values["mtime_ns"]),
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
        measured_at=measured_at if measured_at is not None else _utc_now_iso(),
    )


def open_default_cache() -> SampleTunerCache:
    """data 폴더의 화질 분석 캐시 DB에 정밀 결정 표를 붙인 저장소 생성, 연결은 첫 작업 때"""
    return SampleTunerCache(os.path.join(get_data_dir(), ANALYSIS_DB_FILENAME))


def tune_quality_cached(
    *,
    job: TuneJob,
    plan: EncodePlan,
    cache: SampleTunerCache,
    target: TuneTarget | None = None,
    process_factory: ProcessFactory | None = None,
    ffmpeg_exec: str = FFMPEG_EXECUTABLE_DEFAULT,
    cancel_event: threading.Event | None = None,
    has_vmaf: bool = True,
    black_ratios: Sequence[float] | None = None,
    max_probes: int = TUNE_MAX_PROBES,
    delta_max: float | None = None,
    metric_runner: Callable[[list[str], str], tuple[int, str]] | None = None,
) -> TuneResult:
    """캐시를 먼저 확인하고 없으면 샘플 인코딩으로 탐색한 뒤 결과를 저장

    - 6a-2 화면과 인코딩 경로가 그대로 부를 수 있는 단일 진입점
    - 취소된 탐색 결과는 오염을 막으려고 저장하지 않음
    """
    resolved_target = target if target is not None else TuneTarget()
    key = TuneCacheKey(
        source_path=job.source_path,
        filesize_bytes=job.source_size,
        mtime_ns=job.source_mtime_ns,
        encoder=job.encoder,
        baseline_quality=job.baseline_quality,
        plan_hash=plan_signature(plan),
        target_key=resolved_target.cache_key,
    )
    cached = cache.load(key)
    if cached is not None:
        return cached.result
    result = tune_quality_for_video(
        job=job,
        plan=plan,
        ffmpeg_exec=ffmpeg_exec,
        process_factory=process_factory,
        cancel_event=cancel_event,
        target=resolved_target,
        has_vmaf=has_vmaf,
        black_ratios=black_ratios,
        max_probes=max_probes,
        delta_max=delta_max,
        metric_runner=metric_runner,
    )
    cancelled = cancel_event is not None and cancel_event.is_set()
    if not cancelled and result.reason != REASON_UNMEASURABLE:
        cache.save(record_from_result(key, result))
    return result





