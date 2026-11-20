"""재분석 필요 행(이전 형식, 키프레임 미측정)의 파생 열 블러와 호버 해제 렌더링 모듈"""

from collections.abc import Callable

from PyQt6.QtCore import QEvent, QModelIndex, QObject, QRect, Qt
from PyQt6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPixmap
from PyQt6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTableView

from src.core.evaluation.reanalysis_display import is_reanalysis_blurred
from src.core.models import VideoEvaluation
from src.gui.styles.palettes import ThemePalette
from src.gui.table.delegates import IconTextDelegate, PillBadgeDelegate
from src.gui.table.table_model import VideoTableModel
from src.gui.theme import ThemeManager

BLUR_SCALE_FACTOR: float = 0.32
BLUR_OPACITY: float = 0.75
_CELL_TEXT_PAD_X_PX: int = 6

BLURRED_DERIVED_COLS: tuple[int, ...] = (
    VideoTableModel.COL_ZONE,
    VideoTableModel.COL_SAVED_SIZE,
    VideoTableModel.COL_SAVED_PER_MIN,
    VideoTableModel.COL_EST_ENCODE_TIME,
)


def paint_blurred_cell(
    painter: QPainter,
    option: QStyleOptionViewItem,
    paint_content: Callable[[QPainter, QRect], None],
) -> None:
    """셀 내용을 오프스크린 픽스맵에 그린 뒤 축소/확대 블러를 적용해 대상 영역에 그림"""
    width = option.rect.width()
    height = option.rect.height()
    if width <= 0 or height <= 0:
        return
    pixmap = QPixmap(width, height)
    pixmap.fill(Qt.GlobalColor.transparent)
    buf_painter = QPainter(pixmap)
    paint_content(buf_painter, QRect(0, 0, width, height))
    buf_painter.end()

    small_w = max(1, round(width * BLUR_SCALE_FACTOR))
    small_h = max(1, round(height * BLUR_SCALE_FACTOR))
    blurred = pixmap.scaled(
        small_w,
        small_h,
        Qt.AspectRatioMode.IgnoreAspectRatio,
        Qt.TransformationMode.SmoothTransformation,
    )
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
    painter.setOpacity(BLUR_OPACITY)
    painter.drawPixmap(option.rect, blurred)
    painter.restore()


class RowHoverTracker(QObject):
    """테이블 뷰포트의 마우스 이동을 감시해 현재 호버 중인 행 번호를 추적"""

    def __init__(self, view: QTableView) -> None:
        super().__init__(view)
        self._view = view
        self._hovered_row: int = -1

    @property
    def hovered_row(self) -> int:
        """현재 마우스가 올라간 행 번호, 없으면 -1"""
        return self._hovered_row

    def set_hovered_row(self, row: int) -> None:
        """호버 행을 갱신하고 이전/새 행의 파생 열 영역을 다시 그리도록 요청"""
        if row == self._hovered_row:
            return
        previous = self._hovered_row
        self._hovered_row = row
        self._repaint_row_derived(previous)
        self._repaint_row_derived(row)

    def _repaint_row_derived(self, row: int) -> None:
        viewport = self._view.viewport()
        model = self._view.model()
        if row < 0 or viewport is None or model is None:
            return
        for col in BLURRED_DERIVED_COLS:
            rect = self._view.visualRect(model.index(row, col))
            if rect.isValid():
                viewport.update(rect)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """뷰포트의 마우스 이동과 이탈 이벤트에서 호버 행을 갱신"""
        if a1 is not None:
            etype = a1.type()
            if etype == QEvent.Type.MouseMove and isinstance(a1, QMouseEvent):
                idx = self._view.indexAt(a1.position().toPoint())
                self.set_hovered_row(idx.row() if idx.isValid() else -1)
            elif etype == QEvent.Type.Leave:
                self.set_hovered_row(-1)
        return super().eventFilter(a0, a1)


def _paint_cell_background(
    painter: QPainter,
    option: QStyleOptionViewItem,
    row: int,
    palette: ThemePalette,
) -> None:
    if option.state & QStyle.StateFlag.State_Selected:
        painter.fillRect(option.rect, QColor(palette.bg_selected))
    elif option.state & QStyle.StateFlag.State_MouseOver:
        painter.fillRect(option.rect, QColor(palette.bg_hover))
    elif row % 2 == 1:
        painter.fillRect(option.rect, QColor(palette.bg_table_alt))


