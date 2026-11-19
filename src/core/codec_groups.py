"""코덱 그룹 별칭과 정적 상대 비트레이트 가중치 기준 정의 모듈"""

# - H.264(1.00)와 동등한 체감 화질을 유지하기 위해 각 코덱이 필요로 하는 상대 비트레이트 계수(C_codec)
# - 계수 값이 작을수록(예: AV1=0.65, HEVC=0.80) 압축 성능이 높아 적은 비트레이트로 동일 화질을 구현함
# - 계수 값이 클수록(예: MPEG-4=1.30, MPEG-2=1.60) 압축 성능이 낮아 더 높은 비트레이트를 요구함
DEFAULT_CODEC_WEIGHT_FALLBACK: float = 1.00

CODEC_WEIGHT_GROUPS: dict[str, tuple[str, ...]] = {
    "av1": ("av1", "av01"),
    "hevc_vp9": ("hevc", "h265", "hev1", "hvc1", "vp9"),
    "h264": ("h264", "avc", "avc1"),
    "legacy": (
        "vp8",
        "wmv3",
        "vc1",
        "theora",
        "rv40",
        "mpeg4",
        "msmpeg4v3",
        "msmpeg4v2",
        "flv1",
        "h263",
    ),
}

_CODEC_GROUP_BY_ALIAS: dict[str, str] = {
    alias: group
    for group, aliases in CODEC_WEIGHT_GROUPS.items()
    for alias in aliases
}

_STATIC_CODEC_WEIGHTS: dict[str, float] = {
    "mpeg2video": 1.60,
    "mpeg1video": 1.60,
    "wmv2": 1.60,
    "wmv1": 1.60,
    "mjpeg": 2.50,
    "prores": 2.50,
    "dnxhd": 2.50,
}


def find_codec_group(codec_key: str) -> str | None:
    """정규화된 코덱 별칭에 대응하는 가중치 그룹 이름 반환"""
    return _CODEC_GROUP_BY_ALIAS.get(codec_key)


def get_static_codec_weight(codec_key: str) -> float:
    """고정 가중치가 정의된 코덱의 상대 비트레이트 계수 반환, 미등록 값은 기본값"""
    return _STATIC_CODEC_WEIGHTS.get(codec_key, DEFAULT_CODEC_WEIGHT_FALLBACK)
