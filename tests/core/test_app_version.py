"""프로그램 버전 상수가 pyproject.toml의 [project] version과 같은지 검증하는 단위 테스트"""

import re
import unittest

from src.core.app_version import APP_VERSION
from tests._repo_root import REPO_ROOT

_PROJECT_SECTION_HEADER: str = "[project]"
_NEXT_SECTION_PREFIX: str = "\n["
_VERSION_PATTERN: re.Pattern[str] = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


class TestAppVersion(unittest.TestCase):
    """APP_VERSION와 pyproject.toml 버전 일치 검사"""

    def test_app_version_matches_pyproject_project_version(self) -> None:
        """[project] 섹션의 version이 APP_VERSION과 같음을 검증(다른 섹션의 version 키는 무시)"""
        text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        project_body = text.split(_PROJECT_SECTION_HEADER, 1)[1].split(_NEXT_SECTION_PREFIX, 1)[0]
        match = _VERSION_PATTERN.search(project_body)
        self.assertIsNotNone(match, "pyproject.toml [project] 섹션에 version이 없음")
        assert match is not None
        self.assertEqual(APP_VERSION, match.group(1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
