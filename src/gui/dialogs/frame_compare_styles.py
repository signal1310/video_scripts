"""화질 비교 창의 다크 시네마 스타일 시트

- 공용 하단 재생 바는 `build_preview_qss`를 그대로 쓰고, 이 모듈은 상단 바, 캔버스, 오버레이, 알림만 더함
- 라이트, 다크 테마와 무관하게 미리 보기와 같은 영상 팔레트 토큰으로 칠함
- 배치와 문구는 `frame_compare_layout.py`, 상단 바는 `frame_compare_top_bar.py`가 맡음
"""

from __future__ import annotations

from src.gui.styles.palettes import ThemePalette
from src.gui.styles.qss_preview import build_preview_qss

_RADII = "6px"
_PILL_RADII = "10px"
_CHIP_RADII = "8px"


def build_stylesheet(palette: ThemePalette) -> str:
    """공용 미리 보기 QSS에 화질 비교 전용 규칙을 더한 창 스타일 시트"""
    return build_preview_qss(palette) + f"""
QWidget#CompareTopBar {{
    background: {palette.media_bg}; border-bottom: 1px solid {palette.media_track};
}}
QWidget#CompareTopBar[overlay="true"] {{
    background: {palette.media_panel_bg}; border: 1px solid {palette.media_track};
    border-radius: {_RADII};
}}
QWidget#CompareStageWrap {{ background: {palette.media_bg}; }}
QLabel#CompareFileName {{ color: {palette.media_fg}; font-weight: 600; }}
QLabel#CompareMetaText {{ color: {palette.media_muted}; }}
QLabel#CompareMetaSeparator {{ color: {palette.media_track}; }}
QLabel#CompareScoreChip {{
    background: {palette.media_track}; color: {palette.media_fg};
    border-radius: {_CHIP_RADII}; padding: 2px 8px; font-weight: 600;
}}
QSpinBox#CompareZoomInput {{
    color: {palette.media_fg}; border: 1px solid {palette.media_track};
    border-radius: {_RADII}; padding: 0px; background: {palette.media_overlay_bg};
    min-height: 22px;
}}
QSpinBox#CompareZoomInput:focus {{ border-color: {palette.action_primary_bg}; }}
QSpinBox#CompareZoomInput::up-button, QSpinBox#CompareZoomInput::down-button {{
    width: 0px; border: none;
}}
QSpinBox#CompareZoomInput::up-arrow, QSpinBox#CompareZoomInput::down-arrow {{
    image: none; width: 0px; height: 0px;
}}
QSpinBox#CompareZoomInput QLineEdit {{
    border: none; padding: 0px 8px; min-height: 0px; background: transparent;
    color: {palette.media_fg};
}}
QSpinBox#CompareZoomInput QLineEdit:focus {{ border: none; }}
QToolButton#CompareTargetButton, QToolButton#CompareCollapseButton {{
    color: {palette.media_fg}; background: {palette.media_overlay_bg};
    border: 1px solid {palette.media_track}; border-radius: {_RADII}; padding: 4px 8px;
}}
QToolButton#CompareCollapseButton {{ padding: 0px; }}
QToolButton#CompareTargetButton:hover, QToolButton#CompareCollapseButton:hover {{
    background: {palette.media_track};
}}
QToolButton#CompareTargetButton::menu-indicator {{ image: none; width: 0px; }}
QWidget#CompareSegmented {{
    background: {palette.media_overlay_bg}; border: 1px solid {palette.media_track};
    border-radius: {_RADII};
}}
QWidget#CompareSegmented QToolButton {{
    border: 0; background: transparent; color: {palette.media_muted};
    border-radius: 4px; padding: 3px 10px;
}}
QWidget#CompareSegmented QToolButton:hover {{ background: {palette.media_track}; color: {palette.media_fg}; }}
QWidget#CompareSegmented QToolButton:checked {{
    background: {palette.action_primary_bg}; color: {palette.media_fg}; font-weight: 600;
}}
QWidget#CompareOverlay {{ background: {palette.media_bg}; border: 1px solid {palette.media_track}; }}
QLabel#CompareOverlayTitle {{ color: {palette.media_fg}; font-weight: 700; }}
QLabel#CompareOverlayBody {{ color: {palette.media_muted}; }}
QLabel#CompareLoadingPill {{
    background: {palette.media_overlay_bg}; color: {palette.media_fg};
    border-radius: {_PILL_RADII}; padding: 4px 12px; font-weight: 600;
}}
QLabel#CompareNoticeToast {{
    background: {palette.media_hud_bg}; color: {palette.media_fg};
    border: 1px solid {palette.media_track};
    border-radius: {_CHIP_RADII}; padding: 4px 12px; font-weight: 600;
}}
"""
