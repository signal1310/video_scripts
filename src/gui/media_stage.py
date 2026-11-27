"""미리 보기와 화질 비교 창이 함께 쓰는 영상 뒤 무대 색과 바깥 경계 점선의 공용 기준

- 배경 색 키(검정, 회색, 흰색)를 팔레트 토큰으로 옮기고 다음 색을 정하는 규칙을 한 곳에 둠
- 두 화면이 같은 점선 기하를 쓰도록 바깥 경계 사각형 계산을 공유
- 미리 보기 영역(`preview`)과 대화상자 영역(`dialogs`)이 함께 쓰므로 `gui` 바로 아래에 둠
- 색은 팔레트 토큰만 씀
"""

from __future__ import annotations

from PyQt6.QtCore import QRectF, Qt
from PyQt6.QtGui import QColor, QPen

from src.core.settings.ui_state import (
    DEFAULT_PREVIEW_BACKGROUND,
    PREVIEW_BACKGROUND_BLACK,
    PREVIEW_BACKGROUND_GRAY,
    PREVIEW_BACKGROUND_WHITE,
    VALID_PREVIEW_BACKGROUNDS,
)
from src.gui.styles.palettes import ThemePalette

# - 영상 바깥에 그리는 점선 굵기(논리 픽셀, 화면 배율과 무관한 1 장치 픽셀)
OUTLINE_WIDTH_PX: float = 1.0
_DASH_PATTERN: tuple[float, ...] = (4.0, 3.0)

STAGE_BACKGROUND_LABELS: dict[str, str] = {
    PREVIEW_BACKGROUND_BLACK: "검정",
    PREVIEW_BACKGROUND_GRAY: "회색",
    PREVIEW_BACKGROUND_WHITE: "흰색",
}
# - 사용자 승인 문구(TERMINOLOGY.md 4.5절)는 이 한 곳에서만 관리
STAGE_BACKGROUND_TIP: str = "영상 뒤 배경 색을 바꿉니다. 밝은 배경에서는 검은 여백이 잘 보입니다."
STAGE_BACKGROUND_SHORTCUT_DESCRIPTION: str = "영상 뒤 배경 색을 검정, 회색, 흰색 순서로 바꿉니다."
STAGE_FRAME_OUTLINE_TIP: str = "영상 바깥 경계에 점선을 그립니다. 검은 배경에서도 검은 여백이 어디까지인지 보입니다."


def stage_color(palette: ThemePalette, background: str) -> str:
    """배경 색 키를 팔레트 토큰으로 옮김, 알 수 없는 값은 기본 검정"""
    if background == PREVIEW_BACKGROUND_GRAY:
        return palette.media_stage_gray
    if background == PREVIEW_BACKGROUND_WHITE:
        return palette.media_stage_white
    return palette.media_bg


def next_stage_background(current: str) -> str:
    """검정, 회색, 흰색 순서로 다음 배경 색, 알 수 없는 값은 첫 색부터"""
    backgrounds = VALID_PREVIEW_BACKGROUNDS
    if current not in backgrounds:
        return backgrounds[0]
    return backgrounds[(backgrounds.index(current) + 1) % len(backgrounds)]


def stage_background_notice(background: str) -> str:
    """바뀐 배경 색을 알리는 짧은 문구"""
    return f"배경 색: {STAGE_BACKGROUND_LABELS.get(background, STAGE_BACKGROUND_LABELS[DEFAULT_PREVIEW_BACKGROUND])}"


def stage_outline_rect(frame: QRectF, line_width: float = OUTLINE_WIDTH_PX) -> QRectF:
    """영상 사각형 바깥으로 점선 굵이만큼 키운 사각형, 영상 화소를 덮지 않음"""
    return frame.adjusted(-line_width, -line_width, line_width, line_width)


def stage_frame_rect(
    image_size: tuple[int, int],
    scale: float,
    offset_x: float,
    offset_y: float,
    origin_x: float = 0.0,
) -> QRectF:
    """배율과 오프셋으로 화면에 그려지는 영상 사각형, 좌우 비교 오른쪽 화면은 origin_x만큼 옮김"""
    width, height = image_size
    return QRectF(
        origin_x + offset_x,
        offset_y,
        float(width) * scale,
        float(height) * scale,
    )


def make_outline_pen(color: str) -> QPen:
    """화면 배율과 무관하게 1 장치 픽셀로 그리는 점선 펜"""
    pen = QPen(QColor(color))
    pen.setWidthF(0.0)
    pen.setCosmetic(True)
    pen.setStyle(Qt.PenStyle.CustomDashLine)
    pen.setDashPattern(list(_DASH_PATTERN))
    return pen
