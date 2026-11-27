"""다중 작업 폴더, 하위 폴더 재귀 탐색, 작업 폴더 세션과 캐시 레지스트리를 검증하는 테스트"""

import os
import tempfile
import unittest
from unittest.mock import patch

from src.core.analysis.file_manager import (
    compute_dir_signature,
    get_video_filenames,
)
from src.core.config import ClassifierConfig
from src.core.settings.app_state import record_recent_roots
from src.core.workspace_paths import (
    WON_SIGN,
    are_all_valid_dirs,
    build_root_label_map,
    compose_logical_filename,
    count_cross_drive_moves,
    format_root_dirs,
    normalize_path_separators,
    normalize_root_spec,
    parse_root_dirs,
    primary_root_dir,
    resolve_source_filepath,
    split_logical_filename,
    to_display_filename,
)
from src.service.analyzed_dirs import is_analyzed_dir, mark_analyzed_dir
from src.service.cache import CacheRegistry, TableCache
from src.service.session import ClassifierSession

_ANALYZED_PATCH = "src.service.analyzed_dirs._marker_path"


def _write(path: str, data: bytes = b"v") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


class TestWorkspacePaths(unittest.TestCase):
    """작업 폴더 문자열 파싱, 라벨, 논리 파일명 변환과 원화 기호 정규화를 검증"""

    def test_display_filename_uses_os_separator_only_for_presentation(self) -> None:
        """표시용 파일명은 하위 폴더 구분자를 운영체제 구분자로 바꾸고, 논리 파일명 자체는 유지함을 검증"""
        logical = "sub/dir/clip.mp4"
        self.assertEqual(to_display_filename(logical), logical.replace("/", os.sep))
        self.assertEqual(to_display_filename("clip.mp4"), "clip.mp4")

    def test_parse_root_dirs_dedupes_case_insensitively(self) -> None:
        """대소문자만 다른 같은 폴더는 한 번만 남기고 입력 순서를 유지함을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "A")
            b = os.path.join(tmp, "B")
            os.makedirs(a)
            os.makedirs(b)
            roots = parse_root_dirs(f"{a}; {a.upper()}; {b}")
            self.assertEqual(len(roots), 2)
            self.assertEqual(roots[1], b)
            self.assertEqual(format_root_dirs(roots), f"{a}; {b}")

    def test_existing_folder_with_separator_in_name_is_not_split(self) -> None:
        """폴더명에 세미콜론이 있어도 실존 폴더 전체면 분할하지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            name_with_sep = os.path.join(tmp, "x;y")
            os.makedirs(name_with_sep)
            self.assertEqual(parse_root_dirs(name_with_sep), (name_with_sep,))
            self.assertTrue(are_all_valid_dirs(name_with_sep))

    def test_labels_disambiguate_same_basename_without_separator(self) -> None:
        """basename이 같은 작업 폴더는 상위 폴더명을 붙여 구분되고, 라벨에 구분자가 없음을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            d1 = os.path.join(tmp, "드라이브A", "드라마")
            d2 = os.path.join(tmp, "드라이브B", "드라마")
            os.makedirs(d1)
            os.makedirs(d2)
            labels, label_to_root = build_root_label_map([d1, d2])
            values = list(labels.values())
            self.assertEqual(len(set(values)), 2)
            self.assertTrue(all("/" not in v and "\\" not in v for v in values))
            self.assertEqual(len(label_to_root), 2)

    def test_logical_name_round_trip_for_multi_root(self) -> None:
        """다중 작업 폴더의 논리 파일명이 소속 폴더와 상대 경로로 다시 분해되고 원본 경로로 복원됨을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "드라마")
            b = os.path.join(tmp, "예능")
            os.makedirs(a)
            os.makedirs(b)
            spec = f"{a};{b}"
            logical = compose_logical_filename(a, "시즌1/01.mp4", spec)
            self.assertTrue(logical.endswith("/시즌1/01.mp4"))
            root, rel = split_logical_filename(spec, logical)
            self.assertEqual((root, rel), (a, "시즌1/01.mp4"))
            self.assertEqual(
                resolve_source_filepath(spec, logical),
                os.path.normpath(os.path.join(a, "시즌1", "01.mp4")),
            )

    def test_single_root_logical_name_is_plain_relative_path(self) -> None:
        """작업 폴더가 하나면 논리 파일명이 라벨 없이 상대 경로 그대로임을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(compose_logical_filename(tmp, "01.mp4", tmp), "01.mp4")
            self.assertEqual(primary_root_dir(tmp), tmp)

    def test_count_cross_drive_moves_counts_other_drive_only(self) -> None:
        """주 작업 폴더와 드라이브 문자가 다른 파일만 집계함을 검증"""
        self.assertEqual(
            count_cross_drive_moves("C:\\videos", ["D:\\x\\a.mp4", "C:\\videos\\b.mp4"]),
            1,
        )

    def test_normalize_path_separators_converts_won_sign(self) -> None:
        """한국어 키보드의 원화 기호가 백슬래시로 바뀌고 앞뒤 공백이 제거됨을 검증"""
        self.assertEqual(normalize_path_separators("  C:₩Videos₩Drama "), "C:\\Videos\\Drama")


class TestRecursiveScan(unittest.TestCase):
    """하위 폴더 재귀 탐색의 제외 폴더 목록과 숨김 폴더 차단을 검증"""

    def test_recursive_scan_includes_subdirs_and_skips_hidden_dirs(self) -> None:
        """재귀 탐색이 하위 폴더 영상을 '/' 상대 경로로 찾고, 숨김 폴더와 제외 폴더는 진입하지 않음을 검증"""
        with tempfile.TemporaryDirectory() as root:
            _write(os.path.join(root, "01.mp4"))
            _write(os.path.join(root, "시즌1", "01.mp4"))
            _write(os.path.join(root, "제외", "old.mp4"))
            _write(os.path.join(root, ".hidden", "h.mp4"))

            recursive = get_video_filenames(root, recursive=True, excluded_dirnames=["제외"])
            self.assertEqual(recursive, ["01.mp4", "시즌1/01.mp4"])
            self.assertEqual(get_video_filenames(root), ["01.mp4"])

    def test_multi_root_scan_uses_labels_and_recursive_flag(self) -> None:
        """다중 작업 폴더 스캔이 라벨 접두사를 붙이고 재귀 여부를 각 폴더에 적용함을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "드라마")
            b = os.path.join(tmp, "예능")
            _write(os.path.join(a, "01.mp4"))
            _write(os.path.join(a, "시즌1", "02.mp4"))
            _write(os.path.join(b, "01.mp4"))
            spec = f"{a};{b}"

            direct = get_video_filenames(spec)
            self.assertEqual(len(direct), 2)
            self.assertTrue(all(name.endswith("/01.mp4") for name in direct))

            recursive = get_video_filenames(spec, recursive=True)
            self.assertEqual(len(recursive), 3)
            self.assertTrue(any(name.endswith("/시즌1/02.mp4") for name in recursive))

    def test_recursive_signature_changes_when_nested_file_is_added(self) -> None:
        """하위 폴더에 영상이 추가되면 재귀 시그니처가 바뀌고 비재귀 시그니처는 그대로임을 검증"""
        with tempfile.TemporaryDirectory() as root:
            _write(os.path.join(root, "01.mp4"))
            flat_before = compute_dir_signature(root)
            recursive_before = compute_dir_signature(root, recursive=True)
            _write(os.path.join(root, "시즌1", "02.mp4"))
            self.assertEqual(compute_dir_signature(root), flat_before)
            self.assertNotEqual(compute_dir_signature(root, recursive=True), recursive_before)


