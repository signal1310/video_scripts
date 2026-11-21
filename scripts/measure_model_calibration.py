"""4단계 4a-1 인코더 척도와 판정 모델 보정 실측 스크립트

- 클립 폴더는 인자로만 받고 출력은 `--out-dir`(기본 시스템 임시 폴더) 아래에만 씀
- 샘플 인코딩과 본 인코딩 인자는 `build_encode_plan`과 `build_ffmpeg_args`를 그대로 씀
- 측정 기록은 `--out-dir`의 `cache.json`에 누적해 중단 뒤 이어서 실행할 수 있음
- 사용 예(경로는 모두 인자로 받음):
  python -I -X utf8 scripts/measure_model_calibration.py --clips-dir <클립 폴더> --mode k --encoders hevc_nvenc
  python -I -X utf8 scripts/measure_model_calibration.py --clips-dir <클립 폴더> --mode ratio --encoders libx265
  python -I -X utf8 scripts/measure_model_calibration.py --clips-dir <클립 폴더> --mode guards
  python -I -X utf8 scripts/measure_model_calibration.py --cleanup
"""

from __future__ import annotations

import argparse
import itertools
import json
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.core.analysis.prober import extract_video_probe  # noqa: E402
from src.core.config import CONTAINER_POLICY_MP4, ClassifierConfig  # noqa: E402
from src.core.encode.encode_plan import EncodePlan, build_encode_plan  # noqa: E402
from src.core.encode.ffmpeg_command import build_ffmpeg_args  # noqa: E402
from src.core.encode.quality_tuning import (  # noqa: E402
    BYTES_PER_MB,
    compute_kf_target_bytes,
    resolve_video_stream_bytes,
)
from src.core.encode.stream_layout import StreamLayout, parse_stream_layout  # noqa: E402
from src.core.encoder_specs import get_encoder_spec  # noqa: E402
from src.core.evaluation.evaluator import VideoEvaluator  # noqa: E402
from src.core.evaluation.size_estimator import (  # noqa: E402
    calc_keyframe_increase_ratio,
    estimate_video_stream_mb,
)
from src.core.models import VideoProps  # noqa: E402
from src.core.quality_probe import fit_quality_slope_k  # noqa: E402
from src.core.resolution_tiers import (  # noqa: E402
    MAXRATE_HEADROOM_WARN_RATIO,
    MIN_DOWNSCALE_AREA_RATIO,
    RESOLUTION_TIER_BY_KEY,
    RESOLUTION_TIERS,
    build_scale_expression,
    calc_scaled_dimensions,
    is_meaningful_downscale,
    orientation_of,
)

BASELINE_QUALITY: float = 22.0
QUALITIES_K: tuple[float, ...] = (18.0, 22.0, 26.0, 30.0)
QUALITIES_DECIMAL: tuple[float, ...] = (22.0, 22.2, 22.5, 22.8, 23.0)
DEFAULT_ENCODERS: tuple[str, ...] = (
    "hevc_nvenc",
    "libx265",
    "h264_nvenc",
    "libx264",
    "av1_nvenc",
    "libsvtav1",
)
DEFAULT_K_CLIPS: tuple[str, ...] = ("video7", "video13")
DEFAULT_DECIMAL_CLIPS: tuple[str, ...] = ("video7",)
DEFAULT_CPU_RATIO_CLIPS: tuple[str, ...] = (
    "video7",
    "video13",
    "video14",
    "video15",
    "video16",
    "video17",
)
DEFAULT_GUARD_CLIPS: tuple[str, ...] = ("video1", "video7", "video8")
DEFAULT_GOP_FRAMES: int = 250
GUARD_TIER_KEY: str = "1080p"
ENCODE_TIMEOUT_SEC: float = 1800.0
CACHE_NAME: str = "cache.json"
SUMMARY_NAME: str = "summary.json"
CPU_ENCODERS: frozenset[str] = frozenset({"libx265", "libx264", "libsvtav1"})
FAILURE_TAIL_CHARS: int = 400


@dataclass(frozen=True, slots=True)
class ClipFacts:
    """실측에 필요한 클립 메타데이터와 판정 입력"""

    index: int
    path: Path
    props: VideoProps
    layout: StreamLayout
    size_bytes: int

    @property
    def label(self) -> str:
        """결과 표에 쓰는 짧은 이름"""
        return self.path.stem


