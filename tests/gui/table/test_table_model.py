"""표 모델의 재인코딩 상태 열이 배치 상태 저장소의 표기, 배지, 글자색, 툴팁을 그대로 보이는지 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtGui import QColor, QIcon
from PyQt6.QtWidgets import QApplication

from src.core.encode.encode_batch_book import EncodeBatchBook
from src.core.encode.encode_state import EncodeState
from src.core.workspace_paths import resolve_source_filepath
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.table.table_model import VideoTableModel
from src.gui.theme import ThemeManager
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_ROOT: str = os.path.join("work", "root")
_ICON_PX: int = 12
_DISPLAY = int(Qt.ItemDataRole.DisplayRole)
_TOOLTIP = int(Qt.ItemDataRole.ToolTipRole)
_FOREGROUND = int(Qt.ItemDataRole.ForegroundRole)


class EncodeStateColumnModelTests(unittest.TestCase):
    """재인코딩 상태 열의 셀 값과 배치 상태 변경 신호를 검증"""

    def setUp(self) -> None:
        self.model = VideoTableModel()
        self.model.set_data_source(
            [make_eval(make_video(filename="a.mp4")), make_eval(make_video(filename="b.mp4"))],
            root_dir=_ROOT,
        )
        self.col = VideoTableModel.COL_ENCODE_STATE
        self.path_a = resolve_source_filepath(_ROOT, "a.mp4")
        self.book = EncodeBatchBook()
        self.book.start([self.path_a])

    def _index(self, row: int) -> QModelIndex:
        return self.model.index(row, self.col)

    def test_rows_show_nothing_before_a_batch_is_attached(self) -> None:
        """배치 저장소를 연결하기 전에는 상태 셀이 비어 있는지 검증"""
        self.assertIsNone(self.model.data(self._index(0), _DISPLAY))
        self.assertIsInstance(self.model.data(self._index(0), _TOOLTIP), str)

    def test_batch_target_shows_pending_label_with_muted_color_and_no_badge(self) -> None:
        """배치 대상 대기 행이 대기 표기와 보조 글자색을 쓰고 배지는 없는지 검증"""
        self.model.set_encode_book(self.book)
        self.assertEqual(self.model.data(self._index(0), _DISPLAY), "대기")
        self.assertIsNone(self.model.data(self._index(0), VideoTableModel.BADGE_ROLE))
        self.assertIsInstance(self.model.data(self._index(0), _FOREGROUND), QColor)
        self.assertEqual(self.model.data(self._index(0), _TOOLTIP), "재인코딩 순서를 기다리는 중입니다.")

    def test_state_change_updates_label_badge_and_tooltip(self) -> None:
        """상태가 실패로 바뀌면 표기, 배지, 툴팁 문장이 함께 바뀌는지 검증"""
        self.book.apply(self.path_a, EncodeState.FAILED, "인코딩 실패(종료 코드 1)")
        self.model.set_encode_book(self.book)
        self.assertEqual(self.model.data(self._index(0), _DISPLAY), "실패")
        self.assertIsNotNone(self.model.data(self._index(0), VideoTableModel.BADGE_ROLE))
        self.assertIsNone(self.model.data(self._index(0), _FOREGROUND))
        self.assertEqual(
            self.model.data(self._index(0), _TOOLTIP),
            "재인코딩하지 못했습니다. 원본은 그대로입니다.\n사유: 인코딩 실패(종료 코드 1)",
        )

    def test_row_outside_the_batch_has_no_label_and_keeps_the_row_tooltip(self) -> None:
        """배치 대상이 아닌 행은 상태가 비고, 툴팁은 행 전체 툴팁으로 남는지 검증"""
        self.model.set_encode_book(self.book)
        self.assertIsNone(self.model.data(self._index(1), _DISPLAY))
        self.assertIsNone(self.model.data(self._index(1), VideoTableModel.BADGE_ROLE))
        row_tip = self.model.data(self._index(1), _TOOLTIP)
        self.assertIsInstance(row_tip, str)
        self.assertNotIn("재인코딩을 기다리고 있습니다.", row_tip)

    def test_previous_discard_shows_its_label_and_tooltip_outside_the_batch(self) -> None:
        """이전 폐기 기록이 있는 영상은 배치가 없어도 이전 폐기 표기와 이유 툴팁을 보이고, 배치 상태가 있으면 그것을 우선함"""
        path_b = resolve_source_filepath(_ROOT, "b.mp4")
        self.model.set_previous_discard_provider(
            lambda _evaluations, _root: frozenset({path_b})
        )
        self.model.set_data_source(
            [make_eval(make_video(filename="a.mp4")), make_eval(make_video(filename="b.mp4"))],
            root_dir=_ROOT,
        )
        self.assertEqual(self.model.data(self._index(1), _DISPLAY), "이전 폐기")
        self.assertIsNotNone(self.model.data(self._index(1), VideoTableModel.BADGE_ROLE))
        self.assertIn("재인코딩 대상에서 뺐습니다", self.model.data(self._index(1), _TOOLTIP))
        book = EncodeBatchBook()
        book.start([path_b])
        book.apply(path_b, EncodeState.DONE, "")
        self.model.set_encode_book(book)
        self.assertEqual(self.model.data(self._index(1), _DISPLAY), "완료")

    def test_attaching_the_book_emits_a_change_for_the_state_column(self) -> None:
        """배치 저장소를 연결하면 상태 열 전체가 바뀌었다고 알리는지 검증"""
        changed: list[tuple[int, int]] = []
        self.model.dataChanged.connect(lambda top, bottom, _roles: changed.append((top.column(), bottom.row())))
        self.model.set_encode_book(self.book)
        self.assertEqual(changed, [(self.col, 1)])


class RowToneRoleTests(unittest.TestCase):
    """재인코딩 진행, 일시중지 행의 배경색과 왼쪽 띠 색 노출을 검증"""

    def setUp(self) -> None:
        self.model = VideoTableModel()
        self.model.set_data_source(
            [make_eval(make_video(filename="a.mp4")), make_eval(make_video(filename="b.mp4"))],
            root_dir=_ROOT,
        )
        self.col = VideoTableModel.COL_ENCODE_STATE
        self.path_a = resolve_source_filepath(_ROOT, "a.mp4")
        self.path_b = resolve_source_filepath(_ROOT, "b.mp4")
        self.book = EncodeBatchBook()
        self.book.start([self.path_a, self.path_b])

    def test_running_row_exposes_tint_and_accent(self) -> None:
        """진행 중 행이 에메랄드 배경색과 띠 색을 돌려주는지 검증"""
        self.book.apply(self.path_a, EncodeState.RUNNING, "")
        self.model.set_encode_book(self.book)
        palette = ThemeManager().current_palette
        index = self.model.index(0, self.col)
        background = self.model.data(index, int(Qt.ItemDataRole.BackgroundRole))
        accent = self.model.data(index, VideoTableModel.ACCENT_ROLE)
        self.assertIsInstance(background, QColor)
        self.assertIsInstance(accent, QColor)
        assert isinstance(background, QColor) and isinstance(accent, QColor)
        self.assertEqual(background.name().lower(), palette.row_running_bg.lower())
        self.assertEqual(accent.name().lower(), palette.row_running_accent.lower())

    def test_paused_row_uses_the_amber_tint(self) -> None:
        """일시중지 행이 호박색 배경색과 띠 색으로 바뀌는지 검증"""
        self.book.apply(self.path_a, EncodeState.RUNNING, "")
        self.book.set_item_paused(self.path_a, True)
        self.model.set_encode_book(self.book)
        palette = ThemeManager().current_palette
        index = self.model.index(0, self.col)
        self.assertEqual(self.model.data(index, _FOREGROUND), None)
        background = self.model.data(index, int(Qt.ItemDataRole.BackgroundRole))
        accent = self.model.data(index, VideoTableModel.ACCENT_ROLE)
        assert isinstance(background, QColor) and isinstance(accent, QColor)
        self.assertEqual(background.name().lower(), palette.row_paused_bg.lower())
        self.assertEqual(accent.name().lower(), palette.row_paused_accent.lower())

    def test_other_states_have_no_row_tint(self) -> None:
        """대기, 완료처럼 강조 대상이 아닌 상태는 배경색과 띠가 없는지 검증"""
        self.model.set_encode_book(self.book)
        for row, path, state in ((0, self.path_a, EncodeState.DONE), (1, self.path_b, EncodeState.PENDING)):
            self.book.apply(path, state, "")
            self.model.set_encode_book(self.book)
            index = self.model.index(row, self.col)
            self.assertIsNone(self.model.data(index, int(Qt.ItemDataRole.BackgroundRole)))
            self.assertIsNone(self.model.data(index, VideoTableModel.ACCENT_ROLE))

    def test_done_row_check_icon_uses_the_pass_badge_tint(self) -> None:
        """완료 행의 체크 아이콘이 통과 배지 글자색으로 그려지고 대기 행은 아이콘이 없는지 검증"""
        self.book.apply(self.path_a, EncodeState.DONE, "")
        self.model.set_encode_book(self.book)
        index = self.model.index(0, self.col)
        manager = ThemeManager()
        previous = manager.mode
        self.addCleanup(lambda: manager.set_mode(previous))
        for theme_mode, palette in (
            (ThemeManager.MODE_LIGHT, LIGHT_PALETTE),
            (ThemeManager.MODE_DARK, DARK_PALETTE),
        ):
            with self.subTest(theme=theme_mode):
                manager.set_mode(theme_mode)
                icon = self.model.data(index, int(Qt.ItemDataRole.DecorationRole))
                self.assertIsInstance(icon, QIcon)
                assert isinstance(icon, QIcon)
                self.assertFalse(icon.isNull())
                image = icon.pixmap(_ICON_PX, _ICON_PX).toImage()
                colors = {
                    image.pixelColor(x, y).name().lower()
                    for x in range(image.width())
                    for y in range(image.height())
                    if image.pixelColor(x, y).alpha() == 255
                }
                self.assertIn(palette.badge_pass[1].lower(), colors)
                self.assertNotIn(palette.text_primary.lower(), colors)
        manager.set_mode(previous)
        pending = self.model.index(1, self.col)
        self.assertIsNone(self.model.data(pending, int(Qt.ItemDataRole.DecorationRole)))


class FrameAnalysisColumnModelTests(unittest.TestCase):
    """화질 분석 세 열의 표시, 툴팁, 필터 값, 우클릭 요청 연결 검증"""

    def setUp(self) -> None:
        from src.core.analysis.frame_analysis import ActiveRegion
        from src.core.analysis.frame_analysis_runner import FrameAnalysisResult

        self.model = VideoTableModel()
        self.evals = [make_eval(make_video(filename="a.mp4")), make_eval(make_video(filename="b.mp4"))]
        self.model.set_data_source(self.evals, root_dir=_ROOT)
        self.result = FrameAnalysisResult(
            active_region=ActiveRegion(1920, 1080, 0, 0, False),
            metric_used="ssim",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier="720p",
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        self.handlers: list[tuple[str, object]] = []
        self.model.set_frame_action_handlers(
            apply_candidates=lambda evals: self.handlers.append(("apply", list(evals))),
            toggle_vr=lambda evals, protected: self.handlers.append(("vr", protected)),
            toggle_crop_exclusion=lambda evals, excluded: self.handlers.append(("crop", excluded)),
        )
        self.model.set_manual_vr_overrides(["a.mp4"])
        self.model.set_crop_exclusions(["b.mp4"])

    def test_columns_show_values_tooltips_and_filters(self) -> None:
        empty = self.model.index(0, VideoTableModel.COL_ACTIVE_FRAME)
        self.assertEqual(self.model.data(empty, _DISPLAY), "")
        self.model.set_frame_analysis_results({"a.mp4": self.result})
        index = self.model.index(0, VideoTableModel.COL_DOWNSCALE_CANDIDATE)
        self.assertEqual(self.model.data(index, _DISPLAY), "720p")
        self.assertIn("SSIM으로 대신", self.model.data(index, _TOOLTIP))
        self.assertEqual(self.model.data(self.model.index(0, VideoTableModel.COL_QUALITY_METRIC), _DISPLAY), "SSIM (대체)")
        self.assertEqual(self.model.get_filter_value_for(self.evals[0], VideoTableModel.COL_ACTIVE_FRAME), "전체")
        self.assertEqual(self.model.collect_column_value_counts(VideoTableModel.COL_DOWNSCALE_CANDIDATE), [("", 1), ("720p", 1)])
        self.model.set_sort_stack([(VideoTableModel.COL_DOWNSCALE_CANDIDATE, Qt.SortOrder.AscendingOrder)])
        self.assertEqual(self.model.get_sort_value(1, VideoTableModel.COL_DOWNSCALE_CANDIDATE), (3, 720))

    def test_manual_vr_and_context_actions_delegate(self) -> None:
        self.assertTrue(self.model.is_manual_vr("a.mp4"))
        self.assertTrue(self.model.is_crop_excluded("b.mp4"))
        self.assertFalse(self.model.is_crop_excluded("a.mp4"))
        self.assertEqual(self.model.frame_candidates([self.evals[0]]), {})
        self.model.apply_frame_analysis_candidates(self.evals)
        self.model.toggle_manual_vr(self.evals, True)
        self.model.toggle_crop_exclusion(self.evals, True)
        self.assertEqual(self.handlers, [("apply", self.evals), ("vr", True), ("crop", True)])


if __name__ == "__main__":
    unittest.main(verbosity=2)
