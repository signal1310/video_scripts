from typing import Any, Dict, List, Callable, Tuple, Optional
from src.utils.video_prop import VideoProps
from src.utils import ratio
from src.utils.load_env import load_env


class VideoClassifierByRatio:
    _ratio_diff_cut: float = float(load_env("THRESHOLD_RATIO_DIFF"))

    @staticmethod
    def print(video_prop_table: List[VideoProps], sort_key: Callable[[Dict[str, Any]], Tuple | list] | None, filename_maxlen: int) -> None:
        from src.utils.table_printer import TablePrinter

        table: List[Dict[str, Any]] = []
        for vid in video_prop_table:
            table.append({
                "\n이름": vid.filename,
                "\nW": vid.width,
                "\nH": vid.height,
                "\n│": "│",
                "회전\n각도": vid.rotate_type,
                "\n비율": vid.ratio.real_value,
                "비율\n타입": vid.ratio.type,
                "비율\n차이": vid.ratio.diff,
                "이동\n경로": vid.moved_dirname or ""
            })

        TablePrinter.print(table, sort_key, filename_maxlen)

    @staticmethod
    def classified_dirname(vid: VideoProps) -> Optional[str]:
        if vid.ratio.diff <= VideoClassifierByRatio._ratio_diff_cut: 
            return ratio.ratio_map[vid.ratio.type]["dirname"]
        else: 
            return "기타해상도"