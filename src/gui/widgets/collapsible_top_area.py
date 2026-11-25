"""테이블 위 상단 영역을 탭 줄로 접고, 접힌 동안 선택한 영역을 팝업으로 여는 위젯 모듈"""

from dataclasses import dataclass

from PyQt6.QtCore import QEvent, QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from src.core.settings.shortcuts import DEFAULT_SHORTCUTS, format_tooltip_with_shortcut
from src.gui.constants import (
    CARD_BORDER_PX,
    HEADER_PADDING_X_PX,
    TOOLBAR_ICON_BTN_SIZE,
    TOOLBAR_ICON_SIZE_PX,
)
from src.gui.styles.icons import create_icon_button, set_button_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.gui.widgets.eliding_label import ElidingLabel
from src.gui.widgets.floating_popup import FloatingPopupFrame

TOP_AREA_CONTENT_SPACING_PX: int = 12
TOP_AREA_TAB_STRIP_SPACING_PX: int = 2
TOP_AREA_POPUP_PADDING_PX: int = 8
TOP_AREA_EXPAND_BASE_TOOLTIP: str = "상단 영역을 펼칩니다."
TOP_AREA_EXPAND_TOOLTIP: str = format_tooltip_with_shortcut(
    TOP_AREA_EXPAND_BASE_TOOLTIP, DEFAULT_SHORTCUTS["view.toggle_top_area"]
)
TOP_AREA_EXPAND_NAME: str = "상단 영역 펼치기"
TOP_AREA_EXPAND_ICON: str = "panel-top-open"
# 펼친 상태의 헤더 접기 버튼과 같은 x 위치에 두도록 헤더 테두리와 안쪽 여백을 더함
TOP_AREA_EXPAND_RIGHT_MARGIN_PX: int = CARD_BORDER_PX + HEADER_PADDING_X_PX
# 접힌 줄에서 펼치기 버튼과 폴더 이름 사이 여백
TOP_AREA_SUMMARY_GAP_PX: int = 8
TOP_AREA_SUMMARY_OBJECT_NAME: str = "TopAreaSummaryLabel"


@dataclass(frozen=True)
class TopAreaSection:
    """탭 이름과 그 탭이 여는 상단 영역 위젯 쌍"""

    label: str
    widget: QWidget


