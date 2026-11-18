"""재인코딩 통합 시나리오 검사(계획서 5.3절 커밋 4의 4.4절, 4.6절)

- 합성 입력은 FFmpeg lavfi와 작은 생성 파일로 만들고, 작업 폴더 안에서만 읽고 씀
- 실행은 실제 `EncodeRunner`(보관 정책 기본값)를 사용하고, 실패 주입에만 인자 재작성 공장을 씀
- 검사 항목: 트랙 보존과 사유 기록, 처리 표식 읽기, 일시중지와 재개, 즉시 중지, 실패와 게이트 미통과 시 원본 무변경, 미리 보기 중 교체 대기와 재시도
- 실행 예: python -I -X utf8 scripts/measure_encode_scenarios.py all --work <작업 폴더>
"""

import argparse
import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from ctypes import wintypes
from pathlib import Path
from typing import Any

import psutil

REPO_ROOT: Path = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from src.core.config import RESULT_POLICY_ARCHIVE  # noqa: E402
from src.core.encode_gate import GatePurpose  # noqa: E402
from src.core.encode_plan import (  # noqa: E402
    ACTION_DROP,
    ACTION_TRANSCODE_AAC,
    ACTION_TRANSCODE_MOV_TEXT,
    plan_stream_actions,
)
from src.core.encode_process import EncodeProcess  # noqa: E402
from src.core.encode_state import EncodeState  # noqa: E402
from src.core.encode_tag import parse_encode_tag  # noqa: E402
from src.core.prober import extract_video_props, run_ffprobe  # noqa: E402
from src.core.stream_layout import parse_stream_layout  # noqa: E402
from src.service.encode_history import EncodeHistory  # noqa: E402
from src.service.encode_job import EncodeJob, EncodeSettings, create_batch  # noqa: E402
from src.service.encode_service import EncodeRunner  # noqa: E402

SRC_DIR_NAME: str = "src"
PROBE_ARGS: tuple[str, ...] = ("-show_streams", "-show_format")
ENCODE_TAG_FLAGS: tuple[str, ...] = ("-movflags", "-metadata")
AUDIO_MAP_FLAG: str = "-map"
RESULT_EXTENSIONS: frozenset[str] = frozenset({".mp4", ".mkv", ".mov"})
RESULT_JSON_NAME: str = "scenarios.json"
REAL_DISCARD_CLIP: str = "video17_legacy_xvid.avi"
PRIORITY_NORMAL: str = "normal"
WAIT_BEFORE_PAUSE_SEC: float = 3.0
PAUSE_HOLD_SEC: float = 3.0
POLL_SEC: float = 0.2
TIMEOUT_SEC: float = 600.0
NVENC_ENCODER: str = "hevc_nvenc"
MEASURE_QUALITY: float = 22.0
LONG_SOURCE_SEC: int = 90


