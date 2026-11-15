"""비디오 파일 탐색, 트랜잭션 기반 배치 이동 및 분류 복원 파일 IO 모듈"""

import contextlib
import logging
import os
import shutil
from dataclasses import dataclass
from enum import Enum

from src.core.exceptions import DomainRuntimeError, ErrorCode
from src.core.models import VideoProps

VIDEO_EXTENSIONS: set[str] = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv",
    ".ts", ".m2ts", ".mts", ".m4v", ".3gp", ".mpg", ".mpeg",
    ".ogv", ".vob", ".asf", ".rmvb", ".divx"
}


class MoveIssueKind(Enum):
    """파일 이동 중 발생한 비치명 이슈 유형"""

    CLASSIFY_TARGET_EXISTS = "classify_target_exists"
    UNCLASSIFY_ROOT_EXISTS = "unclassify_root_exists"
    UNCLASSIFY_MOVE_FAILED = "unclassify_move_failed"


@dataclass(frozen=True, slots=True)
class MoveIssue:
    """파일 이동 이슈 1건의 유형과 관련 경로 정보"""

    kind: MoveIssueKind
    filename: str
    dirname: str
    src_path: str = ""
    dst_path: str = ""
    error: str = ""

    @property
    def is_failure(self) -> bool:
        """이동 자체가 실패한 오류 수준 이슈 여부"""
        return self.kind is MoveIssueKind.UNCLASSIFY_MOVE_FAILED


def is_video_file(filename: str) -> bool:
    """파일명 확장자 기반 지원 비디오 포맷 판별

    자막, 이미지, 시스템 파일 등에 의한 ffprobe 파싱 오류 사전 차단
    """
    _, ext = os.path.splitext(filename)
    return ext.lower() in VIDEO_EXTENSIONS


def get_video_filenames(target_root_dir: str) -> list[str]:
    """지정 디렉터리 내 비디오 확장자 파일명 목록 반환"""
    if not os.path.isdir(target_root_dir):
        return []
    with os.scandir(target_root_dir) as entries:
        return [
            entry.name for entry in entries
            if entry.is_file() and is_video_file(entry.name)
        ]


def get_dirnames(target_root_dir: str) -> list[str]:
    """지정한 디렉터리 직하위에 위치한 서브 디렉터리 이름 목록을 반환"""
    if not os.path.isdir(target_root_dir):
        return []
    with os.scandir(target_root_dir) as entries:
        return [entry.name for entry in entries if entry.is_dir()]


def logical_video_filepath(root_dir: str, vid: VideoProps) -> str:
    """디스크 조회 없이 moved_dirname 메타데이터만으로 예상 파일 경로 조합"""
    if vid.moved_dirname:
        return os.path.join(root_dir, vid.moved_dirname, vid.filename)
    return os.path.join(root_dir, vid.filename)


def resolve_video_filepath(root_dir: str, vid: VideoProps) -> str:
    """비디오가 루트 경로에 있거나 분류 하위 폴더(moved_dirname)로 이동된 경우에도 실제 물리 파일 경로를 추적하여 반환"""
    root_path = os.path.join(root_dir, vid.filename)
    if os.path.isfile(root_path):
        return root_path
    if vid.moved_dirname:
        moved_path = logical_video_filepath(root_dir, vid)
        if os.path.isfile(moved_path):
            return moved_path
    return root_path


def compute_dir_signature(root_dir: str) -> tuple[tuple[str, int, int], ...]:
    """작업 폴더 내 비디오 파일의 (파일명, 크기, 수정시간) 튜플 시그니처 생성

    파일 추가, 삭제, 교체 시 시그니처 변화를 감지해 캐시 자동 갱신 유도
    """
    if not os.path.isdir(root_dir):
        return ()
    sig: list[tuple[str, int, int]] = []
    with os.scandir(root_dir) as entries:
        for entry in entries:
            if not is_video_file(entry.name):
                continue
            try:
                if not entry.is_file():
                    continue
                st = entry.stat()
                sig.append((entry.name, st.st_size, st.st_mtime_ns))
            except OSError:
                continue
    sig.sort(key=lambda item: item[0])
    return tuple(sig)


