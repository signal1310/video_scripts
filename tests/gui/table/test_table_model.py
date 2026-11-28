"""표 모델의 인코딩 상태 열이 배치 상태 저장소의 표기, 배지, 글자색, 툴팁을 그대로 보이는지 검증"""

import os
import unittest
from dataclasses import replace
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QModelIndex, Qt
from PyQt6.QtGui import QColor, QFont, QIcon
from PyQt6.QtWidgets import QApplication

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.config import ClassifierConfig
from src.core.encode.encode_batch_book import EncodeBatchBook
from src.core.encode.encode_state import EncodeState
from src.core.models import ActiveFrame
from src.core.smart_action import SmartAction
from src.core.workspace_paths import resolve_source_filepath
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.gui.table.frame_analysis_columns import (
    ACTIVE_FRAME_HDR_TEXT,
    ACTIVE_FRAME_KEEP_FULL_TEXT,
    ACTIVE_FRAME_MANUAL_NAME,
    ACTIVE_FRAME_VR_TEXT,
    crop_review_tooltip,
)
from src.gui.table.table_cell_tooltips import ratio_cell_tip
from src.gui.table.table_model import VideoTableModel
from src.gui.theme import ThemeManager
from src.service.encode.encode_preview import PlannedCommand
from src.service.encode.encode_reasons import NAME_EXISTS
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_ROOT: str = os.path.join("work", "root")
_ICON_PX: int = 12
_DISPLAY = int(Qt.ItemDataRole.DisplayRole)
_TOOLTIP = int(Qt.ItemDataRole.ToolTipRole)
_FOREGROUND = int(Qt.ItemDataRole.ForegroundRole)
_FONT = int(Qt.ItemDataRole.FontRole)
_BACKGROUND = int(Qt.ItemDataRole.BackgroundRole)
_DECORATION = int(Qt.ItemDataRole.DecorationRole)
_ACCESSIBLE = int(Qt.ItemDataRole.AccessibleTextRole)


class EncodeStateColumnModelTests(unittest.TestCase):
    """인코딩 상태 열의 셀 값과 배치 상태 변경 신호를 검증"""

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

    def test_rows_show_target_before_a_batch_is_attached(self) -> None:
        """배치 저장소를 연결하기 전에는 인코딩 대상 행이 대상 표기와 보조 글자색을 쓰고, 보존 행은 비어 있는지 검증"""
        self.assertEqual(self.model.data(self._index(0), _DISPLAY), "대상")
        self.assertIsNone(self.model.data(self._index(0), VideoTableModel.BADGE_ROLE))
        self.assertIsInstance(self.model.data(self._index(0), _FOREGROUND), QColor)
        self.assertEqual(
            self.model.data(self._index(0), _TOOLTIP),
            "이번 인코딩 대상입니다. 인코딩을 시작하면 진행 상태를 보여 줍니다.",
        )
        self.assertEqual(
            self.model.get_filter_value_for(self.model.evaluations[0], self.col),
            "대상",
        )
        self.model.set_data_source(
            [
                make_eval(make_video(filename="a.mp4")),
                make_eval(make_video(filename="b.mp4"), smart_action=SmartAction.PASS_OPTIMAL),
            ],
            root_dir=_ROOT,
        )
        self.assertIsNone(self.model.data(self._index(1), _DISPLAY))
        self.assertEqual(
            self.model.get_filter_value_for(self.model.evaluations[1], self.col),
            "",
        )

    def test_batch_state_takes_precedence_over_the_target_label(self) -> None:
        """배치 저장소를 연결하면 대상 표기 대신 그 배치의 상태가 보이는지 검증"""
        self.assertEqual(self.model.data(self._index(0), _DISPLAY), "대상")
        self.model.set_encode_book(self.book)
        self.assertEqual(self.model.data(self._index(0), _DISPLAY), "대기")
        self.assertIsNone(self.model.data(self._index(1), _DISPLAY))

    def test_batch_target_shows_pending_label_with_muted_color_and_no_badge(self) -> None:
        """배치 대상 대기 행이 대기 표기와 보조 글자색을 쓰고 배지는 없는지 검증"""
        self.model.set_encode_book(self.book)
        self.assertEqual(self.model.data(self._index(0), _DISPLAY), "대기")
        self.assertIsNone(self.model.data(self._index(0), VideoTableModel.BADGE_ROLE))
        self.assertIsInstance(self.model.data(self._index(0), _FOREGROUND), QColor)
        self.assertEqual(self.model.data(self._index(0), _TOOLTIP), "인코딩 순서를 기다리는 중입니다.")

    def test_state_change_updates_label_badge_and_tooltip(self) -> None:
        """상태가 실패로 바뀌면 표기, 배지, 툴팁 문장이 함께 바뀌는지 검증"""
        self.book.apply(self.path_a, EncodeState.FAILED, "인코딩 실패(종료 코드 1)")
        self.model.set_encode_book(self.book)
        self.assertEqual(self.model.data(self._index(0), _DISPLAY), "실패")
        self.assertIsNotNone(self.model.data(self._index(0), VideoTableModel.BADGE_ROLE))
        self.assertIsNone(self.model.data(self._index(0), _FOREGROUND))
        self.assertEqual(
            self.model.data(self._index(0), _TOOLTIP),
            "인코딩하지 못했습니다. 원본은 그대로입니다.\n사유: 인코딩 실패(종료 코드 1)",
        )

    def test_row_outside_the_batch_has_no_label_and_keeps_the_row_tooltip(self) -> None:
        """배치 대상이 아닌 행은 상태가 비고, 툴팁은 행 전체 툴팁으로 남는지 검증"""
        self.model.set_encode_book(self.book)
        self.assertIsNone(self.model.data(self._index(1), _DISPLAY))
        self.assertIsNone(self.model.data(self._index(1), VideoTableModel.BADGE_ROLE))
        row_tip = self.model.data(self._index(1), _TOOLTIP)
        self.assertIsInstance(row_tip, str)
        self.assertNotIn("인코딩을 기다리고 있습니다.", row_tip)

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
        self.assertIn("인코딩 대상에서 제외했습니다", self.model.data(self._index(1), _TOOLTIP))
        book = EncodeBatchBook()
        book.start([path_b])
        book.apply(path_b, EncodeState.DONE, "")
        self.model.set_encode_book(book)
        self.assertEqual(self.model.data(self._index(1), _DISPLAY), "완료")

    def test_attaching_the_book_emits_a_change_for_the_whole_row(self) -> None:
        """배치 저장소를 연결하면 행 배경이 함께 바뀌도록 행 전체 열이 바뀌었다고 알리는지 검증"""
        changed: list[tuple[int, int, int, int]] = []
        self.model.dataChanged.connect(
            lambda top, bottom, _roles: changed.append((top.row(), top.column(), bottom.row(), bottom.column()))
        )
        self.model.set_encode_book(self.book)
        last_col = self.model.columnCount() - 1
        self.assertEqual(changed, [(0, 0, 1, last_col)])


