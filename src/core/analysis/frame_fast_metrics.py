"""화질 분석 2단계 1패스 다중 규격 split SSIM 명령 구성과 로그 수집 모듈

- 한 번의 입력 디코딩으로 대상 규격들의 축소 왕복 SSIM 로그를 모두 만들어 3.22배 단축(12.19절)
- 규격별 로그 이름에 측정 지점 번호를 넣어 여러 영상이나 지점을 병렬로 재도 파일이 겹치지 않게 함
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.hdr_tone_map import tonemap_graph_prefix
from src.core.quality_probe import SsimStats, parse_ssim_stats, ssim_db_from_score
from src.core.resolution_tiers import (
    DEFAULT_SCALE_ALGORITHM,
    ResolutionTier,
    calc_scaled_dimensions,
    scale_filter,
)

SSIM_LOG_SUFFIX: str = ".log"
VMAF_LOG_SUFFIX: str = ".json"


def ssim_log_name(point_index: int, tier_key: str) -> str:
    """측정 지점과 규격별 SSIM 로그 파일 이름"""
    return f"ssim_{point_index}_{tier_key}{SSIM_LOG_SUFFIX}"


def vmaf_log_name(point_index: int) -> str:
    """측정 지점별 VMAF 로그 파일 이름"""
    return f"vmaf_{point_index}{VMAF_LOG_SUFFIX}"


def clear_metric_logs(work_dir: str, names: Sequence[str]) -> None:
    """이전 실행의 지표 로그를 지워 실패한 명령의 옛 점수를 읽지 않게 함"""
    for name in names:
        path = Path(work_dir, name)
        if path.exists():
            path.unlink()


def _crop_prefix(active_region: ActiveRegion) -> str:
    """검은 띠가 있으면 입력에 먼저 적용할 crop 필터 문자열"""
    if not active_region.has_black_bars:
        return ""
    return f"crop={active_region.width}:{active_region.height}:{active_region.x}:{active_region.y},"


def _build_tier_chain(
    active_region: ActiveRegion,
    tier: ResolutionTier,
    *,
    input_label: str,
    log_name: str,
    index: int,
    algorithm: str = DEFAULT_SCALE_ALGORITHM,
) -> str:
    """규격 하나의 축소 왕복과 SSIM 필터 문자열

    - 보간 방식은 인코딩 축소와 같은 값을 받아 예측과 실제 결과를 맞춤
    """
    out_width, out_height = calc_scaled_dimensions(active_region.width, active_region.height, tier)
    scaled = f"[d{index}]{scale_filter(str(out_width), str(out_height), algorithm)},"
    restored = (
        f"{scale_filter(str(active_region.width), str(active_region.height), algorithm)}[s{index}]"
    )
    return f"{scaled}{restored};{input_label}[s{index}]ssim=stats_file={log_name}[o{index}]"


def build_split_ssim_command(
    *,
    ffmpeg_executable: str,
    source_path: str,
    start_sec: float,
    duration_sec: float,
    active_region: ActiveRegion,
    tiers: Sequence[ResolutionTier],
    log_names: Mapping[str, str],
    fast_keyframe_seek: bool,
    algorithm: str = DEFAULT_SCALE_ALGORITHM,
    tonemap: bool = False,
) -> list[str]:
    """대상 규격 전체의 축소 왕복 SSIM을 한 번의 디코딩으로 재는 FFmpeg 인자 목록

    - HDR이면 `split` 앞에서 SDR 톤 매핑을 거쳐 기준과 축소본이 같은 변환 결과로 비교되게 함
    """
    count = len(tiers)
    parts: list[str] = []
    prefix = f"[0:v]{tonemap_graph_prefix(hdr=tonemap)}{_crop_prefix(active_region)}"
    distorted_labels = "".join(f"[d{index}]" for index in range(count))
    if count == 1:
        parts.append(f"{prefix}split=2[ref]{distorted_labels}")
        reference_labels = ["[ref]"]
    else:
        parts.append(f"{prefix}split={count + 1}[base]{distorted_labels}")
        reference_labels = [f"[r{index}]" for index in range(count)]
        parts.append(f"[base]split={count}{''.join(reference_labels)}")
    for index, tier in enumerate(tiers):
        parts.append(
            _build_tier_chain(
                active_region,
                tier,
                input_label=reference_labels[index],
                log_name=log_names[tier.key],
                index=index,
                algorithm=algorithm,
            )
        )
    args = [
        ffmpeg_executable,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{start_sec:.3f}",
    ]
    if fast_keyframe_seek:
        args.append("-noaccurate_seek")
    args.extend(["-i", source_path, "-t", f"{duration_sec:.3f}", "-filter_complex", ";".join(parts)])
    for index in range(count):
        args.extend(["-map", f"[o{index}]"])
    args.extend(["-an", "-f", "null", "-"])
    return args


def _frame_score(stats: SsimStats, index: int) -> float | None:
    """프레임 번호의 점수. 같은 화면(inf)이거나 프레임 수를 넘으면 None"""
    if index >= len(stats.frame_scores):
        return None
    return stats.frame_scores[index]


def moving_score_stats(stats_by_tier: Mapping[str, SsimStats]) -> dict[str, SsimStats]:
    """규격별 SSIM에서 같은 화면이 아닌 프레임만 모은 공통 모집단으로 다시 계산한 점수

    - 모집단은 측정한 규격 중 한 곳에서라도 같은 화면이 아닌 프레임 전체
    - 어떤 규격에서 같은 화면인 프레임은 그 규격에서 점수 1로 넣어 규격 사이 모집단을 맞춤
      (같은 화면 프레임이 평균을 끌어올려 움직이는 프레임의 손실을 통과시키지 않게 함)
    - 모든 규격에서 모든 프레임이 같은 화면이면 빈 사전(정지 지점)
    """
    frames = list(stats_by_tier.values())
    if not frames:
        return {}
    length = max(len(stats.frame_scores) for stats in frames)
    moving = [
        index
        for index in range(length)
        if any(_frame_score(stats, index) is not None for stats in frames)
    ]
    if not moving:
        return {}
    population: dict[str, SsimStats] = {}
    for tier_key, stats in stats_by_tier.items():
        values: list[float] = []
        for index in moving:
            score = _frame_score(stats, index)
            values.append(1.0 if score is None else score)
        mean_score = sum(values) / len(values)
        population[tier_key] = SsimStats(mean_score, ssim_db_from_score(mean_score), tuple(values))
    return population


def parse_ssim_logs(work_dir: str, log_names: Mapping[str, str]) -> dict[str, SsimStats]:
    """규격별 SSIM 로그에서 점수, dB, 프레임별 점수를 읽음. 없는 로그는 결과에서 뺌

    - 같은 화면 프레임을 포함한 규격별 평균과 함께 프레임별 점수를 남겨, 호출자가
      움직이는 프레임만 모은 공통 모집단(`moving_score_stats`)으로 다시 계산하게 함
    """
    scores: dict[str, SsimStats] = {}
    for tier_key, name in log_names.items():
        path = Path(work_dir, name)
        if not path.is_file():
            continue
        stats = parse_ssim_stats(path.read_text(encoding="utf-8", errors="replace"))
        if stats is not None:
            scores[tier_key] = stats
    return scores
