"""재인코딩 인코더 정의의 단일 기준 모듈: 이름, 코덱 계열, 선택지 라벨, 품질 옵션, 기본 인자, 레벨 상한

- 설정 선택지(config), 계열 매핑(config), FFmpeg 인자 생성(ffmpeg_command), 레벨 상한 계산이 이 표를 공유
- 사용 불가(available=False) 인코더는 선택지에 나타나지 않으며, 저장된 설정 값도 기본 인코더로 대체됨
"""

import math
from dataclasses import dataclass

NVENC_BASE_ARGS: tuple[str, ...] = ("-preset:v", "p4", "-tune:v", "hq", "-rc", "vbr", "-level:v", "auto")
X265_BASE_ARGS: tuple[str, ...] = ("-preset:v", "medium")
X264_BASE_ARGS: tuple[str, ...] = ("-preset:v", "medium")
DEFAULT_ENCODER_NAME: str = "hevc_nvenc"

MACROBLOCK_SIZE_PX: int = 16
# - (레벨 식별자 ffprobe 표기, MaxLumaPs, MaxLumaSr, Main 프로파일 MaxBR kbps)
#   NVENC 자동 레벨 선택을 1초 합성 클립으로 확인한 결과(4K60 -> 5.1, 1080p60 -> 4.1 등)와 일치
HEVC_LEVEL_LIMITS: tuple[tuple[int, int, int, int], ...] = (
    (31, 983_040, 33_177_600, 10_000),
    (40, 2_228_224, 66_846_720, 12_000),
    (41, 2_228_224, 133_693_440, 20_000),
    (50, 8_912_896, 267_386_880, 25_000),
    (51, 8_912_896, 534_773_760, 40_000),
    (52, 8_912_896, 1_069_547_520, 60_000),
    (60, 35_651_584, 4_278_190_080, 60_000),
)
# - (레벨 식별자, MaxFS(매크로블록), MaxMBPS, High 프로파일 MaxBR kbps)
H264_LEVEL_LIMITS: tuple[tuple[int, int, int, int], ...] = (
    (31, 3_600, 108_000, 17_500),
    (40, 8_192, 245_760, 25_000),
    (41, 8_192, 245_760, 62_500),
    (42, 8_704, 522_240, 62_500),
    (50, 22_080, 589_824, 168_750),
    (51, 36_864, 983_040, 300_000),
    (52, 36_864, 2_073_600, 300_000),
)


@dataclass(frozen=True, slots=True)
class EncoderSpec:
    """인코더 한 종의 정의. quality_option이 None이면 인자 구성이 미확정이라 선택지에서 제외"""

    name: str
    family: str
    label: str
    quality_option: str | None
    quality_min: float
    quality_max: float
    integer_scale: bool
    is_nvenc: bool
    base_args: tuple[str, ...]
    profile: str | None
    stage: int
    available: bool


ENCODER_SPECS: tuple[EncoderSpec, ...] = (
    EncoderSpec(
        "hevc_nvenc", "hevc", "HEVC (NVENC GPU)", "-cq:v", 0.0, 51.0, False, True,
        NVENC_BASE_ARGS, "main", 3, True,
    ),
    EncoderSpec(
        "libx265", "hevc", "HEVC (CPU x265)", "-crf:v", 0.0, 51.0, False, False,
        X265_BASE_ARGS, None, 3, True,
    ),
    EncoderSpec(
        "h264_nvenc", "h264", "H.264 (NVENC GPU)", "-cq:v", 0.0, 51.0, False, True,
        NVENC_BASE_ARGS, "high", 3, True,
    ),
    EncoderSpec(
        "libx264", "h264", "H.264 (CPU x264)", "-crf:v", 0.0, 51.0, False, False,
        X264_BASE_ARGS, None, 3, True,
    ),
    # - 아래 항목은 장치 확보 전이라 인자 구성을 확정하지 못함(3단계 보고 후 활성화)
    EncoderSpec(
        "hevc_qsv", "hevc", "HEVC (Intel Quick Sync)", "-global_quality", 1.0, 51.0, True, False,
        (), None, 3, False,
    ),
    EncoderSpec(
        "hevc_amf", "hevc", "HEVC (AMD AMF)", None, 0.0, 51.0, True, False,
        (), None, 3, False,
    ),
    # - AV1 계열은 4단계 척도 매핑 후 5단계에서 활성화(결정 22)
    EncoderSpec(
        "av1_nvenc", "av1", "AV1 (NVENC GPU, RTX 40+)", "-cq:v", 0.0, 63.0, False, True,
        NVENC_BASE_ARGS, "main", 5, False,
    ),
    EncoderSpec(
        "libsvtav1", "av1", "AV1 (CPU SVT-AV1)", "-crf", 0.0, 63.0, True, False,
        (), None, 5, False,
    ),
)