@dataclass(frozen=True, slots=True)
class EncodeResult:
    """본 인코딩 한 번의 결과"""

    size_bytes: int
    elapsed_sec: float
    returncode: int
    stderr_tail: str = ""

    @property
    def ok(self) -> bool:
        """인코딩이 성공하고 결과 파일이 있으면 참"""
        return self.returncode == 0 and self.size_bytes > 0


class Cache:
    """측정 기록을 JSON 파일에 누적 보관하는 캐시"""

    def __init__(self, path: Path) -> None:
        self._path: Path = path
        self._data: dict[str, Any] = {}
        if path.is_file():
            loaded: object = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                self._data = loaded

    def get(self, key: str) -> dict[str, Any] | None:
        """키에 해당하는 기록 반환, 없으면 None"""
        value = self._data.get(key)
        return value if isinstance(value, dict) else None

    def put(self, key: str, value: dict[str, Any]) -> None:
        """키 기록을 갱신하고 파일에 저장"""
        self._data[key] = value
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._data, ensure_ascii=False, indent=1), encoding="utf-8")

    def items(self) -> dict[str, Any]:
        """저장된 전체 기록 반환"""
        return dict(self._data)


class ScriptRunner:
    """FFmpeg 실행과 표준 오류 수집을 함께 하는 실행기"""

    def __init__(self, *, timeout_sec: float = ENCODE_TIMEOUT_SEC) -> None:
        self._timeout_sec: float = timeout_sec
        self.last_stderr: str = ""

    def __call__(self, args: list[str], work_dir: str) -> int:
        """명령을 실행하고 종료 코드를 반환. 표준 오류는 마지막 부분만 보관"""
        completed = subprocess.run(
            args,
            cwd=work_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=self._timeout_sec,
            check=False,
        )
        self.last_stderr = completed.stderr.decode("utf-8", errors="replace")[-FAILURE_TAIL_CHARS:]
        return completed.returncode


def select_clips(clips_dir: Path, wanted: tuple[str, ...], limit: int) -> list[Path]:
    """클립 이름 접두사로 대상을 고르고 정렬. wanted가 비면 폴더 전체"""
    paths = sorted(path for path in clips_dir.iterdir() if path.is_file())
    if wanted:
        paths = [path for path in paths if any(path.name.startswith(prefix) for prefix in wanted)]
    if limit > 0:
        paths = paths[:limit]
    return paths


def load_clip(path: Path, index: int, ffprobe: str) -> ClipFacts:
    """ffprobe로 클립 메타데이터, 스트림 구성, 파일 크기를 읽음

    - FFmpeg를 출력 폴더에서 실행하므로 입력 경로는 절대 경로로 고정
    """
    resolved = path.resolve()
    props, probe = extract_video_probe(str(resolved), resolved.name, True, ffprobe=ffprobe)
    return ClipFacts(
        index=index,
        path=resolved,
        props=props,
        layout=parse_stream_layout(probe),
        size_bytes=resolved.stat().st_size,
    )


def build_plan_for(
    facts: ClipFacts,
    encoder: str,
    quality: float,
    output_path: Path,
    *,
    scale_filter: str | None = None,
    output_size: tuple[int, int] | None = None,
    maxrate_kbps: int | None = None,
) -> EncodePlan:
    """실측 클립의 인코딩 계획 생성. 비활성 AV1 행은 계획만 세우고 인코더 이름만 바꿈"""
    spec = get_encoder_spec(encoder)
    base_encoder = encoder if spec is not None and spec.available else "hevc_nvenc"
    plan = build_encode_plan(
        source_path=str(facts.path),
        part_path=str(output_path),
        output_path=str(output_path),
        source_ext=facts.path.suffix.lower().lstrip("."),
        encoder=base_encoder,
        quality=quality,
        fps=facts.props.fps,
        layout=facts.layout,
        container_policy=CONTAINER_POLICY_MP4,
        tag_value="",
        output_size=output_size,
        scale_filter=scale_filter,
        maxrate_kbps=maxrate_kbps,
    )
    return plan if base_encoder == encoder else replace(plan, encoder=encoder)


