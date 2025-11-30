from typing import Any, Dict, List, Callable, Tuple, Optional
from src.utils.video_prop import VideoProps
from src.utils.table_cache import TableCache
from src.utils import ratio
from src.utils.load_env import load_env


class VideoClassifierByRatio:
    _ratio_diff_cut: float = float(load_env("THRESHOLD_RATIO_DIFF"))

    @staticmethod
    def print(cache: TableCache,
              sort_key: Callable[[Dict[str, Any]], Tuple | list] | None, 
              filename_maxlen: int, 
              sanitize_emoji: bool) -> None:
        from src.utils.table_printer import TablePrinter
        from src.utils.filesys import file_exists_in

        table: List[Dict[str, Any]] = []
        for vid in cache.data:
            table.append({
                "\n이름": vid.filename,
                "\nW": vid.width,
                "\nH": vid.height,
                "\n│": "│",
                "회전\n각도": vid.rotate_type,
                "\n비율": vid.ratio.real_value,
                "비율\n타입": vid.ratio.type,
                "비율\n차이": vid.ratio.diff,
                "\n분류경로": vid.moved_dirname or "",
                "가분류\n여부": "가분류" if vid.moved_dirname and file_exists_in(cache.root_dir, vid.filename) else ""
            })

        TablePrinter.print(table, sort_key, filename_maxlen, sanitize_emoji)

    @staticmethod
    def classified_dirname(vid: VideoProps) -> Optional[str]:
        if vid.ratio.diff <= VideoClassifierByRatio._ratio_diff_cut: 
            return ratio.ratio_map[vid.ratio.type]["dirname"]
        else: 
            return "기타해상도"