"""재인코딩 진행 창이 작업 카드, 조작 신호, 중지 확인, 결과 목록, 닫기 숨김을 올바르게 다루는지 검증"""

import os
import tempfile
import unittest
from typing import cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QApplication, QMessageBox, QWidget

from src.core.encode.encode_batch_book import EncodeBatchBook
from src.core.encode.encode_progress import ProgressSample
from src.core.encode.encode_state import EncodeState
from src.gui.controllers.encode_controller import EncodeController
from src.gui.dialogs.encode_progress_window import (
    ALL_STOP_CONFIRM_TEXT,
    ITEM_STOP_CONFIRM_TEXT,
    RESUME_ALL_TIP,
    EncodeProgressWindow,
    ask_stop,
    shorten_middle,
    tab_title,
)
from src.service.encode.encode_events import EVENT_LONG_PAUSE, EVENT_PROGRESS, EVENT_STATE, EncodeEvent
from src.service.encode.encode_job import jobs_from_evaluations
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_NAME: str = "a.mp4"


class _FakeController(QObject):
    """화면이 쓰는 컨트롤러 신호와 제어 메서드를 흉내 내고, 호출을 기록하는 가짜"""

    event_received = pyqtSignal(object)
    batch_finished = pyqtSignal(object)
    pause_changed = pyqtSignal(bool)
    item_pause_changed = pyqtSignal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def pause_all(self) -> None:
        self.calls.append("pause_all")

    def resume_all(self) -> None:
        self.calls.append("resume_all")

    def cancel_all(self) -> None:
        self.calls.append("cancel_all")

    def request_stop_after_current(self) -> None:
        self.calls.append("stop_after")

    def pause_one(self, source: str) -> None:
        self.calls.append(f"pause_one:{source}")

    def resume_one(self, source: str) -> None:
        self.calls.append(f"resume_one:{source}")

    def cancel_one(self, source: str) -> None:
        self.calls.append(f"cancel_one:{source}")


def _sample(out_time: float, speed: float) -> ProgressSample:
    return ProgressSample(
        frame=None,
        fps=None,
        quality=None,
        bitrate_kbps=None,
        total_size_bytes=None,
        out_time_sec=out_time,
        speed=speed,
        finished=False,
    )


