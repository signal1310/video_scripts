"""활성 화면 직접 편집의 좌표 변환, 손잡이 판정, 끌기, 되돌리기 스택 검증"""

import unittest

from PyQt6.QtCore import QPoint, QRect, Qt

from src.core.analysis.frame_analysis import MANUAL_FRAME_MIN_SIZE_PX
from src.core.models import ActiveFrame
from src.gui.preview.crop_edit import (
    HANDLE_EDGE_PX,
    HANDLE_MOVE,
    MAX_MANUAL_UNDO_STEPS,
    FrameHistory,
    content_rect,
    cursor_for,
    drag_frame,
    full_active_frame,
    hit_test,
    kept_label,
    to_source,
)


class ContentRectTests(unittest.TestCase):
    """영상 내용 영역 계산"""

    def test_matching_aspect_fills_the_view(self) -> None:
        self.assertEqual(content_rect((1920, 1080), (1920, 1080)), QRect(0, 0, 1920, 1080))

    def test_pillarbox_centers_the_content_horizontally(self) -> None:
        self.assertEqual(content_rect((1000, 1000), (1000, 500)), QRect(0, 250, 1000, 500))

    def test_letterbox_centers_the_content_vertically(self) -> None:
        rect = content_rect((1000, 1000), (1920, 1080))
        self.assertEqual(rect.width(), 1000)
        self.assertEqual(rect.height(), 562)
        self.assertEqual(rect.top(), 219)

    def test_zero_sizes_do_not_divide_by_zero(self) -> None:
        rect = content_rect((0, 0), (0, 0))
        self.assertEqual(rect.width(), 1)
        self.assertEqual(rect.height(), 1)


class ToSourceTests(unittest.TestCase):
    """위젯 좌표를 원본 좌표로 옮기는 변환"""

    def test_identity_when_the_content_matches_the_video(self) -> None:
        self.assertEqual(to_source(QRect(0, 0, 100, 100), (100, 100), QPoint(25, 40)), (25.0, 40.0))

    def test_offset_content_moves_the_origin(self) -> None:
        self.assertEqual(to_source(QRect(10, 20, 100, 100), (200, 200), QPoint(10, 20)), (0.0, 0.0))

    def test_scales_inside_a_letterboxed_view(self) -> None:
        x, y = to_source(QRect(0, 219, 1000, 562), (1920, 1080), QPoint(500, 219 + 281))
        self.assertAlmostEqual(x, 960.0, places=3)
        self.assertAlmostEqual(y, 540.0, places=1)

    def test_zero_size_content_returns_the_origin(self) -> None:
        self.assertEqual(to_source(QRect(0, 0, 0, 10), (1920, 1080), QPoint(5, 5)), (0.0, 0.0))


class HitTestTests(unittest.TestCase):
    """손잡이 판정 우선순위와 경계"""

    def test_corner_beats_edge_on_the_same_point(self) -> None:
        self.assertEqual(hit_test(QRect(10, 10, 100, 80), QPoint(10, 10)), "top-left")

    def test_each_corner_maps_to_its_handle(self) -> None:
        kept = QRect(10, 10, 100, 80)
        self.assertEqual(hit_test(kept, QPoint(110, 10)), "top-right")
        self.assertEqual(hit_test(kept, QPoint(10, 90)), "bottom-left")
        self.assertEqual(hit_test(kept, QPoint(110, 90)), "bottom-right")

    def test_edges_in_the_middle_of_a_side(self) -> None:
        kept = QRect(10, 10, 100, 80)
        self.assertEqual(hit_test(kept, QPoint(10, 50)), "left")
        self.assertEqual(hit_test(kept, QPoint(110, 50)), "right")
        self.assertEqual(hit_test(kept, QPoint(60, 10)), "top")
        self.assertEqual(hit_test(kept, QPoint(60, 90)), "bottom")

    def test_inside_the_rectangle_moves_it(self) -> None:
        self.assertEqual(hit_test(QRect(10, 10, 100, 80), QPoint(60, 50)), HANDLE_MOVE)

    def test_outside_the_tolerance_is_nothing(self) -> None:
        kept = QRect(10, 10, 100, 80)
        self.assertIsNone(hit_test(kept, QPoint(10 - HANDLE_EDGE_PX - 1, 50)))
        self.assertIsNone(hit_test(kept, QPoint(60, 200)))

    def test_tolerance_is_inclusive(self) -> None:
        kept = QRect(10, 10, 100, 80)
        self.assertEqual(hit_test(kept, QPoint(10 + HANDLE_EDGE_PX, 50)), "left")

    def test_zero_size_rectangle_has_no_handles(self) -> None:
        self.assertIsNone(hit_test(QRect(10, 10, 0, 0), QPoint(10, 10)))



