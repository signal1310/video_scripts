"""미리 보기 정보 표시의 단계, 배치, 항목 키 기준 정의 모듈"""

INFO_MODE_OFF: str = "off"
INFO_MODE_SUMMARY: str = "summary"
INFO_MODE_DETAIL: str = "detail"
VALID_INFO_MODES: tuple[str, ...] = (INFO_MODE_OFF, INFO_MODE_SUMMARY, INFO_MODE_DETAIL)
DEFAULT_INFO_MODE: str = INFO_MODE_OFF

INFO_LAYOUT_SINGLE_LINE: str = "single_line"
INFO_LAYOUT_MULTI_LINE: str = "multi_line"
VALID_INFO_LAYOUTS: tuple[str, ...] = (INFO_LAYOUT_SINGLE_LINE, INFO_LAYOUT_MULTI_LINE)
DEFAULT_INFO_LAYOUT: str = INFO_LAYOUT_MULTI_LINE

INFO_ANCHOR_MIN: float = 0.0
INFO_ANCHOR_MAX: float = 1.0
DEFAULT_INFO_ANCHOR_X: float = INFO_ANCHOR_MIN
DEFAULT_INFO_ANCHOR_Y: float = INFO_ANCHOR_MIN

# - 상자 너비, 높이는 영상 영역 대비 비율, 0은 내용에 맞춘 자동 크기
INFO_BOX_RATIO_MIN: float = 0.0
INFO_BOX_RATIO_MAX: float = 1.0
DEFAULT_INFO_BOX_WIDTH: float = INFO_BOX_RATIO_MIN
DEFAULT_INFO_BOX_HEIGHT: float = INFO_BOX_RATIO_MIN

# - 글자 크기 배율(%), 휠과 설정 막대가 같은 간격으로 움직임
INFO_SCALE_MIN: int = 60
INFO_SCALE_MAX: int = 240
INFO_SCALE_STEP: int = 10
DEFAULT_INFO_SCALE: int = 100

# - 표 열과 같은 항목의 키는 열 식별자(ColumnId.value)와 같게 두며, 너비와 높이 열은 해상도 한 항목으로 합침
INFO_KEY_RESOLUTION: str = "resolution"
VIDEO_INFO_KEYS: tuple[str, ...] = (
    "name",
    INFO_KEY_RESOLUTION,
    "rotate",
    "codec",
    "fps",
    "duration",
    "bitrate",
    "keyframe",
    "size",
)
JUDGMENT_INFO_KEYS: tuple[str, ...] = (
    "status",
    "opt_bitrate",
    "ratio",
    "out_bitrate",
    "target_ratio",
    "zone",
    "after_size",
    "saved_size",
    "saved_per_min",
    "est_encode_time",
    "output_res",
    "action",
    "reason",
)
PLAYER_INFO_KEYS: tuple[str, ...] = (
    "play_resolution",
    "view_size",
    "pixel_format",
    "play_fps",
    "position",
    "frame_time",
)
ALL_INFO_KEYS: tuple[str, ...] = VIDEO_INFO_KEYS + JUDGMENT_INFO_KEYS + PLAYER_INFO_KEYS

DEFAULT_SUMMARY_INFO_KEYS: tuple[str, ...] = (
    INFO_KEY_RESOLUTION,
    "codec",
    "fps",
    "bitrate",
    "zone",
    "output_res",
)
DEFAULT_DETAIL_INFO_KEYS: tuple[str, ...] = (
    "name",
    INFO_KEY_RESOLUTION,
    "rotate",
    "codec",
    "fps",
    "duration",
    "bitrate",
    "keyframe",
    "ratio",
    "zone",
    "output_res",
    "action",
    "reason",
    "play_resolution",
    "play_fps",
    "frame_time",
)


def normalize_info_keys(keys: list[str] | tuple[str, ...]) -> list[str]:
    """유효한 항목 키만 남기고 중복을 없애되, 처음 나온 순서는 사용자가 정한 표시 순서로 유지"""
    valid = set(ALL_INFO_KEYS)
    return list(dict.fromkeys(key for key in keys if key in valid))
