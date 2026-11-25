"""인코딩 결과, 보관, 같은 이름 처리 경로 계산 모듈

- 순수 계산만 포함하며 파일 존재 여부는 호출자가 `exists`로 주입
- 결과 파일은 `<이름>.<확장자>.part` 임시 파일을 거쳐 최종 경로로 배치
"""

import os
from collections.abc import Callable

from src.core.config import NAME_CONFLICT_NUMBER, NAME_CONFLICT_SKIP, ExecutionConfig

PART_SUFFIX: str = ".part"
MAX_NUMBERED_CANDIDATES: int = 999
CONTAINER_EXTENSIONS: dict[str, str] = {"mp4": ".mp4", "mkv": ".mkv", "mov": ".mov"}


def excluded_folder_names(execution: ExecutionConfig) -> tuple[str, ...]:
    """스캔에서 빼야 할 인코딩 폴더 이름(보관 폴더, 별도 출력 폴더)"""
    names = (execution.archive_dirname.strip(), execution.output_dirname.strip())
    return tuple(name for name in names if name)


def in_place_output_path(source_path: str, container: str) -> str:
    """원본과 같은 폴더에 확장자만 컨테이너에 맞춘 결과 경로"""
    stem = os.path.splitext(os.path.basename(source_path))[0]
    return os.path.join(os.path.dirname(source_path), stem + CONTAINER_EXTENSIONS[container])


def archive_path_of(source_path: str, archive_dirname: str) -> str:
    """원본을 옮겨 둘 보관 폴더 경로(같은 폴더 하위)"""
    return os.path.join(os.path.dirname(source_path), archive_dirname, os.path.basename(source_path))


def separate_output_path(
    source_path: str, root_dir: str, output_dirname: str, container: str
) -> str:
    """작업 폴더 기준 상대 경로를 그대로 살린 별도 출력 폴더 경로"""
    relative_dir, name = os.path.split(os.path.relpath(source_path, root_dir))
    stem = os.path.splitext(name)[0]
    return os.path.join(root_dir, output_dirname, relative_dir, stem + CONTAINER_EXTENSIONS[container])


def numbered_candidate(path: str, number: int) -> str:
    """번호를 붙인 후보 경로, 예: `영상.mp4` -> `영상 (1).mp4`"""
    directory, name = os.path.split(path)
    stem, ext = os.path.splitext(name)
    return os.path.join(directory, f"{stem} ({number}){ext}")


def choose_free_path(
    candidate: str,
    name_conflict: str,
    exists: Callable[[str], bool],
) -> str | None:
    """같은 이름 처리 정책에 따라 쓸 수 있는 경로를 고름. 쓸 수 없으면 None

    - 건너뜀 정책: 이름이 있으면 None
    - 번호 정책: 번호를 붙여 비어 있는 첫 경로, 후보가 모두 찼으면 None
    """
    if not exists(candidate):
        return candidate
    if name_conflict == NAME_CONFLICT_SKIP:
        return None
    if name_conflict != NAME_CONFLICT_NUMBER:
        return None
    for number in range(1, MAX_NUMBERED_CANDIDATES + 1):
        numbered = numbered_candidate(candidate, number)
        if not exists(numbered):
            return numbered
    return None


def part_path_of(output_path: str) -> str:
    """결과 임시 파일 경로(같은 폴더, 최종 이름 뒤에 .part)"""
    return output_path + PART_SUFFIX
