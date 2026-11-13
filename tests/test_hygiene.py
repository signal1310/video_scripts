import unittest

from scripts.verify_hygiene import is_forbidden_path, scan_text


class TestVerifyHygiene(unittest.TestCase):
    """저장소 위생 및 로컬 환경 유출 방지 스크립트 단위 테스트."""

    def test_is_forbidden_path_blocks_local_artifacts(self) -> None:
        self.assertIsNotNone(is_forbidden_path(".env"))
        self.assertIsNotNone(is_forbidden_path(".rootdir"))
        self.assertIsNotNone(is_forbidden_path("classify.ipynb"))
        self.assertIsNotNone(is_forbidden_path(".venv/Scripts/python.exe"))
        self.assertIsNotNone(is_forbidden_path(".ruff_cache/0.17.5/cache"))
        self.assertIsNotNone(is_forbidden_path("src/core/__pycache__/config.cpython-310.pyc"))
        self.assertIsNotNone(is_forbidden_path(".video_cache_v2.sqlite3"))

        self.assertIsNone(is_forbidden_path(".env_example"))
        self.assertIsNone(is_forbidden_path(".rootdir_example"))
        self.assertIsNone(is_forbidden_path("classify_example.ipynb"))
        self.assertIsNone(is_forbidden_path("src/core/config.py"))
        self.assertIsNone(is_forbidden_path("AGENTS.md"))

    def test_scan_text_detects_local_leaks_and_ignores_portable_configs(self) -> None:
        fake_win_path = "C:\\" + "Users\\alice\\Videos"
        fake_unix_path = "/" + "home/alice/videos"
        fake_ip = "192." + "168.0.10"
        fake_pool = "Process" + "PoolExecutor(max_workers=8)"

        self.assertEqual(len(scan_text("src/sample.py", f'ROOT = "{fake_win_path}"')), 1)
        self.assertEqual(len(scan_text("src/sample.py", f'ROOT = "{fake_unix_path}"')), 1)
        self.assertEqual(len(scan_text("src/sample.py", f'HOST = "{fake_ip}"')), 1)
        self.assertEqual(len(scan_text("src/sample.py", fake_pool)), 1)

        portable_settings = '{"python.defaultInterpreterPath": "${workspaceFolder}/.venv/Scripts/python.exe"}'
        self.assertEqual(scan_text(".vscode/settings.json", portable_settings), [])

        absolute_settings = '{"python.defaultInterpreterPath": "D:/tools/python.exe"}'
        self.assertEqual(len(scan_text(".vscode/settings.json", absolute_settings)), 1)


if __name__ == "__main__":
    unittest.main()
