"""GUI 테이블 모델 데이터의 CSV 행 생성, 미리 보기용 행 제한, 파일 저장 모듈"""

import csv
import io
from collections.abc import Sequence

from PyQt6.QtCore import QAbstractItemModel, Qt


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


def build_csv_rows(
    model: QAbstractItemModel,
    headers: Sequence[str],
    limit: int | None = None,
) -> list[list[str]]:
    """모델의 현재 표시 행을 헤더 열 수만큼 문자열 행 목록으로 변환, limit이 있으면 앞쪽 행만 반환

    저장과 미리 보기가 이 함수를 함께 사용해 두 결과가 어긋나지 않도록 함
    """
    row_count = model.rowCount()
    if limit is not None:
        row_count = min(row_count, limit)
    display_role = int(Qt.ItemDataRole.DisplayRole)
    col_count = len(headers)
    return [
        [str(model.data(model.index(row, col), display_role) or "") for col in range(col_count)]
        for row in range(row_count)
    ]


def render_csv_text(headers: Sequence[str], rows: Sequence[Sequence[str]]) -> str:
    """헤더와 행을 저장 파일과 동일한 CSV 문자열로 직렬화, BOM은 포함하지 않음"""
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow(headers)
    writer.writerows(rows)
    return buffer.getvalue()


def export_table_to_csv(
    filepath: str,
    model: QAbstractItemModel,
    headers: Sequence[str] | None = None,
) -> int:
    """프록시/뷰 모델에 현재 표시 중인 행, 열 데이터를 UTF-8 BOM CSV 파일로 저장하고 내보낸 행 수를 반환"""
    resolved_headers = resolve_csv_headers(model, headers)
    rows = build_csv_rows(model, resolved_headers)
    with open(filepath, "w", encoding="utf-8-sig", newline="") as fh:
        fh.write(render_csv_text(resolved_headers, rows))
    return len(rows)
