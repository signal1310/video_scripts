"""줄바꿈 흐름 배치 레이아웃: 칸을 왼쪽에서 오른쪽으로 놓고 폭을 넘치면 다음 줄로 넘김

- 칩 줄과 범례 줄이 같은 배치를 쓰도록 분리함. 두 모듈이 서로 import하지 않게 하는 것이 목적
"""

from PyQt6.QtCore import QPoint, QRect, QSize, Qt
from PyQt6.QtWidgets import QLayout, QLayoutItem, QWidget

FLOW_SPACING_PX: int = 6


class FlowLayout(QLayout):
    """가로로 채우다 넘치면 다음 줄로 넘기는 배치. 칩 묶음이 창 폭에 따라 줄바꿈되게 함"""

    def __init__(self, parent: QWidget | None = None, spacing: int = FLOW_SPACING_PX) -> None:
        super().__init__(parent)
        self._items: list[QLayoutItem] = []
        self._gap: int = spacing

    def addItem(self, item: QLayoutItem | None) -> None:
        """레이아웃에 칸을 더함"""
        if item is not None:
            self._items.append(item)

    def count(self) -> int:
        """칸 수"""
        return len(self._items)

    def itemAt(self, index: int) -> QLayoutItem | None:
        """번호의 칸. 범위 밖이면 없음"""
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int) -> QLayoutItem | None:
        """번호의 칸을 떼어 돌려줌. 범위 밖이면 없음"""
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:
        """늘어날 방향 없음"""
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:
        """폭에 따라 높이가 바뀌므로 참"""
        return True

    def heightForWidth(self, width: int) -> int:
        """주어진 폭에서 칸들을 줄바꿈한 전체 높이"""
        return self._place(QRect(0, 0, width, 0), dry_run=True)

    def setGeometry(self, rect: QRect) -> None:
        """칸들을 주어진 영역 안에 줄바꿈하여 놓음"""
        super().setGeometry(rect)
        self._place(rect, dry_run=False)

    def sizeHint(self) -> QSize:
        """권장 크기는 최소 크기와 같음"""
        return self.minimumSize()

    def minimumSize(self) -> QSize:
        """칸들의 최소 크기를 모두 담는 크기"""
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(margins.left() + margins.right(), margins.top() + margins.bottom())

    def _place(self, rect: QRect, *, dry_run: bool) -> int:
        """칸을 왼쪽에서 오른쪽으로 놓고 넘치면 다음 줄. 놓은 뒤 쓴 높이를 돌려줌"""
        margins = self.contentsMargins()
        left = rect.x() + margins.left()
        right = rect.right() - margins.right()
        x = left
        y = rect.y() + margins.top()
        line_height = 0
        for item in self._items:
            hint = item.sizeHint()
            if x > left and x + hint.width() > right:
                x = left
                y += line_height + self._gap
                line_height = 0
            if not dry_run:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + self._gap
            line_height = max(line_height, hint.height())
        return y + line_height + margins.bottom() - rect.y()
