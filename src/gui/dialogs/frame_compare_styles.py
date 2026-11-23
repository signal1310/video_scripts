"""화질 비교 창의 다크 시네마 스타일 시트와 재생 아이콘 갱신

- 라이트, 다크 테마와 무관하게 미리 보기와 같은 영상 팔레트 토큰으로 상단 바, 캔버스, 하단 바를 통일
- 메뉴와 툴팁만 앱 테마 팔레트로 대비를 유지
- 배치와 문구는 `frame_compare_layout.py`, 상단 바는 `frame_compare_top_bar.py`가 맡음
"""

from __future__ import annotations

from src.gui.dialogs.frame_compare_layout import (
    ICON_SIZE,
    MUTE_TEXT,
    MUTE_TIP,
    PAUSE_ICON,
    PAUSE_TEXT,
    PLAY_ICON,
    PLAY_TEXT,
    PLAY_TOGGLE_TIP,
    RATE_ICON,
    RATE_TIP_TEMPLATE,
    UNMUTE_TEXT,
    UNMUTE_TIP,
    VOLUME_ICON,
    VOLUME_MUTED_ICON,
    CompareTimeline,
)
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette

_RADII = "6px"
_PILL_RADII = "10px"
_CHIP_RADII = "8px"


def build_stylesheet(palette: ThemePalette) -> str:
    """창 전체에 적용하는 다크 시네마 스타일 시트"""
    return f"""
QWidget#CompareTopBar {{
    background: {palette.media_bg}; border-bottom: 1px solid {palette.media_track};
}}
QWidget#CompareTopBar[overlay="true"] {{
    background: {palette.media_overlay_bg}; border: 1px solid {palette.media_track};
    border-radius: {_RADII};
}}
QWidget#CompareTimeline {{
    background: {palette.media_bg}; border-top: 1px solid {palette.media_track};
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
    min-height: 0px;
}}
QSpinBox#CompareZoomInput:focus {{ border-color: {palette.action_primary_bg}; }}
QSpinBox#CompareZoomInput QLineEdit {{
    border: none; padding: 0px; min-height: 0px; background: transparent;
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
QToolButton#CompareRateButton::menu-indicator {{ image: none; width: 0px; }}
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
QToolButton#CompareIconButton {{ border: 0; background: transparent; border-radius: 4px; }}
QToolButton#CompareIconButton:hover {{ background: {palette.media_track}; }}
QToolButton#ShortcutGuideButton {{ border: 0; background: transparent; border-radius: 4px; }}
QToolButton#ShortcutGuideButton:hover {{ background: {palette.media_track}; }}
QFrame#CompareBarDivider {{ background: {palette.media_track}; border: 0; }}
QLabel#CompareTimeLabel {{ color: {palette.media_muted}; }}
QWidget#CompareOverlay {{ background: {palette.media_bg}; border: 1px solid {palette.media_track}; }}
QLabel#CompareOverlayTitle {{ color: {palette.media_fg}; font-weight: 700; }}
QLabel#CompareOverlayBody {{ color: {palette.media_muted}; }}
QLabel#CompareLoadingPill {{
    background: {palette.media_overlay_bg}; color: {palette.media_fg};
    border-radius: {_PILL_RADII}; padding: 4px 12px; font-weight: 600;
}}
QMenu {{
    background-color: {palette.bg_card}; color: {palette.text_primary};
    border: 1px solid {palette.border_subtle}; border-radius: {_RADII}; padding: 4px;
}}
QMenu::item {{ background: transparent; color: {palette.text_primary}; padding: 4px 18px; border-radius: 4px; }}
QMenu::item:selected {{ background-color: {palette.bg_selected}; color: {palette.text_primary}; }}
QMenu::item:disabled {{ color: {palette.text_muted}; }}
QMenu::item:checked {{ color: {palette.accent_blue}; font-weight: 600; }}
QMenu::separator {{ height: 1px; background: {palette.border_subtle}; margin: 4px 8px; }}
QToolTip {{
    background-color: {palette.bg_card}; color: {palette.text_primary};
    border: 1px solid {palette.border_focus}; border-radius: 4px; padding: 6px;
}}
QSlider#CompareTimelineSlider::groove:horizontal {{
    height: 3px; background: {palette.media_track}; border-radius: 2px;
}}
QSlider#CompareTimelineSlider::sub-page:horizontal {{ background: {palette.action_primary_bg}; border-radius: 2px; }}
QSlider#CompareTimelineSlider::handle:horizontal {{
    width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; background: {palette.action_primary_bg};
}}
QSlider#CompareVolumeSlider::groove:horizontal {{
    height: 3px; background: {palette.media_track}; border-radius: 2px;
}}
QSlider#CompareVolumeSlider::sub-page:horizontal {{ background: {palette.action_primary_bg}; border-radius: 2px; }}
QSlider#CompareVolumeSlider::handle:horizontal {{
    width: 10px; height: 10px; margin: -4px 0; border-radius: 5px; background: {palette.action_primary_bg};
}}
"""



