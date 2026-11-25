import dataclasses
import os
import shutil
import tempfile
import unittest
from collections.abc import Sequence
from typing import Any
from unittest.mock import patch

from src.core.models import VideoProps
from src.service import analysis_probe as probe_mod
from src.service.analysis_store import AnalysisStore
from src.service.cache import TableCache

_CONTENT_A: bytes = b"video-content-A" * 4096
_CONTENT_B: bytes = b"video-content-B-longer" * 4096


def _fake_props(filename: str, *, include_keyframe: bool, width: int = 1920) -> VideoProps:
    return VideoProps(
        filename=filename,
        width=width,
        height=1080,
        rotate_type=0,
        fps=30.0,
        vid_kbps=2500,
        aud_kbps=128,
        vid_size_MB=1.5,
        duration=12.0,
        codec="h264",
        keyframe_interval=2.0 if include_keyframe else None,
    )


class _ProbeRecorder:
    """probe_files 대체: 호출된 파일 목록을 기록하고 가짜 메타데이터 반환"""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(
        self,
        root_dir: str,
        filenames: Sequence[str],
        include_keyframe_interval: bool = False,
        on_progress: Any = None,
        is_cancelled: Any = None,
        **_kwargs: Any,
    ) -> list[VideoProps]:
        self.calls.append(list(filenames))
        return [_fake_props(name, include_keyframe=include_keyframe_interval) for name in filenames]

    @property
    def probed(self) -> list[str]:
        return [name for call in self.calls for name in call]


class _ProbeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = os.path.join(tmp.name, "work")
        os.makedirs(self.root)
        self.store = AnalysisStore(os.path.join(tmp.name, "data", "video_cache.sqlite3"))
        self.addCleanup(self.store.close)
        self.recorder = _ProbeRecorder()
        patcher = patch.object(probe_mod, "probe_files", side_effect=self.recorder)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write(self, name: str, content: bytes) -> None:
        with open(os.path.join(self.root, name), "wb") as f:
            f.write(content)

    def _scan(self, *, include_keyframe: bool = False, reuse: bool = True) -> list[VideoProps]:
        return probe_mod.probe_directory_with_store(
            self.root,
            include_keyframe=include_keyframe,
            store=self.store,
            reuse_store=reuse,
        )


class TestNameLookup(_ProbeTestCase):
    def test_first_scan_probes_everything_and_second_scan_reuses(self) -> None:
        self._write("a.mp4", _CONTENT_A)
        self._write("b.mp4", _CONTENT_B)
        first = self._scan()
        self.assertEqual(self.recorder.probed, ["a.mp4", "b.mp4"])
        self.assertEqual(self.store.count(), 2)

        self.recorder.calls.clear()
        second = self._scan()
        self.assertEqual(self.recorder.probed, [])
        self.assertEqual(second, first)

    def test_results_keep_directory_order(self) -> None:
        for name in ("c.mp4", "a.mp4", "b.mp4"):
            self._write(name, _CONTENT_A)
        self.assertEqual([p.filename for p in self._scan()], ["a.mp4", "b.mp4", "c.mp4"])

    def test_keyframe_request_reprobes_rows_without_keyframe(self) -> None:
        self._write("k.mp4", _CONTENT_A)
        without = self._scan(include_keyframe=False)
        self.assertIsNone(without[0].keyframe_interval)

        self.recorder.calls.clear()
        with_kf = self._scan(include_keyframe=True)
        self.assertEqual(self.recorder.probed, ["k.mp4"])
        self.assertEqual(with_kf[0].keyframe_interval, 2.0)

    def test_keyframe_not_requested_hides_stored_keyframe(self) -> None:
        self._write("k.mp4", _CONTENT_A)
        self._scan(include_keyframe=True)
        self.recorder.calls.clear()
        fast = self._scan(include_keyframe=False)
        self.assertEqual(self.recorder.probed, [])
        self.assertIsNone(fast[0].keyframe_interval)
        self.assertIsNotNone(self.store.fetch_by_names(["k.mp4"])[0].props.keyframe_interval)

    def test_changed_mtime_with_new_content_is_reprobed(self) -> None:
        self._write("m.mp4", _CONTENT_A)
        self._scan()
        self._write("m.mp4", _CONTENT_B)
        self.recorder.calls.clear()
        self._scan()
        self.assertEqual(self.recorder.probed, ["m.mp4"])

    def test_reuse_disabled_probes_all_and_overwrites(self) -> None:
        self._write("r.mp4", _CONTENT_A)
        self._scan()
        self.recorder.calls.clear()
        self._scan(reuse=False)
        self.assertEqual(self.recorder.probed, ["r.mp4"])
        self.assertEqual(self.store.count(), 1)