def _ff(*args: str) -> None:
    """FFmpeg 명령을 실행(실패 시 예외)"""
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"합성 입력 생성 실패: {result.stderr[-400:]}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def make_sources(src: Path) -> None:
    """시나리오 입력 파일을 만듦. 파일 이름은 검사 항목별로 고정"""
    src.mkdir(parents=True, exist_ok=True)
    video = "testsrc2=size=1280x720:rate=30:duration=6"
    tone = "sine=frequency=440:duration=6"
    _ff("-f", "lavfi", "-i", video, "-f", "lavfi", "-i", tone, "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a", "aac", "-shortest", str(src / "s_plain.mp4"))
    _ff("-i", str(src / "s_plain.mp4"), "-c", "copy", "-f", "mov", str(src / "s_plain.mov"))
    _ff("-i", str(src / "s_plain.mp4"), "-c", "copy", "-f", "matroska", str(src / "s_plain.mkv"))
    _ff("-f", "lavfi", "-i", video, "-f", "lavfi", "-i", tone, "-map", "0:v", "-map", "1:a",
        "-c:v", "libx265", "-preset", "ultrafast", "-crf", "26", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
        "-c:a", "aac", str(src / "s_10bit.mp4"))
    _ff("-f", "lavfi", "-i", "color=c=red:s=320x320", "-frames:v", "1", str(src / "cover.jpg"))
    _ff("-i", str(src / "s_plain.mp4"), "-i", str(src / "cover.jpg"), "-map", "0", "-map", "1", "-c", "copy",
        "-disposition:v:1", "attached_pic", str(src / "s_cover.mp4"))
    _ff("-f", "lavfi", "-i", video, "-f", "lavfi", "-i", "sine=frequency=440:duration=6",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=6", "-f", "lavfi", "-i", "sine=frequency=220:duration=6",
        "-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:a", "-c:v", "libx264", "-preset", "ultrafast",
        "-c:a:0", "aac", "-c:a:1", "flac", "-c:a:2", "ac3", str(src / "s_multi_audio.mkv"))
    _ff("-f", "lavfi", "-i", video, "-f", "lavfi", "-i", tone, "-c:v", "wmv2", "-c:a", "wmav2", "-f", "asf",
        str(src / "s_wma.wmv"))
    ass_text = "[Script Info]\nScriptType: v4.00+\n\n[Events]\nFormat: Layer, Start, End, Style, Text\nDialogue: 0,0:00:00.00,0:00:05.00,Default,테스트\n"
    (src / "a.ass").write_text(ass_text, encoding="utf-8")
    (src / "b.ass").write_text(ass_text, encoding="utf-8")
    _ff("-i", str(src / "s_plain.mp4"), "-i", str(src / "a.ass"), "-i", str(src / "b.ass"), "-map", "0", "-map", "1",
        "-map", "2", "-c", "copy", "-c:s", "ass", str(src / "s_subs.mkv"))
    (src / "dummy_font.ttf").write_bytes(os.urandom(2048))
    _ff("-i", str(src / "s_plain.mkv"), "-attach", str(src / "dummy_font.ttf"),
        "-metadata:s:t:0", "mimetype=application/x-truetype-font", "-c", "copy", str(src / "s_font.mkv"))
    _ff("-i", str(src / "s_plain.mp4"), "-c", "copy", "-timecode", "00:00:01:00", "-f", "mov", str(src / "s_data.mov"))
    _ff("-display_rotation:v:0", "90", "-i", str(src / "s_plain.mp4"), "-c", "copy", str(src / "s_rotate.mp4"))
    _ff("-f", "lavfi", "-i", "testsrc2=size=320x240:rate=10:duration=6", "-f", "lavfi", "-i", "sine=duration=9",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(src / "s_long_audio.mp4"))
    _ff("-f", "lavfi", "-i", f"testsrc2=size=1920x1080:rate=30:duration={LONG_SOURCE_SEC}",
        "-c:v", "libx264", "-preset", "ultrafast", "-an", str(src / "s_long_1080.mp4"))
    data = (src / "s_plain.mp4").read_bytes()
    (src / "s_broken.mp4").write_bytes(data[:2000])


def _streams(path: str) -> list[dict[str, Any]]:
    raw = run_ffprobe(path, extra_args=PROBE_ARGS)
    streams = raw.get("streams")
    return list(streams) if isinstance(streams, list) else []


def _make_job(path: Path, root: Path, purpose: GatePurpose, quality: float = MEASURE_QUALITY) -> EncodeJob:
    """계획 입력을 만듦. 측정용이라 축소와 표식 검사를 제외"""
    props = extract_video_props(str(path), path.name, False)
    stat = path.stat()
    return EncodeJob(
        filename=path.name,
        root_dir=str(root),
        source_path=str(path),
        source_size=stat.st_size,
        source_mtime_ns=stat.st_mtime_ns,
        source_duration_sec=props.duration,
        source_ext=path.suffix.lstrip(".").lower(),
        fps=props.fps,
        quality=quality,
        purpose=purpose,
        output_size=None,
        scale_filter=None,
        maxrate_kbps=None,
    )


def _make_unprobed_job(path: Path, root: Path, purpose: GatePurpose) -> EncodeJob:
    """분석이 실패하는 입력용 계획 입력. 실행기가 계획 단계에서 실패로 기록해야 함"""
    stat = path.stat()
    return EncodeJob(
        filename=path.name,
        root_dir=str(root),
        source_path=str(path),
        source_size=stat.st_size,
        source_mtime_ns=stat.st_mtime_ns,
        source_duration_sec=0.0,
        source_ext=path.suffix.lstrip(".").lower(),
        fps=30.0,
        quality=MEASURE_QUALITY,
        purpose=purpose,
        output_size=None,
        scale_filter=None,
        maxrate_kbps=None,
    )