def override_gop(args: list[str], frames: int) -> None:
    """측정용으로 키프레임 간격 인자 값만 바꿈"""
    for index, token in enumerate(args):
        if token.startswith("-g:") and index + 1 < len(args):
            args[index + 1] = str(frames)
            return


def encode_once(
    *,
    facts: ClipFacts,
    encoder: str,
    quality: float,
    work_dir: Path,
    out_name: str,
    runner: ScriptRunner,
    ffmpeg: str,
    gop_frames: int | None = None,
    scale_filter: str | None = None,
    output_size: tuple[int, int] | None = None,
    maxrate_kbps: int | None = None,
) -> EncodeResult:
    """제품 인자로 본 인코딩 한 번을 실행하고 결과 크기와 소요 시간을 반환"""
    output_path = Path(work_dir, out_name)
    if output_path.exists():
        output_path.unlink()
    plan = build_plan_for(
        facts,
        encoder,
        quality,
        output_path,
        scale_filter=scale_filter,
        output_size=output_size,
        maxrate_kbps=maxrate_kbps,
    )
    args = build_ffmpeg_args(plan, ffmpeg)
    if gop_frames is not None:
        override_gop(args, gop_frames)
    started = time.monotonic()
    returncode = runner(args, str(work_dir))
    elapsed = time.monotonic() - started
    size = output_path.stat().st_size if returncode == 0 and output_path.exists() else 0
    stderr_tail = runner.last_stderr
    if output_path.exists():
        output_path.unlink()
    return EncodeResult(size, elapsed, returncode, stderr_tail)


def cached_encode(
    cache: Cache,
    key: str,
    factory: Callable[[], EncodeResult],
) -> dict[str, Any]:
    """캐시에 없는 인코딩만 실행하고 결과 기록을 반환"""
    hit = cache.get(key)
    if hit is not None:
        return hit
    result = factory()
    value: dict[str, Any] = {
        "bytes": result.size_bytes,
        "seconds": round(result.elapsed_sec, 2),
        "returncode": result.returncode,
        "stderr": "" if result.ok else result.stderr_tail,
    }
    cache.put(key, value)
    print(f"encode {key} bytes={value['bytes']} sec={value['seconds']} rc={result.returncode}", flush=True)
    return value


def evaluator_config(encoder: str) -> ClassifierConfig:
    """목표 인코더를 바꾼 판정 설정 반환"""
    config = ClassifierConfig()
    return replace(config, encode=replace(config.encode, encode_codec=encoder))


def keyframe_target_mb(facts: ClipFacts, config: ClassifierConfig) -> float:
    """결정 33 목표 크기(비영상 스트림 + 키프레임 증가분 포함) MB"""
    fallback_mb = estimate_video_stream_mb(facts.props)
    video_bytes = resolve_video_stream_bytes(
        facts.layout,
        source_size_bytes=facts.size_bytes,
        duration_sec=facts.props.duration,
        fallback_video_mb=fallback_mb,
    )
    spec = compute_kf_target_bytes(
        source_size_bytes=facts.size_bytes,
        video_stream_bytes=video_bytes,
        keyframe_increase_ratio=config.thresholds.keyframe_1s_increase_rate,
    )
    return spec.s_target_bytes / BYTES_PER_MB


def encode_factory(
    *,
    facts: ClipFacts,
    encoder: str,
    quality: float,
    out_dir: Path,
    out_name: str,
    runner: ScriptRunner,
    ffmpeg: str,
    gop_frames: int | None = None,
    scale_filter: str | None = None,
    output_size: tuple[int, int] | None = None,
    maxrate_kbps: int | None = None,
) -> Callable[[], EncodeResult]:
    """측정 인코딩을 지연 실행하는 팩토리 생성"""

    def _run() -> EncodeResult:
        return encode_once(
            facts=facts,
            encoder=encoder,
            quality=quality,
            work_dir=out_dir,
            out_name=out_name,
            runner=runner,
            ffmpeg=ffmpeg,
            gop_frames=gop_frames,
            scale_filter=scale_filter,
            output_size=output_size,
            maxrate_kbps=maxrate_kbps,
        )

    return _run