class TestRenameReuse(_ProbeTestCase):
    def test_renamed_file_reuses_row_by_fingerprint(self) -> None:
        self._write("old_name.mp4", _CONTENT_A)
        original = self._scan(include_keyframe=True)[0]

        os.rename(os.path.join(self.root, "old_name.mp4"), os.path.join(self.root, "new_name.mp4"))
        self.recorder.calls.clear()
        renamed = self._scan(include_keyframe=True)

        self.assertEqual(self.recorder.probed, [])
        self.assertEqual(renamed[0], dataclasses.replace(original, filename="new_name.mp4"))
        keys = [e.key[0] for e in self.store.fetch_by_names(["old_name.mp4", "new_name.mp4"])]
        self.assertEqual(keys, ["new_name.mp4"])

    def test_renamed_and_copied_file_with_new_mtime_still_reuses(self) -> None:
        self._write("src.mp4", _CONTENT_A)
        self._scan(include_keyframe=True)
        shutil.copyfile(os.path.join(self.root, "src.mp4"), os.path.join(self.root, "copy.mp4"))
        os.remove(os.path.join(self.root, "src.mp4"))
        self.recorder.calls.clear()
        self._scan(include_keyframe=True)
        self.assertEqual(self.recorder.probed, [])

    def test_same_size_but_different_content_is_not_reused(self) -> None:
        self._write("first.mp4", _CONTENT_A)
        self._scan()
        os.remove(os.path.join(self.root, "first.mp4"))
        self._write("second.mp4", bytes(reversed(_CONTENT_A)))
        self.recorder.calls.clear()
        self._scan()
        self.assertEqual(self.recorder.probed, ["second.mp4"])

    def test_identical_copies_each_consume_one_row(self) -> None:
        self._write("dup1.mp4", _CONTENT_A)
        self._write("dup2.mp4", _CONTENT_A)
        self._scan()
        os.rename(os.path.join(self.root, "dup1.mp4"), os.path.join(self.root, "x1.mp4"))
        os.rename(os.path.join(self.root, "dup2.mp4"), os.path.join(self.root, "x2.mp4"))
        self.recorder.calls.clear()
        results = self._scan()
        self.assertEqual(self.recorder.probed, [])
        self.assertEqual(sorted(p.filename for p in results), ["x1.mp4", "x2.mp4"])
        self.assertEqual(self.store.count(), 2)


class TestPersistence(_ProbeTestCase):
    def test_persist_keyframes_updates_row_after_memory_populate(self) -> None:
        self._write("late.mp4", _CONTENT_A)
        cache = TableCache.probe(self.root, include_keyframe=False, store=self.store)
        self.assertIsNone(self.store.fetch_by_names(["late.mp4"])[0].props.keyframe_interval)

        def fake_populate(table: list[VideoProps], *_args: Any, **_kwargs: Any) -> None:
            for v in table:
                v.keyframe_interval = 4.5

        with patch("src.service.cache.populate_keyframes", side_effect=fake_populate):
            cache.update_keyframe(True, store=self.store)
        self.assertEqual(self.store.fetch_by_names(["late.mp4"])[0].props.keyframe_interval, 4.5)

