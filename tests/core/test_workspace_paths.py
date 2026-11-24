"""작업 폴더 범위 판정과 논리 파일명 소속 판정(is_within_roots, logical_owner_root) 검증"""

import os
import tempfile
import unittest

from src.core.workspace_paths import is_within_roots, logical_owner_root


class _RootCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.base = tmp.name
        self.root_a = os.path.join(self.base, "root_dir_a")
        self.root_b = os.path.join(self.base, "root_dir_b")
        self.root_a_twin = os.path.join(self.base, "root_dir_a2")
        for path in (self.root_a, self.root_b, self.root_a_twin):
            os.makedirs(path)
        os.makedirs(os.path.join(self.root_a, "sub"))


class TestIsWithinRoots(_RootCase):
    """경로가 작업 폴더 자신이거나 그 아래일 때만 범위 안으로 봄"""

    def test_same_folder_and_child_are_inside(self) -> None:
        self.assertTrue(is_within_roots(self.root_a, [self.root_a, self.root_b]))
        child = os.path.join(self.root_a, "sub", "a.mp4")
        self.assertTrue(is_within_roots(child, [self.root_a, self.root_b]))

    def test_other_folder_and_name_prefix_sibling_are_outside(self) -> None:
        self.assertFalse(is_within_roots(self.root_b, [self.root_a]))
        sibling = os.path.join(self.root_a_twin, "a.mp4")
        self.assertFalse(is_within_roots(sibling, [self.root_a]))

    def test_separator_case_and_trailing_separator_do_not_matter(self) -> None:
        child = os.path.join(self.root_a, "sub", "a.mp4")
        self.assertTrue(is_within_roots(child.replace(os.sep, "/"), [self.root_a + os.sep]))
        self.assertTrue(is_within_roots(self.root_a.upper(), [self.root_a.lower()]))

    def test_empty_roots_is_outside(self) -> None:
        self.assertFalse(is_within_roots(self.root_a, []))

    def test_empty_path_and_empty_root_are_outside(self) -> None:
        # - 빈 폴더는 현재 폴더로 해석되어 모든 상대 경로를 범위 안으로 만들 수 있으므로 비교하지 않음
        self.assertFalse(is_within_roots("", [""]))
        self.assertFalse(is_within_roots(self.root_a, [""]))
        self.assertFalse(is_within_roots("", [self.root_a]))


class TestLogicalOwnerRoot(_RootCase):
    """논리 파일명이 어느 작업 폴더 소속인지, 범위 밖이면 소속 없음"""

    def test_single_root_plain_name_belongs_to_it_when_file_exists(self) -> None:
        path = os.path.join(self.root_a, "a.mp4")
        with open(path, "wb") as handle:
            handle.write(b"x")
        self.assertEqual(logical_owner_root("a.mp4", (self.root_a,)), self.root_a)
        self.assertIsNone(logical_owner_root("absent.mp4", (self.root_a,)))

    def test_single_root_existing_subfolder_belongs_to_it(self) -> None:
        self.assertEqual(logical_owner_root("sub/a.mp4", (self.root_a,)), self.root_a)

    def test_single_root_missing_subfolder_is_unknown(self) -> None:
        self.assertIsNone(logical_owner_root("absent/a.mp4", (self.root_a,)))

    def test_multi_root_label_selects_owner(self) -> None:
        roots = (self.root_a, self.root_b)
        self.assertEqual(logical_owner_root("root_dir_a/a.mp4", roots), self.root_a)
        self.assertEqual(logical_owner_root("root_dir_b/sub/b.mp4", roots), self.root_b)

    def test_multi_root_unknown_label_and_bare_name_are_out_of_scope(self) -> None:
        roots = (self.root_a, self.root_b)
        self.assertIsNone(logical_owner_root("other/a.mp4", roots))
        self.assertIsNone(logical_owner_root("a.mp4", roots))

    def test_empty_root_spec_has_no_owner(self) -> None:
        self.assertIsNone(logical_owner_root("a.mp4", ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
