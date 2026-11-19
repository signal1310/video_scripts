"""미리 보기 정보 표시 항목의 이름과 값을 표 열 정의에서 가져와 만드는 모듈

- 표 열과 같은 항목은 이름과 값을 표(table_model, table_columns)의 정의로 만들어 표와 어긋나지 않게 함
- 이름은 두 가지, 좁은 영상 위 표시는 표 헤더 그대로의 약어(BR, KF 간격)이고 설정 화면은 풀네임
- 재생기에서 얻는 항목만 이 모듈이 직접 서식을 정함
"""

from collections.abc import Mapping
from dataclasses import dataclass

from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.core.resolution_tiers import ResolutionTier
from src.core.settings.preview_info import (
    INFO_KEY_RESOLUTION,
    INFO_MODE_DETAIL,
    INFO_MODE_OFF,
    INFO_MODE_SUMMARY,
    JUDGMENT_INFO_KEYS,
    PLAYER_INFO_KEYS,
    VIDEO_INFO_KEYS,
)
from src.gui.styles.palettes import DARK_PALETTE, ThemePalette
from src.gui.table_columns import COL_DEF_MAP
from src.gui.table_model import VideoTableModel, display_text_for

INFO_EMPTY_TEXT: str = "-"
INFO_VALUE_SEPARATOR: str = "x"
INFO_MODE_LABELS: dict[str, str] = {
    INFO_MODE_OFF: "끔",
    INFO_MODE_SUMMARY: "요약",
    INFO_MODE_DETAIL: "상세",
}
INFO_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("영상 정보", VIDEO_INFO_KEYS),
    ("판정 정보", JUDGMENT_INFO_KEYS),
    ("재생기 상태", PLAYER_INFO_KEYS),
)

_MS_PER_SECOND: int = 1000
_US_PER_SECOND: int = 1_000_000
_SECONDS_PER_MINUTE: int = 60
_SECONDS_PER_HOUR: int = 3600
_PIXEL_FORMAT_PREFIX: str = "Format_"
_PLAY_FPS_DECIMALS: int = 1
_FRAME_TIME_DECIMALS: int = 3

# - 표 열과 같은 항목의 키(ColumnId.value)와 표시에 쓰는 표 열 번호
TABLE_INFO_COLUMNS: dict[str, int] = {
    "name": VideoTableModel.COL_NAME,
    "rotate": VideoTableModel.COL_ROTATE,
    "codec": VideoTableModel.COL_CODEC,
    "fps": VideoTableModel.COL_FPS,
    "duration": VideoTableModel.COL_DURATION,
    "bitrate": VideoTableModel.COL_BITRATE,
    "opt_bitrate": VideoTableModel.COL_OPT_BITRATE,
    "ratio": VideoTableModel.COL_RATIO,
    "out_bitrate": VideoTableModel.COL_OUT_BITRATE,
    "target_ratio": VideoTableModel.COL_TARGET_RATIO,
    "zone": VideoTableModel.COL_ZONE,
    "keyframe": VideoTableModel.COL_KEYFRAME,
    "size": VideoTableModel.COL_SIZE,
    "after_size": VideoTableModel.COL_AFTER_SIZE,
    "saved_size": VideoTableModel.COL_SAVED_SIZE,
    "saved_per_min": VideoTableModel.COL_SAVED_PER_MIN,
    "est_encode_time": VideoTableModel.COL_EST_ENCODE_TIME,
    "output_res": VideoTableModel.COL_OUTPUT_RES,
    "action": VideoTableModel.COL_ACTION,
    "reason": VideoTableModel.COL_REASON,
}
RESOLUTION_INFO_COLUMNS: tuple[int, int] = (VideoTableModel.COL_WIDTH, VideoTableModel.COL_HEIGHT)

_RESOLUTION_LABEL: str = "해상도"
_PLAYER_LABELS: dict[str, str] = {
    "play_resolution": "재생 해상도",
    "view_size": "화면 크기",
    "pixel_format": "프레임 형식",
    "play_fps": "재생 FPS",
    "position": "현재 위치",
    "frame_time": "프레임 시각",
}
_PLAYER_SHORT_LABELS: dict[str, str] = {**_PLAYER_LABELS, "position": "위치"}


@dataclass(frozen=True, slots=True)
class PreviewVideoInfo:
    """정보 표시가 표에서 가져오는 한 영상의 평가 결과와 표 값 계산 입력"""

    ev: VideoEvaluation
    config: ClassifierConfig
    pending_tiers: Mapping[str, ResolutionTier | None]
    selected_count: int


@dataclass(frozen=True, slots=True)
class PlayerSnapshot:
    """정보 표시가 재생기에서 가져오는 순간 상태, Qt 객체 없이 값만 담음"""

    decoded_size: tuple[int, int] | None
    view_size: tuple[int, int]
    pixel_format: str
    play_fps: float | None
    position_ms: int
    duration_ms: int
    frame_time_us: int | None