class TestPrescanReuse(_ProbeTestCase):
    """복원 판정이 읽은 파일 목록과 저장 행을 이어 받을 때 결과가 같고 DB를 다시 읽지 않는지 검증"""

    def _stored(self) -> probe_mod.StoredRows:
        stored = probe_mod.load_stored_rows(self.store, self.root)
        assert stored is not None
        return stored

    def test_prescan_gives_the_same_result_and_skips_the_name_query(self) -> None:
        """판정 행을 넘기면 이름 조회 없이 같은 결과를 내는지 검증"""
        self._write("a.mp4", _CONTENT_A)
        self._write("b.mp4", _CONTENT_B)
        self._scan()
        stored = self._stored()

        self.recorder.calls.clear()
        with patch.object(
            self.store, "fetch_by_names", side_effect=AssertionError("행 조회를 다시 하면 안 됨")
        ):
            reused = probe_mod.probe_directory_with_store(
                self.root,
                include_keyframe=False,
                store=self.store,
                prescan=stored,
            )
        self.assertEqual(self.recorder.probed, [])
        self.assertEqual([p.filename for p in reused], ["a.mp4", "b.mp4"])
        self.assertEqual(reused, probe_mod.probe_directory_with_store(
            self.root, include_keyframe=False, store=self.store
        ))

    def test_changed_file_is_still_probed_when_prescan_is_reused(self) -> None:
        """판정 뒤 바뀐 파일은 판정 행을 넘겨도 다시 분석하는지 검증"""
        self._write("changed.mp4", _CONTENT_A)
        self._scan()
        stored = self._stored()

        self._write("changed.mp4", _CONTENT_B)
        self.recorder.calls.clear()
        results = probe_mod.probe_directory_with_store(
            self.root,
            include_keyframe=False,
            store=self.store,
            prescan=stored,
        )
        self.assertEqual(self.recorder.probed, ["changed.mp4"])
        self.assertEqual([p.filename for p in results], ["changed.mp4"])

    def test_renamed_file_reuses_row_by_fingerprint_with_prescan(self) -> None:
        """판정 행 재사용 경로에서도 이름 변경 지문 대조가 그대로 도는지 검증"""
        self._write("old_name.mp4", _CONTENT_A)
        self._scan()
        stored = self._stored()

        os.rename(
            os.path.join(self.root, "old_name.mp4"), os.path.join(self.root, "new_name.mp4")
        )
        self.recorder.calls.clear()
        results = probe_mod.probe_directory_with_store(
            self.root,
            include_keyframe=False,
            store=self.store,
            prescan=stored,
        )
        self.assertEqual(self.recorder.probed, [])
        self.assertEqual([p.filename for p in results], ["new_name.mp4"])

    def test_entries_out_returns_the_listed_files(self) -> None:
        """조회한 파일 목록을 넘겨 받아 디렉터리 시그니처로 쓸 수 있는지 검증"""
        self._write("a.mp4", _CONTENT_A)
        entries: list[probe_mod.FileEntry] = []
        probe_mod.probe_directory_with_store(
            self.root,
            include_keyframe=False,
            store=self.store,
            entries_out=entries,
        )
        self.assertEqual([entry.filename for entry in entries], ["a.mp4"])
        self.assertEqual(
            [(entry.filename, entry.filesize_bytes, entry.mtime_ns) for entry in entries],
            [(entry.filename, entry.filesize_bytes, entry.mtime_ns) for entry in self._stored().file_entries],
        )


