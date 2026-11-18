"""비디오 파일 탐색과 작업 폴더 시그니처 계산 파일 IO 모듈"""

import os
from collections.abc import Collection, Iterator
from dataclasses import dataclass

from src.core.models import VideoProps
from src.core.workspace_paths import (
    parse_root_dirs,
    resolve_source_filepath,
    root_logical_prefix,
)

VIDEO_EXTENSIONS: set[str] = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv",
    ".ts", ".m2ts", ".mts", ".m4v", ".3gp", ".mpg", ".mpeg",
    ".ogv", ".vob", ".asf", ".rmvb", ".divx"
}
HIDDEN_DIR_PREFIX: str = "."


def is_video_file(filename: str) -> bool:
    """파일명 확장자 기반 지원 비디오 포맷 판별

    자막, 이미지, 시스템 파일 등에 의한 ffprobe 파싱 오류 사전 차단
    """
    _, ext = os.path.splitext(filename)
    return ext.lower() in VIDEO_EXTENSIONS


def _is_excluded_subdir(dirname: str, excluded: set[str]) -> bool:
    """재귀 탐색에서 진입하지 않을 하위 폴더 판별

    - 숨김 폴더와 호출자가 지정한 제외 폴더명은 진입하지 않음
    """
    return dirname.startswith(HIDDEN_DIR_PREFIX) or dirname in excluded


@dataclass(frozen=True, slots=True)
class VideoStat:
    """작업 폴더 비디오 1건의 논리 파일명과 디렉터리 항목에서 얻은 크기, 수정 시각"""

    logical_name: str
    filesize_bytes: int
    mtime_ns: int


def _iter_direct_video_entries(root: str) -> Iterator[tuple[str, os.DirEntry[str]]]:
    """작업 폴더 직하위 비디오 (상대 경로, 디렉터리 항목) 쌍 순회"""
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_file() and is_video_file(entry.name):
                yield entry.name, entry


def _iter_recursive_video_entries(
    root: str, excluded: set[str]
) -> Iterator[tuple[str, os.DirEntry[str]]]:
    """작업 폴더 하위 전체의 비디오 (상대 경로 posix, 디렉터리 항목) 쌍 순회

    - os.walk와 같이 심볼릭 링크 폴더는 진입하지 않고, 디렉터리 항목은 한 번의 scandir로만 조회
    """
    stack: list[tuple[str, str]] = [("", root)]
    while stack:
        rel_dir, abs_dir = stack.pop()
        try:
            with os.scandir(abs_dir) as entries:
                children = list(entries)
        except OSError:
            continue
        for entry in children:
            try:
                is_dir = entry.is_dir()
            except OSError:
                is_dir = False
            if is_dir:
                if not entry.is_symlink() and not _is_excluded_subdir(entry.name, excluded):
                    stack.append((f"{rel_dir}{entry.name}/", entry.path))
                continue
            if is_video_file(entry.name):
                yield f"{rel_dir}{entry.name}", entry


def _iter_video_entries(
    root: str, *, recursive: bool, excluded: set[str]
) -> Iterator[tuple[str, os.DirEntry[str]]]:
    """단일 작업 폴더의 비디오 (상대 경로 posix, 디렉터리 항목) 쌍 순회"""
    if recursive:
        return _iter_recursive_video_entries(root, excluded)
    return _iter_direct_video_entries(root)


def scan_video_stats(
    target_root_dir: str,
    *,
    recursive: bool = False,
    excluded_dirnames: Collection[str] = (),
    skipped: list[str] | None = None,
) -> list[VideoStat]:
    """작업 폴더의 비디오를 논리 파일명 순으로 나열하며 크기와 수정 시각을 함께 반환

    - 한 번의 디렉터리 조회로 얻은 정보를 쓰므로 파일마다 os.stat를 호출하지 않음
    - 조회 중 사라지거나 읽지 못한 파일은 결과에서 빼고, skipped가 주어지면 논리 파일명을 추가
    """
    roots = parse_root_dirs(target_root_dir)
    excluded = set(excluded_dirnames)
    stats: list[VideoStat] = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        prefix = root_logical_prefix(root, roots)
        for rel_path, entry in _iter_video_entries(root, recursive=recursive, excluded=excluded):
            logical_name = f"{prefix}{rel_path}"
            try:
                st = entry.stat()
            except OSError:
                if skipped is not None:
                    skipped.append(logical_name)
                continue
            stats.append(VideoStat(logical_name, st.st_size, st.st_mtime_ns))
    return sorted(stats, key=lambda stat: stat.logical_name)


def get_video_filenames(
    target_root_dir: str,
    *,
    recursive: bool = False,
    excluded_dirnames: Collection[str] = (),
) -> list[str]:
    """작업 폴더의 비디오 논리 파일명 목록 반환

    - 단일 작업 폴더면 상대 경로, 다중 작업 폴더면 '라벨/상대 경로'
    - recursive이면 하위 폴더까지 탐색하되 제외 폴더와 숨김 폴더는 진입하지 않음
    """
    roots = parse_root_dirs(target_root_dir)
    excluded = set(excluded_dirnames)
    logical_names: list[str] = []
    for root in roots:
        if not os.path.isdir(root):
            continue
        prefix = root_logical_prefix(root, roots)
        logical_names.extend(
            f"{prefix}{rel_path}"
            for rel_path, _ in _iter_video_entries(root, recursive=recursive, excluded=excluded)
        )
    return sorted(logical_names)


def resolve_video_filepath(root_dir: str, vid: VideoProps) -> str:
    """비디오의 작업 폴더 기준 물리 파일 경로 반환"""
    return resolve_source_filepath(root_dir, vid.filename)


def compute_dir_signature(
    root_dir: str,
    *,
    recursive: bool = False,
    excluded_dirnames: Collection[str] = (),
) -> tuple[tuple[str, int, int], ...]:
    """작업 폴더 내 비디오 파일의 (논리 파일명, 크기, 수정시간) 시그니처 생성

    파일 추가, 삭제, 교체 시 시그니처 변화를 감지해 캐시 자동 갱신 유도
    """
    return tuple(
        (stat.logical_name, stat.filesize_bytes, stat.mtime_ns)
        for stat in scan_video_stats(
            root_dir,
            recursive=recursive,
            excluded_dirnames=excluded_dirnames,
        )
    )


