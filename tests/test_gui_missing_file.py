"""창에서 파일이 사라진 행을 다룰 때 안내 문구, 목록 재확인, 삭제 반영 뒤 행 제거를 검증"""

import os
import tempfile
import time
import unittest
from collections.abc import Callable

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.gui.main_window import MainWindow
from src.gui.widgets.table_actions import MSG_MISSING_EXCLUDED, MSG_MISSING_REFRESH
from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache
from tests._factories import make_video
from tests._gui_helpers import isolate_app_state
from tests.test_external_resync import drain_scan_threads

_APP = QApplication.instance() or QApplication([])
_WAIT_SEC: float = 10.0


def _wait(predicate: Callable[[], bool]) -> bool:
    end = time.monotonic() + _WAIT_SEC
    while not predicate():
        if time.monotonic() > end:
            return False
        _APP.processEvents()
        time.sleep(0.01)
    return True


class MissingFileWindowTests(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.addCleanup(drain_scan_threads)
        self.root = os.path.join(tmp.name, "work")
        os.makedirs(self.root)
        isolate_app_state(self, tmp.name)
        for name in ("a.mp4", "b.mp4"):
            with open(os.path.join(self.root, name), "wb") as f:
                f.write(b"v")
        self.videos = [make_video(filename="a.mp4"), make_video(filename="b.mp4")]
        self.mw = MainWindow(initial_dir=self.root)
        self.addCleanup(self.mw.close)
        self.mw.set_working_directory(self.root)
        DEFAULT_CACHE_REGISTRY.put(self.root, TableCache(self.root, list(self.videos), include_keyframe=False))
        clf = self.mw.ensure_classifier()
        assert clf is not None
        self.mw.apply_report(clf.get_summary_report(ensure_keyframe=False))

    def _remove_b(self) -> None:
        os.remove(os.path.join(self.root, "b.mp4"))

    def test_selecting_only_a_missing_row_says_so_and_rechecks_the_list(self) -> None:
        """없는 파일 행만 선택하면 목록 재확인 안내를 띄우고, 점검으로 그 행이 표에서 사라지는지 검증"""
        self._remove_b()
        self.mw.table_view.select_filenames({"b.mp4"})
        self.assertTrue(self.mw.table_view.notify_missing_selected())
        self.assertEqual(self.mw.footer_bar.lbl_status.text(), MSG_MISSING_REFRESH)
        report = self.mw.current_report
        assert report is not None
        self.assertTrue(_wait(lambda: self._evaluation_names() == ["a.mp4"]))

    def test_copy_with_some_missing_rows_excludes_them_and_says_how_many(self) -> None:
        """복사 대상 일부가 없으면 그 파일은 빼고 제외 건수를 알리는지 검증"""
        self._remove_b()
        self.mw.table_view.select_filenames({"a.mp4", "b.mp4"})
        copied = self.mw.table_view.copy_selected_paths()
        self.assertEqual(copied, 1)
        self.assertEqual(self.mw.footer_bar.lbl_status.text(), MSG_MISSING_EXCLUDED.format(count=1))

    def _evaluation_names(self) -> list[str]:
        report = self.mw.current_report
        if report is None:
            return []
        return sorted(ev.video.filename for ev in report.evaluations)


if __name__ == "__main__":
    unittest.main()
