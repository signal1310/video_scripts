"""화질 비교 세션과 메타 줄, 점수 줄, 재생 위치 표시 동기화 검증"""

import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QLabel, QWidget

from src.core.analysis.frame_analysis import TierScore
from src.core.analysis.frame_compare import ComparePlan, FrameSource, resolve_compare_plan, result_target
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs import frame_compare_meta as compare_meta
from tests._gui_helpers import GuiTestCase

_APP = QApplication.instance() or QApplication([])


def _timeline(parent: QWidget) -> layout.CompareTimeline:
    return layout.build_timeline(
        parent,
        lambda _step: None,
        lambda _percent: None,
        lambda _value: None,
        lambda: None,
        lambda _rate: None,
        lambda: None,
        lambda _value: None,
    )


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


class MetaWidgetTests(GuiTestCase):
    """메타 줄과 점수 줄 위젯 구성"""

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
        self.assertEqual(score.objectName(), "CompareScore")


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
        self.timeline = _timeline(self.parent)
        self.addCleanup(self.timeline.slider.deleteLater)
        self.meta = compare_meta.build_meta_row(self.parent)
        stage = layout.build_stage(self.parent, lambda _scale: None, lambda _side: None)
        self.addCleanup(stage.overlay.deleteLater)
        self.canvas = stage.canvas
        toolbar = layout.build_toolbar(self.parent, lambda _key: None, lambda _key: None)
        self.target_button = toolbar.target_button
        self.score_label = compare_meta.build_score_label(self.parent)
        self.addCleanup(self.score_label.deleteLater)

    def test_sync_position_updates_slider_labels_and_chips(self) -> None:
        self.timeline.slider.setRange(0, 100_000)
        compare_meta.sync_position(self.timeline, self.meta, 30.0, 100.0)
        self.assertEqual(self.timeline.slider.value(), 30_000)
        self.assertEqual(self.timeline.time_label.text(), "00:30.000 / 01:40.000")
        self.assertEqual(self.meta.time.text(), "시점 00:30.000")
        self.assertTrue(self.timeline.mark_buttons[2].isChecked())
        compare_meta.sync_position(self.timeline, self.meta, 0.0, 0.0)
        self.assertFalse(any(chip.isChecked() for chip in self.timeline.mark_buttons))

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


if __name__ == "__main__":
    unittest.main(verbosity=2)

