"""헤더 인코딩 버튼: 상태 배색, 진행 채움, 대상 수 배지를 직접 그리는 버튼 모듈"""

from PyQt6.QtCore import QEvent, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QEnterEvent, QFontMetrics, QPainter, QPaintEvent, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSizePolicy, QWidget

from src.core.encode.encode_batch_book import (
    CHIP_STATE_IDLE,
    CHIP_STATE_PAUSED,
    CHIP_STATE_RUNNING,
    EncodeChipStatus,
)
from src.gui.constants import TOOLBAR_ICON_BTN_SIZE
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.gui.widgets.eliding_label import ElidingLabel

IDLE_LABEL: str = "인코딩"
RUNNING_LABEL_TEMPLATE: str = "인코딩 {percent}%"
PAUSED_LABEL_TEMPLATE: str = "일시중지 {percent}%"
HEADER_ENCODE_ACCESSIBLE_NAME: str = "인코딩 창 열기"
ENCODE_ICON_NAME: str = "encode-clapper"
ARROW_ICON_NAME: str = "chevron-right"
ARROW_SIZE_PX: int = 20
ARROW_OBJECT_NAME: str = "HeaderEncodeArrow"
_ICON_SIZE_PX: int = 14
_PADDING_X_PX: int = 12
_PADDING_Y_PX: int = 6
_BORDER_RADIUS_PX: float = 7.0
_PERCENT_MAX: int = 100
_BADGE_GAP_PX: int = 6
_BADGE_PADDING_X_PX: int = 6
_BADGE_HEIGHT_PX: int = 16
_BADGE_RADIUS_PX: float = 8.0


