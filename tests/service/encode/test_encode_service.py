"""재인코딩 실행 서비스 검증: FFmpeg와 ffprobe를 가짜로 바꿔 배치, 사후 게이트, 교체 대기, 취소, 일시중지를 확인

- 원본과 결과 파일, 보관 폴더와 기록은 실제 임시 폴더와 SQLite에서 확인
- 실제 FFmpeg는 실행하지 않음(단위 테스트는 합성 입력만 사용)
"""

import dataclasses
import json
import os
import struct
import tempfile
import threading
import time
import unittest
from collections.abc import Callable, Mapping, Sequence
from typing import Any
from unittest import mock

from src.core.config import RESULT_POLICY_ARCHIVE, RESULT_POLICY_SEPARATE, ClassifierConfig
from src.core.encode.encode_gate import GatePurpose
from src.core.encode.encode_process import ProcessOutcome
from src.core.encode.encode_progress import EARLY_STOP_MARGIN, EARLY_STOP_MIN_PROGRESS, LONG_RUN_NOTICE_SEC
from src.core.encode.encode_state import EncodeState
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY
from src.core.smart_action import SmartAction
from src.service.encode.encode_events import EVENT_LONG_PAUSE, EncodeEvent
from src.service.encode.encode_history import EncodeHistory, ItemRow
from src.service.encode.encode_job import (
    EncodeJob,
    EncodeSettings,
    create_batch,
    job_from_json,
    job_to_json,
    jobs_from_evaluations,
)
from src.service.encode.encode_reasons import EARLY_STOP_REASON
from src.service.encode.encode_resume import find_unrecorded_parts, prepare_resume
from src.service.encode.encode_service import EncodeRunner, resolve_workers
from tests._factories import make_eval, make_video

SOURCE_BYTES: int = 1000
OUTPUT_BYTES: int = 400
DURATION_SEC: float = 600.0
MTIME_NS: int = 1_600_000_000 * 1_000_000_000
TAG_VALUE: str = "v=1;enc=libx264;q=22.0;at=2026-11-18T00:00:00Z"


