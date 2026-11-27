"""분석 캐시를 거쳐 작업 폴더의 비디오 메타데이터를 수집하는 모듈

조회 순서: 볼륨 위치, 크기, 수정 시각 일치(파일 읽기 없음) -> 파일명, 크기, 수정 시각 일치(파일 읽기 없음)
-> 같은 크기 후보와 내용 지문 일치(이름 변경 재사용) -> 나머지만 ffprobe 프로빙
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Collection, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace

from src.core.analysis.file_manager import resolve_video_filepath, scan_video_stats
from src.core.analysis.probe_version_rules import (
    KEYFRAME_MEASURE_VERSION,
    REUSABLE_ROW_ACTIONS,
    ROW_LEGACY,
    ROW_REREAD,
    row_reuse_action,
)
from src.core.analysis.prober import FFPROBE_EXECUTABLE_DEFAULT, CancelCheck, ProgressCallback, probe_files
from src.core.analysis.storage_probe import concurrency_limit, default_worker_count, probe_storage
from src.core.analysis.volume_identity import volume_location
from src.core.messages import format_analysis_summary, format_lookup_summary, format_storage_kind
from src.core.models import VideoProps
from src.core.probe_parser import (
    PROBE_VERSION,
    build_video_props,
    decompress_probe_response,
    summarize_streams_from_probe,
)
from src.core.workspace_paths import primary_root_dir, resolve_source_filepath
from src.service.analysis_store import (
    AnalysisStore,
    LocationKey,
    LocationRecord,
    StoredEntry,
    compute_fingerprint,
    root_scope_key,
    scope_matches,
)

logger = logging.getLogger(__name__)
_DISPLAY_CHUNK_SIZE: int = 500


@dataclass(frozen=True, slots=True)
class FileEntry:
    """작업 폴더 한 파일의 식별 정보"""

    filename: str
    filesize_bytes: int
    mtime_ns: int


@dataclass(slots=True)
class TierHits:
    """조회 단계별로 캐시에서 결과를 얻은 건수"""

    location: int = 0
    name: int = 0
    fingerprint: int = 0


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
    entries_out: list[FileEntry] | None = None,
    prescan: StoredRows | None = None,
    ffprobe: str = FFPROBE_EXECUTABLE_DEFAULT,
) -> list[VideoProps]:
    """분석 캐시를 우선 사용해 작업 폴더 메타데이터를 수집하고 새로 분석한 결과를 저장

    reuse_store가 False면 기존 행을 읽지 않고 분석 결과로 덮어씀
    prescan은 직전 판정이 읽은 저장 행. 이름과 식별 키가 같은 행 조회를 다시 하지 않음
    entries_out을 넘기면 이번 조회에서 읽은 파일 목록을 채워 호출자가 디렉터리 시그니처로 재사용
    """
    # - 폴더는 항상 다시 나열. 판정과 분석 사이에 바뀐 파일을 놓치지 않기 위함
    entries = list_file_entries(
        root_dir,
        recursive=recursive,
        excluded_dirnames=excluded_dirnames,
    )
    if entries_out is not None:
        entries_out[:] = entries
    if not entries:
        return []
    logger.info("%s", format_storage_kind(probe_storage(primary_root_dir(root_dir))))

    resolved: dict[str, VideoProps] = {}
    pending: list[FileEntry] = entries
    carried: dict[str, float] = {}
    raw: dict[str, bytes] = {}
    fingerprints: dict[str, str] = {}
    locations = _entry_locations(root_dir, entries)
    tier_hits = TierHits()
    if reuse_store:
        located, pending, upgraded, carried, known = _resolve_by_location(
            store, entries, locations, include_keyframe
        )
        tier_hits.location = len(located)
        resolved.update(located)
        fingerprints.update(known)
        named, pending, name_upgraded, carried, known = _resolve_by_name(
            store, root_dir, pending, include_keyframe, _prescan_rows(prescan), carried
        )
        tier_hits.name = len(named)
        resolved.update(named)
        fingerprints.update(known)
        store.apply((), [*upgraded, *name_upgraded], root_dir)
        moved, pending, known = _resolve_by_fingerprint(
            store,
            root_dir,
            pending,
            include_keyframe,
            known_fingerprints=fingerprints,
            renamed_out=renamed_out,
        )
        tier_hits.fingerprint = len(moved)
        resolved.update(moved)
        fingerprints.update(known)
    reused_count = len(resolved)
    logger.info(
        "%s",
        format_lookup_summary(
            location=tier_hits.location, name=tier_hits.name, fingerprint=tier_hits.fingerprint
        ),
    )

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
    fingerprints.update(_store_fresh(store, root_dir, fresh, by_name, fingerprints, raw))
    # - 확인한 파일의 위치를 남겨 다음 조회가 파일 읽기 없이 위치로 찾게 함(이름 키와 지문 단계에서 찾은 파일 포함)
    store.record_locations(_location_records(entries, locations, fingerprints))
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

    - 분석과 같은 순서로 볼륨 위치와 이름 키를 먼저 대조해 같은 결과를 내되 지문 계산은 생략(예상용)
    """
    entries = list_file_entries(
        root_dir,
        recursive=recursive,
        excluded_dirnames=excluded_dirnames,
    )
    if store is None:
        return len(entries)
    _located, pending, _upgraded, _carried, _known = _resolve_by_location(
        store, entries, _entry_locations(root_dir, entries), include_keyframe
    )
    _named, pending, _upgraded, _carried, _known = _resolve_by_name(
        store, root_dir, pending, include_keyframe
    )
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
    store.apply((), entries, root_dir)


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
class RestoreScope:
    """판정이 훑은 작업 폴더 범위. 바로 잇는 분석이 같은 범위일 때만 판정 결과를 재사용"""

    root_dir: str
    recursive: bool
    excluded_dirnames: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class StoredRows:
    """저장된 결과만으로 만든 표시용 행: 메타데이터, 이전 형식 파일명, 키프레임 포함 여부

    - 판정 단계에서 읽은 파일 목록과 DB 행을 함께 담아, 바로 잇는 분석이 목록과 행을 다시 읽지 않게 함
    """

    rows: tuple[VideoProps, ...]
    legacy_names: frozenset[str]
    include_keyframe: bool
    file_entries: tuple[FileEntry, ...] = ()
    stored_entries: tuple[StoredEntry, ...] = ()
    scope: RestoreScope | None = None

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

    @property
    def dir_signature(self) -> tuple[tuple[str, int, int], ...] | None:
        """판정 때 읽은 파일 목록으로 만든 디렉터리 시그니처, 목록이 없으면 None(호출자가 직접 계산)"""
        if not self.file_entries:
            return None
        return tuple((entry.filename, entry.filesize_bytes, entry.mtime_ns) for entry in self.file_entries)

    def covers(self, root_dir: str, *, recursive: bool, excluded_dirnames: Collection[str]) -> bool:
        """판정 때 훑은 범위가 지금 분석할 범위와 같은지(다르면 판정 목록을 재사용하지 않음)"""
        if self.scope is None:
            return False
        return (
            os.path.normcase(os.path.normpath(self.scope.root_dir))
            == os.path.normcase(os.path.normpath(root_dir))
            and self.scope.recursive == recursive
            and set(self.scope.excluded_dirnames) == set(excluded_dirnames)
        )


