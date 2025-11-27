from typing import Any, Dict, List, Callable, Tuple
from src.utils import filesys
from src.utils.video_prop import VideoProps


class VideoClassifierByRatio:
    @staticmethod
    def print(video_prop_table: List[VideoProps], sort_key: Callable[[Dict[str, Any]], Tuple | list] | None) -> None:
        from src.utils.table_printer import TablePrinter

        table: List[Dict[str, Any]] = []
        for vid in video_prop_table:
            table.append({
                "이름": vid.filename,
                "너비": vid.width,
                "높이": vid.height,
                "|": "|",
                "회전각": vid.rotate_type,
                "비율": vid.ratio.real_value,
                "비율타입": vid.ratio.type,
                "비율차이": vid.ratio.diff
            })

        TablePrinter.print(table, sort_key)

    @staticmethod
    def classify(video_prop_table: List[VideoProps], target_root_dir: str, exception_rules: List[Callable[[VideoProps], bool]]) -> None:
        from src.utils import ratio
        from src.utils.load_env import load_env

        ratio_diff: float = float(load_env("THRESHOLD_RATIO_DIFF"))
        for vid in video_prop_table:
            if any(rule(vid) for rule in exception_rules):
                continue
            if vid.ratio.diff > ratio_diff:
                vid.exists = False
                filesys.move_file(target_root_dir, vid.filename, "기타해상도")
            else:
                vid.exists = False
                filesys.move_file(target_root_dir, vid.filename, ratio.ratio_map[vid.ratio.type]["dirname"])
