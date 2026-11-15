import pathlib
import tempfile
import unittest

from scripts.sync_skills import MIRROR_REL_DIR, SOURCE_REL_DIR, find_differences, write_mirror

_SKILL_REL = "demo/SKILL.md"


def _write(root: pathlib.Path, rel_dir: str, rel: str, data: bytes) -> pathlib.Path:
    path = root / rel_dir / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class TestSyncSkills(unittest.TestCase):
    """스킬 원본, 사본 동기화 검사와 재생성 동작을 검증"""

    def test_identical_trees_have_no_differences(self) -> None:
        """줄바꿈 형식만 다른 사본은 일치로 판정"""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write(root, SOURCE_REL_DIR, _SKILL_REL, b"a\nb\n")
            _write(root, MIRROR_REL_DIR, _SKILL_REL, b"a\r\nb\r\n")
            self.assertEqual(find_differences(root), [])

    def test_missing_changed_and_orphan_files_are_reported(self) -> None:
        """누락, 내용 변경, 원본 없는 잔존 파일을 각각 보고"""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write(root, SOURCE_REL_DIR, _SKILL_REL, b"new\n")
            _write(root, SOURCE_REL_DIR, "other/SKILL.md", b"x\n")
            _write(root, MIRROR_REL_DIR, _SKILL_REL, b"old\n")
            _write(root, MIRROR_REL_DIR, "stale/SKILL.md", b"y\n")
            problems = find_differences(root)
            self.assertEqual(len(problems), 3)
            self.assertTrue(any("내용 불일치" in p and _SKILL_REL in p for p in problems))
            self.assertTrue(any("사본 누락" in p and "other/SKILL.md" in p for p in problems))
            self.assertTrue(any("잔존" in p and "stale/SKILL.md" in p for p in problems))

    def test_write_mirror_resolves_all_differences(self) -> None:
        """재생성 후 불일치가 없고 잔존 파일이 제거됨"""
        with tempfile.TemporaryDirectory() as tmp:
            root = pathlib.Path(tmp)
            _write(root, SOURCE_REL_DIR, _SKILL_REL, b"new\n")
            _write(root, MIRROR_REL_DIR, "stale/SKILL.md", b"y\n")
            write_mirror(root)
            self.assertEqual(find_differences(root), [])
            self.assertFalse((root / MIRROR_REL_DIR / "stale").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
