"""GUI 테이블 표시 값의 CSV 저장 스냅샷, 행 문자열 생성, 파일 쓰기 모듈

- GUI 스레드는 표시 순서 행과 설정 참조만 넘기고, 셀 문자열 생성과 파일 쓰기는 워커 스레드에서 수행
- 미리 보기와 저장이 같은 스냅샷 경로를 써서 두 결과가 어긋나지 않게 함
"""

import csv
import io
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from PyQt6.QtCore import QAbstractItemModel, Qt

from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table_model import VideoTableModel, display_text_for


@dataclass(frozen=True, slots=True)
class CsvSnapshot:
    """저장 입력의 복사본: 헤더와, 호출 시 행 문자열을 만드는 함수

    - render_rows는 GUI 스레드에서 Qt 객체에 접근하지 않으므로 워커 스레드에서 호출 가능
    """

    headers: tuple[str, ...]
    render_rows: Callable[[], list[list[str]]]


def resolve_csv_headers(
    model: QAbstractItemModel, headers: Sequence[str] | None = None
) -> list[str]:
    """지정한 헤더가 없으면 모델의 표시 헤더를 사용해 CSV 헤더 목록 반환"""
    if headers is not None:
        return list(headers)
    display_role = int(Qt.ItemDataRole.DisplayRole)
    return [
        str(model.headerData(col, Qt.Orientation.Horizontal, display_role) or "")
        for col in range(model.columnCount())
    ]


def _cell_text(value: Any) -> str:
    return str(value or "")


def snapshot_table_rows(
    model: QAbstractItemModel,
    headers: Sequence[str],
    limit: int | None = None,
) -> CsvSnapshot:
    """모델의 현재 표시 행을 CSV 입력으로 복사, limit이 있으면 앞쪽 행만 복사

    - 영상 프록시는 셀 값을 GUI 스레드에서 채우지 않고 행 객체와 설정 참조만 넘김
    - 그 밖의 모델은 셀 값을 GUI 스레드에서 미리 채움
    """
    if isinstance(model, VideoSortFilterProxyModel):
        return _snapshot_video_rows(model, headers, limit)
    return _snapshot_generic_rows(model, headers, limit)


def _snapshot_video_rows(
    proxy: VideoSortFilterProxyModel, headers: Sequence[str], limit: int | None
) -> CsvSnapshot:
    source = proxy.sourceModel()
    if not isinstance(source, VideoTableModel):
        return CsvSnapshot(tuple(headers), lambda: [])
    evaluations = tuple(proxy.iter_visible_evaluations()[:limit])
    config = source.config
    pending = source.pending_output_tiers
    column_count = len(headers)

    def render_rows() -> list[list[str]]:
        return [
            [_cell_text(display_text_for(ev, col, config, pending)) for col in range(column_count)]
            for ev in evaluations
        ]

    return CsvSnapshot(tuple(headers), render_rows)


def _snapshot_generic_rows(
    model: QAbstractItemModel, headers: Sequence[str], limit: int | None
) -> CsvSnapshot:
    row_count = model.rowCount() if limit is None else min(model.rowCount(), limit)
    display_role = int(Qt.ItemDataRole.DisplayRole)
    column_count = len(headers)
    rows = [
        [_cell_text(model.data(model.index(row, col), display_role)) for col in range(column_count)]
        for row in range(row_count)
    ]
    return CsvSnapshot(tuple(headers), lambda: rows)


def render_csv_text(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """헤더와 행을 저장 파일과 동일한 CSV 문자열로 직렬화, BOM은 포함하지 않음"""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue()


def write_csv_file(filepath: str, snapshot: CsvSnapshot) -> int:
    """스냅샷을 UTF-8 BOM CSV 파일로 기록하고 기록한 행 수를 반환, 워커 스레드에서 호출 가능"""
    rows = snapshot.render_rows()
    with open(filepath, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(render_csv_text(snapshot.headers, rows))
    return len(rows)


def export_table_to_csv(
    filepath: str,
    model: QAbstractItemModel,
    headers: Sequence[str] | None = None,
) -> int:
    """프록시/뷰 모델에 현재 표시 중인 행, 열 데이터를 UTF-8 BOM CSV 파일로 저장하고 내보낸 행 수를 반환"""
    resolved_headers = resolve_csv_headers(model, headers)
    return write_csv_file(filepath, snapshot_table_rows(model, resolved_headers))
