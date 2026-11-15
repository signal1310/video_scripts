"""UI 무관 ClassifierSession의 상태 보관과 설정 비의존 생성 계약 검증"""

import os
import tempfile
import unittest
from unittest.mock import patch

from src.core.config import ClassifierConfig
from src.core.exceptions import DomainFileNotFoundError, DomainValueError
from src.core.models import VideoProps
from src.service.cache import DEFAULT_CACHE_REGISTRY, CacheRegistry, TableCache
from src.service.session import ClassifierSession


class TestClassifierSession(unittest.TestCase):
    """분류 세션의 생성 부수효과, 예외 규칙, 캐시 조회 동작을 검증"""

    def setUp(self) -> None:
        """테스트 간 클래스 전역 캐시 누수를 막기 위해 종료 시 캐시 초기화 등록"""
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)

    def test_init_does_not_read_or_write_app_state(self) -> None:
        """세션 생성이 상태 파일을 읽거나 쓰지 않는 순수 객체임을 검증"""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("src.core.app_state.load_app_state", side_effect=AssertionError),
            patch("src.core.app_state.save_app_state", side_effect=AssertionError),
            patch(
                "src.core.app_state.ensure_default_config_files",
                side_effect=AssertionError,
            ),
        ):
            session = ClassifierSession(tmpdir, ClassifierConfig())
            self.assertEqual(session.root_dir, tmpdir)

    def test_exception_tokens_and_rules_sync(self) -> None:
        """예외 토큰 정규화, 커스텀 규칙 보존, 일괄 초기화 동작을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ClassifierSession(tmpdir, ClassifierConfig())
            session.set_exception_tokens([" v.codec == 'h264' ", "   "])
            self.assertEqual(session.exception_tokens, ["v.codec == 'h264'"])
            self.assertEqual(len(session.exception_rules), 1)

            session.add_exception_rule(lambda _v: False)
            self.assertEqual(len(session.exception_rules), 2)

            session.clear_exception_rules()
            self.assertEqual(session.exception_tokens, [])
            self.assertEqual(session.exception_rules, [])

    def test_cached_report_and_pseudo_mode_defaults(self) -> None:
        """캐시가 없을 때 get_cached_report가 None을 반환하고 모의 분류 모드를 토글함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ClassifierSession(tmpdir, ClassifierConfig())
            self.assertIsNone(session.get_cached_report())
            self.assertFalse(session.has_cache)
            self.assertFalse(session.is_pseudo_classify)

            session.pseudo_classify_mode(True)
            self.assertTrue(session.is_pseudo_classify)

    def test_set_root_dir_rejects_empty_path(self) -> None:
        """빈 작업 폴더 경로를 도메인 예외로 거부함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ClassifierSession(tmpdir, ClassifierConfig())
            with self.assertRaises(DomainValueError):
                session.set_root_dir("")


class TestClassifierSessionSafety(unittest.TestCase):
    """세션의 무효 예외 토큰 경고, 경로 검증, 복원 후 캐시 일관성을 검증"""

    def test_invalid_exception_token_is_reported_not_silently_dropped(self) -> None:
        """파싱 불가 예외 토큰을 건너뛰면서 경고 로그를 남김을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ClassifierSession(tmpdir, ClassifierConfig(), registry=CacheRegistry())
            with self.assertLogs("src.service.session", level="WARNING") as cm:
                session.set_exception_tokens(["v.fps >= 6O", "keep"])
            self.assertEqual(len(session.exception_rules), 1)
            self.assertIn("v.fps >= 6O", cm.output[0])

    def test_set_root_dir_rejects_missing_directory(self) -> None:
        """존재하지 않는 작업 폴더로의 변경을 거부함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ClassifierSession(tmpdir, ClassifierConfig(), registry=CacheRegistry())
            with self.assertRaises(DomainFileNotFoundError):
                session.set_root_dir(os.path.join(tmpdir, "missing"))

    def test_unclassify_after_restart_drops_stale_cache_so_restored_files_appear(self) -> None:
        """캐시에 없던 하위 폴더 파일이 복원되면 캐시를 무효화해 다음 평가에 반영됨을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = os.path.join(tmpdir, ClassifierConfig().dir_standard_cq22)
            os.makedirs(sub)
            for path in (os.path.join(tmpdir, "a.mp4"), os.path.join(sub, "b.mp4")):
                with open(path, "wb") as f:
                    f.write(b"v")
            registry = CacheRegistry()
            vid = VideoProps("a.mp4", 1280, 720, 0, 30.0, 3000, 128, 10.0, 10.0, "h264", 1.0)
            registry.put(tmpdir, TableCache(tmpdir, [vid], include_keyframe=True))
            session = ClassifierSession(tmpdir, ClassifierConfig(), registry=registry)

            moved, _ = session.unclassify_files()
            self.assertEqual(moved, 1)
            self.assertFalse(session.has_cache)

    def test_classify_with_result_reports_real_moved_count(self) -> None:
        """실이동 분류 결과의 이동 파일 수와 충돌 안내가 실제 값을 반영함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = ClassifierConfig()
            with open(os.path.join(tmpdir, "a.mp4"), "wb") as f:
                f.write(b"v")
            vid = VideoProps("a.mp4", 1920, 1080, 0, 30.0, 20000, 128, 500.0, 60.0, "h264", 1.0)
            registry = CacheRegistry()
            registry.put(tmpdir, TableCache(tmpdir, [vid], include_keyframe=True))
            session = ClassifierSession(tmpdir, cfg, registry=registry)

            _, moved, warnings = session.classify_with_result(dry_run=False)
            self.assertEqual(moved, 1)
            self.assertEqual(warnings, [])
            _, moved_again, _ = session.classify_with_result(dry_run=False)
            self.assertEqual(moved_again, 0)

    def test_exception_keyword_with_dot_is_not_treated_as_expression(self) -> None:
        """dev.mp4, final_v.2 같은 점이 든 파일명 키워드가 조건식 오류가 아닌 키워드로 처리됨을 검증"""
        from src.core.exception_rules import validate_exception_token

        for token in ("dev.mp4", "final_v.2", "obs v.1"):
            fn, err = validate_exception_token(token)
            self.assertIsNone(err)
            self.assertIsNotNone(fn)
        fn_bad, err_bad = validate_exception_token("v.fps >= 6O")
        self.assertIsNone(fn_bad)
        self.assertIsNotNone(err_bad)

if __name__ == "__main__":
    unittest.main(verbosity=2)
