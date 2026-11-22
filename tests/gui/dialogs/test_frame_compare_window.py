"""화질 비교 창 검증: 세션 적재, 비교 대상 전환, 점수 줄, 3모드, 단축키, 로딩과 오류, 스레드 정리

- 추출기는 가짜로 바꿔 실제 미디어와 FFmpeg 없이 확인
"""

import os
import tempfile
import threading
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QBuffer, QEvent, QIODevice, QPoint, Qt
from PyQt6.QtGui import QColor, QImage, QKeyEvent
from PyQt6.QtWidgets import QApplication

from src.core.analysis.frame_analysis import TierScore
from src.core.analysis.frame_compare import (
    ComparePlan,
    FrameSource,
    resolve_compare_plan,
    result_target,
)
from src.gui.dialogs import frame_compare_layout as layout
from src.gui.dialogs.frame_compare_canvas import MODE_SIDE, MODE_SPLIT, MODE_TOGGLE
from src.gui.dialogs.frame_compare_window import (
    CompareSession,
    ExtractOutcome,
    FrameCompareWindow,
    FrameCompareWorker,
    run_extract_body,
)
from src.gui.styles.palettes import LIGHT_PALETTE
from src.service.frame_compare_service import (
    ExtractedFrames,
    FrameCompareExecutionError,
    FrameCompareFileMissing,
)
from tests._gui_helpers import GuiTestCase, release_modifier_keys

_APP = QApplication.instance() or QApplication([])
_WAIT_SECONDS: float = 5.0


