# src/utils/bitrate_utils.py
from src.utils.load_env import load_env
from src.utils.ratio import ratio_map, get_closet_ratio

BASE_WIDTH: int = int(load_env('BASE_RESOLUTION_WIDTH'))
BASE_HEIGHT: int = int(load_env('BASE_RESOLUTION_HEIGHT'))
BASE_BITRATE: int = int(load_env('BASE_TARGET_BITRATE'))
OPTIMAL_BITRATE_RATE: float = float(load_env('THRESHOLD_OPTIMAL_BITRATE_RATE'))


def optimal_bitrate(width: int, height: int) -> int:
    """
    기준 해상도/비트레이트 기반으로 주어진 해상도(width*height)에 
    가장 잘 부합하는 최적 비트레이트 계산
    """
    return int(BASE_BITRATE * (width * height) / (BASE_WIDTH * BASE_HEIGHT))


def optimal_resolution_ratio(width: int, height: int) -> float:
    '''
    주어진 해상도(width*height)를 비율 보존 해상도 변경시 목표 비트레이트에 
    가장 가까운 해상도가 되게 하기 위한 가로 세로 변경 비율 계산
    '''
    # return (target_bitrate / bitrate) ** 0.5
    return (BASE_WIDTH * BASE_HEIGHT / (width * height)) ** 0.5


def is_overencoded_sd_video(bitrate: int, width: int, height: int) -> bool:
    '''
    현재 비트레이트(bitrate), 목표 비트레이트(target_bitrate), 최적 비트레이트(optimal_bitrate)
    세 값을 기반으로 현재 비디오가 과도한 비트레이트가 부여된 SD 동영상인지 확인
    '''
    optimal: int = optimal_bitrate(width, height)
    target_bitrate: int = ratio_map[get_closet_ratio(width / height).type]['target_bitrate']
    return (
        optimal < target_bitrate and
        (
            target_bitrate < bitrate or
            bitrate / optimal > OPTIMAL_BITRATE_RATE 
        )
    )

def is_overbitrate_hd_video(bitrate: int, width: int, height: int) -> bool:
    '''
    현재 비트레이트(bitrate), 목표 비트레이트(target_bitrate), 최적 비트레이트(optimal_bitrate)
    세 값을 기반으로 현재 비디오가 높은 비트레이트를 가진 HD 동영상인지 확인
    '''
    optimal: int = optimal_bitrate(width, height)
    target_bitrate: int = ratio_map[get_closet_ratio(width / height).type]['target_bitrate']
    return (
        target_bitrate <= optimal and
        target_bitrate <= bitrate
    )


