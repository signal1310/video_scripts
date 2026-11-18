"""시작 시 이전 분석 결과 복원, 바뀐 파일만 재분석하는 규칙과 메인 창 연동을 검증하는 테스트"""

import os
import tempfile
import time
import unittest
from collections.abc import Callable
from unittest.mock import patch

from PyQt6.QtWidgets import QApplication

from src.classifier import VideoClassifier
from src.gui.main_window import MainWindow
from src.service.analysis_probe import resolve_restore_keyframe_mode
from src.service.analysis_store import AnalysisStore, StoredEntry, compute_fingerprint
from src.service.analyzed_dirs import is_analyzed_dir, mark_analyzed_dir
from src.service.cache import DEFAULT_CACHE_REGISTRY
from tests._factories import make_video
from tests._gui_helpers import isolate_app_state
from tests._sandbox import reset_sandbox_files

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

_RESTORE_WAIT_SEC: float = 10.0
_QUIET_WAIT_SEC: float = 0.3
_POLL_INTERVAL_SEC: float = 0.01


def _write_clip(directory: str, name: str, payload: bytes) -> str:
    """테스트용 비디오 파일을 만들고 전체 경로 반환"""
    path = os.path.join(directory, name)
    with open(path, "wb") as f:
        f.write(payload)
    return path


def _seed_row(store: AnalysisStore, path: str, *, keyframe_interval: float | None) -> None:
    """파일의 현재 이름, 크기, 수정 시각, 지문으로 분석 캐시 행을 기록"""
    stat = os.stat(path)
    name = os.path.basename(path)
    props = make_video(filename=name, keyframe_interval=keyframe_interval)
    entry = StoredEntry(
        name,
        stat.st_size,
        stat.st_mtime_ns,
        compute_fingerprint(path, stat.st_size),
        props,
    )
    store.apply((), [entry])


def _wait_until(predicate: Callable[[], bool], timeout_sec: float) -> bool:
    """이벤트를 처리하며 조건이 참이 될 때까지 기다리고 결과 반환"""
    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return True
        time.sleep(_POLL_INTERVAL_SEC)
    QApplication.processEvents()
    return predicate()


