"""표 재설정 때 열 폭 계산의 글꼴 캐시, 고정 글꼴 열 가정, 지연 계산 시점을 검증하는 GUI 테스트"""

import os
import unittest

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontMetrics
from PyQt6.QtWidgets import QApplication

from src.core.config import ClassifierConfig
from src.core.evaluator import VideoEvaluator
from src.core.models import VideoEvaluation
from src.gui.column_sizing import TextWidthCache, measure_column_content_widths
from src.gui.table_model import VideoTableModel
from src.gui.widgets.video_table_view import VideoTableView
from tests._factories import make_video
from tests._gui_helpers import GuiTestCase

# 헤드리스 테스트 환경을 위한 오프스크린 설정
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_APP = QApplication.instance() or QApplication([])

_ROW_COUNT: int = 60


def _evaluations(count: int) -> list[VideoEvaluation]:
    videos = [make_video(filename=f"clip{i:03d}.mp4") for i in range(count)]
    return VideoEvaluator.evaluate_all("C:/work", videos, ClassifierConfig()).evaluations


class TestTextWidthCache(unittest.TestCase):
    """글꼴별 폭 캐시가 QFontMetrics 계산과 같은 값을 주고 글꼴을 구분하는지 검증"""

    def test_cached_width_matches_font_metrics(self) -> None:
        """캐시가 계산한 폭과 직접 계산한 폭이 같고, 두 번째 조회도 같은 값을 줌을 검증"""
        font = QFont()
        cache = TextWidthCache()
        expected = QFontMetrics(font).horizontalAdvance("clip001.mp4")
        self.assertEqual(cache.for_font(font).advance("clip001.mp4"), expected)
        self.assertEqual(cache.for_font(font).advance("clip001.mp4"), expected)

    def test_bold_and_regular_fonts_are_measured_separately(self) -> None:
        """굵은 글꼴과 보통 글꼴의 폭 캐시가 서로 섞이지 않음을 검증"""
        regular = QFont()
        bold = QFont(regular)
        bold.setBold(True)
        cache = TextWidthCache()
        self.assertEqual(
            cache.for_font(bold).advance("폭 비교 문자열"),
            QFontMetrics(bold).horizontalAdvance("폭 비교 문자열"),
        )
        self.assertEqual(
            cache.for_font(regular).advance("폭 비교 문자열"),
            QFontMetrics(regular).horizontalAdvance("폭 비교 문자열"),
        )


class TestColumnWidthMeasurement(unittest.TestCase):
    """열 폭 측정이 캐시 상태와 무관하게 같은 결과를 내고, 고정 글꼴 열의 가정이 성립함을 검증"""

    def setUp(self) -> None:
        self.model = VideoTableModel()
        self.model.set_data_source(_evaluations(_ROW_COUNT), root_dir="C:/work")

    def test_warm_cache_gives_same_widths_as_cold_cache(self) -> None:
        """같은 모델을 두 번 측정할 때 캐시가 채워진 뒤에도 열 폭이 같음을 검증"""
        font = QFont()
        cold = measure_column_content_widths(self.model, font, TextWidthCache())
        warm_cache = TextWidthCache()
        measure_column_content_widths(self.model, font, warm_cache)
        warm = measure_column_content_widths(self.model, font, warm_cache)
        self.assertEqual(cold, warm)

    def test_static_font_columns_share_one_font_for_all_rows(self) -> None:
        """행 값으로 글꼴이 바뀌지 않는다고 판정한 열은 모든 행에서 같은 글꼴을 돌려줌을 검증"""
        for col in range(self.model.columnCount()):
            if self.model.column_font_varies(col) or col == VideoTableModel.COL_NAME:
                continue
            fonts = {
                self.model.index(row, col).data(Qt.ItemDataRole.FontRole) for row in range(_ROW_COUNT)
            }
            self.assertLessEqual(len(fonts), 1, f"열 {col}의 글꼴이 행마다 다름")


class TestNameListChangeDetection(unittest.TestCase):
    """파일명 목록이 같을 때는 이름 열 폭 재측정이 필요 없다는 판정이 목록 변화에만 반응함을 검증"""

    def test_flag_tracks_filename_list_only(self) -> None:
        """같은 파일 목록의 재설정은 변화 없음, 파일이 추가되면 변화로 판정하는지 확인"""
        model = VideoTableModel()
        evals = _evaluations(_ROW_COUNT)
        model.set_data_source(evals, root_dir="C:/work")
        self.assertTrue(model.name_list_changed)
        model.set_data_source(list(evals), root_dir="C:/work")
        self.assertFalse(model.name_list_changed)
        model.set_data_source(evals[:-1], root_dir="C:/work")
        self.assertTrue(model.name_list_changed)


class TestDeferredColumnWidths(GuiTestCase):
    """재설정 직후에는 열 폭 계산을 미루고 이벤트 루프에서 한 번 적용함을 검증"""

    def test_widths_apply_after_event_processing(self) -> None:
        """데이터 반영 직후에는 계산이 예약 상태이고, 이벤트 처리 뒤에는 예약이 끝나 열 폭이 적용됨을 검증"""
        view = VideoTableView()
        view.resize(1200, 600)
        view.show()
        self.addCleanup(view.close)
        view.table_model.set_data_source(_evaluations(_ROW_COUNT), root_dir="C:/work")
        self.assertTrue(view._width_timer.isActive())
        _APP.processEvents()
        self.assertFalse(view._width_timer.isActive())
        self.assertGreater(view.columnWidth(VideoTableModel.COL_OUT_BITRATE), 0)


if __name__ == "__main__":
    unittest.main()
