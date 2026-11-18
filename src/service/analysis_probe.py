"""분석 캐시를 거쳐 작업 폴더의 비디오 메타데이터를 수집하는 모듈

조회 순서: 파일명, 크기, 수정 시각 일치 -> 같은 크기 후보와 내용 지문 일치(이름 변경 재사용) -> 나머지만 ffprobe 프로빙
"""

import logging
import os
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from src.core.file_manager import resolve_video_filepath, scan_video_stats
from src.core.messages import format_analysis_summary, format_storage_kind
from src.core.models import VideoProps
from src.core.probe_parser import PROBE_VERSION, build_video_props, decompress_probe_response
from src.core.probe_version_rules import (
    KEYFRAME_MEASURE_VERSION,
    REUSABLE_ROW_ACTIONS,
    ROW_LEGACY,
    ROW_REREAD,
    row_reuse_action,
)
from src.core.prober import FFPROBE_EXECUTABLE_DEFAULT, CancelCheck, ProgressCallback, probe_files
from src.core.storage_probe import concurrency_limit, default_worker_count, probe_storage
from src.core.workspace_paths import primary_root_dir, resolve_source_filepath
from src.service.analysis_store import AnalysisStore, StoredEntry, compute_fingerprint

logger = logging.getLogger(__name__)
_DISPLAY_CHUNK_SIZE: int = 500


@dataclass(frozen=True, slots=True)
class FileEntry:
    """작업 폴더 한 파일의 식별 정보"""

    filename: str
    filesize_bytes: int
    mtime_ns: int


def list_file_entries(
    root_dir: str,
    *,
    recursive: bool = False,
    excluded_dirnames: Collection[str] = (),
) -> list[FileEntry]:
    """작업 폴더의 비디오 파일을 정렬된 순서로 나열하며 조회 중 사라진 파일은 제외"""
    skipped: list[str] = []
    stats = scan_video_stats(
        root_dir,
        recursive=recursive,
        excluded_dirnames=excluded_dirnames,
        skipped=skipped,
    )
    for filename in skipped:
        logger.warning("분석 대상 파일 정보를 읽지 못해 제외합니다: %s", filename)
    return [FileEntry(stat.logical_name, stat.filesize_bytes, stat.mtime_ns) for stat in stats]


def probe_directory_with_store(
    root_dir: str,
    *,
    include_keyframe: bool,
    store: AnalysisStore,
    reuse_store: bool = True,
    on_progress: ProgressCallback = None,
    is_cancelled: CancelCheck = None,
    recursive: bool = False,
    excluded_dirnames: Collection[str] = (),
    renamed_out: dict[str, str] | None = None,
    ffprobe: str = FFPROBE_EXECUTABLE_DEFAULT,
) -> list[VideoProps]:
    """분석 캐시를 우선 사용해 작업 폴더 메타데이터를 수집하고 새로 분석한 결과를 저장

    reuse_store가 False면 기존 행을 읽지 않고 분석 결과로 덮어씀
    """
    entries = list_file_entries(
        root_dir,
        recursive=recursive,
        excluded_dirnames=excluded_dirnames,
    )
    if not entries:
        return []
    logger.info("%s", format_storage_kind(probe_storage(primary_root_dir(root_dir))))

    resolved: dict[str, VideoProps] = {}
    pending: list[FileEntry] = entries
    fingerprints: dict[str, str] = {}
    carried: dict[str, float] = {}
    raw: dict[str, bytes] = {}
    if reuse_store:
        resolved, pending, upgraded, carried = _resolve_by_name(store, entries, include_keyframe)
        store.apply((), upgraded)
        renamed, pending, fingerprints = _resolve_by_fingerprint(
            store, root_dir, pending, include_keyframe, renamed_out=renamed_out
        )
        resolved.update(renamed)
    reused_count = len(resolved)

    # - 전부 캐시에서 얻은 경우 빈 목록으로 프로빙 계층을 거치지 않도록 생략
    full_names = [entry.filename for entry in pending if entry.filename not in carried]
    fresh = (
        probe_files(
            root_dir,
            full_names,
            include_keyframe,
            on_progress=on_progress,
            is_cancelled=is_cancelled,
            ffprobe=ffprobe,
            raw_out=raw,
        )
        if full_names
        else []
    )
    # - 키프레임 값이 남아 있는 행은 메타데이터만 다시 읽고 저장된 키프레임 간격을 이어 받아 패킷 스캔을 생략
    carried_names = [entry.filename for entry in pending if entry.filename in carried]
    if carried_names:
        meta_only = probe_files(
            root_dir, carried_names, False, on_progress=on_progress, is_cancelled=is_cancelled, ffprobe=ffprobe, raw_out=raw
        )
        fresh = [*fresh, *(replace(props, keyframe_interval=carried[props.filename]) for props in meta_only)]
    by_name = {entry.filename: entry for entry in pending}
    _store_fresh(store, root_dir, fresh, by_name, fingerprints, raw)
    for props in fresh:
        resolved[props.filename] = _for_request(props, include_keyframe)

    logger.info(
        "%s",
        format_analysis_summary(
            total=len(entries),
            reused=reused_count,
            fresh=len(fresh),
            failed=len(pending) - len(fresh),
        ),
    )
    return [resolved[entry.filename] for entry in entries if entry.filename in resolved]


