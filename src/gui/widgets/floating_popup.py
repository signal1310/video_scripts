"""마우스 그랩 없이 앵커 옆에 띄우는 팝업의 공통 기반 모듈"""

from PyQt6.QtCore import QEvent, QObject, QPoint, QRect, Qt
from PyQt6.QtGui import QHideEvent, QKeyEvent, QMouseEvent, QShowEvent
from PyQt6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget

FLOATING_POPUP_SCREEN_MARGIN: int = 4


def inherit_stays_on_top(parent: QWidget | None, flags: Qt.WindowType) -> Qt.WindowType:
    """부모 창이 항상 맨 위 창이면 겹쳐 뜨는 팝업도 항상 맨 위로 두는 플래그 반환"""
    if parent is not None and parent.windowFlags() & Qt.WindowType.WindowStaysOnTopHint:
        return flags | Qt.WindowType.WindowStaysOnTopHint
    return flags


class FloatingPopupFrame(QFrame):
    """외부 클릭, Esc, 앱 비활성화 시 닫히는 Tool 창 팝업의 공통 기반

    - QMenu.exec과 달리 마우스를 그랩하지 않아 배경 위젯의 호버와 클릭이 막히지 않음
    - 표시 중에만 앱 이벤트 필터를 설치하고 숨김 시 해제
    - 토글 영역 클릭은 닫기만 하고 해당 클릭을 소비해 같은 앵커가 바로 다시 열리지 않게 함
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(
            parent,
            inherit_stays_on_top(
                parent,
                Qt.WindowType.Tool
                | Qt.WindowType.FramelessWindowHint
                | Qt.WindowType.NoDropShadowWindowHint,
            ),
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._app_filter_installed = False
        self._toggle_rect_global = QRect()

    def _build_card(self, object_name: str, padding: int) -> tuple[QFrame, QVBoxLayout]:
        """팝업 안쪽에 QSS 배경을 그리는 카드를 만들어 카드와 카드 내용 레이아웃을 반환

        반투명 창에서는 최상위 프레임의 QSS 배경이 그려지지 않으므로 카드를 자식으로 둠
        """
        card = QFrame(self)
        card.setObjectName(object_name)
        card.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        body = QVBoxLayout(card)
        body.setContentsMargins(padding, padding, padding, padding)
        return card, body

    def set_toggle_rect(self, rect_global: QRect) -> None:
        """앵커 토글 영역(전역 좌표)을 지정해 해당 영역 클릭을 닫기 전용으로 처리"""
        self._toggle_rect_global = QRect(rect_global)

    def show_clamped_at(self, pos_global: QPoint) -> None:
        """가용 화면 영역 안으로 보정한 전역 좌표에 팝업 표시

        - 닫힌 동안 자식 위젯을 숨겨 두었어도 내용 크기로 다시 맞춰 여는 높이가 남지 않게 함
        - 첫 표시에서야 폴리시가 끝나 크기 힌트가 확정되므로, 표시 직후 같은 턴에서 크기와 위치를 다시 맞춤
          (이벤트 루프를 넘기지 않아 잘못된 크기가 보이지 않음)
        - 표시가 예약한 레이아웃 요청은 이벤트 루프를 돌아야 크기 힌트에 반영되므로(실제 화면에서 첫 열림만 좁게 보임),
          다시 맞추기 전에 예약된 레이아웃 요청을 이 자리에서 처리함
        """
        self._fit_and_place(pos_global)
        self.show()
        QApplication.sendPostedEvents(None, int(QEvent.Type.LayoutRequest))
        self._fit_and_place(pos_global)

    def _fit_and_place(self, pos_global: QPoint) -> None:
        """내용 크기로 맞춘 뒤 가용 화면 안으로 보정한 전역 좌표로 이동"""
        lay = self.layout()
        if lay is not None:
            lay.activate()
        self.adjustSize()
        screen = QApplication.screenAt(pos_global) or QApplication.primaryScreen()
        pos = pos_global
        if screen is not None:
            avail = screen.availableGeometry()
            x = min(pos.x(), avail.right() - self.width() - FLOATING_POPUP_SCREEN_MARGIN)
            y = min(pos.y(), avail.bottom() - self.height() - FLOATING_POPUP_SCREEN_MARGIN)
            pos = QPoint(
                max(avail.left() + FLOATING_POPUP_SCREEN_MARGIN, x),
                max(avail.top() + FLOATING_POPUP_SCREEN_MARGIN, y),
            )
        self.move(pos)

    def _on_hidden(self) -> None:
        """숨김 직후 하위 클래스가 대기 중인 작업을 반영할 때 재정의하는 훅"""

    def _is_own_widget(self, widget: QWidget | None) -> bool:
        """이 팝업에 속한 위젯인지 부모 위젯 사슬로 판정

        - `isAncestorOf`는 같은 창 안의 자식만 인정하므로, QComboBox 목록처럼 별도 최상위 창으로
          뜨는 자식 창이 False가 되어 그 클릭을 바깥 클릭으로 오인함
        """
        current = widget
        while current is not None:
            if current is self:
                return True
            current = current.parentWidget()
        return False

    def _has_active_child_popup(self) -> bool:
        return self._is_own_widget(QApplication.activePopupWidget())

    def _remove_app_event_filter(self) -> None:
        app = QApplication.instance()
        if app is not None and self._app_filter_installed:
            app.removeEventFilter(self)
            self._app_filter_installed = False

    def showEvent(self, a0: QShowEvent | None) -> None:
        """표시 시 마우스 그랩 없이 외부 클릭, Esc, 앱 비활성화를 감지하는 필터 등록"""
        super().showEvent(a0)
        app = QApplication.instance()
        if app is not None and not self._app_filter_installed:
            app.installEventFilter(self)
            self._app_filter_installed = True

    def hideEvent(self, a0: QHideEvent | None) -> None:
        """숨김 시 이벤트 필터를 해제한 뒤 하위 클래스 훅 호출"""
        self._remove_app_event_filter()
        self._on_hidden()
        super().hideEvent(a0)

    def eventFilter(self, a0: QObject | None, a1: QEvent | None) -> bool:
        """외부 클릭, Esc, 앱 비활성화 시 팝업을 닫고 토글 영역 클릭은 소비하며 그 외 호버와 클릭은 차단하지 않음"""
        if a1 is None or not self.isVisible():
            return False

        etype = a1.type()
        if etype == QEvent.Type.ApplicationDeactivate:
            self.close()
            return False
        if etype == QEvent.Type.KeyPress and isinstance(a1, QKeyEvent):
            if a1.key() == Qt.Key.Key_Escape and not self._has_active_child_popup():
                self.close()
                return True
            return False
        if etype == QEvent.Type.MouseButtonPress and isinstance(a1, QMouseEvent):
            global_pos = a1.globalPosition().toPoint()
            if self.rect().contains(self.mapFromGlobal(global_pos)) or self._has_active_child_popup():
                return False
            if self._is_own_widget(QApplication.widgetAt(global_pos)):
                return False
            self.close()
            return bool(
                self._toggle_rect_global.isValid() and self._toggle_rect_global.contains(global_pos)
            )
        return False
