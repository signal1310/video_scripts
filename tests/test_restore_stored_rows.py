"""이전 형식 행의 표시와 재인코딩 제외, 저장 결과만 읽는 판정(DB 쓰기 없음), 취소, 안내 문구를 검증"""

import contextlib
import os
import sqlite3
import tempfile
import time
import unittest
from collections.abc import Callable
from typing import Any
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.core.evaluation.classification_policy import make_legacy_format_policy
from src.core.messages import format_reanalysis_breakdown, format_reanalysis_notice, format_restore_progress
from src.core.probe_parser import compress_probe_response
from src.core.smart_action import SmartAction
from src.gui.main_window import MainWindow
from src.service import analysis_probe as probe_mod
from src.service.analysis_store import AnalysisStore, StoredEntry, compute_fingerprint
from src.service.cache import DEFAULT_CACHE_REGISTRY
from tests._factories import make_video
from tests._gui_helpers import GuiTestCase, isolate_app_state

_APP = QApplication.instance() or QApplication([])


def _probe(duration: str = "10.0") -> dict[str, Any]:
    fmt: dict[str, Any] = {"duration": duration, "size": "64", "bit_rate": "800000"}
    stream: dict[str, Any] = {
        "index": 0,
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "avg_frame_rate": "30/1",
        "r_frame_rate": "30/1",
        "bit_rate": "700000",
        "duration": duration,
    }
    return {"streams": [stream], "format": fmt}


class _Fixture(GuiTestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "work")
        os.makedirs(self.root)
        self.db_path = os.path.join(self._tmp.name, "cache.sqlite3")
        self.store = AnalysisStore(self.db_path)
        self.addCleanup(self.store.close)

    def _row(self, name: str, *, version: int, raw: bool = True, keyframe: float | None = 2.0) -> None:
        path = os.path.join(self.root, name)
        with open(path, "wb") as f:
            f.write(name.encode("utf-8").ljust(64, b"."))
        stat = os.stat(path)
        entry = StoredEntry(
            name,
            stat.st_size,
            stat.st_mtime_ns,
            compute_fingerprint(path, stat.st_size),
            make_video(filename=name, keyframe_interval=keyframe),
            probe_raw=compress_probe_response(_probe()) if raw else None,
        )
        self.store.apply((), [entry])
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE video_props SET probe_version = ? WHERE filename = ?", (version, name))
            conn.commit()

    def _versions(self) -> dict[str, int]:
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            return {n: int(v) for n, v in conn.execute("SELECT filename, probe_version FROM video_props")}


class LegacyPolicyTests(unittest.TestCase):
    def test_legacy_format_is_not_an_encode_target(self) -> None:
        """이전 형식 판정은 재인코딩 대상이 아니고 표시 이름과 안내 사유를 가지는지 검증"""
        self.assertFalse(SmartAction.LEGACY_FORMAT.is_encode)
        self.assertEqual(SmartAction.LEGACY_FORMAT.action_template, "재분석 필요")
        self.assertEqual(SmartAction.LEGACY_FORMAT.reason_label, "분석 형식이 바뀌어 다시 분석해야 합니다")

    def test_policy_marks_only_the_named_files_first(self) -> None:
        """정책 체인의 맨 앞에서 이름이 있는 파일만 이전 형식으로 판정하는지 검증"""
        from src.core.evaluation.classification_policy import DEFAULT_CLASSIFICATION_POLICIES

        policy = make_legacy_format_policy(frozenset({"old.mp4"}))
        self.assertIsNotNone(DEFAULT_CLASSIFICATION_POLICIES)
        ctx_old = type("Ctx", (), {"video": make_video(filename="old.mp4")})()
        ctx_new = type("Ctx", (), {"video": make_video(filename="new.mp4")})()
        decision = policy(ctx_old)  # type: ignore[arg-type]
        assert decision is not None
        self.assertIs(decision.smart_action, SmartAction.LEGACY_FORMAT)
        self.assertIsNone(policy(ctx_new))  # type: ignore[arg-type]


class StoredRowsTests(_Fixture):
    def test_rows_are_read_without_probing_and_without_writing(self) -> None:
        """현재, 번호만 올림, 저장 응답으로 다시 계산, 이전 형식 행을 구분하고 DB는 바꾸지 않는지 검증"""
        self._row("current.mp4", version=5)
        self._row("upgraded.mp4", version=4)
        self._row("reread.mkv", version=4, raw=True)
        self._row("legacy.mp4", version=2)
        before = self._versions()
        with patch.object(probe_mod, "probe_files", side_effect=AssertionError("ffprobe 호출 없어야 함")):
            stored = probe_mod.load_stored_rows(self.store, self.root)
        assert stored is not None
        self.assertEqual(sorted(p.filename for p in stored.rows), ["current.mp4", "legacy.mp4", "reread.mkv", "upgraded.mp4"])
        self.assertEqual(stored.legacy_names, frozenset({"legacy.mp4"}))
        self.assertTrue(stored.include_keyframe)
        self.assertEqual(self._versions(), before)

    def test_cancel_returns_none_and_progress_counts_checked_files(self) -> None:
        """취소되면 결과 없음, 진행 콜백은 확인한 파일 수를 알리는지 검증"""
        self._row("a.mp4", version=5)
        counts: list[int] = []
        self.assertIsNone(probe_mod.load_stored_rows(self.store, self.root, is_cancelled=lambda: True))
        stored = probe_mod.load_stored_rows(self.store, self.root, on_progress=counts.append)
        assert stored is not None
        self.assertEqual(counts, [1])

    def test_missing_keyframe_marks_folder_as_not_keyframe_mode(self) -> None:
        """키프레임 값이 없는 행이 있으면 복원 분석을 키프레임 제외 방식으로 고르는지 검증"""
        self._row("a.mp4", version=5, keyframe=None)
        stored = probe_mod.load_stored_rows(self.store, self.root)
        assert stored is not None
        self.assertFalse(stored.include_keyframe)


