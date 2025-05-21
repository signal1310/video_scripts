class VideoProps:
    '''
    비디오 속성에 대한 데이터클래스
    '''
    def __init__(self, /, width, height, rotate_type, ratio, fps, vid_kbps, aud_kbps, vid_size_MB, duration, codec, keyframe_interval):
        self.width: int = width
        self.height: int = height
        self.rotate_type: int = rotate_type
        self.ratio: float = ratio
        self.fps: float = fps
        self.vid_kbps: int = vid_kbps
        self.aud_kbps: int = aud_kbps
        self.vid_size_MB: float = vid_size_MB
        self.duration: float = duration
        self.codec: str = codec
        self.keyframe_interval: float = keyframe_interval
    
    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "height": self.height,
            "rotate_type": self.rotate_type,
            "ratio": self.ratio,
            "fps": self.fps,
            "vid_kbps": self.vid_kbps,
            "aud_kbps": self.aud_kbps,
            "vid_size_MB": self.vid_size_MB,
            "duration": self.duration,
            "codec": self.codec,
            "keyframe_interval": self.keyframe_interval
        }


def get_rotate_type(vid_stream):
    '''
    비디오 스트림에서 회전 정보 리턴
    '''
    from src.utils.safe_ref import safe_dict
    
    if (side_data_list := safe_dict(vid_stream, 'side_data_list', 0)) == 0:
        return side_data_list
    
    result = []
    for dat in side_data_list:
        if dat.get('side_data_type', "") == 'Display Matrix' and 'rotation' in dat:
            result.append(int(dat['rotation']))
        
    return result[0] if result else 0
    

def get_video_props(filepath: str, include_keyframe_interval: bool) -> VideoProps:
    '''
    지정한 영상파일의 여러 속성 리턴
    '''
    import ffmpeg
    import os
    from src.utils.safe_ref import safe_dict

    probe = ffmpeg.probe(filepath)

    vid_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'video' and 'bit_rate' in stream), None)
    aud_stream = next((stream for stream in probe['streams'] if stream['codec_type'] == 'audio'), None)

    print(f"processing: {filepath}")

    width = safe_dict(vid_stream, 'width', -1)
    height = safe_dict(vid_stream, 'height', -1)
    rotate_type = get_rotate_type(vid_stream)

    return VideoProps(
        width = width,
        height = height,
        rotate_type = rotate_type,
        ratio = width / height if abs(rotate_type) % 180 == 0 else height / width, # 회젼을 고려한 비율계산
        fps = eval(safe_dict(vid_stream, 'r_frame_rate', '0')),
        vid_kbps = int(safe_dict(vid_stream, 'bit_rate', 0)) / 1000,
        aud_kbps = int(safe_dict(aud_stream, 'bit_rate', 0)) / 1000,
        vid_size_MB = os.path.getsize(filepath) / (1024 * 1024),
        duration = int(float(safe_dict(vid_stream, 'duration', 0))),
        codec = safe_dict(vid_stream, 'codec_name', 'unknown'),
        keyframe_interval = get_keyframe_interval(filepath) if include_keyframe_interval else None
    )


def get_keyframe_interval(filepath, lim_sec=30):
    import ffmpeg
    import statistics

    probe = ffmpeg.probe(
        filepath,
        select_streams="v",
        skip_frame="nokey",
        show_entries="frame=pts_time",
        read_intervals=f"%+{lim_sec}", # 0~lim_sec초 구간만 읽기
        of="json"
    )

    keyframes = [
        float(frame.get("pts_time", 0))
        for frame in probe.get("frames", [])
    ]
    intervals = [j - i for i, j in zip(keyframes[:-1], keyframes[1:])]
    if len(intervals) < 1:
        return 10
    
    return statistics.mean(intervals)


def get_video_prop_table(target_dir_root, include_keyframe_interval=False):
    '''
    지정한 경로의 파일에 대한 비율 관련 테이블 리턴
    '''
    from src.utils.filesys import get_filepaths
    from src.utils.ratio import get_closet_ratio
    import os

    data = []
    for path in get_filepaths(target_dir_root):
        props = get_video_props(os.path.join(target_dir_root, path), include_keyframe_interval)
        closet_ratio = get_closet_ratio(props.ratio)

        dat = { "filename": path }
        dat.update(props.to_dict())
        dat.update(closet_ratio.to_dict())
        data.append(dat)

    return data