class TopAreaSummaryLabel(ElidingLabel):
    """접힌 줄의 폴더 이름 라벨. 폭이 모자라면 오른쪽을 말줄임으로 줄임

    - 최대 폭을 문구 폭으로 묶어 남는 공간이 탭 앞 여백으로 흘러가지 않게 함
    - 좁은 창에서는 말줄임으로 줄어들어 접힌 줄이 한 줄 높이를 유지
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, pin_width=True)
        self.setObjectName(TOP_AREA_SUMMARY_OBJECT_NAME)


class TopAreaPopup(FloatingPopupFrame):
    """접힌 상단 영역의 선택 영역을 테이블 위에 붙여 표시하는 팝업

    - 영역 위젯은 열 때 팝업 레이아웃으로 옮기고, 닫히면 dismissed로 알려 호출자가 원래 자리로 되돌리게 함
    """

    dismissed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._card, self._body = self._build_card("TopAreaPopupCard", TOP_AREA_POPUP_PADDING_PX)

    def show_section(
        self,
        widget: QWidget,
        anchor_global: QPoint,
        toggle_rect_global: QRect,
        width: int,
    ) -> None:
        """영역 위젯을 넣고 너비를 고정한 뒤 앵커 아래에 표시하며, 탭 클릭은 닫기 전용으로 처리"""
        self._body.addWidget(widget)
        widget.show()
        # 카드의 캐시된 sizeHint가 이전 영역 기준으로 남으므로 갱신을 알린 뒤 높이를 고정
        self._card.updateGeometry()
        self.setFixedWidth(width)
        self.setFixedHeight(self.sizeHint().height())
        self.set_toggle_rect(toggle_rect_global)
        self.show_clamped_at(anchor_global)

    def release_section(self, widget: QWidget) -> None:
        """팝업 레이아웃에서 영역 위젯을 분리해 호출자가 다른 부모로 옮길 수 있게 함"""
        self._body.removeWidget(widget)

    def _on_hidden(self) -> None:
        self.dismissed.emit()


class CollapsibleTopArea(QWidget, ThemeAwareMixin):
    """탭 줄과 영역 본문을 묶어 펼침과 접힘을 전환하는 상단 영역

    - 펼친 상태에서는 탭 줄을 숨기고, 접기 버튼은 헤더 쪽에 둠
    - 접힌 상태에서는 탭 줄만 보이며 펼치기 버튼으로 본문을 펼침
    - 접힌 상태에서 탭을 누르면 해당 영역을 팝업으로 열고, 펼친 상태에서는 탭 클릭이 무시됨
    - 상태 변경은 collapsed_changed로 알림
    """

    collapsed_changed = pyqtSignal(bool)

    def __init__(self, sections: list[TopAreaSection], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CollapsibleTopArea")
        # 세로 여유 공간을 받지 않도록 최대 높이를 sizeHint로 제한해 접힌 상태에서 테이블이 공간을 가져가게 함
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self._sections: list[TopAreaSection] = list(sections)
        self._collapsed: bool = False
        self._popup_index: int | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(TOP_AREA_CONTENT_SPACING_PX)

        self._tab_strip = QFrame(self)
        self._tab_strip.setObjectName("TopAreaTabStrip")
        # 세로 여유 공간을 받아 늘어나지 않도록 고정해 접힌 상단 영역이 탭 높이만 차지하게 함
        self._tab_strip.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        strip_layout = QHBoxLayout(self._tab_strip)
        strip_layout.setContentsMargins(0, 0, TOP_AREA_EXPAND_RIGHT_MARGIN_PX, 0)
        strip_layout.setSpacing(TOP_AREA_TAB_STRIP_SPACING_PX)
        # - 접힌 줄은 왼쪽부터 펼치기 버튼, 폴더 이름, 영역 탭, 여백 순서로 한 줄에 놓음
        self._expand_btn = create_icon_button(
            self._tab_strip,
            TOP_AREA_EXPAND_TOOLTIP,
            lambda: self.set_collapsed(False),
            accessible_name=TOP_AREA_EXPAND_NAME,
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
            object_name="TopAreaExpandButton",
        )
        set_button_icon(
            self._expand_btn, TOP_AREA_EXPAND_ICON, size=TOOLBAR_ICON_SIZE_PX, pad_right=0
        )
        strip_layout.addWidget(self._expand_btn)
        strip_layout.addSpacing(TOP_AREA_SUMMARY_GAP_PX)
        self._summary_label = TopAreaSummaryLabel(self._tab_strip)
        strip_layout.addWidget(self._summary_label)
        strip_layout.addSpacing(TOP_AREA_CONTENT_SPACING_PX)
        self._tab_buttons: list[QPushButton] = []
        for index, section in enumerate(self._sections):
            tab = QPushButton(section.label, self._tab_strip)
            tab.setObjectName("TopAreaTab")
            tab.setCheckable(True)
            tab.setCursor(Qt.CursorShape.PointingHandCursor)
            tab.setToolTip(f"{section.label} 영역을 테이블 위에 엽니다.")
            tab.setAccessibleName(section.label)
            tab.clicked.connect(lambda _checked=False, i=index: self._on_tab_clicked(i))
            strip_layout.addWidget(tab)
            self._tab_buttons.append(tab)
        strip_layout.addStretch(1)
        root.addWidget(self._tab_strip)

        self._content = QWidget(self)
        self._content_layout = QVBoxLayout(self._content)
        self._content_layout.setContentsMargins(0, 0, 0, 0)
        self._content_layout.setSpacing(TOP_AREA_CONTENT_SPACING_PX)
        for section in self._sections:
            self._content_layout.addWidget(section.widget)
        root.addWidget(self._content)

        self._popup = TopAreaPopup(self)
        self._popup.dismissed.connect(self._restore_popup_section)

        self._tab_strip.setVisible(self._collapsed)
        self._fit_tab_widths()
        self._refresh_controls()
        self.init_theme_listener()

    @property
    def collapsed(self) -> bool:
        """상단 영역이 접힌 상태인지 반환(펼치기 전까지 유지되는 접힘 상태)"""
        return self._collapsed

    @property
    def open_section_index(self) -> int | None:
        """접힌 상태에서 팝업으로 열린 영역의 인덱스. 열린 영역이 없으면 None"""
        return self._popup_index

    @property
    def expand_button(self) -> QPushButton:
        """접힌 상태의 펼치기 버튼"""
        return self._expand_btn

    def update_shortcut_tooltip(self, collapse_key: str) -> None:
        """상단 영역 접기/펼치기 단축키 변경을 펼치기 버튼 툴팁에 반영"""
        self._expand_btn.setToolTip(
            format_tooltip_with_shortcut(TOP_AREA_EXPAND_BASE_TOOLTIP, collapse_key)
        )

    @property
    def summary_label(self) -> TopAreaSummaryLabel:
        """접힌 줄 왼쪽에 보이는 요약 라벨"""
        return self._summary_label

    def set_summary_text(self, text: str) -> None:
        """접힌 줄에 보일 요약 문구를 지정. 빈 문구면 라벨을 숨겨 줄 간격을 남기지 않음"""
        self._summary_label.set_full_text(text)
        self._summary_label.setVisible(bool(text))
        self._tab_strip.updateGeometry()
    def tab_button(self, index: int) -> QPushButton:
        """지정한 순서의 영역 탭 버튼"""
        return self._tab_buttons[index]

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 팔레트에 맞춰 펼치기 버튼의 패널 열기 아이콘 색과 요약 글자색 갱신"""
        set_button_icon(
            self._expand_btn,
            TOP_AREA_EXPAND_ICON,
            palette=palette,
            size=TOOLBAR_ICON_SIZE_PX,
            pad_right=0,
        )
        self._summary_label.set_color(palette.text_secondary)
        self._fit_tab_widths()

    def changeEvent(self, a0: QEvent | None) -> None:
        """스타일 변경 시 탭 너비를 다시 맞춰 선택 상태와 무관하게 너비가 고정되도록 함"""
        super().changeEvent(a0)
        if a0 is not None and a0.type() == QEvent.Type.StyleChange:
            self._fit_tab_widths()

    def _fit_tab_widths(self) -> None:
        """탭을 펼치기 버튼과 같은 높이로 고정하고 너비는 스타일 기준 sizeHint로 확정"""
        for tab in self._tab_buttons:
            tab.ensurePolished()
            tab.setFixedSize(tab.sizeHint().width(), TOOLBAR_ICON_BTN_SIZE)

    def set_collapsed(self, collapsed: bool) -> None:
        """접힘 상태를 지정하고 바뀐 경우에만 본문 표시, 탭 상태, 시그널을 갱신"""
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        if not collapsed:
            self.close_section()
        self._tab_strip.setVisible(collapsed)
        self._content.setVisible(not collapsed)
        self.updateGeometry()
        self._refresh_controls()
        self.collapsed_changed.emit(collapsed)

    def toggle(self) -> None:
        """현재 상태의 반대로 접힘 상태 전환"""
        self.set_collapsed(not self._collapsed)

    def open_section(self, index: int) -> None:
        """접힌 상태에서 지정한 영역을 테이블 위 팝업으로 열고, 펼친 상태에서는 아무 동작도 하지 않음"""
        if not self._collapsed:
            return
        self.close_section()
        tab = self._tab_buttons[index]
        self._popup_index = index
        anchor = self._tab_strip.mapToGlobal(QPoint(0, self._tab_strip.height()))
        tab_rect = QRect(tab.mapToGlobal(QPoint(0, 0)), tab.size())
        self._popup.show_section(self._sections[index].widget, anchor, tab_rect, self.width())
        self._refresh_controls()

    def close_section(self) -> None:
        """열린 팝업을 닫고 영역 위젯을 본문으로 되돌림. 열린 팝업이 없으면 아무 동작도 하지 않음"""
        self._popup.close()
        self._restore_popup_section()

    def _on_tab_clicked(self, index: int) -> None:
        if self._popup_index == index:
            self.close_section()
        else:
            self.open_section(index)
        self._refresh_controls()

    def _restore_popup_section(self) -> None:
        index = self._popup_index
        if index is None:
            return
        self._popup_index = None
        widget = self._sections[index].widget
        self._popup.release_section(widget)
        self._content_layout.insertWidget(index, widget)
        self._refresh_controls()

    def _refresh_controls(self) -> None:
        for index, tab in enumerate(self._tab_buttons):
            tab.setChecked(index == self._popup_index)


def toggle_top_area(window: QWidget) -> None:
    """창 안의 상단 영역을 찾아 접기, 펼치기 전환. 상단 영역이 없으면 아무 동작도 하지 않음"""
    area = window.findChild(CollapsibleTopArea)
    if area is not None:
        area.toggle()
