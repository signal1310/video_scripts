"""인코딩 임시 파일 정리 화면 흐름 검증: 빈 상태 안내, 정리 창 연결, 이어 하지 않음과 실패 행의 옮기기, 영구 삭제 분기

- 휴지통과 영구 삭제는 기록용 대역으로 바꾸고, 확인 창은 응답을 정해 둔 대역으로 바꿈
"""

import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from src.core.config import ClassifierConfig
from src.core.encode.encode_leftovers import LeftoverGroup, LeftoverVerdict
from src.core.encode.encode_state import EncodeState
from src.gui.controllers import encode_cleanup
from src.gui.controllers.encode_cleanup import LeftoverCleanup
from src.service.encode.encode_history import EncodeHistory, NewItem
from src.service.encode.encode_job import EncodeSettings
from src.service.encode.encode_leftovers import LeftoverItem

_APP = QApplication.instance() or QApplication([])
LOGGER: str = "src.gui.controllers.encode_cleanup"
OUTPUT_DIR: str = "_인코딩결과"


class _Recorder:
    """옮긴 경로를 기록하는 휴지통과 영구 삭제 대역"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, path: str) -> bool:
        self.calls.append(path)
        return True


class LeftoverCleanupTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "work")
        os.makedirs(self.root)
        self.history = EncodeHistory(os.path.join(self._tmp.name, "video_cache.sqlite3"), sleep=lambda _s: None)
        self.addCleanup(self.history.close)
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)
        self.cleanup = LeftoverCleanup(self.parent, self.history)
        self.settings_json = EncodeSettings.from_config(ClassifierConfig()).to_json()

    def _part(self, name: str) -> str:
        path = os.path.join(self.root, name)
        with open(path, "wb") as handle:
            handle.write(b"part")
        os.utime(path, ns=(1_600_000_000_000_000_000, 1_600_000_000_000_000_000))
        return path

    def test_open_still_shows_the_window_when_no_part_file_exists(self) -> None:
        """작업 폴더에 임시 파일이 없어도 정리 창을 열어 빈 상태를 보이고, 별도 안내 창은 띄우지 않음"""
        with (
            mock.patch.object(encode_cleanup, "CleanupWindow") as window,
            mock.patch.object(QMessageBox, "information") as notice,
        ):
            self.cleanup.open(self.root, OUTPUT_DIR)
        window.assert_called_once()
        self.assertEqual(window.call_args.args[0], [])
        window.return_value.exec.assert_called_once()
        notice.assert_not_called()

    def test_open_hands_the_found_files_to_the_modal_window(self) -> None:
        """찾은 임시 파일을 정리 창에 넘기고 모달로 실행"""
        self._part("a.mp4.part")
        with mock.patch.object(encode_cleanup, "CleanupWindow") as window:
            self.cleanup.open(self.root, OUTPUT_DIR)
        items = window.call_args.args[0]
        self.assertEqual([os.path.basename(item.path) for item in items], ["a.mp4.part"])
        window.return_value.exec.assert_called_once()

    def test_discard_moves_the_batch_parts_to_trash_and_logs_each_move(self) -> None:
        """이어 하지 않음은 배치의 진행 중 임시 파일을 휴지통으로 옮기고 작업 기록에 한 줄씩 남김"""
        part = self._part("b.mp4.part")
        source = os.path.join(self.root, "b.mp4")
        batch_id = self.history.create_batch(self.root, self.settings_json, [NewItem(source, 4, 1, "{}")])
        self.history.update_item(batch_id, source, {"state": EncodeState.PENDING, "part_path": part})
        recorder = _Recorder()
        with (
            mock.patch.object(encode_cleanup, "move_to_trash", recorder),
            self.assertLogs(LOGGER, "INFO") as logs,
        ):
            self.cleanup.discard_batch(batch_id)
        self.assertEqual(recorder.calls, [part])
        self.assertIn("임시 파일 휴지통 이동: b.mp4.part", logs.output[0])
        self.assertEqual(self.history.list_open_batches(), [])

    def test_discard_keeps_a_part_on_a_drive_without_a_confirmed_trash(self) -> None:
        """이어 하지 않음은 휴지통을 확인할 수 없는 드라이브의 임시 파일을 옮기지 않고, 건너뜀 사유를 작업 기록에 남김"""
        part = self._part("c.mp4.part")
        source = os.path.join(self.root, "c.mp4")
        batch_id = self.history.create_batch(self.root, self.settings_json, [NewItem(source, 4, 1, "{}")])
        self.history.update_item(batch_id, source, {"state": EncodeState.PENDING, "part_path": part})
        recorder = _Recorder()
        with (
            mock.patch.object(encode_cleanup, "move_to_trash", recorder),
            mock.patch.object(encode_cleanup, "needs_permanent_confirm", return_value=True),
            self.assertLogs(LOGGER, "INFO") as logs,
        ):
            self.cleanup.discard_batch(batch_id)
        self.assertEqual(recorder.calls, [])
        self.assertTrue(os.path.exists(part))
        self.assertIn("임시 파일 정리 건너뜀: c.mp4.part", logs.output[0])
        self.assertIn("휴지통을 확인할 수 없는 드라이브", logs.output[0])
        self.assertEqual(self.history.list_items(batch_id)[0].part_path, part)

    def test_trash_part_on_a_local_drive_asks_once_and_moves_the_file(self) -> None:
        """실패 행의 임시 파일은 확인 한 번 뒤 휴지통으로 옮김"""
        recorder = _Recorder()
        with (
            mock.patch.object(encode_cleanup, "needs_permanent_confirm", return_value=False),
            mock.patch.object(encode_cleanup, "ask_part_trash", return_value=True) as ask,
            mock.patch.object(encode_cleanup, "move_to_trash", recorder),
            mock.patch.object(encode_cleanup, "trash_path", wraps=encode_cleanup.trash_path) as trash_path,
            self.assertLogs(LOGGER, "INFO"),
        ):
            part = self._part("c.mp4.part")
            self.cleanup.trash_part(part)
        ask.assert_called_once_with(self.parent, "c.mp4.part")
        trash_path.assert_called_once()
        self.assertEqual(recorder.calls, [part])

    def test_declined_trash_part_leaves_the_file_alone(self) -> None:
        """확인 창에서 취소하면 아무 파일도 옮기지 않음"""
        recorder = _Recorder()
        with (
            mock.patch.object(encode_cleanup, "needs_permanent_confirm", return_value=False),
            mock.patch.object(encode_cleanup, "ask_part_trash", return_value=False),
            mock.patch.object(encode_cleanup, "move_to_trash", recorder),
        ):
            self.cleanup.trash_part(self._part("d.mp4.part"))
        self.assertEqual(recorder.calls, [])

    def test_trash_part_on_a_drive_without_a_trash_needs_the_permanent_confirm(self) -> None:
        """휴지통을 확인할 수 없는 드라이브의 임시 파일은 영구 삭제 확인을 받고, 거절하면 지우지 않음"""
        deleted = _Recorder()
        part = self._part("e.mp4.part")
        with (
            mock.patch.object(encode_cleanup, "needs_permanent_confirm", return_value=True),
            mock.patch.object(encode_cleanup, "ask_permanent", return_value=False) as permanent,
            mock.patch.object(encode_cleanup, "delete_permanently", deleted),
        ):
            self.cleanup.trash_part(part)
        permanent.assert_called_once()
        self.assertEqual(deleted.calls, [])
        with (
            mock.patch.object(encode_cleanup, "needs_permanent_confirm", return_value=True),
            mock.patch.object(encode_cleanup, "ask_permanent", return_value=True),
            mock.patch.object(encode_cleanup, "delete_permanently", deleted),
            self.assertLogs(LOGGER, "INFO") as logs,
        ):
            self.cleanup.trash_part(part)
        self.assertEqual(deleted.calls, [part])
        self.assertIn("임시 파일 영구 삭제: e.mp4.part", logs.output[0])

    def test_window_trash_moves_local_files_and_keeps_remote_ones_when_permanent_delete_is_declined(self) -> None:
        """정리 창의 옮기기는 로컬 파일만 휴지통으로 보내고, 영구 삭제 확인을 거절하면 드라이브 파일은 건드리지 않음"""
        local = self._part("local.mp4.part")
        remote = self._part("remote.mp4.part")
        verdict = LeftoverVerdict(LeftoverGroup.RECORDED, None)
        items = [
            LeftoverItem(local, self.root, 4, 1_600_000_000_000_000_000, verdict),
            LeftoverItem(remote, self.root, 4, 1_600_000_000_000_000_000, verdict),
        ]
        with mock.patch.object(encode_cleanup, "CleanupWindow") as window:
            self.cleanup.open(self.root, OUTPUT_DIR)
            on_trash = window.call_args.args[1]
        recorder = _Recorder()
        deleted = _Recorder()
        with (
            mock.patch.object(encode_cleanup, "paths_needing_permanent_confirm", return_value=[remote]),
            mock.patch.object(encode_cleanup, "ask_permanent", return_value=False),
            mock.patch.object(encode_cleanup, "move_to_trash", recorder),
            mock.patch.object(encode_cleanup, "delete_permanently", deleted),
            self.assertLogs(LOGGER, "INFO"),
        ):
            outcomes = on_trash(items)
        self.assertEqual(recorder.calls, [local])
        self.assertEqual(deleted.calls, [])
        self.assertEqual([o.path for o in outcomes], [local])
        self.assertTrue(os.path.exists(remote))


if __name__ == "__main__":
    unittest.main(verbosity=2)
