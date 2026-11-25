"""SVG 아이콘 템플릿, DPI 대응 QIcon 생성 및 QSS 아이콘 에셋 해석 모듈"""

import logging
import os
import tempfile
from collections.abc import Callable

from PyQt6.QtCore import QByteArray, QRectF, QSize, QStandardPaths, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QPushButton, QWidget

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE, ThemePalette

# 24x24 SVG 아이콘 패스 정의
SVG_TEMPLATES: dict[str, str] = {
    "chevron-up": '<path d="M18 15l-6-6-6 6"/>',
    "chevron-down": '<path d="M6 9l6 6 6-6"/>',
    "chevron-left": '<path d="M15 18l-6-6 6-6"/>',
    "chevron-right": '<path d="M9 18l6-6-6-6"/>',
    "plus": '<line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/>',
    "minus": '<line x1="5" y1="12" x2="19" y2="12"/>',
    "search": '<circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/>',
    "settings": '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/><circle cx="12" cy="12" r="3"/>',
    "folder": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/>',
    "folder-open": '<path d="M2 11h20M2 7a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V7z"/>',
    "folder-plus": '<path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z"/><line x1="12" y1="11" x2="12" y2="17"/><line x1="9" y1="14" x2="15" y2="14"/>',
    "folder-tree": '<path d="M20 10a1 1 0 0 0 1-1V6a1 1 0 0 0-1-1h-2.5a1 1 0 0 1-.8-.4l-.9-1.2A1 1 0 0 0 15 3h-2a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1Z"/><path d="M20 21a1 1 0 0 0 1-1v-3a1 1 0 0 0-1-1h-2.9a1 1 0 0 1-.88-.55l-.42-.85a1 1 0 0 0-.92-.6H13a1 1 0 0 0-1 1v5a1 1 0 0 0 1 1Z"/><path d="M3 5a2 2 0 0 0 2 2h3"/><path d="M3 3v13a2 2 0 0 0 2 2h3"/>',
    "external-link": '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/><polyline points="15 3 21 3 21 9"/><line x1="10" y1="14" x2="21" y2="3"/>',
    "rotate-cw": '<path d="M23 4v6h-6"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
    "rotate-ccw": '<path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
    "rotate-180": '<path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 16h5v5"/>',
    "play": '<polygon points="6 4 20 12 6 20 6 4" fill="currentColor" stroke="none"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none"/><rect x="14" y="4" width="4" height="16" rx="1" fill="currentColor" stroke="none"/>',
    "volume-2": '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><path d="M15.54 8.46a5 5 0 0 1 0 7.07"/><path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>',
    "volume-x": '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/><line x1="23" y1="9" x2="17" y2="15"/><line x1="17" y1="9" x2="23" y2="15"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "ban": '<circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>',
    "gauge": '<path d="m12 14 4-4"/><path d="M3.34 19a10 10 0 1 1 17.32 0"/>',
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "analyze": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/>',
    "x-circle": '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
    "eye": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    "check-circle": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
    "undo": '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
    "sun": '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    "monitor": '<rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
    "compare": '<rect x="2" y="5" width="9" height="14" rx="1.5"/><rect x="13" y="5" width="9" height="14" rx="1.5"/><line x1="12" y1="3" x2="12" y2="21"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "square": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "info": '<circle cx="12" cy="12" r="9"/><line x1="12" y1="11" x2="12" y2="16"/><line x1="12" y1="8" x2="12" y2="8.01"/>',
    "sliders": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
    "film": '<rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="17" x2="22" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/>',
    "crop": '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/>',
    "crop-off": '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/><path d="m2 2 20 20"/>',
    "crop-reset": '<path d="M6 2v14a2 2 0 0 0 2 2h14"/><path d="M18 22V8a2 2 0 0 0-2-2H2"/><polyline points="8 12 12 8 16 12"/>',
    "crop-full": '<rect x="3" y="5" width="18" height="14" rx="1.5"/><polyline points="9 13 11 15 15 10"/>',
    "bar-chart": '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
    "file-text": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/><polyline points="14 2 14 8 20 8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/><polyline points="10 9 9 9 8 9"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/>',
    "trash": '<polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"/>',
    "refresh-cw": '<polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/>',
    "help-circle": '<circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "alert-triangle": '<path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>',
    "filter": '<polygon points="22 3 2 3 10 12.46 10 19 14 21 14 12.46 22 3"/>',
    "keyboard": '<rect x="2" y="5" width="20" height="14" rx="2" ry="2"/><line x1="6" y1="9" x2="6.01" y2="9"/><line x1="10" y1="9" x2="10.01" y2="9"/><line x1="14" y1="9" x2="14.01" y2="9"/><line x1="18" y1="9" x2="18.01" y2="9"/><line x1="7" y1="13" x2="17" y2="13"/>',
    "panel-top-close": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="m9 16 3-3 3 3"/>',
    "panel-top-open": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M3 9h18"/><path d="m9 14 3 3 3-3"/>',
    "pin": '<path d="M12 17v5"/><path d="M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/>',
    "pin-off": '<path d="M12 17v5"/><path d="M15 9.34V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H7.89"/><path d="m2 2 20 20"/><path d="M9 9v1.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h11"/>',
    "maximize-2": '<polyline points="15 3 21 3 21 9"/><polyline points="9 21 3 21 3 15"/><line x1="21" x2="14" y1="3" y2="10"/><line x1="3" x2="10" y1="21" y2="14"/>',
    "minimize-2": '<polyline points="4 14 10 14 10 20"/><polyline points="20 10 14 10 14 4"/><line x1="14" x2="21" y1="10" y2="3"/><line x1="3" x2="10" y1="21" y2="14"/>',
    "move-horizontal": '<polyline points="18 8 22 12 18 16"/><polyline points="6 8 2 12 6 16"/><line x1="2" x2="22" y1="12" y2="12"/>',
}


