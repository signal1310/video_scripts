from typing import Any, Dict, List, Optional, Callable, Tuple
from src.utils.load_env import get_root_dir
from src.utils.video_prop import VideoProps
from enum import Enum

def WindowsNameOrder(name: str):
    """
    문자열을 윈도우 이름 정렬 방식으로 정렬
    """
    import re

    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


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
    _keyframe_flag: bool = False  # include_keyframe_interval에서 대기 중인 플래그

    def __init__(self):
        import os

        self.target_root_dir: str = get_root_dir()
        # 경로가 유효하고 이전 값과 다른 경우에만 처리
        if os.path.isdir(self.target_root_dir) and VideoClassifier._target_root_dir != self.target_root_dir:
            VideoClassifier._video_prop_table = None  # 경로 변경 시 캐시 무효화
            VideoClassifier._target_root_dir = self.target_root_dir
            VideoClassifier._prev_keyframe_flag = False
        self.exception_rules: List[Callable[[VideoProps], bool]] = []

    def _update_video_prop_table(self):
        """
        버퍼를 확인하고, 변경이 필요한 경우에만 get_video_prop_table 호출
        _pending_keyframe_flag를 직접 참조하여 분석 수행
        - 처음 호출: 항상 실행
        - 플래그 True로 상향 변경: 다시 호출 (키프레임 데이터 필요)
        - 플래그 False로 하향 변경: 호출 안 함 (기존 캐시 재사용 가능)
        """
        from src.utils.video_prop import get_video_prop_table

        # 처음 호출 또는 캐시가 초기화됐거나, 플래그가 False→True로 상향 변경될 때만 호출
        if VideoClassifier._video_prop_table is None or (VideoClassifier._keyframe_flag and not VideoClassifier._prev_keyframe_flag):
            VideoClassifier._video_prop_table = get_video_prop_table(self.target_root_dir, VideoClassifier._keyframe_flag)
            VideoClassifier._prev_keyframe_flag = VideoClassifier._keyframe_flag

    def include_keyframe_interval(self, flag: bool = True):
        """
        영상 분석시 키프레임 정보 포함 여부 설정 (플래그만 저장)
        실제 분석은 classify나 print 호출 시점에 수행됨
        키프레임 분석은 많은 오버헤드가 있으므로 꼭 필요한 경우가 아니면 False가 권장됨
        """
        VideoClassifier._keyframe_flag = flag

    def add_exception_rule(self, pred: Callable[[VideoProps], bool]) -> None:
        """
        분류 예외 기준을 등록
        입력으로 video_prop_table이 들어오고, 출력으로 boolean이 들어와야 함
        """
        self.exception_rules.append(pred)

    def classify(self, *, by: Pred):   
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

    def print(self, *, by: Pred, sort_key: Callable[[Dict[str, Any]], Tuple | list] | None = None):
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
                VideoClassifierByRatio.print(VideoClassifier._video_prop_table, sort_key)
            case Pred.BITRATE:
                VideoClassifierByBitrate.print(VideoClassifier._video_prop_table, sort_key)
            case Pred.KEYFRAME:
                VideoClassifierByKeyframe.print(VideoClassifier._video_prop_table, sort_key)
            case Pred.ALL:
                self._print_all_video_prop_table(VideoClassifier._video_prop_table, sort_key)
    
    def _print_all_video_prop_table(self, video_prop_table: List[VideoProps], sort_key: Callable[[Dict[str, Any]], Tuple | list] | None):
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
                "키프레임 간격": vid.keyframe_interval
            })

        TablePrinter.print(table, sort_key)
        

    @staticmethod
    def unclassify_files():
        """
        지정한 경로의 모든 폴더의 각 파일들을 다시 하나로 모음
        """
        import os
        import shutil
        from src.utils.filesys import get_dirpaths

        # 작업 대상인 루트 폴더가 video_prop_table 캐싱된 경로인 경우 캐시 무효화
        if ((target_root_dir := get_root_dir()) == VideoClassifier._target_root_dir 
                and VideoClassifier._video_prop_table is not None
                and os.path.isdir(target_root_dir)):
            VideoClassifier._video_prop_table = None
            VideoClassifier._prev_keyframe_flag = False

        for root, _, files in os.walk(target_root_dir):
            # 최상위 폴더는 건너뜀
            if root == target_root_dir:
                continue
            
            for file in files:
                file_path = os.path.join(root, file)
                target_path = os.path.join(target_root_dir, file)

                # 동일 이름 파일 처리
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