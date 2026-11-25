"""FFmpeg 명령 복사 동작(table_command_actions)과 하단 상태 줄 알림 단위 테스트"""

import os
import unittest
from unittest import mock

from PyQt6.QtWidgets import QApplication

from src.core.models import VideoEvaluation
from src.gui.table.table_command_actions import (
    COPIED_NOTICE,
    copy_ffmpeg_commands,
    planned_command_lines,
)
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

    def test_nothing_to_copy_keeps_the_footer_quiet(self) -> None:
        """명령을 만들 수 없으면 복사 건수 0을 돌려주고 안내도 띄우지 않음"""
        model = _Model({"b.mp4": None})
        footer = _Footer()
        count = copy_ffmpeg_commands(model, [_KEPT], _Window(footer))
        self.assertEqual(count, 0)
        self.assertEqual(footer.texts, [])

    def test_window_without_footer_still_copies(self) -> None:
        """하단 상태 줄이 없는 창에서도 복사는 수행"""
        model = _Model({"a.mp4": "ffmpeg -i a.mp4"})
        with mock.patch(
            "src.gui.table.table_command_actions.copy_lines_to_clipboard",
            return_value=1,
        ):
            self.assertEqual(copy_ffmpeg_commands(model, [_TARGET], _Window(None)), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
