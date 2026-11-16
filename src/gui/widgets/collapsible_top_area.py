"""테이블 위 상단 영역을 탭 줄로 접고, 접힌 동안 선택한 영역을 팝업으로 여는 위젯 모듈"""

from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from src.gui.widgets.floating_popup import FloatingPopupFrame

TOP_AREA_CONTENT_SPACING_PX: int = 12
TOP_AREA_TAB_STRIP_SPACING_PX: int = 4
TOP_AREA_POPUP_PADDING_PX: int = 8
TOP_AREA_PIN_TEXT: str = "펼쳐 두기"
TOP_AREA_UNPIN_TEXT: str = "펼쳐 두기 해제"
TOP_AREA_PIN_TOOLTIP: str = "상단 영역을 펼쳐 둡니다. [Ctrl+F1]"
TOP_AREA_UNPIN_TOOLTIP: str = "펼쳐 두기를 해제합니다. [Ctrl+F1]"
TOP_AREA_PIN_NAME: str = "상단 영역 펼쳐 두기"
TOP_AREA_UNPIN_NAME: str = "상단 영역 펼쳐 두기 해제"


@dataclass(frozen=True)
class TopAreaSection:
    """탭 이름과 그 탭이 여는 상단 영역 위젯 쌍"""

    label: str
    widget: QWidget


class TopAreaPopup(FloatingPopupFrame):
    """접힌 상단 영역의 선택 영역을 테이블 위에 붙여 표시하는 팝업

    - 영역 위젯은 열 때 팝업 레이아웃으로 옮기고, 닫히면 dismissed로 알려 호출자가 원래 자리로 되돌리게 함
    """

    dismissed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        # 반투명 창에서는 최상위 프레임의 QSS 배경이 그려지지 않으므로 카드를 자식으로 둠
        self._card = QFrame(self)
        self._card.setObjectName("TopAreaPopupCard")
        self._card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._card)
        self._body = QVBoxLayout(self._card)
        self._body.setContentsMargins(
            TOP_AREA_POPUP_PADDING_PX,
            TOP_AREA_POPUP_PADDING_PX,
            TOP_AREA_POPUP_PADDING_PX,
            TOP_AREA_POPUP_PADDING_PX,
        )

    def show_section(
        self,
        widget: QWidget,
        anchor_global: QPoint,
        toggle_rect_global: QRect,
        width: int,
    ) -> None:
        """영역 위젯을 넣고 너비를 고정한 뒤 앵커 아래에 표시하며, 탭 클릭은 닫기 전용으로 처리"""
        self._body.addWidget(widget)
        self.setFixedWidth(width)
        self.set_toggle_rect(toggle_rect_global)
        self.show_clamped_at(anchor_global)
        widget.show()

    def release_section(self, widget: QWidget) -> None:
        """팝업 레이아웃에서 영역 위젯을 분리해 호출자가 다른 부모로 옮길 수 있게 함"""
        self._body.removeWidget(widget)

    def _on_hidden(self) -> None:
        self.dismissed.emit()


class CollapsibleTopArea(QWidget):
    """탭 줄과 영역 본문을 묶어 펼침과 접힘을 전환하는 상단 영역

    - 탭 줄은 항상 보이며, 펼쳐 두기 버튼은 본문 표시 상태를 바꿈
    - 접힌 상태에서 탭을 누르면 해당 영역을 팝업으로 열고, 펼친 상태에서는 탭 클릭이 무시됨
    - 상태 변경은 collapsed_changed로 알림
    """

    collapsed_changed = pyqtSignal(bool)

    def __init__(self, sections: list[TopAreaSection], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("CollapsibleTopArea")
        self._sections: list[TopAreaSection] = list(sections)
        self._collapsed: bool = False
        self._popup_index: int | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(TOP_AREA_CONTENT_SPACING_PX)

        self._tab_strip = QWidget(self)
        strip_layout = QHBoxLayout(self._tab_strip)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        strip_layout.setSpacing(TOP_AREA_TAB_STRIP_SPACING_PX)
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
        self._pin_btn = QPushButton(self._tab_strip)
        self._pin_btn.setObjectName("TopAreaPin")
        self._pin_btn.setCheckable(True)
        self._pin_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._pin_btn.clicked.connect(self.toggle)
        strip_layout.addWidget(self._pin_btn)
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

        self._refresh_controls()

    @property
    def collapsed(self) -> bool:
        """상단 영역이 접힌 상태인지 반환(펼쳐 두기가 꺼진 상태)"""
        return self._collapsed

    @property
    def open_section_index(self) -> int | None:
        """접힌 상태에서 팝업으로 열린 영역의 인덱스. 열린 영역이 없으면 None"""
        return self._popup_index

    @property
    def pin_button(self) -> QPushButton:
        """펼쳐 두기 토글 버튼"""
        return self._pin_btn

    def tab_button(self, index: int) -> QPushButton:
        """지정한 순서의 영역 탭 버튼"""
        return self._tab_buttons[index]

    def set_collapsed(self, collapsed: bool) -> None:
        """접힘 상태를 지정하고 바뀐 경우에만 본문 표시, 탭 상태, 시그널을 갱신"""
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        if not collapsed:
            self.close_section()
        self._content.setVisible(not collapsed)
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
        pinned = not self._collapsed
        self._pin_btn.setChecked(pinned)
        if pinned:
            self._pin_btn.setText(TOP_AREA_UNPIN_TEXT)
            self._pin_btn.setToolTip(TOP_AREA_UNPIN_TOOLTIP)
            self._pin_btn.setAccessibleName(TOP_AREA_UNPIN_NAME)
        else:
            self._pin_btn.setText(TOP_AREA_PIN_TEXT)
            self._pin_btn.setToolTip(TOP_AREA_PIN_TOOLTIP)
            self._pin_btn.setAccessibleName(TOP_AREA_PIN_NAME)
        for index, tab in enumerate(self._tab_buttons):
            tab.setChecked(index == self._popup_index)


def toggle_top_area(window: QWidget) -> None:
    """창 안의 상단 영역을 찾아 접기, 펼치기 전환. 상단 영역이 없으면 아무 동작도 하지 않음"""
    area = window.findChild(CollapsibleTopArea)
    if area is not None:
        area.toggle()
