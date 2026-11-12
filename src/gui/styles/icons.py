import os
from typing import Dict, Optional
from PyQt6.QtCore import QByteArray, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication, QPushButton

from src.gui.styles.palettes import DARK_PALETTE, LIGHT_PALETTE, ThemePalette


# 24x24 SVG 아이콘 패스 정의
SVG_TEMPLATES: Dict[str, str] = {
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
    "rotate-ccw": '<path d="M1 4v6h6"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
    "play": '<polygon points="6 4 20 12 6 20 6 4" fill="currentColor" stroke="none"/>',
    "copy": '<rect x="9" y="9" width="13" height="13" rx="2" ry="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    "ban": '<circle cx="12" cy="12" r="10"/><line x1="4.93" y1="4.93" x2="19.07" y2="19.07"/>',
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "scan": '<path d="M3 7V5a2 2 0 0 1 2-2h2"/><path d="M17 3h2a2 2 0 0 1 2 2v2"/><path d="M21 17v2a2 2 0 0 1-2 2h-2"/><path d="M7 21H5a2 2 0 0 1-2-2v-2"/>',
    "x-circle": '<circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/>',
    "eye": '<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>',
    "check-circle": '<path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"/><polyline points="22 4 12 14.01 9 11.01"/>',
    "undo": '<polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10"/>',
    "sun": '<circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/>',
    "moon": '<path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/>',
    "monitor": '<rect x="2" y="3" width="20" height="14" rx="2" ry="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "x": '<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>',
    "sliders": '<line x1="4" y1="21" x2="4" y2="14"/><line x1="4" y1="10" x2="4" y2="3"/><line x1="12" y1="21" x2="12" y2="12"/><line x1="12" y1="8" x2="12" y2="3"/><line x1="20" y1="21" x2="20" y2="16"/><line x1="20" y1="12" x2="20" y2="3"/><line x1="1" y1="14" x2="7" y2="14"/><line x1="9" y1="8" x2="15" y2="8"/><line x1="17" y1="16" x2="23" y2="16"/>',
    "film": '<rect x="2" y="2" width="20" height="20" rx="2.18" ry="2.18"/><line x1="7" y1="2" x2="7" y2="22"/><line x1="17" y1="2" x2="17" y2="22"/><line x1="2" y1="12" x2="22" y2="12"/><line x1="2" y1="7" x2="7" y2="7"/><line x1="2" y1="17" x2="7" y2="17"/><line x1="17" y1="17" x2="22" y2="17"/><line x1="17" y1="7" x2="22" y2="7"/>',
    "bar-chart": '<line x1="18" y1="20" x2="18" y2="10"/><line x1="12" y1="20" x2="12" y2="4"/><line x1="6" y1="20" x2="6" y2="14"/>',
}


def build_svg_xml(
    name: str, color: Optional[str] = None, stroke_width: float = 2.0
) -> str:
    """
    - 지정된 이름의 아이콘에 대해 색상과 선 굵기가 적용된 SVG XML 문자열을 생성.
    - 색상 미지정 시 활성 테마 팔레트의 text_primary 색상을 자동 적용.
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


_active_palette: Optional[ThemePalette] = None


def set_active_palette(palette: ThemePalette) -> None:
    """
    - 현재 애플리케이션의 활성 테마 팔레트를 동기화.
    """
    global _active_palette
    _active_palette = palette


def get_active_palette() -> ThemePalette:
    """
    - 활성 테마 팔레트를 반환. 설정되어 있지 않을 경우 OS 다크 모드 여부를 감지하여 기본 팔레트 선택.
    """
    global _active_palette
    if _active_palette is not None:
        return _active_palette

    app = QApplication.instance()
    if app and hasattr(app, "styleHints"):
        if app.styleHints().colorScheme() == Qt.ColorScheme.Light:
            return LIGHT_PALETTE

    return DARK_PALETTE


def get_icon(
    name: str,
    color: Optional[str] = None,
    size: int = 16,
    palette: Optional[ThemePalette] = None,
    pad_right: int = 0,
) -> QIcon:
    """
    - 지정된 크기 및 배율(DPI Scaling 대응)의 QIcon 객체를 생성하여 반환.
    - color가 미지정된 경우 현재 활성 테마 팔레트의 텍스트 색상을 자동 적용.
    - pad_right 지정 시 아이콘 우측에 투명 여백을 추가하여 텍스트와의 간격을 확보.
    """
    if color is None:
        p = palette or get_active_palette()
        color = p.text_primary

    svg_str = build_svg_xml(name, color=color)
    renderer = QSvgRenderer(QByteArray(svg_str.encode("utf-8")))

    icon = QIcon()
    for scale in (1, 2):
        w = (size + pad_right) * scale
        h = size * scale
        pixmap = QPixmap(QSize(w, h))
        pixmap.fill(QColor(0, 0, 0, 0))
        painter = QPainter(pixmap)
        renderer.render(painter, QRectF(0, 0, size * scale, size * scale))
        painter.end()
        icon.addPixmap(pixmap)

    return icon


def set_button_icon(
    button: QPushButton,
    name: str,
    color: Optional[str] = None,
    size: int = 14,
    pad_right: int = 7,
    palette: Optional[ThemePalette] = None,
) -> None:
    """
    - QPushButton에 아이콘을 설정하고 우측 여백을 포함한 아이콘 크기를 동기화.
    """
    icon = get_icon(name, color=color, size=size, palette=palette, pad_right=pad_right)
    button.setIcon(icon)
    button.setIconSize(QSize(size + pad_right, size))


# QSS에서 직접 참조할 수 있도록 SVG 파일 에셋 생성 경로 보장
ASSETS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "assets", "icons")
)


def ensure_svg_assets_on_disk() -> None:
    """
    - QSS url() 참조를 위해 다크/라이트 테마별 핵심 SVG 파일들을 디스크에 저장.
    """
    os.makedirs(ASSETS_DIR, exist_ok=True)
    theme_colors = {
        "dark": DARK_PALETTE.text_primary,
        "light": LIGHT_PALETTE.text_primary,
    }
    for icon_name in ("chevron-up", "chevron-down", "search"):
        for theme_name, col in theme_colors.items():
            filename = f"{icon_name}-{theme_name}.svg"
            filepath = os.path.join(ASSETS_DIR, filename)
            content = build_svg_xml(icon_name, color=col, stroke_width=2.2)
            try:
                with open(filepath, "w", encoding="utf-8") as f:
                    f.write(content)
            except Exception:
                pass


ensure_svg_assets_on_disk()
