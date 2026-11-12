import os
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple
from dotenv import load_dotenv


# - H.264(1.00)와 동등한 체감 화질을 유지하기 위해 각 코덱이 필요로 하는 상대 비트레이트 계수(C_codec).
# - 계수 값이 작을수록(예: AV1=0.65, HEVC=0.80) 압축 효율이 높아 적은 비트레이트로 동일 화질을 구현함.
# - 계수 값이 클수록(예: MPEG-4=1.35, MPEG-2=1.60) 압축 효율이 낮아 더 높은 비트레이트를 요구함.
DEFAULT_CODEC_WEIGHTS: Dict[str, float] = {
    # 차세대 코덱 (H.264 대비 약 65% 비트레이트로 동등 화질 구현)
    "av1": 0.65,
    "av01": 0.65,
    # 현세대 고효율 코덱 (H.264 대비 약 80% 비트레이트로 동등 화질 구현)
    "hevc": 0.80,
    "h265": 0.80,
    "hev1": 0.80,
    "hvc1": 0.80,
    "vp9": 0.80,
    # 기준 표준 코덱 (H.264 = 1.00 기준점)
    "h264": 1.00,
    "avc": 1.00,
    "avc1": 1.00,
    # 구형 웹/네트워크 코덱 (동일 화질 기준 H.264 대비 125% 비트레이트 필요)
    "vp8": 1.25,
    "wmv3": 1.25,
    "vc1": 1.25,
    "theora": 1.25,
    "rv40": 1.25,
    # 레거시 코덱 (DivX, Xvid, FLV 등 동일 화질 기준 135% 비트레이트 필요)
    "mpeg4": 1.35,
    "msmpeg4v3": 1.35,
    "msmpeg4v2": 1.35,
    "flv1": 1.35,
    "h263": 1.35,
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


DEFAULT_CONFIGS: Dict[str, str] = {
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


def load_env(key: str, reload_dotenv: bool = False) -> str:
    """
    - 환경변수(.env)에서 지정한 설정 키값을 조회.
    - 값이 비어 있거나 존재하지 않으면 DEFAULT_CONFIGS에 정의된 기본값을 반환.
    """
    load_dotenv(dotenv_path=get_env_path(), override=reload_dotenv)
    val = os.environ.get(key)
    if val is not None and val.strip() != "":
        return val.strip().strip("'").strip('"')
    return DEFAULT_CONFIGS[key]


def get_rootdir_path() -> str:
    """
    - .rootdir 파일의 절대 경로를 반환.
    """
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".rootdir")
    )


def get_env_path() -> str:
    """
    - .env 파일의 절대 경로를 반환.
    """
    return os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    )


def set_root_dir(path: str) -> None:
    """
    - 프로젝트 루트의 .rootdir 파일에 지정된 분석 대상 작업 경로를 저장.
    """
    rootdir_path = get_rootdir_path()
    with open(rootdir_path, "w", encoding="utf-8") as f:
        f.write(path.strip() + "\n")


