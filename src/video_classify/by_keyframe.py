from typing import Any, Dict, List, Callable, Tuple, Optional
from src.utils.video_prop import VideoProps
from src.utils.load_env import load_env


class VideoClassifierByKeyframe:
    _keyframe_interval: float = float(load_env("THRESHOLD_KEYFRAME_INTERVAL"))

    @staticmethod
    def print(video_prop_table: List[VideoProps], sort_key: Callable[[Dict[str, Any]], Tuple | list] | None, filename_maxlen: int) -> None:
        from src.utils.table_printer import TablePrinter

        table = []
        for vid in video_prop_table:
            table.append({
                "이름": vid.filename,
                "너비": vid.width,
                "높이": vid.height,
                "|": "|",
                "키프레임 간격": vid.keyframe_interval or -1.0,
                "이동경로": vid.moved_dirname or ""
            })

        TablePrinter.print(table, sort_key, filename_maxlen)

    @staticmethod
    def classified_dirname(vid: VideoProps) -> Optional[str]:
        if vid.keyframe_interval is not None and vid.keyframe_interval > VideoClassifierByKeyframe._keyframe_interval:
            return "_키프레임조정"

        return None