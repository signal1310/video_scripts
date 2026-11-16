"""분석 캐시를 거쳐 작업 폴더의 비디오 메타데이터를 수집하는 모듈

조회 순서: 파일명, 크기, 수정 시각 일치 -> 같은 크기 후보와 내용 지문 일치(이름 변경 재사용) -> 나머지만 ffprobe 프로빙
"""

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, replace

from src.core.file_manager import get_video_filenames, resolve_video_filepath
from src.core.models import VideoProps
from src.core.prober import CancelCheck, ProgressCallback, probe_files
from src.service.analysis_store import AnalysisStore, StoredEntry, compute_fingerprint

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FileEntry:
    """작업 폴더 한 파일의 식별 정보"""

    filename: str
    filesize_bytes: int
    mtime_ns: int


def list_file_entries(root_dir: str) -> list[FileEntry]:
    """작업 폴더의 비디오 파일을 정렬된 순서로 나열하며 조회 중 사라진 파일은 제외"""
    entries: list[FileEntry] = []
    for filename in get_video_filenames(root_dir):
        try:
            stat = os.stat(os.path.join(root_dir, filename))
        except OSError:
            logger.warning("분석 대상 파일 정보를 읽지 못해 제외합니다: %s", filename)
            continue
        entries.append(FileEntry(filename, stat.st_size, stat.st_mtime_ns))
    return entries


def probe_directory_with_store(
    root_dir: str,
    *,
    include_keyframe: bool,
    store: AnalysisStore,
    reuse_store: bool = True,
    on_progress: ProgressCallback = None,
    is_cancelled: CancelCheck = None,
) -> list[VideoProps]:
    """분석 캐시를 우선 사용해 작업 폴더 메타데이터를 수집하고 새로 분석한 결과를 저장

    reuse_store가 False면 기존 행을 읽지 않고 분석 결과로 덮어씀
    """
    entries = list_file_entries(root_dir)
    if not entries:
        return []

    resolved: dict[str, VideoProps] = {}
    pending: list[FileEntry] = entries
    fingerprints: dict[str, str] = {}
    if reuse_store:
        resolved, pending = _resolve_by_name(store, entries, include_keyframe)
        renamed, pending, fingerprints = _resolve_by_fingerprint(
            store, root_dir, pending, include_keyframe
        )
        resolved.update(renamed)

    fresh = probe_files(
        root_dir,
        [entry.filename for entry in pending],
        include_keyframe,
        on_progress=on_progress,
        is_cancelled=is_cancelled,
    )
    by_name = {entry.filename: entry for entry in pending}
    _store_fresh(store, root_dir, fresh, by_name, fingerprints)
    for props in fresh:
        resolved[props.filename] = _for_request(props, include_keyframe)

    return [resolved[entry.filename] for entry in entries if entry.filename in resolved]


def persist_keyframes(store: AnalysisStore, root_dir: str, updated: Sequence[VideoProps]) -> None:
    """메모리에서 새로 채운 키프레임 간격을 분석 캐시 행에 반영

    파일 식별 키와 지문은 디스크에서 다시 읽어 구함
    """
    entries: list[StoredEntry] = []
    for props in updated:
        if props.keyframe_interval is None:
            continue
        path = resolve_video_filepath(root_dir, props)
        try:
            stat = os.stat(path)
            fingerprint = compute_fingerprint(path, stat.st_size)
        except OSError:
            logger.warning("키프레임 저장을 건너뜁니다(파일 읽기 실패): %s", props.filename)
            continue
        entries.append(StoredEntry(props.filename, stat.st_size, stat.st_mtime_ns, fingerprint, props))
    store.apply((), entries)


def _for_request(props: VideoProps, include_keyframe: bool) -> VideoProps:
    """요청 조건에 맞게 키프레임 값을 정리, 키프레임 미요청 시 저장된 값을 가린 복사본 반환"""
    if include_keyframe:
        return props
    return replace(props, keyframe_interval=None)


