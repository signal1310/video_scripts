import pathlib
import tempfile
import unittest

from scripts.hooks.claude_guard import REPO_ROOT, check_python_file

_TEST_FILE = REPO_ROOT / "tests" / "test_module_size.py"


class TestClaudeGuardPostEdit(unittest.TestCase):
    """post-edit 훅의 경로 정규화와 저장소 밖 파일 제외 검증"""

    def test_drive_letter_case_variants_resolve_to_same_clean_result(self) -> None:
        lower = str(_TEST_FILE).replace("C:", "c:", 1)
        upper = str(_TEST_FILE).replace("c:", "C:", 1)
        self.assertIsNone(check_python_file(lower))
        self.assertIsNone(check_python_file(upper))

    def test_file_outside_repository_is_not_checked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outside = pathlib.Path(tmp) / "outside.py"
            outside.write_text("import os\n", encoding="utf-8")
            self.assertIsNone(check_python_file(str(outside)))


if __name__ == "__main__":
    unittest.main()