class EncodeProgressWindowTests(unittest.TestCase):
    """진행 창이 배치 이벤트를 카드와 결과 목록으로 바꾸고 조작을 컨트롤러에 넘기는지 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root: str = self._tmp.name
        with open(os.path.join(self.root, _NAME), "wb") as handle:
            handle.write(b"source")
        jobs = jobs_from_evaluations([make_eval(video=make_video(filename=_NAME))], self.root)
        self.job_path: str = jobs[0].source_path
        self.jobs = {self.job_path: jobs[0]}
        self.book = EncodeBatchBook()
        self.book.start([self.job_path])
        self.controller = _FakeController()
        self.asked: list[str] = []
        self.answer: bool = False
        self.window = EncodeProgressWindow(
            cast("EncodeController", self.controller), self.book, None, confirm=self._confirm
        )
        self.window.start_batch(self.jobs, 1)

    def tearDown(self) -> None:
        self.window.deleteLater()
        self._tmp.cleanup()

    def _confirm(self, _parent: QWidget, text: str) -> bool:
        self.asked.append(text)
        return self.answer

    def _start_running(self) -> None:
        self.book.apply(self.job_path, EncodeState.RUNNING)
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, self.job_path, state=EncodeState.RUNNING))

    def test_running_event_adds_a_card_and_progress_fills_its_values(self) -> None:
        """실행 시작 이벤트가 카드를 만들고, 진행 이벤트가 위치, 속도, 막대, 그래프 기록을 채우는지 검증"""
        self._start_running()
        card = self.window._cards[self.job_path]
        self.controller.event_received.emit(
            EncodeEvent(
                EVENT_PROGRESS,
                1,
                self.job_path,
                sample=_sample(out_time=30.0, speed=1.25),
                instant_kbps=2100.0,
                elapsed_sec=24.0,
            )
        )
        self.assertEqual(card._values[0].text(), "00:30 / 01:00")
        self.assertEqual(card._values[1].text(), "1.25배")
        self.assertEqual(card._values[4].text(), "2100kbps")
        self.assertEqual(card._bar.value(), 500)
        self.assertEqual(len(card.series.points()), 1)

    def test_long_pause_notice_and_item_pause_signal_update_the_card(self) -> None:
        """장시간 안내 이벤트가 안내를 보이게 하고, 개별 일시 정지 신호가 카드 상태와 기록을 바꾸는지 검증"""
        self._start_running()
        card = self.window._cards[self.job_path]
        self.controller.event_received.emit(EncodeEvent(EVENT_LONG_PAUSE, 1, self.job_path))
        self.assertFalse(card._notice.isHidden())
        self.controller.item_pause_changed.emit(self.job_path, True)
        self.assertTrue(card.is_paused)
        self.assertEqual(len(card.series.points()), 1)
        self.controller.item_pause_changed.emit(self.job_path, False)
        self.assertFalse(card.is_paused)
        self.assertTrue(card._notice.isHidden())

    def test_item_buttons_call_the_controller_and_stop_asks_first(self) -> None:
        """영상 일시 정지와 재개는 바로 호출하고, 중지는 확인 창의 답을 따르는지 검증"""
        self._start_running()
        card = self.window._cards[self.job_path]
        card._pause_btn.click()
        self.assertEqual(self.controller.calls, [f"pause_one:{self.job_path}"])
        self.controller.item_pause_changed.emit(self.job_path, True)
        card._pause_btn.click()
        self.assertEqual(self.controller.calls[-1], f"resume_one:{self.job_path}")
        card._stop_btn.click()
        self.assertEqual(self.asked, [ITEM_STOP_CONFIRM_TEXT])
        self.assertNotIn(f"cancel_one:{self.job_path}", self.controller.calls)
        self.answer = True
        card._stop_btn.click()
        self.assertEqual(self.controller.calls[-1], f"cancel_one:{self.job_path}")

    def test_batch_pause_and_stop_controls_follow_the_batch_state(self) -> None:
        """전체 일시 정지와 재개, 현재 영상 후 중지, 전체 중지가 배치 상태에 맞게 호출되는지 검증"""
        self._start_running()
        self.window._pause_all_btn.click()
        self.assertEqual(self.controller.calls[-1], "pause_all")
        self.controller.pause_changed.emit(True)
        self.assertEqual(self.window._pause_all_btn.toolTip(), RESUME_ALL_TIP)
        self.assertTrue(self.window._cards[self.job_path].is_paused)
        self.window._pause_all_btn.click()
        self.assertEqual(self.controller.calls[-1], "resume_all")
        self.window._after_btn.click()
        self.assertEqual(self.controller.calls[-1], "stop_after")
        self.window._stop_all_btn.click()
        self.assertEqual(self.asked[-1], ALL_STOP_CONFIRM_TEXT)
        self.assertNotIn("cancel_all", self.controller.calls)
        self.answer = True
        self.window._stop_all_btn.click()
        self.assertEqual(self.controller.calls[-1], "cancel_all")

    def test_long_pause_link_requests_stop_after_current(self) -> None:
        """장시간 안내의 링크가 현재 영상 후 중지 요청으로 이어지는지 검증"""
        self._start_running()
        card = self.window._cards[self.job_path]
        card._notice.linkActivated.emit("stop-after")
        self.assertEqual(self.controller.calls, ["stop_after"])

    def test_finished_item_moves_to_results_and_batch_finish_updates_the_header(self) -> None:
        """끝난 항목이 카드에서 빠져 결과 목록에 상태와 함께 나오고, 배치 종료가 머리글을 바꾸는지 검증"""
        self._start_running()
        self.book.apply(self.job_path, EncodeState.DONE)
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, self.job_path, state=EncodeState.DONE))
        self.assertNotIn(self.job_path, self.window._cards)
        self.assertEqual(self.window._results.rowCount(), 1)
        status_item = self.window._results.item(0, 1)
        self.assertIsNotNone(status_item)
        self.assertEqual(status_item.text() if status_item is not None else "", "완료")
        self.assertEqual(self.window._header.text(), "전체 1/1 처리")
        self.controller.batch_finished.emit(object())
        self.assertEqual(self.window._header.text(), "재인코딩을 마쳤습니다. 완료 1개")

    def test_long_names_are_shortened_in_the_middle_and_short_names_are_kept(self) -> None:
        """긴 파일 이름은 가운데를 줄이고 짧은 이름은 그대로 두며, 탭 제목에 순서 번호가 붙는지 검증"""
        self.assertEqual(shorten_middle("video10_normal_phone.mp4", 12), "video1…e.mp4")
        self.assertEqual(shorten_middle("short.mp4", 12), "short.mp4")
        self.assertEqual(shorten_middle("abcdef", 1), "…")
        self.assertEqual(tab_title(3, "video1.mp4"), "3. video1.mp4")

    def test_finished_batch_freezes_elapsed_time_and_disables_batch_controls(self) -> None:
        """배치가 끝나면 경과 시간이 끝난 시점에 고정되고, 남은 시간과 전체 조작 버튼이 비활성화되는지 검증"""
        self._start_running()
        self.controller.batch_finished.emit(object())
        frozen = self.window._elapsed.text()
        self.assertFalse(self.window._after_btn.isEnabled())
        self.assertFalse(self.window._stop_all_btn.isEnabled())
        self.assertEqual(self.window._remaining.text(), "")
        with mock.patch("src.gui.dialogs.encode_progress_window.time.monotonic", return_value=1e9):
            self.window._refresh_all()
        self.assertEqual(self.window._elapsed.text(), frozen)
        self.window.start_batch(self.jobs, 1)
        self.assertTrue(self.window._stop_all_btn.isEnabled())

    def test_stopped_batch_says_stopped_and_window_opens_at_minimum_size(self) -> None:
        """전체 중지로 끝나면 머리글이 중지 문구가 되고, 창은 최소 크기로 만들어지는지 검증"""
        self.assertEqual(self.window.size().width(), self.window.minimumWidth())
        self.assertEqual(self.window.size().height(), self.window.minimumHeight())
        self._start_running()
        self.answer = True
        self.window._stop_all_btn.click()
        self.controller.batch_finished.emit(object())
        self.assertEqual(self.window._header.text(), "재인코딩을 중지했습니다. 완료 0개")

    def test_each_running_item_gets_a_tab_named_by_its_file(self) -> None:
        """진행 중인 영상마다 파일 이름의 탭이 생기고, 끝나면 탭이 사라지는지 검증"""
        self._start_running()
        self.assertEqual(self.window._tabs.count(), 1)
        self.assertEqual(self.window._tabs.tabText(0), f"1. {_NAME}")
        self.assertEqual(self.window._tabs.tabToolTip(0), f"1. {_NAME}")
        self.book.apply(self.job_path, EncodeState.DONE)
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, self.job_path, state=EncodeState.DONE))
        self.assertEqual(self.window._tabs.count(), 0)
        self.assertTrue(self.window._tabs.isHidden())

    def test_new_batch_clears_the_previous_cards(self) -> None:
        """새 배치를 시작하면 이전 카드가 지워지는지 검증"""
        self._start_running()
        self.window.start_batch(self.jobs, 1)
        self.assertEqual(self.window._cards, {})

    def test_ask_stop_is_false_when_the_dialog_is_closed_without_a_choice(self) -> None:
        """확인 창을 선택 없이 닫으면 중지로 보지 않는지 검증"""
        with mock.patch.object(QMessageBox, "exec", return_value=0):
            self.assertFalse(ask_stop(self.window, ITEM_STOP_CONFIRM_TEXT))

    def test_close_hides_the_window_and_keeps_the_batch_running(self) -> None:
        """닫기 버튼 동작은 창을 숨기기만 하고 컨트롤러 중지를 부르지 않는지 검증"""
        self.window.show()
        self.window.close()
        self.assertTrue(self.window.isHidden())
        self.assertEqual(self.controller.calls, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
