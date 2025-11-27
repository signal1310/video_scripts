default_configs: dict[str, str] = {
    "THRESHOLD_RATIO_DIFF": "0.2",
    "BASE_RESOLUTION_WIDTH": "1280",
    "BASE_RESOLUTION_HEIGHT": "720",
    "BASE_TARGET_BITRATE": "1550",
    "THRESHOLD_OPTIMAL_BITRATE_RATE": "1.5",
    "THRESHOLD_KEYFRAME_INTERVAL": "2", 
    "TABULATE_FLOATFMT": ".3f",
    "TABULATE_FILENAME_MAXLEN": "35"
}

def load_env(key: str) -> str:
    '''
    key와 대응하는 .env 값을 가져옴
    '''
    from dotenv import load_dotenv
    import os

    load_dotenv()
    return os.environ.get(key) or default_configs[key]


def get_root_dir() -> str:
    """
    .rootdir 파일에서 경로를 매번 읽어옴 (프로그램 재시작 불필요)
    큰따옴표 불필요, 경로만 입력
    """
    import os
    rootdir_path: str = os.path.join(os.path.dirname(__file__), '..', '..', '.rootdir')
    try:
        with open(rootdir_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        raise FileNotFoundError(f".rootdir 파일을 찾을 수 없습니다: {rootdir_path}")
    except Exception as e:
        raise RuntimeError(f".rootdir 파일 읽기 오류: {e}")