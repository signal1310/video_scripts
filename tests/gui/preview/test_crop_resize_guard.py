"""여백 편집 중 창 테두리 크기 조절이 손잡이 누름을 가로채지 않는지 검증"""

import ctypes
import os
import struct
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtCore import QPoint, QRect

from src.gui.preview.crop_edit import CROP_EDIT_MARGIN_PX, HANDLE_EDGE_PX, WINDOW_RESIZE_BAND_PX
from src.gui.preview.crop_resize_guard import (
    HTCLIENT,
    WM_NCHITTEST,
    blocks_frame_resize,
    is_frame_hit_test,
    message_screen_point,
    screen_point_from_lparam,
    should_keep_client_hit_test,
)

_BUTTON_EVENT = b"windows_generic_MSG"
_KEPT = QRect(100, 100, 400, 300)


def _message(message_id: int, point: QPoint) -> tuple[ctypes.Array[ctypes.c_char], sip.voidptr]:
    """MSG 구조체 앞부분과 lParam에 화면 좌표를 담아 네이티브 메시지 흉내를 만듦"""
    l_param = ((point.y() & 0xFFFF) << 16) | (point.x() & 0xFFFF)
    buf = ctypes.create_string_buffer(64)
    struct.pack_into("P", buf, 0, 0x1234)
    struct.pack_into("I", buf, 8, message_id)
    struct.pack_into("Q", buf, 16, 0)
    struct.pack_into("q", buf, 24, l_param)
    return buf, sip.voidptr(buf)


class ScreenPointTests(unittest.TestCase):
    """화면 좌표 변환과 메시지 해석"""

    def test_lparam_splits_into_signed_screen_coordinates(self) -> None:
        self.assertEqual(screen_point_from_lparam((300 << 16) | 200), QPoint(200, 300))

    def test_negative_coordinates_are_restored(self) -> None:
        negative_x = 0xFFFF - 9
        negative_y = 0xFFFF - 19
        self.assertEqual(
            screen_point_from_lparam((negative_y << 16) | negative_x), QPoint(-10, -20)
        )

    def test_message_point_is_read_from_the_message_struct(self) -> None:
        _buf, pointer = _message(WM_NCHITTEST, QPoint(321, 654))
        self.assertEqual(message_screen_point(pointer), QPoint(321, 654))

    def test_empty_pointer_has_no_point(self) -> None:
        self.assertIsNone(message_screen_point(sip.voidptr(0)))

    def test_only_hit_test_messages_are_recognized(self) -> None:
        _buf, hit = _message(WM_NCHITTEST, QPoint(10, 10))
        _buf2, other = _message(0x0200, QPoint(10, 10))
        self.assertTrue(is_frame_hit_test(_BUTTON_EVENT, hit))
        self.assertFalse(is_frame_hit_test(_BUTTON_EVENT, other))
        self.assertFalse(is_frame_hit_test(b"xcb_generic_event_t", hit))
        self.assertFalse(is_frame_hit_test(_BUTTON_EVENT, sip.voidptr(0)))


class HandleBandTests(unittest.TestCase):
    """손잡이 판정 띠가 창 테두리 크기 조절을 막는지"""

    def test_points_on_the_kept_edges_are_blocked(self) -> None:
        for point in (
            QPoint(_KEPT.left(), _KEPT.center().y()),
            QPoint(_KEPT.left() + _KEPT.width(), _KEPT.center().y()),
            QPoint(_KEPT.left(), _KEPT.top()),
            QPoint(_KEPT.left() + _KEPT.width(), _KEPT.top() + _KEPT.height()),
        ):
            with self.subTest(point=point):
                self.assertTrue(
                    blocks_frame_resize(point, _KEPT, tolerance=HANDLE_EDGE_PX)
                )

    def test_points_inside_the_kept_area_are_blocked_too(self) -> None:
        """유지 영역 안쪽 누름은 옮기기라 창 크기 조절로 넘기지 않음"""
        inside = QPoint(_KEPT.center())
        self.assertTrue(blocks_frame_resize(inside, _KEPT, tolerance=HANDLE_EDGE_PX))

    def test_points_outside_the_band_do_not_block(self) -> None:
        outside = QPoint(_KEPT.left() - HANDLE_EDGE_PX - 2, _KEPT.top() - HANDLE_EDGE_PX - 2)
        self.assertFalse(blocks_frame_resize(outside, _KEPT, tolerance=HANDLE_EDGE_PX))

    def test_empty_kept_rect_never_blocks(self) -> None:
        self.assertFalse(
            blocks_frame_resize(QPoint(10, 10), QRect(0, 0, 0, 0), tolerance=HANDLE_EDGE_PX)
        )

    def test_zero_margin_keeps_the_handles_clear_design_value(self) -> None:
        """여백은 창 테두리 판정 폭과 손잡이 판정 폭의 합보다 커야 함"""
        self.assertGreater(CROP_EDIT_MARGIN_PX, WINDOW_RESIZE_BAND_PX + HANDLE_EDGE_PX)
        self.assertEqual(HTCLIENT, 1)


class ShouldKeepClientTests(unittest.TestCase):
    """창 테두리 적중 판정을 클라이언트로 되돌릴 조건"""

    def test_without_an_edit_target_the_window_keeps_resizing(self) -> None:
        _buf, pointer = _message(WM_NCHITTEST, QPoint(10, 10))
        self.assertFalse(
            should_keep_client_hit_test(
                _BUTTON_EVENT, pointer, QRect(0, 0, 100, 100), edited_kept=None, tolerance=8
            )
        )

    def test_presses_inside_the_video_area_are_kept_for_the_handles(self) -> None:
        _buf, pointer = _message(WM_NCHITTEST, QPoint(50, 60))
        self.assertTrue(
            should_keep_client_hit_test(
                _BUTTON_EVENT,
                pointer,
                QRect(0, 0, 100, 100),
                edited_kept=_KEPT,
                tolerance=HANDLE_EDGE_PX,
            )
        )

    def test_presses_outside_the_video_still_resize_the_window(self) -> None:
        _buf, pointer = _message(WM_NCHITTEST, QPoint(900, 900))
        self.assertFalse(
            should_keep_client_hit_test(
                _BUTTON_EVENT,
                pointer,
                QRect(0, 0, 100, 100),
                edited_kept=_KEPT,
                tolerance=HANDLE_EDGE_PX,
            )
        )

    def test_other_messages_are_left_alone(self) -> None:
        _buf, pointer = _message(0x0200, QPoint(50, 60))
        self.assertFalse(
            should_keep_client_hit_test(
                _BUTTON_EVENT,
                pointer,
                QRect(0, 0, 100, 100),
                edited_kept=_KEPT,
                tolerance=HANDLE_EDGE_PX,
            )
        )