def _settings(concurrency: int) -> EncodeSettings:
    return EncodeSettings(
        encoder=NVENC_ENCODER,
        container_policy="keep",
        result_policy=RESULT_POLICY_ARCHIVE,
        archive_dirname="_원본보관",
        output_dirname="_재인코딩결과",
        name_conflict="skip",
        process_priority=PRIORITY_NORMAL,
        ffmpeg_path="",
        ffprobe_path="",
        concurrent_jobs=concurrency,
        prevent_sleep=False,
        after_complete="none",
    )


def _fresh_folder(base: Path, name: str, sources: Sequence[str], src: Path) -> Path:
    """시나리오마다 입력을 새로 복사한 작업 폴더를 만듦(원본 시나리오 입력은 보존)"""
    folder = base / name
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    for name_in_src in sources:
        shutil.copy2(src / name_in_src, folder / name_in_src)
    return folder


def _run_batch(
    history_path: Path,
    settings: EncodeSettings,
    folder: Path,
    jobs: Sequence[EncodeJob],
    factory: Callable[[Sequence[str], Callable[[str], None]], EncodeProcess] | None = None,
) -> tuple[EncodeRunner, EncodeHistory, int]:
    history = EncodeHistory(str(history_path))
    batch_id = create_batch(history, settings, str(folder), jobs)
    runner = EncodeRunner(history, settings, process_factory=factory, cpu_count=os.cpu_count() or 1)
    return runner, history, batch_id


def _row_states(history: EncodeHistory, batch_id: int) -> dict[str, tuple[str, str]]:
    return {
        Path(row.source_path).name: (row.state.value, row.reason) for row in history.list_items(batch_id)
    }


def _drop_pair(args: list[str], flag: str) -> list[str]:
    out = list(args)
    while flag in out:
        idx = out.index(flag)
        del out[idx : idx + 2]
    return out


def scenario_tracks(base: Path, src: Path, report: dict[str, Any]) -> None:
    """트랙 보존과 사유 기록: 시나리오 입력마다 실제 실행 결과의 스트림을 계획과 대조"""
    names = [
        "s_10bit.mp4", "s_cover.mp4", "s_multi_audio.mkv", "s_wma.wmv", "s_subs.mkv",
        "s_font.mkv", "s_data.mov", "s_rotate.mp4",
    ]
    folder = _fresh_folder(base, "tracks", names, src)
    jobs = [_make_job(folder / n, folder, GatePurpose.KEYFRAME_FIX) for n in names]
    history_path = base / "tracks.sqlite3"
    if history_path.exists():
        history_path.unlink()
    runner, history, batch_id = _run_batch(history_path, _settings(3), folder, jobs)
    runner.run(batch_id)
    states = _row_states(history, batch_id)
    history.close()
    rows: list[dict[str, Any]] = []
    for name in names:
        state, reason = states.get(name, ("missing", ""))
        entry: dict[str, Any] = {"source": name, "state": state, "reason": reason}
        output = _find_output(folder, name)
        if output is None or state != EncodeState.DONE.value:
            rows.append(entry)
            continue
        display = extract_video_props(str(src / name), name, False)
        layout = parse_stream_layout(run_ffprobe(str(src / name), extra_args=PROBE_ARGS))
        container = output.suffix.lstrip(".")
        plan = plan_stream_actions(layout, container)
        out_streams = [st for st in _streams(str(output)) if st.get("codec_type") != "data"]
        kept_plan = [a for a in plan if a.action != ACTION_DROP]
        dropped = [a for a in plan if a.action == ACTION_DROP]
        entry.update(
            {
                "container": container,
                "input_streams": len(layout.streams),
                "planned_kept": len(kept_plan),
                "output_streams": len(out_streams),
                "dropped_with_reason": sum(1 for a in dropped if a.reason),
                "dropped_without_reason": sum(1 for a in dropped if not a.reason),
                "transcodes": [f"{a.kind}:{a.action}" for a in plan if a.action in (ACTION_TRANSCODE_AAC, ACTION_TRANSCODE_MOV_TEXT)],
                "output_codecs": [f"{s.get('codec_type')}:{s.get('codec_name')}" for s in out_streams],
                "cover_kept": any(
                    s.get("codec_type") == "video" and bool(s.get("disposition", {}).get("attached_pic", 0))
                    for s in out_streams
                ),
                "output_rotation_tag": any(
                    s.get("side_data_list") and any("rotation" in str(sd) for sd in s.get("side_data_list", []))
                    for s in out_streams
                ),
                "output_pix_fmt": next((s.get("pix_fmt") for s in out_streams if s.get("codec_type") == "video"), None),
                "output_size": [
                    next((s.get("width") for s in out_streams if s.get("codec_type") == "video"), None),
                    next((s.get("height") for s in out_streams if s.get("codec_type") == "video"), None),
                ],
            }
        )
        entry["preserved_ok"] = entry["output_streams"] == entry["planned_kept"] and entry["dropped_without_reason"] == 0
        entry["source_display_size"] = [display.width, display.height]
        entry["display_size_ok"] = entry["output_size"] == [display.width, display.height] and not entry["output_rotation_tag"]
        rows.append(entry)
    report["tracks"] = rows


