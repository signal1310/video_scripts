"""재인코딩 진행 창의 종료 예약 배너: 종료 예약 안내, 즉시 중지로 바꾸기, 종료 예약 취소

- 버튼은 신호만 보내고, 확인 창과 실제 취소는 진행 창과 조정자가 맡음
- 화면 스레드에서만 쓴다
"""

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget

from src.gui.dialogs.encode_progress_card import ICON_BUTTON_PX
from src.gui.styles.icons import create_icon_button, set_button_icon

CLOSE_PENDING_TEXT: str = "진행 중인 영상이 끝나면 종료합니다. 남은 영상은 시작하지 않습니다."
STOP_NOW_TIP: str = "진행 중인 영상을 바로 멈추고 대기로 되돌린 뒤 종료합니다. 확인 창을 한 번 거칩니다."
CANCEL_CLOSE_TIP: str = "종료 예약을 취소하고 배치를 계속합니다."


class CloseBanner(QFrame):
    """종료 예약 중에만 보이는 배너. 두 버튼은 각각 즉시 중지와 예약 취소 신호를 보냄"""

    stop_now_requested = pyqtSignal()
    cancel_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EncodeClosePendingBanner")
        self.setVisible(False)
        banner_text = QLabel(CLOSE_PENDING_TEXT, self)
        banner_text.setWordWrap(True)
        stop_now_btn = create_icon_button(
            self,
            STOP_NOW_TIP,
            lambda: self.stop_now_requested.emit(),
            accessible_name="즉시 중지로 바꾸기",
            fixed_size=ICON_BUTTON_PX,
        )
        cancel_btn = create_icon_button(
            self,
            CANCEL_CLOSE_TIP,
            lambda: self.cancel_requested.emit(),
            accessible_name="종료 예약 취소",
            fixed_size=ICON_BUTTON_PX,
        )
        set_button_icon(stop_now_btn, "ban", pad_right=0)
        set_button_icon(cancel_btn, "undo", pad_right=0)
        row = QHBoxLayout(self)
        row.setContentsMargins(8, 4, 8, 4)
        row.addWidget(banner_text, stretch=1)
        row.addWidget(stop_now_btn)
        row.addWidget(cancel_btn)
