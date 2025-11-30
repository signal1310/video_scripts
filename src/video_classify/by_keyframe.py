from typing import Any, Dict, Callable, Tuple, Optional
from src.utils.video_prop import VideoProps
from src.utils.table_cache import TableCache
from src.utils.load_env import load_env


class VideoClassifierByKeyframe:
    _keyframe_interval: float = float(load_env("THRESHOLD_KEYFRAME_INTERVAL"))

    @staticmethod
    def print(cache: TableCache,
              sort_key: Callable[[Dict[str, Any]], Tuple | list] | None, 
              filename_maxlen: int, 
              sanitize_emoji: bool) -> None:
        from src.utils.table_printer import TablePrinter
        from src.utils.filesys import file_exists_in

        table = []
        for vid in cache.data:
            table.append({
                "\n이름": vid.filename,
                "\nW": vid.width,
                "\nH": vid.height,
                "\n│": "│",
                "키프레임\n간격": vid.keyframe_interval or -1.0,
                "\n분류경로": vid.moved_dirname or "",
                "가분류\n여부": "가분류" if vid.moved_dirname and file_exists_in(cache.root_dir, vid.filename) else ""
            })

        TablePrinter.print(table, sort_key, filename_maxlen, sanitize_emoji)

    @staticmethod
    def classified_dirname(vid: VideoProps) -> Optional[str]:
        if vid.keyframe_interval is not None and vid.keyframe_interval > VideoClassifierByKeyframe._keyframe_interval:
            return "_키프레임조정"

        return None