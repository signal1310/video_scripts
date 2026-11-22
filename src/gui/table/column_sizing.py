"""테이블 열 폭을 셀 내용과 헤더 텍스트 기준으로 측정하는 계산 모듈"""

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontMetrics

from src.core.smart_action import SMART_ACTION_REASONS
from src.gui.table.table_model import VideoTableModel

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
    VideoTableModel.COL_OUTPUT_RES: 84,
    VideoTableModel.COL_ACTIVE_FRAME: 78,
    VideoTableModel.COL_DOWNSCALE_CANDIDATE: 72,
    VideoTableModel.COL_QUALITY_METRIC: 72,
    VideoTableModel.COL_ACTION: 84,
    VideoTableModel.COL_REASON: 74,
    VideoTableModel.COL_OUT_BITRATE: 50,
}
# - 값 목록이 고정된 열은 표본 행 대신 목록 전체의 폭으로 잼. 표본 밖 행에만 있는 긴 값이 잘리는 것을 막음
FIXED_VALUE_COLUMNS: dict[int, tuple[str, ...]] = {
    VideoTableModel.COL_REASON: SMART_ACTION_REASONS,
}
# 의사결정 열(이름, 권장 처리, 판정 구간, 예상 절약)을 앞에 두고 기술 사양은 뒤에 배치
DEFAULT_COL_ORDER: tuple[int, ...] = (
    VideoTableModel.COL_NAME,
    VideoTableModel.COL_ACTION,
    VideoTableModel.COL_ZONE,
    VideoTableModel.COL_SAVED_SIZE,
    VideoTableModel.COL_SAVED_PER_MIN,
    VideoTableModel.COL_SIZE,
    VideoTableModel.COL_DURATION,
    VideoTableModel.COL_EST_ENCODE_TIME,
    VideoTableModel.COL_REASON,
    VideoTableModel.COL_ENCODE_STATE,
    VideoTableModel.COL_OUTPUT_RES,
    VideoTableModel.COL_ACTIVE_FRAME,
    VideoTableModel.COL_DOWNSCALE_CANDIDATE,
    VideoTableModel.COL_QUALITY_METRIC,
    VideoTableModel.COL_KEYFRAME,
    VideoTableModel.COL_CODEC,
    VideoTableModel.COL_WIDTH,
    VideoTableModel.COL_HEIGHT,
    VideoTableModel.COL_FPS,
    VideoTableModel.COL_BITRATE,
    VideoTableModel.COL_ROTATE,
    VideoTableModel.COL_OPT_BITRATE,
    VideoTableModel.COL_RATIO,
    VideoTableModel.COL_OUT_BITRATE,
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
        VideoTableModel.COL_ACTIVE_FRAME,
        VideoTableModel.COL_DOWNSCALE_CANDIDATE,
        VideoTableModel.COL_QUALITY_METRIC,
    }
)



class _FontWidths:
    """한 글꼴의 QFontMetrics와 문자열별 폭 캐시"""

    MAX_TEXTS: int = 50_000

    def __init__(self, font: QFont) -> None:
        self._metrics = QFontMetrics(font)
        self._widths: dict[str, int] = {}

    def advance(self, text: str) -> int:
        """문자열의 가로 폭, 캐시에 없으면 계산해 보관"""
        cached = self._widths.get(text)
        if cached is not None:
            return cached
        # - 파일명이 계속 바뀌는 긴 세션에서 메모리가 무한히 늘지 않도록 상한을 넘으면 비움
        if len(self._widths) >= self.MAX_TEXTS:
            self._widths.clear()
        width = self._metrics.horizontalAdvance(text)
        self._widths[text] = width
        return width


class TextWidthCache:
    """글꼴별 표시 폭 캐시, 재설정마다 같은 파일명과 셀 값의 폭을 다시 재지 않도록 보관

    QFontMetrics 생성과 폭 계산은 Python 호출 비용이 커서 셀마다 반복하면 재설정 시간을 지배함
    """

    MAX_FONTS: int = 8

    def __init__(self) -> None:
        self._fonts: dict[str, _FontWidths] = {}

    def for_font(self, font: QFont) -> _FontWidths:
        """글꼴에 대응하는 폭 캐시, 글꼴 종류가 너무 많아지면 전체를 비움"""
        key = font.key()
        widths = self._fonts.get(key)
        if widths is None:
            if len(self._fonts) >= self.MAX_FONTS:
                self._fonts.clear()
            widths = _FontWidths(font)
            self._fonts[key] = widths
        return widths