def build_svg_xml(
    name: str, color: str | None = None, stroke_width: float = 2.0
) -> str:
    """지정 아이콘의 색상, 선 굵기가 적용된 SVG XML 문자열 생성

    색상 미지정 시 활성 테마 팔레트의 text_primary 색상 자동 적용
    """
    if color is None:
        color = get_active_palette().text_primary
    body = SVG_TEMPLATES.get(name, "")
    if "fill=\"currentColor\"" in body:
        body = body.replace("currentColor", color)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" '
        f'viewBox="0 0 24 24" fill="none" stroke="{color}" '
        f'stroke-width="{stroke_width}" stroke-linecap="round" stroke-linejoin="round">'
        f"{body}</svg>"
    )


_active_palette: ThemePalette | None = None


def set_active_palette(palette: ThemePalette) -> None:
    """현재 애플리케이션의 활성 테마 팔레트를 동기화"""
    global _active_palette
    _active_palette = palette


def get_active_palette() -> ThemePalette:
    """활성 테마 팔레트 반환

    미설정 시 OS 다크 모드 여부를 감지해 기본 팔레트 선택
    """
    global _active_palette
    if _active_palette is not None:
        return _active_palette

    app = QApplication.instance()
    if isinstance(app, QApplication):
        hints = app.styleHints()
        if hints is not None and hints.colorScheme() == Qt.ColorScheme.Light:
            return LIGHT_PALETTE

    return DARK_PALETTE


def _render_icon_pixmaps(name: str, color: str, size: int, pad_right: int) -> list[QPixmap]:
    """배율 1, 2의 SVG 아이콘 픽스맵 목록을 지정 색으로 렌더링"""
    svg_str = build_svg_xml(name, color=color)
    renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))

    pixmaps: list[QPixmap] = []
    for scale in (1, 2):
        w = (size + pad_right) * scale
        h = size * scale
        pixmap = QPixmap(QSize(w, h))
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
        painter.end()
        pixmaps.append(pixmap)
    return pixmaps