def resolve_restore_keyframe_mode(
    store: AnalysisStore,
    root_dir: str,
    *,
    recursive: bool = False,
    excluded_dirnames: Collection[str] = (),
) -> bool | None:
    """작업 폴더 파일에 이전 분석 결과가 남아 있으면 복원에 쓸 키프레임 포함 여부를, 없으면 None 반환

    - 이름, 크기, 수정 시각이 모두 같은 행만 현재 파일의 이전 결과로 간주
    - 이전 결과가 모두 키프레임을 측정했을 때만 포함으로 판정해 복원 뒤 키프레임 값이 빠지지 않게 함
    """
    entries = list_file_entries(
        root_dir,
        recursive=recursive,
        excluded_dirnames=excluded_dirnames,
    )
    if not entries:
        return None
    current_keys = {(entry.filename, entry.filesize_bytes, entry.mtime_ns) for entry in entries}
    hits = [
        stored
        for stored in store.fetch_by_names([entry.filename for entry in entries])
        if stored.key in current_keys and row_reuse_action(stored.probe_version, stored.filename) != ROW_LEGACY
    ]
    if not hits:
        return None
    return all(hit.props.keyframe_interval is not None for hit in hits)


def count_pending_files(
    store: AnalysisStore | None,
    root_dir: str,
    *,
    include_keyframe: bool,
    recursive: bool = False,
    excluded_dirnames: Collection[str] = (),
) -> int:
    """분석 시작 전 ffprobe가 필요한 파일 수를 세며, 이름이 바뀐 파일은 지문 대조 없이 대상으로 셈

    - 지문 계산을 생략해 파일 전체를 읽지 않으므로 예상용으로만 사용
    """
    entries = list_file_entries(
        root_dir,
        recursive=recursive,
        excluded_dirnames=excluded_dirnames,
    )
    if store is None:
        return len(entries)
    _resolved, pending, _upgraded, _carried = _resolve_by_name(store, entries, include_keyframe)
    return len(pending)


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
    """행을 분석 결과로 쓸 수 있는지: 버전 규칙상 재사용 가능하고 요청한 키프레임 값이 있어야 함"""
    if row_reuse_action(entry.probe_version, entry.filename) not in REUSABLE_ROW_ACTIONS:
        return False
    return not include_keyframe or entry.props.keyframe_interval is not None


def _recomputable(hit: StoredEntry, include_keyframe: bool) -> bool:
    """다시 읽어야 하지만 저장된 응답으로 새 계산 규칙을 적용할 수 있는 행인지(키프레임은 값이 이어 받을 수 있어야 함)"""
    if row_reuse_action(hit.probe_version, hit.filename) != ROW_REREAD or hit.probe_raw is None:
        return False
    return not include_keyframe or _carried_keyframe(hit) is not None


def _recompute_from_raw(hit: StoredEntry, include_keyframe: bool) -> StoredEntry:
    """저장된 분석 응답에서 현재 파서 규칙으로 메타데이터를 다시 만들어 행을 돌려줌(ffprobe 호출 없음)"""
    assert hit.probe_raw is not None
    props = build_video_props(hit.filename, hit.filesize_bytes, decompress_probe_response(hit.probe_raw))
    if include_keyframe:
        props = replace(props, keyframe_interval=_carried_keyframe(hit))
    return replace(hit, props=props)


