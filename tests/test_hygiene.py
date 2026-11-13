import io
import pathlib
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest.mock import patch

import scripts.verify_hygiene as verify_hygiene
from scripts.verify_hygiene import (
    IGNORE_MARKER,
    HygieneViolation,
    is_forbidden_path,
    is_scannable_path,
    main,
    run_hygiene_check,
    scan_text,
)


class TestVerifyHygiene(unittest.TestCase):
    """저장소 위생 및 로컬 환경 유출 방지 스크립트 단위 테스트"""

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

    def test_ignore_marker_and_violation_format(self) -> None:
        """hygiene ignore 마커가 해당 줄의 검사를 면제하고 HygieneViolation.format()이 줄 번호 유무에 맞게 포맷하는지 검증"""
        leak_expr = "C:" + "\\Us" + "ers\\alice\\vid.mp4"
        text = f'A = "{leak_expr}"  # {IGNORE_MARKER}\nB = "{leak_expr}"\n'
        violations = scan_text("src/sample.py", text)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0].line, 2)

        with_line = HygieneViolation(path="src/sample.py", line=2, reason="local path", snippet="B = ...")
        without_line = HygieneViolation(path=".env", line=None, reason="forbidden file")
        self.assertEqual(
            with_line.format(),
            "[HYGIENE FAIL] src/sample.py:2 (local path) -> B = ...",
        )
        self.assertEqual(
            without_line.format(),
            "[HYGIENE FAIL] .env (forbidden file)",
        )

    def test_run_hygiene_check_and_main_exit_codes(self) -> None:
        """run_hygiene_check 및 main CLI 진입점이 정상/금지 경로/로컬 정보 위반에 따라 0/1 종료 코드를 반환하는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo_root = pathlib.Path(tmpdir)
            (repo_root / "src").mkdir(parents=True, exist_ok=True)
            (repo_root / "scripts").mkdir(parents=True, exist_ok=True)
            clean_file = repo_root / "src" / "clean.py"
            clean_file.write_text("VALUE = 42\n", encoding="utf-8")

            fake_script_path = str(repo_root / "scripts" / "verify_hygiene.py")
            with (
                patch.object(verify_hygiene, "__file__", fake_script_path),
                patch("scripts.verify_hygiene.collect_target_paths", return_value=["src/clean.py"]),
            ):
                violations, count = run_hygiene_check(repo_root, staged_only=True)
                self.assertEqual(violations, [])
                self.assertEqual(count, 1)

                out_buf = io.StringIO()
                err_buf = io.StringIO()
                with redirect_stdout(out_buf), redirect_stderr(err_buf):
                    code_ok = main(["--staged"])
                self.assertEqual(code_ok, 0)
                self.assertIn("[hygiene] OK (staged", out_buf.getvalue())

            leak_file = repo_root / "src" / "bad.py"
            leak_expr = "C:" + "\\Us" + "ers\\bob\\media.mp4"
            leak_file.write_text(f'ROOT = "{leak_expr}"\n', encoding="utf-8")

            with (
                patch.object(verify_hygiene, "__file__", fake_script_path),
                patch(
                    "scripts.verify_hygiene.collect_target_paths",
                    return_value=[".env", "src/bad.py"],
                ),
            ):
                violations_bad, count_bad = run_hygiene_check(repo_root, staged_only=True)
                self.assertEqual(count_bad, 2)
                self.assertEqual(len(violations_bad), 2)

                out_buf2 = io.StringIO()
                err_buf2 = io.StringIO()
                with redirect_stdout(out_buf2), redirect_stderr(err_buf2):
                    code_fail = main(["--staged"])
                self.assertEqual(code_fail, 1)
                self.assertIn("[HYGIENE FAIL] .env", err_buf2.getvalue())
                self.assertIn("[HYGIENE FAIL] src/bad.py:1", err_buf2.getvalue())


if __name__ == "__main__":
    unittest.main()
