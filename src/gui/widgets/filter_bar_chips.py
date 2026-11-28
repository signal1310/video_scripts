"""검색창 오른쪽 필터 칩(여백 확인 필요, 구간별 여백 차이)의 문구, 명세와 개수 배지 위젯 모듈

- 칩별 이름, 툴팁, 아이콘, 접힌 배지 색을 한 표에 모아 필터 바가 같은 규칙으로 두 칩을 만듦
- 배지는 접힌 칩의 아이콘 오른쪽 위에 개수를 겹쳐 보여 주는 표시 전용 위젯
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFontMetrics
from PyQt6.QtWidgets import QLabel, QWidget

from src.gui.styles.palettes import ThemePalette

# - 칩 문구: 이름은 TERMINOLOGY 4.5절 표준어, 개수는 결과 상태 필터와 같은 `이름 N` 형식
REVIEW_CHIP_LABEL: str = "여백 확인 필요"
REVIEW_CHIP_TOOLTIP: str = "자동 잘라내기를 보류한 영상만 봅니다."
VARY_CHIP_LABEL: str = "구간별 여백 차이"
VARY_CHIP_TOOLTIP: str = "구간마다 화면 여백이 달라진 영상만 봅니다."
CHIP_REVIEW: str = "review"
CHIP_VARY: str = "vary"
# - 칩별 (키, 오브젝트 이름, 접근성 이름/전체 문구, 툴팁, 아이콘, 접힌 배지 색)
CHIP_SPECS: tuple[tuple[str, str, str, str, str, str], ...] = (
    (
        CHIP_REVIEW,
        "ReviewFilterChip",
        REVIEW_CHIP_LABEL,
        REVIEW_CHIP_TOOLTIP,
        "alert-triangle",
        CHIP_REVIEW,
    ),
    (
        CHIP_VARY,
        "VaryFilterChip",
        VARY_CHIP_LABEL,
        VARY_CHIP_TOOLTIP,
        "margins-vary",
        CHIP_VARY,
    ),
)
COUNT_BADGE_OFFSET_PX: int = 2
COUNT_BADGE_HEIGHT_PX: int = 14
COUNT_BADGE_PADDING_X_PX: int = 2
BADGE_RADIUS_PX: int = 7


def count_badge_style(palette: ThemePalette, tone: str) -> str:
    """접힌 칩 배지의 배경, 글자색. 여백 확인 칩은 경고색, 구간별 여백 차이 칩은 갈색, 열 필터 칩은 액센트색"""
    if tone == CHIP_REVIEW:
        background = palette.badge_pending[2]
    elif tone == CHIP_VARY:
        background = palette.badge_vary[2]
    else:
        background = palette.accent_blue
    foreground = palette.text_on_accent
    return (
        f"QLabel#FilterBarCountBadge {{ background-color: {background};"
        f" color: {foreground}; border-radius: {BADGE_RADIUS_PX}px;"
        f" padding: 0px {COUNT_BADGE_PADDING_X_PX}px; font-size: 8pt; font-weight: 700; }}"
    )


def muted_count_badge_style(palette: ThemePalette) -> str:
    """값을 세는 muted 알약 배지의 배경, 글자색. 버튼과 메뉴 행 배경 위에서 개수만 조용히 드러냄"""
    return (
        f"QLabel#FilterBarCountBadge {{ background-color: {palette.bg_elevated};"
        f" color: {palette.text_secondary}; border-radius: {BADGE_RADIUS_PX}px;"
        f" padding: 0px {COUNT_BADGE_PADDING_X_PX}px; font-size: 8pt; font-weight: 700; }}"
    )


class FilterBarCountBadge(QLabel):
    """접힌 칩 버튼 오른쪽 위에 개수를 겹쳐 보여 주는 작은 배지"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("FilterBarCountBadge")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # - 배지는 표시 전용이므로 클릭은 부모 버튼이 그대로 받음
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(COUNT_BADGE_HEIGHT_PX)
        self._count: int = 0
        self.setVisible(False)

    @property
    def count(self) -> int:
        """배지에 그려진 개수"""
        return self._count

    def set_count(self, count: int) -> None:
        """배지에 그릴 개수를 바꾸고 자릿수에 맞춰 폭을 다시 계산"""
        self._count = max(0, count)
        text = str(self._count)
        self.setText(text)
        metrics = QFontMetrics(self.font())
        width = metrics.horizontalAdvance(text) + COUNT_BADGE_PADDING_X_PX * 2
        self.setFixedWidth(max(width, COUNT_BADGE_HEIGHT_PX))


__all__ = [
    "BADGE_RADIUS_PX",
    "CHIP_SPECS",
    "COUNT_BADGE_HEIGHT_PX",
    "COUNT_BADGE_OFFSET_PX",
    "REVIEW_CHIP_LABEL",
    "REVIEW_CHIP_TOOLTIP",
    "VARY_CHIP_LABEL",
    "VARY_CHIP_TOOLTIP",
    "FilterBarCountBadge",
    "count_badge_style",
    "muted_count_badge_style",
]