def _layout(*, field_order: str = "progressive", codec: str = "h264") -> dict[str, Any]:
    return {
        "streams": [
            {
                "index": 0,
                "codec_type": "video",
                "codec_name": codec,
                "width": 1920,
                "height": 1080,
                "field_order": field_order,
                "disposition": {"attached_pic": 0},
            },
            {"index": 1, "codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": str(DURATION_SEC), "size": str(SOURCE_BYTES)},
    }


class _FakeProbe:
    """원본은 주어진 레이아웃을, 임시 파일은 스트림 수와 표식 여부를 바꿔 가며 돌려주는 가짜 ffprobe"""

    def __init__(
        self,
        *,
        source: dict[str, Any] | None = None,
        output_streams: int = 2,
        output_tagged: bool = True,
        fail_source: bool = False,
    ) -> None:
        self._source: dict[str, Any] = source if source is not None else _layout()
        self._output_streams: int = output_streams
        self._output_tagged: bool = output_tagged
        self._fail_source: bool = fail_source

    def __call__(self, path: str, args: tuple[str, ...], cancel: threading.Event) -> dict[str, Any]:
        if path.endswith(".part"):
            streams = [{"index": i, "codec_type": "video" if i == 0 else "audio"} for i in range(self._output_streams)]
            tags = {"VIDEO_SCRIPTS_ENCODE": TAG_VALUE, "KEYWORDS": "re_encoded"} if self._output_tagged else {}
            return {"streams": streams, "format": {"duration": str(DURATION_SEC), "tags": tags}}
        if self._fail_source:
            raise OSError("가짜 ffprobe 실패")
        return self._source


class _BarrierProbe:
    """원본 probe를 장벽에서 기다린 뒤 주어진 가짜 probe를 호출해, 여러 항목이 같은 시점에 결과 경로를 잡게 함"""

    def __init__(self, barrier: threading.Barrier, inner: _FakeProbe) -> None:
        self._barrier: threading.Barrier = barrier
        self._inner: _FakeProbe = inner

    def __call__(self, path: str, args: tuple[str, ...], cancel: threading.Event) -> dict[str, Any]:
        if not path.endswith(".part"):
            self._barrier.wait(timeout=10.0)
        return self._inner(path, args, cancel)


class _FakeEncoder:
    """FFmpeg 대신 마지막 인자(임시 파일)에 결과를 쓰고, 종료 코드와 취소, 일시중지를 흉내 내는 가짜"""

    def __init__(
        self,
        *,
        output_bytes: int = OUTPUT_BYTES,
        returncode: Any = None,
        block: threading.Event | None = None,
        before_finish: Callable[[], None] | None = None,
        gates: Mapping[str, threading.Event] | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self._before_finish: Callable[[], None] | None = before_finish
        self.started: threading.Event = threading.Event()
        self.launched: threading.Semaphore = threading.Semaphore(0)
        self.paused: int = 0
        self.resumed: int = 0
        self.priorities: list[str] = []
        self._output_bytes: int = output_bytes
        self._returncode: Any = returncode if returncode is not None else (lambda _args: 0)
        self._block: threading.Event | None = block
        self._gates: dict[str, threading.Event] = dict(gates or {})

    def block_for(self, args: Sequence[str]) -> threading.Event | None:
        """임시 파일 이름에 맞는 게이트를 돌려주고, 없으면 공용 차단 이벤트를 돌려줌"""
        return self._gates.get(os.path.basename(args[-1]), self._block)

    @property
    def suspend_depth(self) -> int:
        """멈춘 횟수에서 재개 횟수를 뺀 값. Windows의 멈춤은 쌓이므로 0이어야 실행 중"""
        return self.paused - self.resumed

    def factory(self, args: Sequence[str], on_line: Any) -> "_FakeProcess":
        return _FakeProcess(self, list(args))


class _FakeProcess:
    def __init__(self, owner: _FakeEncoder, args: list[str]) -> None:
        self._owner: _FakeEncoder = owner
        self._args: list[str] = args
        self._block: threading.Event | None = owner.block_for(args)
        self._cancelled: bool = False

    def start(self) -> None:
        self._owner.calls.append(self._args)
        part = self._args[-1]
        with open(part, "wb") as handle:
            if part.endswith((".mp4.part", ".mov.part")):
                ftyp = struct.pack(">I4s", 12, b"ftyp") + b"isom"
                moov = struct.pack(">I4s", 8, b"moov")
                mdat_payload = max(0, self._owner._output_bytes - len(ftyp) - len(moov) - 8)
                mdat = struct.pack(">I4s", 8 + mdat_payload, b"mdat") + (b"r" * mdat_payload)
                handle.write(ftyp + mdat + moov)
            else:
                handle.write(b"r" * self._owner._output_bytes)
        self._owner.started.set()
        self._owner.launched.release()

    def pause(self) -> None:
        self._owner.paused += 1

    def resume(self) -> None:
        self._owner.resumed += 1

    def set_priority(self, name: str) -> None:
        self._owner.priorities.append(name)

    def cancel(self) -> None:
        self._cancelled = True
        if self._block is not None:
            self._block.set()

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        if self._block is not None:
            self._block.wait()
        if self._owner._before_finish is not None:
            self._owner._before_finish()
        if self._cancelled:
            return ProcessOutcome(returncode=None, cancelled=True, stderr_tail=("killed",))
        returncode = int(self._owner._returncode(self._args))
        return ProcessOutcome(
            returncode=returncode,
            cancelled=False,
            stderr_tail=() if returncode == 0 else ("ffmpeg error",),
        )


class _SkipWatchRunner(EncodeRunner):
    """대기 영상 하나를 처리 시도한 순간을 알리는 실행기. 현재 영상 후 중지로 건너뛰었는지는 결과 상태로 확인"""

    def __init__(self, *args: Any, watched: str, skipped: threading.Event, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._watched: str = watched
        self._skipped: threading.Event = skipped

    def _process_item(self, batch_id: int, row: ItemRow, job: EncodeJob) -> bool:
        result = super()._process_item(batch_id, row, job)
        if row.source_path == self._watched:
            self._skipped.set()
        return result


class _ServiceCase(unittest.TestCase):
    """임시 작업 폴더, 기록 DB, 설정 사본과 실행기 생성 도우미"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root: str = os.path.join(self._tmp.name, "work")
        os.makedirs(self.root)
        self.history: EncodeHistory = EncodeHistory(os.path.join(self._tmp.name, "video_cache.sqlite3"), sleep=lambda _s: None)
        self.events: list[EncodeEvent] = []

    def tearDown(self) -> None:
        self.history.close()
        self._tmp.cleanup()

    def _source(self, name: str = "clip.mp4", *, folder: str = "", size: int = SOURCE_BYTES) -> str:
        directory = os.path.join(self.root, folder) if folder else self.root
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, name)
        with open(path, "wb") as handle:
            handle.write(b"s" * size)
        os.utime(path, ns=(MTIME_NS, MTIME_NS))
        return path

    def _job(self, path: str) -> EncodeJob:
        stat = os.stat(path)
        return EncodeJob(
            filename=os.path.relpath(path, self.root).replace(os.sep, "/"),
            root_dir=self.root,
            source_path=path,
            source_size=stat.st_size,
            source_mtime_ns=stat.st_mtime_ns,
            source_duration_sec=DURATION_SEC,
            source_ext=os.path.splitext(path)[1].lstrip(".").lower(),
            fps=30.0,
            quality=22.0,
            purpose=GatePurpose.SAVING,
            output_size=None,
            scale_filter=None,
            maxrate_kbps=None,
        )

    def _settings(
        self,
        *,
        result_policy: str = RESULT_POLICY_ARCHIVE,
        name_conflict: str = "skip",
        concurrent_jobs: int = 1,
        container_policy: str = "keep",
    ) -> EncodeSettings:
        return EncodeSettings(
            encoder="libx264",
            container_policy=container_policy,
            result_policy=result_policy,
            archive_dirname="_원본보관",
            output_dirname="_재인코딩결과",
            name_conflict=name_conflict,
            process_priority="normal",
            ffmpeg_path="",
            ffprobe_path="",
            concurrent_jobs=concurrent_jobs,
            prevent_sleep=False,
            after_complete="none",
        )

    def _runner(
        self,
        settings: EncodeSettings,
        *,
        probe: _FakeProbe | None = None,
        encoder: _FakeEncoder | None = None,
        clock: Any = None,
    ) -> EncodeRunner:
        return EncodeRunner(
            self.history,
            settings,
            probe=probe if probe is not None else _FakeProbe(),
            process_factory=(encoder if encoder is not None else _FakeEncoder()).factory,
            on_event=self.events.append,
            cpu_count=1,
            clock=clock if clock is not None else time.monotonic,
        )

    def _run_one(
        self,
        path: str,
        settings: EncodeSettings,
        *,
        probe: _FakeProbe | None = None,
        encoder: _FakeEncoder | None = None,
    ) -> ItemRow:
        batch_id = create_batch(self.history, settings, self.root, [self._job(path)])
        self._runner(settings, probe=probe, encoder=encoder).run(batch_id)
        return self.history.list_items(batch_id)[0]


class TestPlacementPolicies(_ServiceCase):
    """결과 배치 세 가지 정책과 원본 무변경 조건"""

    def test_archive_policy_moves_original_and_places_result_with_source_mtime(self) -> None:
        path = self._source()
        item = self._run_one(path, self._settings(result_policy=RESULT_POLICY_ARCHIVE))
        archived = os.path.join(self.root, "_원본보관", "clip.mp4")
        self.assertIs(item.state, EncodeState.DONE)
        self.assertEqual(os.path.getsize(archived), SOURCE_BYTES)
        placed_size = os.path.getsize(path)
        self.assertGreaterEqual(placed_size, OUTPUT_BYTES)
        self.assertLess(placed_size, SOURCE_BYTES)
        self.assertEqual(os.stat(path).st_mtime_ns, MTIME_NS)
        self.assertIsNone(item.part_path)
        self.assertEqual(item.output_size, placed_size)
        self.assertFalse(os.path.exists(path + ".part"))

    def test_separate_output_keeps_relative_folders_and_leaves_original_alone(self) -> None:
        path = self._source(folder="sub")
        item = self._run_one(path, self._settings(result_policy=RESULT_POLICY_SEPARATE))
        output = os.path.join(self.root, "_재인코딩결과", "sub", "clip.mp4")
        self.assertIs(item.state, EncodeState.DONE)
        self.assertGreaterEqual(os.path.getsize(output), OUTPUT_BYTES)
        self.assertLess(os.path.getsize(output), SOURCE_BYTES)
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)
        self.assertFalse(os.path.exists(os.path.join(self.root, "_원본보관")))

    def test_separate_output_skips_when_same_name_exists(self) -> None:
        path = self._source()
        os.makedirs(os.path.join(self.root, "_재인코딩결과"))
        existing = os.path.join(self.root, "_재인코딩결과", "clip.mp4")
        with open(existing, "wb") as handle:
            handle.write(b"x")
        item = self._run_one(path, self._settings(result_policy=RESULT_POLICY_SEPARATE, name_conflict="skip"))
        self.assertIs(item.state, EncodeState.SKIPPED)
        self.assertEqual(item.reason, "같은 이름의 파일이 있음")
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)

    def test_number_policy_places_result_with_free_number(self) -> None:
        path = self._source()
        os.makedirs(os.path.join(self.root, "_재인코딩결과"))
        with open(os.path.join(self.root, "_재인코딩결과", "clip.mp4"), "wb") as handle:
            handle.write(b"x")
        item = self._run_one(path, self._settings(result_policy=RESULT_POLICY_SEPARATE, name_conflict="number"))
        self.assertIs(item.state, EncodeState.DONE)
        self.assertTrue(os.path.exists(os.path.join(self.root, "_재인코딩결과", "clip (1).mp4")))

    def test_archive_name_conflict_fails_without_touching_original(self) -> None:
        path = self._source()
        os.makedirs(os.path.join(self.root, "_원본보관"))
        with open(os.path.join(self.root, "_원본보관", "clip.mp4"), "wb") as handle:
            handle.write(b"old")
        item = self._run_one(path, self._settings(result_policy=RESULT_POLICY_ARCHIVE))
        self.assertIs(item.state, EncodeState.FAILED)
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)


class TestPostGate(_ServiceCase):
    """사후 게이트 실패는 원본을 건드리지 않고 임시 파일을 지움"""

    def test_larger_result_is_discarded_and_original_kept(self) -> None:
        path = self._source()
        item = self._run_one(path, self._settings(), encoder=_FakeEncoder(output_bytes=SOURCE_BYTES))
        self.assertIs(item.state, EncodeState.DISCARDED)
        self.assertEqual(item.reason, "결과가 원본보다 큼")
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)
        self.assertFalse(os.path.exists(os.path.join(self.root, "_원본보관")))
        self.assertFalse(os.path.exists(path + ".part"))

    def test_missing_tag_fails_and_keeps_original(self) -> None:
        path = self._source("clip.mkv")
        item = self._run_one(path, self._settings(), probe=_FakeProbe(output_tagged=False))
        self.assertIs(item.state, EncodeState.FAILED)
        self.assertEqual(item.reason, "재인코딩 태그 누락")
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)
        self.assertFalse(os.path.exists(path + ".part"))

    def test_stream_count_mismatch_is_integrity_failure(self) -> None:
        path = self._source()
        item = self._run_one(path, self._settings(), probe=_FakeProbe(output_streams=1))
        self.assertIs(item.state, EncodeState.FAILED)
        self.assertEqual(item.reason, "무결성 검사 실패")
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)

    def test_encoder_failure_keeps_original_and_records_stderr(self) -> None:
        path = self._source()
        encoder = _FakeEncoder(returncode=lambda _args: 1)
        item = self._run_one(path, self._settings(), encoder=encoder)
        self.assertIs(item.state, EncodeState.FAILED)
        self.assertEqual(item.reason, "인코딩 실패(종료 코드 1)")
        self.assertEqual(item.stderr_tail, "ffmpeg error")
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)


class TestPreflightAndFallback(_ServiceCase):
    """사전 검사: 인터레이스, 공간 부족, 원본 변경. 하드웨어 디코딩 실패는 CPU로 한 번 재시도"""

    def test_interlaced_source_is_skipped_without_encoding(self) -> None:
        path = self._source()
        encoder = _FakeEncoder()
        item = self._run_one(path, self._settings(), probe=_FakeProbe(source=_layout(field_order="tt")), encoder=encoder)
        self.assertIs(item.state, EncodeState.SKIPPED)
        self.assertEqual(item.reason, "인터레이스 원본")
        self.assertEqual(encoder.calls, [])

    def test_low_disk_space_is_skipped(self) -> None:
        path = self._source()
        with mock.patch("src.service.encode.encode_service.shutil.disk_usage", return_value=mock.Mock(free=0)):
            item = self._run_one(path, self._settings())
        self.assertIs(item.state, EncodeState.SKIPPED)
        self.assertEqual(item.reason, "공간 부족")

    def test_changed_source_is_skipped(self) -> None:
        path = self._source()
        job = self._job(path)
        with open(path, "wb") as handle:
            handle.write(b"changed-content" * 3)
        batch_id = create_batch(self.history, self._settings(), self.root, [job])
        self._runner(self._settings()).run(batch_id)
        item = self.history.list_items(batch_id)[0]
        self.assertIs(item.state, EncodeState.SKIPPED)
        self.assertEqual(item.reason, "원본 변경 또는 없음")

    def test_hardware_decode_failure_retries_once_on_cpu(self) -> None:
        path = self._source()
        encoder = _FakeEncoder(returncode=lambda args: 1 if "-hwaccel" in args else 0)
        item = self._run_one(path, self._settings(), probe=_FakeProbe(source=_layout(codec="hevc")), encoder=encoder)
        self.assertIs(item.state, EncodeState.DONE)
        self.assertTrue(item.decode_retry)
        self.assertEqual(len(encoder.calls), 2)
        self.assertIn("-hwaccel", encoder.calls[0])
        self.assertNotIn("-hwaccel", encoder.calls[1])

    def test_free_space_boundary_is_one_point_one_times_source(self) -> None:
        """여유 공간이 원본 크기의 1.1배보다 작으면 건너뛰고, 넘으면 진행"""
        path = self._source()
        with mock.patch("src.service.encode.encode_service.shutil.disk_usage", return_value=mock.Mock(free=1099)):
            short = self._run_one(path, self._settings())
        self.assertIs(short.state, EncodeState.SKIPPED)
        with mock.patch("src.service.encode.encode_service.shutil.disk_usage", return_value=mock.Mock(free=1101)):
            enough = self._run_one(path, self._settings())
        self.assertIs(enough.state, EncodeState.DONE)

    def test_replace_retry_keeps_hardware_decode_flag(self) -> None:
        """교체 대기 재시도는 실행 중 기록된 CPU 디코딩 재시도 표식과 로그를 지우지 않음"""
        path = self._source()
        real_replace = os.rename
        failures: list[int] = []

        def flaky_replace(src: str, dst: str) -> None:
            if len(failures) < 2 and src == path:
                failures.append(1)
                raise PermissionError("공유 위반")
            real_replace(src, dst)

        encoder = _FakeEncoder(returncode=lambda args: 1 if "-hwaccel" in args else 0)
        with mock.patch("src.service.encode.encode_placement.os.rename", side_effect=flaky_replace):
            batch_id = create_batch(self.history, self._settings(), self.root, [self._job(path)])
            self._runner(self._settings(), probe=_FakeProbe(source=_layout(codec="hevc")), encoder=encoder).run(batch_id)
        self.assertIs(self.history.list_items(batch_id)[0].state, EncodeState.REPLACE_PENDING)

        self._runner(self._settings()).retry_replace_pending(batch_id)
        retried = self.history.list_items(batch_id)[0]
        self.assertIs(retried.state, EncodeState.DONE)
        self.assertTrue(retried.decode_retry)

    def test_probe_failure_is_recorded_as_failed(self) -> None:
        path = self._source()
        item = self._run_one(path, self._settings(), probe=_FakeProbe(fail_source=True))
        self.assertIs(item.state, EncodeState.FAILED)
        self.assertTrue(item.reason.startswith("처리 중 오류"))
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)


class TestCancelStopAndReplace(_ServiceCase):
    """즉시 중지, 현재 영상 후 중지, 교체 대기 재시도"""

    def test_cancel_all_returns_item_to_pending_and_removes_part(self) -> None:
        path = self._source()
        block = threading.Event()
        encoder = _FakeEncoder(block=block)
        settings = self._settings()
        batch_id = create_batch(self.history, settings, self.root, [self._job(path)])
        runner = self._runner(settings, encoder=encoder)
        worker = threading.Thread(target=runner.run, args=(batch_id,))
        worker.start()
        self.assertTrue(encoder.started.wait(timeout=10.0))
        runner.cancel_all()
        worker.join(timeout=10.0)
        item = self.history.list_items(batch_id)[0]
        self.assertIs(item.state, EncodeState.PENDING)
        self.assertFalse(os.path.exists(path + ".part"))
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)

    def test_stop_after_current_leaves_next_items_pending_and_batch_open(self) -> None:
        first = self._source("a.mp4")
        second = self._source("b.mp4")
        block = threading.Event()
        encoder = _FakeEncoder(block=block)
        settings = self._settings(concurrent_jobs=1)
        batch_id = create_batch(self.history, settings, self.root, [self._job(first), self._job(second)])
        runner = self._runner(settings, encoder=encoder)
        worker = threading.Thread(target=runner.run, args=(batch_id,))
        worker.start()
        self.assertTrue(encoder.started.wait(timeout=10.0))
        runner.request_stop_after_current()
        block.set()
        worker.join(timeout=10.0)
        states = {i.source_path: i.state for i in self.history.list_items(batch_id)}
        self.assertIs(states[first], EncodeState.DONE)
        self.assertIs(states[second], EncodeState.PENDING)
        self.assertEqual([b.batch_id for b in self.history.list_open_batches()], [batch_id])

    def test_replace_wait_keeps_result_and_retries_later(self) -> None:
        path = self._source()
        real_replace = os.rename
        failures: list[int] = []

        def flaky_replace(src: str, dst: str) -> None:
            # - 실행 중 시도와 실행 끝의 자동 재시도, 두 번 모두 실패하도록 해 교체 대기 상태가 남게 함
            if len(failures) < 2 and src == path:
                failures.append(1)
                raise PermissionError("공유 위반")
            real_replace(src, dst)

        with mock.patch("src.service.encode.encode_placement.os.rename", side_effect=flaky_replace):
            batch_id = create_batch(self.history, self._settings(), self.root, [self._job(path)])
            self._runner(self._settings()).run(batch_id)
        item = self.history.list_items(batch_id)[0]
        self.assertIs(item.state, EncodeState.REPLACE_PENDING)
        self.assertTrue(os.path.exists(item.part_path or ""))
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)

        self._runner(self._settings()).retry_replace_pending(batch_id)
        retried = self.history.list_items(batch_id)[0]
        self.assertIs(retried.state, EncodeState.DONE)
        self.assertGreaterEqual(os.path.getsize(path), OUTPUT_BYTES)
        self.assertLess(os.path.getsize(path), SOURCE_BYTES)
        self.assertEqual(os.path.getsize(os.path.join(self.root, "_원본보관", "clip.mp4")), SOURCE_BYTES)


class TestPauseAndLongPauseNotice(_ServiceCase):
    """일시중지는 실행 중 프로세스에 적용되고, 10분을 넘으면 안내 이벤트가 나옴(결정 16)"""

    def test_long_pause_notice_follows_current_pause_duration(self) -> None:
        path = self._source()
        block = threading.Event()
        encoder = _FakeEncoder(block=block)
        now = [1000.0]
        settings = self._settings()
        batch_id = create_batch(self.history, settings, self.root, [self._job(path)])
        runner = self._runner(settings, encoder=encoder, clock=lambda: now[0])
        worker = threading.Thread(target=runner.run, args=(batch_id,))
        worker.start()
        self.assertTrue(encoder.started.wait(timeout=10.0))

        runner.pause_all()
        self.assertEqual(encoder.paused, 1)
        now[0] += LONG_RUN_NOTICE_SEC + 1.0
        runner.check_long_pause()
        notices = [e for e in self.events if e.kind == EVENT_LONG_PAUSE]
        self.assertEqual([e.source_path for e in notices], [path])

        runner.resume_all()
        self.assertEqual(encoder.resumed, 1)
        now[0] += LONG_RUN_NOTICE_SEC + 1.0
        runner.check_long_pause()
        self.assertEqual(len([e for e in self.events if e.kind == EVENT_LONG_PAUSE]), 1)

        block.set()
        worker.join(timeout=10.0)
        self.assertIs(self.history.list_items(batch_id)[0].state, EncodeState.DONE)


class TestWorkerCount(_ServiceCase):
    """배치 동시 실행 수: 설정 값을 그대로 쓰고, 0이면 인코더 종류와 코어 수로 정하는지 검증"""

    def test_fixed_setting_is_used_and_zero_is_automatic(self) -> None:
        self.assertEqual(resolve_workers(self._settings(concurrent_jobs=3), cpu_count=8), 3)
        self.assertEqual(resolve_workers(self._settings(concurrent_jobs=0), cpu_count=8), 2)
        self.assertEqual(resolve_workers(self._settings(concurrent_jobs=0), cpu_count=1), 1)


class TestSettingsSnapshotAndJobs(_ServiceCase):
    """설정 사본은 배치 시작 시점에 고정되고, 대상 판정만 작업 입력이 됨"""

    def test_settings_snapshot_ignores_later_config_changes(self) -> None:
        config = ClassifierConfig()
        snapshot = EncodeSettings.from_config(config)
        config.execution.result_policy = RESULT_POLICY_SEPARATE
        self.assertEqual(snapshot.result_policy, RESULT_POLICY_ARCHIVE)
        batch_id = create_batch(self.history, snapshot, self.root, [self._job(self._source())])
        stored = json.loads(self.history.list_open_batches()[0].settings_json)
        self.assertEqual(stored["result_policy"], RESULT_POLICY_ARCHIVE)
        self.assertEqual(self.history.list_items(batch_id)[0].state, EncodeState.PENDING)

    def test_missing_source_stays_in_batch_and_is_recorded_as_skipped(self) -> None:
        """대상 원본이 사라졌으면 배치 항목으로 남고, 실행 때 원본 변경 또는 없음 사유로 건너뜀이 되는지 검증"""
        evaluation = make_eval(
            make_video(filename="gone.mp4", duration=DURATION_SEC, fps=30.0),
            smart_action=SmartAction.ENCODE_OVER,
        )
        jobs = jobs_from_evaluations([evaluation], self.root)
        self.assertEqual([job.filename for job in jobs], ["gone.mp4"])
        settings = self._settings()
        batch_id = create_batch(self.history, settings, self.root, jobs)
        self._runner(settings).run(batch_id)
        item = self.history.list_items(batch_id)[0]
        self.assertIs(item.state, EncodeState.SKIPPED)
        self.assertEqual(item.reason, "원본 변경 또는 없음")

    def test_only_encode_targets_become_jobs(self) -> None:
        target_path = self._source("hd.mp4")
        skipped_path = self._source("ok.mp4")
        evaluations = [
            make_eval(make_video(filename="hd.mp4", duration=DURATION_SEC, fps=30.0), smart_action=SmartAction.ENCODE_OVER),
            make_eval(make_video(filename="ok.mp4"), smart_action=SmartAction.PASS_OPTIMAL),
        ]
        jobs = jobs_from_evaluations(evaluations, self.root)
        self.assertEqual([job.source_path for job in jobs], [target_path])
        self.assertNotIn(skipped_path, [job.source_path for job in jobs])
        self.assertEqual(jobs[0].purpose, GatePurpose.SAVING)
        self.assertEqual(jobs[0].quality, 22.0)
        self.assertIsNone(jobs[0].output_size)

    def test_downscale_evaluation_carries_output_size_and_bitrate_limit(self) -> None:
        self._source("big.mp4")
        tier = RESOLUTION_TIER_BY_KEY["720p"]
        evaluation = make_eval(
            make_video(filename="big.mp4", duration=DURATION_SEC),
            smart_action=SmartAction.ENCODE_DOWNSCALE,
            target_width=1280,
            target_height=720,
            target_tier=tier,
        )
        job = jobs_from_evaluations([evaluation], self.root)[0]
        self.assertEqual(job.output_size, (1280, 720))
        self.assertEqual(job.maxrate_kbps, tier.max_bitrate_kbps)
        self.assertIsNotNone(job.scale_filter)
        self.assertEqual(job_from_json(job_to_json(job)), job)


class TestResume(_ServiceCase):
    """이어서 재인코딩: 진행 중 항목 되돌림, 원본 변경 제외, 기록된 임시 파일만 삭제"""

    def test_prepare_resume_excludes_changed_and_removes_only_recorded_parts(self) -> None:
        running_src = self._source("a.mp4")
        changed_src = self._source("c.mp4")
        pending_src = self._source("b.mp4")
        settings = self._settings()
        batch_id = create_batch(
            self.history,
            settings,
            self.root,
            [self._job(running_src), self._job(pending_src), self._job(changed_src)],
        )
        recorded_part = running_src + ".part"
        with open(recorded_part, "wb") as handle:
            handle.write(b"p")
        self.history.update_item(
            batch_id,
            running_src,
            {"state": EncodeState.RUNNING, "part_path": recorded_part, "started_at": "t"},
        )
        unrecorded_part = os.path.join(self.root, "orphan.mp4.part")
        with open(unrecorded_part, "wb") as handle:
            handle.write(b"o")
        with open(changed_src, "wb") as handle:
            handle.write(b"changed" * 5)

        report = prepare_resume(self.history, batch_id)

        self.assertEqual((report.resumable, report.excluded, report.removed_parts), (2, 1, 1))
        states = {i.source_path: i.state for i in self.history.list_items(batch_id)}
        self.assertIs(states[running_src], EncodeState.PENDING)
        self.assertIs(states[changed_src], EncodeState.SKIPPED)
        self.assertFalse(os.path.exists(recorded_part))
        self.assertTrue(os.path.exists(unrecorded_part))
        self.assertEqual(find_unrecorded_parts([self.root], recorded=set()), [unrecorded_part])


class TestNoOverwriteAndClaims(_ServiceCase):
    """배치 직전에 같은 이름 파일이 생기거나 같은 결과 경로를 여러 항목이 노리는 경우에도 아무 파일도 덮어쓰지 않음"""

    def test_result_name_appearing_before_placement_keeps_both_files(self) -> None:
        path = self._source(folder="sub")
        os.makedirs(os.path.join(self.root, "_재인코딩결과", "sub"))
        final = os.path.join(self.root, "_재인코딩결과", "sub", "clip.mp4")

        def create_conflict() -> None:
            with open(final, "wb") as handle:
                handle.write(b"other")

        settings = self._settings(result_policy=RESULT_POLICY_SEPARATE)
        item = self._run_one(path, settings, encoder=_FakeEncoder(before_finish=create_conflict))
        self.assertIs(item.state, EncodeState.REPLACE_PENDING)
        self.assertEqual(os.path.getsize(final), len(b"other"))
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)
        self.assertTrue(os.path.exists(item.part_path or ""))

    def test_archive_name_appearing_before_placement_keeps_original_in_place(self) -> None:
        path = self._source()
        archive = os.path.join(self.root, "_원본보관", "clip.mp4")

        def create_archive_conflict() -> None:
            os.makedirs(os.path.dirname(archive), exist_ok=True)
            with open(archive, "wb") as handle:
                handle.write(b"old")

        item = self._run_one(path, self._settings(), encoder=_FakeEncoder(before_finish=create_archive_conflict))
        self.assertIs(item.state, EncodeState.REPLACE_PENDING)
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)
        self.assertEqual(os.path.getsize(archive), len(b"old"))

    def test_two_items_for_one_result_path_never_overwrite_each_other(self) -> None:
        """a.avi와 a.mkv가 모두 같은 a.mp4로 가는 경우 하나만 배치되고 나머지 원본은 그대로"""
        first = self._source("a.avi")
        second = self._source("a.mkv")
        barrier = threading.Barrier(2)
        settings = self._settings(concurrent_jobs=2, container_policy="mp4")
        batch_id = create_batch(self.history, settings, self.root, [self._job(first), self._job(second)])
        runner = EncodeRunner(
            self.history,
            settings,
            probe=_BarrierProbe(barrier, _FakeProbe()),
            process_factory=_FakeEncoder().factory,
            cpu_count=2,
        )
        runner.run(batch_id)
        states = sorted(i.state.value for i in self.history.list_items(batch_id))
        self.assertEqual(states, ["done", "skipped"])
        result = os.path.join(self.root, "a.mp4")
        self.assertGreaterEqual(os.path.getsize(result), OUTPUT_BYTES)
        skipped = next(i for i in self.history.list_items(batch_id) if i.state is EncodeState.SKIPPED)
        self.assertEqual(os.path.getsize(skipped.source_path), SOURCE_BYTES)


class TestImmediateStopSkipsReplaceRetry(_ServiceCase):
    """즉시 중지 뒤에는 교체 대기 재시도로 파일을 옮기지 않음"""

    def test_cancel_before_run_leaves_replace_pending_item_untouched(self) -> None:
        path = self._source()
        batch_id = create_batch(self.history, self._settings(), self.root, [self._job(path)])
        archive = os.path.join(self.root, "_원본보관", "clip.mp4")
        os.makedirs(os.path.dirname(archive))
        os.rename(path, archive)
        part = path + ".part"
        with open(part, "wb") as handle:
            handle.write(b"r" * OUTPUT_BYTES)
        self.history.update_item(
            batch_id,
            path,
            {"state": EncodeState.REPLACE_PENDING, "part_path": part, "output_path": path, "archive_path": archive},
        )
        runner = self._runner(self._settings())
        runner.cancel_all()
        runner.run(batch_id)
        self.assertIs(self.history.list_items(batch_id)[0].state, EncodeState.REPLACE_PENDING)
        self.assertTrue(os.path.exists(part))
        self.assertFalse(os.path.exists(path))


class TestInterruptedReplacement(_ServiceCase):
    """원본을 보관 폴더로 옮긴 뒤 결과 배치 전에 끊긴 항목은 결과를 지우지 않고 교체 대기로 되돌림"""

    def test_resume_keeps_finished_result_when_original_already_archived(self) -> None:
        path = self._source()
        batch_id = create_batch(self.history, self._settings(), self.root, [self._job(path)])
        archive = os.path.join(self.root, "_원본보관", "clip.mp4")
        os.makedirs(os.path.dirname(archive))
        os.replace(path, archive)
        part = path + ".part"
        with open(part, "wb") as handle:
            handle.write(b"r" * OUTPUT_BYTES)
        self.history.update_item(
            batch_id,
            path,
            {"state": EncodeState.RUNNING, "part_path": part, "output_path": path, "archive_path": archive},
        )

        report = prepare_resume(self.history, batch_id)

        self.assertEqual((report.resumable, report.excluded, report.removed_parts), (0, 0, 0))
        item = self.history.list_items(batch_id)[0]
        self.assertIs(item.state, EncodeState.REPLACE_PENDING)
        self.assertTrue(os.path.exists(part))
        self._runner(self._settings()).retry_replace_pending(batch_id)
        self.assertGreaterEqual(os.path.getsize(path), OUTPUT_BYTES)
        self.assertLess(os.path.getsize(path), SOURCE_BYTES)
        self.assertEqual(os.path.getsize(archive), SOURCE_BYTES)


class TestXtraFallbackAndTimerCoverage(_ServiceCase):
    """CPU 디코딩 재시도 시 Xtra 1회 기록과 실행 중이 아닐 때와 일시중지 중의 장시간 안내 확인을 검증"""

    def test_cpu_fallback_writes_xtra_once_on_successful_part(self) -> None:
        from src.core.mp4_xtra import XTRA_ENCODE_DETAIL_NAME, XTRA_KEYWORDS_NAME, read_mp4_xtra_items

        path = self._source("hevc.mp4")
        attempts = 0

        def rc(_args: Sequence[str]) -> int:
            nonlocal attempts
            attempts += 1
            return 1 if attempts == 1 else 0

        encoder = _FakeEncoder(returncode=rc)
        item = self._run_one(path, self._settings(), probe=_FakeProbe(source=_layout(codec="hevc")), encoder=encoder)
        self.assertIs(item.state, EncodeState.DONE)
        self.assertTrue(item.decode_retry)
        items = {it.name: it for it in read_mp4_xtra_items(path)}
        self.assertEqual(items[XTRA_KEYWORDS_NAME].texts(), ["re_encoded"])
        self.assertEqual(len(items[XTRA_ENCODE_DETAIL_NAME].texts()), 1)

    def test_long_pause_check_without_active_items_and_while_paused(self) -> None:
        runner = self._runner(self._settings())
        runner.check_long_pause()
        runner.pause_all()
        try:
            runner.check_long_pause()
        finally:
            runner.resume_all()

    def test_corrupt_mp4_part_cleans_up_part_file_on_xtra_error(self) -> None:
        """MP4 상자가 손상된 .part에서 Xtra 쓰기가 실패하면 .part를 지우고 FAILED로 끝남을 검증"""
        path = self._source("bad.mp4")

        class _CorruptMp4Process(_FakeProcess):
            def start(self) -> None:
                self._owner.calls.append(self._args)
                with open(self._args[-1], "wb") as handle:
                    handle.write(b"not_mp4_boxes")
                self._owner.started.set()

        class _CorruptEncoder(_FakeEncoder):
            def factory(self, args: Sequence[str], on_line: Any) -> _FakeProcess:
                return _CorruptMp4Process(self, list(args))

        item = self._run_one(path, self._settings(), encoder=_CorruptEncoder())
        self.assertIs(item.state, EncodeState.FAILED)
        self.assertIn("결과 태그 기록 실패", item.reason)
        self.assertFalse(os.path.exists(path + ".part"))


class TestOutputHistoryIntegration(_ServiceCase):
    """완료 시 재인코딩 이력과 태그 이름 이력 기록, 미완료 미기록, 이전 태그 이름 제거를 검증"""

    def test_done_records_fingerprint_and_retry_replace_pending_records_too(self) -> None:
        from src.core.encode_fingerprint import compute_encode_fingerprint
        from src.service.encode.encode_output_history import EncodeOutputHistory

        path = self._source("done.mp4")
        self._run_one(path, self._settings())
        out_hist = EncodeOutputHistory(os.path.join(self._tmp.name, "video_cache.sqlite3"), sleep=lambda _s: None)
        try:
            fp = compute_encode_fingerprint(path)
            self.assertEqual(out_hist.match_fingerprints({path: fp.size}), {path})
            self.assertEqual(out_hist.list_tag_names(), ("re_encoded",))
        finally:
            out_hist.close()

        # 실패/폐기 경로는 이력을 늘리지 않음
        bad = self._source("discard.mp4")
        self._run_one(bad, self._settings(), encoder=_FakeEncoder(output_bytes=SOURCE_BYTES))
        out_hist2 = EncodeOutputHistory(os.path.join(self._tmp.name, "video_cache.sqlite3"), sleep=lambda _s: None)
        try:
            self.assertEqual(len(out_hist2.find_by_size(fp.size)), 1)
        finally:
            out_hist2.close()

    def test_retry_replace_pending_records_output_history_and_previous_tag_names_are_stripped(self) -> None:
        from src.core.encode_fingerprint import compute_encode_fingerprint
        from src.core.mp4_xtra import XTRA_KEYWORDS_NAME, read_mp4_xtra_items, text_item, write_mp4_xtra
        from src.service.encode.encode_output_history import EncodeOutputHistory

        # 1. 교체 대기 항목을 retry_replace_pending으로 완료 시 이력 기록 확인
        path = self._source("pending.mp4")
        batch_id = create_batch(self.history, self._settings(), self.root, [self._job(path)])
        archive = os.path.join(self.root, "_원본보관", "pending.mp4")
        os.makedirs(os.path.dirname(archive), exist_ok=True)
        os.replace(path, archive)
        part = path + ".part"
        with open(part, "wb") as handle:
            ftyp = struct.pack(">I4s", 12, b"ftyp") + b"isom"
            mdat = struct.pack(">I4s", 40, b"mdat") + (b"x" * 32)
            moov = struct.pack(">I4s", 8, b"moov")
            handle.write(ftyp + mdat + moov)
        self.history.update_item(
            batch_id,
            path,
            {"state": EncodeState.REPLACE_PENDING, "part_path": part, "output_path": path, "archive_path": archive},
        )
        self._runner(self._settings()).retry_replace_pending(batch_id)

        out_hist = EncodeOutputHistory(os.path.join(self._tmp.name, "video_cache.sqlite3"), sleep=lambda _s: None)
        try:
            fp = compute_encode_fingerprint(path)
            self.assertEqual(out_hist.match_fingerprints({path: fp.size}), {path})
            # 이전 태그 이름 이력에 old_tag 등록
            out_hist.record_tag_name("old_tag")
        finally:
            out_hist.close()

        # 2. old_tag가 붙은 MP4를 new_tag로 인코딩하면 old_tag가 제거되고 new_tag만 남음
        src2 = self._source("retag.mp4")
        with open(src2, "wb") as handle:
            handle.write(
                struct.pack(">I4s", 12, b"ftyp")
                + b"isom"
                + struct.pack(">I4s", 1000, b"mdat")
                + (b"s" * 992)
                + struct.pack(">I4s", 8, b"moov")
            )
        write_mp4_xtra(src2, [text_item(XTRA_KEYWORDS_NAME, ["old_tag", "사용자태그"])])
        import dataclasses

        settings2 = dataclasses.replace(self._settings(), encode_tag_name="new_tag")
        self._run_one(src2, settings2)
        items = {it.name: it for it in read_mp4_xtra_items(src2)}
        self.assertEqual(items[XTRA_KEYWORDS_NAME].texts(), ["사용자태그", "new_tag"])


PROGRESS_STEP_SEC: float = 6.0
PROGRESS_BLOCKS: int = 20
CLOCK_START_SEC: float = 100.0


class _ProgressEncoder(_FakeEncoder):
    """진행 줄을 종료 대기 시점에 흘려 조기 중단 경로를 재현하는 가짜

    - 원본 이름별 추정 크기 배수를 받음. 추정 크기는 SOURCE_BYTES x 배수로 일정
    - out_time_available이 False이면 out_time을 N/A로 흘림(MKV, 커버 MP4와 같은 상태)
    - exit_zero_on_cancel이 True이면 중단 요청 뒤에도 종료 코드 0으로 끝남(요청과 정상 종료의 경쟁)
    - 진행 줄은 실행기 잠금을 잡지 않은 wait에서 흘림(시작 중에 흘리면 실행기 잠금과 겹침)
    """

    def __init__(
        self,
        *,
        factors: dict[str, float],
        clock_box: list[float],
        on_block: Callable[[int], None] | None = None,
        output_bytes: int = OUTPUT_BYTES,
        out_time_available: bool = True,
        exit_zero_on_cancel: bool = False,
    ) -> None:
        super().__init__(output_bytes=output_bytes)
        self.factors: dict[str, float] = factors
        self.clock_box: list[float] = clock_box
        self.on_block: Callable[[int], None] | None = on_block
        self.out_time_available: bool = out_time_available
        self.exit_zero_on_cancel: bool = exit_zero_on_cancel

    def factory(self, args: Sequence[str], on_line: Any) -> "_ProgressProcess":
        return _ProgressProcess(self, list(args), on_line)


class _ProgressProcess(_FakeProcess):
    def __init__(self, owner: _ProgressEncoder, args: list[str], on_line: Any) -> None:
        super().__init__(owner, args)
        self._progress_encoder: _ProgressEncoder = owner
        self._on_line: Any = on_line

    def _factor(self) -> float:
        names = {os.path.basename(arg) for arg in self._args}
        return next(factor for name, factor in self._progress_encoder.factors.items() if name in names)

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        owner = self._progress_encoder
        factor = self._factor()
        for block in range(1, PROGRESS_BLOCKS + 1):
            if owner.on_block is not None:
                owner.on_block(block)
            if self._cancelled:
                break
            elapsed = block * PROGRESS_STEP_SEC
            owner.clock_box[0] = CLOCK_START_SEC + elapsed
            progress = elapsed / DURATION_SEC
            size = int(factor * SOURCE_BYTES * progress)
            out_time_line = f"out_time_us={int(elapsed * 1_000_000)}" if owner.out_time_available else "out_time_us=N/A"
            for line in (f"total_size={size}", out_time_line, "progress=continue"):
                self._on_line(line)
        if self._cancelled and owner.exit_zero_on_cancel:
            return ProcessOutcome(returncode=0, cancelled=True, stderr_tail=())
        return super().wait(timeout)


class TestEarlyStop(_ServiceCase):
    """절약 목적 항목의 조기 중단: 해당 항목만 중지, 원본 무변경, CPU 재시도 없음, 재실행 제외"""

    def _progress_runner(self, encoder: _ProgressEncoder, clock_box: list[float], settings: EncodeSettings) -> EncodeRunner:
        return EncodeRunner(
            self.history,
            settings,
            probe=_FakeProbe(),
            process_factory=encoder.factory,
            on_event=self.events.append,
            cpu_count=1,
            clock=lambda: clock_box[0],
        )

    def _jobs(self, *specs: tuple[str, GatePurpose]) -> list[EncodeJob]:
        return [dataclasses.replace(self._job(self._source(name)), purpose=purpose) for name, purpose in specs]

    def test_saving_item_over_margin_is_discarded_early_and_other_items_continue(self) -> None:
        """추정 크기가 여유 배수 이상인 절약 항목만 중지하고, 같은 배치의 다른 항목은 끝까지 인코딩"""
        clock_box = [CLOCK_START_SEC]
        encoder = _ProgressEncoder(factors={"a.mp4": 1.5, "b.mp4": 1.1}, clock_box=clock_box)
        settings = self._settings()
        jobs = self._jobs(("a.mp4", GatePurpose.SAVING), ("b.mp4", GatePurpose.SAVING))
        batch_id = create_batch(self.history, settings, self.root, jobs)
        self._progress_runner(encoder, clock_box, settings).run(batch_id)
        items = {os.path.basename(i.source_path): i for i in self.history.list_items(batch_id)}
        early = items["a.mp4"]
        self.assertIs(early.state, EncodeState.DISCARDED)
        self.assertEqual(early.reason, EARLY_STOP_REASON)
        assert early.early_stop_progress is not None
        self.assertGreaterEqual(early.early_stop_progress, EARLY_STOP_MIN_PROGRESS)
        self.assertGreaterEqual(early.early_stop_estimate_bytes or 0, int(SOURCE_BYTES * EARLY_STOP_MARGIN))
        self.assertFalse(os.path.exists(os.path.join(self.root, "a.mp4.part")))
        self.assertEqual(os.path.getsize(os.path.join(self.root, "a.mp4")), SOURCE_BYTES)
        self.assertIs(items["b.mp4"].state, EncodeState.DONE)
        self.assertIsNone(items["b.mp4"].early_stop_progress)

    def test_early_stopped_item_is_not_retried_on_cpu_and_not_run_again(self) -> None:
        """조기 중단 뒤 CPU 디코딩 재시도를 하지 않고, 다시 실행해도 폐기 항목은 대상이 아님"""
        clock_box = [CLOCK_START_SEC]
        encoder = _ProgressEncoder(factors={"a.mp4": 1.5}, clock_box=clock_box)
        settings = self._settings()
        batch_id = create_batch(self.history, settings, self.root, self._jobs(("a.mp4", GatePurpose.SAVING)))
        self._progress_runner(encoder, clock_box, settings).run(batch_id)
        self.assertEqual(len(encoder.calls), 1)
        again = _ProgressEncoder(factors={"a.mp4": 1.5}, clock_box=clock_box)
        self._progress_runner(again, clock_box, settings).run(batch_id)
        self.assertEqual(again.calls, [])
        self.assertIs(self.history.list_items(batch_id)[0].state, EncodeState.DISCARDED)

    def test_estimate_below_margin_completes_normally(self) -> None:
        """추정 크기가 여유 배수보다 작으면 중단하지 않고 끝까지 인코딩"""
        clock_box = [CLOCK_START_SEC]
        encoder = _ProgressEncoder(factors={"a.mp4": 1.1}, clock_box=clock_box)
        settings = self._settings()
        batch_id = create_batch(self.history, settings, self.root, self._jobs(("a.mp4", GatePurpose.SAVING)))
        self._progress_runner(encoder, clock_box, settings).run(batch_id)
        item = self.history.list_items(batch_id)[0]
        self.assertIs(item.state, EncodeState.DONE)
        self.assertIsNone(item.early_stop_progress)
        self.assertIsNone(item.early_stop_estimate_bytes)

    def test_keyframe_fix_purpose_is_never_stopped_early(self) -> None:
        """절약 목적이 아니면(KF 교정) 추정 크기가 커도 조기 중단하지 않음"""
        clock_box = [CLOCK_START_SEC]
        encoder = _ProgressEncoder(factors={"a.mp4": 1.5}, clock_box=clock_box)
        settings = self._settings()
        batch_id = create_batch(self.history, settings, self.root, self._jobs(("a.mp4", GatePurpose.KEYFRAME_FIX)))
        self._progress_runner(encoder, clock_box, settings).run(batch_id)
        self.assertIs(self.history.list_items(batch_id)[0].state, EncodeState.DONE)

    def test_paused_item_is_not_judged(self) -> None:
        """일시중지 중에는 판정하지 않고, 재개 뒤 끝까지 인코딩"""
        clock_box = [CLOCK_START_SEC]
        encoder = _ProgressEncoder(factors={"a.mp4": 1.5}, clock_box=clock_box)
        settings = self._settings()
        batch_id = create_batch(self.history, settings, self.root, self._jobs(("a.mp4", GatePurpose.SAVING)))
        runner = self._progress_runner(encoder, clock_box, settings)
        runner.pause_all()
        runner.run(batch_id)
        runner.resume_all()
        self.assertEqual(encoder.paused, 1)
        self.assertIs(self.history.list_items(batch_id)[0].state, EncodeState.DONE)

    def test_user_cancel_before_stop_returns_item_to_pending(self) -> None:
        """사용자 즉시 중지가 먼저 오면 조기 중단 대신 대기 상태로 되돌림"""
        clock_box = [CLOCK_START_SEC]
        runner_holder: list[EncodeRunner] = []

        def cancel_at_first_block(block: int) -> None:
            if block == 1:
                runner_holder[0].cancel_all()

        encoder = _ProgressEncoder(factors={"a.mp4": 1.5}, clock_box=clock_box, on_block=cancel_at_first_block)
        settings = self._settings()
        batch_id = create_batch(self.history, settings, self.root, self._jobs(("a.mp4", GatePurpose.SAVING)))
        runner = self._progress_runner(encoder, clock_box, settings)
        runner_holder.append(runner)
        runner.run(batch_id)
        item = self.history.list_items(batch_id)[0]
        self.assertIs(item.state, EncodeState.PENDING)
        self.assertIsNone(item.early_stop_progress)

    def test_accepted_request_with_zero_exit_goes_through_normal_gate(self) -> None:
        """중단 요청이 수락됐어도 프로세스가 종료 코드 0으로 끝나면 조기 중단 폐기가 아니라 일반 사후 게이트로 보냄"""
        clock_box = [CLOCK_START_SEC]
        encoder = _ProgressEncoder(factors={"a.mp4": 1.5}, clock_box=clock_box, exit_zero_on_cancel=True)
        settings = self._settings()
        batch_id = create_batch(self.history, settings, self.root, self._jobs(("a.mp4", GatePurpose.SAVING)))
        self._progress_runner(encoder, clock_box, settings).run(batch_id)
        item = self.history.list_items(batch_id)[0]
        self.assertIs(item.state, EncodeState.DONE)
        self.assertNotEqual(item.reason, EARLY_STOP_REASON)
        self.assertIsNone(item.early_stop_progress)
        self.assertIsNotNone(item.output_path)
        self.assertTrue(os.path.exists(item.output_path or ""))

    def test_out_time_unavailable_is_never_stopped_early_and_gate_discards_larger_result(self) -> None:
        """out_time이 N/A인 항목은 조기 중단하지 않고 끝까지 인코딩한 뒤, 결과가 원본보다 크면 일반 게이트에서 폐기"""
        clock_box = [CLOCK_START_SEC]
        encoder = _ProgressEncoder(
            factors={"a.mp4": 1.5},
            clock_box=clock_box,
            output_bytes=SOURCE_BYTES + 500,
            out_time_available=False,
        )
        settings = self._settings()
        batch_id = create_batch(self.history, settings, self.root, self._jobs(("a.mp4", GatePurpose.SAVING)))
        self._progress_runner(encoder, clock_box, settings).run(batch_id)
        item = self.history.list_items(batch_id)[0]
        self.assertIs(item.state, EncodeState.DISCARDED)
        self.assertEqual(item.reason, "결과가 원본보다 큼")
        self.assertIsNone(item.early_stop_progress)
        self.assertEqual(len(encoder.calls), 1)
        self.assertEqual(os.path.getsize(os.path.join(self.root, "a.mp4")), SOURCE_BYTES)


class TestItemControls(_ServiceCase):
    """영상 하나의 일시중지, 재개, 중지가 배치 기록과 장시간 안내에 맞게 동작하는지 검증"""

    def _start_blocked(
        self, path: str, now: list[float]
    ) -> tuple[EncodeRunner, _FakeEncoder, threading.Thread, threading.Event, int]:
        block = threading.Event()
        encoder = _FakeEncoder(block=block)
        settings = self._settings()
        batch_id = create_batch(self.history, settings, self.root, [self._job(path)])
        runner = self._runner(settings, encoder=encoder, clock=lambda: now[0])
        worker = threading.Thread(target=runner.run, args=(batch_id,))
        worker.start()
        self.assertTrue(encoder.started.wait(timeout=10.0))
        return runner, encoder, worker, block, batch_id

    def test_pause_one_notices_after_the_limit_and_resume_one_restores_the_process(self) -> None:
        path = self._source()
        now = [1000.0]
        runner, encoder, worker, block, batch_id = self._start_blocked(path, now)
        self.assertTrue(runner.pause_one(path))
        self.assertEqual(encoder.paused, 1)
        self.assertFalse(runner.pause_one(path))
        now[0] += LONG_RUN_NOTICE_SEC + 1.0
        runner.check_long_pause()
        self.assertEqual([e.source_path for e in self.events if e.kind == EVENT_LONG_PAUSE], [path])
        self.assertTrue(runner.resume_one(path))
        self.assertEqual(encoder.resumed, 1)
        self.assertFalse(runner.resume_one(path))
        block.set()
        worker.join(timeout=10.0)
        self.assertIs(self.history.list_items(batch_id)[0].state, EncodeState.DONE)

    def test_resume_one_is_refused_while_the_whole_batch_is_paused(self) -> None:
        path = self._source()
        now = [1000.0]
        runner, encoder, worker, block, _batch_id = self._start_blocked(path, now)
        runner.pause_all()
        self.assertFalse(runner.pause_one(path))
        self.assertFalse(runner.resume_one(path))
        runner.resume_all()
        self.assertEqual(encoder.resumed, 1)
        block.set()
        worker.join(timeout=10.0)

    def test_cancel_one_returns_the_item_to_pending_and_keeps_the_batch_open(self) -> None:
        path = self._source()
        now = [1000.0]
        runner, _encoder, worker, _block, batch_id = self._start_blocked(path, now)
        runner.cancel_one(path)
        worker.join(timeout=10.0)
        self.assertIs(self.history.list_items(batch_id)[0].state, EncodeState.PENDING)
        self.assertFalse(os.path.exists(path + ".part"))
        self.assertIn(batch_id, [row.batch_id for row in self.history.list_open_batches()])

    def test_pause_one_then_pause_all_and_resume_all_leaves_no_suspend_depth(self) -> None:
        """개별 일시중지 뒤 전체 일시중지, 전체 재개를 하면 멈춤 깊이가 0이 되어 영상이 다시 실행되는지 검증"""
        path = self._source()
        now = [1000.0]
        runner, encoder, worker, block, _batch_id = self._start_blocked(path, now)
        try:
            self.assertTrue(runner.pause_one(path))
            runner.pause_all()
            runner.resume_all()
            self.assertEqual(encoder.suspend_depth, 0)
        finally:
            block.set()
            worker.join(timeout=10.0)

    def _run_with_stop_after_on_first_item(self, *, cancel_stop: bool) -> dict[str, EncodeState]:
        """첫 영상 실행 중에 현재 영상 후 중지를 요청하고, 취소 여부에 따라 대기 영상의 최종 상태를 돌려줌"""
        first = self._source("a.mp4")
        second = self._source("b.mp4")
        block = threading.Event()
        encoder = _FakeEncoder(block=block)
        settings = self._settings()
        batch_id = create_batch(self.history, settings, self.root, [self._job(first), self._job(second)])
        runner = self._runner(settings, encoder=encoder, clock=lambda: 1000.0)
        worker = threading.Thread(target=runner.run, args=(batch_id,))
        worker.start()
        try:
            self.assertTrue(encoder.started.wait(timeout=10.0))
            runner.request_stop_after_current()
            if cancel_stop:
                runner.cancel_stop_after_current()
        finally:
            block.set()
            worker.join(timeout=10.0)
        return {row.source_path: row.state for row in self.history.list_items(batch_id)}

    def test_stop_after_without_cancel_leaves_the_waiting_item_pending(self) -> None:
        """현재 영상 후 중지만 하면 아직 시작하지 않은 영상은 대기로 남는지 검증(취소 효과의 기준)"""
        states = self._run_with_stop_after_on_first_item(cancel_stop=False)
        self.assertIs(states[os.path.join(self.root, "b.mp4")], EncodeState.PENDING)

    def test_cancel_stop_after_lets_the_waiting_item_start_and_finish(self) -> None:
        """현재 영상 후 중지 예약을 취소하면 아직 시작하지 않은 영상이 이어서 인코딩되는지 검증"""
        states = self._run_with_stop_after_on_first_item(cancel_stop=True)
        self.assertIs(states[os.path.join(self.root, "b.mp4")], EncodeState.DONE)

    def test_cancel_stop_after_reruns_an_item_skipped_while_another_item_still_runs(self) -> None:
        """현재 영상 후 중지 중 건너뛴 대기 영상이, 다른 영상이 아직 도는 동안 예약을 취소하면 같은 배치 안에서 실행되는지 검증"""
        first = self._source("a.mp4")
        second = self._source("b.mp4")
        third = self._source("c.mp4")
        gates = {"a.mp4.part": threading.Event(), "b.mp4.part": threading.Event()}
        encoder = _FakeEncoder(gates=gates)
        settings = self._settings(concurrent_jobs=2)
        batch_id = create_batch(self.history, settings, self.root, [self._job(p) for p in (first, second, third)])
        skipped = threading.Event()
        runner = _SkipWatchRunner(
            self.history,
            settings,
            watched=third,
            skipped=skipped,
            probe=_FakeProbe(),
            process_factory=encoder.factory,
            on_event=self.events.append,
            cpu_count=1,
            clock=lambda: 1000.0,
        )
        worker = threading.Thread(target=runner.run, args=(batch_id,))
        worker.start()
        try:
            self.assertTrue(encoder.launched.acquire(timeout=10.0))
            self.assertTrue(encoder.launched.acquire(timeout=10.0))
            runner.request_stop_after_current()
            gates["a.mp4.part"].set()
            self.assertTrue(skipped.wait(timeout=10.0))
            runner.cancel_stop_after_current()
        finally:
            gates["b.mp4.part"].set()
            worker.join(timeout=10.0)
        states = {row.source_path: row.state for row in self.history.list_items(batch_id)}
        self.assertIs(states[third], EncodeState.DONE)

    def test_finished_run_cancels_the_long_pause_timer_and_it_does_not_rearm(self) -> None:
        """전체 일시중지 중에 중지로 끝난 실행이 안내 타이머를 취소하고, 뒤늦게 울린 타이머도 다시 켜지 않는지 검증"""
        path = self._source()
        now = [1000.0]
        with mock.patch("threading.Timer") as timer_cls:
            runner, _encoder, worker, block, _batch_id = self._start_blocked(path, now)
            runner.pause_all()
            runner.cancel_all()
            worker.join(timeout=10.0)
            block.set()
            timer_cls.return_value.cancel.assert_called()
            now[0] += LONG_RUN_NOTICE_SEC + 1.0
            callback_args = timer_cls.call_args.kwargs["args"]
            timer_cls.call_args.args[1](*callback_args)
        self.assertEqual(timer_cls.call_count, 1)
        self.assertEqual([e for e in self.events if e.kind == EVENT_LONG_PAUSE], [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
