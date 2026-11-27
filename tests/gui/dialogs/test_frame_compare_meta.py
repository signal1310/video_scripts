"""화질 비교 세션과 메타 줄, 점수 칩, 재생 위치, 프레임 이미지 변환 검증"""

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QIODevice
from PyQt6.QtGui import QImage
from PyQt6.QtWidgets import QApplication, QLabel, QToolButton, QWidget

from src.core.analysis.frame_analysis import TierScore
from src.core.analysis.frame_compare import ComparePlan, FrameSource, resolve_compare_plan, result_target
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs import frame_compare_meta as compare_meta
from src.service.frame_compare_decoder import DecodedFrame
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _plan() -> ComparePlan:
    target = result_target("b.mp4", width=1920, height=1080, size_bytes=1024)
    return ComparePlan(
        original=FrameSource(key="원본", path="a.mp4", label="원본", detail="3840x2160 1.42 GB"),
        targets=(target,),
        default_key="result",
    )


class SessionTests(unittest.TestCase):
    """세션 기본값"""

    def test_session_defaults(self) -> None:
        session = compare_meta.CompareSession(plan=_plan(), duration_sec=10.0, fps=30.0, filename="a.mp4")
        self.assertEqual(session.position_sec, 0.0)
        self.assertEqual(session.scores, ())
        self.assertEqual((session.width, session.height), (0, 0))
        self.assertEqual(session.source_path, "")
        self.assertEqual(session.scale_algorithm, "bicubic")


class MetaWidgetTests(GuiTestCase):
    """메타 줄과 점수 칩 위젯 구성"""

    def test_meta_and_score_labels(self) -> None:
        parent = QWidget()
        self.addCleanup(parent.deleteLater)
        meta = compare_meta.build_meta_row(parent)
        self.assertEqual(
            [meta.original.text(), meta.target.text(), meta.time.text()], ["", "", ""]
        )
        self.assertEqual(meta.original.objectName(), "CompareMetaText")
        score = compare_meta.build_score_label(parent)
        self.addCleanup(score.deleteLater)
        self.assertTrue(score.isHidden())
        self.assertEqual(score.objectName(), "CompareScoreChip")


class SyncTests(GuiTestCase):
    """재생 위치, 대상 라벨, 점수 표시 동기화"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.source = os.path.join(tmp.name, "a.mp4")
        with open(self.source, "wb") as handle:
            handle.write(b"x")
        self.meta = compare_meta.build_meta_row(self.parent)
        stage = layout.build_stage(self.parent, lambda _scale: None, lambda _side: None)
        self.addCleanup(stage.overlay.deleteLater)
        self.canvas = stage.canvas
        self.target_button = QToolButton(self.parent)
        self.addCleanup(self.target_button.deleteLater)
        self.score_label = compare_meta.build_score_label(self.parent)
        self.addCleanup(self.score_label.deleteLater)

    def test_sync_position_updates_the_meta_timecode(self) -> None:
        compare_meta.sync_position(self.meta, 30.0)
        self.assertEqual(self.meta.time.text(), "시점 00:30.000")

    def test_sync_target_labels_uses_path_tooltips_and_canvas_labels(self) -> None:
        plan = _plan()
        target = plan.target("result")
        assert target is not None
        compare_meta.sync_target_labels(self.meta, self.target_button, self.canvas, plan, target)
        self.assertEqual(self.meta.original.text(), "원본 3840x2160 1.42 GB")
        self.assertEqual(self.meta.original.toolTip(), "원본 파일: a.mp4")
        self.assertEqual(self.meta.target.text(), "결과 1920x1080 1 KB")
        self.assertEqual(self.meta.target.toolTip(), "결과 파일: b.mp4")
        self.assertEqual(self.target_button.text(), "결과")
        self.assertEqual(self.canvas._left_label.text(), "원본")
        self.assertEqual(self.canvas._right_label.text(), "결과")

    def test_sync_target_labels_keeps_the_roundtrip_tooltip(self) -> None:
        plan = resolve_compare_plan(source_path=self.source, width=3840, height=2160, size_bytes=1024)
        target = plan.target("720p")
        assert target is not None
        compare_meta.sync_target_labels(self.meta, self.target_button, self.canvas, plan, target)
        self.assertEqual(self.meta.target.toolTip(), target.tooltip)
        self.assertIn("축소", self.meta.target.toolTip())

    def test_show_score_only_for_cached_downscale_tiers(self) -> None:
        plan = resolve_compare_plan(source_path=self.source, width=3840, height=2160, size_bytes=1024)
        target = plan.target("720p")
        assert target is not None
        compare_meta.show_score(self.score_label, target, ())
        self.assertTrue(self.score_label.isHidden())
        scores = (TierScore(tier="720p", ssim=0.9983, vmaf=98.7),)
        compare_meta.show_score(self.score_label, target, scores)
        self.assertFalse(self.score_label.isHidden())
        self.assertEqual(self.score_label.text(), "화질 분석 720p: VMAF 98.7, SSIM 0.9983")
        self.assertIn("VMAF는 100에 가까울수록", self.score_label.toolTip())
        result = _plan().target("result")
        assert result is not None
        compare_meta.show_score(self.score_label, result, scores)
        self.assertTrue(self.score_label.isHidden())

    def test_score_label_hides_when_no_metric_was_measured(self) -> None:
        label = QLabel("이전")
        self.addCleanup(label.deleteLater)
        plan = resolve_compare_plan(source_path=self.source, width=3840, height=2160, size_bytes=1024)
        target = plan.target("720p")
        assert target is not None
        compare_meta.show_score(label, target, (TierScore(tier="720p", ssim=None, vmaf=None),))
        self.assertTrue(label.isHidden())


class FrameImageTests(GuiTestCase):
    """메모리 RGB24 프레임과 BMP 바이트 변환"""

    def test_raw_rgb24_frame_becomes_a_qimage(self) -> None:
        frame = DecodedFrame(width=2, height=1, bytes_per_line=6, data=bytes([255, 0, 0, 0, 255, 0]))
        image = compare_meta.frame_image(frame, b"")
        self.assertEqual(image.pixelColor(0, 0).name(), "#ff0000")
        self.assertEqual(image.pixelColor(1, 0).name(), "#00ff00")

    def test_bmp_bytes_are_read_when_no_raw_frame(self) -> None:
        source = QImage(2, 1, QImage.Format.Format_RGB32)
        source.fill(0xFF0000)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        source.save(buffer, "BMP")
        image = compare_meta.frame_image(None, bytes(buffer.data()))
        self.assertEqual(image.pixelColor(0, 0).name(), "#ff0000")


if __name__ == "__main__":
    unittest.main(verbosity=2)
