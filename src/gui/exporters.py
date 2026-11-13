"""GUI 테이블 모델 데이터의 CSV 내보내기 유틸리티 모듈"""

import csv
from collections.abc import Sequence

from PyQt6.QtCore import QAbstractItemModel, Qt


def export_table_to_csv(
    filepath: str,
    model: QAbstractItemModel,
    headers: Sequence[str],
) -> int:
    """프록시/뷰 모델에 현재 표시 중인 행·열 데이터를 UTF-8 BOM CSV 파일로 저장하고 내보낸 행 수를 반환"""
    row_count = model.rowCount()
    col_count = len(headers)
    display_role = int(Qt.ItemDataRole.DisplayRole)

    with open(filepath, "w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(headers)
        for row in range(row_count):
            writer.writerow(
                [
                    str(model.data(model.index(row, col), display_role) or "")
                    for col in range(col_count)
                ]
            )
    return row_count
