"""휴지통 옮기기, 영구 삭제, 드라이브 종류에 따른 영구 삭제 확인 분기 검증

- QFile.moveToTrash와 드라이브 조회는 대역으로 바꿈. 실제 휴지통과 실제 드라이브에는 닿지 않음
"""

import os
import tempfile
import unittest
from unittest import mock

from src.gui.controllers import encode_trash
from src.gui.controllers.encode_trash import (
    DRIVE_REMOTE,
    DRIVE_REMOVABLE,
    DRIVE_UNKNOWN,
    delete_permanently,
    move_to_trash,
    needs_permanent_confirm,
    paths_needing_permanent_confirm,
)


class MoveToTrashTest(unittest.TestCase):
    def test_reports_the_trash_result_from_qfile(self) -> None:
        with mock.patch.object(encode_trash.QFile, "moveToTrash", return_value=(True, "")) as trash:
            self.assertTrue(move_to_trash("C:/x/a.mp4.part"))
        trash.assert_called_once_with("C:/x/a.mp4.part")
        with mock.patch.object(encode_trash.QFile, "moveToTrash", return_value=(False, "")):
            self.assertFalse(move_to_trash("C:/x/a.mp4.part"))


class DeletePermanentlyTest(unittest.TestCase):
    def test_removes_a_temporary_file_and_reports_failures_as_false(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "a.mp4.part")
            with open(path, "wb") as handle:
                handle.write(b"x")
            self.assertTrue(delete_permanently(path))
            self.assertFalse(os.path.exists(path))
            self.assertFalse(delete_permanently(path))


class DriveKindTest(unittest.TestCase):
    def test_removable_remote_and_unknown_drives_need_the_permanent_confirm(self) -> None:
        for kind in (DRIVE_UNKNOWN, DRIVE_REMOVABLE, DRIVE_REMOTE):
            with self.subTest(kind=kind), mock.patch.object(encode_trash, "drive_kind", return_value=kind):
                self.assertTrue(needs_permanent_confirm("E:/clip.mp4.part"))
        with mock.patch.object(encode_trash, "drive_kind", return_value=3):
            self.assertFalse(needs_permanent_confirm("C:/clip.mp4.part"))

    def test_batch_filter_keeps_input_order_and_only_unconfirmed_drives(self) -> None:
        kinds = {"C:/a.part": 3, "E:/b.part": DRIVE_REMOVABLE, "C:/c.part": 3, "Z:/d.part": DRIVE_REMOTE}
        with mock.patch.object(encode_trash, "drive_kind", side_effect=lambda path: kinds[path]):
            self.assertEqual(paths_needing_permanent_confirm(list(kinds)), ["E:/b.part", "Z:/d.part"])

    def test_drive_lookup_is_unknown_without_the_windows_api(self) -> None:
        with mock.patch.object(encode_trash.ctypes, "windll", None, create=True):
            self.assertEqual(encode_trash.drive_kind("C:/a.part"), DRIVE_UNKNOWN)


if __name__ == "__main__":
    unittest.main(verbosity=2)
