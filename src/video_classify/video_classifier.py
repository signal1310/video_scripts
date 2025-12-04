from typing import Any, Dict, List, Optional, Callable, Tuple
from src.utils.pred import Pred
from src.utils.table_cache import TableCache
from src.utils.video_prop import VideoProps
from src.video_classify.by_bitrate import VideoClassifierByBitrate
from src.video_classify.by_ratio import VideoClassifierByRatio
from src.video_classify.by_keyframe import VideoClassifierByKeyframe


desc: bool = True

t: Dict[str, str] = {
    "이름": "\n이름",
    "W": "\nW",
    "H": "\nH",
    "회전 각도": "회전\n각도",
    "비율": "\n비율",
    "비율 타입": "비율\n타입",
    "비율 차이": "비율\n차이",
    "b-rate": "\nb-rate",
    "최적 W": "최적\nW",
    "최적 H": "최적\nH",
    "최적 b-rate": "최적\nb-rate",
    "b-rate 비율": "b-rate\n비율",
    "키프레임 간격": "키프레임\n간격"
}


def col(x: str | int | float | List[int | str], order_by_desc: bool = False) -> str | int | float | List[int | str]:
    """
    테이블 열을 편하게 정렬할 수 있도록 도와주는 wrapper
    """
    if isinstance(x, str): 
        return _str_desc(x) if order_by_desc else x
    if isinstance(x, (int, float)): 
        return -x if order_by_desc else x
    if isinstance(x, list) and all(isinstance(e, (int, str)) for e in x): 
        return _windows_name_order_desc(x) if order_by_desc else x
    
    assert False, f"예상치 못한 타입의 매개변수가 제공되었습니다. 타입: {type(x)}"


def WindowsNameOrder(name: str) -> List[int | str]:
    """
    문자열을 윈도우 이름 정렬 방식으로 정렬
    """
    import re
    return [int(t) if t.isdigit() 
        else t.lower()
        for t in re.split(r"(\d+)", name)
    ]


def _windows_name_order_desc(parts: List[int | str]) -> List[int | str]:
    """
    윈도우 이름 정렬 방식으로 정렬된 문자열을 역정렬화
    """
    return [
        (-p if isinstance(p, int)
         else ''.join(chr(0x10FFFF - ord(c)) for c in p))
        for p in parts
    ]


def _str_desc(s: str) -> str:
    """
    문자열을 역정렬하기 위해 순서를 뒤집음
    """
    return "".join(chr(0x10FFFF - ord(c)) for c in s)