def _find_output(folder: Path, source_name: str) -> Path | None:
    """결과 파일 찾기(같은 이름, 컨테이너 확장자만 다를 수 있음)"""
    stem = Path(source_name).stem
    for candidate in sorted(folder.iterdir()):
        if candidate.is_file() and candidate.stem == stem and candidate.suffix.lower() in RESULT_EXTENSIONS:
            return candidate
    return None


def scenario_tags(base: Path, src: Path, report: dict[str, Any]) -> None:
    """처리 표식 읽기: MP4, MOV, MKV 결과에서 표식을 읽고 원본에는 표식이 없음을 확인(원본마다 별도 폴더)"""
    rows: list[dict[str, Any]] = []
    for name in ["s_plain.mp4", "s_plain.mov", "s_plain.mkv"]:
        folder = _fresh_folder(base, f"tags_{Path(name).suffix.lstrip('.')}", [name], src)
        job = _make_job(folder / name, folder, GatePurpose.KEYFRAME_FIX)
        history_path = base / f"tags_{Path(name).suffix.lstrip('.')}.sqlite3"
        if history_path.exists():
            history_path.unlink()
        runner, history, batch_id = _run_batch(history_path, _settings(1), folder, [job])
        runner.run(batch_id)
        state, _reason = _row_states(history, batch_id).get(name, ("missing", ""))
        history.close()
        entry: dict[str, Any] = {
            "source": name,
            "state": state,
            "source_tag": extract_video_props(str(src / name), name, False).has_encode_tag,
        }
        output = _find_output(folder, name)
        if output is not None:
            props = extract_video_props(str(output), output.name, False)
            fmt = run_ffprobe(str(output), extra_args=PROBE_ARGS).get("format", {})
            tags = fmt.get("tags", {}) if isinstance(fmt, dict) else {}
            parsed = parse_encode_tag(tags if isinstance(tags, dict) else {})
            entry.update(
                {
                    "output": output.name,
                    "output_tag_seen_by_analysis": props.has_encode_tag,
                    "parsed_encoder": None if parsed is None else parsed.encoder,
                }
            )
        rows.append(entry)
    report["tags"] = rows


def scenario_pause_stop(base: Path, src: Path, report: dict[str, Any]) -> None:
    """일시중지와 재개, 즉시 중지: FFmpeg 자식 프로세스 상태와 결과 정리를 확인"""
    rows: list[dict[str, Any]] = []
    folder = _fresh_folder(base, "pause", ["s_long_1080.mp4"], src)
    source = folder / "s_long_1080.mp4"
    digest_before = _sha256(source)
    jobs = [_make_job(source, folder, GatePurpose.KEYFRAME_FIX)]
    history_path = base / "pause.sqlite3"
    if history_path.exists():
        history_path.unlink()
    runner, history, batch_id = _run_batch(history_path, _settings(1), folder, jobs)
    worker = threading.Thread(target=runner.run, args=(batch_id,))
    worker.start()
    time.sleep(WAIT_BEFORE_PAUSE_SEC)
    ffmpeg_children = [p for p in psutil.Process().children(recursive=True) if "ffmpeg" in p.name().lower()]
    runner.pause_all()
    time.sleep(POLL_SEC * 5)
    paused_status = [p.status() for p in ffmpeg_children if p.is_running()]
    time.sleep(PAUSE_HOLD_SEC)
    runner.resume_all()
    worker.join(timeout=TIMEOUT_SEC)
    final_state = _row_states(history, batch_id).get("s_long_1080.mp4", ("missing", ""))
    history.close()
    rows.append(
        {
            "scenario": "pause_resume",
            "ffmpeg_children_seen": len(ffmpeg_children),
            "status_while_paused": paused_status,
            "final_state": final_state[0],
            "archive_matches_original": (folder / "_원본보관" / "s_long_1080.mp4").exists()
            and _sha256(folder / "_원본보관" / "s_long_1080.mp4") == digest_before,
        }
    )
    folder2 = _fresh_folder(base, "stop", ["s_long_1080.mp4"], src)
    source2 = folder2 / "s_long_1080.mp4"
    digest2 = _sha256(source2)
    jobs2 = [_make_job(source2, folder2, GatePurpose.KEYFRAME_FIX)]
    history2_path = base / "stop.sqlite3"
    if history2_path.exists():
        history2_path.unlink()
    runner2, history2, batch2 = _run_batch(history2_path, _settings(1), folder2, jobs2)
    worker2 = threading.Thread(target=runner2.run, args=(batch2,))
    worker2.start()
    time.sleep(WAIT_BEFORE_PAUSE_SEC)
    runner2.cancel_all()
    worker2.join(timeout=TIMEOUT_SEC)
    state2 = _row_states(history2, batch2).get("s_long_1080.mp4", ("missing", ""))
    history2.close()
    leftover_parts = [p.name for p in folder2.iterdir() if p.name.endswith(".part")]
    alive = [p.name() for p in psutil.Process().children(recursive=True) if "ffmpeg" in p.name().lower()]
    rows.append(
        {
            "scenario": "immediate_stop",
            "final_state": state2[0],
            "leftover_part_files": leftover_parts,
            "ffmpeg_alive_after": alive,
            "source_unchanged": _sha256(source2) == digest2,
        }
    )
    report["pause_stop"] = rows


