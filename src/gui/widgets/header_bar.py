"""상단 작업 경로 선택, 분석 모드 분할 버튼 및 테마, 설정 툴바 모듈"""

import os
from dataclasses import dataclass

from PyQt6.QtCore import QPoint, QPointF, QRect, Qt, pyqtSignal
from PyQt6.QtGui import (
    QColor,
    QFocusEvent,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QPainter,
    QPaintEvent,
    QPen,
    QPolygonF,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QStackedWidget,
    QWidget,
)

from src.core.ui_state import DEFAULT_ANALYZE_MODE
from src.gui.constants import ON_ACCENT_COLOR, TOOLBAR_ICON_BTN_SIZE, make_path_font
from src.gui.styles.icons import (
    create_icon_button,
    get_active_palette,
    get_icon,
    set_button_icon,
)
from src.gui.styles.palettes import ThemePalette
from src.gui.theme import ThemeAwareMixin
from src.gui.widgets.analyze_mode_popup import AnalyzeModePopup

_MODE_POPUP_GAP_Y: int = 3
_FOLDER_ICON_SIZE_PX: int = 16
_PATH_TEXT_PAD_X_PX: int = 8
_PATH_PLACEHOLDER: str = "폴더를 선택해 주세요"
_PATH_ELLIPSIS: str = "…"
_SEP_WIDTH_PX: int = 18
_CHEVRON_HALF_WIDTH_PX: float = 2.2
_CHEVRON_HALF_HEIGHT_PX: float = 3.4
_CHEVRON_STROKE_PX: float = 1.9
_CHEVRON_OFFSET_Y_PX: float = 1.5
# 한국어 키보드의 원화 기호 입력을 경로 구분자로 되돌리기 위한 문자
_WON_SIGN: str = "₩"


@dataclass(frozen=True)
class _PathItem:
    """경로 버튼에 그릴 한 조각. 구분자는 텍스트 없이 is_separator로 표시"""

    text: str
    bold: bool = False
    is_separator: bool = False


_SEPARATOR_ITEM = _PathItem("", is_separator=True)


def split_path_parts(path: str) -> list[str]:
    """정규화한 경로를 구분자 기준 구성 요소로 나눔. 빈 요소는 제외해 드라이브 이름만 첫 요소로 남김"""
    return [part for part in os.path.normpath(path).split(os.sep) if part]


def fit_path_items(
    parts: list[str], width: int, normal: QFontMetrics, bold: QFontMetrics
) -> list[_PathItem]:
    """폭에 맞춰 뒤쪽 구성 요소부터 채우고, 생략된 앞쪽이 있으면 말줄임 표시를 앞에 둠

    폴더 이름은 항상 남기며, 앞쪽 생략 여부는 말줄임 자리를 미리 남겨 두고 판정
    """
    leaf = bold.elidedText(parts[-1], Qt.TextElideMode.ElideRight, width)
    used = bold.horizontalAdvance(leaf)
    ellipsis_cost = _SEP_WIDTH_PX + normal.horizontalAdvance(_PATH_ELLIPSIS)
    shown: list[_PathItem] = [_PathItem(leaf, bold=True)]
    hidden_count = len(parts) - 1
    for index in range(len(parts) - 2, -1, -1):
        reserve = ellipsis_cost if index > 0 else 0
        cost = _SEP_WIDTH_PX + normal.horizontalAdvance(parts[index])
        if used + cost + reserve > width:
            break
        used += cost
        shown.insert(0, _PathItem(parts[index]))
        hidden_count -= 1

    items: list[_PathItem] = []
    if hidden_count > 0:
        items.extend([_PathItem(_PATH_ELLIPSIS), _SEPARATOR_ITEM])
    for position, item in enumerate(shown):
        if position > 0:
            items.append(_SEPARATOR_ITEM)
        items.append(item)
    return items