class DragFrameTests(unittest.TestCase):
    """끌기 결과의 경계, 최소 크기, 짝수 정렬"""

    def setUp(self) -> None:
        self.content = QRect(0, 0, 1920, 1080)

    def _drag(
        self,
        handle: str,
        start: ActiveFrame,
        delta: tuple[int, int],
        content: QRect | None = None,
    ) -> ActiveFrame:
        area = self.content if content is None else content
        return drag_frame(
            handle,  # pyright: ignore[reportArgumentType]
            start,
            QPoint(0, 0),
            QPoint(delta[0], delta[1]),
            area,
            (1920, 1080),
        )

    def test_moving_translates_without_resizing(self) -> None:
        moved = self._drag(HANDLE_MOVE, ActiveFrame(800, 600, 100, 100), (50, 40))
        self.assertEqual(moved, ActiveFrame(800, 600, 150, 140))

    def test_moving_is_clamped_inside_the_frame(self) -> None:
        moved = self._drag(HANDLE_MOVE, ActiveFrame(800, 600, 100, 100), (-500, -500))
        self.assertEqual((moved.x, moved.y), (0, 0))
        moved = self._drag(HANDLE_MOVE, ActiveFrame(800, 600, 100, 100), (5000, 5000))
        self.assertEqual((moved.x + moved.width, moved.y + moved.height), (1920, 1080))

    def test_right_edge_changes_only_the_width(self) -> None:
        self.assertEqual(self._drag("right", ActiveFrame(800, 600, 100, 100), (-100, 40)), ActiveFrame(700, 600, 100, 100))

    def test_left_edge_moves_the_origin_and_width(self) -> None:
        self.assertEqual(self._drag("left", ActiveFrame(800, 600, 100, 100), (100, 0)), ActiveFrame(700, 600, 200, 100))

    def test_corner_changes_two_sides(self) -> None:
        self.assertEqual(self._drag("top-left", ActiveFrame(800, 600, 100, 100), (100, 100)), ActiveFrame(700, 500, 200, 200))

    def test_minimum_size_is_respected_on_both_sides(self) -> None:
        self.assertEqual(self._drag("right", ActiveFrame(800, 600, 100, 100), (-5000, 0)).width, MANUAL_FRAME_MIN_SIZE_PX)
        self.assertEqual(self._drag("left", ActiveFrame(800, 600, 100, 100), (5000, 0)).width, MANUAL_FRAME_MIN_SIZE_PX)
        self.assertEqual(self._drag("bottom", ActiveFrame(800, 600, 100, 100), (0, -5000)).height, MANUAL_FRAME_MIN_SIZE_PX)

    def test_resizing_cannot_leave_the_frame(self) -> None:
        grown = self._drag("right", ActiveFrame(800, 600, 100, 100), (5000, 0))
        self.assertEqual(grown.x + grown.width, 1920)
        self.assertEqual(self._drag("top", ActiveFrame(800, 600, 100, 100), (0, -5000)).y, 0)

    def test_result_is_even_aligned(self) -> None:
        resized = self._drag("right", ActiveFrame(800, 600, 100, 100), (101, 0))
        self.assertEqual(resized.width % 2, 0)
        self.assertEqual(resized.x % 2, 0)

    def test_aspect_ratio_is_not_locked(self) -> None:
        resized = self._drag("right", ActiveFrame(800, 600, 100, 100), (400, 0))
        self.assertNotEqual(resized.width, resized.height)

    def test_letterbox_offset_keeps_a_zero_delta_unchanged(self) -> None:
        letterboxed = QRect(0, 219, 1000, 562)
        self.assertEqual(
            self._drag(HANDLE_MOVE, ActiveFrame(800, 600, 100, 100), (0, 0), letterboxed),
            ActiveFrame(800, 600, 100, 100),
        )

    def test_zero_video_size_returns_the_start(self) -> None:
        start = ActiveFrame(10, 10, 0, 0)
        result = drag_frame("right", start, QPoint(0, 0), QPoint(5, 5), self.content, (0, 0))
        self.assertEqual(result, start)


class HelperTests(unittest.TestCase):
    """커서, 표기, 전체 프레임 값"""

    def test_cursor_shapes_follow_the_handle(self) -> None:
        self.assertEqual(cursor_for(HANDLE_MOVE), Qt.CursorShape.SizeAllCursor)
        self.assertEqual(cursor_for("left"), Qt.CursorShape.SizeHorCursor)
        self.assertEqual(cursor_for("top"), Qt.CursorShape.SizeVerCursor)
        self.assertEqual(cursor_for("top-left"), Qt.CursorShape.SizeFDiagCursor)
        self.assertEqual(cursor_for("top-right"), Qt.CursorShape.SizeBDiagCursor)
        self.assertEqual(cursor_for(None), Qt.CursorShape.ArrowCursor)

    def test_label_shows_size_and_offsets(self) -> None:
        self.assertEqual(kept_label(ActiveFrame(100, 80, 10, 20)), "100 x 80, 왼쪽 10, 위 20")

    def test_full_frame_uses_even_dimensions(self) -> None:
        self.assertEqual(full_active_frame((1921, 1081)), ActiveFrame(1920, 1080, 0, 0))


class FrameHistoryTests(unittest.TestCase):
    """창 세션 되돌리기 스택"""

    def test_pop_returns_the_last_pushed_value(self) -> None:
        history = FrameHistory()
        history.push(ActiveFrame(10, 10, 0, 0))
        history.push(None)
        self.assertIsNone(history.pop())
        self.assertEqual(history.pop(), ActiveFrame(10, 10, 0, 0))
        self.assertFalse(history.can_undo())

    def test_limit_drops_the_oldest_values(self) -> None:
        history = FrameHistory(limit=2)
        for index in range(4):
            history.push(ActiveFrame(10 + index, 10, 0, 0))
        self.assertEqual(history.limit, 2)
        self.assertEqual(history.pop(), ActiveFrame(13, 10, 0, 0))
        self.assertEqual(history.pop(), ActiveFrame(12, 10, 0, 0))
        self.assertFalse(history.can_undo())

    def test_empty_pop_is_none_and_clear_empties(self) -> None:
        history = FrameHistory()
        self.assertIsNone(history.pop())
        history.push(None)
        history.clear()
        self.assertFalse(history.can_undo())

    def test_default_limit_is_twenty(self) -> None:
        self.assertEqual(MAX_MANUAL_UNDO_STEPS, 20)
        self.assertEqual(FrameHistory().limit, 20)
