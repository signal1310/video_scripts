import os
import shutil

def get_filenames(target_root_dir: str) -> list[str]:
    '''
    지정한 경로의 파일이름 list[str] 리턴
    '''
    items: list[str] = os.listdir(target_root_dir)
    return [f for f in items if os.path.isfile(os.path.join(target_root_dir, f))]


def get_dirnames(target_root_dir: str) -> list[str]:
    '''
    지정한 경로의 폴더이름 list[str] 리턴
    '''
    items: list[str] = os.listdir(target_root_dir)
    return [f for f in items if os.path.isdir(os.path.join(target_root_dir, f))]


def move_file(target_root_dir: str, filename: str, target_dir_relative: str) -> None:
    '''
    파일을 지정한 폴더로 이동
    '''
    target_dir: str = os.path.join(target_root_dir, target_dir_relative)

    # 타겟 디렉토리가 존재하지 않으면 생성
    if not os.path.exists(target_dir):
        os.makedirs(target_dir)
    
    # 파일 이동
    shutil.move(os.path.join(target_root_dir, filename), target_dir)


def file_exists_in(target_root_dir: str, filename: str) -> bool:
    '''
    target_root_dir 내 지정한 파일이 있는지 확인
    '''
    return os.path.exists(os.path.join(target_root_dir, filename))