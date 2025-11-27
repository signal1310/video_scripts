from typing import Any, Dict, List, Callable, Tuple
from src.utils import bitrate_utils as bu
from src.utils.video_prop import VideoProps


class VideoClassifierByBitrate:
    @staticmethod
    def print(video_prop_table: List[VideoProps], sort_key: Callable[[Dict[str, Any]], Tuple | list] | None) -> None:
        from src.utils.table_printer import TablePrinter
        from src.utils.bitrate_utils import BASE_BITRATE
        
        table: list[Dict[str, Any]] = []
        total_filesize: float = 0
        total_reduced_filesize: float = 0
        for vid in video_prop_table:
            table.append({
                "이름": vid.filename,
                "너비": vid.width,
                "높이": vid.height,
                "|": "|",
                "비트레이트": (bitrate := vid.vid_kbps),
                "|": "|",
                "최적 가로": int((opt_r := bu.optimal_resolution_ratio(vid.width, vid.height)) * vid.width),
                "최적 세로": int(opt_r * vid.height),
                "최적 비트레이트": (optimal_val := bu.optimal_bitrate(vid.width, vid.height)),
                "비트레이트 비율": (bitrate_ratio := bitrate / optimal_val)
            })
            total_filesize += vid.vid_size_MB
            total_reduced_filesize += vid.vid_size_MB / bitrate_ratio \
                if (bu.is_overencoded_sd_video(bitrate, vid.width, vid.height) or 
                    bu.is_overbitrate_hd_video(bitrate, vid.width, vid.height)) \
                else vid.vid_size_MB
        
        TablePrinter.print(table, sort_key)
        print("\n==================================")
        print(f"목표 비트레이트: {BASE_BITRATE} kbps")
        print(f"총 용량: {total_filesize / 1024:.2f} GB")
        print(f"예상 절약시 총 용량: {total_reduced_filesize / 1024:.2f} GB")
        print(f"예상 절약 용량: {(total_filesize - total_reduced_filesize) / 1024:.2f} GB")

    @staticmethod
    def classify(video_prop_table: List[VideoProps], target_root_dir: str, exception_rules: List[Callable[[VideoProps], bool]]) -> None:
        from src.utils import filesys
        
        for vid in video_prop_table:
            if any(rule(vid) for rule in exception_rules):
                continue
            if bu.is_overencoded_sd_video(vid.vid_kbps, vid.width, vid.height):
                filesys.move_file(target_root_dir, vid.filename, "_비트레이트 최적화")
            elif bu.is_overbitrate_hd_video(vid.vid_kbps, vid.width, vid.height):
                filesys.move_file(target_root_dir, vid.filename, "_비트레이트 프리셋컷")