"""FFmpeg 명령 복사 동작(table_command_actions)과 하단 상태 줄 알림 단위 테스트"""

import os
import unittest
from unittest import mock

from PyQt6.QtWidgets import QApplication

from src.core.models import VideoEvaluation
from src.core.smart_action import SmartAction
from src.gui.table.table_command_actions import (
    COPIED_NOTICE,
    NO_COMMAND_NOTICE,
    copy_ffmpeg_commands,
    planned_command_lines,
)
from src.gui.table.table_model import VideoTableModel
from tests._factories import make_eval, make_video

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

_TARGET = make_eval(make_video(filename="a.mp4"))
_KEPT = make_eval(make_video(filename="b.mp4"))


class _Model:
    """영상별 명령줄을 돌려주는 가짜 표 모델"""

    def __init__(self, lines: dict[str, str | None]) -> None:
        self._lines = lines

    def planned_command_line(self, ev: VideoEvaluation) -> str | None:
        return self._lines.get(ev.video.filename)


class _Footer:
    """하단 상태 줄 호출을 기록하는 가짜 바"""

    def __init__(self) -> None:
        self.texts: list[str] = []

    def set_status_text(self, text: str, tooltip: str = "") -> None:
        self.texts.append(text)


class _Window:
    def __init__(self, footer: _Footer | None) -> None:
        if footer is not None:
            self.footer_bar = footer


class _ChildWidget:
    """상태 줄을 직접 갖지 않고 최상위 창만 가지는 자식 위젯 대역"""

    def __init__(self, window: _Window) -> None:
        self._window = window

    def window(self) -> _Window:
        return self._window


class PlannedCommandLineTests(unittest.TestCase):
    """선택 영상의 명령줄 목록 규칙 검증"""

    def test_missing_and_duplicate_commands_are_dropped(self) -> None:
        """명령을 만들 수 없는 영상은 빼고, 같은 명령은 한 번만 남김"""
        model = _Model({"a.mp4": "ffmpeg -i a.mp4", "b.mp4": None})
        lines = planned_command_lines(model, [_TARGET, _KEPT, _TARGET])
        self.assertEqual(lines, ["ffmpeg -i a.mp4"])

    def test_each_selected_video_keeps_its_own_command(self) -> None:
        """선택한 영상마다 자기 명령이 선택 순서대로 들어감"""
        model = _Model({"a.mp4": "ffmpeg -i a.mp4", "b.mp4": "ffmpeg -i b.mp4"})
        self.assertEqual(
            planned_command_lines(model, [_KEPT, _TARGET]),
            ["ffmpeg -i b.mp4", "ffmpeg -i a.mp4"],
        )


class CopyFfmpegCommandsTests(unittest.TestCase):
    """클립보드 복사와 하단 안내 검증"""

    def test_copies_lines_and_reports_the_count(self) -> None:
        """줄바꿈으로 복사하고 복사 건수를 알리며 하단 상태 줄에 알림"""
        model = _Model({"a.mp4": "ffmpeg -i a.mp4", "b.mp4": "ffmpeg -i b.mp4"})
        footer = _Footer()
        with mock.patch(
            "src.gui.table.table_command_actions.copy_lines_to_clipboard",
            return_value=2,
        ) as copy:
            count = copy_ffmpeg_commands(model, [_TARGET, _KEPT], _Window(footer))
        self.assertEqual(count, 2)
        copy.assert_called_once_with(["ffmpeg -i a.mp4", "ffmpeg -i b.mp4"])
        self.assertEqual(footer.texts, [COPIED_NOTICE.format(count=2)])
        self.assertEqual(COPIED_NOTICE.format(count=2), "FFmpeg 명령 2개를 복사했습니다.")

    def test_copies_through_child_widget_resolving_the_top_level_footer(self) -> None:
        """표 뷰처럼 상태 줄을 직접 갖지 않는 자식 위젯에서도 최상위 창의 상태 줄에 알림"""
        model = _Model({"a.mp4": "ffmpeg -i a.mp4"})
        footer = _Footer()
        with mock.patch(
            "src.gui.table.table_command_actions.copy_lines_to_clipboard",
            return_value=1,
        ):
            count = copy_ffmpeg_commands(model, [_TARGET], _ChildWidget(_Window(footer)))
        self.assertEqual(count, 1)
        self.assertEqual(footer.texts, [COPIED_NOTICE.format(count=1)])

    def test_empty_copy_keeps_the_clipboard_and_reports_why(self) -> None:
        """모든 선택 영상이 건너뛰어 0건이면 클립보드는 그대로 두고 복사할 명령이 없음을 알림"""
        model = _Model({"b.mp4": None})
        footer = _Footer()
        with mock.patch(
            "src.gui.table.table_command_actions.copy_lines_to_clipboard",
            return_value=0,
        ) as copy:
            count = copy_ffmpeg_commands(model, [_KEPT], _Window(footer))
        self.assertEqual(count, 0)
        copy.assert_called_once_with([])
        self.assertEqual(footer.texts, [NO_COMMAND_NOTICE])
        self.assertEqual(NO_COMMAND_NOTICE, "복사할 FFmpeg 명령이 없습니다.")

    def test_window_without_footer_still_copies(self) -> None:
        """하단 상태 줄이 없는 창에서도 복사는 수행"""
        model = _Model({"a.mp4": "ffmpeg -i a.mp4"})
        with mock.patch(
            "src.gui.table.table_command_actions.copy_lines_to_clipboard",
            return_value=1,
        ):
            self.assertEqual(copy_ffmpeg_commands(model, [_TARGET], _Window(None)), 1)


class PreservedRowsAreNotCopiedTests(unittest.TestCase):
    """원본 보존 행만 골랐을 때 클립보드를 건드리지 않는지 실제 표 모델로 검증"""

    def test_preserved_rows_copy_nothing(self) -> None:
        """보존 행은 예정 명령이 없으므로 클립보드를 그대로 두고 복사할 명령이 없음을 알림"""
        model = VideoTableModel()
        preserved = [
            make_eval(make_video(filename="keep1.mp4"), smart_action=SmartAction.PASS_OPTIMAL),
            make_eval(make_video(filename="keep2.mp4"), smart_action=SmartAction.PASS_LOW),
        ]
        model.set_data_source(preserved, root_dir="work")
        footer = _Footer()
        clipboard = QApplication.clipboard()
        assert clipboard is not None
        clipboard.setText("sentinel")

        count = copy_ffmpeg_commands(model, preserved, _Window(footer))

        self.assertEqual(count, 0)
        self.assertEqual(clipboard.text(), "sentinel")
        self.assertEqual(footer.texts, [NO_COMMAND_NOTICE])


if __name__ == "__main__":
    unittest.main(verbosity=2)
