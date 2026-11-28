"""수동 화질 분석 진입 경로의 HDR 프레임 판정 재현 스크립트

- 제품의 `analyze_frames`을 그대로 호출해 후보 등급과 규격별 지표를 출력
- HDR 10-bit PQ 원본과 톤매핑 8-bit SDR 사본, SDR 비교 샘플을 같은 경로로 측정
- 임시 파일은 시스템 temp 폴더에만 두고 원본과 `_ref`는 읽기만 함
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.core.analysis.frame_analysis_runner import analyze_frames
from src.core.encode.ffmpeg_command import FFMPEG_EXECUTABLE_DEFAULT

TONEMAP_FILTER: str = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)


def probe_video(path: str) -> dict[str, str | int | float]:
    """ffprobe로 해상도, 길이, 픽셀 포맷, 색 신호 특성을 읽음"""
    args = [
        "ffprobe",
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=pix_fmt,width,height,color_transfer,color_primaries,color_space",
        "-show_entries",
        "format=duration",
        "-of",
        "json",
        path,
    ]
    data = json.loads(subprocess.run(args, check=True, capture_output=True, text=True).stdout)
    stream = data["streams"][0]
    return {
        "pix_fmt": stream.get("pix_fmt", "unknown"),
        "width": int(stream["width"]),
        "height": int(stream["height"]),
        "color_transfer": stream.get("color_transfer", "unknown"),
        "color_primaries": stream.get("color_primaries", "unknown"),
        "color_space": stream.get("color_space", "unknown"),
        "duration_sec": float(data["format"]["duration"]),
    }


def has_vmaf_filter() -> bool:
    """제품이 쓰는 VMAF 필터 사용 가능 여부"""
    from src.core.analysis.frame_analysis_runner import STAGE_VMAF

    probe = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=64x64:d=0.1",
            "-f",
            "lavfi",
            "-i",
            "color=black:s=64x64:d=0.1",
            "-filter_complex",
            "[0][1]libvmaf",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
    )
    del STAGE_VMAF
    return probe.returncode == 0


def make_tone_mapped_copy(source: str, work_dir: Path) -> str:
    """HDR 원본을 톤매핑해 8-bit SDR 사본을 temp에 만듦"""
    out_path = str(Path(work_dir, "tone_mapped_sdr.mp4"))
    args = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        source,
        "-vf",
        TONEMAP_FILTER,
        "-c:v",
        "libx264",
        "-crf",
        "16",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-an",
        out_path,
    ]
    subprocess.run(args, check=True)
    return out_path


def run_analysis(path: str) -> None:
    """제품 진입점으로 한 파일을 측정하고 후보 등급과 지표 표를 출력"""
    info = probe_video(path)
    print(f"\n=== {Path(path).name} ===")
    print(
        f"  {info['width']}x{info['height']} pix_fmt={info['pix_fmt']} "
        f"transfer={info['color_transfer']} primaries={info['color_primaries']} "
        f"space={info['color_space']}"
    )
    with tempfile.TemporaryDirectory(prefix="hdr_investigate_") as work_dir:
        result = analyze_frames(
            source_path=path,
            width=int(info["width"]),
            height=int(info["height"]),
            duration_sec=float(info["duration_sec"]),
            work_dir=work_dir,
            ffmpeg_executable=FFMPEG_EXECUTABLE_DEFAULT,
            has_vmaf=has_vmaf_filter(),
        )
    print(f"  candidate_tier={result.candidate_tier}")
    print(f"  metric_used={result.metric_used} active={result.active_region}")
    print("  tier  ssim       vmaf")
    for score in result.tier_scores:
        ssim = f"{score.ssim:.6f}" if score.ssim is not None else "-"
        vmaf = f"{score.vmaf:.3f}" if score.vmaf is not None else "-"
        print(f"  {score.tier:<6}{ssim}  {vmaf}")


def main() -> None:
    """명령줄 인자를 받아 측정을 실행"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="+", help="측정할 영상 파일 경로")
    parser.add_argument("--tone-map-first", action="store_true", help="첫 파일을 톤매핑해 SDR 사본도 측정")
    args = parser.parse_args()
    first = args.sources[0]
    run_analysis(first)
    if args.tone_map_first:
        with tempfile.TemporaryDirectory(prefix="hdr_investigate_") as tmp:
            sdr_copy = make_tone_mapped_copy(first, Path(tmp))
            run_analysis(sdr_copy)
    for path in args.sources[1:]:
        run_analysis(path)


if __name__ == "__main__":
    sys.exit(main())