class VideoClassifier:
    _table_cache: Optional[TableCache] = None # 테이블 캐시 저장

    def __init__(self) -> None:
        from src.utils.load_env import load_env
        from src.utils.load_env import get_root_dir
        import os

        self._cache: Optional[TableCache] = VideoClassifier._table_cache
        self._root_dir = get_root_dir()
        if not os.path.isdir(self._root_dir):
            raise FileNotFoundError(f"지정된 루트 디렉터리를 찾을 수 없거나 유효하지 않습니다: '{self._root_dir}'")

        # 작업 경로가 달라지는 경우 캐시 초기화
        if self._cache and self._cache.root_dir != self._root_dir:
            self._cache = None
            VideoClassifier._table_cache = None

        self._filename_maxlen = int(load_env("TABULATE_FILENAME_MAXLEN"))
        self._keyframe_flag: bool = False # include_keyframe_interval에서 대기 중인 플래그
        self._pseudo_classifiy_mode: bool = False # pseudo_classify_mode에서 대기 중인 플래그
        self.exception_rules: List[Callable[[VideoProps], bool]] = []


    def _prepare_cache(self) -> None:
        """
        캐시를 준비하는 메서드
        캐시가 없으면 캐시 생성하거나, 키프레임 정보를 업데이트
        """
        # 캐시가 비어 있으면 캐시 생성
        if not self._cache:
            VideoClassifier._table_cache = TableCache(self._root_dir, self._keyframe_flag)
            self._cache = VideoClassifier._table_cache
            return
        
        # 캐시가 존재하는 경우 테이블 캐시의 키프레임 정보 업데이트
        self._cache.update_keyframe(self._keyframe_flag)
    

    def set_filename_max_length(self, max_length: int) -> None:
        """
        파일 이름이 출력되는 열의 너비를 지정
        """
        self._filename_maxlen = max_length


    def include_keyframe_interval(self, flag: bool = True) -> None:
        """
        영상 분석시 키프레임 정보 포함 여부 설정 (플래그만 저장)
        실제 분석은 classify나 print 호출 시점에 수행됨
        키프레임 분석은 많은 오버헤드가 있으므로 꼭 필요한 경우가 아니면 False가 권장됨
        """
        self._keyframe_flag = flag


    def pseudo_classify_mode(self, flag: bool = True) -> None:
        """
        실제 분류는 안 되고 분류 결과만 얻는 모드 설정
        """
        self._pseudo_classifiy_mode = flag


    def add_exception_rule(self, pred: Callable[[VideoProps], bool]) -> None:
        """
        분류 예외 기준을 등록
        입력으로 video_prop_table이 들어오고, 출력으로 boolean이 들어와야 함
        """
        self.exception_rules.append(pred)


    def classify(self, *, by: Pred) -> None:
        # TODO: 가분류 상태, 분류 상태에 대한 처리, classify만의 출력을 생성해야 됨
        """
        지정한 경로의 영상파일들을 조건에 따라 분류
        Pred.ALL은 동작하지 않음
        """
        from src.utils.filesys import move_file, file_exists_in
        if by == Pred.ALL: raise ValueError("[WARN] Pred.ALL 기준으로 분류할 수 없습니다.")

        self._prepare_cache()
        assert self._cache is not None, "Table cache was empty."

        # 분류 전략 선택
        classify_strategy: Callable[[VideoProps], Optional[str]] = {
            Pred.RATIO: VideoClassifierByRatio.classified_dirname,
            Pred.BITRATE: VideoClassifierByBitrate.classified_dirname,
            Pred.KEYFRAME: VideoClassifierByKeyframe.classified_dirname
        }[by]

        for vid in self._cache.data:
            if not file_exists_in(self._cache.root_dir, vid.filename): 
                continue
            if self._pseudo_classifiy_mode and vid.moved_dirname is not None:
                continue
            if any(rule(vid) for rule in self.exception_rules): 
                continue
            
            prev_moved_dirname: Optional[str] = vid.moved_dirname
            vid.moved_dirname = classify_strategy(vid)
            
            if not self._pseudo_classifiy_mode:
                if vid.moved_dirname: move_file(self._cache.root_dir, vid.filename, vid.moved_dirname)
                else: vid.moved_dirname = prev_moved_dirname


    def print(self, *, by: Pred, sanitize_emoji: bool, sort_key: Callable[[Dict[str, Any]], Tuple | list] | None = None) -> None:
        """
        지정한 경로의 영상파일들을 조건에 따라 출력
        """
        
        self._prepare_cache()
        assert self._cache is not None, "video_prop_table was None."

        {
            Pred.RATIO: VideoClassifierByRatio.print,
            Pred.BITRATE: VideoClassifierByBitrate.print,
            Pred.KEYFRAME: VideoClassifierByKeyframe.print,
            Pred.ALL: self._print_all_video_prop_table
        }[by](self._cache, sort_key, self._filename_maxlen, sanitize_emoji)
    

    def _print_all_video_prop_table(
            self,
            cache: TableCache,
            sort_key: Callable[[Dict[str, Any]], Tuple | list] | None, 
            filename_maxlen: int,
            sanitize_emoji: bool) -> None:
        """
        video_prop_table의 모든 요소 출력
        """
        from collections import defaultdict
        from src.utils.table_printer import TablePrinter
        from src.utils import bitrate_utils as bu
        from src.utils.filesys import file_exists_in

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
                "비율\n차이": vid.ratio.diff,
                "\u200b\n│": "│",
                "\nb-rate": (bitrate := vid.vid_kbps),
                "최적\nW": int((opt_r := bu.optimal_resolution_ratio(vid.width, vid.height)) * vid.width),
                "최적\nH": int(opt_r * vid.height),
                "최적\nb-rate": (optimal_val := bu.optimal_bitrate(vid.width, vid.height)),
                "b-rate\n비율": bitrate / optimal_val,
                "\u200b\u200b\n│": "│",
                "키프레임\n간격": vid.keyframe_interval or -1.0
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

        
    def unclassify_files(self, *, unmark_pseudo_classified_only: bool = False) -> None:
        """
        분류 또는 가분류 되어 있는 상태를 다시 분류되지 않은 상태로 만듦
        캐시가 없는 곳의 작동은 허용되지 않음
        """
        import os
        import shutil
        from src.utils.filesys import get_dirnames, file_exists_in

        if not self._cache:
            print(f"[WARN] \"{self._root_dir}\" 경로는 분류 또는 가분류 작업을 수행한 경로가 아닙니다.")
            return

        # 빠른 검색 위한 매핑
        prop_map: Dict[str, VideoProps] = {prop.filename: prop for prop in self._cache.data}

        # 가분류 상태만 제거
        if unmark_pseudo_classified_only:
            for prop in self._cache.data:
                if file_exists_in(self._root_dir, prop.filename):
                    prop.moved_dirname = None
            return

        # 가분류 상태의 논리적 초기화를 위해 moved_dirname 초기화
        for prop in self._cache.data: prop.moved_dirname = None

        # 가분류 상태였으면 실행되지 않는 루프
        root_dir: str = self._cache.root_dir
        for current_workdir, _, files in os.walk(root_dir):
            if current_workdir == root_dir: continue # 최상위 폴더는 건너뜀

            current_dirname: str = os.path.basename(current_workdir)
            for file in files:
                file_path = os.path.join(current_workdir, file)
                target_path = os.path.join(root_dir, file)

                # 동일 이름 충돌 발생시 그냥 남겨둠
                if file_exists_in(root_dir, file):
                    print(f"[WARN] '{file}' 파일이 이미 작업 경로에 존재하여 '{current_dirname}' 폴더에 유지됩니다.")
                    if file in prop_map: prop_map[file].moved_dirname = current_dirname

                # 파일 이동
                try: shutil.move(file_path, target_path)
                except OSError as e:
                    print(f"[Error] 파일 이동 실패: {file_path} -> {target_path} ({e})")
                    if file in prop_map: prop_map[file].moved_dirname = current_dirname

        # 빈 폴더 삭제(혹시나 동일 이름 충돌이 생긴경우 디렉터리 유지해야 됨)
        for dirname in get_dirnames(root_dir):
            dir_path: str = os.path.join(root_dir, dirname)
            try: os.rmdir(dir_path)
            except: pass