class NoticeTextTests(unittest.TestCase):
    def test_notice_breakdown_and_progress_wording(self) -> None:
        """승인된 안내 문구 형식과 시간 없는 변형, 사유별 툴팁, 판정 진행 문구를 검증"""
        self.assertEqual(
            format_reanalysis_notice(3, 12),
            "이 폴더의 영상 중 3개를 다시 분석해야 합니다. (예상 약 12분)",
        )
        self.assertEqual(
            format_reanalysis_notice(3, None),
            "이 폴더의 영상 중 3개를 다시 분석해야 합니다.",
        )
        breakdown = format_reanalysis_breakdown(1, 2)
        self.assertTrue(breakdown.startswith("왜 다시 분석하나요?\n분석 형식이 바뀐 영상 1개:"))
        self.assertIn("\n키프레임 간격이 없는 영상 2개:", breakdown)
        zero_gap = format_reanalysis_breakdown(4, 0)
        self.assertNotIn("키프레임 간격이 없는 영상", zero_gap)
        self.assertIn("분석 형식이 바뀐 영상 4개:", zero_gap)
        self.assertEqual(format_restore_progress(40), "파일 목록 확인 중(40개)")


class RestoreDisplayWindowTests(_Fixture):
    def setUp(self) -> None:
        super().setUp()
        isolate_app_state(self, self._tmp.name)

    def test_legacy_rows_are_shown_excluded_from_encoding_and_noticed(self) -> None:
        """이전 형식이 있는 폴더를 복원하면 저장 결과로 표를 채우고 재인코딩 대상에서 제외하며 안내를 띄우는지 검증"""
        self._row("current.mp4", version=5)
        self._row("legacy.mp4", version=2)
        with patch.object(probe_mod, "probe_files", side_effect=AssertionError("ffprobe 호출 없어야 함")):
            stored = probe_mod.load_stored_rows(self.store, self.root)
            mw = MainWindow(initial_dir=self.root)
            self.addCleanup(mw.close)
            mw.set_working_directory(self.root)
            mw._restore_flow.apply_resolved(stored)
            _wait_until(lambda: mw.current_report is not None)
        report = mw.current_report
        assert report is not None
        by_name = {ev.video.filename: ev for ev in report.evaluations}
        self.assertEqual(by_name["legacy.mp4"].smart_action, SmartAction.LEGACY_FORMAT)
        self.assertFalse(by_name["legacy.mp4"].is_encode_target)
        self.assertIn("1개를 다시 분석해야 합니다", mw.footer_bar.lbl_status.text())
        DEFAULT_CACHE_REGISTRY.invalidate(self.root)


class RestoreNoticeCountTests(_Fixture):
    """이전 형식과 키프레임 간격 없는 행을 함께 복원할 때 안내 개수가 정밀 분석 대상 수와 같고, 사유별 개수가 툴팁에 나오는지 검증"""

    def setUp(self) -> None:
        super().setUp()
        isolate_app_state(self, self._tmp.name)
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate, self.root)

    def test_notice_count_matches_full_analysis_and_tooltip_shows_reasons(self) -> None:
        self._row("legacy.mp4", version=2, keyframe=2.0)
        self._row("gap_a.mp4", version=5, keyframe=None)
        self._row("gap_b.mp4", version=5, keyframe=None)
        self._row("current.mp4", version=5, keyframe=2.0)
        with patch.object(probe_mod, "probe_files", side_effect=AssertionError("ffprobe 호출 없어야 함")):
            stored = probe_mod.load_stored_rows(self.store, self.root)
            mw = MainWindow(initial_dir=self.root)
            self.addCleanup(mw.close)
            mw.set_working_directory(self.root)
            mw._restore_flow.apply_resolved(stored)
            _wait_until(lambda: mw.current_report is not None)
        assert stored is not None
        self.assertEqual(stored.format_changed_count, 1)
        self.assertEqual(stored.keyframe_missing_count, 2)
        self.assertIn("이 폴더의 영상 중 3개를 다시 분석해야 합니다", mw.footer_bar.lbl_status.text())
        self.assertEqual(mw.footer_bar.lbl_status.toolTip(), format_reanalysis_breakdown(1, 2))
        clf = mw._session.classifier
        assert clf is not None
        self.assertEqual(clf.service.pending_count(include_keyframe=True), 3)


def _wait_until(predicate: Callable[[], bool], timeout_sec: float = 10.0) -> bool:
    end = time.monotonic() + timeout_sec
    while not predicate():
        if time.monotonic() > end:
            return False
        _APP.processEvents()
        time.sleep(0.01)
    return True


if __name__ == "__main__":
    unittest.main()
