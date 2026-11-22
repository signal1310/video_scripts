"""재인코딩 진행 창이 작업 카드, 조작 신호, 중지 확인, 결과 목록, 닫기 숨김을 올바르게 다루는지 검증"""

import os
import tempfile
import unittest
from typing import cast
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QObject, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import QApplication, QMessageBox, QPushButton, QWidget

from src.core.encode.encode_batch_book import EncodeBatchBook
from src.core.encode.encode_progress import ProgressSample
from src.core.encode.encode_progress_values import ResultRow
from src.core.encode.encode_state import EncodeState
from src.gui.controllers.encode_controller import EncodeController
from src.gui.dialogs.encode_confirm_dialogs import ask_stop
from src.gui.dialogs.encode_progress_card import NO_POSITION_TIP
from src.gui.dialogs.encode_progress_window import (
    ALL_STOP_CONFIRM_TEXT,
    ITEM_STOP_CONFIRM_TEXT,
    PAUSE_ALL_TIP,
    RESUME_ALL_TIP,
    WINDOW_INITIAL_HEIGHT_PX,
    WINDOW_MIN_WIDTH_PX,
    BatchInfo,
    EncodeProgressWindow,
)
from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE
from src.service.encode.encode_events import (
    EVENT_LONG_PAUSE,
    EVENT_PHASE,
    EVENT_PROGRESS,
    EVENT_STATE,
    PHASE_MAIN,
    PHASE_RETRY,
    PHASE_SAMPLE_PREDICT,
    PHASE_SAMPLING,
    EncodeEvent,
    TuningPhase,
)
from src.service.encode.encode_job import jobs_from_evaluations
from tests._factories import make_eval, make_video
from tests._gui_helpers import release_modifier_keys

_APP = QApplication.instance() or QApplication([])
_NAME: str = "a.mp4"
_LARGE_FONT_PT: float = 13.0
_EXTRA_NAMES: tuple[str, ...] = ("b.mp4", "c.mp4")


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


class _FakeMedia:
    """영상 열기, 미리 보기, 교체 대기 조작 요청을 기록만 하는 대역"""

    def __init__(self) -> None:
        self.opened: list[str] = []
        self.previewed: list[str] = []
        self.retried: list[str] = []
        self.forfeited: list[str] = []
        self.leftovers: set[str] = set()
        self.revealed: list[str] = []
        self.trashed: list[str] = []
        self.compared: list[str] = []

    def open_file(self, path: str) -> None:
        self.opened.append(path)

    def preview_file(self, path: str) -> None:
        self.previewed.append(path)

    def reveal_leftover(self, source: str) -> bool:
        if source not in self.leftovers:
            return False
        self.revealed.append(source)
        return True

    def retry_replace(self, source: str) -> None:
        self.retried.append(source)

    def forfeit_replace(self, source: str) -> None:
        self.forfeited.append(source)

    def trash_leftover(self, source: str) -> None:
        self.trashed.append(source)

    def compare_files(self, source: str) -> None:
        self.compared.append(source)


def _info(single_panel: bool = False) -> BatchInfo:
    return BatchInfo(total=1, single_panel=single_panel, encoder_label="HEVC NVENC")


