"""이어서 재인코딩 확인 대화상자와 교체 포기 확인: 본문 문장, 선택 기록, 기록 밖 임시 파일 목록 표시, 모달 호출 경로

- 모달 실행은 exec를 대역으로 바꿔 즉시 반환하게 하고, 선택은 choose로 직접 기록
"""

import os
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QDialog, QListWidget, QMessageBox, QWidget

from src.gui.dialogs.encode_resume_dialogs import (
    EXCLUDED_LINE,
    REPLACE_LINE,
    RESUME_SCOPE_LINE,
    ResumeChoice,
    ResumeDialog,
    ask_forfeit,
    ask_resume,
    resume_body_lines,
)
from src.service.encode.encode_resume import ResumeCount

_APP = QApplication.instance() or QApplication([])


class ResumeBodyTests(unittest.TestCase):
    """본문 문장: 제외 문장은 제외 영상이 있을 때만, 임시 파일 범위 문장은 항상"""

    def test_excluded_line_appears_only_when_something_is_excluded(self) -> None:
        with_excluded = resume_body_lines(ResumeCount(resumable=12, excluded=3))
        without = resume_body_lines(ResumeCount(resumable=12, excluded=0))
        self.assertIn(EXCLUDED_LINE.format(count=3), with_excluded)
        self.assertNotIn(EXCLUDED_LINE.format(count=3), without)
        self.assertEqual(with_excluded[-1], RESUME_SCOPE_LINE)
        self.assertEqual(without[-1], RESUME_SCOPE_LINE)

    def test_replace_only_batch_does_not_say_zero_videos_can_be_resumed(self) -> None:
        """재개할 영상이 없고 교체 대기만 있으면 '0개 이어서 재인코딩' 문장 대신 교체 대기 개수를 말함"""
        lines = resume_body_lines(ResumeCount(resumable=0, excluded=0, replace_pending=2))
        self.assertFalse(any("0개" in line for line in lines))
        self.assertIn(REPLACE_LINE.format(count=2), lines)


class ResumeDialogTests(unittest.TestCase):
    """대화상자 선택과 목록: 이어서 재인코딩만 수락으로 닫히고, 기록 밖 목록은 스크롤 목록으로 보임"""

    def setUp(self) -> None:
        self.parent = QWidget()
        self.addCleanup(self.parent.deleteLater)

    def test_choosing_resume_accepts_and_the_others_reject(self) -> None:
        dialog = ResumeDialog(ResumeCount(resumable=1, excluded=0), [], self.parent)
        dialog.choose(ResumeChoice.LATER)
        self.assertEqual((dialog.choice, dialog.result()), (ResumeChoice.LATER, QDialog.DialogCode.Rejected))
        dialog.choose(ResumeChoice.RESUME)
        self.assertEqual((dialog.choice, dialog.result()), (ResumeChoice.RESUME, QDialog.DialogCode.Accepted))

    def test_unrecorded_parts_are_listed_in_a_scrollable_list(self) -> None:
        dialog = ResumeDialog(ResumeCount(resumable=1, excluded=0), ["a.part", "b.part"], self.parent)
        listings = dialog.findChildren(QListWidget)
        self.assertEqual([listing.count() for listing in listings], [2])

    def test_no_unrecorded_parts_means_no_list(self) -> None:
        dialog = ResumeDialog(ResumeCount(resumable=1, excluded=0), [], self.parent)
        self.assertEqual(dialog.findChildren(QListWidget), [])

    def test_ask_resume_returns_the_chosen_value_from_the_modal_run(self) -> None:
        def _exec_choosing_discard(dialog: ResumeDialog) -> int:
            dialog.choose(ResumeChoice.DISCARD)
            return 0

        with mock.patch.object(ResumeDialog, "exec", new=_exec_choosing_discard):
            choice = ask_resume(self.parent, ResumeCount(resumable=1, excluded=0), [])
        self.assertIs(choice, ResumeChoice.DISCARD)

    def test_ask_resume_without_a_choice_keeps_the_batch(self) -> None:
        with mock.patch.object(ResumeDialog, "exec", return_value=0):
            choice = ask_resume(self.parent, ResumeCount(resumable=1, excluded=0), ["a.part"])
        self.assertIs(choice, ResumeChoice.LATER)

    def test_forfeit_without_clicking_forfeit_keeps_the_item(self) -> None:
        with mock.patch.object(QMessageBox, "exec", return_value=0):
            self.assertFalse(ask_forfeit(self.parent))
