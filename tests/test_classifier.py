import io
import json
import os
import tempfile
import unittest
from collections.abc import Callable
from contextlib import redirect_stdout

import src
from src.classifier import VideoClassifier
from src.core.app_state import CONFIG_FILENAME, DATA_DIR_NAME, load_app_state, save_app_state
from src.core.exceptions import DomainFileNotFoundError
from src.core.paths import get_root_dir
from src.service.analysis_store import load_manual_target_store, save_manual_target_store
from src.service.cache import DEFAULT_CACHE_REGISTRY, TableCache
from tests._factories import make_video
from tests._gui_helpers import isolate_app_state


class TestClassifierModule(unittest.TestCase):
    """최상위 src 및 src.classifier의 VideoClassifier 엔트리포인트 구성을 검증"""

    def test_top_level_and_classifier_exports(self) -> None:
        self.assertIs(src.VideoClassifier, VideoClassifier)

    def test_notebook_star_import_exposes_public_api(self) -> None:
        """노트북 기본 템플릿의 from src import * 가 __all__ 전체 심볼을 노출함을 검증"""
        ns: dict[str, object] = {}
        exec("from src import *", ns)
        self.assertLessEqual(set(src.__all__), set(ns))
        self.assertIs(ns["VideoClassifier"], VideoClassifier)

    def test_facade_public_state_properties_and_set_config(self) -> None:
        """VideoClassifier 파사드가 내부 service 속성을 직접 노출하지 않고 상태 조회/설정 메서드를 제공함을 검증"""
        import tempfile

        from src.core.config import ClassifierConfig, CuiConfig, ThresholdsConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            vc = VideoClassifier(root_dir=tmpdir, include_keyframe_interval=False)
            self.assertFalse(vc.include_keyframe)
            self.assertFalse(vc.has_cache)

            new_cfg = ClassifierConfig(
                thresholds=ThresholdsConfig(over_bitrate_ratio=1.45),
                cui=CuiConfig(filename_maxlen=60),
            )
            vc.set_config(new_cfg)
            self.assertAlmostEqual(vc.config.thresholds.over_bitrate_ratio, 1.45)
            self.assertEqual(vc.filename_maxlen, 60)

    def _isolated_root(self) -> str:
        """설정, 작업 폴더 기록 경로를 격리하고 작업 폴더로 쓸 임시 폴더 경로를 반환"""
        state_root = tempfile.TemporaryDirectory()
        work = tempfile.TemporaryDirectory()
        self.addCleanup(state_root.cleanup)
        self.addCleanup(work.cleanup)
        isolate_app_state(self, state_root.name)
        self._state_root = state_root.name
        return work.name

    def _write_files(self, work_dir: str, *, legacy_root_dir: str | None = None, over_ratio: float | None = None) -> None:
        """작업 폴더 기록 파일과(선택) 구형 root_dir, 임계값이 담긴 설정 JSON을 기록"""
        data_dir = os.path.join(self._state_root, DATA_DIR_NAME)
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(self._state_root, ".rootdir"), "w", encoding="utf-8") as f:
            f.write(work_dir)
        state: dict[str, object] = {}
        if legacy_root_dir is not None:
            state["paths"] = {"root_dir": legacy_root_dir}
        if over_ratio is not None:
            state["analysis"] = {"thresholds": {"over_bitrate_ratio": over_ratio}}
        with open(os.path.join(data_dir, CONFIG_FILENAME), "w", encoding="utf-8") as f:
            json.dump(state, f)

    def _classifier_with_cache(self) -> VideoClassifier:
        """hevc 영상 1개와 h264 영상 1개가 캐시된 분류기를 반환"""
        work = self._isolated_root()
        self._write_files(work)
        videos = [
            make_video(filename="keep_cui_clip.hevc.mp4", vid_kbps=9500, vid_size_MB=80.0, codec="hevc"),
            make_video(filename="regular.mp4", vid_kbps=9500, vid_size_MB=80.0),
        ]
        DEFAULT_CACHE_REGISTRY.put(work, TableCache(work, videos, include_keyframe=True))
        return VideoClassifier()

    def test_missing_rootdir_raises_domain_error(self) -> None:
        """작업 폴더 기록이 없으면 도메인 예외가 발생함을 검증"""
        self._isolated_root()
        with self.assertRaises(DomainFileNotFoundError):
            VideoClassifier()

    def test_rootdir_file_is_the_only_work_dir_source(self) -> None:
        """구형 JSON에 root_dir이 남아 있어도 rootdir 기록 파일만 작업 경로로 쓰고 임계값은 JSON에서 읽음을 검증"""
        work = self._isolated_root()
        self._write_files(work, legacy_root_dir=tempfile.gettempdir(), over_ratio=1.35)
        vc = VideoClassifier()
        self.assertEqual(vc.root_dir, work)
        self.assertAlmostEqual(vc.config.thresholds.over_bitrate_ratio, 1.35)

    def test_cui_lambda_rule_survives_exception_token_update(self) -> None:
        """CUI 람다 예외 규칙이 set_exception_tokens로 토큰을 바꿔도 유지됨을 검증"""
        vc = self._classifier_with_cache()
        vc.add_exception_rule(lambda v: "keep_cui" in v.filename)
        vc.set_exception_tokens(["v.codec == 'h264'"])
        self.assertEqual(vc.exception_tokens, ["v.codec == 'h264'"])
        targets = {e.video.filename: e.is_encode_target for e in vc.get_summary_report(ensure_keyframe=False).evaluations}
        self.assertEqual(targets, {"keep_cui_clip.hevc.mp4": False, "regular.mp4": False})

    def test_save_state_persists_settings_and_optionally_root_dir(self) -> None:
        """save_state가 임계값과 예외 토큰을 저장하고, save_root_dir=True일 때만 작업 폴더 기록을 갱신함을 검증"""
        vc = self._classifier_with_cache()
        vc.set_exception_tokens(["v.codec == 'h264'"])
        vc.set_over_bitrate_ratio(1.75)
        vc.save_state(save_root_dir=False)
        saved = load_app_state()
        self.assertAlmostEqual(saved.analysis.thresholds.over_bitrate_ratio, 1.75)
        self.assertEqual(saved.ui.exception_tokens, ["v.codec == 'h264'"])

        vc.save_state(save_root_dir=True)
        self.assertEqual(get_root_dir(), vc.root_dir)

    def test_reload_state_applies_external_json_edits(self) -> None:
        """파일을 외부에서 수정한 뒤 reload_state가 새 설정을 반영함을 검증"""
        vc = self._classifier_with_cache()
        vc.save_state(save_root_dir=True)
        edited = load_app_state()
        edited.analysis.thresholds.over_bitrate_ratio = 1.90
        save_app_state(edited)
        vc.reload_state()
        self.assertAlmostEqual(vc.config.thresholds.over_bitrate_ratio, 1.90)

    def test_saved_manual_tier_is_applied_on_cui_load_and_saved_back(self) -> None:
        """저장된 직접 지정이 CUI 로드 시 판정에 반영되고, 세션에서 바꾼 값이 save_state로 저장됨을 검증"""
        from src.core.models import VideoProps
        from src.core.smart_action import SmartAction

        work = self._isolated_root()
        self._write_files(work)
        with open(os.path.join(work, "regular.mp4"), "wb") as f:
            f.write(b"v")
        state = load_app_state()
        state.analysis.encode.max_resolution_tier = "720p"
        save_app_state(state)
        save_manual_target_store({work: {"regular.mp4": "keep"}})

        vid = VideoProps("regular.mp4", 1920, 1080, 0, 30.0, 9000, 128, 112.5, 100.0, "h264", 1.0)
        DEFAULT_CACHE_REGISTRY.put(work, TableCache(work, [vid], include_keyframe=True))
        vc = VideoClassifier()
        self.assertEqual(vc.manual_target_tiers, {"regular.mp4": "keep"})
        self.assertNotEqual(
            vc.get_summary_report().evaluations[0].smart_action, SmartAction.ENCODE_DOWNSCALE
        )

        vc.set_manual_target_tier(["regular.mp4"], None)
        self.assertEqual(
            vc.get_summary_report().evaluations[0].smart_action, SmartAction.ENCODE_DOWNSCALE
        )
        vc.save_state()
        self.assertEqual(load_app_state().ui.manual_target_tiers, {})

    def test_save_state_keeps_disk_manual_tiers_when_session_did_not_load_them(self) -> None:
        """config를 직접 넘겨 만든 세션은 직접 지정을 불러오지 않았으므로 save_state가 디스크의 지정을 지우지 않음을 검증"""
        from src.core.config import ClassifierConfig

        work = self._isolated_root()
        self._write_files(work)
        save_manual_target_store({work: {"regular.mp4": "keep"}})

        vc = VideoClassifier(root_dir=work, config=ClassifierConfig())
        self.assertEqual(vc.manual_target_tiers, {})
        vc.save_state()
        self.assertEqual(load_manual_target_store({}).get(work), {"regular.mp4": "keep"})

    def test_print_methods_respect_table_and_summary_options(self) -> None:
        """print_summary는 표 없이, print_tables는 요약 없이, include_summary=False는 요약만 생략하고 출력함을 검증"""
        vc = self._classifier_with_cache()
        vc.add_exception_rule(lambda v: "keep_cui" in v.filename)
        vc.set_exception_tokens(["v.codec == 'h264'"])

        def output(print_fn: Callable[[], None]) -> str:
            buf = io.StringIO()
            with redirect_stdout(buf):
                print_fn()
            return buf.getvalue()

        summary = output(vc.print_summary)
        self.assertNotIn("regular.mp4", summary)
        self.assertIn("판별 기준 요약", summary)

        tables = output(lambda: vc.print_tables(limit=1))
        self.assertIn("keep_cui_clip", tables)
        self.assertIn("외 1개 생략", tables)
        self.assertNotIn("판별 기준 요약", tables)

        table_only = output(lambda: vc.print(include_summary=False))
        self.assertIn("regular.mp4", table_only)
        self.assertNotIn("판별 기준 요약", table_only)


class TestDownscaleClassification(unittest.TestCase):
    """최대 해상도 설정이 서비스 분류 실이동 경로에서 축소 폴더로 옮겨지는지 검증"""

if __name__ == "__main__":
    unittest.main(verbosity=2)