def scenario_failures(base: Path, src: Path, report: dict[str, Any]) -> None:
    """실패와 게이트 미통과: 결과 상태와 원본 무변경(sha256 비교)"""
    rows: list[dict[str, Any]] = []

    def strip_tag(args: Sequence[str], _on_line: Callable[[str], None]) -> EncodeProcess:
        rewritten = _drop_pair(_drop_pair(list(args), "-movflags"), "-metadata")
        return EncodeProcess(rewritten, on_progress_line=_on_line)

    def drop_audio(args: Sequence[str], _on_line: Callable[[str], None]) -> EncodeProcess:
        rewritten = list(args)
        for idx in range(len(rewritten) - 1):
            if rewritten[idx] == AUDIO_MAP_FLAG and rewritten[idx + 1] == "0:1":
                del rewritten[idx : idx + 2]
                break
        return EncodeProcess(rewritten, on_progress_line=_on_line)

    cases: list[tuple[str, str, GatePurpose, float, Callable[[Sequence[str], Callable[[str], None]], EncodeProcess] | None]] = [
        ("broken_input", "s_broken.mp4", GatePurpose.KEYFRAME_FIX, MEASURE_QUALITY, None),
        ("fail_tag", "s_plain.mp4", GatePurpose.KEYFRAME_FIX, MEASURE_QUALITY, strip_tag),
        ("fail_integrity", "s_plain.mp4", GatePurpose.KEYFRAME_FIX, MEASURE_QUALITY, drop_audio),
        ("discard_larger", "video17_legacy_xvid.avi", GatePurpose.SAVING, MEASURE_QUALITY, None),
        ("long_audio_gate", "s_long_audio.mp4", GatePurpose.KEYFRAME_FIX, MEASURE_QUALITY, None),
    ]
    for case, name, purpose, quality, factory in cases:
        folder = _fresh_folder(base, case, [name], src)
        source = folder / name
        digest = _sha256(source)
        job = (
            _make_unprobed_job(source, folder, purpose)
            if case == "broken_input"
            else _make_job(source, folder, purpose, quality)
        )
        history_path = base / f"{case}.sqlite3"
        if history_path.exists():
            history_path.unlink()
        runner, history, batch_id = _run_batch(history_path, _settings(1), folder, [job], factory)
        runner.run(batch_id)
        state, reason = _row_states(history, batch_id).get(name, ("missing", ""))
        history.close()
        leftover = [p.name for p in folder.iterdir() if p.name.endswith(".part")]
        rows.append(
            {
                "case": case,
                "state": state,
                "reason": reason,
                "leftover_part_files": leftover,
                "source_unchanged": _sha256(source) == digest if source.exists() else "원본 없음",
                "archive_exists": (folder / "_원본보관" / name).exists(),
            }
        )
    report["failures"] = rows


