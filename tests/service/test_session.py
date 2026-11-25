"""UI 무관 ClassifierSession의 상태 보관과 설정 비의존 생성 계약 검증"""

import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from src.core.config import ClassifierConfig, EncodeConfig
from src.core.exceptions import DomainFileNotFoundError, DomainValueError
from src.core.models import VideoProps
from src.core.smart_action import SmartAction
from src.service.analysis_probe import StoredRows
from src.service.cache import DEFAULT_CACHE_REGISTRY, CacheRegistry, TableCache
from src.service.session import ClassifierSession, normalize_root_key


class TestClassifierSession(unittest.TestCase):
    """분류 세션의 생성 부수효과, 예외 규칙, 캐시 조회 동작을 검증"""

    def setUp(self) -> None:
        """테스트 간 클래스 전역 캐시 누수를 막기 위해 종료 시 캐시 초기화 등록"""
        self.addCleanup(DEFAULT_CACHE_REGISTRY.invalidate)

    def test_init_does_not_read_or_write_app_state(self) -> None:
        """세션 생성이 상태 파일을 읽거나 쓰지 않는 순수 객체임을 검증"""
        with (
            tempfile.TemporaryDirectory() as tmpdir,
            patch("src.core.settings.app_state.load_app_state", side_effect=AssertionError),
            patch("src.core.settings.app_state.save_app_state", side_effect=AssertionError),
            patch(
                "src.core.settings.app_state.ensure_default_config_files",
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

    def test_cached_report_defaults_without_cache(self) -> None:
        """캐시가 없을 때 get_cached_report가 None을 반환함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ClassifierSession(tmpdir, ClassifierConfig())
            self.assertIsNone(session.get_cached_report())
            self.assertFalse(session.has_cache)

    def test_set_root_dir_rejects_empty_path(self) -> None:
        """빈 작업 폴더 경로를 도메인 예외로 거부함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ClassifierSession(tmpdir, ClassifierConfig())
            with self.assertRaises(DomainValueError):
                session.set_root_dir("")

    def test_get_summary_report_forwards_prescan_and_callbacks_and_invalidates(self) -> None:
        """복원 판정 결과가 분석 준비에 그대로 전달되고, 콜백 설정과 캐시 초기화가 서비스에 닿는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ClassifierSession(tmpdir, ClassifierConfig(), registry=CacheRegistry())
            prescan = StoredRows((), frozenset(), False)
            service = session._service
            with (
                patch.object(service, "prepare_cache") as prepare,
                patch.object(service, "evaluate_all", return_value=MagicMock()) as evaluate,
            ):
                session.get_summary_report(ensure_keyframe=True, prescan=prescan)
            prepare.assert_called_once_with(force_keyframe=True, prescan=prescan)
            evaluate.assert_called_once_with(ensure_keyframe=True, prescan=prescan)

            progress = MagicMock()
            session.set_callbacks(on_progress=progress, on_is_cancelled=None)
            self.assertIs(service.on_progress, progress)
            self.assertIsNone(service.on_is_cancelled)

            with patch.object(service, "invalidate_cache") as invalidate:
                session.invalidate_cache()
            invalidate.assert_called_once_with()


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

    def test_exception_keyword_with_dot_is_not_treated_as_expression(self) -> None:
        """dev.mp4, final_v.2 같은 점이 든 파일명 키워드가 조건식 오류가 아닌 키워드로 처리됨을 검증"""
        from src.core.evaluation.exception_rules import validate_exception_token

        for token in ("dev.mp4", "final_v.2", "obs v.1"):
            fn, err = validate_exception_token(token)
            self.assertIsNone(err)
            self.assertIsNotNone(fn)
        fn_bad, err_bad = validate_exception_token("v.fps >= 6O")
        self.assertIsNone(fn_bad)
        self.assertIsNotNone(err_bad)


class TestManualOutputTierSession(unittest.TestCase):
    """세션의 출력 해상도 직접 지정 설정, 해제, 작업 폴더별 저장소, 분석 시 정리, 재평가 반영을 검증"""

    def test_set_and_clear_manual_tiers_within_current_root(self) -> None:
        """지정, 자동 복귀, 모두 해제가 현재 작업 폴더 항목만 바꾸고 빈 폴더 항목은 저장소에서 제외됨을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ClassifierSession(tmpdir, ClassifierConfig(), registry=CacheRegistry())
            session.set_manual_target_tier(["a.mp4", "b.mp4"], "720p")
            self.assertEqual(session.manual_target_tiers, {"a.mp4": "720p", "b.mp4": "720p"})

            session.set_manual_target_tier(["a.mp4"], "keep")
            session.set_manual_target_tier(["b.mp4"], None)
            self.assertEqual(session.manual_target_tiers, {"a.mp4": "keep"})

            session.clear_manual_target_tiers()
            self.assertEqual(session.manual_target_tiers, {})
            self.assertEqual(session.manual_target_store, {})

    def test_manual_tiers_are_scoped_per_working_folder(self) -> None:
        """작업 폴더를 바꾸면 이전 폴더의 직접 지정이 보이지 않고 돌아오면 복원됨을 검증"""
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            session = ClassifierSession(first, ClassifierConfig(), registry=CacheRegistry())
            session.set_manual_target_tier(["a.mp4"], "720p")

            session.set_root_dir(second)
            self.assertEqual(session.manual_target_tiers, {})
            session.set_manual_target_tier(["z.mp4"], "keep")
            session.clear_manual_target_tiers()
            self.assertEqual(
                session.manual_target_store, {normalize_root_key(first): {"a.mp4": "720p"}}
            )

            session.set_root_dir(first)
            self.assertEqual(session.manual_target_tiers, {"a.mp4": "720p"})

    def test_same_folder_spellings_share_one_entry(self) -> None:
        """구분자, 끝 구분자, 대소문자가 달라도 같은 폴더는 한 항목으로 조회, 저장되고 옛 키는 같은 키로 합쳐짐을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ClassifierSession(tmpdir, ClassifierConfig(), registry=CacheRegistry())
            session.set_manual_target_tier(["a.mp4"], "720p")
            spellings = [tmpdir.replace(os.sep, "/"), tmpdir + os.sep, tmpdir.swapcase()]
            for spelling in spellings:
                if not os.path.isdir(spelling):
                    continue
                session.set_root_dir(spelling)
                with self.subTest(spelling=spelling):
                    self.assertEqual(session.manual_target_tiers, {"a.mp4": "720p"})

            session.set_root_dir(tmpdir)
            session.set_manual_target_store(
                {tmpdir.replace(os.sep, "/"): {"a.mp4": "720p"}, tmpdir: {"b.mp4": "keep"}}
            )
            self.assertEqual(session.manual_target_tiers, {"a.mp4": "720p", "b.mp4": "keep"})
            self.assertEqual(list(session.manual_target_store), [normalize_root_key(tmpdir)])

    def test_invalid_manual_value_is_rejected(self) -> None:
        """규격 키와 keep 외 값은 도메인 예외로 거부하고 저장하지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ClassifierSession(tmpdir, ClassifierConfig(), registry=CacheRegistry())
            with self.assertRaises(DomainValueError):
                session.set_manual_target_tier(["a.mp4"], "8K")
            self.assertEqual(session.manual_target_tiers, {})

    def test_store_round_trip_applies_to_current_folder(self) -> None:
        """저장된 전체 맵을 적용하면 현재 작업 폴더 몫만 조회되고 저장용 사본에는 빈 항목이 빠짐을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            session = ClassifierSession(tmpdir, ClassifierConfig(), registry=CacheRegistry())
            session.set_manual_target_store(
                {tmpdir: {"a.mp4": "keep"}, os.path.join(tmpdir, "other"): {}}
            )
            self.assertEqual(session.manual_target_tiers, {"a.mp4": "keep"})
            self.assertEqual(
                session.manual_target_store, {normalize_root_key(tmpdir): {"a.mp4": "keep"}}
            )

    def test_report_reflects_manual_tier_changes_without_rescan(self) -> None:
        """메모리 캐시 기준 재평가에 직접 지정이 즉시 반영되고 자동으로 되돌리면 원래 판정으로 돌아감을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vid = VideoProps("a.mp4", 1920, 1080, 0, 30.0, 9000, 128, 107.0, 100.0, "h264", 1.0)
            registry = CacheRegistry()
            registry.put(tmpdir, TableCache(tmpdir, [vid], include_keyframe=True))
            cfg = ClassifierConfig(encode=EncodeConfig(max_resolution_tier="720p"))
            session = ClassifierSession(tmpdir, cfg, registry=registry)

            report = session.get_cached_report()
            assert report is not None
            self.assertEqual(report.evaluations[0].smart_action, SmartAction.ENCODE_DOWNSCALE)

            session.set_manual_target_tier(["a.mp4"], "keep")
            report = session.get_cached_report()
            assert report is not None
            self.assertNotEqual(report.evaluations[0].smart_action, SmartAction.ENCODE_DOWNSCALE)

            session.set_manual_target_tier(["a.mp4"], None)
            report = session.get_cached_report()
            assert report is not None
            self.assertEqual(report.evaluations[0].smart_action, SmartAction.ENCODE_DOWNSCALE)

    def test_analysis_prunes_names_missing_from_root(self) -> None:
        """분석 시 작업 폴더에 없는 파일명만 직접 지정 항목에서 삭제함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg = ClassifierConfig()
            for path in (os.path.join(tmpdir, "a.mp4"), os.path.join(tmpdir, "b.mp4")):
                with open(path, "wb") as f:
                    f.write(b"v")
            vid = VideoProps("a.mp4", 1280, 720, 0, 30.0, 3000, 128, 40.0, 100.0, "h264", 1.0)
            registry = CacheRegistry()
            registry.put(tmpdir, TableCache(tmpdir, [vid], include_keyframe=True))
            session = ClassifierSession(tmpdir, cfg, registry=registry)
            session.set_manual_target_store(
                {tmpdir: {"a.mp4": "720p", "b.mp4": "keep", "gone.mp4": "720p"}}
            )

            session.get_summary_report()
            self.assertEqual(session.manual_target_tiers, {"a.mp4": "720p", "b.mp4": "keep"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
