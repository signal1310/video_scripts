"""미리 보기 방식, 구간 이동 간격, 정보 표시 설정을 한곳에서 바꾸는 패널과 팝업 모듈"""

from collections.abc import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDropEvent, QShowEvent
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QScrollArea,
    QSlider,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from src.core.config import ClassifierConfig
from src.core.settings.preview_info import (
    DEFAULT_INFO_ANCHOR_X,
    DEFAULT_INFO_ANCHOR_Y,
    DEFAULT_INFO_BOX_HEIGHT,
    DEFAULT_INFO_BOX_WIDTH,
    DEFAULT_INFO_SCALE,
    INFO_LAYOUT_MULTI_LINE,
    INFO_LAYOUT_SINGLE_LINE,
    INFO_MODE_DETAIL,
    INFO_MODE_SUMMARY,
    INFO_SCALE_MAX,
    INFO_SCALE_MIN,
    INFO_SCALE_STEP,
)
from src.core.settings.ui_state import VALID_PREVIEW_SEEK_SECONDS, UiState
from src.gui.constants import PREVIEW_BACKEND_LABELS
from src.gui.preview_info_items import INFO_GROUPS, INFO_MODE_LABELS, info_label
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.widgets.floating_popup import FloatingPopupFrame

_PANEL_WIDTH_PX: int = 340
_ITEM_AREA_MAX_HEIGHT_PX: int = 240
_PANEL_PADDING_PX: int = 12
_PANEL_SPACING_PX: int = 8
_ICON_SIZE_PX: int = 14
_RESET_BUTTON_SIZE_PX: int = 26
_SCALE_SLIDER_WIDTH_PX: int = 110
_SCALE_LABEL_WIDTH_PX: int = 44
_LAYOUT_LABELS: tuple[tuple[str, str], ...] = (
    (INFO_LAYOUT_MULTI_LINE, "여러 줄"),
    (INFO_LAYOUT_SINGLE_LINE, "한 줄"),
)
_SUMMARY_COLUMN_TITLE: str = "요약"
_DETAIL_COLUMN_TITLE: str = "상세"
_RESET_ICON: str = "rotate-ccw"
_RESET_TEXT: str = "위치와 크기 초기화"
_RESET_TIP: str = "정보 표시의 위치, 정보 상자 크기, 정보 크기를 처음 값으로 되돌립니다."
_ORDER_LIST_HEIGHT_PX: int = 150
_SUMMARY_ORDER_TITLE: str = "요약 표시 순서"
_DETAIL_ORDER_TITLE: str = "상세 표시 순서"
_ORDER_TIP: str = "끌어서 표시 순서를 바꿉니다. 목록에서 뺄 항목은 위의 체크를 끄세요."


def order_keys(order_list: QListWidget) -> list[str]:
    """목록 행 순서대로 항목 키를 모음"""
    keys: list[str] = []
    for row in range(order_list.count()):
        item = order_list.item(row)
        if item is not None:
            keys.append(str(item.data(Qt.ItemDataRole.UserRole)))
    return keys


class PreviewOrderList(QListWidget):
    """체크된 항목을 표시 순서대로 보여 주고, 끌어서 놓은 뒤 reordered를 내보내는 목록"""

    reordered = pyqtSignal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("PreviewOrderList")
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setToolTip(_ORDER_TIP)
        self.setFixedHeight(_ORDER_LIST_HEIGHT_PX)

    def dropEvent(self, event: QDropEvent | None) -> None:
        """끌어 놓기가 끝난 뒤 바뀐 순서를 알림"""
        super().dropEvent(event)
        self.reordered.emit()


