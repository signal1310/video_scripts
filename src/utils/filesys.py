import os
import shutil

def get_filepaths(target_dir_root: str) -> list[str]:
    '''
    지정한 경로의 파일이름 list[str] 리턴
    '''
    items = os.listdir(target_dir_root)
    return [f for f in items if os.path.isfile(os.path.join(target_dir_root, f))]


def get_dirpaths(target_dir_root: str) -> list[str]:
    '''
    지정한 경로의 폴더이름 list[str] 리턴
    '''
    items = os.listdir(target_dir_root)
    return [f for f in items if os.path.isdir(os.path.join(target_dir_root, f))]



def move_file(target_dir_root: str, filename: str, target_dir_relative: str):
    '''
    파일을 지정한 폴더로 이동
    '''
    target_dir = os.path.join(target_dir_root, target_dir_relative)

    # 타겟 디렉토리가 존재하지 않으면 생성
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
    
    # 파일 이동
    shutil.move(os.path.join(target_dir_root, filename), target_dir)