class BlurredBadgeDelegate(PillBadgeDelegate):
    """화질 등급 배지 열에서 재분석 필요 행이고 호버되지 않았을 때만 블러 처리"""

    def __init__(self, hover_tracker: RowHoverTracker, parent: QTableView) -> None:
        super().__init__(parent)
        self._hover_tracker = hover_tracker

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """재분석 필요 행의 화질 등급 배지를 비호버 상태에서 블러로 그림"""
        if painter is None:
            return
        ev = index.data(VideoTableModel.EVAL_ROLE)
        hovered = (
            self._hover_tracker.hovered_row == index.row()
            or bool(option.state & QStyle.StateFlag.State_MouseOver)
        )
        if (
            index.column() == VideoTableModel.COL_ZONE
            and isinstance(ev, VideoEvaluation)
            and is_reanalysis_blurred(ev)
            and not hovered
        ):
            palette = ThemeManager().current_palette
            _paint_cell_background(painter, option, index.row(), palette)
            local_opt = QStyleOptionViewItem(option)
            local_opt.rect = QRect(0, 0, option.rect.width(), option.rect.height())
            local_opt.state &= ~(
                QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver
            )
            paint_blurred_cell(
                painter,
                option,
                lambda buf_p, _rect: PillBadgeDelegate.paint(self, buf_p, local_opt, index),
            )
            return
        super().paint(painter, option, index)


class BlurredValueDelegate(QStyledItemDelegate):
    """예상 절약, 분당 절약, 예상 시간 열에서 재분석 필요 행이고 비호버일 때 블러 처리"""

    def __init__(self, hover_tracker: RowHoverTracker, parent: QTableView) -> None:
        super().__init__(parent)
        self._hover_tracker = hover_tracker

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """재분석 필요 행의 파생 수치 셀을 비호버 상태에서 블러로 그림"""
        if painter is None:
            return
        ev = index.data(VideoTableModel.EVAL_ROLE)
        hovered = (
            self._hover_tracker.hovered_row == index.row()
            or bool(option.state & QStyle.StateFlag.State_MouseOver)
        )
        if not isinstance(ev, VideoEvaluation) or not is_reanalysis_blurred(ev) or hovered:
            super().paint(painter, option, index)
            return
        palette = ThemeManager().current_palette
        _paint_cell_background(painter, option, index.row(), palette)
        paint_blurred_cell(
            painter,
            option,
            lambda buf_p, local_rect: self._paint_text(
                buf_p, option, local_rect, index, palette
            ),
        )

    def _paint_text(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        local_rect: QRect,
        index: QModelIndex,
        palette: ThemePalette,
    ) -> None:
        text = str(index.data(Qt.ItemDataRole.DisplayRole) or "")
        if not text:
            return
        font_val = index.data(Qt.ItemDataRole.FontRole)
        font = font_val if isinstance(font_val, QFont) else option.font
        painter.setFont(font)
        fg_val = index.data(Qt.ItemDataRole.ForegroundRole)
        fg_color = (
            fg_val if isinstance(fg_val, QColor) else QColor(palette.text_primary)
        )
        painter.setPen(fg_color)
        align_val = index.data(Qt.ItemDataRole.TextAlignmentRole)
        align = (
            int(align_val)
            if isinstance(align_val, int)
            else int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        )
        text_rect = local_rect.adjusted(_CELL_TEXT_PAD_X_PX, 0, -_CELL_TEXT_PAD_X_PX, 0)
        elided = painter.fontMetrics().elidedText(
            text, Qt.TextElideMode.ElideRight, max(0, text_rect.width())
        )
        painter.drawText(text_rect, align, elided)


def install_table_delegates(
    table_view: QTableView,
) -> tuple[PillBadgeDelegate, IconTextDelegate, RowHoverTracker]:
    """테이블 뷰에 배지, 아이콘, 재분석 블러 델리게이트와 행 호버 추적기를 연결"""
    table_view.setMouseTracking(True)
    hover_tracker = RowHoverTracker(table_view)
    viewport = table_view.viewport()
    if viewport is not None:
        viewport.installEventFilter(hover_tracker)

    badge_delegate = BlurredBadgeDelegate(hover_tracker, table_view)
    for col in (
        VideoTableModel.COL_ZONE,
        VideoTableModel.COL_ACTION,
        VideoTableModel.COL_REASON,
    ):
        table_view.setItemDelegateForColumn(col, badge_delegate)

    value_delegate = BlurredValueDelegate(hover_tracker, table_view)
    for col in (
        VideoTableModel.COL_SAVED_SIZE,
        VideoTableModel.COL_SAVED_PER_MIN,
        VideoTableModel.COL_EST_ENCODE_TIME,
    ):
        table_view.setItemDelegateForColumn(col, value_delegate)

    icon_text_delegate = IconTextDelegate(table_view)
    table_view.setItemDelegateForColumn(VideoTableModel.COL_ROTATE, icon_text_delegate)
    return badge_delegate, icon_text_delegate, hover_tracker