class TestStoredRowsScope(unittest.TestCase):
    """판정이 담은 파일 목록 시그니처와 범위 비교를 검증"""

    def _stored(
        self, root_dir: str, *, recursive: bool, excluded: tuple[str, ...] = ()
    ) -> probe_mod.StoredRows:
        entries = (
            probe_mod.FileEntry("a.mp4", 10, 100),
            probe_mod.FileEntry("b.mp4", 20, 200),
        )
        return probe_mod.StoredRows(
            (),
            frozenset(),
            False,
            entries,
            (),
            probe_mod.RestoreScope(root_dir, recursive, excluded),
        )

    def test_dir_signature_matches_the_listed_files(self) -> None:
        """판정 목록이 (이름, 크기, 수정 시각) 시그니처로 그대로 바뀌는지 검증"""
        stored = self._stored("C:/work", recursive=False)
        self.assertEqual(
            stored.dir_signature,
            (("a.mp4", 10, 100), ("b.mp4", 20, 200)),
        )

    def test_dir_signature_is_none_without_a_listing(self) -> None:
        """파일 목록을 담지 않은 결과는 시그니처 없음(None)을 반환해 호출자가 직접 계산하게 하는지 검증"""
        stored = probe_mod.StoredRows((), frozenset(), False)
        self.assertIsNone(stored.dir_signature)
        self.assertFalse(stored.covers("C:/work", recursive=False, excluded_dirnames=()))

    def test_covers_requires_the_same_root_recursive_and_excluded_names(self) -> None:
        """루트 경로, 하위 폴더 포함, 제외 폴더가 모두 같을 때만 범위가 같다고 보는지 검증"""
        stored = self._stored("C:/work", recursive=False, excluded=("archive",))
        self.assertTrue(
            stored.covers("C:\\work", recursive=False, excluded_dirnames=("archive",))
        )
        self.assertFalse(
            stored.covers("C:/work", recursive=True, excluded_dirnames=("archive",))
        )
        self.assertFalse(
            stored.covers("C:/work", recursive=False, excluded_dirnames=("archive", "out"))
        )
        self.assertFalse(
            stored.covers("C:/other", recursive=False, excluded_dirnames=("archive",))
        )
        self.assertFalse(stored.covers("C:/work", recursive=False, excluded_dirnames=()))


class TestStoreFailureFallback(_ProbeTestCase):
    def test_unusable_store_still_returns_fresh_results(self) -> None:
        self._write("a.mp4", _CONTENT_A)
        with tempfile.TemporaryDirectory() as tmp:
            blocker = os.path.join(tmp, "file_not_dir")
            with open(blocker, "wb") as f:
                f.write(b"x")
            broken = AnalysisStore(os.path.join(blocker, "video_cache.sqlite3"))
            self.addCleanup(broken.close)
            with self.assertLogs("src.service", level="WARNING"):
                results = probe_mod.probe_directory_with_store(
                    self.root, include_keyframe=False, store=broken
                )
        self.assertEqual([p.filename for p in results], ["a.mp4"])


if __name__ == "__main__":
    unittest.main()


class TestAnalysisSummaryLog(_ProbeTestCase):
    """분석 한 번의 결과 요약 로그가 캐시 재사용, 새로 분석, 분석 실패 수를 정확히 담는지 검증"""

    def test_summary_counts_reused_fresh_and_failed(self) -> None:
        self._write("a.mp4", _CONTENT_A)
        self._write("b.mp4", _CONTENT_B)
        self._scan()

        self._write("c.mp4", b"video-content-C" * 2048)
        def probe_without_c(root: str, names: Sequence[str], *_args: Any, **_kwargs: Any) -> list[VideoProps]:
            return [_fake_props(name, include_keyframe=False) for name in names if name != "c.mp4"]

        with (
            patch.object(probe_mod, "probe_files", side_effect=probe_without_c),
            self.assertLogs("src.service.analysis_probe", level="INFO") as logs,
        ):
            self._scan()

        self.assertIn(
            "INFO:src.service.analysis_probe:분석 결과: 전체 3개 (캐시 재사용 2, 새로 분석 0, 분석 실패 1)",
            logs.output,
        )