def _png(color: str) -> bytes:
    image = QImage(40, 20, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


_ORIGINAL_PNG: bytes = _png("#FF0000")
_COMPARED_PNG: bytes = _png("#0000FF")


class _Extractor:
    """호출을 기록하고 정해진 PNG나 오류를 돌려주는 가짜 추출기"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, float, int, int]] = []
        self.failure: Exception | None = None

    def extract_pair(
        self,
        original: FrameSource,
        compared: FrameSource,
        *,
        time_sec: float,
        frame_width: int,
        frame_height: int,
        cancel_event: threading.Event,
    ) -> ExtractedFrames:
        self.calls.append((original.path, compared.path, time_sec, frame_width, frame_height))
        if self.failure is not None:
            raise self.failure
        return ExtractedFrames(original_png=_ORIGINAL_PNG, compared_png=_COMPARED_PNG)


def _wait(window: FrameCompareWindow) -> None:
    deadline = time.monotonic() + _WAIT_SECONDS
    while window._worker is not None and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.01)
    _APP.processEvents()


def _press(widget: FrameCompareWindow, key: Qt.Key, modifier: Qt.KeyboardModifier = Qt.KeyboardModifier.NoModifier) -> None:
    """창에 키 누름 사건을 직접 보내 수식키 상태를 남기지 않음"""
    QApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, modifier))


class _WindowCase(GuiTestCase):
    """임시 미디어 파일과 비교 계획, 세션을 준비하는 기반"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.source = os.path.join(self.tmp.name, "video.mp4")
        self.result = os.path.join(self.tmp.name, "video_out.mp4")
        for path in (self.source, self.result):
            with open(path, "wb") as handle:
                handle.write(b"x")
        self.extractor = _Extractor()
        self.window = FrameCompareWindow(self.extractor, lambda: 5, None)
        self.addCleanup(self.window.deleteLater)
        self.window.show()
        _APP.processEvents()

    def session(self, *, with_result: bool = True, scores: tuple[TierScore, ...] = ()) -> CompareSession:
        result = (
            result_target(self.result, width=1920, height=1080, size_bytes=580 * 1024 * 1024)
            if with_result
            else None
        )
        plan = resolve_compare_plan(
            source_path=self.source,
            width=3840,
            height=2160,
            size_bytes=1024**3,
            result=result,
            candidate_tier="1080p",
        )
        return CompareSession(
            plan=plan,
            duration_sec=100.0,
            fps=30.0,
            filename="video.mp4",
            position_sec=10.0,
            scores=scores,
            width=3840,
            height=2160,
        )

    def load(self, *, with_result: bool = True, scores: tuple[TierScore, ...] = ()) -> None:
        self.window.load_session(self.session(with_result=with_result, scores=scores))
        _wait(self.window)


class LoadSessionTests(_WindowCase):
    """세션 적재 뒤 제목, 메타, 비교 대상, 프레임 상태"""

    def test_default_target_is_result_and_frames_load(self) -> None:
        self.load()
        self.assertEqual(self.window._toolbar.filename.text(), "video.mp4")
        self.assertIn("video.mp4", self.window.windowTitle())
        self.assertEqual(self.window._toolbar.target_button.text(), "결과")
        self.assertIn("원본 3840x2160", self.window._meta.original.text())
        self.assertIn("결과 1920x1080", self.window._meta.target.text())
        self.assertEqual(self.window._meta.time.text(), "시점 00:10.000")
        labels = [action.text() for action in self.window._toolbar.target_menu.actions()]
        self.assertEqual(labels[0], "결과")
        self.assertIn("1080p 축소 비교", labels)
        self.assertIsNotNone(self.window._canvas._original)
        self.assertTrue(self.window._stage.overlay.isHidden())
        self.assertEqual(self.extractor.calls[0][2], 10.0)
        self.assertEqual(self.extractor.calls[0][3:], (3840, 2160))

    def test_cached_score_is_shown_only_for_its_tier(self) -> None:
        scores = (TierScore(tier="1080p", ssim=0.9983, vmaf=98.7),)
        self.load(scores=scores)
        self.assertTrue(self.window._score_label.isHidden())
        self.window.set_target("1080p")
        _wait(self.window)
        self.assertFalse(self.window._score_label.isHidden())
        self.assertEqual(self.window._score_label.text(), "화질 분석 1080p: VMAF 98.7, SSIM 0.9983")
        self.assertIn("VMAF는 100에 가까울수록", self.window._score_label.toolTip())
        self.assertEqual(self.window._toolbar.target_button.text(), "1080p 축소 비교")

    def test_plan_without_target_shows_error_overlay(self) -> None:
        plan = ComparePlan(
            original=FrameSource(key="원본", path=self.source, label="원본"),
            targets=(),
            default_key=None,
            reason="비교할 대상을 찾지 못했습니다.",
        )
        session = CompareSession(plan=plan, duration_sec=100.0, fps=30.0, filename="video.mp4")
        self.window.load_session(session)
        self.assertFalse(self.window._stage.overlay.isHidden())
        self.assertEqual(self.window._stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(self.window._stage.body.text(), "비교할 대상을 찾지 못했습니다.")

    def test_missing_original_uses_the_approved_reason(self) -> None:
        os.remove(self.source)
        session = self.session()
        self.window.load_session(session)
        self.assertEqual(self.window._stage.body.text(), "원본 파일이 없어 비교할 수 없습니다.")


class InteractionTests(_WindowCase):
    """단축키, 모드 전환, 배율, 시점 이동"""

    def test_mode_keys_switch_three_modes(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_2)
        self.assertEqual(self.window._canvas.mode(), MODE_TOGGLE)
        self.assertTrue(self.window._toolbar.mode_buttons[MODE_TOGGLE].isChecked())
        _press(self.window, Qt.Key.Key_3)
        self.assertEqual(self.window._canvas.mode(), MODE_SIDE)
        _press(self.window, Qt.Key.Key_1)
        self.assertEqual(self.window._canvas.mode(), MODE_SPLIT)

    def test_c_key_toggles_original_only_in_toggle_mode(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_C)
        self.assertFalse(self.window._canvas.original_pinned())
        _press(self.window, Qt.Key.Key_2)
        _press(self.window, Qt.Key.Key_C)
        self.assertTrue(self.window._canvas.original_pinned())
        _press(self.window, Qt.Key.Key_C)
        self.assertFalse(self.window._canvas.original_pinned())

    def test_zero_and_plus_minus_change_zoom_and_button_checks(self) -> None:
        self.load()
        self.window.set_zoom_preset("1")
        _press(self.window, Qt.Key.Key_Plus)
        self.assertAlmostEqual(self.window._canvas.scale(), 1.25)
        self.assertFalse(any(button.isChecked() for button in self.window._toolbar.zoom_buttons.values()))
        _press(self.window, Qt.Key.Key_Equal)
        self.assertAlmostEqual(self.window._canvas.scale(), 1.25 * 1.25)
        _press(self.window, Qt.Key.Key_Minus)
        self.assertAlmostEqual(self.window._canvas.scale(), 1.25)
        _press(self.window, Qt.Key.Key_0)
        self.assertTrue(self.window._toolbar.zoom_buttons["fit"].isChecked())

    def test_arrow_keys_seek_by_preview_interval_and_shift_is_six_times(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_Right)
        self.assertAlmostEqual(self.window._position_sec, 15.0)
        _press(self.window, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.window._position_sec, 45.0)
        _press(self.window, Qt.Key.Key_Left)
        self.assertAlmostEqual(self.window._position_sec, 40.0)
        _press(self.window, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
        self.assertAlmostEqual(self.window._position_sec, 10.0)
        release_modifier_keys()

    def test_comma_and_period_step_one_frame(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_Period)
        self.assertAlmostEqual(self.window._position_sec, 10.0 + 1 / 30)
        _press(self.window, Qt.Key.Key_Comma)
        self.assertAlmostEqual(self.window._position_sec, 10.0)

    def test_space_closes_the_open_target_menu_first_then_the_window(self) -> None:
        self.load()
        self.window._toolbar.target_menu.popup(QPoint(10, 10))
        _APP.processEvents()
        self.assertTrue(self.window._toolbar.target_menu.isVisible())
        _press(self.window, Qt.Key.Key_Space)
        _APP.processEvents()
        self.assertFalse(self.window._toolbar.target_menu.isVisible())
        self.assertFalse(self.window.isHidden())
        _press(self.window, Qt.Key.Key_Space)
        self.assertTrue(self.window.isHidden())

    def test_mark_chip_moves_to_its_ratio_of_the_duration(self) -> None:
        self.load()
        self.window._timeline.mark_buttons[2].click()
        self.assertAlmostEqual(self.window._position_sec, 30.0)
        self.assertEqual(self.window._meta.time.text(), "시점 00:30.000")
        self.assertTrue(self.window._timeline.mark_buttons[2].isChecked())

    def test_escape_closes_the_window(self) -> None:
        self.load()
        _press(self.window, Qt.Key.Key_Escape)
        self.assertTrue(self.window.isHidden())


class ErrorAndThreadTests(_WindowCase):
    """원인별 오류 표시, 지연 결과 폐기와 종료 시 취소"""

    def test_file_missing_and_execution_errors_use_different_bodies(self) -> None:
        self.extractor.failure = FrameCompareFileMissing("파일이 이동되었거나 삭제되었는지 확인해 주세요.")
        self.load()
        self.assertEqual(self.window._stage.title.text(), layout.ERROR_TITLE)
        self.assertEqual(self.window._stage.body.text(), "파일이 이동되었거나 삭제되었는지 확인해 주세요.")
        self.extractor.failure = FrameCompareExecutionError("FFmpeg로 프레임을 추출하지 못했습니다: boom")
        self.window.set_time(20.0)
        self.window._start_pending_extract()
        _wait(self.window)
        self.assertIn("boom", self.window._stage.body.text())
        self.extractor.failure = None
        self.window.set_time(30.0)
        self.window._start_pending_extract()
        _wait(self.window)
        self.assertTrue(self.window._stage.overlay.isHidden())

    def test_loading_overlay_uses_the_approved_text(self) -> None:
        self.load()
        self.window._show_loading()
        self.assertFalse(self.window._stage.overlay.isHidden())
        self.assertEqual(self.window._stage.title.text(), "프레임을 불러오는 중...")
        self.assertEqual(self.window._stage.body.text(), "")

    def test_stale_request_result_is_discarded(self) -> None:
        self.load()
        before = self.window._canvas._original
        self.window._request_id += 1
        stale = ExtractOutcome(
            self.window._request_id - 1,
            ExtractedFrames(original_png=_ORIGINAL_PNG, compared_png=_COMPARED_PNG),
            time_sec=1.0,
        )
        self.window._on_worker_completed(stale)
        self.assertIs(self.window._canvas._original, before)
        self.window._on_worker_completed("잘못된 값")
        self.assertIs(self.window._canvas._original, before)

    def test_close_cancels_a_running_extraction(self) -> None:
        started = threading.Event()
        cancelled: list[bool] = []

        class _Blocking:
            def extract_pair(
                self,
                original: FrameSource,
                compared: FrameSource,
                *,
                time_sec: float,
                frame_width: int,
                frame_height: int,
                cancel_event: threading.Event,
            ) -> ExtractedFrames:
                started.set()
                cancel_event.wait(_WAIT_SECONDS)
                cancelled.append(cancel_event.is_set())
                raise InterruptedError("취소")

        window = FrameCompareWindow(_Blocking(), lambda: 5, None)
        self.addCleanup(window.deleteLater)
        window.load_session(self.session())
        deadline = time.monotonic() + _WAIT_SECONDS
        while not started.is_set() and time.monotonic() < deadline:
            _APP.processEvents()
            time.sleep(0.01)
        self.assertTrue(started.is_set())
        window.close()
        self.assertIsNone(window._worker)
        self.assertTrue(cancelled)

    def test_theme_styles_and_thirteen_point_text_fit(self) -> None:
        self.load()
        self.window.apply_theme(LIGHT_PALETTE)
        self.assertIn(LIGHT_PALETTE.bg_card, self.window.styleSheet())
        font = self.window.font()
        font.setPointSize(13)
        self.window.setFont(font)
        _APP.processEvents()
        buttons = list(self.window._toolbar.mode_buttons.values()) + list(
            self.window._toolbar.zoom_buttons.values()
        )
        for button in buttons:
            self.assertGreaterEqual(
                button.sizeHint().width(), button.fontMetrics().horizontalAdvance(button.text())
            )


class ExtractBodyTests(unittest.TestCase):
    """스레드 밖 추출 본문의 성공, 취소, 원인별 오류"""

    def setUp(self) -> None:
        self.extractor = _Extractor()
        self.original = FrameSource(key="원본", path="a.mp4", label="원본")
        self.compared = FrameSource(key="result", path="b.mp4", label="결과")
        self.events: list[ExtractOutcome] = []

    def _run(self, event: threading.Event) -> None:
        run_extract_body(
            self.extractor,
            ExtractOutcome(3, None, time_sec=2.0),
            self.original,
            self.compared,
            100,
            50,
            event,
            self.events.append,
        )

    def test_success_reports_frames_and_time(self) -> None:
        self._run(threading.Event())
        self.assertEqual(len(self.events), 1)
        self.assertIsNotNone(self.events[0].frames)
        self.assertEqual(self.events[0].time_sec, 2.0)

    def test_interrupted_body_sends_nothing(self) -> None:
        self.extractor.failure = InterruptedError("취소")
        self._run(threading.Event())
        self.assertEqual(self.events, [])

    def test_file_missing_and_generic_errors_are_distinguished(self) -> None:
        self.extractor.failure = FrameCompareFileMissing("파일 없음")
        self._run(threading.Event())
        self.assertTrue(self.events[0].file_missing)
        self.assertEqual(self.events[0].error, "파일 없음")
        self.extractor.failure = RuntimeError("boom")
        self._run(threading.Event())
        self.assertFalse(self.events[1].file_missing)
        self.assertEqual(self.events[1].error, "boom")


class WorkerTests(unittest.TestCase):
    """워커 본문을 직접 실행해 시그널로 결과를 넘기는지"""

    def test_run_emits_the_extraction_outcome(self) -> None:
        original = FrameSource(key="원본", path="a.mp4", label="원본")
        compared = FrameSource(key="result", path="b.mp4", label="결과")
        worker = FrameCompareWorker(
            _Extractor(),
            ExtractOutcome(7, None, time_sec=1.5),
            original,
            compared,
            10,
            20,
            threading.Event(),
        )
        events: list[ExtractOutcome] = []
        worker.completed.connect(events.append)
        worker.run()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].request_id, 7)
        self.assertEqual(events[0].time_sec, 1.5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
