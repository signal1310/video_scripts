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

    def test_unclassify_files_logs_by_issue_kind_and_returns_text(self) -> None:
        """실패 이슈는 ERROR, 충돌 이슈는 WARNING으로 기록하고 반환값은 기존 문구 리스트인지 검증"""
        import tempfile
        from unittest.mock import patch

        from src.core.file_manager import MoveIssue, MoveIssueKind
        from src.core.messages import format_move_issue

        issues = [
            MoveIssue(MoveIssueKind.UNCLASSIFY_ROOT_EXISTS, "a.mp4", "_1_x"),
            MoveIssue(
                MoveIssueKind.UNCLASSIFY_MOVE_FAILED,
                "b.mp4",
                "_1_x",
                src_path="/src/b.mp4",
                dst_path="/root/b.mp4",
                error="disk full",
            ),
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            vc = VideoClassifier(root_dir=tmpdir)
            with (
                patch(
                    "src.service.classifier_service.VideoClassifierService.unclassify_files",
                    return_value=(0, issues),
                ),
                self.assertLogs("src.service.session", level="WARNING") as cm,
            ):
                moved, texts = vc.unclassify_files()

        self.assertEqual(moved, 0)
        self.assertEqual(texts, [format_move_issue(issue) for issue in issues])
        self.assertEqual([r.levelname for r in cm.records], ["WARNING", "ERROR"])

    def test_facade_public_state_properties_and_set_config(self) -> None:
        """VideoClassifier 파사드가 내부 service 속성을 직접 노출하지 않고 상태 조회/설정 메서드를 제공함을 검증"""
        import tempfile

        from src.core.config import ClassifierConfig, CuiConfig, ThresholdsConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            vc = VideoClassifier(root_dir=tmpdir, include_keyframe_interval=False)
            self.assertFalse(vc.is_pseudo_classify)
            self.assertFalse(vc.include_keyframe)
            self.assertFalse(vc.has_cache)

            vc.pseudo_classify_mode(True)
            self.assertTrue(vc.is_pseudo_classify)

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
        targets = {e.video.filename: e.target_dirname for e in vc.get_summary_report(ensure_keyframe=False).evaluations}
        self.assertEqual(targets, {"keep_cui_clip.hevc.mp4": None, "regular.mp4": None})

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