class FolderPathButton(QPushButton):
    """작업 폴더 경로를 그리는 버튼. 상위 경로는 흐리게, 폴더 이름은 굵게 그리고 폭이 모자라면 앞쪽을 생략

    텍스트를 직접 그리므로 QSS의 글자 속성 대신 팔레트 토큰을 사용
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("FolderNameButton")
        self._path: str = ""
        self._palette: ThemePalette | None = None

    @property
    def path(self) -> str:
        """표시 중인 작업 폴더 경로 반환"""
        return self._path

    def set_path(self, path: str) -> None:
        """표시할 경로를 지정하고 다시 그리기"""
        self._path = path
        self.update()

    def set_palette(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트를 지정하고 다시 그리기"""
        self._palette = palette
        self.update()

    def paintEvent(self, a0: QPaintEvent | None) -> None:
        """배경과 상태 표시는 기본 그리기에 맡기고, 경로 구성 요소와 구분자만 직접 그림"""
        super().paintEvent(a0)
        palette = self._palette or get_active_palette()
        text_rect = self.rect().adjusted(_PATH_TEXT_PAD_X_PX, 0, -_PATH_TEXT_PAD_X_PX, 0)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if not self._path:
            self._draw_text(
                painter, text_rect, _PATH_PLACEHOLDER, self._font(bold=False), palette.text_muted
            )
        else:
            self._draw_path(painter, text_rect, palette)
        painter.end()

    def _font(self, *, bold: bool) -> QFont:
        """버튼 점 크기를 따르는 경로 글꼴의 일반 또는 굵은 글꼴"""
        return make_path_font(self.font().pointSize(), bold=bold)

    def _draw_path(self, painter: QPainter, text_rect: QRect, palette: ThemePalette) -> None:
        parts = split_path_parts(self._path)
        normal_font = self._font(bold=False)
        bold_font = self._font(bold=True)
        if not parts:
            self._draw_text(
                painter, text_rect, os.path.normpath(self._path), normal_font, palette.text_muted
            )
            return

        if self.isEnabled():
            ancestor_color, leaf_color, sep_color = (
                palette.text_secondary,
                palette.text_primary,
                palette.text_muted,
            )
        else:
            ancestor_color = leaf_color = sep_color = palette.text_muted

        items = fit_path_items(
            parts, text_rect.width(), QFontMetrics(normal_font), QFontMetrics(bold_font)
        )
        center_y = text_rect.center().y() + _CHEVRON_OFFSET_Y_PX
        x = text_rect.left()
        for item in items:
            if item.is_separator:
                self._draw_chevron(painter, x + _SEP_WIDTH_PX / 2, center_y, sep_color)
                x += _SEP_WIDTH_PX
                continue
            font = bold_font if item.bold else normal_font
            width = QFontMetrics(font).horizontalAdvance(item.text)
            color = leaf_color if item.bold else ancestor_color
            self._draw_text(painter, QRect(x, text_rect.top(), width, text_rect.height()), item.text, font, color)
            x += width

    @staticmethod
    def _draw_text(
        painter: QPainter, rect: QRect, text: str, font: QFont, color: str
    ) -> None:
        painter.setFont(font)
        painter.setPen(QColor(color))
        painter.drawText(
            rect, int(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter), text
        )

    @staticmethod
    def _draw_chevron(painter: QPainter, center_x: float, center_y: float, color: str) -> None:
        """탐색기 주소 표시줄처럼 구성 요소 사이에 얇은 꺾쇠 기호를 그림"""
        pen = QPen(QColor(color))
        pen.setWidthF(_CHEVRON_STROKE_PX)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.drawPolyline(
            QPolygonF(
                [
                    QPointF(center_x - _CHEVRON_HALF_WIDTH_PX, center_y - _CHEVRON_HALF_HEIGHT_PX),
                    QPointF(center_x + _CHEVRON_HALF_WIDTH_PX, center_y),
                    QPointF(center_x - _CHEVRON_HALF_WIDTH_PX, center_y + _CHEVRON_HALF_HEIGHT_PX),
                ]
            )
        )