class HeaderEncodeButton(QPushButton, ThemeAwareMixin):
    """인코딩 상태를 아이콘, 문구, 진행 채움, 대상 수 배지로 보이는 헤더 버튼

    - 상태 배경과 1px 테두리, 진행 채움을 paintEvent에서 팔레트 토큰으로 직접 그림
    - 아이콘과 글자는 자식 라벨로 두어 문구 폭에 맞춰 버튼 크기가 정해지게 함
    - 진행 중에는 진행률만큼 왼쪽부터 채우고, 일시중지에는 같은 채움을 경고 배색으로 바꿈
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HeaderEncodeButton")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName(HEADER_ENCODE_ACCESSIBLE_NAME)
        # - 문구는 폭이 모자라면 말줄임으로 줄고, 남는 가로 공간은 쓰지 않음
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._palette: ThemePalette = get_active_palette()
        self._status: EncodeChipStatus = EncodeChipStatus(
            state=CHIP_STATE_IDLE, done=0, total=0, percent=0, tooltip=""
        )
        self._target_count: int = 0
        self._hovered: bool = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(_PADDING_X_PX, _PADDING_Y_PX, _PADDING_X_PX, _PADDING_Y_PX)
        layout.setSpacing(_BADGE_GAP_PX)
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(_ICON_SIZE_PX, _ICON_SIZE_PX)
        self.text_label = ElidingLabel(self)
        self.text_label.set_full_text(IDLE_LABEL)
        # - 대상 수 배지가 숨을 때 글자 줄 높이가 낮아져 버튼 높이가 흔들리지 않도록 같은 높이를 둠
        self.text_label.setMinimumHeight(_BADGE_HEIGHT_PX)
        self.badge = EncodeTargetCountBadge(self)
        for child in (self.icon_label, self.text_label, self.badge):
            # - 자식이 마우스를 받지 않아야 버튼 호버와 클릭이 그대로 동작
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(self.icon_label)
        layout.addWidget(self.text_label)
        layout.addWidget(self.badge)
        self._apply_appearance()
        self.init_theme_listener()

    @property
    def state(self) -> str:
        """지금 반영된 인코딩 배치 상태 값"""
        return self._status.state

    @property
    def label_text(self) -> str:
        """상태에 따라 지금 보이는 버튼 문구"""
        if self._status.state == CHIP_STATE_RUNNING:
            return RUNNING_LABEL_TEMPLATE.format(percent=self._status.percent)
        if self._status.state == CHIP_STATE_PAUSED:
            return PAUSED_LABEL_TEMPLATE.format(percent=self._status.percent)
        return IDLE_LABEL

    def fill_width(self) -> int:
        """진행률에 해당하는 채움 폭. 진행과 일시중지 상태에서만 값을 가짐"""
        if not self.isEnabled() or self._status.state not in (
            CHIP_STATE_RUNNING,
            CHIP_STATE_PAUSED,
        ):
            return 0
        percent = min(max(self._status.percent, 0), _PERCENT_MAX)
        return round(self.width() * percent / _PERCENT_MAX)

    def set_status(self, status: EncodeChipStatus) -> None:
        """배치 상태와 진행률을 반영해 문구, 배지, 배색, 채움 폭을 갱신"""
        self._status = status
        self._apply_appearance()

    def set_target_count(self, count: int) -> None:
        """대상 수 배지에 보일 수를 반영. 0 이하이면 배지를 숨김"""
        self._target_count = max(0, count)
        self._apply_appearance()

    def sizeHint(self) -> QSize:
        """자식 라벨을 담은 레이아웃 크기를 버튼 크기 힌트로 씀. 높이는 헤더 아이콘 버튼과 맞춤"""
        layout = self.layout()
        base = layout.sizeHint() if layout is not None else super().sizeHint()
        return QSize(base.width(), max(base.height(), TOOLBAR_ICON_BTN_SIZE))

    def minimumSizeHint(self) -> QSize:
        """좁은 창에서도 줄어들 수 있도록 아이콘과 여백만큼의 최소 크기만 요구

        - 문구는 말줄임으로 줄어들고 진행 채움은 실제 폭 기준으로 다시 계산됨
        """
        return QSize(
            _ICON_SIZE_PX + _PADDING_X_PX * 2,
            max(_BADGE_HEIGHT_PX + _PADDING_Y_PX * 2, TOOLBAR_ICON_BTN_SIZE),
        )

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트를 반영해 배색과 아이콘을 다시 그림"""
        self._palette = palette
        self._apply_appearance()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        """상태 배경, 테두리와 진행률 채움을 팔레트 토큰으로 그림"""
        del a0
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        _background, _foreground, border = self._colors()
        painter.setPen(QPen(QColor(border), 1.0))
        painter.setBrush(QColor(self._base_background()))
        painter.drawRoundedRect(rect, _BORDER_RADIUS_PX, _BORDER_RADIUS_PX)
        fill = self.fill_width()
        if fill > 0:
            # - 채움은 왼쪽부터 그리되 둥근 모서리를 유지하려고 버튼 모양으로 자름
            painter.save()
            painter.setClipRect(QRectF(rect.left(), rect.top(), fill, rect.height()))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(self._fill_color()))
            painter.drawRoundedRect(rect, _BORDER_RADIUS_PX, _BORDER_RADIUS_PX)
            painter.restore()
        painter.end()

    def enterEvent(self, a0: QEnterEvent | None) -> None:
        """호버 상태를 켜고 호버 배색으로 다시 그림"""
        self._hovered = True
        super().enterEvent(a0)
        self.update()

    def leaveEvent(self, a0: QEvent | None) -> None:
        """호버 상태를 끄고 기본 배색으로 다시 그림"""
        self._hovered = False
        super().leaveEvent(a0)
        self.update()

    def _is_ready(self) -> bool:
        """인코딩 창을 열 대상이 있으면서 바로 실행할 수 있는 유휴 상태인지

        - 준비 완료만 강조 배색을 쓰고 나머지 상태는 기존 상태 토큰을 유지
        """
        return (
            self.isEnabled()
            and self._status.state == CHIP_STATE_IDLE
            and self._target_count > 0
        )

    def _base_background(self) -> str:
        """상태 배경 토큰. 호버 중이며 누를 수 있으면 공용 호버 배경을 씀

        - 준비 완료 강조는 상태를 알리는 색이므로 호버에도 중립 배경으로 바꾸지 않음
        """
        background = self._colors()[0]
        if self._hovered and self.isEnabled() and not self._is_ready():
            return self._palette.btn_hover
        return background

    def _colors(self) -> tuple[str, str, str]:
        """상태별 (바탕, 글자, 테두리) 토큰. 비활성은 흐린 공용 배색

        - 진행 중과 일시중지의 바탕은 중립 트랙이고, 채워진 부분만 진행 배색으로 칠해 채움 폭이 보이게 함
        - 대상이 있는 유휴 상태는 카드 강조 토큰으로 실행 가능함을 드러냄
        """
        palette = self._palette
        if not self.isEnabled():
            return palette.bg_elevated, palette.text_muted, palette.border_subtle
        if self._status.state == CHIP_STATE_RUNNING:
            _bg, foreground, border = palette.badge_encode_running
            return palette.bg_elevated, foreground, border
        if self._status.state == CHIP_STATE_PAUSED:
            return palette.bg_elevated, palette.row_paused_accent, palette.row_paused_accent
        if self._is_ready():
            return (
                palette.card_highlight_bg,
                palette.card_highlight_val,
                palette.card_highlight_border,
            )
        return palette.btn_bg, palette.text_primary, palette.btn_border

    def _badge_colors(self) -> tuple[str, str]:
        """대상 수 배지의 (바탕, 글자) 토큰. 준비 완료에서는 강조색 바탕에 고대비 글자

        - 강조색은 라이트에서 어두운 초록, 다크에서 밝은 초록이므로 글자도 테마별 극값을 씀
        """
        palette = self._palette
        if self._is_ready():
            foreground = palette.bg_base if palette.is_dark else palette.text_on_accent
            return palette.card_highlight_val, foreground
        return palette.bg_elevated, palette.text_muted

    def _fill_color(self) -> str:
        """진행 채움 색. 일시중지는 경고 배경, 그 외 진행은 진행 배지 배경 토큰"""
        if self._status.state == CHIP_STATE_PAUSED:
            return self._palette.row_paused_bg
        return self._palette.badge_encode_running[0]

    def _apply_appearance(self) -> None:
        """문구, 글자색, 아이콘, 대상 수 배지를 지금 상태에 맞춤"""
        _background, foreground, _border = self._colors()
        self.text_label.set_full_text(self.label_text)
        self.text_label.set_color(foreground)
        self.icon_label.setPixmap(
            get_icon(ENCODE_ICON_NAME, color=foreground, size=_ICON_SIZE_PX, pad_right=0).pixmap(
                _ICON_SIZE_PX, _ICON_SIZE_PX
            )
        )
        show_badge = self._status.state == CHIP_STATE_IDLE and self._target_count > 0
        self.badge.setEnabled(self.isEnabled())
        self.badge.set_count(self._target_count)
        background, badge_foreground = self._badge_colors()
        self.badge.set_colors(background, badge_foreground)
        self.badge.setVisible(show_badge)
        self.updateGeometry()
        self.update()


