""".env·.rootdir 환경설정 로드/저장과 코덱 가중치 설정 관리 모듈"""

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv

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


def load_env(key: str, reload_dotenv: bool = False) -> str:
    """환경변수(.env)에서 지정한 설정 키값 조회

    값이 비어 있거나 미존재 시 DEFAULT_CONFIGS 기본값 반환
    """
    load_dotenv(dotenv_path=get_env_path(), override=reload_dotenv)
    val = os.environ.get(key)
    if val is not None and val.strip() != "":
        return val.strip().strip("'").strip('"')
    return DEFAULT_CONFIGS[key]


def get_rootdir_path() -> str:
    """.rootdir 파일의 절대 경로를 반환"""
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".rootdir")
    )


def get_env_path() -> str:
    """.env 파일의 절대 경로를 반환"""
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    )


def set_root_dir(path: str) -> None:
    """프로젝트 루트의 .rootdir 파일에 지정된 분석 대상 작업 경로를 저장"""
    rootdir_path = get_rootdir_path()
    with open(rootdir_path, "w", encoding="utf-8") as f:
        f.write(path.strip() + "\n")


def save_env_values(updates: dict[str, str], reload_dotenv: bool = True) -> None:
    """.env 파일의 환경변수를 기존 주석과 구조를 유지하며 갱신·추가

    현재 프로세스의 os.environ과 dotenv 캐시도 함께 동기화
    """
    env_path = get_env_path()
    lines: list[str] = []
    if os.path.isfile(env_path):
        with open(env_path, encoding="utf-8") as f:
            lines = f.readlines()

    remaining_keys = dict(updates)
    new_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining_keys:
                new_val = remaining_keys.pop(key)
                new_lines.append(f"{key}={new_val}\n")
                continue
        new_lines.append(line)

    if remaining_keys:
        if new_lines and not new_lines[-1].endswith("\n"):
            new_lines.append("\n")
        for key, val in remaining_keys.items():
            new_lines.append(f"{key}={val}\n")

    with open(env_path, "w", encoding="utf-8") as f:
        f.writelines(new_lines)

    for k, v in updates.items():
        os.environ[k] = str(v)

    if reload_dotenv:
        load_dotenv(env_path, override=True)


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
    def from_env(cls, reload_dotenv: bool = True) -> "ClassifierConfig":
        """.env 파일 및 환경변수로부터 최신 설정을 파싱하여 ClassifierConfig 객체를 생성"""
        group_values = {
            env_key: float(load_env(env_key, reload_dotenv))
            for env_key in CODEC_WEIGHT_GROUPS
        }
        weights = build_codec_weights_from_groups(group_values, DEFAULT_CODEC_WEIGHTS)

        return cls(
            base_width=int(load_env("BASE_RESOLUTION_WIDTH", reload_dotenv)),
            base_height=int(load_env("BASE_RESOLUTION_HEIGHT", reload_dotenv)),
            base_target_bitrate=int(load_env("BASE_TARGET_BITRATE", reload_dotenv)),
            area_scaling_exponent=float(
                load_env("AREA_SCALING_EXPONENT", reload_dotenv)
            ),
            fps_scaling_exponent=float(load_env("FPS_SCALING_EXPONENT", reload_dotenv)),
            base_fps=float(load_env("BASE_FPS", reload_dotenv)),
            codec_weights=weights,
            over_bitrate_ratio=float(
                load_env("THRESHOLD_OPTIMAL_BITRATE_RATE", reload_dotenv)
            ),
            low_bitrate_ratio=float(
                load_env("THRESHOLD_LOW_BITRATE_RATE", reload_dotenv)
            ),
            keyframe_threshold=float(
                load_env("THRESHOLD_KEYFRAME_INTERVAL", reload_dotenv)
            ),
            small_footage_mb=float(load_env("SMALL_FOOTAGE_SIZE_MB", reload_dotenv)),
            small_footage_min_saved_mb=float(
                load_env("SMALL_FOOTAGE_MIN_SAVED_MB", reload_dotenv)
            ),
            small_footage_cumulative_ratio=float(
                load_env("SMALL_FOOTAGE_CUMULATIVE_RATIO", reload_dotenv)
            ),
            small_footage_cumulative_mb=float(
                load_env("SMALL_FOOTAGE_CUMULATIVE_MB", reload_dotenv)
            ),
            keyframe_1s_overhead_rate=float(
                load_env("KEYFRAME_1S_OVERHEAD_RATE", reload_dotenv)
            ),
            dir_standard_cq22=load_env("DIR_STANDARD_CQ22", reload_dotenv),
            dir_low_quality_keyframe=load_env("DIR_LOW_QUALITY_KEYFRAME", reload_dotenv),
            tabulate_floatfmt=load_env("TABULATE_FLOATFMT", reload_dotenv),
            tabulate_filename_maxlen=int(
                load_env("TABULATE_FILENAME_MAXLEN", reload_dotenv)
            ),
        )

    def to_env_dict(self) -> dict[str, str]:
        """ClassifierConfig 객체의 모든 속성을 .env 환경변수 형식의 딕셔너리로 변환"""
        codec_env = {
            env_key: (
                f"{self.codec_weights.get(aliases[0], float(DEFAULT_CONFIGS[env_key])):.2f}"
            )
            for env_key, aliases in CODEC_WEIGHT_GROUPS.items()
        }
        return {
            "BASE_RESOLUTION_WIDTH": str(self.base_width),
            "BASE_RESOLUTION_HEIGHT": str(self.base_height),
            "BASE_TARGET_BITRATE": str(self.base_target_bitrate),
            "AREA_SCALING_EXPONENT": f"{self.area_scaling_exponent:.2f}",
            "FPS_SCALING_EXPONENT": f"{self.fps_scaling_exponent:.2f}",
            "BASE_FPS": f"{self.base_fps:.1f}",
            **codec_env,
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


def save_entire_config(
    config: ClassifierConfig, root_dir: str | None = None
) -> None:
    """ClassifierConfig 객체의 모든 설정을 .env 파일에 일괄 영구 저장하고, root_dir가 전달된 경우 .rootdir도 동시 갱신"""
    save_env_values(config.to_env_dict(), reload_dotenv=True)
    if root_dir is not None:
        set_root_dir(root_dir)
