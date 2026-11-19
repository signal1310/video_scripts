"""외부 파일 변경 점검의 재동기화 판정, 실행 간격과 순서, 취소, 삭제 반영 후 시그니처 안정성을 검증"""

import os
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.analysis.file_manager import compute_dir_signature
from src.gui.controllers import external_resync as resync
from src.gui.controllers.external_resync import (
    ACTION_ANALYZE_FAST,
    ACTION_ANALYZE_FULL,
    ACTION_PRUNE,
    SCAN_THREAD_NAME,
    ExternalResyncCoordinator,
    plan_external_resync,
)
from src.service.cache import CacheRegistry, TableCache
from src.service.classifier_service import DirScanInputs, VideoClassifierService
from tests._factories import make_video

_APP = QApplication.instance() or QApplication([])
_WAIT_SEC: float = 5.0
# - 실제 시계에 기대지 않도록 간격을 크게 둠. 부하로 실행이 늦어지면 기본 간격을 넘겨 점검이 두 번 일어남
_TEST_CHECK_MIN_INTERVAL_SEC: float = 3600.0


def _wait(predicate: Callable[[], bool], timeout_sec: float = _WAIT_SEC) -> bool:
    """신호가 큐에 쌓이도록 이벤트를 처리하며 조건이 참이 될 때까지 기다림"""
    end = time.monotonic() + timeout_sec
    while not predicate():
        if time.monotonic() > end:
            return False
        _APP.processEvents()
        time.sleep(0.01)
    return True


def drain_scan_threads() -> None:
    """테스트가 끝나기 전에 점검 워커가 모두 끝나 신호를 보내지 않도록 기다림"""
    _wait(lambda: not any(t.name == SCAN_THREAD_NAME for t in threading.enumerate()), timeout_sec=_WAIT_SEC)


def _write(path: str, payload: bytes = b"v") -> None:
    with open(path, "wb") as f:
        f.write(payload)


class PlanExternalResyncTests(unittest.TestCase):
    """저장 시그니처와 현재 시그니처의 차이로 재동기화 방식을 고르는 순수 판정을 검증"""

    SAVED = (("a.mp4", 10, 1), ("b.mp4", 20, 2))

    def test_none_without_saved_signature_or_when_unchanged(self) -> None:
        """저장 값이 없거나 같으면 아무 작업도 하지 않음을 검증"""
        self.assertIsNone(plan_external_resync(None, self.SAVED, include_keyframe=True))
        self.assertIsNone(plan_external_resync(self.SAVED, self.SAVED, include_keyframe=True))

    def test_removal_only_is_prune_without_disk_analysis(self) -> None:
        """삭제만 있으면 행을 빼는 prune을 고른다(키프레임 설정과 무관)"""
        current = (("a.mp4", 10, 1),)
        self.assertEqual(plan_external_resync(self.SAVED, current, include_keyframe=True), ACTION_PRUNE)
        self.assertEqual(plan_external_resync(self.SAVED, current, include_keyframe=False), ACTION_PRUNE)

    def test_added_or_changed_entries_reanalyze_with_saved_keyframe_mode(self) -> None:
        """추가나 같은 이름의 변경이 있으면 키프레임 포함 여부를 유지한 분석을 고른다"""
        added = (*self.SAVED, ("c.mp4", 30, 3))
        changed = (("a.mp4", 10, 1), ("b.mp4", 21, 2))
        renamed = (("a.mp4", 10, 1), ("new.mp4", 20, 2))
        for current in (added, changed, renamed):
            self.assertEqual(plan_external_resync(self.SAVED, current, include_keyframe=True), ACTION_ANALYZE_FULL)
            self.assertEqual(plan_external_resync(self.SAVED, current, include_keyframe=False), ACTION_ANALYZE_FAST)


