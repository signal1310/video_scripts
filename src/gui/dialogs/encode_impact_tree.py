"""인코딩 창 요약 탭의 영상 변환 트리: 집계 항목과 영상 행, 메인 표와 같은 영상 동작

- 집계 항목 제목은 항목별 수를 붙이고, 변환 항목은 해당 수와 전체 대상을 함께 씀
- 집계 항목에는 분류 배지(영상 변환, 스트림, 태그)를 붙이고 1, 2순위 항목은 기본으로 펼침
- 영상 행은 이름과 우측 정렬 변환 내역을 두 칸으로 나누고, 메인 표와 같은 상세 툴팁, 더블클릭 재생,
  스페이스바 미리 보기, 화질 비교 전환 단축키를 제공
- 문구는 TERMINOLOGY.md 6.6절과 6.17절이 기준 정의
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from PyQt6.QtCore import QModelIndex, QRectF, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPen
from PyQt6.QtWidgets import (
    QHeaderView,
    QMessageBox,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from src.core.encode.encode_impact import (
    IMPACT_ATTACHMENT_DROPPED,
    IMPACT_AUDIO_AAC,
    IMPACT_BITMAP_SUB_MKV,
    IMPACT_CONTAINER_CHANGED,
    IMPACT_COVER_KEPT,
    IMPACT_CROP_BARS,
    IMPACT_DATA_DROPPED,
    IMPACT_DOWNSCALE_RESOLUTION,
    IMPACT_EXPLORER_TAGS_PRESERVED,
    IMPACT_EXTRA_VIDEO_DROPPED,
    IMPACT_SUBTITLE_MOV_TEXT,
    IMPACT_TEN_BIT_TO_EIGHT,
    IMPACT_VR_STEREO_LOST,
    EncodeImpactSummary,
    ImpactBucket,
    impact_title,
    is_transform_impact,
)
from src.core.models import VideoEvaluation
from src.gui.constants import FONT_UI_FAMILY
from src.gui.dialogs.frame_compare_shortcuts import compare_shortcut_pressed
from src.gui.styles.icons import get_active_palette
from src.gui.table.table_actions import (
    count_missing_video_files,
    missing_notice,
    row_file_state,
)
from src.gui.table.table_tooltips import format_row_tooltip

UNKNOWN_LABEL: str = "정보 없음"
UNKNOWN_TOOLTIP: str = "분석 정보가 없어 집계하지 못했습니다."
BITMAP_REASON: str = "비트맵 자막(PGS, VobSub 등)은 MP4에 저장할 수 없어 MKV로 출력합니다."
BITMAP_TOOLTIP: str = "MKV로 출력하면 탐색기 태그는 옮기지만 별점은 옮기지 않습니다."
CATEGORY_VIDEO: str = "영상 변환"
CATEGORY_STREAM: str = "스트림"
CATEGORY_TAG: str = "태그"
TRANSFORM_AUTO_EXPAND_COUNT: int = 2
BADGE_PAD_X_PX: int = 8
BADGE_HEIGHT_PX: int = 18
BADGE_RADIUS_PX: float = 9.0
BADGE_BORDER_WIDTH_PX: float = 1.0
BADGE_RIGHT_MARGIN_PX: int = 4
BADGE_FONT_SCALE: float = 0.88
NAME_COLUMN: int = 0
DETAIL_COLUMN: int = 1
CATEGORY_ROLE: int = int(Qt.ItemDataRole.UserRole) + 1
# - 영상 변환 내역은 오른쪽 칸에 붙여 이름 길이와 무관하게 같은 자리에서 읽히게 함
DETAIL_ALIGNMENT: Qt.AlignmentFlag = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
# - 집계 항목을 화면에서 묶어 보여 줄 분류. 정보 없음처럼 분류가 없는 항목은 배지를 붙이지 않음
IMPACT_CATEGORIES: dict[str, str] = {
    IMPACT_CROP_BARS: CATEGORY_VIDEO,
    IMPACT_DOWNSCALE_RESOLUTION: CATEGORY_VIDEO,
    IMPACT_COVER_KEPT: CATEGORY_STREAM,
    IMPACT_SUBTITLE_MOV_TEXT: CATEGORY_STREAM,
    IMPACT_BITMAP_SUB_MKV: CATEGORY_STREAM,
    IMPACT_ATTACHMENT_DROPPED: CATEGORY_STREAM,
    IMPACT_EXTRA_VIDEO_DROPPED: CATEGORY_STREAM,
    IMPACT_DATA_DROPPED: CATEGORY_STREAM,
    IMPACT_AUDIO_AAC: CATEGORY_STREAM,
    IMPACT_CONTAINER_CHANGED: CATEGORY_STREAM,
    IMPACT_TEN_BIT_TO_EIGHT: CATEGORY_STREAM,
    IMPACT_VR_STEREO_LOST: CATEGORY_STREAM,
    IMPACT_EXPLORER_TAGS_PRESERVED: CATEGORY_TAG,
}
CATEGORY_TOOLTIPS: dict[str, str] = {
    CATEGORY_VIDEO: "화면 크기나 해상도가 바뀌는 영상 변환 항목입니다.",
    CATEGORY_STREAM: "결과 파일에 담는 스트림(영상, 소리, 자막, 부가 자료) 구성이 바뀌는 항목입니다.",
    CATEGORY_TAG: "결과 파일에 옮기거나 남기는 태그와 별점 같은 기록 항목입니다.",
}
IMPACT_TOOLTIPS: dict[str, str] = {
    IMPACT_CROP_BARS: (
        "검은 여백을 잘라내고 활성 화면만 남깁니다. 영상 내용은 그대로이며 남는 부분의 화면비와 해상도가 달라집니다."
    ),
    IMPACT_DOWNSCALE_RESOLUTION: (
        "해상도를 낮춰 저장합니다. 같은 화질 기준값에서 용량이 줄고 화면이 작아지며, 육안 확인이 필요한 항목입니다."
    ),
    IMPACT_COVER_KEPT: "앞면 커버 이미지를 다시 인코딩하지 않고 결과 파일에 그대로 복사합니다.",
    IMPACT_EXPLORER_TAGS_PRESERVED: (
        "원본의 탐색기 태그와 별점을 결과 파일에 옮깁니다. 태그나 별점이 없는 영상도 이 목록에 포함됩니다."
    ),
    IMPACT_SUBTITLE_MOV_TEXT: (
        "텍스트 자막을 MP4용 형식으로 바꿉니다. 글꼴, 색, 위치 같은 자막 서식은 사라지고 글자만 남습니다."
    ),
    IMPACT_BITMAP_SUB_MKV: f"{BITMAP_REASON} {BITMAP_TOOLTIP}",
    IMPACT_ATTACHMENT_DROPPED: "MP4는 첨부 파일을 저장하지 않아 글꼴 같은 첨부 파일을 결과에서 뺍니다.",
    IMPACT_EXTRA_VIDEO_DROPPED: "주 영상 밖의 영상 스트림을 결과에서 뺍니다. 커버 이미지는 이 항목에 들지 않습니다.",
    IMPACT_DATA_DROPPED: "데이터 스트림은 결과에 넣지 않습니다. 원본에 든 타임코드 같은 기록 트랙이 여기에 속합니다.",
    IMPACT_AUDIO_AAC: (
        "결과 형식에 맞지 않는 오디오를 AAC로 다시 인코딩합니다. AAC도 손실 압축이라 음질이 조금 달라질 수 있습니다."
    ),
    IMPACT_CONTAINER_CHANGED: (
        "원본과 다른 형식으로 저장합니다. 원본 형식을 유지할 수 없는 형식(WMV 등)은 MP4로 저장하고, "
        "출력 형식이 항상 MP4이면 MKV 원본도 MP4로 저장합니다."
    ),
    IMPACT_TEN_BIT_TO_EIGHT: (
        "10비트 영상을 8비트로 줄여 저장합니다. 색 정보가 줄어 밝기 단계나 그라데이션이 달라질 수 있습니다."
    ),
    IMPACT_VR_STEREO_LOST: (
        "MP4로 출력하면 입체 3D(Stereo 3D) 같은 VR 부가 정보가 사라집니다. 원본 형식 유지(MKV)를 권장합니다."
    ),
}


class SummaryMedia(Protocol):
    """요약 탭 영상 행이 쓰는 열기, 미리 보기, 화질 비교"""

    def open_file(self, path: str) -> None:
        """영상 파일을 기본 플레이어로 연다"""

    def preview_file(self, path: str) -> None:
        """영상 파일을 미리 보기 창으로 연다"""

    def compare_files(self, source: str) -> None:
        """원본과 결과 파일을 화질 비교 창으로 연다"""


class _ImpactItemTree(QTreeWidget):
    """집계 목록 트리. 스페이스바는 미리 보기, 화질 비교 단축키는 화질 비교로 넘기고 항목 줄은 한 번 클릭으로 펼치거나 접음"""

    preview_key_pressed = pyqtSignal()
    compare_key_pressed = pyqtSignal()

    def __init__(self, shortcuts: Callable[[], Mapping[str, str]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._get_shortcuts = shortcuts

    def keyPressEvent(self, event: QKeyEvent | None) -> None:
        if event is not None:
            if compare_shortcut_pressed(event, self._get_shortcuts()):
                self.compare_key_pressed.emit()
                event.accept()
                return
            if event.key() == Qt.Key.Key_Space:
                self.preview_key_pressed.emit()
                event.accept()
                return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent | None) -> None:
        """화살표 클릭은 기본 동작이 펼치거나 접으므로 상태가 바뀌었으면 더 바꾸지 않아 두 번 토글되지 않게 함"""
        if event is None:
            return
        item = self.itemAt(event.position().toPoint())
        was_expanded = item is not None and item.isExpanded()
        super().mousePressEvent(event)
        if item is not None and item.childCount() > 0 and item.isExpanded() == was_expanded:
            item.setExpanded(not was_expanded)


class _ImpactRowDelegate(QStyledItemDelegate):
    """집계 항목 줄은 선택 배경과 포커스 테두리를 그리지 않고 분류 배지를 그리고, 영상 줄은 기본대로 그림"""

    def initStyleOption(self, option: QStyleOptionViewItem | None, index: QModelIndex) -> None:
        super().initStyleOption(option, index)
        if option is not None and not index.parent().isValid():
            option.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_HasFocus)

    @staticmethod
    def _category_of(index: QModelIndex) -> str:
        """같은 줄의 분류 값. 머리글 줄은 첫 열을 펼쳐 그리므로 두 열 어느 쪽에서 물어도 같은 값"""
        category = index.sibling(index.row(), DETAIL_COLUMN).data(CATEGORY_ROLE)
        return category if isinstance(category, str) else ""

    @staticmethod
    def _badge_metrics(option: QStyleOptionViewItem, category: str) -> tuple[QFont, int]:
        """분류 배지 글꼴과 배지 폭. 배지 폭은 좌우 여백을 포함"""
        font = QFont(FONT_UI_FAMILY)
        font.setPointSizeF(option.font.pointSizeF() * BADGE_FONT_SCALE)
        return font, QFontMetrics(font).horizontalAdvance(category) + BADGE_PAD_X_PX * 2

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        """머리글 줄은 배지 높이와 배지까지의 폭을 확보해야 제목이 배지에 가리지 않음"""
        base = super().sizeHint(option, index)
        category = self._category_of(index)
        if index.column() != NAME_COLUMN or not category:
            return base
        _font, width = self._badge_metrics(option, category)
        return QSize(
            max(base.width(), width + BADGE_RIGHT_MARGIN_PX),
            max(base.height(), BADGE_HEIGHT_PX),
        )

    def paint(
        self,
        painter: QPainter | None,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """머리글 줄의 분류 배지는 병합한 줄 오른쪽 끝에 그리고, 그 밖의 칸은 기본대로 그림

        - 머리글 줄은 첫 열만 펼쳐 두 열을 합치므로 배지도 첫 열 안에서 직접 그리고 제목 자리를 비워 둠
        """
        category = self._category_of(index)
        if painter is None or index.column() != NAME_COLUMN or not category:
            super().paint(painter, option, index)
            return
        palette = get_active_palette()
        font, width = self._badge_metrics(option, category)
        text_option = QStyleOptionViewItem(option)
        text_option.rect = option.rect.adjusted(0, 0, -(width + BADGE_RIGHT_MARGIN_PX), 0)
        super().paint(painter, text_option, index)
        rect = option.rect
        pill = QRectF(
            rect.x() + rect.width() - width - BADGE_RIGHT_MARGIN_PX,
            rect.y() + (rect.height() - BADGE_HEIGHT_PX) / 2.0,
            width,
            BADGE_HEIGHT_PX,
        )
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setFont(font)
        painter.setBrush(QColor(palette.bg_elevated))
        painter.setPen(QPen(QColor(palette.border_subtle), BADGE_BORDER_WIDTH_PX))
        painter.drawRoundedRect(pill, BADGE_RADIUS_PX, BADGE_RADIUS_PX)
        painter.setPen(QPen(QColor(palette.text_secondary)))
        painter.drawText(pill, int(Qt.AlignmentFlag.AlignCenter), category)
        painter.restore()


class EncodeImpactTree(QWidget):
    """집계 항목과 영상 행을 보여 주는 트리. 영상 행 동작은 메인 표와 같음

    - preview/compare는 조정자(SummaryMedia)에 절대 경로를 넘김
    - 파일이 없을 때의 안내는 메인 표와 같은 문구를 씀
    """

    def __init__(
        self,
        media: SummaryMedia,
        parent: QWidget | None = None,
        notice: Callable[[QWidget, str, str], None] | None = None,
        *,
        get_shortcuts: Callable[[], Mapping[str, str]],
    ) -> None:
        super().__init__(parent)
        self._media: SummaryMedia = media
        self._notice: Callable[[QWidget, str, str], None] = notice if notice is not None else _show_warning
        self._root_dir: str = ""
        self._target_codec_label: str = ""
        self._evals: dict[str, VideoEvaluation] = {}
        self.tree = _ImpactItemTree(get_shortcuts, self)
        self.tree.setObjectName("EncodeImpactTree")
        self.tree.setColumnCount(2)
        # - 왼쪽은 영상 이름, 오른쪽은 변환 내역. 오른쪽 칸은 내용 폭만 쓰고 남는 폭은 이름 칸이 받음
        header = self.tree.header()
        if header is not None:
            header.setStretchLastSection(False)
            header.setSectionResizeMode(NAME_COLUMN, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(DETAIL_COLUMN, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QTreeWidget.SelectionMode.SingleSelection)
        self.tree.setExpandsOnDoubleClick(False)
        self.tree.setItemDelegate(_ImpactRowDelegate(self.tree))
        self.tree.preview_key_pressed.connect(self.preview_current)
        self.tree.compare_key_pressed.connect(self.compare_current)
        self.tree.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)

    def set_summary(
        self,
        summary: EncodeImpactSummary,
        evaluations: Sequence[VideoEvaluation],
        root_dir: str,
        target_codec_label: str,
    ) -> None:
        """집계 결과로 트리를 다시 만듦. 0개 항목은 숨기고 정보 없음은 마지막에 둠

        - 1, 2순위 항목(영상 변환)은 접힘을 기억할 필요가 없도록 새로 만들 때 펼쳐 둠
        """
        self._root_dir = root_dir
        self._target_codec_label = target_codec_label
        self._evals = {ev.video.filename: ev for ev in evaluations}
        self.tree.clear()
        expanded = 0
        for bucket in summary.buckets:
            if bucket.count == 0:
                continue
            group = self._add_group(bucket, summary.total_count)
            # - 변환 항목(검은 여백 잘라내기, 해상도 축소)은 처음 두 개까지만 펼쳐 둠
            if is_transform_impact(bucket.key) and expanded < TRANSFORM_AUTO_EXPAND_COUNT:
                group.setExpanded(True)
                expanded += 1
        if summary.unknown_count > 0:
            self._add_unknown_group(summary.unknown_filenames)

    def preview_current(self) -> None:
        """스페이스바: 현재 영상 행을 미리 보기로 넘김"""
        ev, path = self._current_row()
        if ev is None:
            return
        if path is None:
            self._notify_missing(ev)
            return
        self._media.preview_file(path)

    def compare_current(self) -> None:
        """화질 비교 전환 단축키: 현재 영상 행을 화질 비교로 넘김"""
        ev, path = self._current_row()
        if ev is None:
            return
        if path is None:
            self._notify_missing(ev)
            return
        self._media.compare_files(path)

    def _current_row(self) -> tuple[VideoEvaluation | None, str | None]:
        """현재 영상 행의 평가 결과와 절대 경로"""
        ev = self._evaluation_of(self.tree.currentItem())
        if ev is None:
            return None, None
        _state, path = row_file_state(self._root_dir, ev)
        return ev, path

    def _add_group(self, bucket: ImpactBucket, total_count: int) -> QTreeWidgetItem:
        """집계 항목 하나를 분류 배지와 함께 추가하고 아래에 영상 행을 나열

        - 머리글 줄은 첫 열을 펼쳐 제목이 잘리지 않게 하고, 배지는 델리게이트가 줄 오른쪽 끝에 그림
        """
        group = QTreeWidgetItem(self.tree, ["", ""])
        group.setText(NAME_COLUMN, impact_title(bucket, total_count))
        group.setToolTip(NAME_COLUMN, IMPACT_TOOLTIPS[bucket.key])
        category = IMPACT_CATEGORIES.get(bucket.key, "")
        group.setData(DETAIL_COLUMN, CATEGORY_ROLE, category)
        if category:
            group.setToolTip(DETAIL_COLUMN, CATEGORY_TOOLTIPS[category])
            # - 머리글 줄은 두 열을 합쳐 그리므로 둘째 열 툴팁이 닿지 않아 첫 열에 함께 적음
            group.setToolTip(NAME_COLUMN, f"{IMPACT_TOOLTIPS[bucket.key]}\n{CATEGORY_TOOLTIPS[category]}")
        group.setFirstColumnSpanned(True)
        for filename in bucket.filenames:
            self._add_child(group, filename, bucket.detail_of(filename))
        return group

    def _add_unknown_group(self, filenames: Sequence[str]) -> None:
        """정보 없음 항목을 추가"""
        group = QTreeWidgetItem(self.tree, [f"{UNKNOWN_LABEL} ({len(filenames)}개)"])
        group.setToolTip(NAME_COLUMN, UNKNOWN_TOOLTIP)
        group.setFirstColumnSpanned(True)
        for filename in filenames:
            self._add_child(group, filename, "")

    def _add_child(self, group: QTreeWidgetItem, filename: str, detail: str) -> None:
        """영상 행을 이름 칸과 우측 정렬 내역 칸으로 나눠 추가. 상세 툴팁에는 내역을 함께 씀"""
        child = QTreeWidgetItem(group, [filename, detail])
        child.setData(NAME_COLUMN, Qt.ItemDataRole.UserRole, filename)
        child.setTextAlignment(DETAIL_COLUMN, DETAIL_ALIGNMENT)
        if detail:
            child.setToolTip(DETAIL_COLUMN, detail)
        ev = self._evals.get(filename)
        if ev is not None:
            tooltip = format_row_tooltip(ev, self._root_dir, self._target_codec_label)
            child.setToolTip(NAME_COLUMN, tooltip if not detail else f"{tooltip}\n{detail}")

    def _evaluation_of(self, item: QTreeWidgetItem | None) -> VideoEvaluation | None:
        """영상 행이면 그 평가 결과, 집계 제목이거나 없으면 None"""
        if item is None or item.parent() is None:
            return None
        filename = item.data(NAME_COLUMN, Qt.ItemDataRole.UserRole)
        return self._evals.get(filename) if isinstance(filename, str) else None

    def _on_item_double_clicked(self, item: QTreeWidgetItem, _column: int) -> None:
        """더블클릭: 영상 행을 기본 플레이어로 재생하고 파일이 없으면 안내"""
        ev = self._evaluation_of(item)
        if ev is None:
            return
        _state, path = row_file_state(self._root_dir, ev)
        if path is None:
            self._notify_missing(ev)
            return
        self._media.open_file(path)

    def _notify_missing(self, ev: VideoEvaluation) -> None:
        """영상 파일이 없을 때 메인 표와 같은 안내 문구를 보여줌"""
        notice = missing_notice(count_missing_video_files(self._root_dir, [ev]), 1)
        if notice is not None:
            self._notice(self, "인코딩", notice)


def _show_warning(parent: QWidget, title: str, text: str) -> None:
    """파일 없음 안내 창"""
    QMessageBox.warning(parent, title, text)
