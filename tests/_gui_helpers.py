"""GUI 테스트 공용 격리, 창 검증 헬퍼"""

import os
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QCoreApplication, QEvent, Qt
from PyQt6.QtWidgets import QApplication, QWidget


def snapshot_top_level_widgets() -> set[QWidget]:
    """현재 최상위 위젯 객체 집합을 반환. 객체 자체를 담아 GC 뒤 id 재사용을 피함"""
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return set()
    return set(app.topLevelWidgets())


def dispose_new_top_level_widgets(before: set[QWidget]) -> None:
    """기록 이후 새로 생긴 부모 없는 위젯만 지연 삭제하고 즉시 처리

    - 기록 이전 위젯(setUpClass에서 만든 공유 창 등)은 같은 클래스의 다른 테스트가 쓰므로 남김
    - 부모가 있는 창(메뉴, 팝업)은 부모 삭제와 함께 정리되므로 따로 지우지 않음
    """
    app = QApplication.instance()
    if not isinstance(app, QApplication):
        return
    for widget in app.topLevelWidgets():
        if widget not in before and widget.parentWidget() is None:
            widget.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


class GuiTestCase(unittest.TestCase):
    """테스트 메서드가 만든 최상위 위젯을 테스트 뒤 정리하는 GUI 테스트 기반 클래스

    - setUpClass 이전 위젯은 run() 기록에 포함되어 보존됨
    """

    def run(self, result: unittest.TestResult | None = None) -> unittest.TestResult | None:
        before = snapshot_top_level_widgets()
        try:
            return super().run(result)
        finally:
            dispose_new_top_level_widgets(before)


def isolate_app_state(case: unittest.TestCase, root: str) -> None:
    """설정 JSON, 작업 폴더 기록, 분석 캐시 DB 경로를 지정 폴더 아래로 돌리고 테스트 종료 시 복원"""
    targets = {
        "src.core.app_state.get_state_path": os.path.join(root, "data", "config.json"),
        "src.core.paths.get_rootdir_path": os.path.join(root, ".rootdir"),
        "src.service.analysis_store.get_data_dir": os.path.join(root, "data"),
    }
    for target, path in targets.items():
        patcher = patch(target, return_value=path)
        patcher.start()
        case.addCleanup(patcher.stop)


def assert_independent_window(case: unittest.TestCase, window: QWidget) -> None:
    """부모 없는 비모달 독립 창으로 열려 있는지 검증"""
    case.assertTrue(window.isVisible())
    case.assertIsNone(window.parent())
    case.assertEqual(window.windowModality(), Qt.WindowModality.NonModal)
    case.assertEqual(window.windowFlags() & Qt.WindowType.WindowType_Mask, Qt.WindowType.Window)
