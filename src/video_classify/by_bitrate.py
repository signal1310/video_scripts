from typing import Any, Dict, Callable, Tuple, Optional
from src.utils import bitrate_utils as bu
from src.utils.video_prop import VideoProps
from src.utils.table_cache import TableCache


class VideoClassifierByBitrate:
    @staticmethod
    def print(cache: TableCache,
              sort_key: Callable[[Dict[str, Any]], Tuple | list] | None, 
              filename_maxlen: int, 
              sanitize_emoji: bool) -> None:
        from src.utils.table_printer import TablePrinter
        from src.utils.bitrate_utils import BASE_BITRATE
        from src.utils.filesys import file_exists_in
        
        table: list[Dict[str, Any]] = []
        total_filesize: float = 0
        total_reduced_filesize: float = 0
        for vid in cache.data:
            table.append({
                "\n이름": vid.filename,
                "\nW": vid.width,
                "\nH": vid.height,
                "\n│": "│",
                "\nb-rate": (bitrate := vid.vid_kbps),
                "\u200b\n│": "│",
                "최적\nW": int((opt_r := bu.optimal_resolution_ratio(vid.width, vid.height)) * vid.width),
                "최적\nH": int(opt_r * vid.height),
                "최적\nb-rate": (optimal_val := bu.optimal_bitrate(vid.width, vid.height)),
                "b-rate\n비율": (bitrate_ratio := bitrate / optimal_val),
                "\n분류경로": vid.moved_dirname or "",
                "가분류\n여부": "가분류" if vid.moved_dirname and file_exists_in(cache.root_dir, vid.filename) else ""
            })
            total_filesize += vid.vid_size_MB
            total_reduced_filesize += vid.vid_size_MB / bitrate_ratio \
                if (bu.is_overencoded_sd_video(bitrate, vid.width, vid.height) or 
                    bu.is_overbitrate_hd_video(bitrate, vid.width, vid.height)) \
                else vid.vid_size_MB
        
        TablePrinter.print(table, sort_key, filename_maxlen, sanitize_emoji)
        print("\n==================================")
        print(f"목표 비트레이트: {BASE_BITRATE} kbps")
        print(f"총 용량: {total_filesize / 1024:.2f} GB")
        print(f"예상 절약시 총 용량: {total_reduced_filesize / 1024:.2f} GB")
        print(f"예상 절약 용량: {(total_filesize - total_reduced_filesize) / 1024:.2f} GB")

    @staticmethod
    def classified_dirname(vid: VideoProps) -> Optional[str]:
        if bu.is_overencoded_sd_video(vid.vid_kbps, vid.width, vid.height):
            return "_비트레이트 최적화"
        elif bu.is_overbitrate_hd_video(vid.vid_kbps, vid.width, vid.height):
            return "_비트레이트 프리셋컷"
        
        return None