class ExternalResyncCoordinatorTests(unittest.TestCase):
    """요청 간격, 실행 중 대기, 바쁨 제외, 취소와 결과 적용 순서를 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.addCleanup(drain_scan_threads)
        self.root = self._tmp.name
        self.calls: list[str] = []
        self.busy = False
        self.encoding = False
        self.input_requests = 0
        self.saved: tuple[tuple[str, int, int], ...] | None = None

    def _inputs(self) -> DirScanInputs | None:
        self.input_requests += 1
        return DirScanInputs(
            root_dir=self.root,
            recursive=False,
            excluded_dirnames=(),
            include_keyframe=True,
            saved_signature=self.saved,
        )

    def _coordinator(self) -> ExternalResyncCoordinator:
        return ExternalResyncCoordinator(
            get_scan_inputs=self._inputs,
            is_busy=lambda: self.busy,
            on_reanalyze=lambda full: self.calls.append(f"reanalyze:{full}"),
            on_prune=lambda current: self.calls.append(f"prune:{len(current)}"),
            on_reevaluate=lambda: self.calls.append("reevaluate"),
            is_encoding=lambda: self.encoding,
        )

    def test_requests_are_dropped_while_encoding(self) -> None:
        """재인코딩 중에는 즉시 요청도 점검을 시작하지 않고 입력 조회도 하지 않음"""
        self.encoding = True
        coordinator = self._coordinator()
        coordinator.request(immediate=True)
        coordinator.on_missing_file("없는 파일")
        self.assertEqual(self.input_requests, 0)
        self.assertEqual(self.calls, [])

    def test_batch_end_runs_one_immediate_check_after_encoding(self) -> None:
        """배치가 끝나면 막혔던 점검을 간격 없이 한 번 실행하고, 실행 중 요청은 되살리지 않음"""
        self.encoding = True
        coordinator = self._coordinator()
        coordinator.request(immediate=True)
        self.encoding = False
        coordinator.on_encode_finished(None)
        self.assertTrue(_wait(lambda: self.input_requests >= 1))
        drain_scan_threads()
        self.assertEqual(self.calls, [])

    def test_drag_finished_schedules_the_check_after_the_sync_delay(self) -> None:
        """외부 드래그 완료는 동기화 지연 뒤의 점검으로 예약되는지 검증"""
        coordinator = self._coordinator()
        with patch.object(resync, "call_later") as schedule:
            coordinator.on_drag_finished([])
        schedule.assert_called_once()
        self.assertEqual(schedule.call_args.args[1], resync.DRAG_SYNC_DELAY_MS)

    def test_removal_is_pruned_and_reevaluated(self) -> None:
        """삭제만 있으면 prune 뒤 재평가가 실행되는지 검증"""
        keep, gone = os.path.join(self.root, "a.mp4"), os.path.join(self.root, "b.mp4")
        _write(keep)
        _write(gone)
        self.saved = compute_dir_signature(self.root)
        os.remove(gone)
        coordinator = self._coordinator()
        coordinator.request(immediate=True)
        self.assertTrue(_wait(lambda: "reevaluate" in self.calls))
        self.assertEqual(self.calls, ["prune:1", "reevaluate"])

    def test_busy_state_skips_the_check_entirely(self) -> None:
        """바쁨 상태에서는 점검을 시작하지 않는지 검증"""
        _write(os.path.join(self.root, "a.mp4"))
        self.saved = ()
        self.busy = True
        coordinator = self._coordinator()
        coordinator.request(immediate=True)
        _wait(lambda: False, timeout_sec=0.3)
        self.assertEqual(self.calls, [])

    def test_activation_requests_within_interval_are_ignored(self) -> None:
        """활성화 요청은 직전 점검 뒤 최소 간격 안이면 무시되고, 즉시 요청은 간격을 적용하지 않는지 검증"""
        _write(os.path.join(self.root, "a.mp4"))
        coordinator = self._coordinator()
        real_scan = resync.compute_dir_signature
        finished: list[int] = []

        def tracked_scan(*args: object, **kwargs: object) -> object:
            result = real_scan(*args, **kwargs)  # type: ignore[arg-type]
            finished.append(1)
            return result

        with (
            patch.object(resync, "compute_dir_signature", side_effect=tracked_scan) as scan,
            patch.object(resync, "CHECK_MIN_INTERVAL_SEC", _TEST_CHECK_MIN_INTERVAL_SEC),
        ):
            coordinator.request(immediate=False)
            self.assertTrue(_wait(lambda: len(finished) == 1))
            _wait(lambda: False, timeout_sec=0.1)
            coordinator.request(immediate=False)
            _wait(lambda: False, timeout_sec=0.3)
            self.assertEqual(scan.call_count, 1)
            coordinator.request(immediate=True)
            self.assertTrue(_wait(lambda: scan.call_count == 2))
            self.assertTrue(_wait(lambda: len(finished) == 2))

    def test_request_during_scan_runs_once_more_after_it_finishes(self) -> None:
        """실행 중 요청은 동시에 돌지 않고 끝난 뒤 한 번 더 실행되는지 검증"""
        _write(os.path.join(self.root, "a.mp4"))
        coordinator = self._coordinator()
        real_scan = resync.compute_dir_signature

        def slow_scan(*args: object, **kwargs: object) -> object:
            time.sleep(0.2)
            return real_scan(*args, **kwargs)  # type: ignore[arg-type]

        with patch.object(resync, "compute_dir_signature", side_effect=slow_scan) as scan:
            coordinator.request(immediate=True)
            coordinator.request(immediate=True)
            self.assertTrue(_wait(lambda: scan.call_count == 2))
            _wait(lambda: False, timeout_sec=0.3)
            self.assertEqual(scan.call_count, 2)

    def test_reset_cancels_a_running_scan_without_applying_it(self) -> None:
        """작업 폴더 전환(reset) 뒤에는 진행 중 점검 결과를 적용하지 않고, 새 요청은 정상 실행되는지 검증"""
        keep, gone = os.path.join(self.root, "a.mp4"), os.path.join(self.root, "b.mp4")
        _write(keep)
        _write(gone)
        self.saved = compute_dir_signature(self.root)
        os.remove(gone)
        real_scan = resync.compute_dir_signature
        started = threading.Event()

        def slow_scan(*args: object, **kwargs: object) -> object:
            started.set()
            time.sleep(0.2)
            return real_scan(*args, **kwargs)  # type: ignore[arg-type]

        coordinator = self._coordinator()
        with patch.object(resync, "compute_dir_signature", side_effect=slow_scan):
            coordinator.request(immediate=True)
            self.assertTrue(started.wait(_WAIT_SEC))
            coordinator.reset()
            _wait(lambda: False, timeout_sec=0.4)
        self.assertEqual(self.calls, [])
        self.saved = None
        coordinator.request(immediate=True)
        self.assertTrue(_wait(lambda: self.calls == []))


class PruneKeepsSignatureStableTests(unittest.TestCase):
    """삭제 반영 뒤 저장 시그니처가 현재 값으로 바뀌어, 다음 점검이 변경 없음으로 끝나는지 검증"""

    def test_second_check_after_prune_reports_no_change(self) -> None:
        """prune 뒤 즉시 두 번째 점검은 변경 없음이고 행 목록은 남은 파일만 가짐"""
        with tempfile.TemporaryDirectory() as root:
            _write(os.path.join(root, "a.mp4"))
            gone = os.path.join(root, "b.mp4")
            _write(gone)
            registry = CacheRegistry()
            registry.put(root, TableCache(root, [make_video(filename="a.mp4"), make_video(filename="b.mp4")], include_keyframe=False))
            service = VideoClassifierService(root, registry=registry)
            os.remove(gone)
            current = compute_dir_signature(root)
            self.assertEqual(service.prune_missing(current), 1)
            cache = registry.get(root)
            assert cache is not None
            self.assertEqual([vid.filename for vid in cache.data], ["a.mp4"])
            inputs = service.external_scan_inputs()
            assert inputs is not None
            self.assertIsNone(
                plan_external_resync(inputs.saved_signature, compute_dir_signature(root), include_keyframe=False)
            )


if __name__ == "__main__":
    unittest.main()
