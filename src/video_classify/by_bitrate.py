from src.utils import filesys
from src.utils import bitrate_utils as bu


class VideoClassifierByBitrate:
    @staticmethod
    def print(video_prop_table, sort_key):
        from src.utils.table_printer import TablePrinter
        from src.utils.bitrate_utils import BASE_BITRATE
        
        table = []
        total_filesize = 0
        total_reduced_filesize = 0
        for vid in video_prop_table:
            current_val = vid['vid_kbps']
            width = vid['width']
            height = vid['height']
            optimal_val = bu.optimal_bitrate(width, height)
            optimal_width = bu.optimal_resolution_ratio(width, height) * width
            optimal_height = bu.optimal_resolution_ratio(width, height) * height
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
                if (bu.is_overencoded_sd_video(current_val, width, height) or 
                    bu.is_overbitrate_hd_video(current_val, width, height)) \
                else vid['vid_size_MB']
            table.append(t)
        
        TablePrinter.print(table, sort_key)
        print('\n==================================')
        print(f"목표 비트레이트: {BASE_BITRATE} kbps")
        print(f"총 용량: {total_filesize / 1024:.2f} GB")
        print(f"예상 절약시 총 용량: {total_reduced_filesize / 1024:.2f} GB")
        print(f"예상 절약 용량: {(total_filesize - total_reduced_filesize) / 1024:.2f} GB")

    @staticmethod
    def classify(video_prop_table, target_dir_root, exception_rules):
        for vid in video_prop_table:
            if any(rule(vid) for rule in exception_rules):
                continue
            if bu.is_overencoded_sd_video(vid['vid_kbps'], vid['width'], vid['height']):
                filesys.move_file(target_dir_root, vid['filename'], '_비트레이트 최적화')
            elif bu.is_overbitrate_hd_video(vid['vid_kbps'], vid['width'], vid['height']):
                filesys.move_file(target_dir_root, vid['filename'], '_비트레이트 프리셋컷')