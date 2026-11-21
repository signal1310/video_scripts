"""4단계 4a-2 화질 분석 기준값, 동일 VMAF 매핑, 결정 25/27 실측 스크립트

- 클립 폴더와 출력 폴더는 인자로만 받고 `_ref`는 읽기만 함
- 세그먼트 인코딩은 `build_encode_plan`과 `build_sample_ffmpeg_args`를 재사용
- 왕복 축소 손실은 디코딩, 스케일, 지표만 한 번에 수행(결정 19)
- 사용 예(경로는 모두 인자로 받음):
  python -I -X utf8 scripts/measure_frame_analysis.py --clips-dir <클립 폴더> --mode vmaf
  python -I -X utf8 scripts/measure_frame_analysis.py --clips-dir <클립 폴더> --mode roundtrip
  python -I -X utf8 scripts/measure_frame_analysis.py --clips-dir <클립 폴더> --mode level
  python -I -X utf8 scripts/measure_frame_analysis.py --clips-dir <클립 폴더> --mode vr
  python -I -X utf8 scripts/measure_frame_analysis.py --cleanup
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import statistics
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.frame_analysis_common import (  # noqa: E402
    ClipFacts,
    Env,
    MetricValue,
)

from src.core.analysis.prober import run_ffprobe  # noqa: E402
from src.core.config import CONTAINER_POLICY_KEEP, CONTAINER_POLICY_MP4  # noqa: E402
from src.core.encode.stream_layout import parse_stream_layout  # noqa: E402
from src.core.encoder_specs import get_encoder_spec  # noqa: E402
from src.core.quality_probe import (  # noqa: E402
    LOG_NAME_SSIM,
    LOG_NAME_VMAF,
    METRIC_SSIM,
    METRIC_VMAF,
    build_metric_filter,
    parse_ssim_stats,
    parse_vmaf_log,
    vmaf_model_for,
)
from src.core.resolution_tiers import (  # noqa: E402
    RESOLUTION_TIER_BY_KEY,
    ResolutionTier,
    calc_scaled_dimensions,
)

SEGMENT_START_SEC: float = 10.0
VMAF_QUALITY_PROBES: tuple[float, ...] = (22.0, 26.0)
SVT_QUALITY_PROBES: tuple[float, ...] = (22.0, 30.0)
ANCHOR_QUALITIES: tuple[float, ...] = (22.0, 26.0)
VMAF_SEGMENT_SEC: float = 6.0
ROUNDTRIP_SEGMENT_SEC: float = 2.0
ROUNDTRIP_STARTS: tuple[float, ...] = (10.0, 30.0)
ROUNDTRIP_TIER_KEYS: tuple[str, ...] = ("1440p", "1080p", "720p", "480p")
DEFAULT_ENCODERS: tuple[str, ...] = ("libx265", "h264_nvenc", "libx264", "libsvtav1")
ROUNDTRIP_CLIPS: tuple[str, ...] = ("video1", "video2", "video3", "video7", "video13")
LEVEL_CLIPS: tuple[str, ...] = ("video10", "video11")
LEVEL_TARGET: str = "6.2"
FAILURE_TAIL_CHARS: int = 400


def encode_and_measure(
    env: Env,
    facts: ClipFacts,
    encoder: str,
    quality: float,
    reference: Path,
    *,
    start_sec: float,
    duration_sec: float,
    tag: str,
    level: str | None = None,
) -> dict[str, Any]:
    """구간 인코딩과 VMAF 측정을 한 번에 수행"""
    out_name = f"seg_{facts.index}_{tag}.mp4"
    size, outcome = env.encode_segment(
        facts,
        encoder,
        quality,
        start_sec=start_sec,
        duration_sec=duration_sec,
        out_name=out_name,
        level=level,
    )
    metric = MetricValue(None, None, 0.0, outcome.returncode)
    if size > 0:
        metric = env.metric(
            distorted=Path(env.out_dir, out_name),
            reference=reference,
            metric=METRIC_VMAF,
            model=vmaf_model_for(facts.props.width, facts.props.height),
        )
    return {
        "quality": quality,
        "bytes": size,
        "vmaf": metric.score,
        "seconds": round(outcome.seconds, 2),
        "returncode": outcome.returncode,
        "stderr": "" if outcome.returncode == 0 else outcome.stderr[-FAILURE_TAIL_CHARS:],
    }


def solve_quality_for_vmaf(points: list[dict[str, Any]], target: float) -> float | None:
    """두 측정점의 VMAF 직선에서 목표 점수가 되는 품질값 추정"""
    valid = [(float(point["quality"]), float(point["vmaf"])) for point in points if point["vmaf"] is not None]
    if len(valid) < 2 or valid[0][0] == valid[1][0]:
        return None
    (q1, v1), (q2, v2) = valid[0], valid[1]
    slope = (v2 - v1) / (q2 - q1)
    if slope == 0:
        return None
    return q1 + (target - v1) / slope


def measure_vmaf_mapping(env: Env, clips: list[ClipFacts], encoders: tuple[str, ...]) -> dict[str, Any]:
    """인코더별 동일 VMAF 품질값과 출력 크기 비율 측정"""
    records: list[dict[str, Any]] = []
    for facts in clips:
        reference = env.reference_segment(facts, SEGMENT_START_SEC, VMAF_SEGMENT_SEC)
        if reference is None:
            records.append({"clip": facts.label, "error": "reference"})
            continue
        anchors: dict[str, dict[str, Any]] = {}
        for quality in ANCHOR_QUALITIES:
            anchors[f"{quality:.1f}"] = encode_and_measure(
                env,
                facts,
                "hevc_nvenc",
                quality,
                reference,
                start_sec=SEGMENT_START_SEC,
                duration_sec=VMAF_SEGMENT_SEC,
                tag=f"anchor_{quality:.1f}",
            )
        probes: dict[str, list[dict[str, Any]]] = {}
        for encoder in encoders:
            probe_qualities = SVT_QUALITY_PROBES if encoder == "libsvtav1" else VMAF_QUALITY_PROBES
            probes[encoder] = [
                encode_and_measure(
                    env,
                    facts,
                    encoder,
                    quality,
                    reference,
                    start_sec=SEGMENT_START_SEC,
                    duration_sec=VMAF_SEGMENT_SEC,
                    tag=f"{encoder}_{quality:.1f}",
                )
                for quality in probe_qualities
            ]
        matches: list[dict[str, Any]] = []
        for encoder, points in probes.items():
            spec = get_encoder_spec(encoder)
            q_min = spec.quality_min if spec else 0.0
            q_max = spec.quality_max if spec else 51.0
            integer_scale = spec.integer_scale if spec else False
            for anchor_name, anchor in anchors.items():
                target = anchor.get("vmaf")
                if target is None:
                    continue
                estimate = solve_quality_for_vmaf(points, float(target))
                if estimate is None:
                    continue
                quality = min(max(estimate, q_min), q_max)
                quality = float(round(quality)) if integer_scale else round(quality, 1)
                result = encode_and_measure(
                    env,
                    facts,
                    encoder,
                    quality,
                    reference,
                    start_sec=SEGMENT_START_SEC,
                    duration_sec=VMAF_SEGMENT_SEC,
                    tag=f"{encoder}_match_{anchor_name}",
                )
                size_ratio = (
                    result["bytes"] / anchor["bytes"] if anchor["bytes"] and result["bytes"] else None
                )
                matches.append(
                    {
                        "encoder": encoder,
                        "anchor_quality": anchor_name,
                        "target_vmaf": target,
                        "quality": quality,
                        "vmaf": result["vmaf"],
                        "vmaf_delta": (
                            round(float(result["vmaf"]) - float(target), 2)
                            if result["vmaf"] is not None
                            else None
                        ),
                        "bytes": result["bytes"],
                        "size_ratio_vs_anchor": round(size_ratio, 3) if size_ratio is not None else None,
                    }
                )
                print(
                    f"vmaf {facts.label} {encoder} anchor={anchor_name} q={quality} "
                    f"vmaf={result['vmaf']} ratio={matches[-1]['size_ratio_vs_anchor']}",
                    flush=True,
                )
        records.append(
            {
                "clip": facts.label,
                "width": facts.props.width,
                "height": facts.props.height,
                "anchors": anchors,
                "probes": probes,
                "matches": matches,
            }
        )
    return {"records": records}



def available_tiers(facts: ClipFacts) -> list[ResolutionTier]:
    """원본보다 작아지는 규격만 반환"""
    tiers: list[ResolutionTier] = []
    for key in ROUNDTRIP_TIER_KEYS:
        tier = RESOLUTION_TIER_BY_KEY[key]
        out_width, out_height = calc_scaled_dimensions(facts.props.width, facts.props.height, tier)
        if (out_width, out_height) != (facts.props.width, facts.props.height):
            tiers.append(tier)
    return tiers


def make_synthetic_clips(env: Env) -> list[ClipFacts]:
    """검은 화면, 단색, 고주파 패턴 합성 클립 생성"""
    specs = (
        ("black", "color=c=black:s=1280x720:r=30:d=6"),
        ("solid", "color=c=0x336699:s=1280x720:r=30:d=6"),
        ("pattern", "testsrc2=s=1280x720:r=30:d=6"),
    )
    clips: list[ClipFacts] = []
    for index, (name, source) in enumerate(specs, start=100):
        path = Path(env.out_dir, f"synthetic_{name}.mp4")
        if not path.exists():
            args = [
                env.ffmpeg,
                "-hide_banner",
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                source,
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-pix_fmt",
                "yuv420p",
                str(path),
            ]
            env.encode_runner(args, str(env.out_dir))
        if path.exists():
            clips.append(env.load(path, index))
    return clips


def roundtrip_starts_for(facts: ClipFacts) -> tuple[float, ...]:
    """짧은 합성 클립은 시작 0에서만 잼"""
    return (0.0,) if facts.props.duration <= 8.0 else ROUNDTRIP_STARTS


def median_or_none(values: list[float]) -> float | None:
    """값 목록의 중앙값, 없으면 None"""
    return round(statistics.median(values), 2) if values else None


def measure_roundtrip(env: Env, clips: list[ClipFacts]) -> dict[str, Any]:
    """규격별 왕복 VMAF/SSIM과 검은 화면 비율 측정"""
    records: list[dict[str, Any]] = []
    for facts in clips:
        starts = roundtrip_starts_for(facts)
        black = env.black_ratio(facts, starts[0], ROUNDTRIP_SEGMENT_SEC)
        rows: list[dict[str, Any]] = []
        for tier in available_tiers(facts):
            vmaf_values: list[float] = []
            ssim_values: list[float] = []
            db_values: list[float] = []
            vmaf_seconds: list[float] = []
            ssim_seconds: list[float] = []
            for start in starts:
                vmaf = env.roundtrip_metric(
                    facts,
                    tier,
                    start_sec=start,
                    duration_sec=ROUNDTRIP_SEGMENT_SEC,
                    metric=METRIC_VMAF,
                )
                ssim = env.roundtrip_metric(
                    facts,
                    tier,
                    start_sec=start,
                    duration_sec=ROUNDTRIP_SEGMENT_SEC,
                    metric=METRIC_SSIM,
                )
                if vmaf.score is not None:
                    vmaf_values.append(vmaf.score)
                    vmaf_seconds.append(vmaf.seconds)
                if ssim.score is not None:
                    ssim_values.append(ssim.score)
                    ssim_seconds.append(ssim.seconds)
                    if ssim.db is not None:
                        db_values.append(ssim.db)
            out_width, out_height = calc_scaled_dimensions(facts.props.width, facts.props.height, tier)
            rows.append(
                {
                    "tier": tier.key,
                    "output": [out_width, out_height],
                    "vmaf": median_or_none(vmaf_values),
                    "ssim": median_or_none(ssim_values),
                    "ssim_db": median_or_none(db_values),
                    "vmaf_seconds": median_or_none(vmaf_seconds),
                    "ssim_seconds": median_or_none(ssim_seconds),
                }
            )
            print(
                f"roundtrip {facts.label} {tier.key} vmaf={rows[-1]['vmaf']} "
                f"ssim={rows[-1]['ssim']} db={rows[-1]['ssim_db']}",
                flush=True,
            )
        records.append(
            {
                "clip": facts.label,
                "width": facts.props.width,
                "height": facts.props.height,
                "black_ratio": black,
                "tiers": rows,
            }
        )
    return {"records": records}



def measure_with_skip(
    env: Env,
    *,
    distorted: Path,
    reference: Path,
    metric: str,
    model: str,
) -> MetricValue:
    """키프레임만 디코딩(-skip_frame nokey)하고 지표를 잼"""
    log_name = LOG_NAME_VMAF if metric == METRIC_VMAF else LOG_NAME_SSIM
    log_path = Path(env.out_dir, log_name)
    if log_path.exists():
        log_path.unlink()
    args = [
        env.ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-skip_frame",
        "nokey",
        "-i",
        str(distorted),
        "-skip_frame",
        "nokey",
        "-i",
        str(reference),
        "-lavfi",
        build_metric_filter(metric, model=model, log_name=log_name),
        "-f",
        "null",
        "-",
    ]
    outcome = env.metric_runner(args, str(env.out_dir))
    if outcome.returncode != 0 or not log_path.exists():
        return MetricValue(None, None, outcome.seconds, outcome.returncode)
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if metric == METRIC_VMAF:
        return MetricValue(parse_vmaf_log(text), None, outcome.seconds, outcome.returncode)
    ssim = parse_ssim_stats(text)
    if ssim is None:
        return MetricValue(None, None, outcome.seconds, outcome.returncode)
    return MetricValue(ssim[0], ssim[1], outcome.seconds, outcome.returncode)


def measure_skip_frame(env: Env, facts: ClipFacts) -> dict[str, Any]:
    """전체 프레임과 키프레임만 디코딩의 점수 편차와 소요 시간 비교"""
    reference = env.reference_segment(facts, ROUNDTRIP_STARTS[0], ROUNDTRIP_SEGMENT_SEC)
    if reference is None:
        return {"clip": facts.label, "error": "reference"}
    out_name = f"skip_{facts.index}.mp4"
    size, outcome = env.encode_segment(
        facts,
        "hevc_nvenc",
        22.0,
        start_sec=ROUNDTRIP_STARTS[0],
        duration_sec=ROUNDTRIP_SEGMENT_SEC,
        out_name=out_name,
    )
    if size <= 0:
        return {"clip": facts.label, "error": outcome.stderr[-FAILURE_TAIL_CHARS:]}
    distorted = Path(env.out_dir, out_name)
    model = vmaf_model_for(facts.props.width, facts.props.height)
    result: dict[str, Any] = {"clip": facts.label, "bytes": size}
    for metric in (METRIC_VMAF, METRIC_SSIM):
        full = env.metric(distorted=distorted, reference=reference, metric=metric, model=model)
        skip = measure_with_skip(env, distorted=distorted, reference=reference, metric=metric, model=model)
        result[metric] = {
            "full_score": full.score,
            "full_db": full.db,
            "skip_score": skip.score,
            "skip_db": skip.db,
            "full_seconds": round(full.seconds, 2),
            "skip_seconds": round(skip.seconds, 2),
        }
        print(f"skipframe {facts.label} {metric} full={full.score} skip={skip.score}", flush=True)
    return result



LEVEL_SIZE_K: float = 5.78


def encode_level_candidate(
    env: Env,
    facts: ClipFacts,
    reference: Path,
    quality: float,
    tag: str,
) -> dict[str, Any] | None:
    """레벨 상한 해제 상태로 후보 품질값 하나를 인코딩하고 VMAF를 잼"""
    out_name = f"seg_{facts.index}_{tag}.mp4"
    size, outcome = env.encode_segment(
        facts,
        "hevc_nvenc",
        quality,
        start_sec=SEGMENT_START_SEC,
        duration_sec=VMAF_SEGMENT_SEC,
        out_name=out_name,
        level=LEVEL_TARGET,
    )
    if size <= 0:
        return None
    metric = env.metric(
        distorted=Path(env.out_dir, out_name),
        reference=reference,
        metric=METRIC_VMAF,
        model=vmaf_model_for(facts.props.width, facts.props.height),
    )
    return {
        "quality": quality,
        "bytes": size,
        "vmaf": metric.score,
        "seconds": round(outcome.seconds, 2),
    }


def measure_level(env: Env, clips: list[ClipFacts]) -> dict[str, Any]:
    """NVENC 레벨 상한 유지와 해제를 같은 크기 VMAF로 비교"""
    records: list[dict[str, Any]] = []
    for facts in clips:
        reference = env.reference_segment(facts, SEGMENT_START_SEC, VMAF_SEGMENT_SEC)
        if reference is None:
            records.append({"clip": facts.label, "error": "reference"})
            continue
        auto = encode_and_measure(
            env,
            facts,
            "hevc_nvenc",
            22.0,
            reference,
            start_sec=SEGMENT_START_SEC,
            duration_sec=VMAF_SEGMENT_SEC,
            tag="level_auto",
        )
        unlocked = encode_and_measure(
            env,
            facts,
            "hevc_nvenc",
            22.0,
            reference,
            start_sec=SEGMENT_START_SEC,
            duration_sec=VMAF_SEGMENT_SEC,
            tag="level_unlocked",
            level=LEVEL_TARGET,
        )
        ratio = unlocked["bytes"] / auto["bytes"] if auto["bytes"] and unlocked["bytes"] else None
        same_size_q = (
            round(22.0 + LEVEL_SIZE_K * math.log2(ratio), 1)
            if ratio is not None and ratio > 0
            else None
        )
        matched: dict[str, Any] | None = None
        if same_size_q is not None:
            matched = encode_level_candidate(env, facts, reference, same_size_q, "level_matched")
            if matched is not None and auto["bytes"] and matched["bytes"]:
                adjust = math.log2(auto["bytes"] / matched["bytes"])
                if abs(adjust) > 0.03:
                    refined_q = round(same_size_q - LEVEL_SIZE_K * adjust, 1)
                    refined = encode_level_candidate(env, facts, reference, refined_q, "level_matched_refined")
                    if refined is not None and abs(refined["bytes"] - auto["bytes"]) < abs(
                        matched["bytes"] - auto["bytes"]
                    ):
                        matched = refined
        records.append(
            {
                "clip": facts.label,
                "width": facts.props.width,
                "height": facts.props.height,
                "level_auto": {
                    "quality": 22.0,
                    "bytes": auto["bytes"],
                    "vmaf": auto["vmaf"],
                    "kbps": round(auto["bytes"] * 8 / VMAF_SEGMENT_SEC / 1000, 1) if auto["bytes"] else None,
                },
                "level_unlocked": {
                    "quality": 22.0,
                    "bytes": unlocked["bytes"],
                    "vmaf": unlocked["vmaf"],
                    "kbps": round(unlocked["bytes"] * 8 / VMAF_SEGMENT_SEC / 1000, 1)
                    if unlocked["bytes"]
                    else None,
                },
                "unlocked_over_auto_size": round(ratio, 3) if ratio is not None else None,
                "same_size_quality": same_size_q,
                "same_size": matched,
            }
        )
        print(
            f"level {facts.label} auto={auto['vmaf']} unlocked={unlocked['vmaf']} "
            f"ratio={records[-1]['unlocked_over_auto_size']} sameq={same_size_q}",
            flush=True,
        )
    return {"records": records}



def probe_side_data_types(env: Env, path: Path) -> list[str]:
    """ffprobe로 주 영상 스트림의 부가 정보 종류를 읽음"""
    probe = run_ffprobe(str(path), ffprobe=env.ffprobe)
    layout = parse_stream_layout(probe)
    primary = layout.primary_video
    return list(primary.side_data_types) if primary is not None else []


def make_stereo3d_source(env: Env) -> Path | None:
    """Stereo 3D 부가 정보가 있는 MKV 시험 입력 생성"""
    mkv = Path(env.out_dir, "stereo3d_src.mkv")
    if not mkv.exists():
        args = [
            env.ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=s=640x320:r=30:d=3",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-metadata:s:v:0",
            "stereo_mode=1",
            str(mkv),
        ]
        env.encode_runner(args, str(env.out_dir))
    return mkv if mkv.exists() else None


def measure_vr(env: Env) -> dict[str, Any]:
    """video2 cropdetect와 합성 Stereo 3D 부가 정보 보존 확인"""
    result: dict[str, Any] = {}
    paths = env.select(("video2",), 1)
    if paths:
        facts = env.load(paths[0], 500)
        crops = [env.cropdetect(facts, start, ROUNDTRIP_SEGMENT_SEC) for start in ROUNDTRIP_STARTS]
        result["video2"] = {
            "width": facts.props.width,
            "height": facts.props.height,
            "side_data": probe_side_data_types(env, facts.path),
            "crops": [list(crop) if crop is not None else None for crop in crops],
            "crop_keeps_frame": [
                crop == (facts.props.width, facts.props.height, 0, 0) for crop in crops if crop is not None
            ],
        }
        print(f"vr video2 crops={result['video2']['crops']} side_data={result['video2']['side_data']}", flush=True)
    source = make_stereo3d_source(env)
    if source is not None:
        input_types = probe_side_data_types(env, source)
        facts = env.load(source, 501)
        outputs: dict[str, Any] = {}
        for suffix, policy in (("mkv", CONTAINER_POLICY_KEEP), ("mp4", CONTAINER_POLICY_MP4)):
            out_name = f"stereo3d_out.{suffix}"
            size, outcome = env.full_encode(facts, "hevc_nvenc", 22.0, out_name, policy)
            out_path = Path(env.out_dir, out_name)
            output_types = probe_side_data_types(env, out_path) if size > 0 else []
            data = out_path.read_bytes() if size > 0 else b""
            outputs[suffix] = {
                "output_side_data": output_types,
                "boxes": [name for name in ("st3d", "sv3d") if name.encode() in data],
                "bytes": size,
                "returncode": outcome.returncode,
            }
            print(
                f"vr stereo3d {suffix} in={input_types} out={output_types} "
                f"boxes={outputs[suffix]['boxes']}",
                flush=True,
            )
        result["stereo3d"] = {"input_side_data": input_types, "outputs": outputs}
    return result



def split_list(value: str) -> tuple[str, ...]:
    """쉼표 구분 인자를 목록으로 변환"""
    return tuple(part.strip() for part in value.split(",") if part.strip())


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """명령줄 인자 해석"""
    parser = argparse.ArgumentParser(description="4a-2 화질 분석 기준값, 동일 VMAF 매핑, 결정 25/27 실측")
    parser.add_argument("--clips-dir", type=Path, default=None, help="실측 클립 폴더(읽기만 함)")
    parser.add_argument("--out-dir", type=Path, default=None, help="출력 폴더(기본 시스템 임시 폴더)")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg 실행 파일")
    parser.add_argument("--ffprobe", default="ffprobe", help="ffprobe 실행 파일")
    parser.add_argument(
        "--mode",
        choices=("vmaf", "roundtrip", "skip", "level", "vr", "all"),
        default="all",
    )
    parser.add_argument("--encoders", default=",".join(DEFAULT_ENCODERS), help="쉼표 구분 인코더 이름")
    parser.add_argument("--clips", default="", help="쉼표 구분 클립 이름 접두사, 비면 모드 기본값")
    parser.add_argument("--limit", type=int, default=0, help="선택한 클립 수 상한(0은 전체)")
    parser.add_argument("--cleanup", action="store_true", help="출력 폴더를 지우고 끝냄")
    return parser.parse_args(argv)


def default_out_dir() -> Path:
    """시스템 임시 폴더 아래 기본 출력 경로"""
    return Path(tempfile.gettempdir(), "video_scripts_frame_analysis_4a2")


def load_clips(env: Env, prefixes: tuple[str, ...], limit: int = 0) -> list[ClipFacts]:
    """접두사에 맞는 클립을 순서대로 로드"""
    return [env.load(path, index) for index, path in enumerate(env.select(prefixes, limit))]


def run_mode(mode: str, env: Env, overrides: tuple[str, ...], encoders: tuple[str, ...]) -> dict[str, Any]:
    """모드 하나를 실행하고 결과 요약 반환"""
    if mode == "vmaf":
        clips = load_clips(env, overrides or ("video7", "video13"))
        return measure_vmaf_mapping(env, clips, encoders)
    if mode == "roundtrip":
        clips = load_clips(env, overrides or ROUNDTRIP_CLIPS)
        clips += make_synthetic_clips(env)
        return measure_roundtrip(env, clips)
    if mode == "skip":
        return {"records": [measure_skip_frame(env, facts) for facts in load_clips(env, overrides or ("video7",), 1)]}
    if mode == "level":
        return measure_level(env, load_clips(env, overrides or LEVEL_CLIPS))
    return measure_vr(env)


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
    env = Env(clips_dir=args.clips_dir, out_dir=out_dir, ffmpeg=args.ffmpeg, ffprobe=args.ffprobe)
    overrides = split_list(args.clips)
    encoders = split_list(args.encoders)
    modes = (
        ("vmaf", "roundtrip", "skip", "level", "vr") if args.mode == "all" else (args.mode,)
    )
    summary: dict[str, Any] = {}
    for mode in modes:
        summary[mode] = run_mode(mode, env, overrides, encoders)
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"요약 저장: {summary_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

