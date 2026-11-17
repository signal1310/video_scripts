"""저장 장치 대기열 디스패처가 대기열별 동시 실행 상한, 결과 순서, 취소, 예외 전파를 지키는지 가짜 워커로 검증"""

import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from src.core import storage_probe
from src.core.exceptions import DomainInterruptedError
from src.core.prober import _run_cancellable_pool
from src.core.storage_probe import StorageInfo, StorageKind, concurrency_limit, plan_lanes

_SLEEP_SEC: float = 0.005


class TestLaneDispatch(unittest.TestCase):
    """대기열 상한과 완료 처리 순서를 가짜 워커로 확인"""

    def test_per_lane_peak_concurrency_respects_limits(self) -> None:
        """상한이 있는 대기열은 동시에 상한을 넘지 않고, 상한이 없는 대기열은 모두 실행되는지 확인"""
        lock = threading.Lock()
        active: dict[int, int] = {0: 0, 1: 0, 2: 0}
        peak: dict[int, int] = {0: 0, 1: 0, 2: 0}

        def worker(task: tuple[int, int], _ev: threading.Event) -> tuple[int, int]:
            lane = task[0]
            with lock:
                active[lane] += 1
                peak[lane] = max(peak[lane], active[lane])
            time.sleep(_SLEEP_SEC)
            with lock:
                active[lane] -= 1
            return task

        tasks = [(i % 3, i) for i in range(30)]
        results: dict[int, tuple[int, int]] = {}
        _run_cancellable_pool(
            tasks,
            worker,
            lambda idx, res, _c, _t: results.__setitem__(idx, res),
            lanes=[lane for lane, _ in tasks],
            lane_limits={0: 1, 1: 2},
        )
        self.assertEqual(len(results), len(tasks))
        self.assertLessEqual(peak[0], 1)
        self.assertLessEqual(peak[1], 2)
        self.assertGreaterEqual(peak[2], 1)
        for idx, task in enumerate(tasks):
            self.assertEqual(results[idx], task)

    def test_without_lanes_every_task_runs_once(self) -> None:
        """대기열 정보가 없으면 기존처럼 모든 작업이 한 번씩 실행되는지 확인"""
        seen: list[int] = []
        _run_cancellable_pool(
            list(range(12)),
            lambda task, _ev: task,
            lambda _idx, res, _c, _t: seen.append(res),
        )
        self.assertEqual(sorted(seen), list(range(12)))

    def test_cancel_stops_submitting_remaining_tasks(self) -> None:
        """취소 요청 뒤에는 아직 투입하지 않은 작업이 실행되지 않고 취소 예외가 전파되는지 확인"""
        started: list[int] = []
        lock = threading.Lock()
        done_count = [0]

        def worker(task: int, _ev: threading.Event) -> int:
            with lock:
                started.append(task)
            time.sleep(_SLEEP_SEC)
            return task

        def on_item(_idx: int, _res: int, _c: int, _t: int) -> None:
            done_count[0] += 1

        with self.assertRaises(DomainInterruptedError):
            _run_cancellable_pool(
                list(range(60)),
                worker,
                on_item,
                is_cancelled=lambda: done_count[0] >= 3,
                lanes=[0] * 60,
                lane_limits={0: 1},
            )
        self.assertLess(len(started), 60)

    def test_worker_exception_propagates(self) -> None:
        """워커의 예외가 호출자에게 그대로 전파되는지 확인"""

        def worker(task: int, _ev: threading.Event) -> int:
            if task == 3:
                raise ValueError("실패한 작업")
            return task

        with self.assertRaises(ValueError):
            _run_cancellable_pool(
                list(range(8)),
                worker,
                lambda _idx, _res, _c, _t: None,
                lanes=[0] * 8,
                lane_limits={0: 2},
            )


class TestLanePolicy(unittest.TestCase):
    """장치 종류별 상한과 대기열 묶음 규칙을 표 기반으로 검증"""

    def test_concurrency_limit_table(self) -> None:
        """SSD와 판별 실패는 제한 없음, 네트워크는 CPU 기준 중간값, HDD는 정책 상수를 따르는지 확인"""
        self.assertIsNone(concurrency_limit(StorageKind.SSD, 8))
        self.assertIsNone(concurrency_limit(StorageKind.UNKNOWN, 8))
        self.assertEqual(concurrency_limit(StorageKind.NETWORK, 4), 4)
        self.assertEqual(concurrency_limit(StorageKind.NETWORK, 1), 2)
        with patch.object(storage_probe, "HDD_LANE_LIMIT", 2):
            self.assertEqual(concurrency_limit(StorageKind.HDD, 8), 2)
        self.assertIsNone(concurrency_limit(StorageKind.HDD, 8))

    def test_plan_lanes_groups_paths_by_physical_disk(self) -> None:
        """같은 물리 디스크의 두 폴더는 한 대기열로 묶이고 다른 디스크는 따로 묶이는지 확인"""
        with tempfile.TemporaryDirectory() as tmp:
            first = os.path.join(tmp, "a")
            second = os.path.join(tmp, "b")
            other = os.path.join(tmp, "c")
            infos = {
                first: StorageInfo(StorageKind.HDD, None, 1),
                second: StorageInfo(StorageKind.HDD, None, 1),
                other: StorageInfo(StorageKind.SSD, None, 2),
            }
            with patch.object(storage_probe, "probe_storage", side_effect=lambda p: infos[p]), patch.object(
                storage_probe, "HDD_LANE_LIMIT", 1
            ):
                lanes, limits = plan_lanes([first, other, second], cpu_count=4)
        self.assertEqual(lanes, [0, 1, 0])
        self.assertEqual(limits, {0: 1})

    @unittest.skipUnless(os.name == "nt", "드라이브 문자 경로는 Windows에서만 의미가 있음")
    def test_drive_letters_on_same_disk_share_one_lane(self) -> None:
        """드라이브 문자가 달라도 같은 물리 디스크 번호면 한 대기열로 묶여 상한을 공유하는지 확인"""
        paths = ["C:\\data\\a", "D:\\data\\b", "E:\\data\\c"]
        infos = {
            paths[0]: StorageInfo(StorageKind.HDD, None, 3),
            paths[1]: StorageInfo(StorageKind.HDD, None, 3),
            paths[2]: StorageInfo(StorageKind.SSD, None, 4),
        }
        with patch.object(storage_probe, "probe_storage", side_effect=lambda p: infos[p]), patch.object(
            storage_probe, "HDD_LANE_LIMIT", 1
        ):
            lanes, limits = plan_lanes(paths, cpu_count=4)
        self.assertEqual(lanes, [0, 0, 1])
        self.assertEqual(limits, {0: 1})


if __name__ == "__main__":
    unittest.main()