class EncodeTargetCountBadge(QWidget):
    """인코딩 대상 수를 알약으로 그리는 배지"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("HeaderEncodeCountBadge")
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._count: int = 0
        self._background: str = ""
        self._foreground: str = ""
        self.setFixedHeight(_BADGE_HEIGHT_PX)
        self.setVisible(False)

    @property
    def count(self) -> int:
        """배지에 그리는 수"""
        return self._count

    def set_count(self, count: int) -> None:
        """배지에 그릴 수를 바꾸고 폭을 다시 계산"""
        self._count = count
        self.setFixedWidth(self._text_width())
        self.update()

    def set_colors(self, background: str, foreground: str) -> None:
        """배지 배경과 글자색 토큰을 받아 다시 그림"""
        self._background = background
        self._foreground = foreground
        self.update()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        """알약 배경과 수를 그림"""
        del a0
        if not self._background or not self._foreground:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(self._background))
        painter.drawRoundedRect(QRectF(self.rect()), _BADGE_RADIUS_PX, _BADGE_RADIUS_PX)
        painter.setPen(QColor(self._foreground))
        painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, str(self._count))
        painter.end()

    def _text_width(self) -> int:
        """수 문구와 좌우 여백을 더한 폭. 알약 높이보다 좁아지지 않게 함"""
        metrics = QFontMetrics(self.font())
        text_width = metrics.horizontalAdvance(str(self._count)) + _BADGE_PADDING_X_PX * 2
        return max(text_width, _BADGE_HEIGHT_PX)


def create_encode_arrow_label(
    parent: QWidget, palette: ThemePalette | None = None
) -> QLabel:
    """분석 버튼과 인코딩 버튼 사이에 두는 장식용 화살표 라벨

    - 누를 수 없는 표시 전용이며 마우스 이벤트를 통과시켜 옆 컨트롤 조작을 막지 않음
    """
    label = QLabel(parent)
    label.setObjectName(ARROW_OBJECT_NAME)
    label.setFixedSize(ARROW_SIZE_PX, ARROW_SIZE_PX)
    label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
    set_encode_arrow_color(label, palette or get_active_palette())
    return label


def set_encode_arrow_color(label: QLabel, palette: ThemePalette) -> None:
    """장식 화살표 아이콘 색을 흐린 글자 토큰으로 맞춤"""
    label.setPixmap(
        get_icon(
            ARROW_ICON_NAME, color=palette.text_muted, size=ARROW_SIZE_PX, pad_right=0
        ).pixmap(ARROW_SIZE_PX, ARROW_SIZE_PX)
    )
