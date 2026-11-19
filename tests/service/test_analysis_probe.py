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
