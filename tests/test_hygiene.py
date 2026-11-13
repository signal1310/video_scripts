import pathlib
import unittest

from scripts.verify_hygiene import is_forbidden_path, is_scannable_path, scan_text


class TestVerifyHygiene(unittest.TestCase):
    """저장소 위생 및 로컬 환경 유출 방지 스크립트 단위 테스트."""

    def test_is_forbidden_path_blocks_local_artifacts(self) -> None:
        self.assertIsNotNone(is_forbidden_path(".env"))
        self.assertIsNotNone(is_forbidden_path(".env.local"))
        self.assertIsNotNone(is_forbidden_path(".rootdir"))
        self.assertIsNotNone(is_forbidden_path(".rootdir.bak"))
        self.assertIsNotNone(is_forbidden_path("classify.ipynb"))
        self.assertIsNotNone(is_forbidden_path(".ipynb_checkpoints/classify-checkpoint.ipynb"))
        self.assertIsNotNone(is_forbidden_path(".venv/Scripts/python.exe"))
        self.assertIsNotNone(is_forbidden_path(".ruff_cache/0.17.5/cache"))
        self.assertIsNotNone(is_forbidden_path("src/core/__pycache__/config.cpython-310.pyc"))
        self.assertIsNotNone(is_forbidden_path(".video_cache_v2.sqlite3"))
        self.assertIsNotNone(is_forbidden_path("video_analysis_report.csv"))

        self.assertIsNone(is_forbidden_path(".env_example"))
        self.assertIsNone(is_forbidden_path(".rootdir_example"))
        self.assertIsNone(is_forbidden_path("classify_example.ipynb"))
        self.assertIsNone(is_forbidden_path("src/core/config.py"))
        self.assertIsNone(is_forbidden_path("AGENTS.md"))

    def test_is_scannable_path_covers_notebooks_presets_and_examples(self) -> None:
        self.assertTrue(is_scannable_path(pathlib.Path("classify_example.ipynb")))
        self.assertTrue(is_scannable_path(pathlib.Path("shana_presets/preset.xml")))
        self.assertTrue(is_scannable_path(pathlib.Path(".rootdir_example")))
        self.assertTrue(is_scannable_path(pathlib.Path(".env_example")))
        self.assertTrue(is_scannable_path(pathlib.Path(".gitignore")))
        self.assertFalse(is_scannable_path(pathlib.Path("assets/preview.png")))

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

    def test_scan_text_detects_external_drive_and_unc_paths(self) -> None:
        ext_drive_bs = "F:" + "\\Videos\\clip.mp4"
        ext_drive_slash = "F:" + "/Archive/clip.mp4"
        ext_drive_json = r'"text": ["F:' + r'\\Archive\\new_clip.mp4\n"]'
        unc_raw = "\\\\" + "nas\\videos\\clip.mp4"
        unc_json = r'"' + "\\\\\\\\" + r'nas\\videos\\clip.mp4"'

        self.assertEqual(len(scan_text("src/sample.py", f'ROOT = "{ext_drive_bs}"')), 1)
        self.assertEqual(len(scan_text("src/sample.py", f'ROOT = "{ext_drive_slash}"')), 1)
        self.assertEqual(len(scan_text("classify_example.ipynb", ext_drive_json)), 1)
        self.assertEqual(len(scan_text("shana_presets/preset.xml", f"<Out>{ext_drive_bs}</Out>")), 1)
        self.assertEqual(len(scan_text(".rootdir_example", ext_drive_bs)), 1)
        self.assertEqual(len(scan_text("src/sample.py", f'ROOT = "{unc_raw}"')), 1)
        self.assertEqual(len(scan_text("classify_example.ipynb", unc_json)), 1)

        # URL 스킴 및 JSON 줄바꿈 이스케이프는 오탐하지 않음
        self.assertEqual(scan_text("src/sample.py", 'URL = "https://example.com/videos"'), [])
        self.assertEqual(scan_text("classify_example.ipynb", r'"source": ["Option A:\n"]'), [])

        # tests/ 내 가상 드라이브 픽스처는 허용하되, 개인 홈·UNC 경로는 차단
        fake_win_home = "C:\\" + "Users\\alice\\Videos"
        self.assertEqual(scan_text("tests/test_sample.py", 'ROOT = "C:/dummy"'), [])
        self.assertEqual(len(scan_text("tests/test_sample.py", f'ROOT = "{fake_win_home}"')), 1)
        self.assertEqual(len(scan_text("tests/test_sample.py", f'ROOT = "{unc_raw}"')), 1)


if __name__ == "__main__":
    unittest.main()