def _sample(out_time: float | None, speed: float, frame: int | None = None) -> ProgressSample:
    return ProgressSample(
        frame=frame,
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
        self.media = _FakeMedia()
        self.window = EncodeProgressWindow(
            cast("EncodeController", self.controller), self.book, self.media, None, confirm=self._confirm
        )
        self.window.start_batch(self.jobs, _info())

    def tearDown(self) -> None:
        self.window.deleteLater()
        self._tmp.cleanup()

    def test_theme_stylesheet_covers_the_confirmation_dialog_and_tooltips(self) -> None:
        """진행 창 QSS가 확인 대화상자 글자색, 툴팁, 메뉴 색을 앱 테마로 고정하는지 검증"""
        self.window.apply_theme(LIGHT_PALETTE)
        sheet = self.window.styleSheet()
        self.assertIn(f"EncodeProgressWindow {{\n    background: {LIGHT_PALETTE.bg_base};", sheet)
        self.assertIn(f"QMessageBox {{\n    background-color: {LIGHT_PALETTE.bg_card};", sheet)
        self.assertIn(f"QMessageBox QPushButton {{\n    background-color: {LIGHT_PALETTE.btn_bg};", sheet)
        self.assertIn(f"color: {LIGHT_PALETTE.text_primary};", sheet)
        self.assertIn("QMessageBox QPushButton:hover", sheet)
        self.assertIn("QToolTip {", sheet)
        self.assertIn("QMenu::item:selected", sheet)

        self.window.apply_theme(DARK_PALETTE)
        dark_sheet = self.window.styleSheet()
        self.assertIn(DARK_PALETTE.bg_base, dark_sheet)
        self.assertIn(DARK_PALETTE.btn_border, dark_sheet)
        self.assertNotIn(LIGHT_PALETTE.bg_base, dark_sheet)

    def test_overall_remaining_uses_running_progress_so_it_shrinks_as_the_encode_advances(self) -> None:
        """완료 항목이 없어도 진행 중 영상의 진행 비율로 전체 남은 시간을 추정하고, 진행이 늘면 남은 시간이 줄어듦"""
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, self.job_path, state=EncodeState.RUNNING))
        with mock.patch.object(self.window, "_now", return_value=100.0):
            self.controller.event_received.emit(
                EncodeEvent(EVENT_PROGRESS, 1, self.job_path, sample=_sample(out_time=30.0, speed=1.0), elapsed_sec=100.0)
            )
            self.window._refresh_all()
        self.assertEqual(self.window._remaining.text(), "남은 시간 01:40")
        with mock.patch.object(self.window, "_now", return_value=150.0):
            self.controller.event_received.emit(
                EncodeEvent(EVENT_PROGRESS, 1, self.job_path, sample=_sample(out_time=45.0, speed=1.0), elapsed_sec=150.0)
            )
            self.window._refresh_all()
        self.assertEqual(self.window._remaining.text(), "남은 시간 00:50")

    def _resumed_four_item_batch(self) -> list[str]:
        """이전 실행에서 첫 영상이 끝나고 둘째 영상이 건너뛰어진 상태로 네 영상 배치를 다시 시작하고 대상 경로를 돌려줌"""
        names = ["a.mp4", "b.mp4", "c.mp4", "d.mp4"]
        for name in names:
            with open(os.path.join(self.root, name), "wb") as handle:
                handle.write(b"source")
        jobs = jobs_from_evaluations([make_eval(video=make_video(filename=name)) for name in names], self.root)
        paths = [job.source_path for job in jobs]
        self.jobs = {job.source_path: job for job in jobs}
        self.book.start(paths)
        self.book.apply(paths[0], EncodeState.DONE, "")
        self.book.apply(paths[1], EncodeState.SKIPPED, "")
        self.window.start_batch(self.jobs, BatchInfo(total=4, single_panel=False, encoder_label="HEVC NVENC"))
        return paths

    def _run_first_part_of_the_third_item(self, path: str) -> None:
        """셋째 영상이 1분 길이 중 30초까지 1.25배로 인코딩되고, 경과 24초 시점의 화면 갱신을 마침"""
        self.book.apply(path, EncodeState.RUNNING)
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, path, state=EncodeState.RUNNING))
        with mock.patch.object(self.window, "_now", return_value=24.0):
            self.controller.event_received.emit(
                EncodeEvent(EVENT_PROGRESS, 1, path, sample=_sample(out_time=30.0, speed=1.25), elapsed_sec=24.0)
            )
            self.window._refresh_all()

    def test_speed_average_and_remaining_count_only_items_encoded_in_this_run(self) -> None:
        """평균 속도와 전체 남은 시간은 이번 실행에서 인코딩한 영상만 셈. 이전 실행에서 끝난 영상과 건너뜀은 빼야 함"""
        _done, _skipped, running, _pending = self._resumed_four_item_batch()
        self._run_first_part_of_the_third_item(running)
        self.assertTrue(self.window._totals.speed_text().endswith("평균 1.25배"))
        with mock.patch.object(self.window, "_now", return_value=100.0):
            self.window._refresh_all()
        self.assertEqual(self.window._remaining.text(), "남은 시간 05:00")

    def test_item_that_failed_before_encoding_changes_neither_speed_nor_remaining_time(self) -> None:
        """인코딩을 시작하지 않고 실패한 항목은 평균 속도의 처리한 길이와 전체 남은 시간의 대상 수에서 모두 빠짐"""
        _done, _skipped, running, pending = self._resumed_four_item_batch()
        self._run_first_part_of_the_third_item(running)
        self.book.apply(pending, EncodeState.FAILED, "")
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, pending, state=EncodeState.FAILED))
        with mock.patch.object(self.window, "_now", return_value=100.0):
            self.window._refresh_all()
        self.assertTrue(self.window._totals.speed_text().endswith("평균 0.30배"))
        self.assertEqual(self.window._remaining.text(), "남은 시간 01:40")

    def test_early_stopped_item_counts_only_the_position_it_reached(self) -> None:
        """6분 25초 영상이 40초 위치에서 조기 중단되면 평균 속도의 처리한 길이는 원본 길이가 아니라 40초"""
        self._use_long_item(duration=385.0)
        self.book.apply(self.job_path, EncodeState.RUNNING)
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, self.job_path, state=EncodeState.RUNNING))
        with mock.patch.object(self.window, "_now", return_value=50.0):
            self.controller.event_received.emit(
                EncodeEvent(EVENT_PROGRESS, 1, self.job_path, sample=_sample(out_time=40.0, speed=1.0), elapsed_sec=50.0)
            )
        self.book.apply(self.job_path, EncodeState.DISCARDED, "")
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, self.job_path, state=EncodeState.DISCARDED))
        with mock.patch.object(self.window, "_now", return_value=100.0):
            self.window._refresh_all()
        self.assertTrue(self.window._totals.speed_text().endswith("평균 0.40배"))

    def _use_long_item(self, duration: float) -> None:
        """테스트 영상 하나의 길이를 바꿔 같은 배치를 다시 시작함"""
        jobs = jobs_from_evaluations([make_eval(video=make_video(filename=_NAME, duration=duration))], self.root)
        self.jobs = {jobs[0].source_path: jobs[0]}
        self.window.start_batch(self.jobs, _info())

    def test_frame_only_item_counts_its_frame_position_in_the_average(self) -> None:
        """진행 위치 없이 프레임만 오는 영상(30fps, 900프레임에서 완료)은 처리한 길이를 30초로 셈"""
        self._use_long_item(duration=60.0)
        self.book.apply(self.job_path, EncodeState.RUNNING)
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, self.job_path, state=EncodeState.RUNNING))
        with mock.patch.object(self.window, "_now", return_value=50.0):
            self.controller.event_received.emit(
                EncodeEvent(EVENT_PROGRESS, 1, self.job_path, sample=_sample(None, 1.0, frame=900), elapsed_sec=50.0)
            )
        self.book.apply(self.job_path, EncodeState.DONE, "")
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, self.job_path, state=EncodeState.DONE))
        with mock.patch.object(self.window, "_now", return_value=100.0):
            self.window._refresh_all()
        self.assertTrue(self.window._totals.speed_text().endswith("평균 0.30배"))

    def test_running_frame_only_item_counts_its_frame_position_while_encoding(self) -> None:
        """진행 중인 프레임 전용 영상도 프레임 위치(450프레임, 15초)를 평균 속도의 처리한 길이에 넣음"""
        self._use_long_item(duration=60.0)
        self.book.apply(self.job_path, EncodeState.RUNNING)
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, self.job_path, state=EncodeState.RUNNING))
        with mock.patch.object(self.window, "_now", return_value=100.0):
            self.controller.event_received.emit(
                EncodeEvent(EVENT_PROGRESS, 1, self.job_path, sample=_sample(None, 1.0, frame=450), elapsed_sec=100.0)
            )
            self.window._refresh_all()
        self.assertTrue(self.window._totals.speed_text().endswith("평균 0.15배"))

    def test_item_skipped_during_this_run_leaves_the_remaining_count(self) -> None:
        """이번 실행 중 건너뜀이 된 영상은 전체 남은 시간의 대상 수에서 뺌"""
        _done, _skipped, running, pending = self._resumed_four_item_batch()
        self._run_first_part_of_the_third_item(running)
        self.book.apply(pending, EncodeState.SKIPPED, "")
        with mock.patch.object(self.window, "_now", return_value=100.0):
            self.window._refresh_all()
        self.assertEqual(self.window._remaining.text(), "남은 시간 01:40")

    def test_window_opens_on_the_progress_tab_and_the_result_tab_counts_finished_items(self) -> None:
        """진행 탭이 기본이고, 결과 탭은 결과 줄 수를 이름에 붙이며, 전환하면 결과 목록이 보임"""
        self.assertEqual(self.window._view_switch.currentIndex(), 0)
        self.assertEqual(self.window._pages.currentIndex(), 0)
        self.assertEqual(self.window._view_switch.tabText(1), "결과 0")
        self.book.apply(self.job_path, EncodeState.DONE, "")
        self.window.refresh_rows()
        self.assertEqual(self.window._view_switch.tabText(1), "결과 1")
        self.window._view_switch.setCurrentIndex(1)
        self.assertEqual(self.window._pages.currentIndex(), 1)

    def test_idle_notice_shows_only_while_no_card_is_running(self) -> None:
        """진행 중인 카드가 없을 때만 빈 상태 안내를 보이고, 카드가 생기면 숨김"""
        self.assertFalse(self.window._idle.isHidden())
        self.window._on_state(self.job_path, EncodeState.RUNNING)
        self.assertTrue(self.window._idle.isHidden())
        self.window._on_state(self.job_path, EncodeState.DONE)
        self.assertFalse(self.window._idle.isHidden())

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
        self.assertEqual(card._values[12].text(), "2100kbps")
        self.assertEqual(card._bar.value(), 500)
        self.assertEqual(len(card.series.points()), 1)

    def test_phase_events_update_chip_card_and_retry_notice(self) -> None:
        """샘플 확인, 본 인코딩, 재시도 단계 이벤트가 칩 배지와 카드 안내, 목표 품질값을 갱신하는지 검증"""
        self._start_running()
        card = self.window._cards[self.job_path]
        self.controller.event_received.emit(
            EncodeEvent(
                EVENT_PHASE,
                1,
                self.job_path,
                phase=TuningPhase(
                    stage=PHASE_SAMPLE_PREDICT,
                    segment_done=2,
                    segment_total=5,
                    baseline_quality=22.0,
                    current_quality=22.0,
                ),
            )
        )
        self.assertEqual(card._values[0].text(), "샘플 확인 중 (구간 2/5)")
        self.assertEqual(card._values[9].text(), "22.0")
        chip = self.window._tabs._entries[0][1]
        self.assertEqual(chip._percent.text(), "샘플")
        self.controller.event_received.emit(
            EncodeEvent(
                EVENT_PHASE,
                1,
                self.job_path,
                phase=TuningPhase(
                    stage=PHASE_SAMPLING,
                    sample_pass=1,
                    sample_total=2,
                    segment_done=3,
                    segment_total=5,
                    baseline_quality=22.0,
                    current_quality=22.0,
                ),
            )
        )
        self.assertEqual(card._values[0].text(), "샘플 1/2 확인 중 (구간 3/5)")
        self.assertEqual(card._values[9].text(), "계산 중 (기준 22.0)")
        self.assertTrue(card._retry_notice.isHidden())

        self.controller.event_received.emit(
            EncodeEvent(
                EVENT_PHASE,
                1,
                self.job_path,
                phase=TuningPhase(
                    stage=PHASE_RETRY,
                    baseline_quality=22.0,
                    current_quality=27.6,
                    previous_quality=26.9,
                    retry_count=1,
                ),
            )
        )
        self.assertFalse(card._retry_notice.isHidden())
        self.assertIn("26.9", card._retry_notice.text())
        self.assertEqual(card._values[9].text(), "27.6 (기준 22.0)")

        self.controller.event_received.emit(
            EncodeEvent(
                EVENT_PHASE,
                1,
                self.job_path,
                phase=TuningPhase(stage=PHASE_MAIN, baseline_quality=22.0, current_quality=26.6),
            )
        )
        self.assertTrue(card._retry_notice.isHidden())
        self.assertEqual(card._values[9].text(), "26.6 (기준 22.0)")

    def test_long_pause_notice_and_item_pause_signal_update_the_card(self) -> None:
        """장시간 안내 이벤트가 안내를 보이게 하고, 개별 일시중지 신호가 카드 상태와 기록을 바꾸는지 검증"""
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
        """영상 일시중지와 재개는 바로 호출하고, 중지는 확인 창의 답을 따르는지 검증"""
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
        """전체 일시중지와 재개, 현재 영상 후 중지, 전체 중지가 배치 상태에 맞게 호출되는지 검증"""
        self._start_running()
        self.window._pause_all_btn.click()
        self.assertEqual(self.controller.calls[-1], "pause_all")
        self.controller.pause_changed.emit(True)
        self.assertEqual(self.window._pause_all_btn.toolTip(), RESUME_ALL_TIP)
        self.assertTrue(self.window._cards[self.job_path].is_paused)
        self.window._pause_all_btn.click()
        self.assertEqual(self.controller.calls[-1], "resume_all")
        self.controller.pause_changed.emit(False)
        self.window._after_btn.click()
        self.assertEqual(self.controller.calls[-1], "stop_after")
        self.window._stop_all_btn.click()
        self.assertEqual(self.asked[-1], ALL_STOP_CONFIRM_TEXT)
        self.assertNotIn("cancel_all", self.controller.calls)
        self.answer = True
        self.window._stop_all_btn.click()
        self.assertEqual(self.controller.calls[-1], "cancel_all")

    def test_long_pause_link_stops_after_current_and_resumes_all_when_an_item_is_paused(self) -> None:
        """개별 일시중지 중 장시간 안내 링크도 현재 영상 후 중지를 예약하고 멈춘 영상을 전체 재개하는지 검증"""
        self._start_running()
        card = self.window._cards[self.job_path]
        self.controller.item_pause_changed.emit(self.job_path, True)
        card._notice.linkActivated.emit("stop-after")
        self.assertEqual(self.controller.calls, ["stop_after", "resume_all"])

    def test_header_stop_after_button_resumes_all_when_batch_or_item_is_paused(self) -> None:
        """머리글 현재 영상 후 중지 버튼은 전체 또는 개별 일시중지 중에 예약과 전체 재개를 함께 부르고, 멈춘 것이 없으면 예약만 하는지 검증"""
        self._start_running()
        self.window._after_btn.click()
        self.assertEqual(self.controller.calls, ["stop_after"])
        self.controller.pause_changed.emit(True)
        self.window._after_btn.click()
        self.assertEqual(self.controller.calls[-2:], ["stop_after", "resume_all"])
        self.controller.pause_changed.emit(False)
        self.controller.item_pause_changed.emit(self.job_path, True)
        self.window._after_btn.click()
        self.assertEqual(self.controller.calls[-2:], ["stop_after", "resume_all"])

    def test_next_batch_starts_with_the_pause_button_back_in_pause_state(self) -> None:
        """전체 일시중지 상태로 배치가 끝나면 전체 일시중지 버튼이 일시중지 툴팁으로 돌아가고, 다음 배치에도 그대로인지 검증"""
        self._start_running()
        self.controller.pause_changed.emit(True)
        self.controller.batch_finished.emit(object())
        self.assertEqual(self.window._pause_all_btn.toolTip(), PAUSE_ALL_TIP)
        self.window.start_batch(self.jobs, _info())
        self.assertEqual(self.window._pause_all_btn.toolTip(), PAUSE_ALL_TIP)

    def test_long_pause_link_resumes_the_whole_batch_when_it_is_paused(self) -> None:
        """전체 일시중지 중 장시간 안내 링크는 현재 영상 후 중지를 예약하고 전체를 재개하는지 검증"""
        self._start_running()
        card = self.window._cards[self.job_path]
        self.controller.pause_changed.emit(True)
        card._notice.linkActivated.emit("stop-after")
        self.assertEqual(self.controller.calls, ["stop_after", "resume_all"])

    def test_batch_pause_disables_item_buttons_until_the_batch_resumes(self) -> None:
        """전체 일시중지 중에는 카드의 일시중지 버튼이 꺼지고 전체 재개 뒤 다시 켜지는지 검증"""
        self._start_running()
        card = self.window._cards[self.job_path]
        self.controller.pause_changed.emit(True)
        self.assertFalse(card._pause_btn.isEnabled())
        card._pause_btn.click()
        self.assertEqual(self.controller.calls, [])
        self.controller.pause_changed.emit(False)
        self.assertTrue(card._pause_btn.isEnabled())

    def test_card_started_during_a_batch_pause_starts_paused_and_locked(self) -> None:
        """전체 일시중지 중에 시작한 영상 카드는 멈춘 상태로 나타나고 개별 버튼이 꺼져 있는지 검증"""
        self.controller.pause_changed.emit(True)
        self._start_running()
        card = self.window._cards[self.job_path]
        self.assertTrue(card.is_paused)
        self.assertFalse(card._pause_btn.isEnabled())

    def test_finished_item_moves_to_results_and_batch_finish_updates_the_header(self) -> None:
        """끝난 항목이 카드에서 빠져 결과 목록에 상태와 함께 나오고, 배치 종료가 머리글을 바꾸는지 검증"""
        self._start_running()
        self.book.apply(self.job_path, EncodeState.DONE)
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, self.job_path, state=EncodeState.DONE))
        self.assertNotIn(self.job_path, self.window._cards)
        self.assertEqual(self.window._results.row_count(), 1)
        self.assertEqual(self.window._results.status_text(0), "완료")
        self.assertEqual(self.window._header.text(), "전체 1/1 처리")
        self.controller.batch_finished.emit(object())
        self.assertEqual(self.window._header.text(), "재인코딩을 마쳤습니다. 완료 1개")

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
        self.window.start_batch(self.jobs, _info())
        self.assertTrue(self.window._stop_all_btn.isEnabled())

    def test_stopped_batch_says_stopped_and_window_opens_at_initial_size(self) -> None:
        """전체 중지로 끝나면 머리글이 중지 문구가 되고, 창은 폭만 고정한 초기 크기로 열리는지 검증"""
        self.assertEqual(self.window.minimumWidth(), WINDOW_MIN_WIDTH_PX)
        self.assertEqual(self.window.size(), QSize(WINDOW_MIN_WIDTH_PX, WINDOW_INITIAL_HEIGHT_PX))
        self._start_running()
        self.answer = True
        self.window._stop_all_btn.click()
        self.controller.batch_finished.emit(object())
        self.assertEqual(self.window._header.text(), "재인코딩을 중지했습니다. 완료 0개")

    def test_large_font_window_is_tall_enough_for_the_card_values(self) -> None:
        """글꼴이 클 때 창 높이가 레이아웃 최소 높이 이상이어서 카드의 수치 칸이 눌리지 않는지 검증"""
        self._use_large_font()
        for path in (self.job_path, *(os.path.join(self.root, name) for name in _EXTRA_NAMES)):
            self.window._add_card(path)
        self.window.show()
        self.addCleanup(self.window.hide)
        _APP.processEvents()
        layout = self.window.layout()
        assert layout is not None
        self.assertGreaterEqual(self.window.height(), layout.totalMinimumSize().height())
        for card in self.window._cards.values():
            for value in card._values:
                self.assertGreaterEqual(value.height(), value.sizeHint().height())
        for _, chip in self.window._tabs._entries:
            self.assertGreaterEqual(chip.width(), chip.minimumSizeHint().width())

    def test_reopened_window_keeps_the_size_the_user_set(self) -> None:
        """숨겼다 다시 여는 창이 사용자가 정한 크기를 유지하는지 검증"""
        self.window.show()
        self.window.resize(520, 700)
        self.window.close()
        self.assertFalse(self.window.isVisible())
        self.window.show()
        _APP.processEvents()
        self.addCleanup(self.window.hide)
        self.assertEqual(self.window.size(), QSize(520, 700))

    def _use_large_font(self) -> None:
        """테스트 중 앱 글꼴을 키우고 끝나면 원래 글꼴로 되돌림"""
        original = QApplication.font()
        self.addCleanup(QApplication.setFont, original)
        large = QApplication.font()
        large.setPointSizeF(_LARGE_FONT_PT)
        QApplication.setFont(large)

    def test_each_running_item_gets_a_tab_named_by_its_file(self) -> None:
        """진행 중인 영상마다 파일 이름의 탭이 생기고, 끝나면 탭이 사라지는지 검증"""
        self._start_running()
        self.assertEqual(self.window._tabs.chip_count(), 1)
        chip = self.window._tabs._entries[0][1]
        self.assertEqual(chip.toolTip(), f"1. {_NAME}")
        self.book.apply(self.job_path, EncodeState.DONE)
        self.controller.event_received.emit(EncodeEvent(EVENT_STATE, 1, self.job_path, state=EncodeState.DONE))
        self.assertEqual(self.window._tabs.chip_count(), 0)
        self.assertTrue(self.window._tabs.isHidden())

    def test_new_batch_clears_the_previous_cards(self) -> None:
        """새 배치를 시작하면 이전 카드가 지워지는지 검증"""
        self._start_running()
        self.window.start_batch(self.jobs, _info())
        self.assertEqual(self.window._cards, {})

    def test_ask_stop_is_false_when_the_dialog_is_closed_without_a_choice(self) -> None:
        """확인 창을 선택 없이 닫으면 중지로 보지 않는지 검증"""
        with mock.patch.object(QMessageBox, "exec", return_value=0):
            self.assertFalse(ask_stop(self.window, ITEM_STOP_CONFIRM_TEXT))

    def _banner_button(self, accessible_name: str) -> QPushButton:
        buttons = [b for b in self.window._banner.findChildren(QPushButton) if b.accessibleName() == accessible_name]
        self.assertEqual(len(buttons), 1)
        return buttons[0]

    def test_close_pending_banner_shows_and_cancel_button_requests_the_cancel(self) -> None:
        """종료 예약 배너가 예약 상태에 따라 보이고, 종료 예약 취소 버튼이 취소 신호를 보내는지 검증"""
        received: list[bool] = []
        self.window.close_cancel_requested.connect(lambda: received.append(True))
        self.assertTrue(self.window._banner.isHidden())
        self.window.set_close_pending(True)
        self.assertFalse(self.window._banner.isHidden())
        self._banner_button("종료 예약 취소").click()
        self.assertEqual(received, [True])
        self.window.set_close_pending(False)
        self.assertTrue(self.window._banner.isHidden())

    def test_banner_stop_now_button_confirms_then_cancels_the_batch(self) -> None:
        """배너의 즉시 중지로 바꾸기는 확인 창을 거친 뒤 전체 중지를 부르는지 검증"""
        self._start_running()
        self.window.set_close_pending(True)
        self._banner_button("즉시 중지로 바꾸기").click()
        self.assertEqual(self.asked[-1], ALL_STOP_CONFIRM_TEXT)
        self.assertNotIn("cancel_all", self.controller.calls)
        self.answer = True
        self._banner_button("즉시 중지로 바꾸기").click()
        self.assertEqual(self.controller.calls[-1], "cancel_all")

    def test_cancelled_stop_after_lets_the_finished_header_read_completed(self) -> None:
        """현재 영상 후 중지 예약을 취소한 뒤 배치가 끝나면 끝난 머리글이 마쳤습니다 문구인지 검증"""
        self._start_running()
        self.window._after_btn.click()
        self.window.clear_stop_after_request()
        self.controller.batch_finished.emit(object())
        self.assertEqual(self.window._header.text(), "재인코딩을 마쳤습니다. 완료 0개")

    def test_close_hides_the_window_and_keeps_the_batch_running(self) -> None:
        """닫기 버튼 동작은 창을 숨기기만 하고 컨트롤러 중지를 부르지 않는지 검증"""
        self.window.show()
        self.window.close()
        self.assertTrue(self.window.isHidden())
        self.assertEqual(self.controller.calls, [])


    def test_single_panel_hides_the_tab_bar_and_multi_panel_keeps_it(self) -> None:
        """동시 실행이 1개면 탭 막대를 숨기고, 여러 개면 탭 막대를 보이는 설정으로 두는지 검증"""
        chip_row = self.window._tabs._chip_host
        self.window.start_batch(self.jobs, _info(single_panel=True))
        self.assertTrue(chip_row.isHidden())
        self.window.start_batch(self.jobs, _info(single_panel=False))
        self.assertFalse(chip_row.isHidden())

    def test_missing_position_shows_no_value_and_the_frame_based_reason(self) -> None:
        """진행 위치가 없는 영상은 진행 위치를 정보 없음으로 두고, 막대 툴팁에 프레임 기준 사유를 보이는지 검증"""
        self._start_running()
        card = self.window._cards[self.job_path]
        sample = ProgressSample(
            frame=900,
            fps=None,
            quality=22.0,
            bitrate_kbps=None,
            total_size_bytes=None,
            out_time_sec=None,
            speed=None,
            finished=False,
        )
        self.controller.event_received.emit(EncodeEvent(EVENT_PROGRESS, 1, self.job_path, sample=sample))
        self.assertEqual(card._values[0].text(), "정보 없음")
        self.assertEqual(card._bar.toolTip(), NO_POSITION_TIP)
        self.assertEqual(card._values[2].text(), "900")

    def test_speed_summary_follows_running_items_and_saved_totals_follow_results(self) -> None:
        """전체 속도는 진행 중 영상의 배속을, 완료분 절약은 결과 행의 실제 크기 차이를 보여 주는지 검증"""
        self._start_running()
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
        self.window._refresh_all()
        self.assertTrue(self.window._totals.speed_text().startswith("현재 1.25배, 평균 "))
        self.assertEqual(self.window._totals.saving_text(), "실제 정보 없음, 예상 정보 없음")
        self.book.apply(self.job_path, EncodeState.DONE)
        self.window.set_result(
            ResultRow(
                source_path=self.job_path,
                output_path=os.path.join(self.root, "_재인코딩결과", _NAME),
                source_bytes=100 * 1024 * 1024,
                output_bytes=40 * 1024 * 1024,
                expected_delta_mb=None,
            )
        )
        self.assertEqual(self.window._totals.saving_text(), "실제 60.0 MB, 예상 정보 없음")
        self.assertEqual(self.window._results.name_text(0), _NAME)
        result_item = self.window._results._list.item(0)
        assert result_item is not None
        result_widget = self.window._results._list.itemWidget(result_item)
        assert result_widget is not None
        self.assertEqual(result_widget.toolTip(), f"결과: {os.path.join('_재인코딩결과', _NAME)}")
        self.assertIn("→", self.window._results.size_text(0))

    def test_result_row_actions_forward_retry_and_forfeit_for_the_source(self) -> None:
        """교체 대기 행의 재시도와 교체 포기 신호는 원본 경로와 함께 미디어 조정자로 전달됨"""
        self.window._results.retry_requested.emit(self.job_path)
        self.window._results.forfeit_requested.emit(self.job_path)
        self.assertEqual(self.media.retried, [self.job_path])
        self.assertEqual(self.media.forfeited, [self.job_path])

    def test_open_and_preview_use_the_result_file_once_the_item_is_done(self) -> None:
        """완료된 항목의 더블클릭과 스페이스바는 결과 파일을 열고, 진행 중 항목은 원본을 씀"""
        output = os.path.join(self._tmp.name, "out.mp4")
        self.book.apply(self.job_path, EncodeState.RUNNING)
        self.window._results.activated.emit(self.job_path)
        self.assertEqual(self.media.opened, [self.job_path])
        self.book.apply(self.job_path, EncodeState.DONE)
        self.window.set_result(ResultRow(self.job_path, output, 2000, 800, None))
        self.window._results.activated.emit(self.job_path)
        self.window._results.preview_requested.emit(self.job_path)
        self.assertEqual(self.media.opened, [self.job_path, output])
        self.assertEqual(self.media.previewed, [output])

    def test_leftover_rows_reveal_their_part_on_double_click_and_space_instead_of_the_source(self) -> None:
        """임시 파일이 남은 행(교체 대기, 임시 파일 실패)은 더블클릭과 스페이스 모두 임시 파일 위치 선택으로 넘어가고, 원본은 열지 않음"""
        self.media.leftovers.add(self.job_path)
        self.window._results.activated.emit(self.job_path)
        self.window._results.preview_requested.emit(self.job_path)
        self.assertEqual(self.media.revealed, [self.job_path, self.job_path])
        self.assertEqual(self.media.opened, [])
        self.assertEqual(self.media.previewed, [])

    def test_result_row_reveal_and_trash_actions_reach_the_media(self) -> None:
        """결과 행의 위치 열기 신호는 임시 파일 위치 선택으로, 휴지통 신호는 같은 자리 임시 파일 정리로 넘어감"""
        self.media.leftovers.add(self.job_path)
        self.window._results.reveal_requested.emit(self.job_path)
        self.window._results.trash_requested.emit(self.job_path)
        self.assertEqual(self.media.revealed, [self.job_path])
        self.assertEqual(self.media.trashed, [self.job_path])

    def test_result_row_compare_action_reaches_the_media(self) -> None:
        """결과 행의 화질 비교 신호는 원본 경로와 함께 미디어 조정자로 전달됨"""
        self.window._results.compare_requested.emit(self.job_path)
        self.assertEqual(self.media.compared, [self.job_path])

    def test_shift_space_on_a_result_row_opens_the_compare_window(self) -> None:
        """결과 목록에서 Shift+Space를 누르면 선택한 행의 원본으로 화질 비교를 요청하는지 검증"""
        self.book.apply(self.job_path, EncodeState.DONE)
        self.window.refresh_rows()
        self.window._view_switch.setCurrentIndex(1)
        self.window._results._list.setCurrentRow(0)
        QApplication.sendEvent(
            self.window._results._list,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier),
        )
        self.assertEqual(self.media.compared, [self.job_path])
        self.assertEqual(self.media.previewed, [])
        release_modifier_keys()

    def test_shift_space_on_the_progress_card_opens_the_compare_window(self) -> None:
        """진행 카드가 보이는 상태에서 Shift+Space를 누르면 그 영상의 화질 비교를 요청하는지 검증"""
        self._start_running()
        QApplication.sendEvent(
            self.window,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.ShiftModifier),
        )
        self.assertEqual(self.media.compared, [self.job_path])
        release_modifier_keys()

    def test_refresh_rows_updates_the_result_list_without_an_event(self) -> None:
        """조정자가 기록을 다시 읽은 뒤 refresh_rows를 부르면 교체 대기 행이 나타남"""
        self.book.apply(self.job_path, EncodeState.REPLACE_PENDING, "교체 실패")
        self.window.refresh_rows()
        self.assertEqual(self.window._results.row_count(), 1)
        self.assertEqual(len(self.window._results.action_buttons(0)), 4)

    def test_empty_progress_event_keeps_previous_last_position(self) -> None:
        """진행 위치와 프레임이 모두 없는 이벤트는 이전 도달 위치를 0으로 덮지 않고, 처음 받으면 0으로 둠"""
        self._start_running()
        empty = ProgressSample(
            frame=None,
            fps=None,
            quality=None,
            bitrate_kbps=None,
            total_size_bytes=None,
            out_time_sec=None,
            speed=None,
            finished=False,
        )
        self.controller.event_received.emit(EncodeEvent(EVENT_PROGRESS, 1, self.job_path, sample=empty))
        self.assertEqual(self.window._last_positions[self.job_path], 0.0)

        self.controller.event_received.emit(
            EncodeEvent(EVENT_PROGRESS, 1, self.job_path, sample=_sample(out_time=30.0, speed=1.0))
        )
        self.assertEqual(self.window._last_positions[self.job_path], 30.0)
        self.controller.event_received.emit(EncodeEvent(EVENT_PROGRESS, 1, self.job_path, sample=empty))
        self.assertEqual(self.window._last_positions[self.job_path], 30.0)

    def test_result_row_widgets_all_have_parent_and_same_window(self) -> None:
        """결과 목록 행 위젯과 그 하위 위젯이 모두 명시적 부모와 진행 창 최상위 창을 가지는지 검증"""
        self.book.apply(self.job_path, EncodeState.REPLACE_PENDING, "교체 실패")
        self.window.refresh_rows()
        item = self.window._results._list.item(0)
        assert item is not None
        row_widget = self.window._results._list.itemWidget(item)
        assert row_widget is not None
        self.assertIsNotNone(row_widget.parentWidget())
        self.assertIs(row_widget.window(), self.window.window())
        for child in row_widget.findChildren(QWidget):
            self.assertIsNotNone(child.parentWidget())
            self.assertIs(child.window(), self.window.window())


if __name__ == "__main__":
    unittest.main(verbosity=2)