ENCODER_BY_NAME: dict[str, EncoderSpec] = {spec.name: spec for spec in ENCODER_SPECS}
_ENCODE_CODEC_LABELS: dict[str, str] = {spec.name: spec.label for spec in ENCODER_SPECS}
# - 목표 인코더가 속한 코덱 그룹 별칭으로, CODEC_WEIGHT_GROUPS의 키와 동일한 이름 사용. 미활성 인코더도 계열은 판정에 필요
ENCODE_CODEC_FAMILY: dict[str, str] = {spec.name: spec.family for spec in ENCODER_SPECS}
ENCODE_REFERENCE_FAMILY: str = "h264"
ENCODE_FAMILY_LABELS: dict[str, str] = {"hevc": "HEVC", "h264": "H.264", "av1": "AV1"}
# - 계열별로 코덱을 표준으로 담는 컨테이너 확장자(결정 8). None은 아직 확인하지 않은 계열(AV1은 5단계에서 확인)
FAMILY_STANDARD_CONTAINERS: dict[str, frozenset[str] | None] = {
    "h264": frozenset({"mp4", "mov", "mkv", "ts", "flv", "avi"}),
    "hevc": frozenset({"mp4", "mov", "mkv", "ts"}),
    "av1": None,
}
# - 재인코딩 태그(결정 28)를 기록할 수 있는 컨테이너 확장자
TAG_RECORDABLE_CONTAINERS: frozenset[str] = frozenset({"mp4", "mov", "mkv"})
_PENDING_CONTAINER_REASON: str = "5단계에서 확인"


def keep_container_block_reason(ext: str, family: str) -> str | None:
    """원본 형식 유지 정책에서 확장자 원본이 MP4로 바뀌는 이유를 돌려주며, 원본 형식을 유지하면 None

    - 코덱 계열을 표준으로 담지 못하는 확장자는 코덱 사유, 태그를 기록할 수 없는 확장자는 태그 사유
    """
    standard = FAMILY_STANDARD_CONTAINERS.get(family)
    if standard is None:
        return _PENDING_CONTAINER_REASON
    if ext not in standard:
        return f"{ENCODE_FAMILY_LABELS.get(family, family)} 코덱을 표준으로 담지 못함"
    if ext not in TAG_RECORDABLE_CONTAINERS:
        return "재인코딩 태그를 기록할 수 없음"
    return None


def keep_container_exts(family: str) -> frozenset[str] | None:
    """원본 형식을 유지하는 확장자 집합(계열 표준과 태그 기록 가능 형식의 교집합). 확인하지 않은 계열은 None"""
    standard = FAMILY_STANDARD_CONTAINERS.get(family)
    if standard is None:
        return None
    return standard & TAG_RECORDABLE_CONTAINERS


def get_encoder_spec(name: str) -> EncoderSpec | None:
    """인코더 이름에 대응하는 정의 반환, 미등록 이름은 None"""
    return ENCODER_BY_NAME.get(name)


def get_encode_codec_label(codec: str) -> str:
    """목표 인코딩 코덱 코드에 대응하는 사용자 표시 라벨 반환"""
    return _ENCODE_CODEC_LABELS.get(codec, codec)


def get_encode_codec_family(codec: str) -> str:
    """목표 인코딩 코덱이 속한 코덱 그룹 별칭 반환, 미등록 값은 기준 코덱(H.264)으로 간주"""
    return ENCODE_CODEC_FAMILY.get(codec, ENCODE_REFERENCE_FAMILY)


def _mb_count(width: int, height: int) -> int:
    """매크로블록 단위 프레임 크기(MaxFS 비교용) 계산"""
    return math.ceil(width / MACROBLOCK_SIZE_PX) * math.ceil(height / MACROBLOCK_SIZE_PX)


def _hevc_level_cap_kbps(width: int, height: int, fps: float) -> int | None:
    pixels = width * height
    luma_rate = pixels * fps
    for _level, max_ps, max_sr, max_br in HEVC_LEVEL_LIMITS:
        if pixels <= max_ps and luma_rate <= max_sr:
            return max_br
    return None


def _h264_level_cap_kbps(width: int, height: int, fps: float) -> int | None:
    frame_mbs = _mb_count(width, height)
    mb_rate = frame_mbs * fps
    for _level, max_fs, max_mbps, max_br in H264_LEVEL_LIMITS:
        if frame_mbs <= max_fs and mb_rate <= max_mbps:
            return max_br
    return None


def resolve_level_cap_kbps(encoder: str, width: int, height: int, fps: float) -> int | None:
    """NVENC 자동 레벨(-level:v auto)이 고르는 레벨의 최대 비트레이트(kbps) 반환

    - 명시한 최대 비트레이트(-maxrate)가 없을 때 NVENC가 적용하는 상한이며, 해당 레벨이 없으면 None
    - NVENC가 아닌 인코더는 레벨 상한을 적용하지 않으므로 항상 None
    """
    if width <= 0 or height <= 0 or fps <= 0:
        return None
    spec = get_encoder_spec(encoder)
    if spec is None or not spec.is_nvenc:
        return None
    if spec.family == "hevc":
        return _hevc_level_cap_kbps(width, height, fps)
    if spec.family == "h264":
        return _h264_level_cap_kbps(width, height, fps)
    return None
