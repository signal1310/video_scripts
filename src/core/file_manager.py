import contextlib
import os
import shutil

from src.core.models import VideoProps

VIDEO_EXTENSIONS: set[str] = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv",
    ".ts", ".m2ts", ".mts", ".m4v", ".3gp", ".mpg", ".mpeg",
    ".ogv", ".vob", ".asf", ".rmvb", ".divx"
}


def is_video_file(filename: str) -> bool:
    """
    - 파일명의 확장자를 검사하여 지원 대상 비디오 포맷인지 판별.
    - 자막(.srt), 이미지, 시스템 파일(desktop.ini) 등으로 인한 ffprobe 파싱 오류를 사전에 차단.
    """
    _, ext = os.path.splitext(filename)
    return ext.lower() in VIDEO_EXTENSIONS


def get_video_filenames(target_root_dir: str) -> list[str]:
    """
    - 지정한 디렉터리 내에 존재하는 파일들 중 비디오 확장자를 가진 파일명 목록을 반환.
    """
    if not os.path.isdir(target_root_dir):
        return []
    items: list[str] = os.listdir(target_root_dir)
    return [
        f for f in items
        if os.path.isfile(os.path.join(target_root_dir, f)) and is_video_file(f)
    ]


def get_dirnames(target_root_dir: str) -> list[str]:
    """
    - 지정한 디렉터리 직하위에 위치한 서브 디렉터리 이름 목록을 반환.
    """
    if not os.path.isdir(target_root_dir):
        return []
    items: list[str] = os.listdir(target_root_dir)
    return [f for f in items if os.path.isdir(os.path.join(target_root_dir, f))]


def file_exists_in(target_root_dir: str, filename: str) -> bool:
    """
    - 지정한 대상 디렉터리의 직하위에 해당 파일이 존재하는지 여부를 확인.
    """
    return os.path.isfile(os.path.join(target_root_dir, filename))


def resolve_video_filepath(root_dir: str, vid: VideoProps) -> str:
    """
    - 비디오가 루트 경로에 있거나 분류 하위 폴더(moved_dirname)로 이동된 경우에도 실제 물리 파일 경로를 추적하여 반환.
    """
    root_path = os.path.join(root_dir, vid.filename)
    if os.path.isfile(root_path):
        return root_path
    if vid.moved_dirname:
        moved_path = os.path.join(root_dir, vid.moved_dirname, vid.filename)
        if os.path.isfile(moved_path):
            return moved_path
    return root_path


def compute_dir_signature(root_dir: str) -> tuple[tuple[str, int, int], ...]:
    """
    - 작업 경로 내 비디오 파일들의 (파일명, 크기, 수정시간)을 수집하여 튜플 형태의 고유 시그니처를 생성.
    - 파일의 추가, 삭제, 인코딩 교체 발생 시 시그니처 변화를 감지하여 캐시를 자동으로 갱신.
    """
    if not os.path.isdir(root_dir):
        return ()
    sig: list[tuple[str, int, int]] = []
    for fname in sorted(get_video_filenames(root_dir)):
        fpath = os.path.join(root_dir, fname)
        try:
            st = os.stat(fpath)
            sig.append((fname, st.st_size, st.st_mtime_ns))
        except OSError:
            continue
    return tuple(sig)


def move_file(target_root_dir: str, filename: str, target_dir_relative: str) -> bool:
    """
    - 파일을 대상 하위 폴더로 이동.
    - 동일한 이름의 파일이 목적지 폴더에 이미 존재할 경우에는 덮어쓰지 않고 False 반환.
    """
    src_path: str = os.path.join(target_root_dir, filename)
    if not os.path.isfile(src_path):
        return False

    target_dir: str = os.path.join(target_root_dir, target_dir_relative)
    os.makedirs(target_dir, exist_ok=True)

    dst_path: str = os.path.join(target_dir, filename)
    if os.path.exists(dst_path):
        return False

    shutil.move(src_path, dst_path)
    return True


def unclassify_all_files(
    root_dir: str,
    cached_videos: list[VideoProps] | None = None,
    unmark_pseudo_classified_only: bool = False,
) -> tuple[int, list[str]]:
    """
    - 하위 폴더로 분류되었거나 모의 분류(가분류)된 영상들을 루트 작업 경로로 복구하고 빈 폴더를 정리.
    - 루트 경로에 동일 파일명이 이미 존재하는 경우, 덮어쓰지 않고 하위 폴더에 그대로 유지하여 파일 손실을 방지.
    - 반환값으로 (복구된 파일 개수, 경고 및 오류 메시지 리스트)를 제공.
    """
    warnings: list[str] = []
    prop_map: dict[str, VideoProps] = (
        {prop.filename: prop for prop in cached_videos} if cached_videos else {}
    )

    # - 실제 파일 이동 없이 가분류 표시만 초기화하는 옵션을 처리.
    if unmark_pseudo_classified_only:
        if cached_videos:
            for prop in cached_videos:
                if file_exists_in(root_dir, prop.filename):
                    prop.moved_dirname = None
        return 0, warnings

    if cached_videos:
        for prop in cached_videos:
            prop.moved_dirname = None

    moved_count: int = 0
    for current_workdir, _, files in os.walk(root_dir):
        if os.path.abspath(current_workdir) == os.path.abspath(root_dir):
            continue

        current_dirname: str = os.path.relpath(current_workdir, root_dir)
        for file in files:
            if not is_video_file(file):
                continue

            file_path = os.path.join(current_workdir, file)
            target_path = os.path.join(root_dir, file)

            # - 파일명 충돌 발생 시 덮어쓰지 않고 하위 폴더에 그대로 보존.
            if file_exists_in(root_dir, file):
                warnings.append(
                    f"[WARN] '{file}' 파일이 이미 루트 작업 경로에 존재하여 '{current_dirname}' 폴더에 유지됨."
                )
                if file in prop_map:
                    prop_map[file].moved_dirname = current_dirname
                continue

            try:
                shutil.move(file_path, target_path)
                moved_count += 1
            except OSError as e:
                warnings.append(
                    f"[ERROR] 파일 이동 실패: {file_path} -> {target_path} ({e})"
                )
                if file in prop_map:
                    prop_map[file].moved_dirname = current_dirname

    # - 이동이 끝난 후 비어있는 하위 폴더들을 역순으로 삭제.
    for current_workdir, dirnames, _ in os.walk(root_dir, topdown=False):
        for d in dirnames:
            dir_path = os.path.join(current_workdir, d)
            with contextlib.suppress(OSError):
                os.rmdir(dir_path)

    return moved_count, warnings
