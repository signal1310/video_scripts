"""재인코딩 중 앱 종료 확인 대화상자

- 기본 버튼은 계속이고, Esc도 계속으로 봄(닫기 무시)
- 본문은 일시중지 영상이 있는지에 따라 달라짐
"""

from PyQt6.QtWidgets import QMessageBox, QWidget

from src.core.encode.encode_close import CloseChoice

CLOSE_TITLE: str = "재인코딩 진행 중"
CLOSE_BODY: str = "재인코딩이 진행 중입니다. 지금 종료하면 진행 중인 영상은 처음부터 다시 시작해야 합니다. 원본 파일은 바뀌지 않습니다."
CLOSE_BODY_PAUSED: str = "일시중지한 영상이 있습니다. 종료하면 일시중지한 영상도 처음부터 다시 시작해야 합니다. 원본 파일은 바뀌지 않습니다."
IMMEDIATE_BUTTON_TEXT: str = "즉시 중지 후 종료"
AFTER_CURRENT_BUTTON_TEXT: str = "현재 영상 후 중지하고 종료"
KEEP_BUTTON_TEXT: str = "계속"


def ask_close(parent: QWidget, paused: bool) -> CloseChoice:
    """종료 확인 창. 세 버튼 중 하나의 선택을 돌려주며, 닫기나 Esc는 계속으로 봄"""
    box = QMessageBox(parent)
    box.setWindowTitle(CLOSE_TITLE)
    box.setText(CLOSE_BODY_PAUSED if paused else CLOSE_BODY)
    immediate = box.addButton(IMMEDIATE_BUTTON_TEXT, QMessageBox.ButtonRole.DestructiveRole)
    after_current = box.addButton(AFTER_CURRENT_BUTTON_TEXT, QMessageBox.ButtonRole.ActionRole)
    keep = box.addButton(KEEP_BUTTON_TEXT, QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(keep)
    box.setEscapeButton(keep)
    box.exec()
    clicked = box.clickedButton()
    if clicked is immediate:
        return CloseChoice.IMMEDIATE
    if clicked is after_current:
        return CloseChoice.AFTER_CURRENT
    return CloseChoice.KEEP
