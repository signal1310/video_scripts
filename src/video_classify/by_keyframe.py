from typing import Any, Dict, List, Callable, Tuple
from src.utils import filesys
from src.utils.video_prop import VideoProps

class VideoClassifierByKeyframe:
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
                "키프레임 간격": vid.keyframe_interval
            })

        TablePrinter.print(table, sort_key, filename_maxlen)

    @staticmethod
    def classify(video_prop_table: List[VideoProps], target_root_dir: str, exception_rules: List[Callable[[VideoProps], bool]]) -> None:
        from src.utils.load_env import load_env

        keyframe_interval = float(load_env("THRESHOLD_KEYFRAME_INTERVAL"))
        for vid in video_prop_table:
            if any(rule(vid) for rule in exception_rules):
                continue
            if vid.keyframe_interval is None:
                print(f"[WARN] {vid.filename} 파일의 키프레임이 구해지지 않았으므로 분류가 생략됩니다.")
                continue
            if vid.keyframe_interval > keyframe_interval:
                vid.exists = False
                filesys.move_file(target_root_dir, vid.filename, "_키프레임조정")