"""다중 작업 폴더, 하위 폴더 재귀 탐색, 해시 매니페스트 기반 평탄 분류와 원위치 복원을 검증하는 테스트"""

import os
import tempfile
import unittest
from unittest.mock import patch

from src.core.app_state import record_recent_roots
from src.core.classify_manifest import (
    extract_hash_from_filename,
    load_manifest_records,
)
from src.core.config import ClassifierConfig
from src.core.file_manager import (
    MoveIssueKind,
    classify_files_batch,
    compute_dir_signature,
    get_video_filenames,
    preview_unclassify_all,
    unclassify_all_files,
)
from src.core.models import VideoProps
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
)
from src.gui.session_controller import evaluate_action_availability
from src.service.analyzed_dirs import is_analyzed_dir, mark_analyzed_dir
from src.service.cache import CacheRegistry, TableCache
from src.service.session import ClassifierSession

_MANIFEST_PATCH = "src.core.classify_manifest.get_classify_manifest_path"
_ANALYZED_PATCH = "src.service.analyzed_dirs._marker_path"
CLASS_DIR = "_1_표준인코딩_CQ22"
DOWNSCALE_PREFIX = "_3_해상도축소_"


def _write(path: str, data: bytes = b"v") -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def _vid(name: str, moved: str | None = None) -> VideoProps:
    return VideoProps(name, 1280, 720, 0, 30.0, 3000, 128, 10.0, 5.0, "h264", moved_dirname=moved)


class TestWorkspacePaths(unittest.TestCase):
    """작업 폴더 문자열 파싱, 라벨, 논리 파일명 변환과 원화 기호 정규화를 검증"""

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
    """하위 폴더 재귀 탐색의 분류 폴더 제외와 숨김 폴더 차단을 검증"""

    def test_recursive_scan_includes_subdirs_and_skips_class_dirs(self) -> None:
        """재귀 탐색이 하위 폴더 영상을 '/' 상대 경로로 찾고, 분류 폴더, 축소 폴더, 숨김 폴더는 제외함을 검증"""
        with tempfile.TemporaryDirectory() as root:
            _write(os.path.join(root, "01.mp4"))
            _write(os.path.join(root, "시즌1", "01.mp4"))
            _write(os.path.join(root, CLASS_DIR, "old.mp4"))
            _write(os.path.join(root, f"{DOWNSCALE_PREFIX}720p", "small.mp4"))
            _write(os.path.join(root, ".hidden", "h.mp4"))

            recursive = get_video_filenames(
                root,
                recursive=True,
                excluded_dirnames=[CLASS_DIR],
                downscale_prefix=DOWNSCALE_PREFIX,
            )
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