@dataclass(frozen=True, slots=True)
class StoredRows:
    """저장된 결과만으로 만든 표시용 행: 메타데이터, 이전 형식 파일명, 키프레임 포함 여부"""

    rows: tuple[VideoProps, ...]
    legacy_names: frozenset[str]
    include_keyframe: bool

    @property
    def format_changed_count(self) -> int:
        """이전 형식으로 저장되어 다시 분석해야 하는 영상 수"""
        return len(self.legacy_names)

    @property
    def keyframe_missing_count(self) -> int:
        """이전 형식은 아니지만 키프레임 간격 값이 없어 키프레임 분석 때 다시 읽히는 영상 수"""
        return sum(
            1 for props in self.rows if props.filename not in self.legacy_names and props.keyframe_interval is None
        )


def _display_props(hit: StoredEntry) -> tuple[VideoProps, bool]:
    """표시용 메타데이터와 이전 형식 여부. 다시 읽을 행은 저장된 응답으로 계산하고, 그 밖의 옛 행은 저장 값 그대로 표시"""
    action = row_reuse_action(hit.probe_version, hit.filename)
    if action in REUSABLE_ROW_ACTIONS:
        return hit.props, False
    if action == ROW_REREAD and hit.probe_raw is not None:
        props = build_video_props(hit.filename, hit.filesize_bytes, decompress_probe_response(hit.probe_raw))
        return replace(props, keyframe_interval=_carried_keyframe(hit)), False
    return hit.props, True


def load_stored_rows(
    store: AnalysisStore,
    root_dir: str,
    *,
    recursive: bool = False,
    excluded_dirnames: Collection[str] = (),
    on_progress: Callable[[int], None] | None = None,
    is_cancelled: CancelCheck = None,
) -> StoredRows | None:
    """작업 폴더 파일 중 저장된 결과가 있는 행을 ffprobe 없이 읽어 표시용 행을 만듦

    - 판정 단계이므로 DB에 쓰지 않음(결정 30). 이전 형식 행은 저장 값 그대로 표시하고 이름을 legacy_names에 담음
    - 묶음마다 확인한 파일 수를 알리고, 취소되면 None
    """
    entries = list_file_entries(root_dir, recursive=recursive, excluded_dirnames=excluded_dirnames)
    rows: list[VideoProps] = []
    legacy: set[str] = set()
    for start in range(0, len(entries), _DISPLAY_CHUNK_SIZE):
        if is_cancelled is not None and is_cancelled():
            return None
        chunk = entries[start : start + _DISPLAY_CHUNK_SIZE]
        index = {hit.key: hit for hit in store.fetch_by_names([entry.filename for entry in chunk])}
        for entry in chunk:
            hit = index.get((entry.filename, entry.filesize_bytes, entry.mtime_ns))
            if hit is None:
                continue
            props, is_legacy = _display_props(hit)
            rows.append(props)
            if is_legacy:
                legacy.add(entry.filename)
        if on_progress is not None:
            on_progress(min(start + _DISPLAY_CHUNK_SIZE, len(entries)))
    include_keyframe = bool(rows) and all(props.keyframe_interval is not None for props in rows)
    return StoredRows(tuple(rows), frozenset(legacy), include_keyframe)


def _carried_keyframe(hit: StoredEntry) -> float | None:
    """현재 키프레임 측정 방법으로 저장된 간격이 있으면 그 값(메타데이터만 다시 읽을 때 이어 받음)"""
    if hit.keyframe_version != KEYFRAME_MEASURE_VERSION:
        return None
    return hit.props.keyframe_interval


