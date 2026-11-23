"""내장 미리 보기 창과 오버레이(gui/preview) 전용 QSS 규칙 생성 모듈"""

from src.gui.styles.palettes import ThemePalette


def build_preview_qss(p: ThemePalette) -> str:
    """전달된 팔레트로 내장 미리 보기 영역 QSS 문자열 반환"""
    return f"""
QWidget#PreviewControlBar {{
    background-color: {p.media_bg};
}}
QToolButton#PreviewToggleButton, QToolButton#PreviewVolumeButton {{
    background-color: transparent;
    border: none;
    border-radius: 13px;
}}
QToolButton#PreviewToggleButton:hover, QToolButton#PreviewVolumeButton:hover {{
    background-color: {p.media_track};
}}
QToolButton#PreviewToggleButton:pressed, QToolButton#PreviewVolumeButton:pressed {{
    background-color: {p.media_track};
}}
QLabel#PreviewTimeLabel {{
    color: {p.media_muted};
    font-size: 9pt;
}}
QFrame#PreviewInfoOverlayFrame, QFrame#PreviewToastFrame {{
    background-color: {p.media_overlay_bg};
    border-radius: 6px;
}}
QLabel#PreviewInfoOverlay, QLabel#PreviewToast {{
    background: transparent;
    color: {p.media_fg};
}}
QLabel#PreviewToast {{
    font-size: 9pt;
}}
QToolButton#PreviewRateButton::menu-indicator {{
    image: none;
    width: 0px;
}}
QToolButton#PreviewSettingsButton, QToolButton#PreviewRateButton,
QToolButton#PreviewCompareButton {{
    background-color: transparent;
    border: none;
    border-radius: 13px;
}}
QToolButton#PreviewSettingsButton:hover, QToolButton#PreviewSettingsButton:pressed,
QToolButton#PreviewRateButton:hover, QToolButton#PreviewRateButton:pressed,
QToolButton#PreviewCompareButton:hover, QToolButton#PreviewCompareButton:pressed {{
    background-color: {p.media_track};
}}
QFrame#PreviewBarDivider {{ background-color: {p.media_track}; border: none; }}
QToolButton#ShortcutGuideButton {{ border: 0; background: transparent; border-radius: 4px; }}
QToolButton#ShortcutGuideButton:hover {{ background: {p.media_track}; }}
QSlider#PreviewPositionSlider::groove:horizontal, QSlider#PreviewVolumeSlider::groove:horizontal {{
    height: 3px;
    background: {p.media_track};
    border-radius: 1px;
}}
QSlider#PreviewPositionSlider::sub-page:horizontal, QSlider#PreviewVolumeSlider::sub-page:horizontal {{
    background: {p.action_primary_bg};
    border-radius: 1px;
}}
QSlider#PreviewPositionSlider::handle:horizontal, QSlider#PreviewVolumeSlider::handle:horizontal {{
    width: 10px;
    height: 10px;
    margin: -4px 0;
    border: none;
    border-radius: 5px;
    background: {p.action_primary_bg};
}}
QListWidget#PreviewOrderList {{
    background-color: {p.bg_input};
    color: {p.text_primary};
    selection-background-color: {p.bg_selected};
    selection-color: {p.text_primary};
    border: 1px solid {p.border_subtle};
    border-radius: 6px;
    padding: 3px;
    outline: none;
}}
QListWidget#PreviewOrderList::item {{
    color: {p.text_primary};
    padding: 4px 6px;
    border-radius: 4px;
}}
QListWidget#PreviewOrderList::item:hover {{
    background-color: {p.bg_hover};
}}
QListWidget#PreviewOrderList::item:selected,
QListWidget#PreviewOrderList::item:selected:active,
QListWidget#PreviewOrderList::item:selected:!active {{
    background-color: {p.bg_selected};
    color: {p.text_primary};
}}
"""