class SplitAnalyzeButton(QFrame, ThemeAwareMixin):
    """빠른 분석과 정밀 분석을 선택 실행하는 분할(Split) 버튼 위젯

    본체 클릭 시 현재 선택된 분석 모드 즉시 실행, 드롭다운 화살표로 모드 전환
    """

    MODE_FAST = DEFAULT_ANALYZE_MODE
    MODE_FULL = "full"

    analyze_fast_requested = pyqtSignal()
    analyze_full_requested = pyqtSignal()
    mode_changed = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("SplitAnalyzeFrame")
        self._mode = self.MODE_FAST

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self.btn_main = QPushButton("빠른 분석", self)
        self.btn_main.setObjectName("SplitMainButton")
        self.btn_main.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_main.clicked.connect(self._on_main_clicked)

        self.btn_arrow = QPushButton(self)
        self.btn_arrow.setObjectName("SplitArrowButton")
        self.btn_arrow.setFixedWidth(26)
        self.btn_arrow.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_arrow.setToolTip("분석 모드를 선택합니다: 빠른 분석 / 정밀 분석")
        self.btn_arrow.setAccessibleName("분석 모드 선택")
        self.btn_arrow.clicked.connect(self._on_arrow_clicked)

        layout.addWidget(self.btn_main)
        layout.addWidget(self.btn_arrow)

        self._mode_popup = AnalyzeModePopup(self)
        self._mode_popup.fast_selected.connect(self._select_fast_mode)
        self._mode_popup.full_selected.connect(self._select_full_mode)

        self._palette: ThemePalette | None = None
        self.set_mode(self.MODE_FAST)
        self.init_theme_listener()

    @property
    def current_mode(self) -> str:
        """현재 선택된 스캔 모드 반환"""
        return self._mode

    def set_mode(self, mode: str, *, emit_signal: bool = False) -> None:
        """스캔 버튼 모드 전환 및 아이콘, 툴팁 갱신"""
        target_mode = self.MODE_FULL if mode == self.MODE_FULL else self.MODE_FAST
        changed = self._mode != target_mode
        # 비활성 시 QSS가 텍스트 색을 text_muted로 바꾸므로 아이콘도 같은 토큰으로 맞춤
        palette = self._palette or get_active_palette()
        if target_mode == self.MODE_FULL:
            self._mode = self.MODE_FULL
            self.btn_main.setText("정밀 분석")
            set_button_icon(
                self.btn_main,
                "analyze",
                color=palette.text_on_accent,
                size=14,
                pad_right=7,
                disabled_color=palette.text_muted,
            )
            self.btn_main.setToolTip(
                "모든 영상의 키프레임 간격을 실측하여 권장 처리를 판정합니다."
            )
        else:
            self._mode = self.MODE_FAST
            self.btn_main.setText("빠른 분석")
            set_button_icon(
                self.btn_main,
                "zap",
                color=palette.text_on_accent,
                size=14,
                pad_right=7,
                disabled_color=palette.text_muted,
            )
            self.btn_main.setToolTip(
                "키프레임 측정을 생략하고 해상도와 비트레이트를 빠르게 분석합니다."
            )
        set_button_icon(
            self.btn_arrow,
            "chevron-down",
            color=palette.text_on_accent,
            size=12,
            pad_right=0,
            disabled_color=palette.text_muted,
        )
        if emit_signal and changed:
            self.mode_changed.emit(self._mode)

    def setEnabled(self, enabled: bool) -> None:
        """분할 버튼 전체 및 내부 메인, 화살표 버튼 활성화 상태 동기화"""
        super().setEnabled(enabled)
        self.btn_main.setEnabled(enabled)
        self.btn_arrow.setEnabled(enabled)

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트 기반 버튼 아이콘 색상 갱신"""
        self._palette = palette
        self.set_mode(self._mode)

    def _on_main_clicked(self) -> None:
        if self._mode == self.MODE_FULL:
            self.analyze_full_requested.emit()
        else:
            self.analyze_fast_requested.emit()

    def _on_arrow_clicked(self) -> None:
        # 마우스 클릭은 팝업 기반의 토글 영역이 먼저 처리하므로, 여기서는 키보드로 연 경우의 닫기만 담당
        if self._mode_popup.isVisible():
            self._mode_popup.close()
            return
        from src.gui.theme import ThemeManager

        popup = self._mode_popup
        popup.refresh_icons(self._palette or ThemeManager().current_palette)
        popup.setMinimumWidth(self.width())
        popup.set_toggle_rect(
            QRect(self.btn_arrow.mapToGlobal(QPoint(0, 0)), self.btn_arrow.size())
        )
        # 팝업이 분할 버튼보다 넓으면 오른쪽 끝을 맞춰 펼침
        pos_x = min(0, self.width() - popup.sizeHint().width())
        popup.show_clamped_at(self.mapToGlobal(QPoint(pos_x, self.height() + _MODE_POPUP_GAP_Y)))

    def _select_fast_mode(self) -> None:
        self.set_mode(self.MODE_FAST, emit_signal=True)

    def _select_full_mode(self) -> None:
        self.set_mode(self.MODE_FULL, emit_signal=True)


class PathLineEdit(QLineEdit):
    """작업 폴더 경로 입력창. Esc 입력과 포커스 이탈을 편집 취소 신호로 알림"""

    edit_cancelled = pyqtSignal()

    def keyPressEvent(self, a0: QKeyEvent | None) -> None:
        """Esc 입력 시 편집을 취소하고 그 외 키는 기본 처리"""
        if a0 is not None and a0.key() == Qt.Key.Key_Escape:
            self.edit_cancelled.emit()
            return
        super().keyPressEvent(a0)

    def focusOutEvent(self, a0: QFocusEvent | None) -> None:
        """포커스 이탈 시 확정하지 않은 입력을 취소 신호로 알림"""
        super().focusOutEvent(a0)
        self.edit_cancelled.emit()


class HeaderBar(QFrame, ThemeAwareMixin):
    """작업 경로 선택, 스캔 실행 및 환경설정 진입을 제공하는 헤더 바 위젯"""

    browse_clicked = pyqtSignal()
    open_explorer_clicked = pyqtSignal()
    analyze_fast_clicked = pyqtSignal()
    analyze_full_clicked = pyqtSignal()
    analyze_mode_changed = pyqtSignal(str)
    cancel_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()
    path_submitted = pyqtSignal(str)
    path_rejected = pyqtSignal(str)
    committed_path_changed = pyqtSignal(str)

    def __init__(
        self, initial_dir: str = "", parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setObjectName("HeaderBar")
        self._committed_path: str = initial_dir

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(10)

        # 폴더 아이콘은 경로 입력 중에도 남도록 스택 바깥에 둠
        self.folder_icon = QLabel(self)
        self.folder_icon.setFixedSize(_FOLDER_ICON_SIZE_PX, _FOLDER_ICON_SIZE_PX)

        # 평소에는 전체 경로를 보여 주고, 클릭하면 경로 입력창으로 전환
        self.folder_name_btn = FolderPathButton(self)
        self.folder_name_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.folder_name_btn.setAccessibleName("작업 폴더 경로 직접 입력")
        # 긴 폴더 이름이 헤더의 최소 폭을 밀어내지 않도록 가로 최소 크기를 텍스트에 맞추지 않음
        self.folder_name_btn.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
        )
        self.folder_name_btn.clicked.connect(self.begin_path_edit)

        self.path_edit = PathLineEdit(initial_dir)
        self.path_edit.setFont(make_path_font())
        self.path_edit.setPlaceholderText(
            "작업 폴더 경로를 선택하거나 입력해 주세요..."
        )
        self.path_edit.setAccessibleName("작업 폴더 경로")
        self.path_edit.returnPressed.connect(self._on_path_edit_finished)
        self.path_edit.edit_cancelled.connect(self.restore_committed_path)

        self.path_stack = QStackedWidget(self)
        self.path_stack.addWidget(self.folder_name_btn)
        self.path_stack.addWidget(self.path_edit)

        self.btn_browse = create_icon_button(
            self,
            "작업 폴더를 선택합니다.",
            self.browse_clicked.emit,
            accessible_name="작업 폴더 선택",
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
        )
        self.btn_open_explorer = create_icon_button(
            self,
            "탐색기에서 작업 폴더를 엽니다.",
            self.open_explorer_clicked.emit,
            accessible_name="탐색기에서 작업 폴더 열기",
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
        )

        layout.addWidget(self.folder_icon)
        layout.addWidget(self.path_stack, stretch=1)
        layout.addWidget(self.btn_browse)
        layout.addWidget(self.btn_open_explorer)
        layout.addSpacing(8)

        # 스캔 실행 스플릿 버튼 및 인플레이스 중단 버튼 (QStackedWidget)
        self.analyze_stack = QStackedWidget(self)
        self.analyze_stack.setObjectName("AnalyzeStack")

        self.split_analyze = SplitAnalyzeButton(self)
        self.split_analyze.analyze_fast_requested.connect(self.analyze_fast_clicked.emit)
        self.split_analyze.analyze_full_requested.connect(self.analyze_full_clicked.emit)
        self.split_analyze.mode_changed.connect(self.analyze_mode_changed.emit)
        self.analyze_stack.addWidget(self.split_analyze)

        self.btn_cancel = QPushButton("분석 중단", self)
        self.btn_cancel.setObjectName("DangerButton")
        self.btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cancel.setToolTip("현재 진행 중인 분석 작업을 즉시 중단합니다 [Esc]")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self.cancel_clicked.emit)
        self.analyze_stack.addWidget(self.btn_cancel)

        self.analyze_stack.setCurrentIndex(0)
        layout.addWidget(self.analyze_stack)
        layout.addSpacing(6)

        # 설정, 판별 기준 통합 버튼 (아이콘 전용 미니멀 버튼)
        self.btn_settings = create_icon_button(
            self,
            "설정 및 판별 기준 창을 엽니다. (Ctrl+, / 판별 기준: Ctrl+T)",
            self.settings_clicked.emit,
            accessible_name="설정",
            fixed_size=TOOLBAR_ICON_BTN_SIZE,
            object_name="SettingsButton",
        )
        layout.addWidget(self.btn_settings)

        self._refresh_folder_name(initial_dir)
        self.init_theme_listener()

    @property
    def committed_path(self) -> str:
        """적용이 확정된 작업 폴더 경로 반환"""
        return self._committed_path

    def set_path(self, path: str) -> None:
        """확정 작업 폴더 경로와 입력창 텍스트, 폴더 이름 표시를 함께 갱신하고 변경을 알림"""
        self._committed_path = path
        self.path_edit.setText(path)
        self._refresh_folder_name(path)
        self.committed_path_changed.emit(path)

    def begin_path_edit(self) -> None:
        """폴더 이름 대신 경로 입력창을 보이고 입력 포커스와 기존 경로 전체 선택"""
        self.path_stack.setCurrentWidget(self.path_edit)
        self.path_edit.setFocus()
        self.path_edit.selectAll()

    def restore_committed_path(self) -> None:
        """적용되지 않은 입력을 마지막 확정 경로로 되돌리고 폴더 이름 표시로 복귀"""
        self.path_edit.setText(self._committed_path)
        self._show_folder_name()

    def _show_folder_name(self) -> None:
        self.path_stack.setCurrentWidget(self.folder_name_btn)

    def _refresh_folder_name(self, path: str) -> None:
        """경로 버튼 표시와 툴팁을 확정 경로 기준으로 갱신"""
        self.folder_name_btn.set_path(path)
        hint = "경로 직접 입력: 클릭하면 작업 폴더 경로를 입력할 수 있습니다."
        if not path:
            self.folder_name_btn.setToolTip(hint)
            return
        self.folder_name_btn.setToolTip(f"{os.path.normpath(path)}\n{hint}")

    def _on_path_edit_finished(self) -> None:
        path = self.path_edit.text().strip().replace(_WON_SIGN, "\\")
        if not path or os.path.normpath(path) == os.path.normpath(self._committed_path):
            self.restore_committed_path()
        elif not os.path.isdir(path):
            self.restore_committed_path()
            self.path_rejected.emit(
                "입력한 경로를 폴더로 찾을 수 없습니다. 올바른 폴더 경로를 입력해 주세요."
            )
        else:
            self.path_submitted.emit(path)
        self._show_folder_name()

    def set_analyze_mode(self, mode: str) -> None:
        """헤더 분할 버튼의 스캔 모드 설정"""
        self.split_analyze.set_mode(mode)

    @property
    def current_analyze_mode(self) -> str:
        """헤더 분할 버튼의 현재 스캔 모드 반환"""
        return self.split_analyze.current_mode

    def set_busy(self, busy: bool) -> None:
        """작업 진행 여부에 따라 컨트롤 활성화 상태를 제어하고, 스캔 버튼과 중단 버튼을 인플레이스 전환"""
        self.path_edit.setEnabled(not busy)
        self.folder_name_btn.setEnabled(not busy)
        self.btn_browse.setEnabled(not busy)
        self.btn_open_explorer.setEnabled(not busy)
        self.split_analyze.setEnabled(not busy)
        self.btn_cancel.setEnabled(busy)
        self.analyze_stack.setCurrentIndex(1 if busy else 0)

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 버튼 아이콘 색상을 갱신"""
        self.folder_name_btn.set_palette(palette)
        self.folder_icon.setPixmap(
            get_icon("folder", palette=palette, size=_FOLDER_ICON_SIZE_PX).pixmap(
                _FOLDER_ICON_SIZE_PX, _FOLDER_ICON_SIZE_PX
            )
        )
        set_button_icon(self.btn_browse, "folder-open", palette=palette, size=16, pad_right=0)
        set_button_icon(
            self.btn_open_explorer, "external-link", palette=palette, size=16, pad_right=0
        )
        self.split_analyze.apply_theme(palette)
        set_button_icon(
            self.btn_cancel, "x-circle", color=ON_ACCENT_COLOR, size=14, pad_right=7
        )
        set_button_icon(self.btn_settings, "settings", palette=palette, size=16, pad_right=0)
