from typing import Any, Dict, List, Optional, Callable, Tuple
from src.utils.video_prop import VideoProps
from enum import Enum

desc: bool = True

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


class Pred(Enum):
    RATIO = 1
    BITRATE = 2
    KEYFRAME = 3
    ALL = 4


class VideoClassifier:
    # 클래스 변수: 모든 인스턴스가 공유 (프로그램 실행 중 계속 유지)
    _video_prop_table: Optional[List[VideoProps]] = None  # 분석 결과 데이터
    _target_root_dir: Optional[str] = None  # 이전 경로 버퍼
    _prev_keyframe_flag: bool = False  # 이전 키프레임 플래그 버퍼

    def __init__(self) -> None:
        from src.utils.load_env import load_env
        from src.utils.load_env import get_root_dir
        import os

        self._filename_maxlen = int(load_env("TABULATE_FILENAME_MAXLEN"))
        self._keyframe_flag: bool = False # include_keyframe_interval에서 대기 중인 플래그
        self.target_root_dir: str = get_root_dir()
        # 경로가 유효하고 이전 값과 다른 경우에만 처리
        if (isdir := os.path.isdir(self.target_root_dir)) and VideoClassifier._target_root_dir != self.target_root_dir:
            VideoClassifier._video_prop_table = None  # 경로 변경 시 캐시 무효화
            VideoClassifier._prev_keyframe_flag = False
            VideoClassifier._target_root_dir = self.target_root_dir
        
        if not isdir: print(f"[WARN] \"{self.target_root_dir}\" 경로는 유효한 경로가 아닙니다.")

        self.exception_rules: List[Callable[[VideoProps], bool]] = []

    def _update_video_prop_table(self) -> None:
        """
        버퍼를 확인하고, 변경이 필요한 경우에만 get_video_prop_table 호출
        _pending_keyframe_flag를 직접 참조하여 분석 수행
        - 처음 호출: 항상 실행
        - 플래그 True로 상향 변경: 다시 호출 (키프레임 데이터 필요)
        - 플래그 False로 하향 변경: 호출 안 함 (기존 캐시 재사용 가능)
        """
        from src.utils.video_prop import get_video_prop_table, include_keyframe_at

        # keyframe 플래그가 False→True로 상향 변경된 경우
        keyframe_flag_raised: bool = self._keyframe_flag and not VideoClassifier._prev_keyframe_flag

        if not VideoClassifier._video_prop_table:
            # 처음 호출 캐시가 초기화되면 prop table 재생성
            VideoClassifier._video_prop_table = get_video_prop_table(self.target_root_dir, self._keyframe_flag)
        elif keyframe_flag_raised:
            # prop table 존재하는데 keyframe이 False->True로 상향 변경되면 키프레임 포함시킴
            include_keyframe_at(VideoClassifier._video_prop_table, self.target_root_dir)

        VideoClassifier._prev_keyframe_flag = self._keyframe_flag
    
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

    def add_exception_rule(self, pred: Callable[[VideoProps], bool]) -> None:
        """
        분류 예외 기준을 등록
        입력으로 video_prop_table이 들어오고, 출력으로 boolean이 들어와야 함
        """
        self.exception_rules.append(pred)

    def classify(self, *, by: Pred) -> None:   
        """
        지정한 경로의 영상파일들을 조건에 따라 분류
        Pred.ALL은 동작하지 않음
        """
        from src.video_classify.by_bitrate import VideoClassifierByBitrate
        from src.video_classify.by_ratio import VideoClassifierByRatio
        from src.video_classify.by_keyframe import VideoClassifierByKeyframe

        self._update_video_prop_table()

        assert VideoClassifier._video_prop_table is not None, "video_prop_table was None."
        match by:
            case Pred.RATIO:
                VideoClassifierByRatio.classify(VideoClassifier._video_prop_table, self.target_root_dir, self.exception_rules)
            case Pred.BITRATE:
                VideoClassifierByBitrate.classify(VideoClassifier._video_prop_table, self.target_root_dir, self.exception_rules)
            case Pred.KEYFRAME:
                VideoClassifierByKeyframe.classify(VideoClassifier._video_prop_table, self.target_root_dir, self.exception_rules)

    def print(self, *, by: Pred, sort_key: Callable[[Dict[str, Any]], Tuple | list] | None = None) -> None:
        """
        지정한 경로의 영상파일들을 조건에 따라 출력
        """
        from src.video_classify.by_bitrate import VideoClassifierByBitrate
        from src.video_classify.by_ratio import VideoClassifierByRatio
        from src.video_classify.by_keyframe import VideoClassifierByKeyframe

        self._update_video_prop_table()

        assert VideoClassifier._video_prop_table is not None, "video_prop_table was None."
        match by:
            case Pred.RATIO:
                VideoClassifierByRatio.print(VideoClassifier._video_prop_table, sort_key, self._filename_maxlen)
            case Pred.BITRATE:
                VideoClassifierByBitrate.print(VideoClassifier._video_prop_table, sort_key, self._filename_maxlen)
            case Pred.KEYFRAME:
                VideoClassifierByKeyframe.print(VideoClassifier._video_prop_table, sort_key, self._filename_maxlen)
            case Pred.ALL:
                self._print_all_video_prop_table(VideoClassifier._video_prop_table, sort_key, self._filename_maxlen)
    
    def _print_all_video_prop_table(self, video_prop_table: List[VideoProps], sort_key: Callable[[Dict[str, Any]], Tuple | list] | None, filename_maxlen: int):
        """
        video_prop_table의 모든 요소 출력
        """
        from src.utils.table_printer import TablePrinter
        from src.utils import bitrate_utils as bu

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
                "비율차이": vid.ratio.diff,
                "| ": "|",
                "비트레이트": (bitrate := vid.vid_kbps),
                "최적 가로": int(bu.optimal_resolution_ratio(vid.width, vid.height) * vid.width),
                "최적 세로": int(bu.optimal_resolution_ratio(vid.width, vid.height) * vid.height),
                "최적 비트레이트": (optimal_val := bu.optimal_bitrate(vid.width, vid.height)),
                "비트레이트 비율": bitrate / optimal_val,
                "|  ": "|",
                "키프레임 간격": vid.keyframe_interval or -1.0
            })

        TablePrinter.print(table, sort_key, filename_maxlen)

    @staticmethod
    def unclassify_files() -> None:
        """
        지정한 경로의 모든 폴더의 각 파일들을 다시 하나로 모음
        """
        import os
        import shutil
        from src.utils.filesys import get_dirpaths
        from src.utils.load_env import get_root_dir

        if not os.path.isdir(target_root_dir := get_root_dir()):
            print(f"[WARN] \"{target_root_dir}\" 경로는 유효한 경로가 아닙니다.")
            return

        # 캐시 존재 + 동일 루트 → exists 복원하기
        use_cache_fix: bool = (
            target_root_dir == VideoClassifier._target_root_dir and
            VideoClassifier._video_prop_table is not None
        )

        # 효율적 탐색을 위한 dict 생성
        # filename → VideoProps 매핑
        prop_map: Dict[str, VideoProps] | None = None
        if use_cache_fix:
            assert VideoClassifier._video_prop_table is not None, "video_prop_table was None."
            prop_map = {prop.filename: prop for prop in VideoClassifier._video_prop_table}

        # 파일 이동
        for root, _, files in os.walk(target_root_dir):
            # 최상위 폴더는 건너뜀
            if root == target_root_dir:
                continue

            for file in files:
                file_path = os.path.join(root, file)
                target_path = os.path.join(target_root_dir, file)

                # exists 복구: O(1) 접근
                if prop_map and file in prop_map:
                    prop_map[file].exists = True

                # 동일 이름 충돌 처리
                count = 1
                while os.path.exists(target_path):
                    name, ext = os.path.splitext(file)
                    target_path = os.path.join(target_root_dir, f"{name}_{count}{ext}")
                    count += 1

                # 파일 이동
                shutil.move(file_path, target_path)

        # 빈 폴더 삭제
        for dir in get_dirpaths(target_root_dir):
            dir_path = os.path.join(target_root_dir, dir)
            try:
                os.rmdir(dir_path)
            except:
                pass