def _is_reusable(entry: StoredEntry, include_keyframe: bool) -> bool:
    return not include_keyframe or entry.props.keyframe_interval is not None


def _resolve_by_name(
    store: AnalysisStore, entries: Sequence[FileEntry], include_keyframe: bool
) -> tuple[dict[str, VideoProps], list[FileEntry]]:
    stored = store.fetch_by_names([entry.filename for entry in entries])
    index = {stored_entry.key: stored_entry for stored_entry in stored}
    resolved: dict[str, VideoProps] = {}
    pending: list[FileEntry] = []
    for entry in entries:
        hit = index.get((entry.filename, entry.filesize_bytes, entry.mtime_ns))
        if hit is not None and _is_reusable(hit, include_keyframe):
            resolved[entry.filename] = _for_request(hit.props, include_keyframe)
        else:
            pending.append(entry)
    return resolved, pending


def _resolve_by_fingerprint(
    store: AnalysisStore,
    root_dir: str,
    pending: Sequence[FileEntry],
    include_keyframe: bool,
) -> tuple[dict[str, VideoProps], list[FileEntry], dict[str, str]]:
    """이름이 바뀐 파일을 같은 크기 후보의 내용 지문으로 찾아 행을 옮기고, 계산한 지문을 함께 반환"""
    if not pending:
        return {}, [], {}
    candidates = store.fetch_by_sizes([entry.filesize_bytes for entry in pending])
    by_size: dict[int, list[StoredEntry]] = {}
    for candidate in candidates:
        if _is_reusable(candidate, include_keyframe):
            by_size.setdefault(candidate.filesize_bytes, []).append(candidate)

    resolved: dict[str, VideoProps] = {}
    moved: list[tuple[StoredEntry, FileEntry]] = []
    remaining: list[FileEntry] = []
    fingerprints: dict[str, str] = {}
    used: set[tuple[str, int, int]] = set()
    for entry in pending:
        same_size = by_size.get(entry.filesize_bytes)
        if not same_size:
            remaining.append(entry)
            continue
        fingerprint = _fingerprint_or_none(root_dir, entry)
        if fingerprint is None:
            remaining.append(entry)
            continue
        fingerprints[entry.filename] = fingerprint
        match = next(
            (c for c in same_size if c.fingerprint == fingerprint and c.key not in used),
            None,
        )
        if match is None:
            remaining.append(entry)
            continue
        used.add(match.key)
        moved.append((match, entry))
        resolved[entry.filename] = _for_request(
            replace(match.props, filename=entry.filename), include_keyframe
        )

    if moved:
        store.apply(
            [match.key for match, _ in moved],
            [
                StoredEntry(
                    entry.filename,
                    entry.filesize_bytes,
                    entry.mtime_ns,
                    match.fingerprint,
                    replace(match.props, filename=entry.filename),
                )
                for match, entry in moved
            ],
        )
    return resolved, remaining, fingerprints


def _fingerprint_or_none(root_dir: str, entry: FileEntry) -> str | None:
    path = os.path.join(root_dir, entry.filename)
    try:
        return compute_fingerprint(path, entry.filesize_bytes)
    except OSError:
        logger.warning("내용 지문을 계산하지 못했습니다: %s", entry.filename)
        return None


def _store_fresh(
    store: AnalysisStore,
    root_dir: str,
    fresh: Sequence[VideoProps],
    by_name: dict[str, FileEntry],
    fingerprints: dict[str, str],
) -> None:
    records: list[StoredEntry] = []
    for props in fresh:
        entry = by_name.get(props.filename)
        if entry is None:
            continue
        fingerprint = fingerprints.get(entry.filename) or _fingerprint_or_none(root_dir, entry)
        if fingerprint is None:
            continue
        records.append(
            StoredEntry(entry.filename, entry.filesize_bytes, entry.mtime_ns, fingerprint, props)
        )
    store.apply((), records)
