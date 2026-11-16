"""GUI 테스트 공용 격리, 창 검증 헬퍼"""

import os
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget


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
