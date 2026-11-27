"""작업 폴더 범위 판정과 논리 파일명 소속 판정(is_within_roots, logical_owner_root, LogicalOwnerResolver) 검증"""

import os
import tempfile
import unittest
from unittest import mock

from src.core import workspace_paths as paths_module
from src.core.workspace_paths import LogicalOwnerResolver, is_within_roots, logical_owner_root


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


class TestLogicalOwnerResolverEquivalence(_RootCase):
    """행마다 쓰는 판정기가 기존 함수와 같은 소유 판정을 내는지"""

    def setUp(self) -> None:
        super().setUp()
        for path in (
            os.path.join(self.root_a, "a.mp4"),
            os.path.join(self.root_a, "only_a.mp4"),
            os.path.join(self.root_a, "sub", "nested.mp4"),
            os.path.join(self.root_a_twin, "sibling.mp4"),
            os.path.join(self.root_b, "only_b.mp4"),
        ):
            with open(path, "wb") as handle:
                handle.write(b"x")

    def _assert_same(self, roots: tuple[str, ...], names: tuple[str, ...]) -> None:
        """판정기 결과가 기존 함수 결과와 같은지 이름마다 비교"""
        resolver = LogicalOwnerResolver(roots)
        for name in names:
            with self.subTest(roots=roots, name=name):
                self.assertEqual(resolver.owner(name), logical_owner_root(name, roots))

    def test_single_root_matches_logical_owner_root(self) -> None:
        """단일 폴더에서 직속 파일, 하위 폴더, 없는 파일, 다른 줄기 이름이 기존 함수와 같은지 검증"""
        self._assert_same(
            (self.root_a,),
            (
                "a.mp4",
                "A.MP4",
                "only_a.mp4",
                "absent.mp4",
                "sub/nested.mp4",
                "sub/missing.mp4",
                "absent/a.mp4",
                "root_dir_b/only_b.mp4",
            ),
        )

    def test_multi_root_matches_logical_owner_root(self) -> None:
        """여러 폴더에서 라벨로 소유를 고르는 판정이 기존 함수와 같은지 검증"""
        self._assert_same(
            (self.root_a, self.root_b),
            (
                "root_dir_a/a.mp4",
                "root_dir_b/only_b.mp4",
                "root_dir_a/sub/nested.mp4",
                "other/a.mp4",
                "a.mp4",
                "root_dir_a/absent.mp4",
            ),
        )

    def test_missing_root_folder_matches_logical_owner_root(self) -> None:
        """작업 폴더가 사라졌을 때도 기존 함수와 같은 판정인지 검증"""
        gone = os.path.join(self.base, "gone_root")
        self._assert_same((gone,), ("a.mp4", "sub/a.mp4"))
        self._assert_same((gone, self.root_b), ("gone_root/a.mp4", "root_dir_b/only_b.mp4"))

    def test_empty_root_spec_has_no_owner(self) -> None:
        """작업 폴더가 없으면 어떤 이름도 소유하지 않는지 검증"""
        resolver = LogicalOwnerResolver("")
        self.assertIsNone(resolver.owner("a.mp4"))
        self.assertIsNone(resolver.owner("root_dir_a/a.mp4"))


class TestLogicalOwnerResolverCost(_RootCase):
    """행 수가 늘어도 폴더 파싱과 파일 존재 조회가 반복되지 않는지"""

    ROW_COUNT: int = 3_000

    def test_folder_scan_happens_once_for_thousands_of_rows(self) -> None:
        """수천 행을 판정해도 폴더 파싱 한 번, isfile 호출 없이 끝나는지 검증"""
        with open(os.path.join(self.root_a, "a.mp4"), "wb") as handle:
            handle.write(b"x")
        with (
            mock.patch.object(paths_module, "parse_root_dirs", wraps=paths_module.parse_root_dirs) as parse,
            mock.patch.object(paths_module.os.path, "isfile", wraps=os.path.isfile) as isfile,
            mock.patch.object(paths_module.os, "scandir", wraps=os.scandir) as scandir,
        ):
            resolver = LogicalOwnerResolver([self.root_a])
            for index in range(self.ROW_COUNT):
                resolver.owner(f"clip_{index}.mp4")
            self.assertEqual(resolver.owner("a.mp4"), self.root_a)
        self.assertLessEqual(parse.call_count, 2)
        self.assertEqual(isfile.call_count, 0)
        self.assertEqual(scandir.call_count, 1)

    def test_multi_root_label_lookup_happens_once(self) -> None:
        """여러 폴더에서도 라벨 맵을 한 번만 만들어 행마다 재사용하는지 검증"""
        with (
            mock.patch.object(paths_module, "build_root_label_map", wraps=paths_module.build_root_label_map) as labels,
            mock.patch.object(paths_module, "parse_root_dirs", wraps=paths_module.parse_root_dirs) as parse,
        ):
            resolver = LogicalOwnerResolver([self.root_a, self.root_b])
            for index in range(self.ROW_COUNT):
                resolver.owner(f"root_dir_a/clip_{index}.mp4")
        self.assertEqual(labels.call_count, 1)
        self.assertLessEqual(parse.call_count, 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestResolveWithNormalizedKey(_RootCase):
    """정규화 키(소문자로 바뀐 목록)로 풀어도 라벨이 같은 작업 폴더를 가리키는지"""

    def test_label_matches_case_insensitively(self) -> None:
        with tempfile.TemporaryDirectory() as base:
            work_a = os.path.join(base, "WorkA")
            work_b = os.path.join(base, "WorkB")
            spec = f"{work_a};{work_b}"
            resolved = paths_module.resolve_source_filepath(
                paths_module.normalize_root_key(spec), "WorkB/x.mp4"
            )
            self.assertEqual(os.path.normcase(resolved), os.path.normcase(os.path.join(work_b, "x.mp4")))