def _upgrade_props(hit: StoredEntry) -> VideoProps:
    """이전 버전 행을 번호만 올려 재사용할 때 저장된 응답으로 빈 스트림 요약을 보완"""
    if not hit.props.stream_summary and hit.probe_raw is not None:
        summary = summarize_streams_from_probe(decompress_probe_response(hit.probe_raw))
        if summary:
            return replace(hit.props, stream_summary=summary)
    return hit.props


def _display_props(hit: StoredEntry) -> tuple[VideoProps, bool]:
    """표시용 메타데이터와 이전 형식 여부. 다시 읽을 행은 저장된 응답으로 계산하고, 그 밖의 옛 행은 저장 값 그대로 표시"""
    action = row_reuse_action(hit.probe_version, hit.filename)
    if action in REUSABLE_ROW_ACTIONS:
        return _upgrade_props(hit), False
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

    - 판정 단계이므로 DB에 쓰지 않음(결정 30). 볼륨 위치 조회와 이름 키 조회만 쓰고 지문 읽기와 프로빙은 하지 않음
    - 이전 형식 행은 저장 값 그대로 표시하고 이름을 legacy_names에 담음
    - 묶음마다 확인한 파일 수를 알리고, 취소되면 None. 볼륨 위치 키도 묶음마다 계산해 첫 알림 전에 모든 파일을 훑지 않음
    - 파일 목록, 조회한 행, 훑은 범위를 결과에 담아 바로 잇는 분석이 같은 조회와 목록을 다시 하지 않게 함
    """
    entries = list_file_entries(root_dir, recursive=recursive, excluded_dirnames=excluded_dirnames)
    rows: list[VideoProps] = []
    legacy: set[str] = set()
    hits: list[StoredEntry] = []
    for start in range(0, len(entries), _DISPLAY_CHUNK_SIZE):
        if is_cancelled is not None and is_cancelled():
            return None
        chunk = entries[start : start + _DISPLAY_CHUNK_SIZE]
        # - 볼륨 위치 계산은 폴더 단위로 캐시한 드라이브 조회를 쓰지만 묶음마다 취소 확인 뒤에 계산해 첫 진행 알림을 늦추지 않게 함
        located = _location_hits(store, chunk, _entry_locations(root_dir, chunk))
        pending = [entry for entry in chunk if entry.filename not in located]
        index = _scope_index(store.fetch_by_names([entry.filename for entry in pending]), root_dir)
        for entry in chunk:
            hit = located.get(entry.filename)
            if hit is None:
                hit = index.get((entry.filename, entry.filesize_bytes, entry.mtime_ns))
            if hit is None:
                continue
            hits.append(hit)
            props, is_legacy = _display_props(hit)
            rows.append(props)
            if is_legacy:
                legacy.add(entry.filename)
        if on_progress is not None:
            on_progress(min(start + _DISPLAY_CHUNK_SIZE, len(entries)))
    include_keyframe = bool(rows) and all(props.keyframe_interval is not None for props in rows)
    scope = RestoreScope(root_dir, recursive, tuple(excluded_dirnames))
    return StoredRows(
        tuple(rows), frozenset(legacy), include_keyframe, tuple(entries), tuple(hits), scope
    )


def _carried_keyframe(hit: StoredEntry) -> float | None:
    """현재 키프레임 측정 방법으로 저장된 간격이 있으면 그 값(메타데이터만 다시 읽을 때 이어 받음)"""
    if hit.keyframe_version != KEYFRAME_MEASURE_VERSION:
        return None
    return hit.props.keyframe_interval


def _prescan_rows(prescan: StoredRows | None) -> Sequence[StoredEntry] | None:
    """판정이 읽은 저장 행 목록(없으면 None). 분석이 같은 행 조회를 다시 하지 않게 넘김"""
    if prescan is None or not prescan.stored_entries:
        return None
    return prescan.stored_entries


def _scope_index(stored: Sequence[StoredEntry], root_dir: str) -> dict[tuple[str, int, int], StoredEntry]:
    """식별 키별로 지금 작업 폴더 범위의 행을 고르고, 없으면 범위를 모르는 옛 행을 고름

    - 같은 이름, 크기, 수정 시각의 키가 작업 폴더마다 따로 있을 수 있어 범위가 다른 행은 다른 파일이므로 쓰지 않음
    - 옛 표기(폴더 순서가 다르거나 상대 경로로 저장한 범위)도 폴더 단위로 비교해 함께 찾음
    """
    scope = root_scope_key(root_dir)
    index: dict[tuple[str, int, int], StoredEntry] = {}
    for entry in stored:
        if entry.root_scope and not scope_matches(entry.root_scope, scope):
            continue
        current = index.get(entry.key)
        if current is None or (current.root_scope != scope and entry.root_scope == scope):
            index[entry.key] = entry
    return index


def _reuse_hit(hit: StoredEntry, include_keyframe: bool) -> tuple[VideoProps, bool] | None:
    """행을 그대로 쓰거나 저장된 응답으로 다시 계산할 수 있으면 메타데이터와 다시 저장 필요 여부를 반환

    - 측정 번호가 낮은 행은 번호만 올려 다시 저장해 다음 조회에서 승격 판정을 되풀이하지 않게 함
    """
    if _is_reusable(hit, include_keyframe):
        props = _upgrade_props(hit)
        return props, props != hit.props or hit.probe_version != PROBE_VERSION
    if _recomputable(hit, include_keyframe):
        recomputed = _recompute_from_raw(hit, include_keyframe)
        return recomputed.props, True
    return None


def _entry_locations(root_dir: str, entries: Sequence[FileEntry]) -> dict[str, LocationKey]:
    """파일별 볼륨 위치 키. 논리 파일명이 아니라 파일의 절대 경로로 계산해 다중 작업 폴더에서도 소속을 가리지 않음"""
    return {
        entry.filename: _location_key(resolve_source_filepath(root_dir, entry.filename))
        for entry in entries
    }


def _location_key(path: str) -> LocationKey:
    """파일 경로의 볼륨 위치 키(볼륨 식별자, 볼륨 기준 상대 경로)"""
    location = volume_location(path)
    return (location.volume_id, location.volume_path)


def _content_hit(content: StoredEntry, entry: FileEntry, fingerprint: str) -> StoredEntry:
    """내용 행을 현재 파일의 행으로 바꿈(파일명과 수정 시각은 현재 파일 값, 내용은 저장 값)"""
    return StoredEntry(
        entry.filename,
        entry.filesize_bytes,
        entry.mtime_ns,
        fingerprint,
        replace(content.props, filename=entry.filename),
        probe_version=content.probe_version,
        keyframe_version=content.keyframe_version,
        probe_raw=content.probe_raw,
    )


def _location_hits(
    store: AnalysisStore, entries: Sequence[FileEntry], locations: Mapping[str, LocationKey]
) -> dict[str, StoredEntry]:
    """위치 행이 현재 파일과 같은 크기, 수정 시각을 가리키면 그 내용 행을 현재 파일 기준으로 돌려줌(파일 읽기 없음)"""
    if not entries:
        return {}
    records = {
        (record.volume_id, record.volume_path): record
        for record in store.fetch_locations([locations[entry.filename] for entry in entries])
    }
    matches: list[tuple[FileEntry, LocationRecord]] = []
    for entry in entries:
        record = records.get(locations[entry.filename])
        if record is None:
            continue
        if record.filesize_bytes != entry.filesize_bytes or record.mtime_ns != entry.mtime_ns:
            continue
        matches.append((entry, record))
    contents = {
        (content.filesize_bytes, content.fingerprint): content
        for content in store.fetch_by_contents([(record.filesize_bytes, record.fingerprint) for _, record in matches])
    }
    hits: dict[str, StoredEntry] = {}
    for entry, record in matches:
        content = contents.get((record.filesize_bytes, record.fingerprint))
        if content is not None:
            hits[entry.filename] = _content_hit(content, entry, record.fingerprint)
    return hits


def _location_records(
    entries: Sequence[FileEntry], locations: Mapping[str, LocationKey], fingerprints: Mapping[str, str]
) -> list[LocationRecord]:
    """확인된 파일 위치를 저장할 위치 행 목록. 지문을 모르는 파일은 위치 행을 남기지 않음"""
    records: list[LocationRecord] = []
    for entry in entries:
        fingerprint = fingerprints.get(entry.filename)
        key = locations.get(entry.filename)
        if fingerprint is None or key is None:
            continue
        records.append(LocationRecord(key[0], key[1], entry.filesize_bytes, entry.mtime_ns, fingerprint))
    return records


def _resolve_by_location(
    store: AnalysisStore,
    entries: Sequence[FileEntry],
    locations: Mapping[str, LocationKey],
    include_keyframe: bool,
) -> tuple[dict[str, VideoProps], list[FileEntry], list[StoredEntry], dict[str, float], dict[str, str]]:
    """볼륨 위치가 같은 행을 재사용하고, 나머지는 다음 조회 단계로 넘김

    - 반환값은 (재사용 결과, 남은 파일, 번호를 올려 저장할 행, 이어 받을 키프레임 값, 확인한 지문)
    - 읽기만 하므로 복원 판정에서도 씀
    """
    hits = _location_hits(store, entries, locations)
    resolved: dict[str, VideoProps] = {}
    pending: list[FileEntry] = []
    upgraded: list[StoredEntry] = []
    carried: dict[str, float] = {}
    fingerprints: dict[str, str] = {}
    for entry in entries:
        hit = hits.get(entry.filename)
        if hit is None:
            pending.append(entry)
            continue
        fingerprints[entry.filename] = hit.fingerprint
        reused = _reuse_hit(hit, include_keyframe)
        if reused is not None:
            props, changed = reused
            resolved[entry.filename] = _for_request(props, include_keyframe)
            if changed:
                upgraded.append(replace(hit, props=props))
            continue
        pending.append(entry)
        kept = _carried_keyframe(hit) if include_keyframe else None
        if kept is not None:
            carried[entry.filename] = kept
    return resolved, pending, upgraded, carried, fingerprints


def _resolve_by_name(
    store: AnalysisStore,
    root_dir: str,
    entries: Sequence[FileEntry],
    include_keyframe: bool,
    prescan_rows: Sequence[StoredEntry] | None = None,
    carried: dict[str, float] | None = None,
) -> tuple[dict[str, VideoProps], list[FileEntry], list[StoredEntry], dict[str, float], dict[str, str]]:
    """이름과 식별 키가 같은 행을 재사용하고, 번호만 올려 써도 되는 옛 행은 현재 번호로 다시 저장할 목록을 반환

    - 반환값은 (재사용 결과, 남은 파일, 번호를 올려 저장할 행, 이어 받을 키프레임 값, 확인한 지문)
    - prescan_rows가 있으면 판정이 읽은 같은 행을 그대로 쓰고 DB를 다시 조회하지 않음. 이름이 같고 식별 키가 같은 행만 쓰므로 결과가 같음
    """
    stored = prescan_rows if prescan_rows is not None else store.fetch_by_names([entry.filename for entry in entries])
    index = _scope_index(stored, root_dir)

    resolved: dict[str, VideoProps] = {}
    pending: list[FileEntry] = []
    upgraded: list[StoredEntry] = []
    fingerprints: dict[str, str] = {}
    carried_values = {} if carried is None else carried
    for entry in entries:
        hit = index.get((entry.filename, entry.filesize_bytes, entry.mtime_ns))
        if hit is not None:
            fingerprints[entry.filename] = hit.fingerprint
            reused = _reuse_hit(hit, include_keyframe)
            if reused is not None:
                props, changed = reused
                resolved[entry.filename] = _for_request(props, include_keyframe)
                if changed:
                    upgraded.append(replace(hit, props=props))
                continue
        pending.append(entry)
        kept = _carried_keyframe(hit) if hit is not None and include_keyframe else None
        if kept is not None:
            carried_values[entry.filename] = kept
    return resolved, pending, upgraded, carried_values, fingerprints


def _resolve_by_fingerprint(
    store: AnalysisStore,
    root_dir: str,
    pending: Sequence[FileEntry],
    include_keyframe: bool,
    *,
    known_fingerprints: Mapping[str, str] | None = None,
    renamed_out: dict[str, str] | None = None,
) -> tuple[dict[str, VideoProps], list[FileEntry], dict[str, str]]:
    """이름이 바뀐 파일을 같은 크기 후보의 내용 지문으로 찾아 결과와 지문을 돌려줌

    - 옛 이름의 조회 키는 지우지 않고, 사라진 옛 경로의 위치 행만 지워 새 위치 행으로 대체
    """
    known = dict(known_fingerprints or {})
    if not pending:
        return {}, [], known
    scope = root_scope_key(root_dir)
    candidates = store.fetch_by_sizes([entry.filesize_bytes for entry in pending])
    by_size: dict[int, list[StoredEntry]] = {}
    for candidate in candidates:
        # - 훑는 범위 밖 작업 폴더의 같은 크기 행과 짝지으면 다른 파일을 이름 변경으로 기록하므로 범위 행만 씀
        if candidate.root_scope and not scope_matches(candidate.root_scope, scope):
            continue
        if _is_reusable(candidate, include_keyframe):
            by_size.setdefault(candidate.filesize_bytes, []).append(candidate)

    missing = [
        entry for entry in pending if entry.filename not in known and entry.filesize_bytes in by_size
    ]
    fingerprints = {**known, **_fingerprints_for(root_dir, missing)}

    resolved: dict[str, VideoProps] = {}
    moved: list[tuple[StoredEntry, FileEntry]] = []
    remaining: list[FileEntry] = []
    used: set[tuple[str, int, int]] = set()
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
        upgraded_props = replace(_upgrade_props(match), filename=entry.filename)
        resolved[entry.filename] = _for_request(upgraded_props, include_keyframe)

    if moved:
        store.apply(
            (),
            [
                StoredEntry(
                    entry.filename,
                    entry.filesize_bytes,
                    entry.mtime_ns,
                    match.fingerprint,
                    replace(_upgrade_props(match), filename=entry.filename),
                    probe_raw=match.probe_raw,
                )
                for match, entry in moved
            ],
            root_dir,
        )
        store.remove_locations(_stale_location_keys(root_dir, moved))
    return resolved, remaining, fingerprints


def _stale_location_keys(root_dir: str, moved: Sequence[tuple[StoredEntry, FileEntry]]) -> list[LocationKey]:
    """이름이 바뀐 옛 경로의 위치 키 중 실제 파일이 사라진 것만 고름(복사로 남은 경로의 위치는 유지)"""
    stale: list[LocationKey] = []
    for match, _entry in moved:
        old_path = resolve_source_filepath(root_dir, match.filename)
        if os.path.exists(old_path):
            continue
        stale.append(_location_key(old_path))
    return stale


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
) -> dict[str, str]:
    """새로 분석한 결과를 저장하고, 저장에 쓴 파일별 지문을 돌려줌(위치 행 기록에 재사용)"""
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
    store.apply((), records, root_dir)
    return all_fingerprints
