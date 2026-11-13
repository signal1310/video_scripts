""".rootdir 경로 조회·저장과 분석 임계값·코덱 가중치 설정 스키마 관리 모듈"""

import os
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from src.core.exceptions import (
    DomainFileNotFoundError,
    DomainRuntimeError,
    ErrorCode,
)

# - H.264(1.00)와 동등한 체감 화질을 유지하기 위해 각 코덱이 필요로 하는 상대 비트레이트 계수(C_codec)
# - 계수 값이 작을수록(예: AV1=0.65, HEVC=0.80) 압축 효율이 높아 적은 비트레이트로 동일 화질을 구현함
# - 계수 값이 클수록(예: MPEG-4=1.30, MPEG-2=1.60) 압축 효율이 낮아 더 높은 비트레이트를 요구함
DEFAULT_CODEC_WEIGHT_FALLBACK: float = 1.00

CODEC_WEIGHT_GROUPS: dict[str, tuple[str, ...]] = {
    # 차세대 코덱 (H.264 대비 약 65% 비트레이트로 동등 화질 구현)
    "CODEC_WEIGHT_AV1": ("av1", "av01"),
    # 현세대 고효율 코덱 (H.264 대비 약 80% 비트레이트로 동등 화질 구현)
    "CODEC_WEIGHT_HEVC_VP9": ("hevc", "h265", "hev1", "hvc1", "vp9"),
    # 기준 표준 코덱 (H.264 = 1.00 기준점)
    "CODEC_WEIGHT_H264": ("h264", "avc", "avc1"),
    # 구형 웹/네트워크 및 레거시 코덱 (DivX, Xvid, FLV, VP8 등 동일 화질 기준 130% 비트레이트 필요)
    "CODEC_WEIGHT_LEGACY": (
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

_STATIC_CODEC_WEIGHTS: dict[str, float] = {
    # 고전 코덱 (DVD, 초기 WMV 규격 등 동일 화질 기준 160% 비트레이트 필요)
    "mpeg2video": 1.60,
    "mpeg1video": 1.60,
    "wmv2": 1.60,
    "wmv1": 1.60,
    # 프레임 내 독립 압축 방식의 편집용 코덱
    "mjpeg": 2.50,
    "prores": 2.50,
    "dnxhd": 2.50,
}


DEFAULT_CONFIGS: dict[str, str] = {
    "BASE_RESOLUTION_WIDTH": "1280",
    "BASE_RESOLUTION_HEIGHT": "720",
    "BASE_TARGET_BITRATE": "1500",
    "AREA_SCALING_EXPONENT": "0.82",
    "FPS_SCALING_EXPONENT": "0.60",
    "BASE_FPS": "30.0",
    "CODEC_WEIGHT_AV1": "0.65",
    "CODEC_WEIGHT_HEVC_VP9": "0.80",
    "CODEC_WEIGHT_H264": "1.00",
    "CODEC_WEIGHT_LEGACY": "1.30",
    "THRESHOLD_OPTIMAL_BITRATE_RATE": "1.2",
    "THRESHOLD_LOW_BITRATE_RATE": "0.8",
    "THRESHOLD_KEYFRAME_INTERVAL": "2.2",
    "SMALL_FOOTAGE_SIZE_MB": "30.0",
    "SMALL_FOOTAGE_MIN_SAVED_MB": "3.0",
    "SMALL_FOOTAGE_CUMULATIVE_RATIO": "0.15",
    "SMALL_FOOTAGE_CUMULATIVE_MB": "500.0",
    "KEYFRAME_1S_OVERHEAD_RATE": "0.06",
    "DIR_STANDARD_CQ22": "_1_표준인코딩_CQ22",
    "DIR_LOW_QUALITY_KEYFRAME": "_2_저화질_키프레임교정",
    "TABULATE_FLOATFMT": ".3f",
    "TABULATE_FILENAME_MAXLEN": "80",
}

DEFAULT_ROOT_DIR: str = ""
DEFAULT_ROOTDIR_TEMPLATE: str = (
    "# 분석 대상 비디오 작업 폴더 경로를 첫 줄에 입력하세요\n"
)

DEFAULT_SCAN_MODE: str = "fast"
VALID_SCAN_MODES: tuple[str, ...] = ("fast", "full")

DEFAULT_FILTER_SEARCH: str = ""
DEFAULT_FILTER_ZONE: str = "ALL"
VALID_FILTER_ZONES: tuple[str, ...] = ("ALL", "OVER", "OPTIMAL", "LOW")

DEFAULT_FILTER_TARGET: str = "ALL"
VALID_FILTER_TARGETS: tuple[str, ...] = ("ALL", "ENCODE_ONLY", "PASS_ONLY", "DEFECT_KF")

DEFAULT_THEME_MODE: str = "system"
VALID_THEME_MODES: tuple[str, ...] = ("system", "dark", "light")

DEFAULT_APP_STATE: dict[str, Any] = {
    "root_dir": DEFAULT_ROOT_DIR,
    "recent_dirs": [],
    "exception_tokens": [],
    "scan_mode": DEFAULT_SCAN_MODE,
    "filter_search": DEFAULT_FILTER_SEARCH,
    "filter_zone": DEFAULT_FILTER_ZONE,
    "filter_target": DEFAULT_FILTER_TARGET,
    "theme_mode": DEFAULT_THEME_MODE,
    "window_geometry": "",
    "table_header_state": "",
}


def build_codec_weights_from_groups(
    group_values: dict[str, float],
    base_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    """코덱 그룹별 가중치 맵을 개별 코덱 alias 딕셔너리로 전개"""
    weights: dict[str, float] = dict(base_weights or _STATIC_CODEC_WEIGHTS)
    for env_key, aliases in CODEC_WEIGHT_GROUPS.items():
        if env_key in group_values:
            val = float(group_values[env_key])
            for codec in aliases:
                weights[codec] = val
    return weights


DEFAULT_CODEC_WEIGHTS: dict[str, float] = build_codec_weights_from_groups(
    {env_key: float(DEFAULT_CONFIGS[env_key]) for env_key in CODEC_WEIGHT_GROUPS}
)


def get_app_base_dir() -> str:
    """실행 파일(.exe) 또는 소스 프로젝트 루트 디렉터리 절대 경로 반환"""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def get_rootdir_path() -> str:
    """.rootdir 파일의 절대 경로 반환"""
    return os.path.join(get_app_base_dir(), ".rootdir")


def ensure_default_rootdir_file() -> str:
    """.rootdir 파일이 없으면 기본값 또는 안내 템플릿으로 자동 생성하고 경로를 반환"""
    rootdir_path = get_rootdir_path()
    if not os.path.exists(rootdir_path):
        parent_dir = os.path.dirname(rootdir_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        content = (
            f"{DEFAULT_ROOT_DIR.strip()}\n"
            if DEFAULT_ROOT_DIR.strip()
            else DEFAULT_ROOTDIR_TEMPLATE
        )
        with open(rootdir_path, "w", encoding="utf-8") as f:
            f.write(content)
    return rootdir_path


def set_root_dir(path: str) -> None:
    """앱 실행 루트의 .rootdir 파일에 지정된 분석 대상 작업 경로를 저장"""
    rootdir_path = get_rootdir_path()
    with open(rootdir_path, "w", encoding="utf-8") as f:
        f.write(path.strip() + "\n")


def get_root_dir() -> str:
    """프로젝트 루트의 .rootdir 파일에서 분석 대상 작업 경로 조회

    프로세스나 Jupyter 커널 재시작 없이 파일 변경 내용 즉시 반영
    """
    rootdir_path = get_rootdir_path()
    try:
        with open(rootdir_path, encoding="utf-8") as f:
            lines = [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
            return lines[0] if lines else ""
    except FileNotFoundError as e:
        raise DomainFileNotFoundError(
            f".rootdir 파일을 찾을 수 없습니다: {rootdir_path}",
            code=ErrorCode.ROOT_DIR_NOT_FOUND,
        ) from e
    except Exception as e:
        raise DomainRuntimeError(
            f".rootdir 파일을 읽는 중 오류가 발생했습니다: {e}",
            code=ErrorCode.ROOT_DIR_READ_ERROR,
        ) from e


@dataclass
class ClassifierConfig:
    """고정 품질(CQ) 기반 비디오 분석·분류용 임계값 및 경로 설정 관리

    코덱별 상대 비트레이트 가중치(codec_weights)를 함께 보관해 해상도·코덱별 기준치 제공
    """
    base_width: int = int(DEFAULT_CONFIGS["BASE_RESOLUTION_WIDTH"])
    base_height: int = int(DEFAULT_CONFIGS["BASE_RESOLUTION_HEIGHT"])
    base_target_bitrate: int = int(DEFAULT_CONFIGS["BASE_TARGET_BITRATE"])
    area_scaling_exponent: float = float(DEFAULT_CONFIGS["AREA_SCALING_EXPONENT"])
    fps_scaling_exponent: float = float(DEFAULT_CONFIGS["FPS_SCALING_EXPONENT"])
    base_fps: float = float(DEFAULT_CONFIGS["BASE_FPS"])

    codec_weights: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_CODEC_WEIGHTS)
    )

    over_bitrate_ratio: float = float(DEFAULT_CONFIGS["THRESHOLD_OPTIMAL_BITRATE_RATE"])
    low_bitrate_ratio: float = float(DEFAULT_CONFIGS["THRESHOLD_LOW_BITRATE_RATE"])
    keyframe_threshold: float = float(DEFAULT_CONFIGS["THRESHOLD_KEYFRAME_INTERVAL"])

    small_footage_mb: float = float(DEFAULT_CONFIGS["SMALL_FOOTAGE_SIZE_MB"])
    small_footage_min_saved_mb: float = float(
        DEFAULT_CONFIGS["SMALL_FOOTAGE_MIN_SAVED_MB"]
    )
    small_footage_cumulative_ratio: float = float(
        DEFAULT_CONFIGS["SMALL_FOOTAGE_CUMULATIVE_RATIO"]
    )
    small_footage_cumulative_mb: float = float(
        DEFAULT_CONFIGS["SMALL_FOOTAGE_CUMULATIVE_MB"]
    )
    keyframe_1s_overhead_rate: float = float(
        DEFAULT_CONFIGS["KEYFRAME_1S_OVERHEAD_RATE"]
    )

    dir_standard_cq22: str = DEFAULT_CONFIGS["DIR_STANDARD_CQ22"]
    dir_low_quality_keyframe: str = DEFAULT_CONFIGS["DIR_LOW_QUALITY_KEYFRAME"]

    tabulate_floatfmt: str = DEFAULT_CONFIGS["TABULATE_FLOATFMT"]
    tabulate_filename_maxlen: int = int(DEFAULT_CONFIGS["TABULATE_FILENAME_MAXLEN"])

    def get_codec_weight(self, codec: str) -> float:
        """코덱 명칭을 정규화해 H.264 대비 상대 비트레이트 가중치 반환

        목록에 미등록된 코덱에는 기본 가중치 1.00 적용
        """
        key = (codec or "h264").lower().strip()
        return self.codec_weights.get(key, DEFAULT_CODEC_WEIGHT_FALLBACK)

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> "ClassifierConfig":
        """문자열 키-값 매핑으로부터 기본값을 보완해 ClassifierConfig 객체를 생성"""

        def _get(key: str) -> str:
            raw = values.get(key)
            if raw is not None and str(raw).strip() != "":
                return str(raw).strip().strip("'").strip('"')
            return DEFAULT_CONFIGS[key]

        group_values = {
            env_key: float(_get(env_key)) for env_key in CODEC_WEIGHT_GROUPS
        }
        weights = build_codec_weights_from_groups(group_values, DEFAULT_CODEC_WEIGHTS)

        return cls(
            base_width=int(_get("BASE_RESOLUTION_WIDTH")),
            base_height=int(_get("BASE_RESOLUTION_HEIGHT")),
            base_target_bitrate=int(_get("BASE_TARGET_BITRATE")),
            area_scaling_exponent=float(_get("AREA_SCALING_EXPONENT")),
            fps_scaling_exponent=float(_get("FPS_SCALING_EXPONENT")),
            base_fps=float(_get("BASE_FPS")),
            codec_weights=weights,
            over_bitrate_ratio=float(_get("THRESHOLD_OPTIMAL_BITRATE_RATE")),
            low_bitrate_ratio=float(_get("THRESHOLD_LOW_BITRATE_RATE")),
            keyframe_threshold=float(_get("THRESHOLD_KEYFRAME_INTERVAL")),
            small_footage_mb=float(_get("SMALL_FOOTAGE_SIZE_MB")),
            small_footage_min_saved_mb=float(_get("SMALL_FOOTAGE_MIN_SAVED_MB")),
            small_footage_cumulative_ratio=float(
                _get("SMALL_FOOTAGE_CUMULATIVE_RATIO")
            ),
            small_footage_cumulative_mb=float(_get("SMALL_FOOTAGE_CUMULATIVE_MB")),
            keyframe_1s_overhead_rate=float(_get("KEYFRAME_1S_OVERHEAD_RATE")),
            dir_standard_cq22=_get("DIR_STANDARD_CQ22"),
            dir_low_quality_keyframe=_get("DIR_LOW_QUALITY_KEYFRAME"),
            tabulate_floatfmt=_get("TABULATE_FLOATFMT"),
            tabulate_filename_maxlen=int(_get("TABULATE_FILENAME_MAXLEN")),
        )

    def to_dict(self) -> dict[str, str]:
        """ClassifierConfig 객체의 모든 속성을 설정 직렬화용 문자열 사전으로 변환"""
        codec_map = {
            cfg_key: (
                f"{self.codec_weights.get(aliases[0], float(DEFAULT_CONFIGS[cfg_key])):.2f}"
            )
            for cfg_key, aliases in CODEC_WEIGHT_GROUPS.items()
        }
        return {
            "BASE_RESOLUTION_WIDTH": str(self.base_width),
            "BASE_RESOLUTION_HEIGHT": str(self.base_height),
            "BASE_TARGET_BITRATE": str(self.base_target_bitrate),
            "AREA_SCALING_EXPONENT": f"{self.area_scaling_exponent:.2f}",
            "FPS_SCALING_EXPONENT": f"{self.fps_scaling_exponent:.2f}",
            "BASE_FPS": f"{self.base_fps:.1f}",
            **codec_map,
            "THRESHOLD_OPTIMAL_BITRATE_RATE": f"{self.over_bitrate_ratio:.2f}",
            "THRESHOLD_LOW_BITRATE_RATE": f"{self.low_bitrate_ratio:.2f}",
            "THRESHOLD_KEYFRAME_INTERVAL": f"{self.keyframe_threshold:.1f}",
            "SMALL_FOOTAGE_SIZE_MB": f"{self.small_footage_mb:.1f}",
            "SMALL_FOOTAGE_MIN_SAVED_MB": f"{self.small_footage_min_saved_mb:.1f}",
            "SMALL_FOOTAGE_CUMULATIVE_RATIO": f"{self.small_footage_cumulative_ratio:.2f}",
            "SMALL_FOOTAGE_CUMULATIVE_MB": f"{self.small_footage_cumulative_mb:.1f}",
            "KEYFRAME_1S_OVERHEAD_RATE": f"{self.keyframe_1s_overhead_rate:.2f}",
            "DIR_STANDARD_CQ22": self.dir_standard_cq22,
            "DIR_LOW_QUALITY_KEYFRAME": self.dir_low_quality_keyframe,
            "TABULATE_FLOATFMT": self.tabulate_floatfmt,
            "TABULATE_FILENAME_MAXLEN": str(self.tabulate_filename_maxlen),
        }