def run_k_mode(
    clips: list[ClipFacts],
    encoders: tuple[str, ...],
    out_dir: Path,
    cache: Cache,
    runner: ScriptRunner,
    ffmpeg: str,
) -> dict[str, Any]:
    """인코더별 품질값 곡선과 기울기 K 측정"""
    records: list[dict[str, Any]] = []
    for facts in clips:
        for encoder in encoders:
            points: list[tuple[float, float]] = []
            failures: dict[str, str] = {}
            for quality in QUALITIES_K:
                value = cached_encode(
                    cache,
                    f"k|{facts.label}|{encoder}|{quality:.1f}",
                    encode_factory(
                        facts=facts,
                        encoder=encoder,
                        quality=quality,
                        out_dir=out_dir,
                        out_name=f"c{facts.index}_{encoder}_{quality:.1f}.mp4",
                        runner=runner,
                        ffmpeg=ffmpeg,
                    ),
                )
                if value["returncode"] != 0:
                    failures[f"{quality:.1f}"] = str(value.get("stderr", ""))[:200]
                    continue
                points.append((quality, float(value["bytes"])))
            record = {
                "clip": facts.label,
                "encoder": encoder,
                "width": facts.props.width,
                "height": facts.props.height,
                "fps": facts.props.fps,
                "points": points,
                "k": fit_quality_slope_k(points),
                "failures": failures,
            }
            records.append(record)
            print(f"K clip={facts.label} encoder={encoder} k={record['k']} failures={failures}", flush=True)
    return {"records": records}


def run_decimal_mode(
    clips: list[ClipFacts],
    encoders: tuple[str, ...],
    out_dir: Path,
    cache: Cache,
    runner: ScriptRunner,
    ffmpeg: str,
) -> dict[str, Any]:
    """소수 품질값이 실제 출력 크기를 바꾸는지 측정"""
    records: list[dict[str, Any]] = []
    for facts in clips:
        for encoder in encoders:
            sizes: list[dict[str, Any]] = []
            for quality in QUALITIES_DECIMAL:
                value = cached_encode(
                    cache,
                    f"dec|{facts.label}|{encoder}|{quality:.1f}",
                    encode_factory(
                        facts=facts,
                        encoder=encoder,
                        quality=quality,
                        out_dir=out_dir,
                        out_name=f"c{facts.index}_{encoder}_{quality:.1f}.mp4",
                        runner=runner,
                        ffmpeg=ffmpeg,
                    ),
                )
                sizes.append({"quality": quality, "bytes": value["bytes"], "returncode": value["returncode"]})
            ok_sizes = [item["bytes"] for item in sizes if item["returncode"] == 0]
            strictly_decreasing = all(
                left > right for left, right in itertools.pairwise(ok_sizes)
            )
            equal_pairs = [
                [sizes[index]["quality"], sizes[index + 1]["quality"]]
                for index in range(len(sizes) - 1)
                if sizes[index]["returncode"] == 0
                and sizes[index]["bytes"] == sizes[index + 1]["bytes"]
            ]
            records.append(
                {
                    "clip": facts.label,
                    "encoder": encoder,
                    "sizes": sizes,
                    "distinct_sizes": len(set(ok_sizes)),
                    "strictly_decreasing": strictly_decreasing,
                    "equal_pairs": equal_pairs,
                }
            )
            print(f"decimals clip={facts.label} encoder={encoder} distinct={len(set(ok_sizes))}", flush=True)
    return {"records": records}


def ratio_clips_for(encoder: str, clips: list[ClipFacts], cpu_prefixes: tuple[str, ...]) -> list[ClipFacts]:
    """CPU 인코더는 지정 접두사 클립만 골라 측정 시간을 제한"""
    if encoder in CPU_ENCODERS and cpu_prefixes:
        return [facts for facts in clips if any(facts.label.startswith(prefix) for prefix in cpu_prefixes)]
    return list(clips)