def refresh_timeline_icons(timeline: CompareTimeline, palette: ThemePalette) -> None:
    """테마가 바뀌면 하단 바의 프레임 이동과 전체화면 아이콘 색을 다시 입힘"""
    timeline.prev_button.setIcon(get_icon("chevron-left", color=palette.media_fg, size=ICON_SIZE))
    timeline.next_button.setIcon(get_icon("chevron-right", color=palette.media_fg, size=ICON_SIZE))
    timeline.fullscreen_button.setIcon(
        get_icon("maximize-2", color=palette.media_fg, size=ICON_SIZE)
    )


def update_play_button(timeline: CompareTimeline, playing: bool) -> None:
    """재생 상태에 맞춰 재생 아이콘, 툴팁, 접근성 이름을 바꿈"""
    palette = get_active_palette()
    icon_name = PAUSE_ICON if playing else PLAY_ICON
    timeline.toggle_button.setIcon(get_icon(icon_name, color=palette.media_fg, size=ICON_SIZE))
    timeline.toggle_button.setToolTip(PLAY_TOGGLE_TIP)
    timeline.toggle_button.setAccessibleName(PAUSE_TEXT if playing else PLAY_TEXT)


def update_rate_button(timeline: CompareTimeline, rate: float) -> None:
    """현재 재생 배속을 메뉴 선택과 아이콘, 툴팁에 반영"""
    palette = get_active_palette()
    timeline.rate_button.setIcon(get_icon(RATE_ICON, color=palette.media_fg, size=ICON_SIZE))
    timeline.rate_button.setToolTip(RATE_TIP_TEMPLATE.format(rate=rate))
    action = timeline.rate_actions.get(rate)
    if action is not None:
        action.setChecked(True)


def update_mute_button(timeline: CompareTimeline, muted: bool) -> None:
    """음소거 상태에 맞춰 아이콘, 툴팁, 접근성 이름을 바꿈"""
    palette = get_active_palette()
    icon_name = VOLUME_MUTED_ICON if muted else VOLUME_ICON
    timeline.mute_button.setIcon(get_icon(icon_name, color=palette.media_fg, size=ICON_SIZE))
    timeline.mute_button.setToolTip(UNMUTE_TIP if muted else MUTE_TIP)
    timeline.mute_button.setAccessibleName(UNMUTE_TEXT if muted else MUTE_TEXT)


def update_fullscreen_button(timeline: CompareTimeline, fullscreen: bool) -> None:
    """전체화면 여부에 맞춰 해제, 전환 아이콘과 접근성 이름을 바꿈"""
    palette = get_active_palette()
    icon_name = "minimize-2" if fullscreen else "maximize-2"
    timeline.fullscreen_button.setIcon(get_icon(icon_name, color=palette.media_fg, size=ICON_SIZE))
    timeline.fullscreen_button.setAccessibleName("전체화면 해제" if fullscreen else "전체화면 전환")


def refresh_playback_buttons(
    timeline: CompareTimeline, *, playing: bool, muted: bool, rate: float
) -> None:
    """재생, 배속, 음소거 버튼을 현재 재생 상태에 맞춤"""
    update_play_button(timeline, playing)
    update_rate_button(timeline, rate)
    update_mute_button(timeline, muted)