def scenario_preview_hold(base: Path, src: Path, report: dict[str, Any]) -> None:
    """미리 보기 중 보관 실패와 재시도: 공유 삭제를 허용하지 않는 핸들로 원본을 열어 둔 상태에서 실행"""
    folder = _fresh_folder(base, "preview", ["s_plain.mp4"], src)
    source = folder / "s_plain.mp4"
    digest = _sha256(source)
    job = _make_job(source, folder, GatePurpose.KEYFRAME_FIX)
    history_path = base / "preview.sqlite3"
    if history_path.exists():
        history_path.unlink()
    settings = _settings(1)
    runner, history, batch_id = _run_batch(history_path, settings, folder, [job])
    handle = _open_shared_no_delete(source)
    try:
        runner.run(batch_id)
        held_state, held_reason = _row_states(history, batch_id).get("s_plain.mp4", ("missing", ""))
        held_files = {"source": source.exists(), "part": (folder / "s_plain.mp4.part").exists(),
                      "archive": (folder / "_원본보관" / "s_plain.mp4").exists()}
    finally:
        _close_handle(handle)
    runner.retry_replace_pending(batch_id)
    after_state, after_reason = _row_states(history, batch_id).get("s_plain.mp4", ("missing", ""))
    history.close()
    final = folder / "s_plain.mp4"
    report["preview_hold"] = {
        "held_state": held_state,
        "held_reason": held_reason,
        "held_files": held_files,
        "after_retry_state": after_state,
        "after_retry_reason": after_reason,
        "archive_is_original": (folder / "_원본보관" / "s_plain.mp4").exists()
        and _sha256(folder / "_원본보관" / "s_plain.mp4") == digest,
        "result_present": final.exists() and final.stat().st_size > 0,
        "part_left": (folder / "s_plain.mp4.part").exists(),
    }


GENERIC_READ: int = 0x80000000
FILE_SHARE_READ: int = 0x00000001
FILE_SHARE_WRITE: int = 0x00000002
OPEN_EXISTING: int = 3
FILE_ATTRIBUTE_NORMAL: int = 0x80
INVALID_HANDLE: int = -1


def _open_shared_no_delete(path: Path) -> int:
    """삭제와 이름 바꾸기를 막는 핸들을 연다(미리 보기 프로그램이 파일을 열어 둔 상태와 같음)"""
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                       wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    handle = create(str(path), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE, None, OPEN_EXISTING,
                    FILE_ATTRIBUTE_NORMAL, None)
    if handle is None or int(handle) == INVALID_HANDLE:
        raise OSError(ctypes.get_last_error(), "핸들을 열지 못했습니다")
    return int(handle)


def _close_handle(handle: int) -> None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    close = kernel32.CloseHandle
    close.argtypes = [wintypes.HANDLE]
    close.restype = wintypes.BOOL
    if not close(handle):
        raise OSError(ctypes.get_last_error(), "핸들을 닫지 못했습니다")


def main() -> None:
    """시나리오 명령 진입점(all 또는 개별 시나리오 이름)"""
    parser = argparse.ArgumentParser(description="재인코딩 통합 시나리오 검사")
    parser.add_argument("command", choices=["make", "tracks", "tags", "pause", "failures", "preview", "all"])
    parser.add_argument("--work", required=True)
    args = parser.parse_args()
    work = Path(args.work)
    src = work / SRC_DIR_NAME
    scenario_root = work / "scenarios"
    scenario_root.mkdir(parents=True, exist_ok=True)
    if args.command in ("make", "all") and not (src / "s_plain.mp4").exists():
        make_sources(src)
    real_clip = work / "input" / REAL_DISCARD_CLIP
    if not (src / REAL_DISCARD_CLIP).exists() and real_clip.exists():
        shutil.copy2(real_clip, src / REAL_DISCARD_CLIP)
    if args.command == "make":
        return
    report: dict[str, Any] = {}
    steps: dict[str, Callable[[Path, Path, dict[str, Any]], None]] = {
        "tracks": scenario_tracks,
        "tags": scenario_tags,
        "pause": scenario_pause_stop,
        "failures": scenario_failures,
        "preview": scenario_preview_hold,
    }
    selected = list(steps) if args.command == "all" else [args.command]
    for name in selected:
        steps[name](scenario_root, src, report)
    out = scenario_root / RESULT_JSON_NAME
    existing: dict[str, Any] = json.loads(out.read_text(encoding="utf-8")) if out.exists() else {}
    existing.update(report)
    out.write_text(json.dumps(existing, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
