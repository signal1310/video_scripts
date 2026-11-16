"""출력 해상도 단축키 즉시 실행, 메뉴 액션의 단일 바인딩, 다단계 되돌리기와 다시 실행의 메인 창 연동을 검증하는 GUI 테스트"""

import os
import tempfile
import unittest
from collections import Counter

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QAction, QKeyEvent
from PyQt6.QtWidgets import QApplication

from src.classifier import VideoClassifier
from src.core.config import SavingsConfig, ThresholdsConfig
from src.core.shortcuts import REDO_SECONDARY_SHORTCUT
from src.gui.main_window import MainWindow
from src.gui.session_controller import update_filter_exceptions
from src.service.analysis_store import load_manual_target_store
from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache
from src.service.session import normalize_root_key
from tests._factories import make_video
from tests._gui_helpers import isolate_app_state
from tests._sandbox import reset_sandbox_files

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])


def _shortcut_strings(action: QAction) -> list[str]:
    """액션에 바인딩된 모든 단축키 문자열 목록"""
    return [seq.toString() for seq in action.shortcuts()]


def _action_for_key(window: MainWindow, key: str) -> QAction | None:
    """창에 등록된 액션 중 해당 단축키를 가진 첫 액션 반환"""
    for action in window.actions():
        if key in _shortcut_strings(action):
            return action
    return None


class TestResolutionShortcutsAndHistory(unittest.TestCase):
    """출력 해상도 단축키 실행과 앱 히스토리 되돌리기, 다시 실행의 메인 창 동작을 검증"""

    def setUp(self) -> None:
        """샌드박스 파일을 테스트 전후로 초기화해 실제 사용자 데이터 접근을 차단"""
        reset_sandbox_files()
        self.addCleanup(reset_sandbox_files)

    def _build_window(self) -> tuple[MainWindow, str]:
        """분석 캐시가 준비된 메인 창과 격리된 작업 폴더 경로 생성"""
        work = tempfile.TemporaryDirectory()
        self.addCleanup(work.cleanup)
        isolate_app_state(self, work.name)
        clf = VideoClassifier(root_dir=work.name)
        videos = [make_video(filename="big.mp4", vid_kbps=9000, vid_size_MB=500.0, duration=120.0)]
        DEFAULT_CACHE_REGISTRY.put(work.name, TableCache(work.name, videos, include_keyframe=True))
        mw = MainWindow(classifier=clf)
        self.addCleanup(mw.close)
        mw.apply_report(clf.get_summary_report(ensure_keyframe=False))
        return mw, work.name

    def test_shift_digit_press_triggers_its_action_through_symbol_key(self) -> None:
        """US 자판에서 Ctrl+Shift+0을 누르면 기호 키 이벤트로 도착해도 출력 해상도 직접 지정 모두 해제가 한 번 실행됨을 검증"""
        mw, _ = self._build_window()
        mw.show()
        self.addCleanup(mw.close)
        mw.activateWindow()
        QApplication.processEvents()
        action = _action_for_key(mw, "Ctrl+Shift+0")
        self.assertIsNotNone(action)
        assert action is not None
        hits: list[int] = []
        action.triggered.connect(lambda _checked=False: hits.append(1))
        modifiers = Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
        QApplication.sendEvent(mw, QKeyEvent(QEvent.Type.KeyPress, int(Qt.Key.Key_ParenRight), modifiers))
        QApplication.processEvents()
        self.assertEqual(len(hits), 1)

    def test_each_command_shortcut_is_bound_to_exactly_one_action(self) -> None:
        """기본 단축키가 액션마다 한 번씩만 바인딩되어 Qt의 모호한 단축키 충돌이 없음을 검증"""
        mw, _ = self._build_window()
        counts = Counter(
            key for action in mw.actions() for key in _shortcut_strings(action)
        )
        self.assertEqual(
            {key: count for key, count in counts.items() if count > 1},
            {},
        )
        resolution_keys = [f"Ctrl+{index}" for index in range(1, 9)]
        for key in (*resolution_keys, "Ctrl+0", "Ctrl+9", "Ctrl+Shift+0"):
            with self.subTest(key=key):
                self.assertIsNotNone(_action_for_key(mw, key))
        redo_action = _action_for_key(mw, "Ctrl+Y")
        self.assertIsNotNone(redo_action)
        assert redo_action is not None
        self.assertIn(REDO_SECONDARY_SHORTCUT, _shortcut_strings(redo_action))

    def test_resolution_shortcut_applies_to_selected_row_immediately(self) -> None:
        """Ctrl+4 단축키가 메뉴를 열지 않고 선택 행에 720p 직접 지정을 즉시 적용하고 저장함을 검증"""
        mw, work_dir = self._build_window()
        mw.table_view.selectAll()
        action = _action_for_key(mw, "Ctrl+4")
        self.assertIsNotNone(action)
        assert action is not None
        action.trigger()

        self.assertEqual(
            load_manual_target_store({}),
            {normalize_root_key(work_dir): {"big.mp4": "720p"}},
        )

    def test_disabled_resolution_shortcut_leaves_state_unchanged(self) -> None:
        """그 규격으로 의미 있게 축소되지 않는 영상에는 단축키를 눌러도 지정이 저장되지 않음을 검증"""
        mw, _ = self._build_window()
        mw.table_view.selectAll()
        before = mw.shortcuts.capture()
        action = _action_for_key(mw, "Ctrl+8")
        self.assertIsNotNone(action)
        assert action is not None
        action.trigger()

        self.assertTrue(mw.shortcuts.capture().same_state(before))

    def test_three_steps_undo_to_start_and_redo_to_end(self) -> None:
        """출력 해상도 지정, 예외 규칙 추가, 판별 기준 변경 후 세 번 되돌리고 다시 실행하면 전후 상태가 정확히 복원됨을 검증"""
        mw, _ = self._build_window()
        mw.table_view.setFocus()
        mw.table_view.selectAll()
        initial = mw.shortcuts.capture()

        mw.table_view.set_manual_target_for(None, "720p")
        update_filter_exceptions(mw.filter_bar, ["keyword"], add=True)
        mw._on_criteria_changed(ThresholdsConfig(over_bitrate_ratio=1.9), SavingsConfig())
        final = mw.shortcuts.capture()
        self.assertFalse(final.same_state(initial))
        self.assertEqual(mw.menu.act_undo.text(), "되돌리기 (판별 기준 조정)")

        for _ in range(3):
            mw.shortcuts.trigger_undo()
        self.assertTrue(mw.shortcuts.capture().same_state(initial))
        self.assertFalse(mw.menu.act_undo.isEnabled())
        self.assertEqual(mw.menu.act_redo.text(), "다시 실행 (출력 해상도 지정 (720p))")

        for _ in range(3):
            mw.shortcuts.trigger_redo()
        self.assertTrue(mw.shortcuts.capture().same_state(final))
        self.assertFalse(mw.menu.act_redo.isEnabled())

    def test_working_directory_switch_clears_history(self) -> None:
        """작업 폴더를 바꾸면 이전 폴더의 되돌리기 단계가 제거되어 되돌리기가 비활성화됨을 검증"""
        mw, _ = self._build_window()
        mw.table_view.selectAll()
        mw.table_view.set_manual_target_for(None, "720p")
        self.assertTrue(mw.menu.act_undo.isEnabled())

        other = tempfile.TemporaryDirectory()
        self.addCleanup(other.cleanup)
        mw.set_working_directory(other.name)
        self.assertFalse(mw.menu.act_undo.isEnabled())


if __name__ == "__main__":
    unittest.main(verbosity=2)
