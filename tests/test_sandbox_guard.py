"""테스트 격리 샌드박스 가드가 실제 환경 변조를 차단하는지 검증"""

import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import unittest

from PyQt6.QtCore import QSettings

from src.core.app_state import CONFIG_FILENAME, DATA_DIR_NAME, get_data_dir, get_state_path
from src.core.config import get_app_base_dir, get_rootdir_path
from tests._sandbox import get_sandbox_dir

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


class TestSandboxGuard(unittest.TestCase):
    """테스트 프로세스 전역 격리 가드의 리다이렉션 및 외부 쓰기 차단을 검증"""

    def test_config_and_qsettings_redirect_into_sandbox(self) -> None:
        sandbox_root = os.path.normcase(os.path.realpath(get_sandbox_dir()))
        base_dir = os.path.normcase(os.path.realpath(get_app_base_dir()))
        rootdir_path = os.path.normcase(os.path.realpath(get_rootdir_path()))
        data_dir = os.path.normcase(os.path.realpath(get_data_dir()))
        state_path = os.path.normcase(os.path.realpath(get_state_path()))

        self.assertEqual(base_dir, sandbox_root)
        self.assertTrue(rootdir_path.startswith(sandbox_root))
        self.assertTrue(data_dir.startswith(sandbox_root))
        self.assertTrue(state_path.startswith(sandbox_root))
        self.assertNotEqual(
            rootdir_path, os.path.normcase(os.path.realpath(str(_REPO_ROOT / ".rootdir")))
        )
        self.assertNotEqual(
            state_path,
            os.path.normcase(
                os.path.realpath(str(_REPO_ROOT / DATA_DIR_NAME / CONFIG_FILENAME))
            ),
        )

    def test_blocks_direct_registry_and_external_filesystem_writes(self) -> None:
        with self.assertRaises(PermissionError):
            QSettings("VideoClassifier", "Studio")

        outside_file = _REPO_ROOT / ".sandbox_guard_probe.tmp"
        outside_dir = _REPO_ROOT / ".sandbox_guard_probe_dir"
        outside_db = _REPO_ROOT / ".sandbox_guard_probe.sqlite3"

        with self.assertRaises(PermissionError), open(outside_file, "w", encoding="utf-8"):
            pass

        with self.assertRaises(PermissionError):
            outside_file.write_text("blocked", encoding="utf-8")

        with self.assertRaises(PermissionError):
            outside_file.mkdir()

        with self.assertRaises(PermissionError):
            os.makedirs(outside_dir)

        with self.assertRaises(PermissionError):
            sqlite3.connect(str(outside_db))

        with tempfile.TemporaryDirectory() as tmpdir:
            inside_src = os.path.join(tmpdir, "ok.txt")
            inside_dst = os.path.join(tmpdir, "moved.txt")
            with open(inside_src, "w", encoding="utf-8") as f:
                f.write("ok")
            with self.assertRaises(PermissionError):
                shutil.move(inside_src, str(outside_file))
            shutil.move(inside_src, inside_dst)
            self.assertTrue(os.path.isfile(inside_dst))

        if sys.platform == "win32":
            import winreg

            with self.assertRaises(PermissionError):
                winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\__Blocked__")


if __name__ == "__main__":
    unittest.main(verbosity=2)
