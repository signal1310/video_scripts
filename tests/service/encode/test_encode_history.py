"""재인코딩 작업 기록 저장소: 배치와 항목 기록, 재시도, 스레드별 연결, 분석 캐시 작업과의 격리"""

import sqlite3
import tempfile
import threading
import unittest
from collections.abc import Generator
from contextlib import contextmanager
from unittest import mock

from src.core.encode.encode_state import BATCH_STATE_CLOSED, EncodeState
from src.service.analysis_store import ANALYSIS_TABLE_NAME, AnalysisStore
from src.service.encode import encode_history
from src.service.encode.encode_history import ENCODE_BATCH_TABLE, ENCODE_ITEM_TABLE, EncodeHistory, NewItem


def _items(*names: str) -> list[NewItem]:
    return [
        NewItem(source_path=name, source_size=100 + i, source_mtime_ns=1_000 + i, plan_json="{}")
        for i, name in enumerate(names)
    ]


class _HistoryCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = f"{self._tmp.name}/video_cache.sqlite3"
        self.history = EncodeHistory(self.db_path, sleep=lambda _s: None)

    def tearDown(self) -> None:
        self.history.close()
        self._tmp.cleanup()


class TestBatchAndItemRecords(_HistoryCase):
    """배치 생성, 항목 조회와 갱신, 재개 대상 조회"""

    def test_created_batch_is_listed_as_open_with_pending_items(self) -> None:
        batch_id = self.history.create_batch("C:/work", '{"encoder": "libx264"}', _items("a.mp4", "b.mp4"))
        batches = self.history.list_open_batches()
        self.assertEqual([b.batch_id for b in batches], [batch_id])
        self.assertEqual((batches[0].root_dir, batches[0].settings_json), ("C:/work", '{"encoder": "libx264"}'))
        items = self.history.list_items(batch_id)
        self.assertEqual([i.source_path for i in items], ["a.mp4", "b.mp4"])
        self.assertTrue(all(i.state is EncodeState.PENDING for i in items))
        self.assertEqual((items[0].source_size, items[0].source_mtime_ns), (100, 1_000))

    def test_update_changes_only_given_columns(self) -> None:
        batch_id = self.history.create_batch("C:/work", "{}", _items("a.mp4"))
        self.history.update_item(
            batch_id,
            "a.mp4",
            {
                "state": EncodeState.FAILED,
                "reason": "인코딩 실패",
                "decode_retry": True,
                "output_size": 42,
                "stderr_tail": "line1\nline2",
            },
        )
        item = self.history.list_items(batch_id)[0]
        self.assertIs(item.state, EncodeState.FAILED)
        self.assertEqual((item.reason, item.output_size, item.stderr_tail), ("인코딩 실패", 42, "line1\nline2"))
        self.assertTrue(item.decode_retry)
        self.assertIsNone(item.part_path)

    def test_unknown_column_is_rejected(self) -> None:
        batch_id = self.history.create_batch("C:/work", "{}", _items("a.mp4"))
        with self.assertRaises(ValueError):
            self.history.update_item(batch_id, "a.mp4", {"source_size": 1})

    def test_running_items_go_back_to_pending_on_reset(self) -> None:
        batch_id = self.history.create_batch("C:/work", "{}", _items("a.mp4", "b.mp4"))
        self.history.update_item(batch_id, "a.mp4", {"state": EncodeState.RUNNING, "started_at": "t"})
        self.assertEqual(self.history.reset_running(batch_id), 1)
        states = {i.source_path: i.state for i in self.history.list_items(batch_id)}
        self.assertEqual(states, {"a.mp4": EncodeState.PENDING, "b.mp4": EncodeState.PENDING})

    def test_closed_batch_is_not_listed_as_open(self) -> None:
        batch_id = self.history.create_batch("C:/work", "{}", _items("a.mp4"))
        self.history.close_batch(batch_id)
        self.assertEqual(self.history.list_open_batches(), [])
        with sqlite3.connect(self.db_path) as conn:
            state = conn.execute(f"SELECT state FROM {ENCODE_BATCH_TABLE} WHERE batch_id = ?", (batch_id,)).fetchone()
        self.assertEqual(state, (BATCH_STATE_CLOSED,))

    def test_delete_removes_batch_and_its_items(self) -> None:
        batch_id = self.history.create_batch("C:/work", "{}", _items("a.mp4", "b.mp4"))
        self.history.delete_batch(batch_id)
        self.assertEqual(self.history.list_open_batches(), [])
        self.assertEqual(self.history.list_items(batch_id), [])


