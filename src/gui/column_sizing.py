"""테이블 열 폭을 셀 내용과 헤더 텍스트 기준으로 측정하는 계산 모듈"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontMetrics

from src.gui.table_model import VideoTableModel

CELL_PADDING_PX: int = 24
HEADER_CHROME_PX: int = 28
ICON_CELL_EXTRA_PX: int = 16
CONTENT_SAMPLE_ROWS: int = 400
NAME_COL_DEFAULT_WIDTH: int = 104
NAME_COL_MAX_WIDTH: int = 360
NAME_COL_CELL_PADDING_PX: int = 24

# 열 최소 폭, 실제 폭은 측정한 내용 폭과 이 값 중 큰 쪽을 따름
MIN_COL_WIDTHS: dict[int, int] = {
    VideoTableModel.COL_NAME: NAME_COL_DEFAULT_WIDTH,
    VideoTableModel.COL_STATUS: 70,
    VideoTableModel.COL_WIDTH: 42,
    VideoTableModel.COL_HEIGHT: 42,
    VideoTableModel.COL_ROTATE: 44,
    VideoTableModel.COL_CODEC: 46,
    VideoTableModel.COL_FPS: 40,
    VideoTableModel.COL_DURATION: 50,
    VideoTableModel.COL_BITRATE: 46,
    VideoTableModel.COL_OPT_BITRATE: 50,
    VideoTableModel.COL_RATIO: 46,
    VideoTableModel.COL_TARGET_RATIO: 46,
    VideoTableModel.COL_ZONE: 56,
    VideoTableModel.COL_KEYFRAME: 56,
    VideoTableModel.COL_SIZE: 72,
    VideoTableModel.COL_AFTER_SIZE: 72,
    VideoTableModel.COL_SAVED_SIZE: 78,
    VideoTableModel.COL_SAVED_PER_MIN: 86,
    VideoTableModel.COL_EST_ENCODE_TIME: 60,
    VideoTableModel.COL_ACTION: 84,
    VideoTableModel.COL_REASON: 74,
}
# 의사결정 열(이름, 권장 처리, 분류 상태, 예상 절약)을 앞에 두고 기술 사양은 뒤에 배치
DEFAULT_COL_ORDER: tuple[int, ...] = (
    VideoTableModel.COL_NAME,
    VideoTableModel.COL_ACTION,
    VideoTableModel.COL_STATUS,
    VideoTableModel.COL_ZONE,
    VideoTableModel.COL_SAVED_SIZE,
    VideoTableModel.COL_SAVED_PER_MIN,
    VideoTableModel.COL_SIZE,
    VideoTableModel.COL_DURATION,
    VideoTableModel.COL_EST_ENCODE_TIME,
    VideoTableModel.COL_REASON,
    VideoTableModel.COL_KEYFRAME,
    VideoTableModel.COL_CODEC,
    VideoTableModel.COL_WIDTH,
    VideoTableModel.COL_HEIGHT,
    VideoTableModel.COL_FPS,
    VideoTableModel.COL_BITRATE,
    VideoTableModel.COL_ROTATE,
    VideoTableModel.COL_OPT_BITRATE,
    VideoTableModel.COL_RATIO,
    VideoTableModel.COL_TARGET_RATIO,
    VideoTableModel.COL_AFTER_SIZE,
)
DEFAULT_HIDDEN_COLS: frozenset[int] = frozenset(
    {
        VideoTableModel.COL_ROTATE,
        VideoTableModel.COL_OPT_BITRATE,
        VideoTableModel.COL_RATIO,
        VideoTableModel.COL_TARGET_RATIO,
        VideoTableModel.COL_AFTER_SIZE,
    }
)



def measure_column_content_widths(
    model: VideoTableModel, base_font: QFont
) -> dict[int, int]:
    """표본 행의 표시 텍스트와 헤더 단어의 최대 폭으로 파일명 열을 제외한 열별 필요 폭을 계산"""
    row_count = model.rowCount()
    if row_count <= 0:
        return {}
    bold_font = QFont(base_font)
    bold_font.setBold(True)
    metrics = QFontMetrics(bold_font)
    # 표본 행 수를 상한으로 두어 대용량 목록에서도 데이터 로드 비용을 일정하게 유지
    stride = max(1, row_count // CONTENT_SAMPLE_ROWS)
    widths: dict[int, int] = {}
    for col in range(model.columnCount()):
        if col == VideoTableModel.COL_NAME:
            continue
        widest_text = 0
        for row in range(0, row_count, stride):
            index = model.index(row, col)
            text = index.data(Qt.ItemDataRole.DisplayRole)
            if not text:
                continue
            # 고정폭 숫자 열은 굵은 글꼴보다 셀 글꼴이 넓으므로 두 값 중 큰 쪽을 사용
            cell_font = index.data(Qt.ItemDataRole.FontRole)
            cell_metrics = (
                QFontMetrics(cell_font) if isinstance(cell_font, QFont) else metrics
            )
            widest_text = max(
                widest_text,
                metrics.horizontalAdvance(str(text)),
                cell_metrics.horizontalAdvance(str(text)),
            )
        header_text = model.headerData(
            col, Qt.Orientation.Horizontal, int(Qt.ItemDataRole.DisplayRole)
        )
        widest_word = max(
            (metrics.horizontalAdvance(word) for word in str(header_text or "").split()),
            default=0,
        )
        icon_extra = ICON_CELL_EXTRA_PX if col == VideoTableModel.COL_ROTATE else 0
        widths[col] = max(
            widest_text + CELL_PADDING_PX + icon_extra,
            widest_word + HEADER_CHROME_PX,
        )
    return widths


def measure_name_content_width(model: VideoTableModel, base_font: QFont) -> int:
    """파일명 열이 표시할 최대 텍스트 폭을 전체 행 기준으로 계산하고 상한으로 제한"""
    target_w = NAME_COL_DEFAULT_WIDTH
    row_count = model.rowCount()
    if row_count <= 0:
        return target_w
    metrics = QFontMetrics(base_font)
    for row in range(row_count):
        raw_name = model.index(row, VideoTableModel.COL_NAME).data(
            Qt.ItemDataRole.DisplayRole
        )
        if not raw_name:
            continue
        measured = metrics.horizontalAdvance(str(raw_name)) + NAME_COL_CELL_PADDING_PX
        if measured >= NAME_COL_MAX_WIDTH:
            return NAME_COL_MAX_WIDTH
        if measured > target_w:
            target_w = measured
    return target_w