class RowToneRoleTests(unittest.TestCase):
    """인코딩 진행, 일시중지 행의 배경색과 왼쪽 띠 색 노출을 검증"""

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
            set_manual_active_frame=lambda evals, frame: self.handlers.append(("manual", frame)),
        )
        self.model.set_manual_vr_overrides(["a.mp4"])
        self.model.set_crop_exclusions(["b.mp4"])

    def test_hdr_row_shows_pill_text_and_batch_reason_until_it_is_measured(self) -> None:
        """자동 배치가 건너뛴 HDR 행은 활성 화면 셀에 HDR 문구와 사유 툴팁을 쓰고, 측정 뒤에는 결과를 씀"""
        from src.gui.frame_analysis_skip import SkipKind, skip_reason_tooltip

        self.model.set_data_source(
            [
                make_eval(make_video(filename="hdr.mp4"), smart_action=SmartAction.PASS_HDR),
                self.evals[1],
            ],
            root_dir=_ROOT,
        )
        for col in (
            VideoTableModel.COL_ACTIVE_FRAME,
            VideoTableModel.COL_DOWNSCALE_CANDIDATE,
            VideoTableModel.COL_QUALITY_METRIC,
        ):
            self.assertEqual(
                self.model.data(self.model.index(0, col), _TOOLTIP),
                skip_reason_tooltip(SkipKind.HDR),
            )
        active = self.model.index(0, VideoTableModel.COL_ACTIVE_FRAME)
        self.assertEqual(self.model.data(active, _DISPLAY), ACTIVE_FRAME_HDR_TEXT)
        hdr_eval = self.model.get_evaluation_row(0)
        assert hdr_eval is not None
        self.assertEqual(
            self.model.get_filter_values_for(hdr_eval, VideoTableModel.COL_ACTIVE_FRAME),
            (ACTIVE_FRAME_HDR_TEXT,),
        )
        self.model.set_frame_analysis_results({"hdr.mp4": self.result})
        self.assertEqual(self.model.data(active, _DISPLAY), "-")

    def test_columns_show_values_tooltips_and_filters(self) -> None:
        empty = self.model.index(0, VideoTableModel.COL_ACTIVE_FRAME)
        self.assertEqual(self.model.data(empty, _DISPLAY), "")
        self.model.set_frame_analysis_results({"a.mp4": self.result})
        index = self.model.index(0, VideoTableModel.COL_DOWNSCALE_CANDIDATE)
        self.assertEqual(self.model.data(index, _DISPLAY), "720p")
        self.assertIn("SSIM으로 대신", self.model.data(index, _TOOLTIP))
        self.assertEqual(self.model.data(self.model.index(0, VideoTableModel.COL_QUALITY_METRIC), _DISPLAY), "SSIM (대체)")
        self.assertEqual(self.model.get_filter_value_for(self.evals[0], VideoTableModel.COL_ACTIVE_FRAME), "-")
        self.assertEqual(self.model.collect_column_value_counts(VideoTableModel.COL_DOWNSCALE_CANDIDATE), [("", 1), ("720p", 1)])
        self.model.set_sort_stack([(VideoTableModel.COL_DOWNSCALE_CANDIDATE, Qt.SortOrder.AscendingOrder)])
        self.assertEqual(self.model.get_sort_value(1, VideoTableModel.COL_DOWNSCALE_CANDIDATE), (3, 720))

    def test_cells_show_their_column_tooltip_and_name_cell_keeps_row_details(self) -> None:
        """이름 셀은 종합 상세 툴팁, 그 밖의 셀은 이 영상의 값 툴팁을 보여 주는지 검증"""
        ratio_index = self.model.index(0, VideoTableModel.COL_RATIO)
        self.assertEqual(self.model.data(ratio_index, _TOOLTIP), ratio_cell_tip(self.evals[0]))
        name_tip = self.model.data(self.model.index(0, VideoTableModel.COL_NAME), _TOOLTIP)
        self.assertIn("파일명:", name_tip)
        self.assertNotIn("파일명:", self.model.data(ratio_index, _TOOLTIP))

    def test_manual_vr_and_context_actions_delegate(self) -> None:
        self.assertTrue(self.model.is_manual_vr("a.mp4"))
        self.assertTrue(self.model.is_crop_excluded("b.mp4"))
        self.assertFalse(self.model.is_crop_excluded("a.mp4"))
        self.assertEqual(self.model.frame_candidates([self.evals[0]]), {})
        self.model.apply_frame_analysis_candidates(self.evals)
        self.model.toggle_manual_vr(self.evals, True)
        self.model.toggle_crop_exclusion(self.evals, True)
        manual = ActiveFrame(1280, 720, 0, 180)
        self.model.request_manual_active_frame(self.evals, manual)
        self.assertEqual(
            self.handlers,
            [("apply", self.evals), ("vr", True), ("crop", True), ("manual", manual)],
        )

    def test_crop_off_setting_adds_the_no_crop_line_to_the_active_frame_tooltip(self) -> None:
        """화면 여백 잘라내기 설정이 꺼져 있으면 활성 화면 툴팁이 자르지 않는다고 알리는지 검증"""
        from src.core.analysis.frame_analysis import ActiveRegion
        from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
        from src.core.config import ClassifierConfig

        cropped = FrameAnalysisResult(
            active_region=ActiveRegion(1920, 800, 0, 140, True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        self.model.set_frame_analysis_results({"a.mp4": cropped})
        index = self.model.index(0, VideoTableModel.COL_ACTIVE_FRAME)
        self.assertNotIn("화면 여백 잘라내기 설정이 꺼져 있어", self.model.data(index, _TOOLTIP))
        config = ClassifierConfig()
        config.encode.crop_black_bars = False
        self.model.set_config(config)
        self.assertIn("화면 여백 잘라내기 설정이 꺼져 있어", self.model.data(index, _TOOLTIP))

    def test_crop_off_setting_mutes_the_active_frame_cell(self) -> None:
        """잘라내기 설정이 꺼지면 표의 활성 화면 셀이 흐린 색으로 다시 그려지는지 검증"""
        from src.core.config import ClassifierConfig

        self.model.set_frame_analysis_results(
            {
                "a.mp4": FrameAnalysisResult(
                    active_region=ActiveRegion(1920, 800, 0, 140, True),
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(),
                    candidate_tier=None,
                    valid_segment_count=5,
                    total_segment_count=5,
                    unmeasurable=False,
                    is_vr_protected=False,
                )
            }
        )
        index = self.model.index(0, VideoTableModel.COL_ACTIVE_FRAME)
        self.assertIsNone(self.model.data(index, _FOREGROUND))
        config = ClassifierConfig()
        config.encode.crop_black_bars = False
        self.model.set_config(config)
        color = self.model.data(index, _FOREGROUND)
        self.assertIsInstance(color, QColor)
        self.assertEqual(color, QColor(ThemeManager().current_palette.fg_cell_inactive))
        font = self.model.data(index, _FONT)
        self.assertIsInstance(font, QFont)
        self.assertTrue(font.strikeOut())

    def test_manual_active_frames_redraw_the_active_frame_column(self) -> None:
        """사용자 지정 활성 화면이 표시 값에 반영되고 활성 화면 열이 다시 그려지는지 검증"""
        self.model.set_frame_analysis_results({"a.mp4": self.result})
        with mock.patch.object(self.model, "dataChanged") as changed:
            self.model.set_manual_active_frames({"a.mp4": ActiveFrame(1280, 720, 0, 180)})
        self.assertTrue(changed.emit.called)
        index = self.model.index(0, VideoTableModel.COL_ACTIVE_FRAME)
        self.assertEqual(self.model.data(index, _DISPLAY), "1280x720")
        self.assertIn(
            "사용자 지정 활성 화면입니다.", self.model.data(index, _TOOLTIP)
        )

    def test_manual_active_frames_also_redraw_the_name_column(self) -> None:
        """사용자 지정 활성 화면이 여백 확인 필요 경고를 없애므로 파일 이름 열도 다시 그리는지 검증"""
        flagged = FrameAnalysisResult(
            active_region=ActiveRegion(1836, 1008, 42, 36, True, dark_outliers_ignored=True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        self.model.set_frame_analysis_results({"a.mp4": flagged})
        self.model.set_review_flagged(["a.mp4"])
        name = self.model.index(0, VideoTableModel.COL_NAME)
        self.assertIsNotNone(self.model.data(name, _DECORATION))
        changed: list[tuple[int, int, int, int]] = []
        self.model.dataChanged.connect(
            lambda top, bottom, _roles: changed.append(
                (top.row(), top.column(), bottom.row(), bottom.column())
            )
        )
        self.model.set_manual_active_frames({"a.mp4": ActiveFrame(1280, 720, 0, 180)})
        self.assertIn(
            (0, VideoTableModel.COL_NAME, 1, VideoTableModel.COL_NAME), changed
        )
        self.assertIsNone(self.model.data(name, _DECORATION))

    def test_crop_setting_change_redraws_the_active_frame_column(self) -> None:
        """잘라내기 설정이 바뀌면 활성 화면 열의 문구와 정렬 캐시를 새로 만드는지 검증"""
        from src.core.config import ClassifierConfig

        barred = FrameAnalysisResult(
            active_region=ActiveRegion(734, 444, 593, 138, True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        self.model.set_frame_analysis_results({"b.mp4": barred})
        col = VideoTableModel.COL_ACTIVE_FRAME
        video = self.evals[1].video
        self.assertEqual(self.model.get_sort_value(1, col), (1, video.width * video.height))
        self.model.set_sort_stack([(col, Qt.SortOrder.DescendingOrder)])
        changed: list[tuple[int, int, int, int]] = []
        self.model.dataChanged.connect(
            lambda top, bottom, _roles: changed.append(
                (top.row(), top.column(), bottom.row(), bottom.column())
            )
        )
        config = ClassifierConfig()
        config.encode.crop_black_bars = False
        with mock.patch.object(self.model, "set_sort_stack") as re_sort:
            self.model.set_config(config)
        # - 활성 화면 열로 정렬 중이면 새 정렬 키로 다시 정렬
        re_sort.assert_called_once()
        self.assertIn((0, col, len(self.evals) - 1, col), changed)
        b_row = next(
            row
            for row in range(self.model.rowCount())
            if self.model.get_evaluation_row(row) is self.evals[1]
        )
        self.assertEqual(
            self.model.data(self.model.index(b_row, col), _DISPLAY), "734x444"
        )
        # - 감지 크기를 보여 주므로 정렬도 그 크기를 씀
        self.assertEqual(self.model.get_sort_value(b_row, col), (1, 734 * 444))

    def test_unrelated_config_change_does_not_redraw_rows(self) -> None:
        """잘라내기 설정이 그대로면 설정 변경이 전 행 다시 그리기를 내지 않는지 검증"""
        from src.core.config import ClassifierConfig

        changed: list[tuple[int, int, int, int]] = []
        self.model.dataChanged.connect(
            lambda top, bottom, _roles: changed.append(
                (top.row(), top.column(), bottom.row(), bottom.column())
            )
        )
        config = ClassifierConfig()
        config.thresholds.over_bitrate_ratio = 3.0
        self.model.set_config(config)
        self.assertEqual(changed, [])


    def test_review_flag_marks_name_and_active_frame_cells(self) -> None:
        """여백 확인 필요 영상만 두 셀에 경고 아이콘, 접근성 이름, 툴팁, 셀 배경을 받는지 검증"""
        reviewed = FrameAnalysisResult(
            active_region=ActiveRegion(1836, 1008, 42, 36, True, dark_outliers_ignored=True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        self.model.set_frame_analysis_results({"a.mp4": reviewed, "b.mp4": self.result})
        self.model.set_review_flagged(["a.mp4"])
        palette = ThemeManager().current_palette
        for col in (VideoTableModel.COL_NAME, VideoTableModel.COL_ACTIVE_FRAME):
            icon = self.model.data(self.model.index(0, col), _DECORATION)
            self.assertIsInstance(icon, QIcon, col)
            self.assertFalse(icon.isNull())
        active = self.model.index(0, VideoTableModel.COL_ACTIVE_FRAME)
        name = self.model.index(0, VideoTableModel.COL_NAME)
        name_tip = self.model.data(name, _TOOLTIP)
        active_tip = self.model.data(active, _TOOLTIP)
        # - 파일 이름 셀은 행 상세 툴팁 앞에 여백 확인 안내를 덧붙여 상세 정보를 잃지 않음
        self.assertTrue(name_tip.startswith(active_tip))
        self.assertIn("파일명: a.mp4", name_tip)
        self.assertEqual(active_tip, crop_review_tooltip(ActiveRegion(1836, 1008, 42, 36, True, dark_outliers_ignored=True)))
        self.assertIn("여백 확인 필요", active_tip)
        self.assertIn("1836x1008", active_tip)
        self.assertEqual(self.model.data(active, _ACCESSIBLE), "여백 확인 필요")
        self.assertIsNone(self.model.data(name, _ACCESSIBLE))
        self.assertEqual(self.model.data(active, _BACKGROUND), QColor(palette.badge_pending[0]))
        self.assertIsNone(self.model.data(self.model.index(1, VideoTableModel.COL_ACTIVE_FRAME), _DECORATION))
        self.assertIsNone(self.model.data(self.model.index(1, VideoTableModel.COL_NAME), _BACKGROUND))
        self.assertEqual(self.model.data(active, _DISPLAY), "1836x1008")

    def test_keep_full_row_shows_pill_icon_filter_and_full_frame_sort(self) -> None:
        """잘라내기 제외에 화면 여백가 있는 행이 전체 화면 유지 문구, 아이콘, 필터, 정렬을 쓰는지 검증"""
        barred = FrameAnalysisResult(
            active_region=ActiveRegion(734, 444, 593, 138, True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        self.model.set_frame_analysis_results({"b.mp4": barred})
        index = self.model.index(1, VideoTableModel.COL_ACTIVE_FRAME)
        self.assertEqual(self.model.data(index, _DISPLAY), ACTIVE_FRAME_KEEP_FULL_TEXT)
        self.assertIsInstance(self.model.data(index, _DECORATION), QIcon)
        self.assertEqual(
            self.model.data(index, _TOOLTIP),
            "이 영상은 잘라내지 않고 전체 화면을 유지합니다. "
            "자동으로 감지한 활성 화면(734x444)은 쓰지 않습니다.",
        )
        self.assertIsNone(self.model.data(index, _FOREGROUND))
        self.assertEqual(
            self.model.get_filter_values_for(self.evals[1], VideoTableModel.COL_ACTIVE_FRAME),
            (ACTIVE_FRAME_KEEP_FULL_TEXT,),
        )
        # - 정렬은 감지 크기(734x444)가 아니라 영상 전체 면적을 씀
        video = self.evals[1].video
        self.assertEqual(
            self.model.get_sort_value(1, VideoTableModel.COL_ACTIVE_FRAME),
            (1, video.width * video.height),
        )

    def test_keep_full_yields_to_manual_frame_vr_and_no_bars(self) -> None:
        """사용자 지정, VR 보호, 화면 여백 없음은 전체 화면 유지 문구 대신 각자의 표기를 씀"""
        barred = FrameAnalysisResult(
            active_region=ActiveRegion(734, 444, 593, 138, True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        self.model.set_frame_analysis_results({"a.mp4": barred, "b.mp4": self.result})
        vr_result = FrameAnalysisResult(
            active_region=ActiveRegion(734, 444, 593, 138, True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=True,
        )
        index = self.model.index(0, VideoTableModel.COL_ACTIVE_FRAME)
        self.model.set_manual_active_frames({"a.mp4": ActiveFrame(132, 240, 0, 0)})
        self.assertEqual(self.model.data(index, _DISPLAY), "132x240")
        self.assertEqual(self.model.data(index, _ACCESSIBLE), ACTIVE_FRAME_MANUAL_NAME)
        self.assertEqual(
            self.model.get_filter_values_for(self.evals[0], VideoTableModel.COL_ACTIVE_FRAME),
            ("132x240",),
        )
        self.model.set_manual_active_frames({})
        self.model.set_frame_analysis_results({"a.mp4": vr_result, "b.mp4": self.result})
        self.assertEqual(self.model.data(index, _DISPLAY), ACTIVE_FRAME_VR_TEXT)
        # - 화면 여백가 없는 행은 제외로 지정해도 전체 화면 유지 알약이 아니라 `-` 그대로
        self.assertEqual(
            self.model.data(self.model.index(1, VideoTableModel.COL_ACTIVE_FRAME), _DISPLAY),
            "-",
        )

    def test_keep_full_returns_to_muted_size_when_crop_setting_is_off(self) -> None:
        """화면 여백 잘라내기 설정을 끄면 전체 화면 유지 대신 크기와 흐린 글자색을 씀"""
        from src.core.config import ClassifierConfig

        barred = FrameAnalysisResult(
            active_region=ActiveRegion(734, 444, 593, 138, True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        self.model.set_frame_analysis_results({"b.mp4": barred})
        index = self.model.index(1, VideoTableModel.COL_ACTIVE_FRAME)
        config = ClassifierConfig()
        config.encode.crop_black_bars = False
        self.model.set_config(config)
        self.assertEqual(self.model.data(index, _DISPLAY), "734x444")
        self.assertEqual(
            self.model.data(index, _FOREGROUND), QColor(ThemeManager().current_palette.fg_cell_inactive)
        )
        self.assertIsNone(self.model.data(index, _DECORATION))
        self.assertEqual(
            self.model.get_filter_values_for(self.evals[1], VideoTableModel.COL_ACTIVE_FRAME),
            ("734x444",),
        )

    def test_review_flag_clears_when_the_service_drops_it(self) -> None:
        """서비스가 확인한 영상을 표시 집합에서 빼면 두 셀의 경고가 모두 사라지는지 검증"""
        flagged = FrameAnalysisResult(
            active_region=ActiveRegion(1836, 1008, 42, 36, True, dark_outliers_ignored=True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        self.model.set_frame_analysis_results({"a.mp4": flagged})
        self.assertIsNone(
            self.model.data(self.model.index(0, VideoTableModel.COL_ACTIVE_FRAME), _DECORATION)
        )
        self.model.set_review_flagged(["a.mp4"])
        self.model.set_manual_active_frames({"a.mp4": ActiveFrame(1280, 720, 0, 180)})
        self.model.set_review_flagged([])
        for col in (VideoTableModel.COL_NAME,):
            self.assertIsNone(self.model.data(self.model.index(0, col), _DECORATION), col)
        # - 남은 사용자 지정 활성 화면은 연필 아이콘이 붙고 경고 아이콘은 사라짐
        active_icon = self.model.data(
            self.model.index(0, VideoTableModel.COL_ACTIVE_FRAME), _DECORATION
        )
        self.assertIsInstance(active_icon, QIcon)
        self.assertEqual(
            self.model.data(self.model.index(0, VideoTableModel.COL_ACTIVE_FRAME), _ACCESSIBLE),
            ACTIVE_FRAME_MANUAL_NAME,
        )
        self.assertEqual(self.model.review_flagged_count, 0)

    def test_review_filter_value_keeps_the_size_and_adds_the_name(self) -> None:
        """활성 화면 열 필터 값이 크기와 여백 확인 필요를 함께 가지는지 검증"""
        flagged = FrameAnalysisResult(
            active_region=ActiveRegion(1836, 1008, 42, 36, True, dark_outliers_ignored=True),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=None,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )
        self.model.set_frame_analysis_results({"a.mp4": flagged, "b.mp4": self.result})
        self.model.set_review_flagged(["a.mp4"])
        values = self.model.get_filter_values_for(self.evals[0], VideoTableModel.COL_ACTIVE_FRAME)
        self.assertEqual(values, ("1836x1008", "여백 확인 필요"))
        self.assertEqual(
            self.model.get_filter_values_for(self.evals[1], VideoTableModel.COL_ACTIVE_FRAME),
            ("-",),
        )
        counts = dict(self.model.collect_column_value_counts(VideoTableModel.COL_ACTIVE_FRAME))
        self.assertEqual(counts["1836x1008"], 1)
        self.assertEqual(counts["여백 확인 필요"], 1)
        self.assertEqual(counts["-"], 1)


class EncodeLabelCacheTests(unittest.TestCase):
    """인코딩 상태 표기 캐시가 역할별 결과를 바꾸지 않고 원본 경로 조회를 행마다 한 번으로 줄이는지 검증"""

    _ROLES = (
        int(Qt.ItemDataRole.DisplayRole),
        int(Qt.ItemDataRole.BackgroundRole),
        VideoTableModel.ACCENT_ROLE,
        int(Qt.ItemDataRole.ForegroundRole),
        VideoTableModel.BADGE_ROLE,
        int(Qt.ItemDataRole.DecorationRole),
    )
    _ROW_COUNT = 12
    _COL_COUNT = 20

    def setUp(self) -> None:
        self.model = VideoTableModel()
        self.paths = [resolve_source_filepath(_ROOT, f"v{i}.mp4") for i in range(self._ROW_COUNT)]
        self.model.set_data_source(
            [make_eval(make_video(filename=f"v{i}.mp4")) for i in range(self._ROW_COUNT)],
            root_dir=_ROOT,
        )

    def _role_results(self) -> list[list[object]]:
        rows: list[list[object]] = []
        for row in range(self.model.rowCount()):
            for col in range(self._COL_COUNT):
                index = self.model.index(row, col)
                rows.append([self.model.data(index, role) for role in self._ROLES])
        return rows

    def _book_with_states(self) -> EncodeBatchBook:
        book = EncodeBatchBook()
        book.start(self.paths)
        book.apply(self.paths[1], EncodeState.RUNNING, "")
        book.apply(self.paths[2], EncodeState.DONE, "")
        book.apply(self.paths[3], EncodeState.RUNNING, "")
        book.set_item_paused(self.paths[3], True)
        return book

    def test_role_results_match_without_the_cache(self) -> None:
        """모든 인코딩 상태에서 캐시 적용 전후의 역할별 결과가 같은지 검증"""
        self.book = self._book_with_states()
        self.model.set_previous_discard_provider(lambda _evals, _root: frozenset({self.paths[4]}))
        self.model.set_data_source(
            [make_eval(make_video(filename=f"v{i}.mp4")) for i in range(self._ROW_COUNT)],
            root_dir=_ROOT,
        )
        self.model.set_encode_book(self.book)
        cached = self._role_results()
        self.model._encode_label_cache.clear()
        self.assertEqual(self._role_results(), cached)

    def test_full_background_pass_resolves_each_row_once(self) -> None:
        """전체 배경색 통과가 행마다 원본 경로를 한 번만 조회하는지 검증"""
        self.model.set_encode_book(self._book_with_states())
        calls = 0

        def counting(root: str, filename: str) -> str:
            nonlocal calls
            calls += 1
            return resolve_source_filepath(root, filename)

        with mock.patch("src.gui.table.table_model.resolve_source_filepath", counting):
            for row in range(self.model.rowCount()):
                for col in range(self._COL_COUNT):
                    self.model.data(
                        self.model.index(row, col), int(Qt.ItemDataRole.BackgroundRole)
                    )
        self.assertLessEqual(calls, self._ROW_COUNT)

    def test_new_book_invalidates_the_labels(self) -> None:
        """배치 저장소를 바꾸면 캐시를 비워 새 상태 표기를 보이는지 검증"""
        first = EncodeBatchBook()
        first.start([self.paths[5]])
        self.model.set_encode_book(first)
        index = self.model.index(5, VideoTableModel.COL_ENCODE_STATE)
        self.assertEqual(self.model.data(index, _DISPLAY), "대기")
        second = EncodeBatchBook()
        second.start([self.paths[5]])
        second.apply(self.paths[5], EncodeState.DONE, "")
        self.model.set_encode_book(second)
        self.assertEqual(self.model.data(index, _DISPLAY), "완료")


class FrameAnalysisPartialUpdateTests(unittest.TestCase):
    """화질 분석 부분 갱신이 영향 행만 알리고 다른 열의 정렬 키를 유지하는지 검증"""

    _ROW_COUNT = 100

    def setUp(self) -> None:
        self._result_cls = FrameAnalysisResult
        self._region_cls = ActiveRegion
        self.model = VideoTableModel()
        self.evals = [
            make_eval(make_video(filename=f"v{i}.mp4")) for i in range(self._ROW_COUNT)
        ]
        self.model.set_data_source(self.evals, root_dir=_ROOT)
        self.changed: list[tuple[int, int, int, int]] = []
        self.model.dataChanged.connect(
            lambda top, bottom, _roles: self.changed.append(
                (top.row(), top.column(), bottom.row(), bottom.column())
            )
        )

    def _result(self, candidate: str) -> "FrameAnalysisResult":
        return self._result_cls(
            active_region=self._region_cls(1920, 1080, 0, 0, False),
            metric_used="vmaf",
            vmaf_model="vmaf_v0.6.1",
            tier_scores=(),
            candidate_tier=candidate,
            valid_segment_count=5,
            total_segment_count=5,
            unmeasurable=False,
            is_vr_protected=False,
        )

    def test_only_affected_rows_are_announced(self) -> None:
        """전체 100행 중 3행만 갱신하면 그 행들만 dataChanged를 내는지 검증"""
        other_index = self.model.index(7, VideoTableModel.COL_DOWNSCALE_CANDIDATE)
        before = self.model.data(other_index, _DISPLAY)
        self.model.update_frame_analysis_results(
            {
                "v1.mp4": self._result("720p"),
                "v2.mp4": self._result("1080p"),
                "v40.mp4": self._result("480p"),
            }
        )
        self.assertEqual(
            self.changed,
            [
                (1, VideoTableModel.COL_ACTIVE_FRAME, 2, VideoTableModel.COL_QUALITY_METRIC),
                (40, VideoTableModel.COL_ACTIVE_FRAME, 40, VideoTableModel.COL_QUALITY_METRIC),
            ],
        )
        self.assertEqual(self.model.data(other_index, _DISPLAY), before)
        self.assertEqual(
            self.model.data(
                self.model.index(40, VideoTableModel.COL_DOWNSCALE_CANDIDATE), _DISPLAY
            ),
            "480p",
        )

    def test_frame_sort_is_reapplied_on_each_partial_update_and_at_completion(self) -> None:
        """화질 분석 열로 정렬 중이면 부분 갱신마다, 완료 때 다시 정렬하고 다른 열 정렬 키는 보존하는지 검증"""
        frame_col = VideoTableModel.COL_DOWNSCALE_CANDIDATE
        self.model.set_sort_stack([(frame_col, Qt.SortOrder.AscendingOrder)])
        name_col = VideoTableModel.COL_NAME
        self.model._rank_for(name_col)
        name_rank = self.model._rank_by_col[name_col]
        with mock.patch.object(self.model, "set_sort_stack") as re_sort:
            for row in range(5):
                self.model.update_frame_analysis_results(
                    {f"v{row}.mp4": self._result("720p")}
                )
            self.assertEqual(re_sort.call_count, 5)
            self.assertIs(self.model._rank_by_col[name_col], name_rank)
            self.model.set_frame_analysis_results(
                {f"v{row}.mp4": self._result("720p") for row in range(5)}
            )
            self.assertEqual(re_sort.call_count, 6)

    def test_partial_updates_keep_the_sorted_order_true(self) -> None:
        """정렬 중 도착한 결과가 있어도 표시 순서가 값 기준 정렬과 일치하는지 검증"""
        self.model.set_sort_stack([(VideoTableModel.COL_DOWNSCALE_CANDIDATE, Qt.SortOrder.AscendingOrder)])
        self.model.update_frame_analysis_results({"v3.mp4": self._result("1080p"), "v1.mp4": self._result("480p")})
        keys = [self.model.get_sort_value(row, VideoTableModel.COL_DOWNSCALE_CANDIDATE) for row in range(self.model.rowCount())]
        resolved = [key for key in keys if key[0] == 3]
        self.assertEqual(resolved, sorted(resolved))
        self.assertEqual(keys, sorted(keys))

class FfmpegArgsTooltipTests(unittest.TestCase):
    """권장 처리와 출력 해상도 셀 툴팁 끝에 예정 FFmpeg 인자가 붙는지 검증"""

    def setUp(self) -> None:
        self.model = VideoTableModel()
        self.model.set_data_source([make_eval(make_video(filename="a.mp4"))], root_dir=_ROOT)

    def _tip(self, col: int) -> str:
        return str(self.model.data(self.model.index(0, col), _TOOLTIP) or "")

    def test_action_cell_tip_ends_with_ffmpeg_args(self) -> None:
        """권장 처리 셀은 기존 문구 뒤에 FFmpeg 인자 줄을 붙임"""
        tip = self._tip(VideoTableModel.COL_ACTION)
        self.assertTrue(tip.startswith("품질 "))
        self.assertIn("\nFFmpeg 인자: ", tip)

    def test_output_res_cell_tip_ends_with_ffmpeg_args(self) -> None:
        """출력 해상도 셀도 FFmpeg 인자 줄을 붙임"""
        tip = self._tip(VideoTableModel.COL_OUTPUT_RES)
        self.assertIn("\nFFmpeg 인자: ", tip)

    def test_other_cells_are_unchanged(self) -> None:
        """다른 셀 툴팁에는 FFmpeg 인자 줄을 붙이지 않음"""
        self.assertNotIn("FFmpeg 인자", self._tip(VideoTableModel.COL_CODEC))

    def test_no_ffmpeg_line_when_command_cannot_be_built(self) -> None:
        """명령을 만들 수 없으면 기존 문구만 남김"""
        with mock.patch(
            "src.gui.table.table_model.planned_command_for_evaluation",
            return_value=None,
        ):
            tip = self._tip(VideoTableModel.COL_ACTION)
        self.assertNotIn("FFmpeg 인자", tip)
        self.assertTrue(tip)

    def test_ffmpeg_args_column_shows_summary_and_full_command_tip(self) -> None:
        """인자 열은 요약을 표시하고 셀 툴팁에 붙여 넣을 전체 명령줄을 보여 줌"""
        display = self._tip(VideoTableModel.COL_FFMPEG_ARGS)
        self.assertTrue(display.startswith("ffmpeg "))
        self.assertIn(" -c:v:", display)
        summary = str(self.model.data(self.model.index(0, VideoTableModel.COL_FFMPEG_ARGS)))
        self.assertNotEqual(summary, display)
        self.assertIn("cq", summary)

    def test_preserved_row_has_no_command_to_copy(self) -> None:
        """원본 보존 행은 예정 명령이 없어 인자 열이 비고 툴팁에 명령줄이 없음"""
        preserved = make_eval(
            make_video(filename="keep.mp4"), smart_action=SmartAction.PASS_OPTIMAL
        )
        self.model.set_data_source([preserved], root_dir=_ROOT)

        display = self.model.data(self.model.index(0, VideoTableModel.COL_FFMPEG_ARGS))
        self.assertIn(display, (None, ""))
        self.assertIsNone(self.model.planned_command_line(preserved))
        self.assertNotIn("ffmpeg", self._tip(VideoTableModel.COL_FFMPEG_ARGS))
        self.assertNotIn("FFmpeg 인자", self._tip(VideoTableModel.COL_ACTION))

    def test_encode_book_change_clears_the_planned_command_cache(self) -> None:
        """배치 저장소가 바뀌면 파일 시스템 판정이 낡지 않도록 예정 명령 캐시를 비움"""
        ev = make_eval(make_video(filename="a.mp4"))
        with mock.patch(
            "src.gui.table.table_model.planned_command_for_evaluation",
            return_value=PlannedCommand(
                command_line="", summary="", approximate=False, skip_reason=NAME_EXISTS
            ),
        ):
            self.assertIsNone(self.model.planned_command_line(ev))
        self.model.set_encode_book(EncodeBatchBook())
        with mock.patch(
            "src.gui.table.table_model.planned_command_for_evaluation",
            return_value=PlannedCommand(
                command_line="ffmpeg -i a.mp4", summary="hevc_nvenc cq 22.0", approximate=False
            ),
        ) as planned:
            self.assertEqual(self.model.planned_command_line(ev), "ffmpeg -i a.mp4")
        planned.assert_called_once()

    def test_skipped_command_shows_reason_instead_of_a_command(self) -> None:
        """결과 경로를 쓸 수 없어 명령을 만들지 않으면 인자 열과 툴팁이 사유를 보여 주고 복사는 비움"""
        ev = make_eval(make_video(filename="a.mp4"))
        with mock.patch(
            "src.gui.table.table_model.planned_command_for_evaluation",
            return_value=PlannedCommand(
                command_line="", summary="", approximate=False, skip_reason=NAME_EXISTS
            ),
        ):
            display = self.model.data(self.model.index(0, VideoTableModel.COL_FFMPEG_ARGS))
            tip = self._tip(VideoTableModel.COL_FFMPEG_ARGS)
            action_tip = self._tip(VideoTableModel.COL_ACTION)
            self.assertIsNone(self.model.planned_command_line(ev))
        self.assertEqual(display, NAME_EXISTS)
        self.assertEqual(tip, NAME_EXISTS)
        self.assertNotIn("FFmpeg 인자", action_tip)

class DataSourceUpdateTests(unittest.TestCase):
    """같은 파일 목록을 다시 적재할 때 행 초기화 없이 값만 바꾸는지 검증"""

    def setUp(self) -> None:
        self.model = VideoTableModel()
        self.videos = [make_video(filename=f"{index:02d}.mp4") for index in range(4)]
        self.rows = [make_eval(video) for video in self.videos]
        self.model.set_data_source(self.rows, root_dir=_ROOT)
        self.events: list[str] = []
        self.model.modelAboutToBeReset.connect(lambda: self.events.append("reset"))
        self.model.layoutAboutToBeChanged.connect(lambda: self.events.append("layout"))
        self.model.dataChanged.connect(lambda *_args: self.events.append("data"))

    def test_same_file_list_updates_values_without_resetting_rows(self) -> None:
        """같은 파일 목록이면 행 초기화 없이 값 변경 신호만 내는지 검증"""
        replaced = [
            make_eval(video, smart_action=SmartAction.PASS_OPTIMAL) for video in self.videos
        ]
        self.model.set_data_source(replaced, root_dir=_ROOT)
        self.assertNotIn("reset", self.events)
        self.assertIn("data", self.events)
        self.assertEqual(
            [ev.smart_action for ev in self.model.evaluations],
            [SmartAction.PASS_OPTIMAL] * 4,
        )
        self.assertIsNone(
            self.model.data(self.model.index(0, VideoTableModel.COL_ENCODE_STATE), _DISPLAY)
        )

    def test_reordered_base_list_resets_rows(self) -> None:
        """적재 순서 자체가 바뀌면 종전처럼 행을 다시 만드는지 검증"""
        replaced = [make_eval(video) for video in reversed(self.videos)]
        self.model.set_data_source(replaced, root_dir=_ROOT)
        self.assertIn("reset", self.events)

    def test_in_place_update_notifies_data_source_changed(self) -> None:
        """제자리 갱신은 행 초기화가 하던 열 폭 재측정을 위해 변경 신호를 내는지 검증"""
        notices: list[bool] = []
        self.model.data_source_changed.connect(lambda: notices.append(True))
        self.model.set_data_source(
            [make_eval(video) for video in self.videos], root_dir=_ROOT
        )
        self.assertEqual(notices, [True])
        self.assertFalse(self.model.name_list_changed)

    def test_new_values_are_visible_through_the_model(self) -> None:
        """제자리 갱신 뒤 셀 값과 검색 대상 문자열이 새 평가 결과를 따르는지 검증"""
        replaced = [
            make_eval(video, smart_action=SmartAction.PASS_OPTIMAL) for video in self.videos
        ]
        self.model.set_data_source(replaced, root_dir=_ROOT)
        self.assertNotIn("할 일", self.model.get_search_blob(0))
        self.assertEqual(
            self.model.data(self.model.index(0, VideoTableModel.COL_ACTION), _DISPLAY),
            SmartAction.PASS_OPTIMAL.action_label,
        )

    def test_changed_sort_values_reorder_through_a_layout_change(self) -> None:
        """정렬 값이 바뀌면 행 초기화 대신 레이아웃 변경으로 순서를 바꾸는지 검증"""
        self.model.set_sort_stack([(VideoTableModel.COL_BITRATE, Qt.SortOrder.AscendingOrder)])
        self.events.clear()
        bumped = [
            make_eval(make_video(**{**video.to_dict(), "vid_kbps": 9000 - index * 100}))
            for index, video in enumerate(self.videos)
        ]
        self.model.set_data_source(bumped, root_dir=_ROOT)
        self.assertNotIn("reset", self.events)
        self.assertIn("layout", self.events)
        self.assertEqual(
            [ev.video.vid_kbps for ev in self.model.evaluations],
            [8700, 8800, 8900, 9000],
        )

    def test_different_file_list_and_other_folder_still_reset(self) -> None:
        """파일 목록이 다르거나 작업 폴더가 바뀌면 종전처럼 행을 다시 만드는지 검증"""
        self.model.set_data_source(self.rows[:2], root_dir=_ROOT)
        self.assertIn("reset", self.events)
        self.events.clear()
        self.model.set_data_source(self.rows, root_dir="other")
        self.assertIn("reset", self.events)

    def test_set_config_alone_does_not_rebuild_rows(self) -> None:
        """설정 변경만 있을 때는 행을 다시 만들지 않는지 검증"""
        base = ClassifierConfig()
        self.model.set_config(replace(base, thresholds=replace(base.thresholds, over_bitrate_ratio=3.0)))
        self.assertEqual(self.events, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)