def get_icon(
    name: str,
    color: str | None = None,
    size: int = 16,
    palette: ThemePalette | None = None,
    pad_right: int = 0,
    disabled_color: str | None = None,
) -> QIcon:
    """지정 크기, 배율(DPI Scaling 대응) QIcon 객체 생성 및 반환

    color 미지정 시 활성 테마 텍스트 색상 적용, pad_right 지정 시 우측 투명 여백 추가.
    disabled_color 지정 시 비활성 상태 전용 색을 쓰고, 미지정이면 Qt가 일반 픽스맵으로 비활성 표현을 자동 생성
    """
    if color is None:
        p = palette or get_active_palette()
        color = p.text_primary

    icon = QIcon()
    for pixmap in _render_icon_pixmaps(name, color, size, pad_right):
        icon.addPixmap(pixmap, QIcon.Mode.Normal)
    if disabled_color is not None:
        for pixmap in _render_icon_pixmaps(name, disabled_color, size, pad_right):
            icon.addPixmap(pixmap, QIcon.Mode.Disabled)
    return icon


def set_button_icon(
    button: QPushButton,
    name: str,
    color: str | None = None,
    size: int = 14,
    pad_right: int = 7,
    palette: ThemePalette | None = None,
    disabled_color: str | None = None,
) -> None:
    """QPushButton에 아이콘을 설정하고 우측 여백을 포함한 아이콘 크기를 동기화"""
    icon = get_icon(
        name,
        color=color,
        size=size,
        palette=palette,
        pad_right=pad_right,
        disabled_color=disabled_color,
    )
    button.setIcon(icon)
    button.setIconSize(QSize(size + pad_right, size))


def create_icon_button(
    parent: QWidget | None,
    tooltip: str,
    on_click: Callable[[], None],
    *,
    accessible_name: str,
    fixed_size: int = 36,
    object_name: str = "IconActionButton",
) -> QPushButton:
    """아이콘 전용 정사각 QPushButton을 생성하고 접근성 이름, 커서, 툴팁, 클릭 시그널을 연결"""
    btn = QPushButton(parent)
    btn.setObjectName(object_name)
    btn.setFixedSize(fixed_size, fixed_size)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setToolTip(tooltip)
    btn.setAccessibleName(accessible_name)
    btn.clicked.connect(on_click)
    return btn


# 정적 번들 SVG 아이콘 디렉터리(읽기 전용 기준) 및 런타임 캐시 설정
ASSETS_DIR: str = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "assets", "icons")
)
_ICON_CACHE_SUBDIR: str = "video_classifier_icons"
_QSS_ICON_STROKE_WIDTH: float = 2.2
CHECK_QSS_STROKE_WIDTH: float = 2.6


def _get_icon_cache_dir() -> str:
    """QSS url() 폴백 SVG를 저장할 사용자 쓰기 가능 캐시 디렉터리를 반환"""
    cache_root = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.CacheLocation
    )
    base = cache_root if cache_root else tempfile.gettempdir()
    return os.path.join(base, _ICON_CACHE_SUBDIR)


def resolve_qss_icon_path(
    name: str,
    palette: ThemePalette,
    *,
    color: str | None = None,
    stroke_width: float = _QSS_ICON_STROKE_WIDTH,
) -> str:
    """QSS url() 참조용 SVG 파일 경로 반환

    정적 번들 에셋 일치 시 디스크 쓰기 없이 재사용하고, 누락, 불일치 시 사용자 캐시 경로에 지연 생성
    """
    filename = f"{name}-{palette.name}.svg"
    bundled_path = os.path.join(ASSETS_DIR, filename)
    resolved_color = color if color is not None else palette.text_primary
    content = build_svg_xml(
        name, color=resolved_color, stroke_width=stroke_width
    )

    if os.path.isfile(bundled_path):
        try:
            with open(bundled_path, encoding="utf-8") as f:
                if f.read() == content:
                    return bundled_path.replace("\\", "/")
        except OSError:
            logging.getLogger(__name__).debug(
                "번들 SVG 아이콘 읽기 실패: %s", bundled_path, exc_info=True
            )

    cache_dir = _get_icon_cache_dir()
    cache_path = os.path.join(cache_dir, filename)
    try:
        if os.path.isfile(cache_path):
            with open(cache_path, encoding="utf-8") as f:
                if f.read() == content:
                    return cache_path.replace("\\", "/")
        os.makedirs(cache_dir, exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            f.write(content)
        return cache_path.replace("\\", "/")
    except OSError:
        logging.getLogger(__name__).debug(
            "SVG 아이콘 캐시 기록 실패: %s", cache_path, exc_info=True
        )
    return bundled_path.replace("\\", "/") if os.path.isfile(bundled_path) else ""
