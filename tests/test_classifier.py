import unittest

import src
from src.classifier import VideoClassifier


class TestClassifierModule(unittest.TestCase):
    """최상위 src 및 src.classifier의 VideoClassifier 엔트리포인트 구성을 검증"""

    def test_top_level_and_classifier_exports(self) -> None:
        self.assertIs(src.VideoClassifier, VideoClassifier)

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

    def test_cui_does_not_import_service_layer(self) -> None:
        """CUI 계층이 Service 계층을 직접 임포트하지 않고 도메인/프로토콜 경계를 유지함을 검증"""
        import ast
        import os

        cui_root = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "src", "cui")
        )
        for fname in os.listdir(cui_root):
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(cui_root, fname)
            with open(fpath, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=fpath)
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertFalse(
                        node.module.startswith("src.service"),
                        f"CUI->Service 역방향 임포트 발견: {fpath} ({node.module})",
                    )

    def test_save_reload_state_and_custom_exception_preservation(self) -> None:
        """CUI 사용자 정의 람다 예외 규칙 보존 및 save_state·reload_state 계층 연동을 검증"""
        import os
        import tempfile
        from unittest.mock import patch

        from src.core.app_state import (
            CONFIG_FILENAME,
            DATA_DIR_NAME,
            AppState,
            load_app_state,
            save_app_state,
        )
        from src.core.config import AnalysisConfig, PathsConfig, ThresholdsConfig
        from src.core.exceptions import DomainFileNotFoundError
        from src.core.models import VideoProps

        with (
            tempfile.TemporaryDirectory() as tmpdir,
            tempfile.TemporaryDirectory() as json_dir,
            tempfile.TemporaryDirectory() as rootfile_dir,
        ):
            root_path = os.path.join(tmpdir, ".rootdir")
            state_path = os.path.join(tmpdir, DATA_DIR_NAME, CONFIG_FILENAME)

            with (
                patch("src.core.config.get_rootdir_path", return_value=root_path),
                patch("src.core.app_state.get_state_path", return_value=state_path),
            ):
                # 경로 미해결 시 도메인 예외 발생
                with self.assertRaises(DomainFileNotFoundError):
                    VideoClassifier()

                # JSON보다 .rootdir 파일 경로가 항상 우선 적용됨
                save_app_state(
                    AppState(
                        paths=PathsConfig(root_dir=json_dir),
                        analysis=AnalysisConfig(
                            thresholds=ThresholdsConfig(over_bitrate_ratio=1.35)
                        ),
                    ),
                    sync_rootdir=False,
                )
                with open(root_path, "w", encoding="utf-8") as f:
                    f.write(rootfile_dir)

                vc = VideoClassifier()
                self.assertEqual(vc.root_dir, rootfile_dir)
                self.assertAlmostEqual(vc.config.thresholds.over_bitrate_ratio, 1.35)

                # CUI 람다 예외 규칙은 set_exception_tokens 갱신 시에도 유지됨
                from src.service.cache import TableCache
                from src.service.classifier_service import VideoClassifierService

                cache = TableCache(rootfile_dir)
                cache._data = [
                    VideoProps(
                        filename="keep_cui_clip.hevc.mp4",
                        width=1920,
                        height=1080,
                        rotate_type=0,
                        fps=30.0,
                        vid_kbps=9500,
                        aud_kbps=128,
                        vid_size_MB=80.0,
                        duration=60.0,
                        codec="hevc",
                        keyframe_interval=1.0,
                    ),
                    VideoProps(
                        filename="regular.mp4",
                        width=1920,
                        height=1080,
                        rotate_type=0,
                        fps=30.0,
                        vid_kbps=9500,
                        aud_kbps=128,
                        vid_size_MB=80.0,
                        duration=60.0,
                        codec="h264",
                        keyframe_interval=1.0,
                    ),
                ]
                cache._include_keyframe = True
                VideoClassifierService.inject_cache(rootfile_dir, cache)

                vc.add_exception_rule(lambda v: "keep_cui" in v.filename)
                vc.set_exception_tokens(["v.codec == 'h264'"])
                self.assertEqual(vc.exception_tokens, ["v.codec == 'h264'"])
                rep = vc.get_summary_report(ensure_keyframe=False)
                emap = {e.video.filename: e for e in rep.evaluations}
                self.assertIsNone(emap["keep_cui_clip.hevc.mp4"].target_dirname)
                self.assertIsNone(emap["regular.mp4"].target_dirname)

                vc.set_over_bitrate_ratio(1.75)
                vc.save_state(save_root_dir=False)
                saved = load_app_state()
                self.assertAlmostEqual(saved.analysis.thresholds.over_bitrate_ratio, 1.75)
                self.assertEqual(saved.ui.exception_tokens, ["v.codec == 'h264'"])

                # 외부에서 JSON 설정을 수정 후 reload_state 반영 확인
                saved.analysis.thresholds.over_bitrate_ratio = 1.90
                save_app_state(saved, sync_rootdir=False)
                vc.reload_state()
                self.assertAlmostEqual(vc.config.thresholds.over_bitrate_ratio, 1.90)


if __name__ == "__main__":
    unittest.main(verbosity=2)
