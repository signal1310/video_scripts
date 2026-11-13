import unittest

import src
from src.classifier import VideoClassifier
from src.video_classify import VideoClassifier as CompatVideoClassifier
from src.video_classify.video_classifier import (
    VideoClassifier as CompatFileVideoClassifier,
)


class TestClassifierModule(unittest.TestCase):
    """
    - 최상위 src 및 src.classifier, src.video_classify의 VideoClassifier 임포트 호환성을 검증.
    """

    def test_top_level_and_classifier_exports(self) -> None:
        self.assertIs(src.VideoClassifier, VideoClassifier)
        self.assertIs(CompatVideoClassifier, VideoClassifier)
        self.assertIs(CompatFileVideoClassifier, VideoClassifier)

    def test_facade_public_state_properties_and_set_config(self) -> None:
        """
        - VideoClassifier 파사드가 내부 service 속성을 직접 노출하지 않고 상태 조회/설정 메서드를 제공함을 검증.
        """
        import tempfile

        from src.core.config import ClassifierConfig

        with tempfile.TemporaryDirectory() as tmpdir:
            vc = VideoClassifier(root_dir=tmpdir, include_keyframe_interval=False)
            self.assertFalse(vc.is_pseudo_classify)
            self.assertFalse(vc.include_keyframe)
            self.assertFalse(vc.has_cache)

            vc.pseudo_classify_mode(True)
            self.assertTrue(vc.is_pseudo_classify)

            new_cfg = ClassifierConfig(over_bitrate_ratio=1.45, tabulate_filename_maxlen=60)
            vc.set_config(new_cfg)
            self.assertAlmostEqual(vc.config.over_bitrate_ratio, 1.45)
            self.assertEqual(vc.filename_maxlen, 60)

    def test_cui_does_not_import_service_layer(self) -> None:
        """
        - CUI 계층이 Service 계층을 직접 임포트하지 않고 도메인/프로토콜 경계를 유지함을 검증.
        """
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
