"""인코딩 진행 창 항목 조작 검증: 현재 배치가 없으면 아무 일도 하지 않고, 없는 영상과 임시 파일은 안내만 함

- 컨트롤러, 정리, 미리 보기, 확인 창과 안내 창은 대역으로 바꿈
"""

import os
import tempfile
import unittest
from unittest import mock

from src.core.config import ClassifierConfig
from src.gui.controllers import encode_item_actions
from src.gui.controllers.encode_item_actions import LEFTOVER_MISSING_TEXT, EncodeItemActions
from src.service.encode.encode_job import EncodeSettings


class _ItemActionCase(unittest.TestCase):
    """대역을 묶어 조작 대상을 만드는 도우미. 현재 배치는 테스트가 정함"""

    def setUp(self) -> None:
        self.controller = mock.Mock()
        self.history = mock.Mock()
        self.cleanup = mock.Mock()
        self.preview = mock.Mock()
        self.refresh = mock.Mock()
        self.window = mock.Mock()
        self.batch: tuple[int, EncodeSettings] | None = None
        self.actions = EncodeItemActions(
            self.controller,
            self.history,
            self.cleanup,
            self.preview,
            lambda: self.window,
            lambda: self.batch,
            self.refresh,
        )


class ItemActionsWithoutBatchTests(_ItemActionCase):
    """배치를 시작하지 않은 상태에서는 교체 대기와 임시 파일 조작이 대상 없이 끝남"""

    def test_retry_forfeit_reveal_and_trash_do_nothing_without_a_batch(self) -> None:
        with mock.patch.object(encode_item_actions, "ask_forfeit") as ask:
            self.actions.retry_replace("a.mp4")
            self.actions.forfeit_replace("a.mp4")
            self.actions.trash_leftover("a.mp4")
            self.assertFalse(self.actions.reveal_leftover("a.mp4"))
        ask.assert_not_called()
        self.controller.retry_replace.assert_not_called()
        self.controller.forfeit_replace.assert_not_called()
        self.cleanup.trash_part.assert_not_called()
        self.refresh.assert_not_called()


class ItemActionsWithBatchTests(_ItemActionCase):
    """배치가 있을 때 파일이 없는 조작은 안내하고, 임시 파일이 사라졌으면 그 사실을 안내함"""

    def test_missing_video_is_reported_for_open_and_preview(self) -> None:
        missing = os.path.join(os.sep, "no-such-folder-for-item-actions", "missing.mp4")
        with (
            mock.patch.object(encode_item_actions, "open_media_path", return_value=False),
            mock.patch.object(encode_item_actions.QMessageBox, "information") as notice,
        ):
            self.actions.open_file(missing)
            self.actions.preview_file(missing)
        self.assertEqual(notice.call_count, 2)
        self.preview.preview_path.assert_not_called()

    def test_leftover_reveal_reports_a_missing_part_file(self) -> None:
        self.batch = (7, EncodeSettings.from_config(ClassifierConfig()))
        with (
            mock.patch.object(encode_item_actions, "leftover_part_path", return_value="gone.mp4.part"),
            mock.patch.object(encode_item_actions, "reveal_path", return_value=False),
            mock.patch.object(encode_item_actions.QMessageBox, "information") as notice,
        ):
            self.assertTrue(self.actions.reveal_leftover("a.mp4"))
        self.assertEqual(notice.call_args.args[2], LEFTOVER_MISSING_TEXT)

    def test_replace_designated_confirms_nothing_and_refreshes_after_it(self) -> None:
        """그대로 교체는 확인 없이 실행기에 넘기고 성공하면 행을 다시 읽으며, 거부되면 실행 중 안내"""
        self.batch = (7, EncodeSettings.from_config(ClassifierConfig()))
        self.controller.replace_designated.return_value = True
        with mock.patch.object(encode_item_actions.QMessageBox, "information") as notice:
            self.actions.replace_designated("a.mp4")
        self.controller.replace_designated.assert_called_once_with(7, self.batch[1], "a.mp4")
        self.refresh.assert_called_once_with(7)
        notice.assert_not_called()

        self.controller.replace_designated.return_value = False
        with mock.patch.object(encode_item_actions.QMessageBox, "information") as notice:
            self.actions.replace_designated("a.mp4")
        self.assertEqual(notice.call_args.args[2], encode_item_actions.REPLACE_BUSY_TEXT)

    def test_discard_failure_notice_is_about_discarding_not_forfeiting(self) -> None:
        """폐기가 실패하면 지운 결과 파일을 지우지 못했다는 사유를 안내하고, 거부되면 폐기 전용 안내를 씀"""
        self.batch = (7, EncodeSettings.from_config(ClassifierConfig()))
        self.controller.discard_replace.return_value = (False, "임시 결과 파일을 삭제하지 못했습니다: 공유 위반")
        with (
            mock.patch.object(encode_item_actions, "ask_discard_replace", return_value=True),
            mock.patch.object(encode_item_actions.QMessageBox, "information") as notice,
        ):
            self.actions.discard_replace("a.mp4")
        self.assertIn("공유 위반", notice.call_args.args[2])
        self.assertNotIn(encode_item_actions.FORFEIT_BUSY_TEXT, notice.call_args.args[2])

        self.controller.discard_replace.return_value = (False, "")
        with (
            mock.patch.object(encode_item_actions, "ask_discard_replace", return_value=True),
            mock.patch.object(encode_item_actions.QMessageBox, "information") as notice,
        ):
            self.actions.discard_replace("a.mp4")
        self.assertEqual(notice.call_args.args[2], encode_item_actions.DISCARD_BUSY_TEXT)

    def test_compare_files_delegates_to_the_injected_opener(self) -> None:
        """화질 비교 요청은 주입한 열기 함수로 원본 경로를 넘김"""
        calls: list[str] = []
        self.actions._compare_files = calls.append
        self.actions.compare_files("/work/a.mp4")
        self.assertEqual(calls, ["/work/a.mp4"])

    def test_compare_files_without_an_opener_does_nothing(self) -> None:
        """열기 함수가 없으면 조용히 넘어감"""
        self.actions.compare_files("/work/a.mp4")

    def test_preview_file_passes_the_progress_window_as_the_owner(self) -> None:
        """진행 창의 미리 보기 요청이 진행 창을 미리 보기 창의 소유자로 넘기는지 검증"""
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "clip.mp4")
            with open(path, "wb") as handle:
                handle.write(b"x")
            self.actions.preview_file(path)
        self.preview.preview_path.assert_called_once_with(path, owner=self.window)


if __name__ == "__main__":
    unittest.main()
