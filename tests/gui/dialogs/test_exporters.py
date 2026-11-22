"""CSV 내보내기 스냅샷이 표의 화질 분석 세 열을 포함한 현재 표시 값을 저장하는지 검증"""

import csv
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PyQt6.QtWidgets import QApplication

from src.core.analysis.frame_analysis import ActiveRegion
from src.core.analysis.frame_analysis_runner import FrameAnalysisResult
from src.core.config import ClassifierConfig
from src.gui.dialogs.exporters import (
    export_table_to_csv,
    render_csv_text,
    resolve_csv_headers,
    snapshot_table_rows,
    write_csv_file,
)
from src.gui.table.frame_analysis_columns import (
    ACTIVE_FRAME_COLUMN,
    DOWNSCALE_CANDIDATE_COLUMN,
    QUALITY_METRIC_COLUMN,
)
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_model import VideoTableModel
from tests._factories import make_eval, make_video

_APP = QApplication.instance() or QApplication([])
_EMPTY_INDEX = QModelIndex()


class _GenericModel(QAbstractTableModel):
    """행 문자열과 헤더를 고정한 표 모델 대역"""

    def __init__(self, rows: list[list[str | None]]) -> None:
        super().__init__()
        self._rows = rows

    def rowCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        return 0 if parent.isValid() else len(self._rows[0])

    def data(self, index: QModelIndex, role: int = int(Qt.ItemDataRole.DisplayRole)) -> object:
        if not index.isValid() or role != int(Qt.ItemDataRole.DisplayRole):
            return None
        return self._rows[index.row()][index.column()]

    def headerData(
        self, section: int, orientation: Qt.Orientation, role: int = int(Qt.ItemDataRole.DisplayRole)
    ) -> object:
        if orientation != Qt.Orientation.Horizontal or role != int(Qt.ItemDataRole.DisplayRole):
            return None
        return f"h{section}"


def _analysis_result() -> FrameAnalysisResult:
    return FrameAnalysisResult(
        active_region=ActiveRegion(1920, 800, 0, 140, True),
        metric_used="vmaf",
        vmaf_model="vmaf_v0.6.1",
        tier_scores=(),
        candidate_tier="720p",
        valid_segment_count=5,
        total_segment_count=5,
        unmeasurable=False,
        is_vr_protected=False,
    )


class CsvExporterTests(unittest.TestCase):
    """일반 모델과 영상 프록시의 CSV 스냅샷, 문자열 생성, 파일 쓰기 검증"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "export.csv")

    def test_generic_snapshot_honours_explicit_headers_and_limit(self) -> None:
        """일반 모델이 명시 헤더와 행 제한을 따르고 빈 셀을 빈 문자열로 저장하는지 검증"""
        model = _GenericModel([["a", None], ["c", "d"]])
        self.assertEqual(resolve_csv_headers(model), ["h0", "h1"])
        self.assertEqual(resolve_csv_headers(model, ["x"]), ["x"])
        self.assertEqual(snapshot_table_rows(model, ["h0", "h1"], limit=1).render_rows(), [["a", ""]])
        self.assertEqual(snapshot_table_rows(model, ["h0", "h1"]).render_rows(), [["a", ""], ["c", "d"]])
        self.assertEqual(write_csv_file(self.path, snapshot_table_rows(model, ["h0", "h1"])), 2)
        with open(self.path, encoding="utf-8-sig", newline="") as handle:
            self.assertEqual(list(csv.reader(handle)), [["h0", "h1"], ["a", ""], ["c", "d"]])

    def test_proxy_snapshot_writes_frame_analysis_cells(self) -> None:
        """영상 프록시 스냅샷이 활성 화면, 축소 후보, 측정 지표 표시 문구를 CSV로 저장하는지 검증"""
        model = VideoTableModel(config=ClassifierConfig())
        model.set_data_source([make_eval(video=make_video(filename="a.mp4"))], root_dir="C:/work")
        model.set_frame_analysis_results({"a.mp4": _analysis_result()})
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(model)
        headers = resolve_csv_headers(proxy)
        self.assertEqual(len(headers), model.columnCount())
        rows = snapshot_table_rows(proxy, headers).render_rows()
        self.assertEqual(rows[0][0], "a.mp4")
        self.assertEqual(rows[0][ACTIVE_FRAME_COLUMN], "1920x800")
        self.assertEqual(rows[0][DOWNSCALE_CANDIDATE_COLUMN], "720p")
        self.assertEqual(rows[0][QUALITY_METRIC_COLUMN], "VMAF")
        self.assertEqual(export_table_to_csv(self.path, proxy), 1)
        with open(self.path, encoding="utf-8-sig", newline="") as handle:
            saved = list(csv.reader(handle))
        self.assertEqual(saved[0][QUALITY_METRIC_COLUMN], "측정 지표")
        self.assertEqual(saved[1][ACTIVE_FRAME_COLUMN], "1920x800")
        self.assertEqual(saved[1][DOWNSCALE_CANDIDATE_COLUMN], "720p")
        self.assertEqual(saved[1][QUALITY_METRIC_COLUMN], "VMAF")

    def test_proxy_with_another_source_is_empty_and_csv_text_quotes_values(self) -> None:
        """영상 프록시가 아닌 원본 모델이면 행이 없고, CSV 문자열이 쉼표를 인용하는지 검증"""
        proxy = VideoSortFilterProxyModel()
        proxy.setSourceModel(_GenericModel([["a", "b"]]))
        self.assertEqual(snapshot_table_rows(proxy, ["h0"]).render_rows(), [])
        self.assertEqual(export_table_to_csv(self.path, _GenericModel([["a", "b"]])), 1)
        self.assertEqual(render_csv_text(["a,b"], [["x", "y"]]), '"a,b"\r\nx,y\r\n')


if __name__ == "__main__":
    unittest.main()
