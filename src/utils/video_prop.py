from typing import Any, Dict, Iterator, List, Optional
from dataclasses import dataclass
from src.utils.ratio import ClosetRatio

@dataclass
class VideoProps:
    """
    비디오 속성에 대한 데이터클래스
    """
    filename: str
    width: int
    height: int
    rotate_type: int
    fps: float
    vid_kbps: int
    aud_kbps: int
    vid_size_MB: float
    duration: float
    codec: str
    ratio: ClosetRatio
    keyframe_interval: Optional[float] = None
    exists: bool = True


def _get_rotate_type(vid_stream: Optional[Dict[str, Any]]) -> int:
    """
    비디오 스트림에서 회전 정보 리턴
    """
    from src.utils.safe_ref import safe_dict
    
    if (side_data_list := safe_dict(vid_stream, "side_data_list", 0)) == 0:
        return side_data_list
    
    result: List[int] = []
    for dat in side_data_list:
        if dat.get("side_data_type", "") == "Display Matrix" and "rotation" in dat:
            result.append(int(dat["rotation"]))
        
    return result[0] if result else 0
    

def _get_video_props(filepath: str, filename: str, include_keyframe_interval: bool) -> VideoProps:
    """
    지정한 영상파일의 여러 속성 리턴
    """
    import ffmpeg
    import os
    from src.utils.safe_ref import safe_dict
    from src.utils.ratio import get_closet_ratio

    probe: Dict[str, Any] = ffmpeg.probe(filepath)

    streams: List[Dict[str, Any]] = probe.get("streams", [])
    vid_stream: Optional[Dict[str, Any]] = next((stream for stream in streams if stream.get("codec_type") == "video" and "bit_rate" in stream), None)
    aud_stream: Optional[Dict[str, Any]] = next((stream for stream in streams if stream.get("codec_type") == "audio"), None)

    return VideoProps(
        filename = filename,
        width = (width := safe_dict(vid_stream, "width", -1)),
        height = (height := safe_dict(vid_stream, "height", -1)),
        rotate_type = (rotate_type := _get_rotate_type(vid_stream)),
        fps = float(eval(safe_dict(vid_stream, "r_frame_rate", "0"))),
        vid_kbps = round(int(safe_dict(vid_stream, "bit_rate", 0)) / 1000),
        aud_kbps = round(int(safe_dict(aud_stream, "bit_rate", 0)) / 1000),
        vid_size_MB = os.path.getsize(filepath) / (1024 * 1024),
        duration = (duration := float(safe_dict(vid_stream, "duration", 0)) or 0.0),
        codec = safe_dict(vid_stream, "codec_name", "unknown"),
        ratio = get_closet_ratio(width / height if abs(rotate_type) % 180 == 0 else height / width), #회전을 고려한 비율 계산
        keyframe_interval = _get_keyframe_interval(filepath, duration) if include_keyframe_interval else None
    )


def _get_keyframe_interval(filepath: str, duration: float) -> float:
    """
    키프레임 간격을 빠르게 얻되,
    최대 15초 구간까지만 탐색.
    15초 탐색에서도 1개 이하인 경우 → 15초로 간주.
    """
    import ffmpeg
    import statistics

    lim_list: List[float] = [2.1, 5.1, 10]

    # 극단적으로 짧은 비디오는 키프레임 값 1.0
    if duration < lim_list[0]:
        return 1.0

    for lim in lim_list:
        probe: Dict[str, Any] = ffmpeg.probe(
            filepath,
            select_streams="v",
            skip_frame="nokey",
            show_entries="frame=pts_time",
            read_intervals=f"%+{lim}",
            of="json"
        )

        keyframes: List[float] = [
            float(frame.get("pts_time", 0))
            for frame in probe.get("frames", [])
        ]

        # 키프레임 2개 이상 → 간격 계산 가능
        if len(keyframes) >= 2:
            intervals: List[float] = [j - i for i, j in zip(keyframes[:-1], keyframes[1:])]
            if intervals:
                return statistics.mean(intervals)

    # 여기 오면 키프레임이 2개 미만
    return float(lim_list[-1])


def _worker_include_keyframe_at(args: tuple[str, str, Optional[float], float]) -> tuple[str, Optional[float]]:
    import os 
    target_root_dir, filename, current_interval, duration = args
    filepath: str = os.path.join(target_root_dir, filename)

    if current_interval: return f"Skipped: {filepath} (이미 키프레임이 확인된 파일)", None

    return f"processed: {filepath}", _get_keyframe_interval(filepath, duration)


def include_keyframe_at(video_prop_table: List[VideoProps], target_root_dir: str) -> None:
    """
    구해진 video_prop_table에 키프레임 정보를 추가적으로 삽입
    """
    from concurrent.futures import ProcessPoolExecutor
    import os

    tasks: List[tuple[str, str, Optional[float], float]] = [
        (target_root_dir, vid.filename, vid.keyframe_interval, vid.duration) 
        for vid in video_prop_table
    ]

    with ProcessPoolExecutor() as exe:
        results: Iterator[tuple[str, Optional[float]]] = exe.map(_worker_include_keyframe_at, tasks)

        for vid, (log, interval) in zip(video_prop_table, results):
            print(log)
            vid.keyframe_interval = interval


def _worker_get_video_prop_table(args: tuple[str, str, bool]) -> tuple[VideoProps, str]:
    import os
    target_root_dir, path, include_keyframe_interval = args
    filepath = os.path.join(target_root_dir, path)
    log: str = f"processed: {filepath}"
    return _get_video_props(filepath, path, include_keyframe_interval), log


def get_video_prop_table(target_root_dir: str, include_keyframe_interval: bool) -> List[VideoProps]:
    """
    지정한 경로의 파일에 대한 비율 관련 테이블 리턴
    """
    from src.utils.filesys import get_filepaths
    from concurrent.futures import ProcessPoolExecutor

    tasks: List[tuple[str, str, bool]] = [(target_root_dir, path, include_keyframe_interval) for path in get_filepaths(target_root_dir)]
    results: List[VideoProps] = []
    with ProcessPoolExecutor() as exe:
        for result, log in exe.map(_worker_get_video_prop_table, tasks):
            print(log)
            results.append(result)

    return results