import unittest

from src.classifier import VideoClassifier
import src
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
