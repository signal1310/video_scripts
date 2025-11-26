def load_env(key: str):
    '''
    key와 대응하는 .env 값을 가져옴
    '''
    from dotenv import load_dotenv
    import os

    load_dotenv()
    return os.environ.get(key)


def get_root_dir() -> str:
    """
    .rootdir 파일에서 경로를 매번 읽어옴 (프로그램 재시작 불필요)
    큰따옴표 불필요, 경로만 입력
    """
    import os
    rootdir_path = os.path.join(os.path.dirname(__file__), '..', '..', '.rootdir')
    try:
        with open(rootdir_path, 'r', encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        raise FileNotFoundError(f".rootdir 파일을 찾을 수 없습니다: {rootdir_path}")
    except Exception as e:
        raise RuntimeError(f".rootdir 파일 읽기 오류: {e}")