class TestFlatClassifyManifest(unittest.TestCase):
    """평탄 분류의 해시 접미사, 매니페스트 기록, 세션 없이 이루어지는 원위치 복원을 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.manifest_path = os.path.join(self._tmp.name, "data", "classify_manifest.json")
        patcher = patch(_MANIFEST_PATCH, return_value=self.manifest_path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_flat_classify_with_hash_manifest_and_restore_across_sessions(self) -> None:
        """여러 작업 폴더의 동명 파일을 한 분류 폴더에 해시 접미사로 모으고, 캐시 없이 원래 위치로 복원함을 검증"""
        root1 = os.path.join(self._tmp.name, "드라마")
        root2 = os.path.join(self._tmp.name, "예능")
        _write(os.path.join(root1, "01.mp4"), b"A")
        _write(os.path.join(root1, "시즌1", "01.mp4"), b"B")
        _write(os.path.join(root2, "01.mp4"), b"C")
        spec = f"{root1};{root2}"

        names = get_video_filenames(spec, recursive=True)
        vids = [_vid(name) for name in names]
        moved, issues = classify_files_batch(spec, [(vid, CLASS_DIR) for vid in vids])
        self.assertEqual(moved, 3)
        self.assertEqual(issues, [])

        class_dir = os.path.join(root1, CLASS_DIR)
        flat_names = sorted(os.listdir(class_dir))
        self.assertEqual(len(flat_names), 3)
        for name in flat_names:
            self.assertIsNotNone(extract_hash_from_filename(name))
        self.assertEqual(len(load_manifest_records()), 3)
        self.assertTrue(all(vid.moved_dirname == CLASS_DIR for vid in vids))

        restorable, conflicts = preview_unclassify_all(spec, dirnames=[CLASS_DIR])
        self.assertEqual((restorable, conflicts), (3, 0))

        restored, restore_issues = unclassify_all_files(
            spec, cached_videos=None, dirnames=[CLASS_DIR]
        )
        self.assertEqual(restored, 3)
        self.assertEqual(restore_issues, [])
        self.assertTrue(os.path.isfile(os.path.join(root1, "01.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(root1, "시즌1", "01.mp4")))
        self.assertTrue(os.path.isfile(os.path.join(root2, "01.mp4")))
        self.assertFalse(os.path.isdir(class_dir))
        self.assertFalse(os.path.isfile(self.manifest_path))

    def test_restore_after_reencode_extension_change_uses_new_extension(self) -> None:
        """샤나인코더 재인코딩으로 확장자가 바뀐 분류 파일을 원래 하위 폴더에 새 확장자로 복원함을 검증"""
        root = os.path.join(self._tmp.name, "작업")
        _write(os.path.join(root, "시즌1", "01.mkv"))
        classify_files_batch(root, [(_vid("시즌1/01.mkv"), CLASS_DIR)])

        class_dir = os.path.join(root, CLASS_DIR)
        (flat_name,) = os.listdir(class_dir)
        stem, _ = os.path.splitext(flat_name)
        os.rename(os.path.join(class_dir, flat_name), os.path.join(class_dir, f"{stem}.mp4"))

        restored, issues = unclassify_all_files(root, dirnames=[CLASS_DIR])
        self.assertEqual(restored, 1)
        self.assertEqual(issues, [])
        self.assertTrue(os.path.isfile(os.path.join(root, "시즌1", "01.mp4")))

    def test_classify_skips_existing_unregistered_target_file(self) -> None:
        """주 작업 폴더 직하위 파일의 이름과 같은 미등록 대상 파일이 있으면 덮어쓰지 않고 건너뜀을 검증"""
        root = os.path.join(self._tmp.name, "작업")
        _write(os.path.join(root, "dup.mp4"), b"SRC")
        _write(os.path.join(root, CLASS_DIR, "dup.mp4"), b"KEEP")

        moved, issues = classify_files_batch(root, [(_vid("dup.mp4"), CLASS_DIR)])
        self.assertEqual(moved, 0)
        self.assertEqual([issue.kind for issue in issues], [MoveIssueKind.CLASSIFY_TARGET_EXISTS])
        with open(os.path.join(root, CLASS_DIR, "dup.mp4"), "rb") as f:
            self.assertEqual(f.read(), b"KEEP")

    def test_manifest_loader_reflects_rewrites(self) -> None:
        """매니페스트 파일을 다시 쓰면 읽기 결과가 즉시 바뀌고, 비우면 파일이 삭제됨을 검증"""
        root = os.path.join(self._tmp.name, "작업")
        _write(os.path.join(root, "a.mp4"))
        classify_files_batch(root, [(_vid("a.mp4"), CLASS_DIR)])
        self.assertEqual(len(load_manifest_records()), 1)

        restored, _ = unclassify_all_files(root, dirnames=[CLASS_DIR])
        self.assertEqual(restored, 1)
        self.assertEqual(load_manifest_records(), {})


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

    def test_analyzed_dirs_separate_recursive_mode(self) -> None:
        """분석 기록이 하위 폴더 포함 여부별로 구분되고 다중 폴더 목록 단위로 기록됨을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "analyzed_dirs.json")
            a = os.path.join(tmp, "A")
            b = os.path.join(tmp, "B")
            os.makedirs(a)
            os.makedirs(b)
            with patch(_ANALYZED_PATCH, return_value=marker):
                mark_analyzed_dir(a)
                self.assertTrue(is_analyzed_dir(a))
                self.assertFalse(is_analyzed_dir(a, recursive=True))
                mark_analyzed_dir(a, recursive=True)
                self.assertTrue(is_analyzed_dir(a, recursive=True))
                self.assertFalse(is_analyzed_dir(f"{a};{b}"))

    def test_action_availability_checks_every_root_for_class_dirs(self) -> None:
        """다중 작업 폴더에서 두 번째 폴더에만 분류 폴더가 있어도 복원 가능으로 판정됨을 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            a = os.path.join(tmp, "A")
            b = os.path.join(tmp, "B")
            os.makedirs(a)
            os.makedirs(os.path.join(b, CLASS_DIR))
            config = ClassifierConfig()

            self.assertEqual(
                evaluate_action_availability(None, format_root_dirs([a, b]), config),
                (False, True),
            )
            self.assertEqual(
                evaluate_action_availability(None, format_root_dirs([a]), config),
                (False, False),
            )


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
