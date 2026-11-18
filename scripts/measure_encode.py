"""재인코딩 실측 스크립트: 샤나 바이트 비교, 배치 처리 시간, 디코딩 구성 비교, 사후 게이트 검사

- 원본 클립은 작업 폴더로 복사한 사본에만 적용하며, 결과는 작업 폴더 안에만 씀. 원본 폴더는 읽기만 함
- 실행은 실제 `EncodeRunner`와 `build_ffmpeg_args`를 사용. 디코딩 구성 비교에만 인자를 바꾸는 프로세스 공장을 주입
- 측정 실행은 사후 게이트를 무결성 기준(KEYFRAME_FIX)으로 두어 결과가 크기 때문에 버려지지 않게 함. 절약 기준 판정은 `gate`가 따로 계산
- 실행 예(경로는 모두 인자로 받음):
  python -I -X utf8 scripts/measure_encode.py prepare --clips <원본 폴더> --work <작업 폴더>
  python -I -X utf8 scripts/measure_encode.py run --work <작업 폴더> --label nvenc_cq22 --concurrency 3 --name cq22_c3
  python -I -X utf8 scripts/measure_encode.py bytes --work <작업 폴더> --run cq22_c3 --shana <샤나 결과 폴더>
  python -I -X utf8 scripts/measure_encode.py gate --work <작업 폴더> --run cq22_c3
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.core.config import RESULT_POLICY_SEPARATE  # noqa: E402
from src.core.encode_gate import GatePurpose, judge_result, summarize_probe  # noqa: E402
from src.core.encode_plan import ACTION_DROP, plan_stream_actions  # noqa: E402
from src.core.encode_process import EncodeProcess  # noqa: E402
from src.core.encode_state import EncodeState  # noqa: E402
from src.core.prober import extract_video_props, run_ffprobe  # noqa: E402
from src.core.resolution_tiers import (  # noqa: E402
    RESOLUTION_TIER_BY_KEY,
    build_scale_expression,
    calc_scaled_dimensions,
)
from src.core.stream_layout import parse_stream_layout  # noqa: E402
from src.service.encode_events import EVENT_STATE, EncodeEvent  # noqa: E402
from src.service.encode_history import EncodeHistory  # noqa: E402
from src.service.encode_job import EncodeJob, EncodeSettings, create_batch  # noqa: E402
from src.service.encode_ports import EncodeProcessLike  # noqa: E402
from src.service.encode_service import EncodeRunner  # noqa: E402

VIDEO_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mkv", ".mov", ".avi", ".wmv", ".ts"})
PROBE_ARGS: tuple[str, ...] = ("-show_streams", "-show_format")
OUTPUT_DIR_PREFIX: str = "out_"
RUN_DIR_NAME: str = "runs"
INPUT_DIR_NAME: str = "input"
SHANA_EXTENSION: str = ".mp4"
DISK_SPACE_RATIO: float = 1.1
PRESET_COUNT: int = 6
SHANA_X265_CLIPS: tuple[str, ...] = ("video6.mp4", "video7.mp4")
CONCURRENCY_CAP: int = 4

HWACCEL_FLAG: str = "-hwaccel"
HWACCEL_OUTPUT_FLAG: str = "-hwaccel_output_format"
INPUT_FLAG: str = "-i"
FILTER_FLAG: str = "-filter:v:0"
PIX_FMT_FLAG: str = "-pix_fmt"
METADATA_MARKER_FLAG: str = "-map_metadata"
GPU_PIXEL_FORMAT: str = "nv12"
SCALE_PATTERN: re.Pattern[str] = re.compile(r"^scale='(?P<w>.*)':'(?P<h>.*)'$")
DECODE_SHANA: str = "shana"
DECODE_CPU: str = "cpu"
DECODE_GPU_CPU: str = "gpu_cpu"
DECODE_GPU_GPU: str = "gpu_gpu"
DECODE_CHOICES: tuple[str, ...] = (DECODE_SHANA, DECODE_CPU, DECODE_GPU_CPU, DECODE_GPU_GPU)
PRIORITY_NORMAL: str = "normal"


@dataclass(frozen=True, slots=True)
class LabelSpec:
    """샤나 기준 라벨 하나의 인코더, 품질값, 축소 여부"""

    encoder: str
    quality: float
    scaled: bool


LABELS: dict[str, LabelSpec] = {
    "nvenc_cq22": LabelSpec("hevc_nvenc", 22.0, False),
    "nvenc_cq26": LabelSpec("hevc_nvenc", 26.0, False),
    "nvenc_1080p": LabelSpec("hevc_nvenc", 22.0, True),
    "x265_cq22": LabelSpec("libx265", 22.0, False),
    "x265_cq26": LabelSpec("libx265", 26.0, False),
    "x265_1080p": LabelSpec("libx265", 22.0, True),
}


def _run_text(cmd: Sequence[str]) -> str:
    """외부 명령을 실행하고 표준 출력을 문자열로 반환(실패는 예외)"""
    result = subprocess.run(list(cmd), capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    if result.returncode != 0:
        raise RuntimeError(f"명령 실패({result.returncode}): {' '.join(cmd[:3])} ... {result.stderr[-300:]}")
    return result.stdout


def _input_videos(input_dir: Path) -> list[str]:
    """작업 폴더 입력 사본의 영상 파일 이름 목록(정렬)"""
    return sorted(p.name for p in input_dir.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)


def cmd_prepare(args: argparse.Namespace) -> None:
    """원본 폴더의 영상을 작업 폴더로 복사하고 필요한 여유 공간을 검사"""
    clips = Path(args.clips)
    work = Path(args.work)
    names = sorted(p.name for p in clips.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS)
    total = sum((clips / n).stat().st_size for n in names)
    required = int(total * args.presets * DISK_SPACE_RATIO)
    work.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(work).free
    print(f"입력 {len(names)}개, 총 {total} 바이트, 필요 여유 {required} 바이트, 가용 {free} 바이트")
    if free < required:
        raise SystemExit("여유 공간이 부족해 중단합니다")
    input_dir = work / INPUT_DIR_NAME
    input_dir.mkdir(exist_ok=True)
    for name in names:
        target = input_dir / name
        if target.exists() and target.stat().st_size == (clips / name).stat().st_size:
            continue
        shutil.copy2(clips / name, target)
    print(f"복사 완료: {input_dir}")


def _rewrite_pair(args: list[str], flag: str, value: str | None) -> list[str]:
    """입력 앞 디코딩 옵션에서 플래그를 바꾸거나 제거하고, 없으면 입력 앞에 추가"""
    out = list(args)
    if flag in out:
        idx = out.index(flag)
        del out[idx : idx + 2]
        if value is None:
            return out
        out[idx:idx] = [flag, value]
        return out
    if value is None:
        return out
    input_idx = out.index(INPUT_FLAG)
    out[input_idx:input_idx] = [flag, value]
    return out


def rewrite_args(args: Sequence[str], *, decode: str) -> list[str]:
    """디코딩 구성과 키프레임 강제 옵션을 바꾼 FFmpeg 인자 목록을 만든다

    - shana: 제품 인자 그대로(HEVC, AV1만 하드웨어 자동)
    - cpu: 하드웨어 디코딩 옵션 없음
    - gpu_cpu: 모든 입력을 NVDEC으로 디코딩하고 결과를 시스템 메모리로 받아 CPU 필터 사용
    - gpu_gpu: NVDEC 프레임을 GPU에 둔 채 scale_cuda 필터 사용
    """
    out = list(args)
    if decode == DECODE_SHANA:
        return out
    if decode == DECODE_CPU:
        return _rewrite_pair(out, HWACCEL_FLAG, None)
    if decode == DECODE_GPU_CPU:
        return _rewrite_pair(out, HWACCEL_FLAG, "cuda")
    if decode == DECODE_GPU_GPU:
        out = _rewrite_pair(out, HWACCEL_FLAG, "cuda")
        out = _rewrite_pair(out, HWACCEL_OUTPUT_FLAG, "cuda")
        # - GPU 프레임에 yuv420p를 지정하면 NVENC 열기가 실패하므로 pix_fmt를 빼고 scale_cuda로 nv12 변환
        out = _rewrite_pair(out, PIX_FMT_FLAG, None)
        if FILTER_FLAG in out:
            idx = out.index(FILTER_FLAG) + 1
            match = SCALE_PATTERN.match(out[idx])
            if match is None:
                raise ValueError(f"scale 식을 해석하지 못했습니다: {out[idx]}")
            out[idx] = f"scale_cuda=w='{match.group('w')}':h='{match.group('h')}':format={GPU_PIXEL_FORMAT}"
            return out
        marker = out.index(METADATA_MARKER_FLAG)
        out[marker:marker] = [FILTER_FLAG, f"scale_cuda=format={GPU_PIXEL_FORMAT}"]
        return out
    raise ValueError(f"알 수 없는 디코딩 구성: {decode}")


def make_factory(decode: str) -> Callable[[Sequence[str], Callable[[str], None]], EncodeProcessLike]:
    """인자를 재작성해 FFmpeg 프로세스를 만드는 공장"""

    def factory(args: Sequence[str], on_line: Callable[[str], None]) -> EncodeProcessLike:
        rewritten = rewrite_args(args, decode=decode)
        return EncodeProcess(rewritten, on_progress_line=on_line)

    return factory


def build_jobs(input_dir: Path, label: LabelSpec, names: Sequence[str]) -> list[EncodeJob]:
    """라벨 설정으로 계획 입력 목록을 만든다. 측정 목적은 무결성 기준(KEYFRAME_FIX)"""
    tier = RESOLUTION_TIER_BY_KEY["1080p"]
    jobs: list[EncodeJob] = []
    for name in names:
        path = input_dir / name
        props = extract_video_props(str(path), name, False)
        stat = path.stat()
        output_size = calc_scaled_dimensions(props.width, props.height, tier) if label.scaled else None
        jobs.append(
            EncodeJob(
                filename=name,
                root_dir=str(input_dir),
                source_path=str(path),
                source_size=stat.st_size,
                source_mtime_ns=stat.st_mtime_ns,
                source_duration_sec=props.duration,
                source_ext=path.suffix.lstrip(".").lower(),
                fps=props.fps,
                quality=label.quality,
                purpose=GatePurpose.KEYFRAME_FIX,
                output_size=output_size,
                scale_filter=build_scale_expression(tier) if label.scaled else None,
                maxrate_kbps=tier.max_bitrate_kbps if label.scaled else None,
            )
        )
    return jobs


def _iso_seconds(text: str | None) -> float | None:
    if not text:
        return None
    return datetime.fromisoformat(text).timestamp()


def cmd_run(args: argparse.Namespace) -> None:
    """라벨 하나를 실제 EncodeRunner로 배치 실행하고 항목별 결과를 JSON으로 저장"""
    work = Path(args.work)
    input_dir = work / INPUT_DIR_NAME
    run_dir = work / RUN_DIR_NAME
    run_dir.mkdir(parents=True, exist_ok=True)
    label = LABELS[args.label]
    if args.only:
        names = [n.strip() for n in args.only.split(",") if n.strip()]
    elif label.encoder == "libx265" and not args.all_clips:
        names = list(SHANA_X265_CLIPS)
    else:
        names = _input_videos(input_dir)
    jobs = build_jobs(input_dir, label, names)
    output_dirname = f"{OUTPUT_DIR_PREFIX}{args.name}"
    stale_output = input_dir / output_dirname
    if stale_output.exists():
        shutil.rmtree(stale_output)
    db_path = run_dir / f"{args.name}.sqlite3"
    if db_path.exists():
        db_path.unlink()
    settings = EncodeSettings(
        encoder=label.encoder,
        container_policy="keep",
        result_policy=RESULT_POLICY_SEPARATE,
        archive_dirname="_원본보관",
        output_dirname=output_dirname,
        name_conflict="skip",
        process_priority=PRIORITY_NORMAL,
        ffmpeg_path="",
        ffprobe_path="",
        concurrent_jobs=args.concurrency,
        prevent_sleep=True,
        after_complete="none",
    )
    history = EncodeHistory(str(db_path))
    batch_id = create_batch(history, settings, str(input_dir), jobs)

    def on_event(event: EncodeEvent) -> None:
        if event.kind == EVENT_STATE and event.state is not None:
            print(f"[{args.name}] {event.state.value} {Path(event.source_path).name} {event.reason}", flush=True)

    runner = EncodeRunner(
        history,
        settings,
        process_factory=make_factory(args.decode),
        on_event=on_event,
        cpu_count=os.cpu_count() or 1,
    )
    started = time.perf_counter()
    runner.run(batch_id)
    elapsed = time.perf_counter() - started
    rows = history.list_items(batch_id)
    history.close()
    items = [
        {
            "filename": Path(row.source_path).name,
            "state": row.state.value,
            "reason": row.reason,
            "output_path": row.output_path,
            "output_size": row.output_size,
            "source_size": row.source_size,
            "source_duration_sec": next(j.source_duration_sec for j in jobs if j.source_path == row.source_path),
            "decode_retry": row.decode_retry,
            "started_at": row.started_at,
            "ended_at": row.ended_at,
            "item_sec": (
                (_iso_seconds(row.ended_at) or 0.0) - (_iso_seconds(row.started_at) or 0.0)
                if row.started_at and row.ended_at
                else None
            ),
        }
        for row in rows
    ]
    report = {
        "name": args.name,
        "label": args.label,
        "decode": args.decode,
        "concurrency": args.concurrency,
        "encoder": label.encoder,
        "count": len(jobs),
        "elapsed_sec": round(elapsed, 2),
        "items": items,
    }
    (run_dir / f"{args.name}.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    done = sum(1 for i in items if i["state"] == EncodeState.DONE.value)
    print(f"== {args.name}: 완료 {done}/{len(items)}, 배치 경과 {elapsed:.1f}초", flush=True)


def _primary_video_ordinal(path: str) -> int:
    """첨부 이미지를 제외한 첫 영상 스트림의 영상 내 순번"""
    probe = json.loads(
        _run_text(["ffprobe", "-v", "error", "-show_streams", "-of", "json", path])
    )
    ordinal = 0
    for stream in probe.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        attached = bool(stream.get("disposition", {}).get("attached_pic", 0))
        if not attached:
            return ordinal
        ordinal += 1
    raise RuntimeError(f"주 영상 스트림이 없습니다: {path}")


def _packet_lines(path: str, ordinal: int) -> list[str]:
    text = _run_text(
        ["ffprobe", "-v", "error", "-select_streams", f"v:{ordinal}", "-show_entries", "packet=pts,size,flags",
         "-of", "csv=p=0", path]
    )
    return [line for line in text.splitlines() if line.strip()]


def _packet_md5(path: str, ordinal: int) -> str:
    text = _run_text(["ffmpeg", "-v", "error", "-i", path, "-map", f"0:v:{ordinal}", "-c", "copy", "-f", "md5", "-"])
    return text.strip().removeprefix("MD5=")


def stream_fingerprint(path: str) -> dict[str, Any]:
    """주 영상 패킷 데이터의 MD5와 패킷 메타데이터(pts, 크기, 플래그) 목록을 구함"""
    ordinal = _primary_video_ordinal(path)
    lines = _packet_lines(path, ordinal)
    return {"md5": _packet_md5(path, ordinal), "packets": lines}


def _first_difference(ours: list[str], ref: list[str]) -> int | None:
    for index, (a, b) in enumerate(zip(ours, ref, strict=False)):
        if a != b:
            return index
    if len(ours) != len(ref):
        return min(len(ours), len(ref))
    return None


def cmd_bytes(args: argparse.Namespace) -> None:
    """실행 결과의 주 영상 패킷을 샤나 결과와 비교"""
    work = Path(args.work)
    report = json.loads((work / RUN_DIR_NAME / f"{args.run}.json").read_text(encoding="utf-8"))
    label = report["label"]
    shana_dir = Path(args.shana) / label
    results: list[dict[str, Any]] = []
    for item in report["items"]:
        if item["state"] != EncodeState.DONE.value or not item["output_path"]:
            results.append({"filename": item["filename"], "result": f"결과 없음({item['state']})"})
            continue
        stem = Path(item["filename"]).stem
        ref_path = shana_dir / f"{stem}{SHANA_EXTENSION}"
        ours = stream_fingerprint(item["output_path"])
        if not ref_path.exists() or ref_path.stat().st_size == 0:
            results.append({"filename": item["filename"], "result": "샤나 결과 없음 또는 0바이트(무결성만 확인)"})
            continue
        ref = stream_fingerprint(str(ref_path))
        same = ours["md5"] == ref["md5"] and ours["packets"] == ref["packets"]
        results.append(
            {
                "filename": item["filename"],
                "result": "일치" if same else "불일치",
                "ours_md5": ours["md5"],
                "shana_md5": ref["md5"],
                "ours_packets": len(ours["packets"]),
                "shana_packets": len(ref["packets"]),
                "first_diff_packet": _first_difference(ours["packets"], ref["packets"]),
            }
        )
    out_path = work / RUN_DIR_NAME / f"{args.run}.bytes.json"
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    for row in results:
        print(json.dumps(row, ensure_ascii=False))
    matched = sum(1 for r in results if r["result"] == "일치")
    print(f"== {args.run}: 일치 {matched}/{len(results)}")


def cmd_gate(args: argparse.Namespace) -> None:
    """실행 결과 전체에 사후 게이트 판정을 독립 계산(무결성, 처리 표식, 절약 기준)"""
    work = Path(args.work)
    input_dir = work / INPUT_DIR_NAME
    report = json.loads((work / RUN_DIR_NAME / f"{args.run}.json").read_text(encoding="utf-8"))
    counts: dict[str, int] = {}
    rows: list[dict[str, Any]] = []
    for item in report["items"]:
        output = item["output_path"]
        if not output or not os.path.exists(output):
            continue
        source = input_dir / item["filename"]
        layout = parse_stream_layout(run_ffprobe(str(source), extra_args=PROBE_ARGS))
        container = Path(output).suffix.lstrip(".").lower()
        expected = sum(
            1 for action in plan_stream_actions(layout, container) if action.action != ACTION_DROP
        )
        out_probe = run_ffprobe(output, extra_args=PROBE_ARGS)
        summary = summarize_probe(out_probe)
        out_size = os.path.getsize(output)
        verdicts = {}
        for purpose in (GatePurpose.SAVING, GatePurpose.KEYFRAME_FIX):
            verdict = judge_result(
                output=summary,
                expected_stream_count=expected,
                source_duration_sec=item["source_duration_sec"],
                source_size_bytes=item["source_size"],
                output_size_bytes=out_size,
                purpose=purpose,
            )
            verdicts[purpose.value] = verdict.value
            key = f"{purpose.value}:{verdict.value}"
            counts[key] = counts.get(key, 0) + 1
        rows.append(
            {
                "filename": item["filename"],
                "container": container,
                "expected_streams": expected,
                "output_streams": summary.stream_count,
                "source_sec": round(item["source_duration_sec"], 3),
                "output_sec": round(summary.duration_sec, 3),
                "has_tag": summary.has_encode_tag,
                "verdicts": verdicts,
                "source_size": item["source_size"],
                "output_size": out_size,
            }
        )
    out_path = work / RUN_DIR_NAME / f"{args.run}.gate.json"
    out_path.write_text(json.dumps({"counts": counts, "rows": rows}, ensure_ascii=False, indent=1), encoding="utf-8")
    for key in sorted(counts):
        print(f"{key}: {counts[key]}")
    for row in rows:
        print(json.dumps(row, ensure_ascii=False))


def build_parser() -> argparse.ArgumentParser:
    """명령줄 인자 파서(하위 명령별 필수 인자 정의)"""
    parser = argparse.ArgumentParser(description="재인코딩 실측 스크립트(계획서 5.3절 커밋 4)")
    sub = parser.add_subparsers(dest="command", required=True)

    prep = sub.add_parser("prepare", help="원본 클립을 작업 폴더로 복사하고 여유 공간 검사")
    prep.add_argument("--clips", required=True)
    prep.add_argument("--work", required=True)
    prep.add_argument("--presets", type=int, default=PRESET_COUNT)
    prep.set_defaults(func=cmd_prepare)

    run = sub.add_parser("run", help="라벨 하나를 EncodeRunner로 배치 실행")
    run.add_argument("--work", required=True)
    run.add_argument("--label", required=True, choices=sorted(LABELS))
    run.add_argument("--concurrency", type=int, required=True)
    run.add_argument("--decode", choices=DECODE_CHOICES, default=DECODE_SHANA)
    run.add_argument("--only", default="")
    run.add_argument("--all-clips", action="store_true")
    run.add_argument("--name", required=True)
    run.set_defaults(func=cmd_run)

    byte = sub.add_parser("bytes", help="주 영상 패킷을 샤나 결과와 비교")
    byte.add_argument("--work", required=True)
    byte.add_argument("--run", required=True)
    byte.add_argument("--shana", required=True)
    byte.set_defaults(func=cmd_bytes)

    gate = sub.add_parser("gate", help="실행 결과에 사후 게이트 판정을 독립 계산")
    gate.add_argument("--work", required=True)
    gate.add_argument("--run", required=True)
    gate.set_defaults(func=cmd_gate)
    return parser


def main() -> None:
    """명령줄 진입점"""
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
