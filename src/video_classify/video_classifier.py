from src.utils.load_env import load_env
from enum import Enum

def WindowsNameOrder(name: str):
    '''
    문자열을 윈도우 이름 정렬 방식으로 정렬
    '''
    import re

    return [int(t) if t.isdigit() else t.lower() for t in re.split(r'(\d+)', name)]


class Pred(Enum):
    RATIO = 1
    BITRATE = 2
    KEYFRAME = 3
    ALL = 4


class VideoClassifier:
    def __init__(self):
        self.target_dir_root = load_env('ROOT_DIR')
        self.video_prop_table = None
        self.exception_rules = []

    def include_keyframe_interval(self, flag: bool = True):
        '''
        영상 분석시 키프레임 정보 포함 여부 설정
        키프레임 분석은 많은 오버헤드가 있으므로 꼭 필요한 경우가 아니면 False가 권장됨
        '''
        from src.utils import video_prop

        self.video_prop_table = video_prop.get_video_prop_table(self.target_dir_root, flag)

    def add_exception_rule(self, pred):
        '''
        분류 예외 기준을 등록
        입력으로 video_prop_table이 들어오고, 출력으로 boolean이 들어와야 함
        '''
        self.exception_rules.append(pred)

    def classify(self, *, by: Pred):   
        '''
        지정한 경로의 영상파일들을 조건에 따라 분류
        Pred.ALL은 동작하지 않음
        '''
        if self.video_prop_table is None:
            self.include_keyframe_interval(False)

        from src.video_classify.by_bitrate import VideoClassifierByBitrate
        from src.video_classify.by_ratio import VideoClassifierByRatio
        from src.video_classify.by_keyframe import VideoClassifierByKeyframe

        match by:
            case Pred.RATIO:
                VideoClassifierByRatio.classify(self.video_prop_table, self.target_dir_root, self.exception_rules)
            case Pred.BITRATE:
                VideoClassifierByBitrate.classify(self.video_prop_table, self.target_dir_root, self.exception_rules)
            case Pred.KEYFRAME:
                VideoClassifierByKeyframe.classify(self.video_prop_table, self.target_dir_root, self.exception_rules)

    def print(self, *, by: Pred, sort_key=None):
        '''
        지정한 경로의 영상파일들을 조건에 따라 출력
        '''
        if self.video_prop_table is None:
            self.include_keyframe_interval(False)
        
        from src.utils.table_printer import TablePrinter
        from src.video_classify.by_bitrate import VideoClassifierByBitrate
        from src.video_classify.by_ratio import VideoClassifierByRatio
        from src.video_classify.by_keyframe import VideoClassifierByKeyframe

        match by:
            case Pred.RATIO:
                VideoClassifierByRatio.print(self.video_prop_table, sort_key)
            case Pred.BITRATE:
                VideoClassifierByBitrate.print(self.video_prop_table, sort_key)
            case Pred.KEYFRAME:
                VideoClassifierByKeyframe.print(self.video_prop_table, sort_key)
            case Pred.ALL:
                TablePrinter.print(self.video_prop_table, sort_key)

    @staticmethod
    def unclassify_files():
        '''
        지정한 경로의 모든 폴더의 각 파일들을 다시 하나로 모음
        '''
        import os
        import shutil
        from src.utils.filesys import get_dirpaths

        target_dir_root = load_env('ROOT_DIR')
        for root, _, files in os.walk(target_dir_root):
            # 최상위 폴더는 건너뜀
            if root == target_dir_root:
                continue
            
            for file in files:
                file_path = os.path.join(root, file)
                target_path = os.path.join(target_dir_root, file)

                # 동일 이름 파일 처리
                count = 1
                while os.path.exists(target_path):
                    name, ext = os.path.splitext(file)
                    target_path = os.path.join(target_dir_root, f"{name}_{count}{ext}")
                    count += 1

                # 파일 이동
                shutil.move(file_path, target_path)

        # 빈 폴더 삭제
        for dir in get_dirpaths(target_dir_root):
            dir_path = os.path.join(target_dir_root, dir)
            try:
                os.rmdir(dir_path)
            except:
                pass