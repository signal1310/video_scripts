"""UI 무관 ClassifierSession의 상태 보관과 설정 비의존 생성 계약 검증"""

import tempfile
import unittest
from unittest.mock import patch

from src.core.config import ClassifierConfig
from src.core.exceptions import DomainValueError
from src.service.cache import DEFAULT_CACHE_REGISTRY
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
