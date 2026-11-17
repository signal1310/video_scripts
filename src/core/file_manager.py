"""비디오 파일 탐색, 트랜잭션 기반 배치 이동 및 분류 복원 파일 IO 모듈"""

import contextlib
import logging
import os
import shutil
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from enum import Enum

from src.core.classify_manifest import (
    ManifestRecord,
    build_hashed_filename,
    build_restore_hash_index,
    compute_path_hash,
    find_moved_flat_path,
    load_manifest_records,
    record_key,
    resolve_restore_destination,
    save_manifest_records,
)
from src.core.exceptions import DomainRuntimeError, DomainValueError, ErrorCode
from src.core.models import VideoProps
from src.core.paths import is_valid_dirname
from src.core.workspace_paths import (
    parse_root_dirs,
    primary_root_dir,
    resolve_source_filepath,
    root_logical_prefix,
)

VIDEO_EXTENSIONS: set[str] = {
    ".mp4", ".mkv", ".avi", ".mov", ".webm", ".wmv", ".flv",
    ".ts", ".m2ts", ".mts", ".m4v", ".3gp", ".mpg", ".mpeg",
    ".ogv", ".vob", ".asf", ".rmvb", ".divx"
}
HIDDEN_DIR_PREFIX: str = "."


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


@dataclass(frozen=True, slots=True)
class _RestoreItem:
    """분류 폴더 안 영상 1건의 복원 계획"""

    logical_name: str
    dirname: str
    current_path: str
    target_path: str
    record_key: str | None


def is_video_file(filename: str) -> bool:
    """파일명 확장자 기반 지원 비디오 포맷 판별

    자막, 이미지, 시스템 파일 등에 의한 ffprobe 파싱 오류 사전 차단
    """
    _, ext = os.path.splitext(filename)
    return ext.lower() in VIDEO_EXTENSIONS


def _is_excluded_subdir(dirname: str, excluded: set[str], downscale_prefix: str) -> bool:
    """재귀 탐색에서 진입하지 않을 하위 폴더 판별

    - 숨김 폴더, 분류 폴더, 축소 폴더 접두사로 시작하는 폴더는 제외
    """
    if dirname.startswith(HIDDEN_DIR_PREFIX) or dirname in excluded:
        return True
    return bool(downscale_prefix) and dirname.startswith(downscale_prefix)


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
    root: str, excluded: set[str], downscale_prefix: str
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
                if not entry.is_symlink() and not _is_excluded_subdir(
                    entry.name, excluded, downscale_prefix
                ):
                    stack.append((f"{rel_dir}{entry.name}/", entry.path))
                continue
            if is_video_file(entry.name):
                yield f"{rel_dir}{entry.name}", entry


def _iter_video_entries(
    root: str, *, recursive: bool, excluded: set[str], downscale_prefix: str
) -> Iterator[tuple[str, os.DirEntry[str]]]:
    """단일 작업 폴더의 비디오 (상대 경로 posix, 디렉터리 항목) 쌍 순회"""
    if recursive:
        return _iter_recursive_video_entries(root, excluded, downscale_prefix)
    return _iter_direct_video_entries(root)


