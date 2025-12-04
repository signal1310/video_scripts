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
        from collections import defaultdict

        tables: Dict[Tuple[str, bool], List[Dict[str, Any]]] = defaultdict(list)
        for vid in cache.data:
            pseudo_classified: bool = vid.moved_dirname is not None and file_exists_in(cache.root_dir, vid.filename)
            tables[(vid.moved_dirname or "", pseudo_classified)].append({
                "\n이름": vid.filename,
                "\nW": vid.width,
                "\nH": vid.height,
                "\n│": "│",
                "회전\n각도": vid.rotate_type,
                "\n비율": vid.ratio.real_value,
                "비율\n타입": vid.ratio.type,
                "비율\n차이": vid.ratio.diff
            })

        sorted_items = sorted(tables.items(), key=lambda i: (bool(i[0][0]), i[0][1], i[0][0]))
        for (dirname, pseudo_classified), table in sorted_items:
            if not dirname:
                print(f"\n\n[ 분류되지 않은 비디오 목록 - 총 {len(table)}개 ]")
            elif pseudo_classified:
                print(f"\n\n[ '{dirname}' 경로로 모의 분류된 비디오 목록 (실제로 분류되지 않음) - 총 {len(table)}개 ]")
            else:
                print(f"\n\n[ '{dirname}' 경로로 분류된 비디오 목록 - 총 {len(table)}개 ]")
            TablePrinter.print(table, sort_key, filename_maxlen, sanitize_emoji)

    @staticmethod
    def classified_dirname(vid: VideoProps) -> Optional[str]:
        if vid.ratio.diff <= VideoClassifierByRatio._ratio_diff_cut: 
            return ratio.ratio_map[vid.ratio.type]["dirname"]
        else: 
            return "기타해상도"