@contextmanager
def _locked_by_other_connection(db_path: str) -> Generator[None]:
    """다른 연결이 쓰기 트랜잭션을 쥐고 있는 동안만 잠금을 유지"""
    other = sqlite3.connect(db_path, isolation_level=None)
    other.execute("BEGIN IMMEDIATE")
    try:
        yield
    finally:
        other.execute("ROLLBACK")
        other.close()


class TestLockRetry(_HistoryCase):
    """쓰기 잠금 오류는 재시도하고, 계속 잠겨 있으면 예외를 올림"""

    def test_locked_write_succeeds_after_lock_is_released(self) -> None:
        batch_id = self.history.create_batch("C:/work", "{}", _items("a.mp4"))
        other = sqlite3.connect(self.db_path, isolation_level=None)
        other.execute("BEGIN IMMEDIATE")
        released: list[float] = []

        def release_on_first_sleep(delay: float) -> None:
            released.append(delay)
            other.execute("ROLLBACK")

        retry_history = EncodeHistory(self.db_path, sleep=release_on_first_sleep)
        try:
            with mock.patch.object(encode_history, "SQLITE_BUSY_TIMEOUT_SEC", 0.01):
                retry_history.update_item(batch_id, "a.mp4", {"reason": "재시도 후 기록"})
        finally:
            other.close()
            retry_history.close()
        self.assertEqual(len(released), 1)
        self.assertEqual(self.history.list_items(batch_id)[0].reason, "재시도 후 기록")

    def test_persistent_lock_raises_after_all_attempts(self) -> None:
        batch_id = self.history.create_batch("C:/work", "{}", _items("a.mp4"))
        delays: list[float] = []
        strict = EncodeHistory(self.db_path, sleep=delays.append)
        try:
            with (
                mock.patch.object(encode_history, "SQLITE_BUSY_TIMEOUT_SEC", 0.01),
                _locked_by_other_connection(self.db_path),
                self.assertRaises(sqlite3.OperationalError),
            ):
                strict.update_item(batch_id, "a.mp4", {"reason": "기록 못함"})
        finally:
            strict.close()
        self.assertEqual(len(delays), encode_history.WRITE_RETRY_ATTEMPTS - 1)
        self.assertEqual(delays, sorted(delays))

    def test_non_lock_errors_are_not_retried(self) -> None:
        """DB 파일 경로가 디렉터리면 잠금이 아니므로 재시도 없이 바로 올림"""
        delays: list[float] = []
        history = EncodeHistory(self._tmp.name, sleep=delays.append)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                history.create_batch("C:/work", "{}", _items("a.mp4"))
        finally:
            history.close()
        self.assertEqual(delays, [])


class TestThreadedWrites(_HistoryCase):
    """워커 스레드마다 연결을 따로 써도 모든 갱신이 남음"""

    def test_parallel_updates_from_threads_all_persist(self) -> None:
        names = [f"item{i}.mp4" for i in range(8)]
        batch_id = self.history.create_batch("C:/work", "{}", _items(*names))
        errors: list[BaseException] = []

        def worker(name: str) -> None:
            try:
                self.history.update_item(batch_id, name, {"state": EncodeState.DONE, "output_size": 1})
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(name,)) for name in names]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(errors, [])
        self.assertTrue(all(i.state is EncodeState.DONE for i in self.history.list_items(batch_id)))


class TestIsolationFromAnalysisCache(_HistoryCase):
    """분석 캐시 비우기와 스키마 재생성은 재인코딩 작업 기록을 건드리지 않음"""

    def test_clearing_analysis_cache_keeps_batches_and_items(self) -> None:
        batch_id = self.history.create_batch("C:/work", "{}", _items("a.mp4", "b.mp4"))
        store = AnalysisStore(self.db_path)
        try:
            store.clear()
        finally:
            store.close()
        self.assertEqual(len(self.history.list_items(batch_id)), 2)
        self.assertEqual(len(self.history.list_open_batches()), 1)

    def test_analysis_schema_regeneration_keeps_batches_and_items(self) -> None:
        """사용자 버전이 달라 분석 테이블이 다시 만들어져도 작업 기록 테이블은 그대로"""
        batch_id = self.history.create_batch("C:/work", "{}", _items("a.mp4"))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA user_version = 0")
            conn.execute(f"DROP TABLE IF EXISTS {ANALYSIS_TABLE_NAME}")
        store = AnalysisStore(self.db_path)
        try:
            self.assertEqual(store.count(), 0)
        finally:
            store.close()
        self.assertEqual([i.source_path for i in self.history.list_items(batch_id)], ["a.mp4"])
        with sqlite3.connect(self.db_path) as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertIn(ENCODE_ITEM_TABLE, tables)


if __name__ == "__main__":
    unittest.main(verbosity=2)