class PreviewSettingsPanel(QWidget):
    """미리 보기 설정을 모아 보여 주고 바꾸는 패널, 바뀐 값은 UI 상태에 바로 쓰고 changed로 알림

    - 단축키 설정은 단축키 화면이 맡아 이 패널에 두지 않음
    """

    changed = pyqtSignal()

    def __init__(
        self,
        get_ui: Callable[[], UiState],
        get_config: Callable[[], ClassifierConfig],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._get_ui = get_ui
        self._get_config = get_config
        self._summary_boxes: dict[str, QCheckBox] = {}
        self._detail_boxes: dict[str, QCheckBox] = {}
        self._order_lists: dict[str, PreviewOrderList] = {}
        self.setFixedWidth(_PANEL_WIDTH_PX)

        self._combo_backend = self._make_combo(PREVIEW_BACKEND_LABELS)
        self._combo_seek = self._make_combo(
            tuple((seconds, f"{seconds}초") for seconds in VALID_PREVIEW_SEEK_SECONDS)
        )
        self._combo_mode = self._make_combo(tuple(INFO_MODE_LABELS.items()))
        self._combo_layout = self._make_combo(_LAYOUT_LABELS)
        self._slider_scale = QSlider(Qt.Orientation.Horizontal, self)
        self._slider_scale.setRange(INFO_SCALE_MIN // INFO_SCALE_STEP, INFO_SCALE_MAX // INFO_SCALE_STEP)
        self._slider_scale.setFixedWidth(_SCALE_SLIDER_WIDTH_PX)
        self._slider_scale.setToolTip("정보 표시의 글자 크기를 조절합니다. 정보 표시 위에서 마우스 휠을 돌려도 바꿀 수 있습니다.")
        self._label_scale = QLabel(self)
        self._label_scale.setFixedWidth(_SCALE_LABEL_WIDTH_PX)
        self._label_scale.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._btn_reset_position = QToolButton(self)
        self._btn_reset_position.setIcon(
            get_icon(_RESET_ICON, color=get_active_palette().text_primary, size=_ICON_SIZE_PX)
        )
        self._btn_reset_position.setFixedSize(_RESET_BUTTON_SIZE_PX, _RESET_BUTTON_SIZE_PX)
        self._btn_reset_position.setToolTip(_RESET_TIP)
        self._btn_reset_position.setAccessibleName(_RESET_TEXT)
        self._btn_reset_position.setCursor(Qt.CursorShape.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(_PANEL_SPACING_PX)
        layout.addLayout(self._row("미리 보기 방식", self._combo_backend))
        layout.addLayout(self._row("구간 이동 간격", self._combo_seek))
        layout.addWidget(self._separator())
        layout.addLayout(self._row("정보 표시", self._combo_mode))
        layout.addLayout(self._row("정보 배치", self._combo_layout, self._btn_reset_position))
        layout.addLayout(self._row("정보 크기", self._slider_scale, self._label_scale))
        layout.addWidget(self._build_item_area())
        layout.addLayout(self._build_order_area())

        self.refresh()
        self._combo_backend.currentIndexChanged.connect(self._on_backend_changed)
        self._combo_seek.currentIndexChanged.connect(self._on_seek_changed)
        self._combo_mode.currentIndexChanged.connect(self._on_mode_changed)
        self._combo_layout.currentIndexChanged.connect(self._on_layout_changed)
        self._slider_scale.valueChanged.connect(self._on_scale_changed)
        self._btn_reset_position.clicked.connect(self._on_reset_position)

    def refresh(self) -> None:
        """UI 상태의 현재 값으로 모든 조작 요소를 맞춤, 값 변경 신호는 내보내지 않음"""
        ui = self._get_ui()
        info = ui.preview_info
        self._select(self._combo_backend, ui.preview_backend)
        self._select(self._combo_seek, ui.preview_seek_seconds)
        self._select(self._combo_mode, info.mode)
        self._select(self._combo_layout, info.layout)
        self._slider_scale.blockSignals(True)
        self._slider_scale.setValue(info.scale // INFO_SCALE_STEP)
        self._slider_scale.blockSignals(False)
        self._label_scale.setText(f"{info.scale}%")
        for key, box in self._summary_boxes.items():
            self._set_checked(box, key in info.summary_items)
        for key, box in self._detail_boxes.items():
            self._set_checked(box, key in info.detail_items)
        self._rebuild_order_lists()

    def _build_item_area(self) -> QWidget:
        """항목마다 요약, 상세 단계에 보일지 고르는 체크 표를 스크롤 영역에 구성"""
        config = self._get_config()
        content = QWidget()
        grid = QGridLayout(content)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(_PANEL_SPACING_PX)
        grid.setVerticalSpacing(2)
        grid.setColumnStretch(0, 1)
        row = 0
        grid.addWidget(QLabel("정보 표시 항목", content), row, 0)
        grid.addWidget(self._centered(_SUMMARY_COLUMN_TITLE, content), row, 1)
        grid.addWidget(self._centered(_DETAIL_COLUMN_TITLE, content), row, 2)
        for group_title, keys in INFO_GROUPS:
            row += 1
            title = QLabel(group_title, content)
            title.setObjectName("GroupHeaderTitle")
            grid.addWidget(title, row, 0, 1, 3)
            for key in keys:
                row += 1
                grid.addWidget(QLabel(info_label(key, config), content), row, 0)
                grid.addWidget(self._item_box(key, self._summary_boxes, content), row, 1)
                grid.addWidget(self._item_box(key, self._detail_boxes, content), row, 2)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setMaximumHeight(_ITEM_AREA_MAX_HEIGHT_PX)
        scroll.setWidget(content)
        return scroll

    def _build_order_area(self) -> QHBoxLayout:
        """요약, 상세 단계별로 체크된 항목의 표시 순서를 끌어서 바꾸는 두 목록"""
        columns = QHBoxLayout()
        columns.setSpacing(_PANEL_SPACING_PX)
        for level, title in (
            (INFO_MODE_SUMMARY, _SUMMARY_ORDER_TITLE),
            (INFO_MODE_DETAIL, _DETAIL_ORDER_TITLE),
        ):
            order_list = PreviewOrderList(self)
            order_list.reordered.connect(lambda level=level: self._on_order_dropped(level))
            self._order_lists[level] = order_list
            column = QVBoxLayout()
            column.setSpacing(2)
            column.addWidget(QLabel(title, self))
            column.addWidget(order_list)
            columns.addLayout(column)
        return columns

    def _rebuild_order_lists(self) -> None:
        """두 목록을 상태의 표시 순서대로 다시 채움"""
        config = self._get_config()
        info = self._get_ui().preview_info
        for level, keys in ((INFO_MODE_SUMMARY, info.summary_items), (INFO_MODE_DETAIL, info.detail_items)):
            order_list = self._order_lists[level]
            order_list.clear()
            for key in keys:
                item = QListWidgetItem(info_label(key, config))
                item.setData(Qt.ItemDataRole.UserRole, key)
                order_list.addItem(item)

    def _item_box(self, key: str, boxes: dict[str, QCheckBox], parent: QWidget) -> QWidget:
        box = QCheckBox(parent)
        box.toggled.connect(self._on_items_toggled)
        boxes[key] = box
        holder = QWidget(parent)
        holder_layout = QHBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 0, 0)
        holder_layout.addWidget(box, alignment=Qt.AlignmentFlag.AlignCenter)
        return holder

    @staticmethod
    def _centered(text: str, parent: QWidget) -> QLabel:
        label = QLabel(text, parent)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        return label

    @staticmethod
    def _make_combo(entries: tuple[tuple[object, str], ...]) -> QComboBox:
        combo = QComboBox()
        for data, text in entries:
            combo.addItem(text, data)
        return combo

    @staticmethod
    def _row(title: str, *widgets: QWidget) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(_PANEL_SPACING_PX)
        row.addWidget(QLabel(title), 1)
        for widget in widgets:
            row.addWidget(widget)
        return row

    @staticmethod
    def _separator() -> QFrame:
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setFrameShadow(QFrame.Shadow.Sunken)
        return line

    @staticmethod
    def _select(combo: QComboBox, data: object) -> None:
        index = combo.findData(data)
        if index >= 0:
            combo.blockSignals(True)
            combo.setCurrentIndex(index)
            combo.blockSignals(False)

    @staticmethod
    def _set_checked(box: QCheckBox, checked: bool) -> None:
        box.blockSignals(True)
        box.setChecked(checked)
        box.blockSignals(False)

    def _on_backend_changed(self, _index: int) -> None:
        self._get_ui().preview_backend = str(self._combo_backend.currentData())
        self.changed.emit()

    def _on_seek_changed(self, _index: int) -> None:
        self._get_ui().preview_seek_seconds = int(self._combo_seek.currentData())
        self.changed.emit()

    def _on_mode_changed(self, _index: int) -> None:
        self._get_ui().preview_info.mode = str(self._combo_mode.currentData())
        self.changed.emit()

    def _on_layout_changed(self, _index: int) -> None:
        self._get_ui().preview_info.layout = str(self._combo_layout.currentData())
        self.changed.emit()

    def _on_scale_changed(self, value: int) -> None:
        scale = value * INFO_SCALE_STEP
        self._get_ui().preview_info.scale = scale
        self._label_scale.setText(f"{scale}%")
        self.changed.emit()

    def _on_items_toggled(self, _checked: bool) -> None:
        info = self._get_ui().preview_info
        info.summary_items = self._checked_in_order(self._summary_boxes, info.summary_items)
        info.detail_items = self._checked_in_order(self._detail_boxes, info.detail_items)
        self._rebuild_order_lists()
        self.changed.emit()

    @staticmethod
    def _checked_in_order(boxes: dict[str, QCheckBox], current: list[str]) -> list[str]:
        """현재 표시 순서를 유지한 채 체크된 항목만 남기고, 새로 체크한 항목은 뒤에 덧붙임"""
        checked = {key for key, box in boxes.items() if box.isChecked()}
        kept = [key for key in current if key in checked]
        return kept + [key for key in boxes if key in checked and key not in kept]

    def _on_order_dropped(self, level: str) -> None:
        """끌어 놓은 목록의 순서를 해당 단계의 표시 순서로 저장"""
        keys = order_keys(self._order_lists[level])
        info = self._get_ui().preview_info
        if level == INFO_MODE_SUMMARY:
            info.summary_items = keys
        else:
            info.detail_items = keys
        self.changed.emit()

    def _on_reset_position(self) -> None:
        info = self._get_ui().preview_info
        info.anchor_x = DEFAULT_INFO_ANCHOR_X
        info.anchor_y = DEFAULT_INFO_ANCHOR_Y
        info.box_width = DEFAULT_INFO_BOX_WIDTH
        info.box_height = DEFAULT_INFO_BOX_HEIGHT
        info.scale = DEFAULT_INFO_SCALE
        self.refresh()
        self.changed.emit()


class PreviewSettingsPopup(FloatingPopupFrame):
    """미리 보기 설정 패널을 담아 앵커 옆에 띄우는 팝업, 열릴 때마다 현재 상태를 다시 읽음"""

    def __init__(self, panel: PreviewSettingsPanel, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._panel = panel
        _card, body = self._build_card("PreviewSettingsPopupCard", _PANEL_PADDING_PX)
        body.addWidget(panel)

    def showEvent(self, a0: QShowEvent | None) -> None:
        """표시 직전에 패널 값을 최신 상태로 맞춤"""
        self._panel.refresh()
        super().showEvent(a0)