def _resolve_by_name(
    store: AnalysisStore, entries: Sequence[FileEntry], include_keyframe: bool
) -> tuple[dict[str, VideoProps], list[FileEntry], list[StoredEntry], dict[str, float]]:
    """이름과 식별 키가 같은 행을 재사용하고, 번호만 올려 써도 되는 옛 행은 현재 번호로 다시 저장할 목록을 반환

    - 세 번째 값은 번호를 올려 다시 저장할 행, 네 번째 값은 메타데이터만 다시 읽고 이어 받을 키프레임 간격(파일명별)
    """
    stored = store.fetch_by_names([entry.filename for entry in entries])
    index = {stored_entry.key: stored_entry for stored_entry in stored}
    resolved: dict[str, VideoProps] = {}
    pending: list[FileEntry] = []
    upgraded: list[StoredEntry] = []
    carried: dict[str, float] = {}
    for entry in entries:
        hit = index.get((entry.filename, entry.filesize_bytes, entry.mtime_ns))
        if hit is not None and _is_reusable(hit, include_keyframe):
            resolved[entry.filename] = _for_request(hit.props, include_keyframe)
            if hit.probe_version != PROBE_VERSION:
                upgraded.append(hit)
            continue
        if hit is not None and _recomputable(hit, include_keyframe):
            recomputed = _recompute_from_raw(hit, include_keyframe)
            resolved[entry.filename] = _for_request(recomputed.props, include_keyframe)
            upgraded.append(recomputed)
            continue
        pending.append(entry)
        kept = _carried_keyframe(hit) if hit is not None and include_keyframe else None
        if kept is not None:
            carried[entry.filename] = kept
    return resolved, pending, upgraded, carried


def _resolve_by_fingerprint(
    store: AnalysisStore,
    root_dir: str,
    pending: Sequence[FileEntry],
    include_keyframe: bool,
    *,
    renamed_out: dict[str, str] | None = None,
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
    used: set[tuple[str, int, int]] = set()
    fingerprints = _fingerprints_for(
        root_dir, [entry for entry in pending if entry.filesize_bytes in by_size]
    )
    for entry in pending:
        same_size = by_size.get(entry.filesize_bytes)
        fingerprint = fingerprints.get(entry.filename)
        if not same_size or fingerprint is None:
            remaining.append(entry)
            continue
        match = next(
            (c for c in same_size if c.fingerprint == fingerprint and c.key not in used),
            None,
        )
        if match is None:
            remaining.append(entry)
            continue
        used.add(match.key)
        moved.append((match, entry))
        if renamed_out is not None and match.filename != entry.filename:
            renamed_out[match.filename] = entry.filename
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
                    probe_raw=match.probe_raw,
                )
                for match, entry in moved
            ],
        )
    return resolved, remaining, fingerprints


def _fingerprint_or_none(root_dir: str, entry: FileEntry) -> str | None:
    path = resolve_source_filepath(root_dir, entry.filename)
    try:
        return compute_fingerprint(path, entry.filesize_bytes)
    except OSError:
        logger.warning("내용 지문을 계산하지 못했습니다: %s", entry.filename)
        return None


def _fingerprint_workers(root_dir: str) -> int:
    """지문 계산 병렬 수, 작업 폴더 저장 장치의 동시 실행 상한을 넘지 않음"""
    cpu_count = os.cpu_count() or 1
    workers = default_worker_count(cpu_count)
    limit = concurrency_limit(probe_storage(primary_root_dir(root_dir)).kind, cpu_count)
    return workers if limit is None else min(workers, limit)


def _fingerprints_for(root_dir: str, entries: Sequence[FileEntry]) -> dict[str, str]:
    """여러 파일의 내용 지문을 병렬로 계산하며, 읽지 못한 파일은 결과에서 제외

    - 파일 읽기는 GIL을 풀어 주므로 스레드 풀을 사용
    """
    if not entries:
        return {}
    with ThreadPoolExecutor(_fingerprint_workers(root_dir)) as pool:
        digests = list(pool.map(lambda entry: _fingerprint_or_none(root_dir, entry), entries))
    return {
        entry.filename: digest
        for entry, digest in zip(entries, digests, strict=True)
        if digest is not None
    }


def _store_fresh(
    store: AnalysisStore,
    root_dir: str,
    fresh: Sequence[VideoProps],
    by_name: dict[str, FileEntry],
    fingerprints: dict[str, str],
    raw: Mapping[str, bytes],
) -> None:
    missing = [
        by_name[props.filename]
        for props in fresh
        if props.filename in by_name and props.filename not in fingerprints
    ]
    all_fingerprints = {**fingerprints, **_fingerprints_for(root_dir, missing)}
    records: list[StoredEntry] = []
    for props in fresh:
        entry = by_name.get(props.filename)
        if entry is None:
            continue
        fingerprint = all_fingerprints.get(entry.filename)
        if fingerprint is None:
            continue
        records.append(
            StoredEntry(
                entry.filename, entry.filesize_bytes, entry.mtime_ns, fingerprint, props, probe_raw=raw.get(entry.filename)
            )
        )
    store.apply((), records)