class TestSessionAndRegistry(unittest.TestCase):
    """다중 작업 폴더 세션 API, 재귀 모드별 캐시 키, 자동 복원 기록 구분을 검증"""

    def test_session_add_root_dir_and_recursive_flag(self) -> None:
        """작업 폴더 추가는 중복을 무시하고, 재귀 설정은 세션과 서비스에 반영됨을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "A")
            b = os.path.join(tmp, "B")
            os.makedirs(a)
            os.makedirs(b)
            session = ClassifierSession(a, ClassifierConfig(), registry=CacheRegistry())

            self.assertTrue(session.add_root_dir(b))
            self.assertEqual(len(session.root_dirs), 2)
            self.assertFalse(session.add_root_dir(b))

            self.assertFalse(session.recursive)
            session.set_recursive(True)
            self.assertTrue(session.recursive)

    def test_cache_registry_separates_recursive_mode(self) -> None:
        """재귀 모드가 다른 캐시는 서로 조회되지 않음을 검증"""
        with tempfile.TemporaryDirectory() as root:
            registry = CacheRegistry()
            cache = TableCache(root, [], include_keyframe=False)
            registry.put(root, cache)
            self.assertIs(registry.get(root), cache)
            self.assertIsNone(registry.get(root, recursive=True))

    def test_analyzed_dirs_recursive_record_covers_descendants(self) -> None:
        """비재귀 기록은 하위 폴더를 덮지 않고, 재귀 기록은 상위 폴더를 덮고 다중 폴더 목록은 모두 기록되어야 함을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "analyzed_dirs.json")
            a = os.path.join(tmp, "A")
            b = os.path.join(tmp, "B")
            os.makedirs(a)
            os.makedirs(b)
            nested = os.path.join(a, "sub")
            os.makedirs(nested)
            with patch(_ANALYZED_PATCH, return_value=marker):
                mark_analyzed_dir(a)
                self.assertTrue(is_analyzed_dir(a))
                self.assertFalse(is_analyzed_dir(a, recursive=True))
                self.assertFalse(is_analyzed_dir(nested))
                mark_analyzed_dir(a, recursive=True)
                self.assertTrue(is_analyzed_dir(a, recursive=True))
                self.assertTrue(is_analyzed_dir(nested))
                self.assertFalse(is_analyzed_dir(f"{a};{b}"))

class TestRootSpecInputs(unittest.TestCase):
    """설정 입력 정규화와 최근 작업 폴더 기록이 다중 작업 폴더 문자열을 폴더 단위로 처리함을 검증"""

    def test_normalize_root_spec_replaces_won_sign_and_normalizes_each_root(self) -> None:
        """원화 기호 입력과 중복 경로가 표준 표기로 정리됨을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "A")
            os.makedirs(a)
            raw = f"{a.replace(os.sep, WON_SIGN)}; {a}{os.sep}"
            self.assertEqual(normalize_root_spec(raw), os.path.normpath(a))

    def test_record_recent_roots_records_each_root_with_primary_first(self) -> None:
        """다중 작업 폴더 문자열이 폴더별로 기록되고 주 작업 폴더가 맨 앞에 옴을 검증"""
        spec = format_root_dirs(["C:/work/A", "C:/work/B"])
        recent = record_recent_roots([], spec)
        self.assertEqual(recent[0], os.path.normpath("C:/work/A"))
        self.assertIn(os.path.normpath("C:/work/B"), recent)
        self.assertNotIn(spec, recent)


if __name__ == "__main__":
    unittest.main(verbosity=2)
