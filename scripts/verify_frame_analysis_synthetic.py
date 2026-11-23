"""합성 lavfi 영상으로 화질 분석 코어, 실행기, 서비스, SQLite 캐시 통합 검증

- 실제 FFmpeg로 합성 영상 5종을 만들고 실제 `FrameAnalysisService`로 분석
- 출력 폴더는 인자로 받고 기본은 시스템 임시 폴더 아래 새 폴더, 로컬 경로를 쓰지 않음
- 사용 예(경로는 인자로 받음):
  python -I -X utf8 scripts/verify_frame_analysis_synthetic.py --cleanup
  python -I -X utf8 scripts/verify_frame_analysis_synthetic.py --ffmpeg <FFmpeg 경로> --out-dir <출력 폴더>
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.core.analysis.frame_analysis_runner import FrameAnalysisResult  # noqa: E402
from src.core.encode.ffmpeg_capability import probe_ffmpeg_capability  # noqa: E402
from src.service.frame_analysis_service import (  # noqa: E402
    FrameAnalysisRequest,
    FrameAnalysisService,
    FrameAnalysisStore,
)

VIDEO_DURATION_SEC: float = 25.0
FRAME_RATE: int = 30
ENCODE_TIMEOUT_SEC: float = 300.0
ANALYSIS_TIMEOUT_SEC: float = 300.0
FAILURE_TAIL_CHARS: int = 400
CACHE_FILENAME: str = "verification.sqlite3"
OUT_DIR_PREFIX: str = "frame_analysis_synthetic_"


@dataclass(frozen=True, slots=True)
class SyntheticCase:
    """합성 영상 한 개의 생성 입력과 수용 기준"""

    name: str
    lavfi_source: str
    width: int
    height: int
    video_filter: str = ""
    expect_unmeasurable: bool = False
    expect_black_bars: bool | None = None
    expect_active: tuple[int, int] | None = None
    expect_candidate: bool | None = None


SYNTHETIC_CASES: tuple[SyntheticCase, ...] = (
    SyntheticCase(
        name="black",
        lavfi_source=f"color=c=black:s=1280x720:r={FRAME_RATE}:d={VIDEO_DURATION_SEC:.0f}",
        width=1280,
        height=720,
        expect_unmeasurable=True,
    ),
    SyntheticCase(
        name="solid",
        lavfi_source=f"color=c=0x336699:s=1280x720:r={FRAME_RATE}:d={VIDEO_DURATION_SEC:.0f}",
        width=1280,
        height=720,
        expect_unmeasurable=True,
    ),
    SyntheticCase(
        name="pattern",
        lavfi_source=f"testsrc2=s=1920x1080:r={FRAME_RATE}:d={VIDEO_DURATION_SEC:.0f}",
        width=1920,
        height=1080,
        expect_unmeasurable=False,
        expect_black_bars=False,
        expect_candidate=False,
    ),
    SyntheticCase(
        name="letterbox",
        lavfi_source=f"testsrc2=s=720x1080:r={FRAME_RATE}:d={VIDEO_DURATION_SEC:.0f}",
        width=1920,
        height=1080,
        video_filter="pad=1920:1080:(ow-iw)/2:(oh-ih)/2:black",
        expect_unmeasurable=False,
        expect_black_bars=True,
        expect_active=(720, 1080),
    ),
    SyntheticCase(
        name="upscale",
        lavfi_source=f"testsrc2=s=640x360:r={FRAME_RATE}:d={VIDEO_DURATION_SEC:.0f}",
        width=1920,
        height=1080,
        video_filter="scale=1920:1080:flags=bicubic",
        expect_unmeasurable=False,
        expect_candidate=True,
    ),
)


def build_command(ffmpeg: str, case: SyntheticCase, out_path: Path) -> list[str]:
    """합성 영상 생성 FFmpeg 인자 목록"""
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-f",
        "lavfi",
        "-i",
        case.lavfi_source,
    ]
    if case.video_filter:
        command.extend(["-vf", case.video_filter])
    command.extend(["-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p", "-an", str(out_path)])
    return command


def create_video(ffmpeg: str, case: SyntheticCase, out_path: Path) -> str | None:
    """합성 영상 하나를 만들고 실패하면 표준 오류 꼬리를 반환"""
    completed = subprocess.run(
        build_command(ffmpeg, case, out_path),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        timeout=ENCODE_TIMEOUT_SEC,
        check=False,
    )
    if completed.returncode == 0 and out_path.is_file():
        return None
    return completed.stderr.decode("utf-8", errors="replace")[-FAILURE_TAIL_CHARS:]


def build_request(case: SyntheticCase, video_path: Path) -> FrameAnalysisRequest:
    """합성 영상 파일 상태와 알려진 크기, 길이로 분석 요청 생성"""
    stat = video_path.stat()
    return FrameAnalysisRequest(
        filename=video_path.name,
        path=str(video_path),
        width=case.width,
        height=case.height,
        duration_sec=VIDEO_DURATION_SEC,
        filesize_bytes=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def judge_case(case: SyntheticCase, result: FrameAnalysisResult) -> list[str]:
    """수용 기준과 다른 항목의 설명 목록 반환"""
    failures: list[str] = []
    if result.unmeasurable != case.expect_unmeasurable:
        failures.append(f"unmeasurable={result.unmeasurable} (기대 {case.expect_unmeasurable})")
    if case.expect_black_bars is not None and result.active_region.has_black_bars != case.expect_black_bars:
        failures.append(f"has_black_bars={result.active_region.has_black_bars} (기대 {case.expect_black_bars})")
    if case.expect_active is not None:
        active = (result.active_region.width, result.active_region.height)
        if active != case.expect_active:
            failures.append(f"active={active[0]}x{active[1]} (기대 {case.expect_active[0]}x{case.expect_active[1]})")
    if case.expect_candidate is not None and (result.candidate_tier is not None) != case.expect_candidate:
        failures.append(f"candidate_tier={result.candidate_tier} (후보 존재 기대 {case.expect_candidate})")
    if case.expect_unmeasurable and result.valid_segment_count != 0:
        failures.append(f"valid_segments={result.valid_segment_count} (측정 불가인데 유효 구간이 있음)")
    return failures


def _describe(case: SyntheticCase, result: FrameAnalysisResult, failures: Sequence[str]) -> str:
    status = "통과" if not failures else "실패(" + ", ".join(failures) + ")"
    region = result.active_region
    return (
        f"[verify] {case.name}: unmeasurable={result.unmeasurable} "
        f"black_bars={region.has_black_bars} active={region.width}x{region.height} "
        f"candidate={result.candidate_tier} metric={result.metric_used} "
        f"valid={result.valid_segment_count}/{result.total_segment_count} -> {status}"
    )


def _is_under_temp(path: Path) -> bool:
    try:
        path.resolve().relative_to(Path(tempfile.gettempdir()).resolve())
    except ValueError:
        return False
    return True


def cleanup_outputs(out_dir: Path) -> None:
    """검증 산출물을 지움. 임시 폴더 밖 경로는 건드리지 않음"""
    if not _is_under_temp(out_dir):
        print(f"[verify] 임시 폴더 밖 경로라 정리하지 않습니다: {out_dir}")
        return
    shutil.rmtree(out_dir, ignore_errors=True)
    print(f"[verify] 출력 폴더 정리: {out_dir}")


def run_verification(ffmpeg: str, out_dir: Path) -> int:
    """합성 영상 생성, 분석, 수용 기준 확인을 수행하고 실패 건수를 종료 코드로 반환"""
    created: list[tuple[SyntheticCase, Path]] = []
    for case in SYNTHETIC_CASES:
        video_path = out_dir / f"{case.name}.mp4"
        error = create_video(ffmpeg, case, video_path)
        if error is not None:
            print(f"[verify] 합성 영상 생성 실패: {case.name}\n{error}", file=sys.stderr)
            return 1
        created.append((case, video_path))

    capability = probe_ffmpeg_capability(ffmpeg)
    vmaf_note = "VMAF 사용" if capability.has_vmaf else "VMAF 없음, SSIM 단독"
    print(f"[verify] FFmpeg: {capability.executable} ({vmaf_note})")

    store = FrameAnalysisStore(os.path.join(out_dir, CACHE_FILENAME))
    service = FrameAnalysisService(
        store,
        work_dir=str(out_dir),
        ffmpeg_executable=ffmpeg,
        has_vmaf=capability.has_vmaf,
        timeout_sec=ANALYSIS_TIMEOUT_SEC,
    )
    failures = 0
    try:
        for case, video_path in created:
            request = build_request(case, video_path)
            result = service.analyze_batch([request])[request.filename]
            case_failures = judge_case(case, result)
            print(_describe(case, result, case_failures))
            failures += len(case_failures)

        frames = service.load_active_frames([build_request(case, path) for case, path in created])
        expected_crop = sum(1 for case, _path in created if case.expect_black_bars is True)
        if len(frames) == expected_crop:
            print(f"[verify] 캐시 재사용 확인: 잘라내기 확정 활성 화면 {len(frames)}개")
        else:
            print(
                f"[verify] 캐시 재사용 실패: 잘라내기 확정 활성 화면 {len(frames)}/{expected_crop}",
                file=sys.stderr,
            )
            failures += 1
    finally:
        service.close()

    if failures:
        print(f"[verify] 실패 {failures}건", file=sys.stderr)
        return 1
    print(f"[verify] 합성 영상 {len(created)}종 모두 수용 기준 통과")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """인자 해석과 임시 폴더 준비, 검증 뒤 요청 시 정리"""
    parser = argparse.ArgumentParser(description="합성 lavfi 영상으로 화질 분석 통합 검증을 수행합니다.")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="FFmpeg 실행 파일 경로(기본: PATH의 ffmpeg)")
    parser.add_argument("--out-dir", default="", help="출력 폴더(기본: 시스템 임시 폴더 아래 새 폴더)")
    parser.add_argument("--cleanup", action="store_true", help="검증 뒤 임시 폴더 안의 출력 폴더 삭제")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir) if args.out_dir else Path(tempfile.mkdtemp(prefix=OUT_DIR_PREFIX))
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[verify] 출력 폴더: {out_dir}")
    try:
        return run_verification(args.ffmpeg, out_dir)
    finally:
        if args.cleanup:
            cleanup_outputs(out_dir)


if __name__ == "__main__":
    raise SystemExit(main())
