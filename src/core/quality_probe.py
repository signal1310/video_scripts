"""샘플 인코딩과 VMAF/SSIM 지표 측정의 순수 구성/파싱 함수, 프로세스 실행기

- 샘플 인코딩은 `build_sample_ffmpeg_args`를 재사용하며 별도 인코더 명령줄 빌더를 두지 않음
- 지표 선택, 지표 필터 구성, 로그 파싱, 기울기와 예측 대 실제 비율 집계는 부작용 없음
- 프로세스 실행은 `CommandRunner` 주입으로 분리하고 `threading.Event` 취소를 지원
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

from src.core.encode.encode_plan import EncodePlan
from src.core.encode.ffmpeg_command import FFMPEG_EXECUTABLE_DEFAULT, build_sample_ffmpeg_args

METRIC_VMAF: str = "vmaf"
METRIC_SSIM: str = "ssim"
VMAF_MODEL_DEFAULT: str = "vmaf_v0.6.1"
VMAF_MODEL_4K: str = "vmaf_4k_v0.6.1"
# - 4K급 판정은 긴 변 기준(3840x2160, 3840x1920 VR180, 3840x1600 21:9 포함)
FOUR_K_MIN_LONG_SIDE: int = 3840
VMAF_THREADS: int = 4
LOG_NAME_VMAF: str = "metric_vmaf.json"
LOG_NAME_SSIM: str = "metric_ssim.log"
POLL_INTERVAL_SEC: float = 0.1
PROCESS_TERMINATE_TIMEOUT_SEC: float = 5.0
MIN_SLOPE_POINTS: int = 2
# - 같은 화면의 SSIM dB는 inf로 출력되므로 평균과 무효 구간 판정(40dB 이상)이 흔들리지 않게 상한으로 캡핑
SSIM_DB_MAX: float = 100.0
SSIM_ALL_RE: re.Pattern[str] = re.compile(r"All:\s*([0-9.]+)\s*\(([0-9.]+|inf)\)")


class CommandRunner(Protocol):
    """외부 명령 실행기. 취소와 제한 시간 처리는 실행기 구현이 맡음"""

    def __call__(self, args: list[str], work_dir: str) -> int:
        """명령을 실행하고 종료 코드를 반환"""
        ...


def _terminate_process(proc: subprocess.Popen[bytes]) -> None:
    """실행 중인 자식 프로세스를 종료하고 회수"""
    if proc.poll() is None:
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired):
        proc.wait(timeout=PROCESS_TERMINATE_TIMEOUT_SEC)


class ProcessCommandRunner:
    """외부 명령을 실행하며 취소 신호와 제한 시간을 감시하는 기본 실행기"""

    def __init__(
        self,
        *,
        cancel_event: threading.Event | None = None,
        timeout_sec: float | None = None,
    ) -> None:
        self._cancel_event: threading.Event | None = cancel_event
        self._timeout_sec: float | None = timeout_sec

    def __call__(self, args: list[str], work_dir: str) -> int:
        """명령을 실행하고 종료 코드를 반환. 취소되면 InterruptedError, 제한 시간 초과면 TimeoutError"""
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        proc = subprocess.Popen(
            args,
            cwd=work_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creationflags,
        )
        deadline = None if self._timeout_sec is None else time.monotonic() + self._timeout_sec
        try:
            while True:
                if self._cancel_event is not None and self._cancel_event.is_set():
                    raise InterruptedError("샘플 인코딩이 취소되었습니다")
                if deadline is not None and time.monotonic() >= deadline:
                    raise TimeoutError(f"명령 실행 시간({self._timeout_sec:.0f}초)을 초과했습니다")
                try:
                    return proc.wait(timeout=POLL_INTERVAL_SEC)
                except subprocess.TimeoutExpired:
                    continue
        except BaseException:
            _terminate_process(proc)
            raise


def vmaf_model_for(width: int, height: int) -> str:
    """4K급(긴 변 3840 이상)이면 4K 모델, 그 외에는 기본 모델 이름 반환

    - 3840x2160, 3840x1920(VR180), 3840x1600(21:9)을 모두 4K급으로 봄
    """
    return VMAF_MODEL_4K if max(width, height) >= FOUR_K_MIN_LONG_SIDE else VMAF_MODEL_DEFAULT


def resolve_metric_name(has_vmaf: bool) -> str:
    """VMAF 필터가 없으면 SSIM으로 대체한 지표 이름 반환"""
    return METRIC_VMAF if has_vmaf else METRIC_SSIM


def build_metric_filter(
    metric: str,
    *,
    model: str = VMAF_MODEL_DEFAULT,
    log_name: str = "",
    n_subsample: int = 0,
) -> str:
    """왜곡([0:v])과 기준([1:v]) 입력을 비교하는 lavfi 필터 문자열 생성

    - 로그 파일 이름은 작업 폴더 기준 상대 이름을 받아 Windows 경로의 콜론 문제를 피함
    - `n_subsample`이 1보다 크면 VMAF가 그 간격으로 프레임을 표본화함
    """
    if metric == METRIC_VMAF:
        options = f"model=version={model}:n_threads={VMAF_THREADS}"
        if n_subsample > 1:
            options += f":n_subsample={n_subsample}"
        if log_name:
            options += f":log_path={log_name}:log_fmt=json"
        return f"[0:v][1:v]libvmaf={options}"
    if metric == METRIC_SSIM:
        return f"[0:v][1:v]ssim=stats_file={log_name}" if log_name else "[0:v][1:v]ssim"
    raise ValueError(f"지원하지 않는 지표입니다: {metric}")


def build_metric_command(
    *,
    ffmpeg_executable: str,
    distorted_path: str,
    reference_path: str,
    metric: str,
    model: str,
    log_name: str,
) -> list[str]:
    """두 영상의 지표를 재고 로그를 작업 폴더에 남기는 FFmpeg 인자 목록"""
    return [
        ffmpeg_executable,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        distorted_path,
        "-i",
        reference_path,
        "-lavfi",
        build_metric_filter(metric, model=model, log_name=log_name),
        "-f",
        "null",
        "-",
    ]


def parse_vmaf_log(text: str) -> float | None:
    """libvmaf JSON 로그의 pooled vmaf 평균 점수. 형식이 다르면 None"""
    try:
        data: object = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    pooled = data.get("pooled_metrics")
    if not isinstance(pooled, dict):
        return None
    vmaf = pooled.get("vmaf")
    if not isinstance(vmaf, dict):
        return None
    mean = vmaf.get("mean")
    if isinstance(mean, (int, float)):
        return float(mean)
    return None


def parse_ssim_stats(text: str) -> tuple[float, float] | None:
    """ssim stats 로그의 All 점수와 dB 평균. 측정 줄이 없으면 None

    - 같은 화면처럼 dB가 inf인 줄은 `SSIM_DB_MAX`로 캡핑해 평균이 무한대가 되지 않게 함
    - 캡핑 값은 무효 구간 판정 기준(40dB 이상)보다 크므로 정적 구간 제외가 그대로 동작
    """
    scores: list[float] = []
    dbs: list[float] = []
    for match in SSIM_ALL_RE.finditer(text):
        scores.append(float(match.group(1)))
        raw_db = SSIM_DB_MAX if match.group(2) == "inf" else float(match.group(2))
        dbs.append(min(raw_db, SSIM_DB_MAX))
    if not scores:
        return None
    return (sum(scores) / len(scores), sum(dbs) / len(dbs))


def fit_quality_slope_k(points: Sequence[tuple[float, float]]) -> float | None:
    """(품질값, 바이트) 쌍에서 크기 곡선 기울기 K 산출

    - 크기는 2^((q0 - q)/K)에 비례하므로 log2(바이트) 대 품질값 직선의 기울기에서 K를 역산
    - 서로 다른 품질값이 2개 미만이거나 기울기가 0 이상이면 None
    """
    valid = [(quality, math.log2(size)) for quality, size in points if size > 0]
    if len(valid) < MIN_SLOPE_POINTS:
        return None
    qualities = [quality for quality, _ in valid]
    logs = [value for _, value in valid]
    mean_q = sum(qualities) / len(qualities)
    mean_y = sum(logs) / len(logs)
    denominator = sum((quality - mean_q) ** 2 for quality in qualities)
    if denominator <= 0:
        return None
    slope = sum((quality - mean_q) * (value - mean_y) for quality, value in valid) / denominator
    if slope >= 0:
        return None
    return -1.0 / slope


def median_size_ratio(actual_bytes: Sequence[float], predicted_bytes: Sequence[float]) -> float | None:
    """(실제/예측) 크기 비율의 중앙값. 길이가 다르거나 유효 쌍이 없으면 None"""
    if len(actual_bytes) != len(predicted_bytes):
        return None
    ratios = sorted(
        actual / predicted
        for actual, predicted in zip(actual_bytes, predicted_bytes, strict=True)
        if actual > 0 and predicted > 0
    )
    if not ratios:
        return None
    middle = len(ratios) // 2
    if len(ratios) % 2 == 1:
        return ratios[middle]
    return (ratios[middle - 1] + ratios[middle]) / 2.0


def encode_sample_segments(
    *,
    plan: EncodePlan,
    segments: Sequence[tuple[float, float]],
    quality: float,
    work_dir: str,
    runner: CommandRunner,
    ffmpeg_executable: str = FFMPEG_EXECUTABLE_DEFAULT,
) -> list[int]:
    """구간별 샘플 인코딩 결과 바이트 목록 반환. 실패한 구간은 0"""
    sizes: list[int] = []
    for index, (start_sec, duration_sec) in enumerate(segments):
        part_path = os.path.join(work_dir, f"sample_{index}_{quality:.1f}.mp4")
        args = build_sample_ffmpeg_args(
            plan,
            start_sec=start_sec,
            duration_sec=duration_sec,
            part_path=part_path,
            quality=quality,
            ffmpeg_executable=ffmpeg_executable,
        )
        if runner(args, work_dir) != 0 or not os.path.exists(part_path):
            sizes.append(0)
            continue
        sizes.append(os.path.getsize(part_path))
    return sizes


def measure_metric(
    *,
    distorted_path: str,
    reference_path: str,
    work_dir: str,
    metric: str,
    runner: CommandRunner,
    model: str = VMAF_MODEL_DEFAULT,
    ffmpeg_executable: str = FFMPEG_EXECUTABLE_DEFAULT,
) -> float | None:
    """두 영상 비교 지표 값(VMAF 점수 또는 SSIM 점수) 반환. 측정 실패 시 None"""
    log_name = LOG_NAME_VMAF if metric == METRIC_VMAF else LOG_NAME_SSIM
    log_path = Path(work_dir, log_name)
    if log_path.exists():
        log_path.unlink()
    args = build_metric_command(
        ffmpeg_executable=ffmpeg_executable,
        distorted_path=distorted_path,
        reference_path=reference_path,
        metric=metric,
        model=model,
        log_name=log_name,
    )
    if runner(args, work_dir) != 0 or not log_path.exists():
        return None
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if metric == METRIC_VMAF:
        return parse_vmaf_log(text)
    ssim = parse_ssim_stats(text)
    return ssim[0] if ssim is not None else None