def scan_video_stats(
    target_root_dir: str,
    *,
    recursive: bool = False,
    excluded_dirnames: Collection[str] = (),
    downscale_prefix: str = "",
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
        for rel_path, entry in _iter_video_entries(
            root, recursive=recursive, excluded=excluded, downscale_prefix=downscale_prefix
        ):
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
    downscale_prefix: str = "",
) -> list[str]:
    """작업 폴더의 비디오 논리 파일명 목록 반환

    - 단일 작업 폴더면 상대 경로, 다중 작업 폴더면 '라벨/상대 경로'
    - recursive이면 하위 폴더까지 탐색하되 분류 폴더와 숨김 폴더는 진입하지 않음
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
            for rel_path, _ in _iter_video_entries(
                root, recursive=recursive, excluded=excluded, downscale_prefix=downscale_prefix
            )
        )
    return sorted(logical_names)


def get_dirnames(target_root_dir: str) -> list[str]:
    """지정한 디렉터리 직하위에 위치한 서브 디렉터리 이름 목록을 반환"""
    if not os.path.isdir(target_root_dir):
        return []
    with os.scandir(target_root_dir) as entries:
        return [entry.name for entry in entries if entry.is_dir()]


def logical_video_filepath(root_dir: str, vid: VideoProps) -> str:
    """영상의 분류 상태를 반영한 예상 물리 경로 반환

    - 분류된 영상은 분류 폴더 내 실제 평탄 파일 경로를 매니페스트와 해시로 찾음
    """
    source_path = resolve_source_filepath(root_dir, vid.filename)
    if vid.moved_dirname:
        return find_moved_flat_path(
            primary_root_dir(root_dir), vid.moved_dirname, source_path
        )
    return source_path


def resolve_video_filepath(root_dir: str, vid: VideoProps) -> str:
    """비디오가 원래 위치나 분류 폴더에 있는 실제 물리 파일 경로 반환

    - 분류 이동 기록이 없으면 존재 검사 없이 원래 위치 경로를 반환
    """
    source_path = resolve_source_filepath(root_dir, vid.filename)
    if not vid.moved_dirname:
        return source_path
    if os.path.isfile(source_path):
        return source_path
    moved_path = logical_video_filepath(root_dir, vid)
    if os.path.isfile(moved_path):
        return moved_path
    return source_path


def compute_dir_signature(
    root_dir: str,
    *,
    recursive: bool = False,
    excluded_dirnames: Collection[str] = (),
    downscale_prefix: str = "",
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
            downscale_prefix=downscale_prefix,
        )
    )


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


def _validate_target_dirnames(moves: list[tuple[VideoProps, str]]) -> None:
    """분류 폴더명이 작업 폴더 직하위 단일 이름인지 이동 전에 검증"""
    for _, target_dir_relative in moves:
        if not is_valid_dirname(target_dir_relative):
            raise DomainValueError(
                f"분류 폴더명이 올바르지 않아 이동을 중단했습니다: '{target_dir_relative}'",
                code=ErrorCode.CLASS_DIRNAME_INVALID,
            )


def _batch_name_counts(
    root_dir: str, moves: list[tuple[VideoProps, str]]
) -> dict[tuple[str, str], int]:
    """배치 내 (분류 폴더명, 원본 basename 소문자) 빈도 집계"""
    counts: dict[tuple[str, str], int] = {}
    for vid, target_dir_relative in moves:
        base = os.path.basename(resolve_source_filepath(root_dir, vid.filename)).lower()
        key = (target_dir_relative, base)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _source_index_key(primary_root: str, src_path: str, target_dirname: str) -> tuple[str, str, str]:
    """(주 작업 폴더, 원본 경로, 분류 폴더명) 기준 색인 키"""
    return (
        os.path.normcase(os.path.abspath(primary_root)),
        os.path.normcase(os.path.abspath(src_path)),
        target_dirname,
    )


def _index_records_by_source(
    records: dict[str, ManifestRecord],
) -> dict[tuple[str, str, str], ManifestRecord]:
    """원본 경로 기준 레코드 색인"""
    return {
        _source_index_key(rec.primary_root, rec.src_path, rec.target_dirname): rec
        for rec in records.values()
    }


def classify_files_batch(
    root_dir: str,
    moves: list[tuple[VideoProps, str]],
    *,
    rollback_on_error: bool = True,
) -> tuple[int, list[MoveIssue]]:
    """다건 영상을 주 작업 폴더의 분류 폴더로 평탄 이동

    - 주 작업 폴더 직하위 파일이고 충돌이 없으면 원본 파일명 유지
    - 하위 폴더, 추가 작업 폴더 파일이나 이름이 겹치면 해시 접미사를 붙여 이동
    - 기존 대상 파일은 덮어쓰지 않고 건너뜀
    - 중간 OSError 시 이번 배치 이동을 역순 롤백하며, 매니페스트는 전체 성공 후에만 저장
    """
    _validate_target_dirnames(moves)

    primary_root = primary_root_dir(root_dir)
    primary_norm = os.path.normcase(os.path.normpath(primary_root))
    records = load_manifest_records()
    source_index = _index_records_by_source(records)
    name_counts = _batch_name_counts(root_dir, moves)

    issues: list[MoveIssue] = []
    moved_ledger: list[tuple[VideoProps, str, str]] = []
    created_dirs: list[str] = []
    created_dir_set: set[str] = set()

    for vid, target_dir_relative in moves:
        src_path = resolve_source_filepath(root_dir, vid.filename)
        if not os.path.isfile(src_path):
            vid.moved_dirname = None
            continue

        abs_src = os.path.abspath(src_path)
        orig_base = os.path.basename(src_path)
        target_dir = os.path.join(primary_root, target_dir_relative)
        is_primary_direct = os.path.normcase(os.path.dirname(abs_src)) == primary_norm
        source_key = _source_index_key(primary_root, abs_src, target_dir_relative)
        prior = source_index.get(source_key)
        if prior is not None and os.path.exists(os.path.join(target_dir, prior.flat_filename)):
            issues.append(
                MoveIssue(
                    kind=MoveIssueKind.CLASSIFY_TARGET_EXISTS,
                    filename=vid.filename,
                    dirname=target_dir_relative,
                )
            )
            vid.moved_dirname = None
            continue

        plain_target = os.path.join(target_dir, orig_base)
        if is_primary_direct and os.path.exists(plain_target) and (
            record_key(primary_root, target_dir_relative, orig_base) not in records
        ):
            issues.append(
                MoveIssue(
                    kind=MoveIssueKind.CLASSIFY_TARGET_EXISTS,
                    filename=vid.filename,
                    dirname=target_dir_relative,
                )
            )
            vid.moved_dirname = None
            continue

        hash_key = compute_path_hash(abs_src)
        use_plain_name = (
            is_primary_direct
            and name_counts[(target_dir_relative, orig_base.lower())] == 1
            and not os.path.exists(plain_target)
        )
        flat_name = orig_base if use_plain_name else build_hashed_filename(orig_base, hash_key)
        dst_path = os.path.join(target_dir, flat_name)
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

        vid.moved_dirname = target_dir_relative
        moved_ledger.append((vid, src_path, dst_path))
        new_record = ManifestRecord(
            hash_key=hash_key,
            primary_root=primary_root,
            target_dirname=target_dir_relative,
            flat_filename=flat_name,
            src_path=abs_src,
            logical_filename=vid.filename,
        )
        if prior is not None:
            records.pop(record_key(primary_root, target_dir_relative, prior.flat_filename), None)
        records[record_key(primary_root, target_dir_relative, flat_name)] = new_record
        source_index[source_key] = new_record

    if moved_ledger:
        try:
            save_manifest_records(records)
        except OSError as exc:
            rollback_errors = _rollback_moved_files(moved_ledger, created_dirs)
            suffix = (
                f" (롤백 중 추가 경고: {'; '.join(rollback_errors)})"
                if rollback_errors
                else ""
            )
            raise DomainRuntimeError(
                f"분류 매니페스트 저장에 실패하여 이번 배치({len(moved_ledger)}건)를 롤백했습니다: {exc}{suffix}",
                code=ErrorCode.FILE_MOVE_BATCH_FAILED,
            ) from exc

    return len(moved_ledger), issues


def _resolve_unclassify_target_dirs(
    root_dir: str, dirnames: Collection[str] | None
) -> list[str]:
    """복원 대상 분류 폴더의 절대 경로 목록 반환

    dirnames 생략 시 작업 폴더 직하위 전체가 대상이며 유효하지 않은 이름과 폴더가 아닌 항목은 제외
    """
    candidate_names = get_dirnames(root_dir) if dirnames is None else dirnames
    return [
        os.path.join(root_dir, name)
        for name in dict.fromkeys(candidate_names)
        if is_valid_dirname(name) and os.path.isdir(os.path.join(root_dir, name))
    ]


def _unclassify_targets(
    root_dir: str, dirnames: Collection[str] | None
) -> list[tuple[str, str]]:
    """모든 작업 폴더의 (주 작업 폴더 경로, 분류 폴더 경로) 쌍 목록"""
    targets: list[tuple[str, str]] = []
    for root in parse_root_dirs(root_dir):
        for target_dir in _resolve_unclassify_target_dirs(root, dirnames):
            targets.append((root, target_dir))
    return targets


def _iter_restore_items(
    targets: list[tuple[str, str]], records: dict[str, ManifestRecord]
) -> Iterator[_RestoreItem]:
    """분류 폴더 안 영상의 복원 계획을 순회

    - 분류 폴더 직하위 영상만 매니페스트로 원위치 판정
    - 하위 폴더에 있던 영상은 매니페스트 대상이 아니므로 주 작업 폴더 직하위로 복원
    """
    hash_index = build_restore_hash_index(records)
    for primary_root, target_dir in targets:
        target_dirname = os.path.basename(target_dir)
        for current_dir, _, files in os.walk(target_dir):
            for name in files:
                if not is_video_file(name):
                    continue
                current_path = os.path.join(current_dir, name)
                if os.path.normcase(current_dir) == os.path.normcase(target_dir):
                    target_path, logical_name, key = resolve_restore_destination(
                        primary_root, target_dirname, name, records, hash_index
                    )
                else:
                    target_path = os.path.join(primary_root, name)
                    logical_name, key = name, None
                yield _RestoreItem(
                    logical_name=logical_name,
                    dirname=target_dirname,
                    current_path=current_path,
                    target_path=target_path,
                    record_key=key,
                )


def preview_unclassify_all(
    root_dir: str,
    dirnames: Collection[str] | None = None,
) -> tuple[int, int]:
    """파일을 이동하지 않고 복원 예정 영상 수와 이름 충돌로 남을 영상 수를 집계

    unclassify_all_files와 같은 대상 규칙을 쓰며 (복원 예정 수, 충돌 수) 튜플 반환
    """
    restorable: int = 0
    conflicts: int = 0
    records = load_manifest_records()
    for item in _iter_restore_items(_unclassify_targets(root_dir, dirnames), records):
        if os.path.isfile(item.target_path):
            conflicts += 1
        else:
            restorable += 1
    return restorable, conflicts


def unclassify_all_files(
    root_dir: str,
    cached_videos: list[VideoProps] | None = None,
    dirnames: Collection[str] | None = None,
) -> tuple[int, list[MoveIssue]]:
    """분류 폴더의 영상을 원래 위치로 복원하고 비어 버린 분류 폴더 정리

    - 매니페스트에 기록된 원래 하위 폴더와 원래 파일명(확장자 변경 포함)으로 복원
    - 원래 위치에 동일 파일이 있으면 덮어쓰지 않고 분류 폴더에 유지
    - dirnames로 복원 대상 분류 폴더를 한정하며 생략 시 모든 직하위 폴더가 대상
    - (복원 파일 수, 이동 이슈 리스트) 튜플 반환
    """
    issues: list[MoveIssue] = []
    prop_map: dict[str, VideoProps] = (
        {prop.filename: prop for prop in cached_videos} if cached_videos else {}
    )

    if cached_videos:
        for prop in cached_videos:
            prop.moved_dirname = None

    targets = _unclassify_targets(root_dir, dirnames)
    records = load_manifest_records()
    records_changed = False

    moved_count: int = 0
    for item in _iter_restore_items(targets, records):
        if os.path.isfile(item.target_path):
            issues.append(
                MoveIssue(
                    kind=MoveIssueKind.UNCLASSIFY_ROOT_EXISTS,
                    filename=item.logical_name,
                    dirname=item.dirname,
                )
            )
            if item.logical_name in prop_map:
                prop_map[item.logical_name].moved_dirname = item.dirname
            continue

        try:
            os.makedirs(os.path.dirname(item.target_path), exist_ok=True)
            shutil.move(item.current_path, item.target_path)
        except OSError as e:
            issues.append(
                MoveIssue(
                    kind=MoveIssueKind.UNCLASSIFY_MOVE_FAILED,
                    filename=item.logical_name,
                    dirname=item.dirname,
                    src_path=item.current_path,
                    dst_path=item.target_path,
                    error=str(e),
                )
            )
            if item.logical_name in prop_map:
                prop_map[item.logical_name].moved_dirname = item.dirname
            continue

        moved_count += 1
        if item.record_key is not None:
            records.pop(item.record_key, None)
            records_changed = True

    if records_changed:
        try:
            save_manifest_records(records)
        except OSError as exc:
            raise DomainRuntimeError(
                f"분류 매니페스트 갱신에 실패했습니다: {exc}",
                code=ErrorCode.FILE_MOVE_BATCH_FAILED,
            ) from exc

    # - 이동이 끝난 후 비어있는 분류 폴더를 하위부터 역순으로 삭제
    for _, target_dir in targets:
        for current_workdir, subdirs, _ in os.walk(target_dir, topdown=False):
            for d in subdirs:
                with contextlib.suppress(OSError):
                    os.rmdir(os.path.join(current_workdir, d))
        with contextlib.suppress(OSError):
            os.rmdir(target_dir)

    return moved_count, issues
