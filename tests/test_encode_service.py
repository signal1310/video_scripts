"""재인코딩 실행 서비스 검증: FFmpeg와 ffprobe를 가짜로 바꿔 배치, 사후 게이트, 교체 대기, 취소, 일시중지를 확인

- 원본과 결과 파일, 보관 폴더와 기록은 실제 임시 폴더와 SQLite에서 확인
- 실제 FFmpeg는 실행하지 않음(단위 테스트는 합성 입력만 사용)
"""

import json
import os
import tempfile
import threading
import time
import unittest
from collections.abc import Callable, Sequence
from typing import Any
from unittest import mock

from src.core.config import RESULT_POLICY_ARCHIVE, RESULT_POLICY_SEPARATE, ClassifierConfig
from src.core.encode_gate import GatePurpose
from src.core.encode_process import ProcessOutcome
from src.core.encode_progress import LONG_RUN_NOTICE_SEC
from src.core.encode_state import EncodeState
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY
from src.core.smart_action import SmartAction
from src.service.encode_events import EVENT_LONG_PAUSE, EncodeEvent
from src.service.encode_history import EncodeHistory, ItemRow
from src.service.encode_job import (
    EncodeJob,
    EncodeSettings,
    create_batch,
    job_from_json,
    job_to_json,
    jobs_from_evaluations,
)
from src.service.encode_resume import find_unrecorded_parts, prepare_resume
from src.service.encode_service import EncodeRunner
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
            tags = {"video_scripts_encode": TAG_VALUE} if self._output_tagged else {}
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
    ) -> None:
        self.calls: list[list[str]] = []
        self._before_finish: Callable[[], None] | None = before_finish
        self.started: threading.Event = threading.Event()
        self.paused: int = 0
        self.resumed: int = 0
        self.priorities: list[str] = []
        self._output_bytes: int = output_bytes
        self._returncode: Any = returncode if returncode is not None else (lambda _args: 0)
        self._block: threading.Event | None = block

    def factory(self, args: Sequence[str], on_line: Any) -> "_FakeProcess":
        return _FakeProcess(self, list(args))


class _FakeProcess:
    def __init__(self, owner: _FakeEncoder, args: list[str]) -> None:
        self._owner: _FakeEncoder = owner
        self._args: list[str] = args
        self._cancelled: bool = False

    def start(self) -> None:
        self._owner.calls.append(self._args)
        part = self._args[-1]
        with open(part, "wb") as handle:
            handle.write(b"r" * self._owner._output_bytes)
        self._owner.started.set()

    def pause(self) -> None:
        self._owner.paused += 1

    def resume(self) -> None:
        self._owner.resumed += 1

    def set_priority(self, name: str) -> None:
        self._owner.priorities.append(name)

    def cancel(self) -> None:
        self._cancelled = True
        if self._owner._block is not None:
            self._owner._block.set()

    def wait(self, timeout: float | None = None) -> ProcessOutcome:
        if self._owner._block is not None:
            self._owner._block.wait()
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
        self.assertEqual(os.path.getsize(path), OUTPUT_BYTES)
        self.assertEqual(os.stat(path).st_mtime_ns, MTIME_NS)
        self.assertIsNone(item.part_path)
        self.assertEqual(item.output_size, OUTPUT_BYTES)
        self.assertFalse(os.path.exists(path + ".part"))

    def test_separate_output_keeps_relative_folders_and_leaves_original_alone(self) -> None:
        path = self._source(folder="sub")
        item = self._run_one(path, self._settings(result_policy=RESULT_POLICY_SEPARATE))
        output = os.path.join(self.root, "_재인코딩결과", "sub", "clip.mp4")
        self.assertIs(item.state, EncodeState.DONE)
        self.assertEqual(os.path.getsize(output), OUTPUT_BYTES)
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
        path = self._source()
        item = self._run_one(path, self._settings(), probe=_FakeProbe(output_tagged=False))
        self.assertIs(item.state, EncodeState.FAILED)
        self.assertEqual(item.reason, "처리 표식 누락")
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
        with mock.patch("src.service.encode_service.shutil.disk_usage", return_value=mock.Mock(free=0)):
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
        with mock.patch("src.service.encode_service.shutil.disk_usage", return_value=mock.Mock(free=1099)):
            short = self._run_one(path, self._settings())
        self.assertIs(short.state, EncodeState.SKIPPED)
        with mock.patch("src.service.encode_service.shutil.disk_usage", return_value=mock.Mock(free=1101)):
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
        with mock.patch("src.service.encode_placement.os.rename", side_effect=flaky_replace):
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

        with mock.patch("src.service.encode_placement.os.rename", side_effect=flaky_replace):
            batch_id = create_batch(self.history, self._settings(), self.root, [self._job(path)])
            self._runner(self._settings()).run(batch_id)
        item = self.history.list_items(batch_id)[0]
        self.assertIs(item.state, EncodeState.REPLACE_PENDING)
        self.assertTrue(os.path.exists(item.part_path or ""))
        self.assertEqual(os.path.getsize(path), SOURCE_BYTES)

        self._runner(self._settings()).retry_replace_pending(batch_id)
        retried = self.history.list_items(batch_id)[0]
        self.assertIs(retried.state, EncodeState.DONE)
        self.assertEqual(os.path.getsize(path), OUTPUT_BYTES)
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
        self.assertEqual(os.path.getsize(result), OUTPUT_BYTES)
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
        self.assertEqual(os.path.getsize(path), OUTPUT_BYTES)
        self.assertEqual(os.path.getsize(archive), SOURCE_BYTES)


if __name__ == "__main__":
    unittest.main(verbosity=2)
