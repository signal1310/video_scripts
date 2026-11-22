"""화질 비교 창의 팔레트 기반 스타일 시트

- 색은 팔레트 토큰만 쓰고, 독립 창 안의 메뉴와 툴팁도 같은 팔레트로 칠함
- 배치와 문구는 `frame_compare_layout.py`가 맡고 이 모듈은 스타일만 만듦
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
    CompareToolbar,
)
from src.gui.styles.icons import get_active_palette, get_icon
from src.gui.styles.palettes import ThemePalette

_RADII = "6px"
_CHIP_RADII = "8px"
_PILL_RADII = "10px"


def build_stylesheet(palette: ThemePalette) -> str:
    """창 전체에 적용하는 팔레트 기반 스타일 시트"""
    return f"""
QWidget#CompareToolbar, QWidget#CompareTimeline, QWidget#CompareMeta {{
    background: {palette.bg_card}; border-bottom: 1px solid {palette.border_subtle};
}}
QWidget#CompareStageWrap {{ background: {palette.media_bg}; border-bottom: 1px solid {palette.border_subtle}; }}
QWidget#CompareTimeline {{ border-top: 1px solid {palette.border_subtle}; border-bottom: 0; }}
QLabel#CompareFileName {{ color: {palette.text_primary}; font-weight: 600; }}
QLabel#CompareCaption {{ color: {palette.text_muted}; }}
QLabel#CompareMetaText {{ color: {palette.text_secondary}; }}
QLabel#CompareMetaSeparator {{ color: {palette.text_muted}; }}
QLabel#CompareScore {{
    background: {palette.subtitle_bg}; color: {palette.subtitle_text};
    border-bottom: 1px solid {palette.subtitle_border}; padding: 6px 14px;
}}
QSpinBox#CompareZoomInput {{
    color: {palette.text_secondary}; border: 1px solid {palette.border_subtle};
    border-radius: {_RADII}; padding: 2px 6px; background: {palette.bg_input};
}}
QSpinBox#CompareZoomInput:focus {{ border-color: {palette.border_focus}; color: {palette.text_primary}; }}
QToolButton#CompareTargetButton {{
    color: {palette.text_primary}; background: {palette.bg_input};
    border: 1px solid {palette.border_subtle}; border-radius: {_RADII}; padding: 4px 8px;
}}
QToolButton#CompareTargetButton:hover {{ border-color: {palette.border_focus}; background: {palette.bg_hover}; }}
QToolButton#CompareTargetButton::menu-indicator {{ image: none; width: 0px; }}
QToolButton#CompareRateButton::menu-indicator {{ image: none; width: 0px; }}
QWidget#CompareSegmented {{
    background: {palette.bg_input}; border: 1px solid {palette.border_subtle}; border-radius: {_RADII};
}}
QWidget#CompareSegmented QToolButton {{
    border: 0; background: transparent; color: {palette.text_secondary};
    border-radius: 4px; padding: 3px 10px;
}}
QWidget#CompareSegmented QToolButton:hover {{ background: {palette.bg_hover}; color: {palette.text_primary}; }}
QWidget#CompareSegmented QToolButton:checked {{
    background: {palette.bg_selected}; color: {palette.text_primary}; font-weight: 600;
    border: 1px solid {palette.action_primary_border};
}}
QToolButton#CompareIconButton {{ border: 0; background: transparent; border-radius: 4px; }}
QToolButton#CompareIconButton:hover {{ background: {palette.bg_hover}; }}
QToolButton#CompareMarkChip {{
    color: {palette.text_secondary}; border: 1px solid {palette.border_subtle};
    border-radius: {_CHIP_RADII}; padding: 1px 8px; background: {palette.bg_input};
}}
QToolButton#CompareMarkChip:checked {{
    background: {palette.bg_selected}; border-color: {palette.action_primary_border};
    color: {palette.text_primary}; font-weight: 600;
}}
QLabel#CompareTimeLabel {{ color: {palette.text_secondary}; }}
QLabel#CompareShortcuts {{ color: {palette.text_muted}; }}
QWidget#CompareOverlay {{ background: {palette.media_bg}; border: 1px solid {palette.border_subtle}; }}
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
    height: 3px; background: {palette.progress_track_bg}; border-radius: 2px;
}}
QSlider#CompareTimelineSlider::sub-page:horizontal {{ background: {palette.action_primary_bg}; border-radius: 2px; }}
QSlider#CompareTimelineSlider::handle:horizontal {{
    width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; background: {palette.action_primary_bg};
}}
QSlider#CompareVolumeSlider::groove:horizontal {{
    height: 3px; background: {palette.progress_track_bg}; border-radius: 2px;
}}
QSlider#CompareVolumeSlider::sub-page:horizontal {{ background: {palette.action_primary_bg}; border-radius: 2px; }}
QSlider#CompareVolumeSlider::handle:horizontal {{
    width: 10px; height: 10px; margin: -4px 0; border-radius: 5px; background: {palette.action_primary_bg};
}}
"""

def refresh_icons(toolbar: CompareToolbar, timeline: CompareTimeline, palette: ThemePalette) -> None:
    """테마가 바뀌면 아이콘 색을 다시 입힘"""
    toolbar.target_button.setIcon(get_icon("chevron-down", color=palette.text_secondary, size=ICON_SIZE))
    timeline.prev_button.setIcon(get_icon("chevron-left", color=palette.text_primary, size=ICON_SIZE))
    timeline.next_button.setIcon(get_icon("chevron-right", color=palette.text_primary, size=ICON_SIZE))


def update_play_button(timeline: CompareTimeline, playing: bool) -> None:
    """재생 상태에 맞춰 재생 아이콘, 툴팁, 접근성 이름을 바꿈

    - 조작 바는 카드 배경을 쓰므로 어두운 기본 글자색 아이콘으로 칠함
    """
    palette = get_active_palette()
    icon_name = PAUSE_ICON if playing else PLAY_ICON
    timeline.toggle_button.setIcon(get_icon(icon_name, color=palette.text_primary, size=ICON_SIZE))
    timeline.toggle_button.setToolTip(PLAY_TOGGLE_TIP)
    timeline.toggle_button.setAccessibleName(PAUSE_TEXT if playing else PLAY_TEXT)


def update_rate_button(timeline: CompareTimeline, rate: float) -> None:
    """현재 재생 배속을 메뉴 선택과 아이콘, 툴팁에 반영"""
    palette = get_active_palette()
    timeline.rate_button.setIcon(get_icon(RATE_ICON, color=palette.text_primary, size=ICON_SIZE))
    timeline.rate_button.setToolTip(RATE_TIP_TEMPLATE.format(rate=rate))
    action = timeline.rate_actions.get(rate)
    if action is not None:
        action.setChecked(True)


def update_mute_button(timeline: CompareTimeline, muted: bool) -> None:
    """음소거 상태에 맞춰 아이콘, 툴팁, 접근성 이름을 바꿈"""
    palette = get_active_palette()
    icon_name = VOLUME_MUTED_ICON if muted else VOLUME_ICON
    timeline.mute_button.setIcon(get_icon(icon_name, color=palette.text_primary, size=ICON_SIZE))
    timeline.mute_button.setToolTip(UNMUTE_TIP if muted else MUTE_TIP)
    timeline.mute_button.setAccessibleName(UNMUTE_TEXT if muted else MUTE_TEXT)


def refresh_playback_buttons(
    timeline: CompareTimeline, *, playing: bool, muted: bool, rate: float
) -> None:
    """재생, 배속, 음소거 버튼을 현재 재생 상태에 맞춤"""
    update_play_button(timeline, playing)
    update_rate_button(timeline, rate)
    update_mute_button(timeline, muted)