def run_ratio_mode(
    clips: list[ClipFacts],
    encoders: tuple[str, ...],
    out_dir: Path,
    cache: Cache,
    runner: ScriptRunner,
    ffmpeg: str,
    cpu_prefixes: tuple[str, ...],
) -> dict[str, Any]:
    """판정 예상 크기와 실제 인코딩 크기 비율, 키프레임 목표 괴리 측정"""
    records: list[dict[str, Any]] = []
    sizes_by_encoder: dict[str, dict[str, int]] = {}
    for encoder in encoders:
        config = evaluator_config(encoder)
        for facts in ratio_clips_for(encoder, clips, cpu_prefixes):
            value = cached_encode(
                cache,
                f"ratio|{facts.label}|{encoder}|{BASELINE_QUALITY:.1f}",
                encode_factory(
                    facts=facts,
                    encoder=encoder,
                    quality=BASELINE_QUALITY,
                    out_dir=out_dir,
                    out_name=f"c{facts.index}_{encoder}_{BASELINE_QUALITY:.1f}.mp4",
                    runner=runner,
                    ffmpeg=ffmpeg,
                ),
            )
            actual_bytes = int(value["bytes"]) if value["returncode"] == 0 else 0
            evaluation = VideoEvaluator.evaluate_video(facts.props, config)
            kf_target = keyframe_target_mb(facts, config)
            record = {
                "clip": facts.label,
                "encoder": encoder,
                "source_mb": round(facts.size_bytes / BYTES_PER_MB, 2),
                "expected_mb": round(evaluation.expected_after_mb, 2),
                "expected_delta_mb": round(evaluation.expected_delta_mb, 2),
                "actual_mb": round(actual_bytes / BYTES_PER_MB, 2),
                "ratio": (
                    round((actual_bytes / BYTES_PER_MB) / evaluation.expected_after_mb, 3)
                    if actual_bytes > 0 and evaluation.expected_after_mb > 0
                    else None
                ),
                "is_encode_target": evaluation.is_encode_target,
                "action": evaluation.smart_action.name,
                "is_keyframe_exceeded": evaluation.is_keyframe_exceeded,
                "kf_target_mb": round(kf_target, 2),
                "kf_gap_mb": round(kf_target - evaluation.expected_after_mb, 2),
            }
            records.append(record)
            if actual_bytes > 0:
                sizes_by_encoder.setdefault(encoder, {})[facts.label] = actual_bytes
            print(
                f"ratio clip={facts.label} encoder={encoder} expected={record['expected_mb']} "
                f"actual={record['actual_mb']} ratio={record['ratio']}",
                flush=True,
            )
    aggregates: dict[str, Any] = {}
    for encoder in encoders:
        rows = [
            record
            for record in records
            if record["encoder"] == encoder and record["is_encode_target"] and record["ratio"] is not None
        ]
        ratios = [float(record["ratio"]) for record in rows]
        aggregates[encoder] = {
            "target_count": len(rows),
            "median_actual_over_expected": round(statistics.median(ratios), 3) if ratios else None,
        }
    hevc_sizes = sizes_by_encoder.get("hevc_nvenc", {})
    multipliers: dict[str, Any] = {}
    for encoder, sizes in sizes_by_encoder.items():
        if encoder == "hevc_nvenc":
            continue
        common = sorted(set(sizes) & set(hevc_sizes))
        if common:
            multipliers[f"hevc_nvenc_to_{encoder}"] = {
                "count": len(common),
                "median_actual_size_ratio": round(
                    statistics.median([sizes[clip] / hevc_sizes[clip] for clip in common]), 3
                ),
            }
    return {"records": records, "aggregates": aggregates, "multipliers": multipliers}


