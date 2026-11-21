"""화질 분석 실측 스크립트 공통 실행기, 클립 정보, 캐시, 측정 환경

- `measure_frame_analysis.py`와 `measure_frame_analysis_vr.py`가 함께 씀
- 세그먼트 인코딩은 제품 인자(`build_encode_plan`, `build_sample_ffmpeg_args`)를 재사용
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from src.core.analysis.prober import extract_video_probe
from src.core.config import CONTAINER_POLICY_MP4
from src.core.encode.encode_plan import EncodePlan, build_encode_plan
from src.core.encode.ffmpeg_command import build_ffmpeg_args, build_sample_ffmpeg_args
from src.core.encode.stream_layout import StreamLayout, parse_stream_layout
from src.core.encoder_specs import get_encoder_spec
from src.core.models import VideoProps
from src.core.quality_probe import (
    LOG_NAME_SSIM,
    LOG_NAME_VMAF,
    METRIC_VMAF,
    build_metric_command,
    parse_ssim_stats,
    parse_vmaf_log,
    vmaf_model_for,
)
from src.core.resolution_tiers import ResolutionTier, calc_scaled_dimensions

BLACK_PIXEL_THRESHOLD: float = 0.10
CROP_LIMIT: int = 24
ENCODE_TIMEOUT_SEC: float = 1800.0
METRIC_TIMEOUT_SEC: float = 1800.0
CACHE_NAME: str = "cache.json"
STDERR_KEEP_CHARS: int = 4000


@dataclass(frozen=True, slots=True)
class ClipFacts:
    """실측에 필요한 클립 정보"""

    index: int
    path: Path
    props: VideoProps
    layout: StreamLayout

    @property
    def label(self) -> str:
        """결과 표에 쓰는 짧은 이름"""
        return self.path.stem


@dataclass(frozen=True, slots=True)
class CommandOutcome:
    """외부 명령 실행 결과"""

    returncode: int
    seconds: float
    stderr: str


@dataclass(frozen=True, slots=True)
class MetricValue:
    """지표 한 번의 점수, dB, 소요 시간"""

    score: float | None
    db: float | None
    seconds: float
    returncode: int


class ScriptRunner:
    """외부 명령을 실행하고 표준 오류를 보관하는 실행기"""

    def __init__(self, *, timeout_sec: float) -> None:
        self._timeout_sec: float = timeout_sec

    def __call__(self, args: list[str], work_dir: str) -> CommandOutcome:
        """명령을 실행하고 종료 코드, 소요 시간, 표준 오류 꼬리를 반환"""
        started = time.monotonic()
        try:
            completed = subprocess.run(
                args,
                cwd=work_dir,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=self._timeout_sec,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return CommandOutcome(-1, time.monotonic() - started, "timeout")
        stderr = completed.stderr.decode("utf-8", errors="replace")[-STDERR_KEEP_CHARS:]
        return CommandOutcome(completed.returncode, time.monotonic() - started, stderr)


class Cache:
    """측정 기록 누적 캐시"""

    def __init__(self, path: Path) -> None:
        self._path: Path = path
        self._data: dict[str, Any] = {}
        if path.is_file():
            loaded: object = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._data = loaded

    def get(self, key: str) -> dict[str, Any] | None:
        """키 기록 반환, 없으면 None"""
        value = self._data.get(key)
        return value if isinstance(value, dict) else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        """키 기록 저장"""
        self._data[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")


class Env:
    """클립, 캐시, 실행기를 묶은 측정 환경"""

    def __init__(self, *, clips_dir: Path, out_dir: Path, ffmpeg: str, ffprobe: str) -> None:
        self.clips_dir: Path = clips_dir
        self.out_dir: Path = out_dir
        self.ffmpeg: str = ffmpeg
        self.ffprobe: str = ffprobe
        self.encode_runner: ScriptRunner = ScriptRunner(timeout_sec=ENCODE_TIMEOUT_SEC)
        self.metric_runner: ScriptRunner = ScriptRunner(timeout_sec=METRIC_TIMEOUT_SEC)
        self.cache: Cache = Cache(out_dir / CACHE_NAME)

    def select(self, prefixes: tuple[str, ...], limit: int = 0) -> list[Path]:
        """클립 이름 접두사로 대상을 고름. 접두사가 비면 전체"""
        paths = sorted(path for path in self.clips_dir.iterdir() if path.is_file())
        if prefixes:
            paths = [path for path in paths if any(path.name.startswith(prefix) for prefix in prefixes)]
        return paths[:limit] if limit > 0 else paths

    def load(self, path: Path, index: int) -> ClipFacts:
        """ffprobe로 클립 정보 로드. 입력 경로는 절대 경로로 고정"""
        resolved = path.resolve()
        props, probe = extract_video_probe(str(resolved), resolved.name, True, ffprobe=self.ffprobe)
        return ClipFacts(index=index, path=resolved, props=props, layout=parse_stream_layout(probe))

    def plan(
        self,
        facts: ClipFacts,
        encoder: str,
        quality: float,
        container_policy: str = CONTAINER_POLICY_MP4,
    ) -> EncodePlan:
        """세그먼트 인코딩 계획. 미활성 AV1 행은 계획만 세우고 인코더 이름만 바꿈"""
        spec = get_encoder_spec(encoder)
        base_encoder = encoder if spec is not None and spec.available else "hevc_nvenc"
        plan = build_encode_plan(
            source_path=str(facts.path),
            part_path="unused.mp4",
            output_path="unused.mp4",
            source_ext=facts.path.suffix.lower().lstrip("."),
            encoder=base_encoder,
            quality=quality,
            fps=facts.props.fps,
            layout=facts.layout,
            container_policy=container_policy,
            tag_value="",
        )
        return plan if base_encoder == encoder else replace(plan, encoder=encoder)

    def encode_segment(
        self,
        facts: ClipFacts,
        encoder: str,
        quality: float,
        *,
        start_sec: float,
        duration_sec: float,
        out_name: str,
        level: str | None = None,
        container_policy: str = CONTAINER_POLICY_MP4,
    ) -> tuple[int, CommandOutcome]:
        """샘플 구간을 제품 인자로 인코딩하고 결과 크기와 실행 결과 반환"""
        out_path = Path(self.out_dir, out_name)
        if out_path.exists():
            out_path.unlink()
        args = build_sample_ffmpeg_args(
            self.plan(facts, encoder, quality, container_policy),
            start_sec=start_sec,
            duration_sec=duration_sec,
            part_path=str(out_path),
            quality=quality,
            ffmpeg_executable=self.ffmpeg,
        )
        if level is not None:
            override_level(args, level)
        outcome = self.encode_runner(args, str(self.out_dir))
        size = out_path.stat().st_size if outcome.returncode == 0 and out_path.exists() else 0
        return size, outcome

    def reference_segment(self, facts: ClipFacts, start_sec: float, duration_sec: float) -> Path | None:
        """비교 기준이 되는 무손실 구간 생성(libx264 CRF 0)"""
        out_path = Path(self.out_dir, f"ref_{facts.label}_{start_sec:.0f}_{duration_sec:.0f}.mp4")
        if out_path.exists():
            return out_path
        size, _outcome = self.encode_segment(
            facts,
            "libx264",
            0.0,
            start_sec=start_sec,
            duration_sec=duration_sec,
            out_name=out_path.name,
        )
        self.cache.put(f"ref|{facts.label}|{start_sec:.1f}|{duration_sec:.1f}", {"bytes": size})
        return out_path if size > 0 else None

    def full_encode(
        self,
        facts: ClipFacts,
        encoder: str,
        quality: float,
        out_name: str,
        container_policy: str,
    ) -> tuple[int, CommandOutcome]:
        """본 인코딩과 같은 인자로 전체 구간을 인코딩(부가 정보 보존 확인용)"""
        out_path = Path(self.out_dir, out_name)
        if out_path.exists():
            out_path.unlink()
        plan = self.plan(facts, encoder, quality, container_policy)
        plan = replace(plan, part_path=str(out_path), output_path=str(out_path))
        args = build_ffmpeg_args(plan, self.ffmpeg)
        outcome = self.encode_runner(args, str(self.out_dir))
        size = out_path.stat().st_size if outcome.returncode == 0 and out_path.exists() else 0
        return size, outcome

    def metric(self, *, distorted: Path, reference: Path, metric: str, model: str) -> MetricValue:
        """두 영상 비교 지표 실행. 로그는 출력 폴더에 남기고 읽음"""
        log_name = LOG_NAME_VMAF if metric == METRIC_VMAF else LOG_NAME_SSIM
        log_path = Path(self.out_dir, log_name)
        if log_path.exists():
            log_path.unlink()
        args = build_metric_command(
            ffmpeg_executable=self.ffmpeg,
            distorted_path=str(distorted),
            reference_path=str(reference),
            metric=metric,
            model=model,
            log_name=log_name,
        )
        outcome = self.metric_runner(args, str(self.out_dir))
        return read_metric_log(log_path, metric, outcome)

    def roundtrip_metric(
        self,
        facts: ClipFacts,
        tier: ResolutionTier,
        *,
        start_sec: float,
        duration_sec: float,
        metric: str,
    ) -> MetricValue:
        """규격으로 줄였다 되돌린 프레임과 원본을 한 번에 비교(결정 19)"""
        out_width, out_height = calc_scaled_dimensions(facts.props.width, facts.props.height, tier)
        if (out_width, out_height) == (facts.props.width, facts.props.height):
            return MetricValue(None, None, 0.0, -2)
        log_name = LOG_NAME_VMAF if metric == METRIC_VMAF else LOG_NAME_SSIM
        log_path = Path(self.out_dir, log_name)
        if log_path.exists():
            log_path.unlink()
        scaled = (
            f"[0:v]split=2[ref][dist];[dist]scale={out_width}:{out_height}:flags=bicubic,"
            f"scale={facts.props.width}:{facts.props.height}:flags=bicubic[sc]"
        )
        if metric == METRIC_VMAF:
            model = vmaf_model_for(facts.props.width, facts.props.height)
            chain = f"{scaled};[sc][ref]libvmaf=model=version={model}:n_threads=4:log_path={log_name}:log_fmt=json"
        else:
            chain = f"{scaled};[sc][ref]ssim=stats_file={log_name}"
        args = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-ss",
            f"{start_sec:.3f}",
            "-i",
            str(facts.path),
            "-t",
            f"{duration_sec:.3f}",
            "-filter_complex",
            chain,
            "-f",
            "null",
            "-",
        ]
        outcome = self.metric_runner(args, str(self.out_dir))
        return read_metric_log(log_path, metric, outcome)

    def black_ratio(self, facts: ClipFacts, start_sec: float, duration_sec: float) -> float | None:
        """blackdetect가 찾은 검은 구간 길이 합의 비율"""
        args = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-ss",
            f"{start_sec:.3f}",
            "-i",
            str(facts.path),
            "-t",
            f"{duration_sec:.3f}",
            "-vf",
            f"blackdetect=d=0.1:pix_th={BLACK_PIXEL_THRESHOLD:.2f}",
            "-an",
            "-f",
            "null",
            "-",
        ]
        outcome = self.metric_runner(args, str(self.out_dir))
        if outcome.returncode != 0:
            return None
        total = 0.0
        for line in outcome.stderr.splitlines():
            if "black_duration:" not in line:
                continue
            try:
                total += float(line.rsplit("black_duration:", 1)[1].strip())
            except ValueError:
                continue
        return min(1.0, total / duration_sec) if duration_sec > 0 else None

    def cropdetect(self, facts: ClipFacts, start_sec: float, duration_sec: float) -> tuple[int, int, int, int] | None:
        """cropdetect 제안 crop 중 가장 많이 나온 값을 반환"""
        args = [
            self.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-ss",
            f"{start_sec:.3f}",
            "-i",
            str(facts.path),
            "-t",
            f"{duration_sec:.3f}",
            "-vf",
            f"cropdetect={CROP_LIMIT}:2:0",
            "-an",
            "-f",
            "null",
            "-",
        ]
        outcome = self.metric_runner(args, str(self.out_dir))
        if outcome.returncode != 0:
            return None
        counts: dict[tuple[int, int, int, int], int] = {}
        for line in outcome.stderr.splitlines():
            marker = "crop="
            if marker not in line:
                continue
            raw = line.rsplit(marker, 1)[1].split()[0]
            parts = raw.split(":")
            if len(parts) != 4:
                continue
            try:
                value = (int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3]))
            except ValueError:
                continue
            counts[value] = counts.get(value, 0) + 1
        return max(counts, key=lambda key: counts[key]) if counts else None


def read_metric_log(log_path: Path, metric: str, outcome: CommandOutcome) -> MetricValue:
    """지표 로그를 읽어 점수와 dB 반환. 실패하면 빈 결과"""
    if outcome.returncode != 0 or not log_path.exists():
        return MetricValue(None, None, outcome.seconds, outcome.returncode)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if metric == METRIC_VMAF:
        return MetricValue(parse_vmaf_log(text), None, outcome.seconds, outcome.returncode)
    ssim = parse_ssim_stats(text)
    if ssim is None:
        return MetricValue(None, None, outcome.seconds, outcome.returncode)
    return MetricValue(ssim[0], ssim[1], outcome.seconds, outcome.returncode)


def override_level(args: list[str], level: str) -> None:
    """측정용으로 NVENC 레벨 인자 값만 바꿈"""
    for index, token in enumerate(args):
        if token.startswith("-level:v") and index + 1 < len(args):
            args[index + 1] = level
            return