def move_file(target_root_dir: str, filename: str, target_dir_relative: str) -> bool:
    """파일을 대상 하위 폴더로 이동

    목적지 폴더에 동일 이름 파일이 이미 존재하면 덮어쓰지 않고 False 반환
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


def _rollback_moved_files(
    moved_ledger: list[tuple[VideoProps, str, str]],
    created_dirs: list[str],
) -> list[str]:
    """배치 이동 실패 시 이미 이동된 파일을 역순 롤백하고 새로 만든 빈 폴더를 정리"""
    logger = logging.getLogger(__name__)
    rollback_errors: list[str] = []

    for vid, src_path, dst_path in reversed(moved_ledger):
        vid.moved_dirname = None
        if not os.path.isfile(dst_path):
            continue
        if os.path.exists(src_path):
            rollback_errors.append(f"이동 롤백 충돌(기존 파일 보존): '{src_path}'")
            continue
        try:
            shutil.move(dst_path, src_path)
        except OSError as exc:
            logger.warning("배치 이동 롤백 실패: %s -> %s", dst_path, src_path, exc_info=True)
            rollback_errors.append(f"이동 롤백 실패: '{dst_path}' -> '{src_path}' ({exc})")

    for d in reversed(created_dirs):
        with contextlib.suppress(OSError):
            os.rmdir(d)

    return rollback_errors


def classify_files_batch(
    root_dir: str,
    moves: list[tuple[VideoProps, str]],
    *,
    rollback_on_error: bool = True,
) -> tuple[int, list[MoveIssue]]:
    """다건 파일 분류 이동을 트랜잭션 단위로 수행하며 대상 충돌 시 덮어쓰지 않고 건너뜀

    중간 OSError 발생 시 이번 배치 이동 내역 역순 롤백 후 DomainRuntimeError 송출
    """
    issues: list[MoveIssue] = []
    moved_ledger: list[tuple[VideoProps, str, str]] = []
    created_dirs: list[str] = []
    created_dir_set: set[str] = set()

    for vid, target_dir_relative in moves:
        src_path = os.path.join(root_dir, vid.filename)
        if not os.path.isfile(src_path):
            vid.moved_dirname = None
            continue

        target_dir = os.path.join(root_dir, target_dir_relative)
        dst_path = os.path.join(target_dir, vid.filename)
        if os.path.exists(dst_path):
            issues.append(
                MoveIssue(
                    kind=MoveIssueKind.CLASSIFY_TARGET_EXISTS,
                    filename=vid.filename,
                    dirname=target_dir_relative,
                )
            )
            vid.moved_dirname = None
            continue

        try:
            if not os.path.isdir(target_dir):
                os.makedirs(target_dir, exist_ok=True)
                norm_dir = os.path.abspath(target_dir)
                if norm_dir not in created_dir_set:
                    created_dir_set.add(norm_dir)
                    created_dirs.append(target_dir)
            shutil.move(src_path, dst_path)
            vid.moved_dirname = target_dir_relative
            moved_ledger.append((vid, src_path, dst_path))
        except OSError as exc:
            vid.moved_dirname = None
            if not rollback_on_error:
                raise
            rollback_errors = _rollback_moved_files(moved_ledger, created_dirs)
            suffix = (
                f" (롤백 중 추가 경고: {'; '.join(rollback_errors)})"
                if rollback_errors
                else ""
            )
            raise DomainRuntimeError(
                f"'{vid.filename}' 파일 이동 중 오류가 발생하여 이번 배치({len(moved_ledger)}건)를 롤백했습니다: {exc}{suffix}",
                code=ErrorCode.FILE_MOVE_BATCH_FAILED,
            ) from exc

    return len(moved_ledger), issues


def unclassify_all_files(
    root_dir: str,
    cached_videos: list[VideoProps] | None = None,
) -> tuple[int, list[MoveIssue]]:
    """하위 분류 폴더의 영상을 작업 폴더로 복원하고 빈 하위 폴더 정리

    작업 폴더에 동일 파일명 존재 시 덮어쓰지 않고 하위 폴더에 유지해 파일 손실 방지
    (복원 파일 수, 이동 이슈 리스트) 튜플 반환
    """
    issues: list[MoveIssue] = []
    prop_map: dict[str, VideoProps] = (
        {prop.filename: prop for prop in cached_videos} if cached_videos else {}
    )

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

            # - 파일명 충돌 발생 시 덮어쓰지 않고 하위 폴더에 그대로 보존
            if os.path.isfile(target_path):
                issues.append(
                    MoveIssue(
                        kind=MoveIssueKind.UNCLASSIFY_ROOT_EXISTS,
                        filename=file,
                        dirname=current_dirname,
                    )
                )
                if file in prop_map:
                    prop_map[file].moved_dirname = current_dirname
                continue

            try:
                shutil.move(file_path, target_path)
                moved_count += 1
            except OSError as e:
                issues.append(
                    MoveIssue(
                        kind=MoveIssueKind.UNCLASSIFY_MOVE_FAILED,
                        filename=file,
                        dirname=current_dirname,
                        src_path=file_path,
                        dst_path=target_path,
                        error=str(e),
                    )
                )
                if file in prop_map:
                    prop_map[file].moved_dirname = current_dirname

    # - 이동이 끝난 후 비어있는 하위 폴더들을 역순으로 삭제
    for current_workdir, dirnames, _ in os.walk(root_dir, topdown=False):
        for d in dirnames:
            dir_path = os.path.join(current_workdir, d)
            with contextlib.suppress(OSError):
                os.rmdir(dir_path)

    return moved_count, issues