def info_label(key: str, config: ClassifierConfig, *, short: bool = False) -> str:
    """항목 이름, 표 열과 같은 항목은 그 열의 헤더(short면 표에 보이는 약어, 아니면 풀네임, 용량 열은 단위 포함)"""
    if key == INFO_KEY_RESOLUTION:
        return _RESOLUTION_LABEL
    player_label = (_PLAYER_SHORT_LABELS if short else _PLAYER_LABELS).get(key)
    if player_label is not None:
        return player_label
    col_def = COL_DEF_MAP[TABLE_INFO_COLUMNS[key]]
    if col_def.header_factory is not None:
        return col_def.header_factory(config)
    return col_def.header if short else col_def.menu_title


def info_value(key: str, video: PreviewVideoInfo | None, player: PlayerSnapshot) -> str:
    """항목 값, 표시할 값이 없으면 빈 값 표기"""
    if key in _PLAYER_LABELS:
        return _player_value(key, player)
    if video is None:
        return INFO_EMPTY_TEXT
    if key == INFO_KEY_RESOLUTION:
        width, height = (_cell_text(video, col) for col in RESOLUTION_INFO_COLUMNS)
        return f"{width}{INFO_VALUE_SEPARATOR}{height}"
    return _cell_text(video, TABLE_INFO_COLUMNS[key])


# - 영상 위 정보 표시는 테마와 무관한 어두운 고정 표면이므로 색은 다크 팔레트에서 고름
_OVERLAY_PALETTE: ThemePalette = DARK_PALETTE


def info_value_color(key: str, video: PreviewVideoInfo | None) -> str | None:
    """표 셀의 전경색 또는 배지 글자색을 그대로 가져오고, 흐린 기본색이면 기본 글자색을 쓰도록 None 반환"""
    if video is None or key not in TABLE_INFO_COLUMNS:
        return None
    col_def = COL_DEF_MAP[TABLE_INFO_COLUMNS[key]]
    color: str | None = None
    if col_def.foreground_getter is not None:
        color = col_def.foreground_getter(video.ev, _OVERLAY_PALETTE)
    elif col_def.badge_getter is not None:
        badge = col_def.badge_getter(video.ev, _OVERLAY_PALETTE)
        if isinstance(badge, tuple) and len(badge) == 3:
            # - 배지 튜플은 (배경, 글자, 테두리) 순서
            color = str(badge[1])
    if color is None or color == _OVERLAY_PALETTE.fg_cell_muted:
        return None
    return color


def info_value_colors(keys: list[str], video: PreviewVideoInfo | None) -> list[str | None]:
    """항목 키 순서대로 값의 상태 색 목록"""
    return [info_value_color(key, video) for key in keys]


def info_rows(
    keys: list[str],
    video: PreviewVideoInfo | None,
    player: PlayerSnapshot,
    config: ClassifierConfig,
    *,
    short: bool = False,
) -> list[tuple[str, str]]:
    """항목 키마다 (이름, 값) 쌍을 만들어 키 순서대로 반환"""
    return [
        (info_label(key, config, short=short), info_value(key, video, player)) for key in keys
    ]


def _cell_text(video: PreviewVideoInfo, col: int) -> str:
    """표 셀과 같은 함수로 계산한 표시 문자열"""
    value = display_text_for(video.ev, col, video.config, video.pending_tiers)
    return INFO_EMPTY_TEXT if value is None else str(value)


def _player_value(key: str, player: PlayerSnapshot) -> str:
    if key == "play_resolution":
        return _format_size(player.decoded_size)
    if key == "view_size":
        return _format_size(player.view_size)
    if key == "pixel_format":
        return player.pixel_format.removeprefix(_PIXEL_FORMAT_PREFIX) or INFO_EMPTY_TEXT
    if key == "play_fps":
        if player.play_fps is None:
            return INFO_EMPTY_TEXT
        return f"{player.play_fps:.{_PLAY_FPS_DECIMALS}f}"
    if key == "position":
        return f"{format_clock_ms(player.position_ms)} / {format_clock_ms(player.duration_ms)}"
    if player.frame_time_us is None:
        return INFO_EMPTY_TEXT
    return f"{player.frame_time_us / _US_PER_SECOND:.{_FRAME_TIME_DECIMALS}f}초"


def _format_size(size: tuple[int, int] | None) -> str:
    if size is None or size[0] <= 0 or size[1] <= 0:
        return INFO_EMPTY_TEXT
    return f"{size[0]}{INFO_VALUE_SEPARATOR}{size[1]}"


def format_clock_ms(ms: int) -> str:
    """밀리초를 분:초.밀리초로 바꾸고 한 시간 이상이면 시 단위를 앞에 붙임"""
    total_ms = max(ms, 0)
    total_seconds, millis = divmod(total_ms, _MS_PER_SECOND)
    hours, rest = divmod(total_seconds, _SECONDS_PER_HOUR)
    minutes, seconds = divmod(rest, _SECONDS_PER_MINUTE)
    clock = f"{minutes:02d}:{seconds:02d}.{millis:03d}"
    return f"{hours}:{clock}" if hours else clock
