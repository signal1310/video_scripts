from src.utils import filesys
from src.utils.load_env import load_env
from src.utils.ratio import ratio_map, get_closet_ratio

# 1280*720 기준 목표 비트레이트값(kbps)
target_bitrate = ratio_map['16-9']['target_bitrate']
optimal_bitrate_rate = float(load_env('THRESHOLD_OPTIMAL_BITRATE_RATE'))

def optimal_bitrate(width, height):
    '''
    주어진 해상도(width*height)에 가장 잘 부합하는 최적 비트레이트 계산
    '''
    return target_bitrate * (width * height) / (1280 * 720)


def optimal_resolution_ratio(width, height):
    '''
    주어진 해상도(width*height)를 비율 보존 해상도 변경시 목표 비트레이트에 
    가장 가까운 해상도가 되게 하기 위한 가로 세로 변경 비율 계산
    '''
    # return (target_bitrate / bitrate) ** 0.5
    return (1280 * 720 / (width * height)) ** 0.5


def is_overencoded_sd_video(bitrate, width, height):
    '''
    현재 비트레이트(bitrate), 목표 비트레이트(target_bitrate), 최적 비트레이트(optimal_bitrate)
    세 값을 기반으로 현재 비디오가 과도한 비트레이트가 부여된 SD 동영상인지 확인
    '''
    optimal = optimal_bitrate(width, height)
    target_bitrate = ratio_map[get_closet_ratio(width / height).type]['target_bitrate']
    return (
        optimal < target_bitrate and
        (
            target_bitrate < bitrate or
            bitrate / optimal > optimal_bitrate_rate 
        )
    )

def is_overbitrate_hd_video(bitrate, width, height):
    '''
    현재 비트레이트(bitrate), 목표 비트레이트(target_bitrate), 최적 비트레이트(optimal_bitrate)
    세 값을 기반으로 현재 비디오가 높은 비트레이트를 가진 HD 동영상인지 확인
    '''
    optimal = optimal_bitrate(width, height)
    target_bitrate = ratio_map[get_closet_ratio(width / height).type]['target_bitrate']
    return (
        target_bitrate <= optimal and
        target_bitrate <= bitrate
    )


class VideoClassifierByBitrate:
    @staticmethod
    def print(video_prop_table, sort_key):
        from src.utils.table_printer import TablePrinter
        
        table = []
        total_filesize = 0
        total_reduced_filesize = 0
        for vid in video_prop_table:
            current_val = vid['vid_kbps']
            width = vid['width']
            height = vid['height']
            optimal_val = optimal_bitrate(width, height)
            optimal_width = optimal_resolution_ratio(width, height) * width
            optimal_height = optimal_resolution_ratio(width, height) * height
            t = {
                "filename": vid['filename'],
                "가로": width,
                "세로": height,
                "영상 비트레이트": current_val,
                "|": "|",
                "최적 가로": int(optimal_width),
                "최적 세로": int(optimal_height),
                "최적 비트레이트": optimal_val,
                "비트레이트 비율": current_val / optimal_val
            }
            total_filesize += vid['vid_size_MB']
            total_reduced_filesize += vid['vid_size_MB'] / t['비트레이트 비율'] \
                if (is_overencoded_sd_video(current_val, width, height) or 
                    is_overbitrate_hd_video(current_val, width, height)) \
                else vid['vid_size_MB']
            table.append(t)
        
        TablePrinter.print(table, sort_key)
        print('\n==================================')
        print(f"목표 비트레이트: {target_bitrate} kbps")
        print(f"총 용량: {total_filesize / 1024:.2f} GB")
        print(f"예상 절약시 총 용량: {total_reduced_filesize / 1024:.2f} GB")
        print(f"예상 절약 용량: {(total_filesize - total_reduced_filesize) / 1024:.2f} GB")

    @staticmethod
    def classify(video_prop_table, target_dir_root):
        for vid in video_prop_table:
            if is_overencoded_sd_video(vid['vid_kbps'], vid['width'], vid['height']):
                filesys.move_file(target_dir_root, vid['filename'], '_비트레이트 최적화')
            elif is_overbitrate_hd_video(vid['vid_kbps'], vid['width'], vid['height']):
                filesys.move_file(target_dir_root, vid['filename'], '_비트레이트 프리셋컷')