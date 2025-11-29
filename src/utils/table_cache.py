from typing import List, Literal
from src.utils.video_prop import VideoProps
from src.utils.pred import Pred

class TableCache:
    """
    지정한 경로의 비디오 속성 데이터를 캐싱하고 관리하는 클래스
    """
    AllowedPred = Literal[Pred.RATIO, Pred.BITRATE, Pred.KEYFRAME]

    def __init__(self, root_dir: str, init_keyframe_flag: bool = False) -> None:
        import os
        from src.utils.video_prop import get_video_prop_table
        
        # 유효성 검사
        if not os.path.isdir(root_dir):
            raise FileNotFoundError(f"지정된 루트 디렉터리를 찾을 수 없거나 유효하지 않습니다: '{root_dir}'")
        
        self._root_dir: str = root_dir # 분석이 된 대상 경로
        self._include_keyframe: bool = init_keyframe_flag # 키프레임 포함 여부
        self._data: List[VideoProps] = get_video_prop_table(self._root_dir, self._include_keyframe) # 분석 결과 데이터

    @property
    def root_dir(self) -> str: return self._root_dir

    @property
    def data(self) -> List[VideoProps]: return self._data

    def _keyframe_flag_raised(self, keyframe_flag: bool) -> bool:
        """
        keyframe_flag가 raise up된 상황인지 판별
        """
        return not self._include_keyframe and keyframe_flag

    def update_keyframe(self, keyframe_flag: bool) -> None:
        """
        keyframe을 업데이트 하면서, 필요한 상황인 경우 data에 키프레임 정보를 업데이트함
        """
        from src.utils.video_prop import include_keyframe_at
        if self._keyframe_flag_raised(keyframe_flag): 
            self._include_keyframe = keyframe_flag
            include_keyframe_at(self.data, self._root_dir)