def run_guards_mode(
    clips: list[ClipFacts],
    out_dir: Path,
    cache: Cache,
    runner: ScriptRunner,
    ffmpeg: str,
) -> dict[str, Any]:
    """키프레임 1초화 증가율, maxrate 여유, 미세 축소 가드 측정"""
    config = ClassifierConfig()
    quality = config.encode.baseline_cq
    gop_records: list[dict[str, Any]] = []
    for facts in clips:
        one_second_frames = max(1, round(facts.props.fps))
        one_second = cached_encode(
            cache,
            f"gop|{facts.label}|{one_second_frames}|{quality:.1f}",
            encode_factory(
                facts=facts,
                encoder="hevc_nvenc",
                quality=quality,
                out_dir=out_dir,
                out_name=f"c{facts.index}_gop1s.mp4",
                runner=runner,
                ffmpeg=ffmpeg,
                gop_frames=one_second_frames,
            ),
        )
        default_gop = cached_encode(
            cache,
            f"gop|{facts.label}|{DEFAULT_GOP_FRAMES}|{quality:.1f}",
            encode_factory(
                facts=facts,
                encoder="hevc_nvenc",
                quality=quality,
                out_dir=out_dir,
                out_name=f"c{facts.index}_gop{DEFAULT_GOP_FRAMES}.mp4",
                runner=runner,
                ffmpeg=ffmpeg,
                gop_frames=DEFAULT_GOP_FRAMES,
            ),
        )
        measured: float | None = None
        if default_gop["returncode"] == 0 and default_gop["bytes"] > 0 and one_second["returncode"] == 0:
            measured = round(one_second["bytes"] / default_gop["bytes"] - 1.0, 4)
        model_ratio = calc_keyframe_increase_ratio(
            facts.props.keyframe_interval,
            config.thresholds.keyframe_1s_increase_rate,
            fps=facts.props.fps,
            base_fps=config.bitrate_model.base_fps,
        )
        gop_records.append(
            {
                "clip": facts.label,
                "source_keyframe_sec": facts.props.keyframe_interval,
                "fps": facts.props.fps,
                "one_second_frames": one_second_frames,
                "model_increase": round(model_ratio, 4),
                "measured_increase": measured,
                "increase_rate_setting": config.thresholds.keyframe_1s_increase_rate,
            }
        )
        print(f"gop clip={facts.label} model={model_ratio:.4f} measured={measured}", flush=True)

    tier = RESOLUTION_TIER_BY_KEY[GUARD_TIER_KEY]
    maxrate_records: list[dict[str, Any]] = []
    for facts in clips:
        output_size = calc_scaled_dimensions(facts.props.width, facts.props.height, tier)
        value = cached_encode(
            cache,
            f"maxrate|{facts.label}|{GUARD_TIER_KEY}|{quality:.1f}",
            encode_factory(
                facts=facts,
                encoder="hevc_nvenc",
                quality=quality,
                out_dir=out_dir,
                out_name=f"c{facts.index}_maxrate.mp4",
                runner=runner,
                ffmpeg=ffmpeg,
                scale_filter=build_scale_expression(tier),
                output_size=output_size,
                maxrate_kbps=tier.max_bitrate_kbps,
            ),
        )
        actual_kbps: float | None = None
        if value["returncode"] == 0 and facts.props.duration > 0:
            actual_kbps = round(value["bytes"] * 8 / facts.props.duration / 1000, 1)
        headroom = (
            round(tier.max_bitrate_kbps / actual_kbps, 3) if actual_kbps is not None and actual_kbps > 0 else None
        )
        maxrate_records.append(
            {
                "clip": facts.label,
                "maxrate_kbps": tier.max_bitrate_kbps,
                "output_size": list(output_size),
                "actual_kbps": actual_kbps,
                "headroom_ratio": headroom,
                "warn": headroom is not None and headroom < MAXRATE_HEADROOM_WARN_RATIO,
            }
        )
        print(f"maxrate clip={facts.label} actual={actual_kbps} headroom={headroom}", flush=True)

    blocked: list[dict[str, Any]] = []
    for facts in clips:
        width, height = facts.props.width, facts.props.height
        orientation = orientation_of(width, height)
        for res_tier in RESOLUTION_TIERS:
            box_width, box_height = res_tier.box(orientation)
            if width <= box_width and height <= box_height:
                continue
            if is_meaningful_downscale(width, height, res_tier):
                continue
            out_width, out_height = calc_scaled_dimensions(width, height, res_tier)
            blocked.append(
                {
                    "clip": facts.label,
                    "tier": res_tier.key,
                    "area_ratio": round(out_width * out_height / (width * height), 4),
                }
            )
    return {
        "gop": gop_records,
        "maxrate": maxrate_records,
        "downscale_guard": {
            "threshold": float(MIN_DOWNSCALE_AREA_RATIO),
            "blocked": blocked,
        },
    }


DEFAULT_CLIPS_BY_MODE: dict[str, tuple[str, ...]] = {
    "k": DEFAULT_K_CLIPS,
    "decimal": DEFAULT_DECIMAL_CLIPS,
    "ratio": (),
    "guards": DEFAULT_GUARD_CLIPS,
}