def save_env_values(updates: Dict[str, str], reload_dotenv: bool = True) -> None:
    """
    - .env 파일 내의 환경변수를 기존 주석 및 구조를 유지하며 갱신 또는 추가.
    - 현재 프로세스의 os.environ 및 dotenv 캐시 동기화.
    """
    env_path = get_env_path()
    lines = []
    if os.path.isfile(env_path):
        with open(env_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

    remaining_keys = dict(updates)
    new_lines = []

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
    """
    - 프로젝트 루트의 .rootdir 파일에서 분석 대상 작업 경로를 즉시 읽어옴.
    - 프로그램이나 주피터 커널을 재시작하지 않아도 파일 내용 변경이 즉각 반영됨.
    """
    rootdir_path = get_rootdir_path()
    try:
        with open(rootdir_path, "r", encoding="utf-8") as f:
            lines = [
                line.strip()
                for line in f
                if line.strip() and not line.strip().startswith("#")
            ]
            return lines[0] if lines else ""
    except FileNotFoundError:
        raise FileNotFoundError(f".rootdir 파일을 찾을 수 없음: {rootdir_path}")
    except Exception as e:
        raise RuntimeError(f".rootdir 파일 읽기 오류: {e}")


@dataclass
class ClassifierConfig:
    """
    - 고정 품질(CQ) 기반 비디오 분석 및 분류에 필요한 모든 임계값과 경로 설정을 관리.
    - 코덱별 상대 비트레이트 가중치(codec_weights)를 보관하여 해상도와 코덱에 부합하는 기준치를 제공.
    """
    base_width: int = 1280
    base_height: int = 720
    base_target_bitrate: int = 1500
    area_scaling_exponent: float = 0.82
    fps_scaling_exponent: float = 0.60
    base_fps: float = 30.0

    codec_weights: Dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_CODEC_WEIGHTS)
    )

    over_bitrate_ratio: float = 1.20
    low_bitrate_ratio: float = 0.80
    keyframe_threshold: float = 2.2

    small_footage_mb: float = 30.0
    small_footage_min_saved_mb: float = 3.0
    small_footage_cumulative_ratio: float = 0.15
    small_footage_cumulative_mb: float = 500.0
    keyframe_1s_overhead_rate: float = 0.06

    dir_standard_cq22: str = "_1_표준인코딩_CQ22"
    dir_low_quality_keyframe: str = "_2_저화질_키프레임교정"

    tabulate_floatfmt: str = ".3f"
    tabulate_filename_maxlen: int = 80

    def get_codec_weight(self, codec: str) -> float:
        """
        - 코덱 명칭을 정규화하여 H.264 대비 상대 비트레이트 가중치를 조회.
        - 목록에 등록되지 않은 코덱인 경우 기본값 1.00 반환.
        """
        key = (codec or "h264").lower().strip()
        return self.codec_weights.get(key, 1.00)

    @classmethod
    def from_env(cls, reload_dotenv: bool = True) -> "ClassifierConfig":
        """
        - .env 파일 및 환경변수로부터 최신 설정을 파싱하여 ClassifierConfig 객체를 생성.
        """
        weights = dict(DEFAULT_CODEC_WEIGHTS)
        w_av1 = float(load_env("CODEC_WEIGHT_AV1", reload_dotenv))
        w_hevc = float(load_env("CODEC_WEIGHT_HEVC_VP9", reload_dotenv))
        w_h264 = float(load_env("CODEC_WEIGHT_H264", reload_dotenv))
        w_legacy = float(load_env("CODEC_WEIGHT_LEGACY", reload_dotenv))

        for k in ("av1", "av01"):
            weights[k] = w_av1
        for k in ("hevc", "h265", "hev1", "hvc1", "vp9"):
            weights[k] = w_hevc
        for k in ("h264", "avc", "avc1"):
            weights[k] = w_h264
        for k in (
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
        ):
            weights[k] = w_legacy

        # 구버전 환경변수 키(DIR_LOW_QUALITY_KF)가 남아있는 경우에도 유연하게 대처.
        dir_low_kf = os.environ.get("DIR_LOW_QUALITY_KEYFRAME") or os.environ.get(
            "DIR_LOW_QUALITY_KF"
        ) or DEFAULT_CONFIGS["DIR_LOW_QUALITY_KEYFRAME"]

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
            dir_low_quality_keyframe=dir_low_kf,
            tabulate_floatfmt=load_env("TABULATE_FLOATFMT", reload_dotenv),
            tabulate_filename_maxlen=int(
                load_env("TABULATE_FILENAME_MAXLEN", reload_dotenv)
            ),
        )

    def to_env_dict(self) -> Dict[str, str]:
        """
        - ClassifierConfig 객체의 모든 속성을 .env 환경변수 형식의 딕셔너리로 변환.
        """
        return {
            "BASE_RESOLUTION_WIDTH": str(self.base_width),
            "BASE_RESOLUTION_HEIGHT": str(self.base_height),
            "BASE_TARGET_BITRATE": str(self.base_target_bitrate),
            "AREA_SCALING_EXPONENT": f"{self.area_scaling_exponent:.2f}",
            "FPS_SCALING_EXPONENT": f"{self.fps_scaling_exponent:.2f}",
            "BASE_FPS": f"{self.base_fps:.1f}",
            "CODEC_WEIGHT_AV1": f"{self.codec_weights.get('av1', 0.65):.2f}",
            "CODEC_WEIGHT_HEVC_VP9": f"{self.codec_weights.get('hevc', 0.80):.2f}",
            "CODEC_WEIGHT_H264": f"{self.codec_weights.get('h264', 1.00):.2f}",
            "CODEC_WEIGHT_LEGACY": f"{self.codec_weights.get('mpeg4', 1.30):.2f}",
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
    config: ClassifierConfig, root_dir: Optional[str] = None
) -> None:
    """
    - ClassifierConfig 객체의 모든 설정을 .env 파일에 일괄 영구 저장하고, root_dir가 전달된 경우 .rootdir도 동시 갱신.
    """
    save_env_values(config.to_env_dict(), reload_dotenv=True)
    if root_dir is not None:
        set_root_dir(root_dir)
