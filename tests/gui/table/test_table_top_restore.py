"""표 맨 위 영상의 저장과 복원, 저장한 파일이 없을 때 가까운 행으로 이동하는 동작을 검증하는 GUI 테스트"""

import json
import os
import unittest

from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.evaluation.evaluator import VideoEvaluator
from src.core.models import VideoEvaluation
from src.core.settings.app_state import AppState
from src.core.settings.state_codec import parse_state_mapping, render_state_jsonc, strip_jsonc_comments
from src.core.settings.ui_state import UiLayoutState, UiState
from src.gui.table.video_table_view import VideoTableView
from tests._factories import make_video
from tests._gui_helpers import GuiTestCase

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

_ROW_COUNT: int = 200
_ROOT_DIR: str = "C:/work"


def _evaluations(count: int) -> list[VideoEvaluation]:
    """파일명이 f000.mp4부터 순서대로 붙은 평가 결과 목록"""
    videos = [make_video(filename=f"f{i:03d}.mp4") for i in range(count)]
    return VideoEvaluator.evaluate_all(_ROOT_DIR, videos, ClassifierConfig()).evaluations


class TestTableTopRestore(GuiTestCase):
    """맨 위 영상을 기준으로 표 스크롤 위치를 저장하고 다음 데이터 반영 때 복원하는 동작을 검증"""

    def setUp(self) -> None:
        self.view = VideoTableView()
        self.view.resize(400, 300)
        self.view.show()
        self.addCleanup(self.view.close)
        self.evals = _evaluations(_ROW_COUNT)

    def _load(self, evals: list[VideoEvaluation]) -> None:
        self.view.table_model.set_data_source(evals, root_dir=_ROOT_DIR)
        _APP.processEvents()

    def test_top_row_key_follows_scroll(self) -> None:
        """스크롤한 뒤 맨 위에 보이는 행의 파일명과 행 번호를 반환함을 검증"""
        self._load(self.evals)
        self.view.scrollTo(self.view.group_model.index(120, 0), self.view.ScrollHint.PositionAtTop)
        _APP.processEvents()
        top = self.view.top_visible_row_key()
        self.assertIsNotNone(top)
        assert top is not None
        filename, row = top
        self.assertEqual(filename, self.evals[row].video.filename)
        self.assertEqual(row, self.view.rowAt(0))

    def test_top_row_key_is_none_for_empty_table(self) -> None:
        """표가 비어 있으면 맨 위 행 정보가 없음을 검증"""
        self.assertIsNone(self.view.top_visible_row_key())

    def test_restore_moves_saved_file_to_top_when_rows_arrive_later(self) -> None:
        """행이 아직 없을 때 요청한 복원이 분석 결과가 들어온 뒤 저장한 파일을 맨 위로 옮김을 검증"""
        self.view.request_top_restore("f150.mp4", 150)
        self._load(self.evals)
        self.assertEqual(self.view.top_visible_row_key(), ("f150.mp4", 150))

    def test_missing_file_falls_back_to_saved_row(self) -> None:
        """저장한 파일이 표에 없으면 저장한 행 번호 근처로 이동함을 검증"""
        self.view.request_top_restore("gone.mp4", 80)
        self._load(self.evals)
        top = self.view.top_visible_row_key()
        self.assertIsNotNone(top)
        assert top is not None
        self.assertEqual(top[1], 80)

    def test_missing_file_past_end_clamps_to_last_row(self) -> None:
        """저장한 행 번호가 현재 행 수보다 크면 마지막 행 근처로 이동함을 검증"""
        self.view.request_top_restore("gone.mp4", _ROW_COUNT + 50)
        self._load(self.evals[:40])
        top = self.view.top_visible_row_key()
        self.assertIsNotNone(top)
        assert top is not None
        self.assertLess(top[1], 40)

    def test_restore_is_one_shot(self) -> None:
        """복원은 한 번만 적용되어 이후 정렬이나 재평가로 스크롤을 다시 옮기지 않음을 검증"""
        self.view.request_top_restore("f150.mp4", 150)
        self._load(self.evals)
        self.view.scrollTo(self.view.group_model.index(10, 0), self.view.ScrollHint.PositionAtTop)
        _APP.processEvents()
        self._load(self.evals)
        self.assertEqual(self.view.top_visible_row_key(), ("f010.mp4", 10))


class TestTableTopLayoutCodec(unittest.TestCase):
    """맨 위 영상 저장 필드의 직렬화 왕복과 잘못된 행 번호 보정을 검증"""

    def test_round_trip_keeps_top_file_and_row(self) -> None:
        """저장한 파일명과 행 번호가 JSONC 렌더링과 파싱을 거쳐도 그대로 복원됨을 검증"""
        state = AppState(ui=UiState(layout=UiLayoutState(table_top_filename="a.mp4", table_top_row=42)))
        parsed = parse_state_mapping(json.loads(strip_jsonc_comments(render_state_jsonc(state))))
        self.assertEqual(parsed.ui.layout.table_top_filename, "a.mp4")
        self.assertEqual(parsed.ui.layout.table_top_row, 42)

    def test_negative_row_is_sanitized_to_zero(self) -> None:
        """음수 행 번호는 0으로 보정됨을 검증"""
        parsed = parse_state_mapping({"ui": {"layout": {"table_top_filename": "a.mp4", "table_top_row": -3}}})
        self.assertEqual(parsed.ui.layout.table_top_row, 0)


if __name__ == "__main__":
    unittest.main()