class TestResolveRestoreKeyframeMode(unittest.TestCase):
    """분석 캐시 행과 현재 파일의 일치 규칙으로 복원 모드를 고르는지 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = os.path.join(self._tmp.name, "work")
        os.makedirs(self.work)
        self.store = AnalysisStore(os.path.join(self._tmp.name, "video_cache.sqlite3"))
        self.addCleanup(self.store.close)

    def test_returns_none_without_matching_rows(self) -> None:
        """분석 캐시가 비어 있으면 복원 대상이 없어 None을 반환함을 검증"""
        _write_clip(self.work, "a.mp4", b"a" * 10)
        self.assertIsNone(resolve_restore_keyframe_mode(self.store, self.work))

    def test_fast_rows_restore_in_fast_mode(self) -> None:
        """키프레임 없이 저장된 행만 있으면 빠른 분석으로 복원함을 검증"""
        path = _write_clip(self.work, "a.mp4", b"a" * 10)
        _seed_row(self.store, path, keyframe_interval=None)
        self.assertFalse(resolve_restore_keyframe_mode(self.store, self.work))

    def test_full_rows_restore_in_full_mode(self) -> None:
        """키프레임까지 측정된 행이 모두 있으면 정밀 분석으로 복원함을 검증"""
        path = _write_clip(self.work, "a.mp4", b"a" * 10)
        _seed_row(self.store, path, keyframe_interval=2.0)
        self.assertTrue(resolve_restore_keyframe_mode(self.store, self.work))

    def test_mixed_rows_fall_back_to_fast_mode(self) -> None:
        """일부 행만 키프레임을 가지면 키프레임 값이 빠지지 않도록 정밀 분석으로 복원하지 않음을 검증"""
        full = _write_clip(self.work, "a.mp4", b"a" * 10)
        fast = _write_clip(self.work, "b.mp4", b"b" * 10)
        _seed_row(self.store, full, keyframe_interval=2.0)
        _seed_row(self.store, fast, keyframe_interval=None)
        self.assertFalse(resolve_restore_keyframe_mode(self.store, self.work))

    def test_changed_file_size_breaks_the_match(self) -> None:
        """파일 크기가 바뀌면 이전 행과 맞지 않아 복원 대상이 없음을 검증"""
        path = _write_clip(self.work, "a.mp4", b"a" * 10)
        _seed_row(self.store, path, keyframe_interval=None)
        with open(path, "ab") as f:
            f.write(b"more")
        self.assertIsNone(resolve_restore_keyframe_mode(self.store, self.work))

    def test_rows_of_other_names_are_ignored(self) -> None:
        """현재 폴더에 없는 파일의 행은 복원 판단에 쓰지 않음을 검증"""
        other = _write_clip(self._tmp.name, "other.mp4", b"o" * 10)
        _seed_row(self.store, other, keyframe_interval=None)
        _write_clip(self.work, "a.mp4", b"a" * 10)
        self.assertIsNone(resolve_restore_keyframe_mode(self.store, self.work))


class TestStartupRestoreWindow(unittest.TestCase):
    """메인 창을 띄울 때 이전 분석 결과가 표에 복원되고 ffprobe가 불필요하게 돌지 않는지 검증"""

    def setUp(self) -> None:
        reset_sandbox_files()
        self.addCleanup(reset_sandbox_files)
        DEFAULT_CACHE_REGISTRY.invalidate()
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.work = os.path.join(self._tmp.name, "work")
        os.makedirs(self.work)
        data_dir = os.path.join(self._tmp.name, "data")
        os.makedirs(data_dir)
        isolate_app_state(self, self._tmp.name)
        # - 분류 세션, 작업 태스크, 창 복원이 같은 저장소 인스턴스를 쓰도록 주입해 정리 때 연결을 닫음
        self.store = AnalysisStore(os.path.join(data_dir, "video_cache.sqlite3"))
        self.addCleanup(self.store.close)
        marker = patch("src.service.analyzed_dirs.get_data_dir", return_value=data_dir)
        marker.start()
        self.addCleanup(marker.stop)
        for target in (
            "src.classifier.open_default_store",
            "src.gui.session_controller.open_default_store",
            "src.gui.task_runner.open_default_store",
            "src.gui.restore_resolver.open_default_store",
        ):
            shared = patch(target, return_value=self.store)
            shared.start()
            self.addCleanup(shared.stop)

    def _seed_store(self, paths: list[str]) -> None:
        for path in paths:
            _seed_row(self.store, path, keyframe_interval=None)

    def _mark_analyzed(self, root: str | None = None) -> None:
        """분석을 끝낸 적 있는 폴더로 기록해 자동 복원 대상으로 만듦"""
        mark_analyzed_dir(root or self.work)

    def _open_window(self, root: str | None = None) -> MainWindow:
        clf = VideoClassifier(root_dir=root or self.work)
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        return mw

    def test_restore_progress_is_visible_before_resolution_finishes(self) -> None:
        """판정이 끝나기 전에도 하단 진행 막대가 보여 시작 직후부터 복원 진행이 드러나는지 검증"""
        path = _write_clip(self.work, "clip.mp4", b"c" * 64)
        self._seed_store([path])
        self._mark_analyzed()
        with patch("src.gui.restore_resolver.RestoreModeResolver.start"):
            mw = self._open_window()
            # - 복원 확인은 0ms 타이머로 예약되므로 이벤트를 한 번 처리해 실행
            QApplication.processEvents()
        self.assertTrue(mw.footer_bar.progress_bar.isVisibleTo(mw))

    def test_restore_progress_clears_when_nothing_to_restore(self) -> None:
        """판정 결과 복원 대상이 없으면 미리 켠 진행 막대를 끄는지 검증"""
        path = _write_clip(self.work, "clip.mp4", b"c" * 64)
        self._seed_store([path])
        self._mark_analyzed()
        with patch("src.gui.restore_resolver.RestoreModeResolver.start"):
            mw = self._open_window()
        mw._restore_flow.apply_resolved(None)
        self.assertFalse(mw.footer_bar.progress_bar.isVisibleTo(mw))

    def test_restore_status_text_clears_when_nothing_to_restore(self) -> None:
        """판정 결과 복원 대상이 없으면 바쁨 상태에서 띄운 복원 진행 문구가 남지 않는지 검증"""
        path = _write_clip(self.work, "clip.mp4", b"c" * 64)
        self._seed_store([path])
        self._mark_analyzed()
        with patch("src.gui.restore_resolver.RestoreModeResolver.start"):
            mw = self._open_window()
            # - 복원 확인은 0ms 타이머로 예약되므로 이벤트를 한 번 처리해 바쁨 문구를 먼저 띄움
            QApplication.processEvents()
        self.assertIn("복원 진행 중", mw.footer_bar.lbl_status.text())
        mw._restore_flow.apply_resolved(None)
        self.assertEqual(mw.footer_bar.lbl_status.text(), "")

    def test_unchanged_folder_restores_table_without_ffprobe(self) -> None:
        """파일이 그대로면 시작만으로 표가 채워지고 ffprobe는 한 번도 호출되지 않음을 검증"""
        path = _write_clip(self.work, "clip.mp4", b"c" * 64)
        self._seed_store([path])
        self._mark_analyzed()
        with patch("src.service.analysis_probe.probe_files") as probe:
            mw = self._open_window()
            restored = _wait_until(lambda: mw.current_report is not None, _RESTORE_WAIT_SEC)
        self.assertTrue(restored)
        assert mw.current_report is not None
        self.assertEqual(
            [ev.video.filename for ev in mw.current_report.evaluations], ["clip.mp4"]
        )
        probe.assert_not_called()

    def test_changed_file_is_reanalyzed_and_unchanged_one_is_reused(self) -> None:
        """바뀐 파일만 다시 분석하고 그대로인 파일은 캐시를 쓰는지 검증"""
        kept = _write_clip(self.work, "kept.mp4", b"k" * 64)
        self._seed_store([kept])
        self._mark_analyzed()
        _write_clip(self.work, "new.mp4", b"n" * 64)
        with patch(
            "src.service.analysis_probe.probe_files",
            return_value=[make_video(filename="new.mp4")],
        ) as probe:
            mw = self._open_window()
            restored = _wait_until(
                lambda: mw.current_report is not None
                and len(mw.current_report.evaluations) == 2,
                _RESTORE_WAIT_SEC,
            )
        self.assertTrue(restored)
        probe.assert_called_once()
        self.assertEqual(probe.call_args[0][1], ["new.mp4"])

    def test_folder_without_previous_results_stays_empty(self) -> None:
        """이전 분석 결과가 없는 폴더는 시작 시 분석을 돌리지 않고 빈 표로 두는지 검증"""
        _write_clip(self.work, "fresh.mp4", b"f" * 64)
        with patch("src.service.analysis_probe.probe_files") as probe:
            mw = self._open_window()
            _wait_until(lambda: False, _QUIET_WAIT_SEC)
        self.assertIsNone(mw.current_report)
        probe.assert_not_called()

    def test_unanalyzed_folder_is_never_analyzed_even_with_matching_rows(self) -> None:
        """한 번도 분석되지 않은 폴더는 일부 파일이 기존 행과 맞아도 시작 시 분석하지 않음을 검증"""
        kept = _write_clip(self.work, "kept.mp4", b"k" * 64)
        self._seed_store([kept])
        _write_clip(self.work, "new.mp4", b"n" * 64)
        with patch("src.service.analysis_probe.probe_files") as probe:
            mw = self._open_window()
            _wait_until(lambda: False, _QUIET_WAIT_SEC)
        self.assertIsNone(mw.current_report)
        probe.assert_not_called()

    def test_switching_back_to_analyzed_folder_restores_table(self) -> None:
        """분석된 폴더로 다시 전환하면 표가 바로 복원되고, 분석되지 않은 폴더로 전환해도 분석하지 않음을 검증"""
        clip = _write_clip(self.work, "clip.mp4", b"c" * 64)
        self._seed_store([clip])
        self._mark_analyzed()
        other = os.path.join(self._tmp.name, "other")
        os.makedirs(other)
        _write_clip(other, "other.mp4", b"o" * 64)
        with patch("src.service.analysis_probe.probe_files") as probe:
            mw = self._open_window(root=other)
            _wait_until(lambda: False, _QUIET_WAIT_SEC)
            self.assertIsNone(mw.current_report)

            mw.set_working_directory(self.work)
            restored = _wait_until(lambda: mw.current_report is not None, _RESTORE_WAIT_SEC)
            self.assertTrue(restored)
            assert mw.current_report is not None
            self.assertEqual(
                [ev.video.filename for ev in mw.current_report.evaluations], ["clip.mp4"]
            )

            mw.set_working_directory(other)
            _wait_until(lambda: False, _QUIET_WAIT_SEC)
        self.assertIsNone(mw.current_report)
        probe.assert_not_called()

    def test_completed_analysis_marks_folder_as_analyzed(self) -> None:
        """분석이 결과를 내고 끝나면 해당 폴더가 분석된 폴더로 기록됨을 검증"""
        _write_clip(self.work, "clip.mp4", b"c" * 64)
        self.assertFalse(is_analyzed_dir(self.work))
        with patch(
            "src.service.analysis_probe.probe_files",
            return_value=[make_video(filename="clip.mp4")],
        ):
            mw = self._open_window()
            mw.start_analyze(include_keyframe=False)
            done = _wait_until(lambda: is_analyzed_dir(self.work), _RESTORE_WAIT_SEC)
        self.assertTrue(done)
        self.assertIsNotNone(mw.current_report)


if __name__ == "__main__":
    unittest.main(verbosity=2)