def _font_or_bold(value: object, bold_font: QFont) -> QFont:
    """셀 글꼴 데이터가 QFont면 그 글꼴, 아니면 굵은 글꼴"""
    return value if isinstance(value, QFont) else bold_font


def _widest_cell_text(
    model: VideoTableModel,
    col: int,
    stride: int,
    bold_widths: _FontWidths,
    bold_font: QFont,
    cache: TextWidthCache,
) -> int:
    """고정 값 열은 값 목록 전체, 그 밖의 열은 표본 행 중 가장 넓은 표시 텍스트 폭. 셀 글꼴과 굵은 글꼴 중 큰 쪽을 사용"""
    fixed_values = FIXED_VALUE_COLUMNS.get(col)
    if fixed_values is not None:
        cell_widths = cache.for_font(
            _font_or_bold(model.index(0, col).data(Qt.ItemDataRole.FontRole), bold_font)
        )
        return max(
            (max(bold_widths.advance(value), cell_widths.advance(value)) for value in fixed_values),
            default=0,
        )
    row_count = model.rowCount()
    # - 글꼴이 행 값에 따라 달라지는 열만 셀마다 글꼴을 조회하고, 나머지는 첫 행의 글꼴을 공유
    varies = model.column_font_varies(col)
    shared_widths = (
        None
        if varies
        else cache.for_font(_font_or_bold(model.index(0, col).data(Qt.ItemDataRole.FontRole), bold_font))
    )
    widest = 0
    for row in range(0, row_count, stride):
        index = model.index(row, col)
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not text:
            continue
        text_str = str(text)
        if shared_widths is not None:
            cell_widths = shared_widths
        else:
            cell_font = _font_or_bold(index.data(Qt.ItemDataRole.FontRole), bold_font)
            cell_widths = cache.for_font(cell_font)
        widest = max(widest, bold_widths.advance(text_str), cell_widths.advance(text_str))
    return widest


def measure_column_content_widths(
    model: VideoTableModel, base_font: QFont, cache: TextWidthCache
) -> dict[int, int]:
    """표본 행의 표시 텍스트와 헤더 단어의 최대 폭으로 파일명 열을 제외한 열별 필요 폭을 계산"""
    row_count = model.rowCount()
    if row_count <= 0:
        return {}
    bold_font = QFont(base_font)
    bold_font.setBold(True)
    bold_widths = cache.for_font(bold_font)
    # 표본 행 수를 상한으로 두어 대용량 목록에서도 데이터 로드 비용을 일정하게 유지
    stride = max(1, row_count // CONTENT_SAMPLE_ROWS)
    widths: dict[int, int] = {}
    for col in range(model.columnCount()):
        if col == VideoTableModel.COL_NAME:
            continue
        widest_text = _widest_cell_text(model, col, stride, bold_widths, bold_font, cache)
        header_text = model.headerData(
            col, Qt.Orientation.Horizontal, int(Qt.ItemDataRole.DisplayRole)
        )
        widest_word = max(
            (bold_widths.advance(word) for word in str(header_text or "").split()),
            default=0,
        )
        icon_extra = ICON_CELL_EXTRA_PX if col == VideoTableModel.COL_ROTATE else 0
        widths[col] = max(
            widest_text + CELL_PADDING_PX + icon_extra,
            widest_word + HEADER_CHROME_PX,
        )
    return widths


def measure_name_content_width(
    model: VideoTableModel, base_font: QFont, cache: TextWidthCache
) -> int:
    """파일명 열이 표시할 최대 텍스트 폭을 전체 행 기준으로 계산하고 상한으로 제한"""
    target_w = NAME_COL_DEFAULT_WIDTH
    row_count = model.rowCount()
    if row_count <= 0:
        return target_w
    name_widths = cache.for_font(base_font)
    for row in range(row_count):
        raw_name = model.index(row, VideoTableModel.COL_NAME).data(
            Qt.ItemDataRole.DisplayRole
        )
        if not raw_name:
            continue
        measured = name_widths.advance(str(raw_name)) + NAME_COL_CELL_PADDING_PX
        if measured >= NAME_COL_MAX_WIDTH:
            return NAME_COL_MAX_WIDTH
        if measured > target_w:
            target_w = measured
    return target_w