def split_list(value: str) -> tuple[str, ...]:
    """쉼표 구분 인자를 비어 있지 않은 항목 목록으로 변환"""
    return tuple(part.strip() for part in value.split(",") if part.strip())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """명령줄 인자 해석"""
    parser = argparse.ArgumentParser(description="4a-1 인코더 척도와 판정 모델 보정 실측")
    parser.add_argument("--clips-dir", type=Path, default=None, help="실측 클립 폴더(읽기만 함)")
    parser.add_argument("--out-dir", type=Path, default=None, help="결과와 캐시를 쓸 폴더(기본 시스템 임시 폴더)")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg 실행 파일")
    parser.add_argument("--ffprobe", default="ffprobe", help="ffprobe 실행 파일")
    parser.add_argument("--mode", choices=("k", "decimal", "ratio", "guards", "all"), default="all")
    parser.add_argument("--encoders", default=",".join(DEFAULT_ENCODERS), help="쉼표 구분 인코더 이름")
    parser.add_argument("--clips", default="", help="쉼표 구분 클립 이름 접두사, 비면 모드 기본값")
    parser.add_argument("--cpu-clips", default=",".join(DEFAULT_CPU_RATIO_CLIPS), help="CPU 인코더 비율 측정 클립 접두사")
    parser.add_argument("--limit", type=int, default=0, help="선택한 클립 수 상한(0은 전체)")
    parser.add_argument("--cleanup", action="store_true", help="출력 폴더를 지우고 끝냄")
    return parser.parse_args(argv)


def default_out_dir() -> Path:
    """시스템 임시 폴더 아래 기본 출력 경로"""
    return Path(tempfile.gettempdir(), "video_scripts_calibration_4a1")


def run_mode(
    mode_name: str,
    *,
    clips_dir: Path,
    overrides: tuple[str, ...],
    cpu_prefixes: tuple[str, ...],
    limit: int,
    encoders: tuple[str, ...],
    out_dir: Path,
    cache: Cache,
    runner: ScriptRunner,
    ffmpeg: str,
    ffprobe: str,
) -> dict[str, Any]:
    """모드 하나를 실행하고 결과 요약을 반환"""
    prefixes = overrides or DEFAULT_CLIPS_BY_MODE[mode_name]
    paths = select_clips(clips_dir, prefixes, limit)
    if not paths:
        print(f"클립을 찾지 못했습니다: mode={mode_name}", file=sys.stderr)
        return {}
    clips = [load_clip(path, index, ffprobe) for index, path in enumerate(paths)]
    print(f"모드 {mode_name}: 클립 {len(clips)}개", flush=True)
    if mode_name == "k":
        return run_k_mode(clips, encoders, out_dir, cache, runner, ffmpeg)
    if mode_name == "decimal":
        return run_decimal_mode(clips, encoders, out_dir, cache, runner, ffmpeg)
    if mode_name == "ratio":
        return run_ratio_mode(clips, encoders, out_dir, cache, runner, ffmpeg, () if overrides else cpu_prefixes)
    return run_guards_mode(clips, out_dir, cache, runner, ffmpeg)


def main(argv: list[str] | None = None) -> int:
    """CLI 진입점"""
    args = parse_args(argv)
    out_dir: Path = args.out_dir if args.out_dir is not None else default_out_dir()
    if args.cleanup:
        shutil.rmtree(out_dir, ignore_errors=True)
        print(f"정리 완료: {out_dir}")
        return 0
    if args.clips_dir is None:
        print("--clips-dir이 필요합니다", file=sys.stderr)
        return 2
    if not args.clips_dir.is_dir():
        print(f"클립 폴더가 없습니다: {args.clips_dir}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)
    cache = Cache(out_dir / CACHE_NAME)
    runner = ScriptRunner()
    encoders = split_list(args.encoders)
    overrides = split_list(args.clips)
    cpu_prefixes = split_list(args.cpu_clips)
    modes = ("k", "decimal", "ratio", "guards") if args.mode == "all" else (args.mode,)
    summary: dict[str, Any] = {}
    for mode_name in modes:
        summary[mode_name] = run_mode(
            mode_name,
            clips_dir=args.clips_dir,
            overrides=overrides,
            cpu_prefixes=cpu_prefixes,
            limit=args.limit,
            encoders=encoders,
            out_dir=out_dir,
            cache=cache,
            runner=runner,
            ffmpeg=args.ffmpeg,
            ffprobe=args.ffprobe,
        )
    summary_path = out_dir / SUMMARY_NAME
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"요약 저장: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
