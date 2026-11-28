"""비디오 스캔, 캐시 동기화 및 종합 평가 리포트 생성 서비스 모듈"""

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.core.analysis.file_manager import get_video_filenames
from src.core.analysis.frame_analysis import (
    DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    FRAME_ANALYSIS_VERSION,
    FrameAnalysisThresholds,
    analysis_version_for_mode,
    apply_no_bars_ratio,
    frame_thresholds_from_config,
    is_usable_analysis_version,
    is_vr_protected,
    rejudge_candidate_tier,
    resolve_crop_frame,
    review_holds_automatic_crop,
)
from src.core.analysis.prober import CancelCheck, ProgressCallback, resolve_ffprobe_executable
from src.core.config import ClassifierConfig
from src.core.encode.encode_impact import parse_stream_summary_layout
from src.core.encode.encode_paths import excluded_folder_names
from src.core.evaluation.classification_policy import (
    DEFAULT_CLASSIFICATION_POLICIES,
    ClassificationPolicy,
    ExceptionRule,
    make_legacy_format_policy,
)
from src.core.evaluation.evaluator import VideoEvaluator
from src.core.exceptions import DomainFileNotFoundError, ErrorCode
from src.core.messages import format_invalid_root_dir_msg
from src.core.models import ActiveFrame, SummaryReport, VideoProps
from src.core.quality_probe import METRIC_VMAF
from src.core.workspace_paths import are_all_valid_dirs, parse_root_dirs, resolve_source_filepath
from src.service.analysis_probe import FileEntry, StoredRows, count_pending_files
from src.service.analysis_store import AnalysisStore, FileKey
from src.service.analysis_work_records import ManualTargetSync
from src.service.cache import DEFAULT_CACHE_REGISTRY, CacheRegistry, DirSignature, TableCache
from src.service.designation_content import ContentIdentity
from src.service.encode.encode_output_history import EncodeOutputHistory
from src.service.frame_analysis_record import FrameAnalysisRecord
from src.service.frame_analysis_store import FrameAnalysisStore
from src.service.frame_override_rows import review_region_box


@dataclass(frozen=True, slots=True)
class DirScanInputs:
    """백그라운드 시그니처 점검에 넘기는 값 복사본(캐시와 Qt 객체 참조를 담지 않음)"""

    root_dir: str
    recursive: bool
    excluded_dirnames: tuple[str, ...]
    include_keyframe: bool
    saved_signature: DirSignature | None


def _policies_for(legacy_names: frozenset[str]) -> tuple[ClassificationPolicy, ...] | None:
    """이전 형식 파일이 있으면 그 정책을 맨 앞에 둔 판정 체인, 없으면 기본 체인(None)"""
    if not legacy_names:
        return None
    return (make_legacy_format_policy(legacy_names), *DEFAULT_CLASSIFICATION_POLICIES)


def _resolve_encoded_evidence_for(
    root_dir: str,
    videos: Sequence[VideoProps],
    db_path: str | None,
    current_name: str,
) -> tuple[tuple[str, ...], frozenset[str]]:
    """인코딩 이력 DB에서 태그 이름 목록과 지문 일치 파일명 집합을 조회"""
    if db_path is None:
        return (current_name,), frozenset()
    out_hist = EncodeOutputHistory(db_path)
    try:
        tag_names = tuple(dict.fromkeys((current_name, *out_hist.list_tag_names())))
        path_to_name: dict[str, str] = {}
        candidates: dict[str, int] = {}
        for video in videos:
            if video.fingerprint_lookup_size <= 0:
                continue
            path = resolve_source_filepath(root_dir, video.filename)
            path_to_name[path] = video.filename
            candidates[path] = video.fingerprint_lookup_size
        matched_paths = out_hist.match_fingerprints(candidates)
        matched_names = frozenset(path_to_name[p] for p in matched_paths if p in path_to_name)
        return tag_names, matched_names
    finally:
        out_hist.close()


@dataclass(frozen=True, slots=True)
class FrameCacheView:
    """화질 분석 캐시에서 뽑은 잘라내기 확정 영상, 축소 후보, 사용자 지정 활성 화면 맵

    - manual_frames는 사용자 지정 활성 화면이 잘라내기에 실제로 적용된 영상만 담음(잘라내기 제외 영상은 빠짐)
    """

    active_frames: dict[str, ActiveFrame]
    frame_targets: dict[str, str]
    manual_frames: dict[str, ActiveFrame]


def _resolve_frame_cache_for(
    root_dir: str,
    videos: Sequence[VideoProps],
    db_path: str | None,
    *,
    analysis_version: int = FRAME_ANALYSIS_VERSION,
    crop_enabled: bool = True,
    thresholds: FrameAnalysisThresholds = DEFAULT_FRAME_ANALYSIS_THRESHOLDS,
    frame_targets_enabled: bool = True,
) -> FrameCacheView:
    """화질 분석 캐시에서 잘라내기 확정 영상과 축소 후보 규격을 파일명별로 조회

    - 화질 분석 행도 사용자 지정 활성 화면도 없으면 원본 파일 stat을 하지 않고 빈 결과를 돌려줌
    - 화질 분석 행이 없어도 저장된 사용자 지정 활성 화면은 직접 지정 판정에 넘김
    - 현재 파일 크기와 수정 시각이 같은 행만 써서 바뀐 파일의 낡은 측정을 쓰지 않음
    - 잘라내기를 쓰지 않는 영상(설정 끔, VR 보호, 검은 여백 없음)은 축소 후보도 반영하지 않음
    - 사용자 지정 활성 화면은 자동 감지 결과와 영상별 제외를 덮어씀
    - 어두운 근거로 지점을 무시한 영상은 사용자가 확인할 때까지 영상별 제외와 같은 경로로 자동 크롭을 보류
    - 활성 화면 면적이 현재 무시 상한을 넘으면 전체 프레임으로 되돌려 판정
    """
    empty = FrameCacheView({}, {}, {})
    if db_path is None:
        return empty
    store = FrameAnalysisStore(db_path)
    try:
        if store.count() == 0 and store.count_manual_active_frames() == 0:
            return empty
        entries: list[tuple[tuple[str, int, int], str]] = []
        path_by_name: dict[str, str] = {}
        size_by_name: dict[str, tuple[int, int]] = {}
        for video in videos:
            path = resolve_source_filepath(root_dir, video.filename)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            path_by_name[video.filename] = path
            size_by_name[video.filename] = (video.width, video.height)
            entries.append(((video.filename, stat.st_size, stat.st_mtime_ns), path))
        excluded = store.load_crop_exclusions(list(path_by_name.values()))
        manual = store.load_manual_active_frames(
            {path: size_by_name[name] for name, path in path_by_name.items()}
        )
        # - VR 보호는 경로별 지정이라, 내용이 같아 한 행을 공유하는 다른 사본에 그대로 적용되지 않게 요청별로 판정
        manual_vr = store.load_vr_overrides(list(path_by_name.values()))
        expected_vr: dict[str, bool] = {}
        for video in videos:
            path = path_by_name.get(video.filename)
            if path is None:
                continue
            layout = parse_stream_summary_layout(video.stream_summary)
            expected_vr[video.filename] = is_vr_protected(
                video.width,
                video.height,
                has_vr_metadata=layout is not None and layout.is_vr,
                manual_vr=path in manual_vr,
            )
        fingerprints = store.resolve_content_keys(entries)
        records: dict[str, FrameAnalysisRecord] = {}
        contents = store.load_many(
            [(key[1], fingerprint) for key, fingerprint in fingerprints.items()]
        )
        for key, _path in entries:
            fingerprint = fingerprints.get(key)
            if fingerprint is None:
                continue
            record = contents.get((key[1], fingerprint))
            if record is not None:
                records[key[0]] = record
        regions = {
            path_by_name[name]: review_region_box(
                apply_no_bars_ratio(
                    record.active_region,
                    *size_by_name[name],
                    thresholds=thresholds,
                )
            )
            for name, record in records.items()
            if name in path_by_name
        }
        acknowledged = store.load_crop_review_acknowledged(regions) if regions else frozenset()
        frames: dict[str, ActiveFrame] = {}
        frame_targets: dict[str, str] = {}
        manual_frames: dict[str, ActiveFrame] = {}
        for name, record in records.items():
            if not is_usable_analysis_version(record.analysis_version, analysis_version):
                continue
            width, height = size_by_name.get(name, (0, 0))
            region = apply_no_bars_ratio(
                record.active_region, width, height, thresholds=thresholds
            )
            path = path_by_name.get(name, "")
            # - 사람 확인이 필요한 자동 크롭은 영상별 제외와 같은 경로로 전체 프레임 판정을 받게 함
            vr_protected = expected_vr.get(name, False)
            review_hold = review_holds_automatic_crop(
                region,
                crop_enabled=crop_enabled,
                is_vr_protected=vr_protected,
                manual_frame=manual.get(path),
                acknowledged=path in acknowledged,
            )
            frame = resolve_crop_frame(
                region,
                crop_enabled=crop_enabled,
                excluded=path in excluded or review_hold,
                is_vr_protected=vr_protected,
                manual_frame=manual.get(path),
            )
            if frame is not None:
                frames[name] = frame
            # - 사용자 지정 활성 화면이 이번 판정에서 실제로 잘라내기에 적용된 영상만 직접 지정 판정에 넘김
            if frame is not None and manual.get(path) == frame:
                manual_frames[name] = frame
            if not frame_targets_enabled:
                continue
            candidate = record.candidate_tier
            if record.tier_scores:
                candidate = rejudge_candidate_tier(
                    record.tier_scores,
                    record.candidate_tier,
                    use_vmaf=record.metric_used == METRIC_VMAF,
                    thresholds=thresholds,
                )
            if not candidate:
                continue
            # - 화질 분석은 자동 감지 영역으로 쟀으므로 사용자 지정 활성 화면과는 기준이 다름
            if (frame is not None and manual.get(path) is None) or (
                frame is None and not region.has_black_bars
            ):
                frame_targets[name] = candidate
        # - 화질 분석 행이 없는 영상도 사용자 지정 활성 화면만으로 직접 지정 판정을 받게 함
        for name, path in path_by_name.items():
            manual_frame = manual.get(path)
            if manual_frame is None or name in manual_frames:
                continue
            if not crop_enabled or expected_vr.get(name, False):
                continue
            frames[name] = manual_frame
            manual_frames[name] = manual_frame
        return FrameCacheView(frames, frame_targets, manual_frames)
    finally:
        store.close()


class VideoClassifierService:
    """비디오 스캔, 작업 폴더별 캐시 동기화, 종합 평가 리포트 생성을 처리하는 서비스"""

    def __init__(
        self,
        root_dir: str,
        config: ClassifierConfig | None = None,
        on_progress: ProgressCallback = None,
        on_is_cancelled: CancelCheck = None,
        registry: CacheRegistry = DEFAULT_CACHE_REGISTRY,
        store: AnalysisStore | None = None,
    ) -> None:
        if not root_dir or not are_all_valid_dirs(root_dir):
            raise DomainFileNotFoundError(
                format_invalid_root_dir_msg(root_dir),
                code=ErrorCode.ROOT_DIR_INVALID,
            )

        self.root_dir: str = root_dir
        self.config: ClassifierConfig = config or ClassifierConfig()
        self.on_progress: ProgressCallback = on_progress
        self.on_is_cancelled: CancelCheck = on_is_cancelled

        self.include_keyframe: bool = False
        self.recursive: bool = False
        self.exception_rules: list[ExceptionRule] = []
        # - 현재 작업 폴더의 파일명 -> 규격 키 또는 축소 안 함 직접 지정 맵, 세션이 동기화
        self.manual_targets: dict[str, str] = {}

        self._registry: CacheRegistry = registry
        self._store: AnalysisStore | None = store
        self._reuse_store: bool = True
        self.sync_cache_state()

    @property
    def registry(self) -> CacheRegistry:
        """이 서비스가 캐시를 조회, 저장하는 레지스트리 인스턴스 반환"""
        return self._registry

    @property
    def root_dirs(self) -> tuple[str, ...]:
        """현재 작업 폴더 목록(주 작업 폴더가 첫 번째)"""
        return parse_root_dirs(self.root_dir)

    def _excluded_dirnames(self) -> tuple[str, ...]:
        """스캔에서 뺄 인코딩 폴더 이름(보관 폴더, 별도 출력 폴더)"""
        return excluded_folder_names(self.config.execution)

    @property
    def excluded_dirnames(self) -> tuple[str, ...]:
        """이 서비스의 스캔 범위에서 빼는 폴더 이름. 시작 복원 판정이 같은 범위를 쓰도록 공개"""
        return self._excluded_dirnames()

    def sync_cache_state(self) -> None:
        """외부 파사드에서 작업 경로 변경 후 캐시 일관성을 동기화"""
        self._registry.drop_if_stale(
            self.root_dir, recursive=self.recursive, excluded_dirnames=self._excluded_dirnames()
        )

    def external_scan_inputs(self) -> DirScanInputs | None:
        """백그라운드 시그니처 점검에 넘길 값 복사본, 등록된 캐시가 없으면 None"""
        cache = self.cache
        if cache is None:
            return None
        return DirScanInputs(
            root_dir=self.root_dir,
            recursive=self.recursive,
            excluded_dirnames=self._excluded_dirnames(),
            include_keyframe=cache.include_keyframe,
            saved_signature=self._registry.saved_signature(self.root_dir, recursive=self.recursive),
        )

    def prune_missing(self, current: DirSignature) -> int:
        """삭제만 있을 때 사라진 파일의 행을 캐시에서 빼고, 저장 시그니처를 점검이 만든 현재 값으로 맞춤

        시그니처를 맞추지 않으면 다음 점검마다 변경됨으로 판정되어 반복 재평가가 생김
        """
        cache = self.cache
        if cache is None:
            return 0
        names = {name for name, _size, _mtime in current}
        removed = cache.drop_files([vid.filename for vid in cache.data if vid.filename not in names])
        self._registry.store_signature(self.root_dir, current, recursive=self.recursive)
        return removed

    def invalidate_cache(self) -> None:
        """인메모리 캐시를 비우고 다음 분석은 분석 캐시 행을 읽지 않고 새 결과로 덮어쓰도록 설정"""
        self._registry.invalidate(self.root_dir, recursive=self.recursive)
        self._reuse_store = False

    def close(self) -> None:
        """분석 DB 연결을 닫아 파일 핸들을 놓음. 이후 DB 접근은 필요할 때 다시 연결"""
        if self._store is not None:
            self._store.close()

    def prepare_cache(
        self, force_keyframe: bool = False, *, prescan: StoredRows | None = None
    ) -> TableCache:
        """인메모리 캐시의 유효성을 점검하고, 필요한 경우 키프레임 간격 정보를 추가로 로드하여 준비

        prescan은 시작 복원 판정이 이미 읽은 파일 목록과 저장 행. 목록 범위가 같은 때만 받고,
        넘어오면 행 조회와 디렉터리 재조회를 건너뛰어 새 분석이 같은 일을 두 번 하지 않게 함
        """
        need_keyframe: bool = self.include_keyframe or force_keyframe
        # - 판정 범위가 지금 분석 범위와 같을 때만 읽은 행과 목록을 재사용. 범위가 다르면 종전처럼 직접 조회
        reusable = (
            prescan
            if prescan is not None
            and self._reuse_store
            and prescan.covers(
                self.root_dir,
                recursive=self.recursive,
                excluded_dirnames=self._excluded_dirnames(),
            )
            else None
        )
        # - 판정이 방금 나열한 목록은 디스크의 현재 상태이므로, 그 값으로 시그니처를 비교해 폴더를 다시 훑지 않음
        self._registry.drop_if_stale(
            self.root_dir,
            recursive=self.recursive,
            excluded_dirnames=self._excluded_dirnames(),
            current=reusable.dir_signature if reusable is not None else None,
        )

        cache = self._registry.get(self.root_dir, recursive=self.recursive)
        # - 이전 형식 행이 있는 캐시는 저장 결과 표시용이므로 다시 조회. 이전 형식 행만 프로빙되고 나머지는 저장 행을 재사용
        if cache is None or cache.legacy_names:
            entries: list[FileEntry] = []
            cache = TableCache.probe(
                self.root_dir,
                include_keyframe=need_keyframe,
                on_progress=self.on_progress,
                is_cancelled=self.on_is_cancelled,
                store=self._store,
                reuse_store=self._reuse_store,
                recursive=self.recursive,
                excluded_dirnames=self._excluded_dirnames(),
                prescan=reusable,
                entries_out=entries,
                ffprobe=resolve_ffprobe_executable(self.config.execution.ffprobe_path),
            )
            self._reuse_store = True
            self._registry.put(
                self.root_dir,
                cache,
                signature=self._probed_signature(entries),
                recursive=self.recursive,
                excluded_dirnames=self._excluded_dirnames(),
            )
        else:
            cache.update_keyframe(
                need_keyframe,
                on_progress=self.on_progress,
                is_cancelled=self.on_is_cancelled,
                store=self._store,
                ffprobe=resolve_ffprobe_executable(self.config.execution.ffprobe_path),
            )

        return cache

    def _probed_signature(self, entries: Sequence[FileEntry]) -> DirSignature | None:
        """방금 조회한 파일 목록의 디렉터리 시그니처, 목록을 읽지 않았으면 None으로 저장소가 직접 계산하게 함

        - 분석 캐시를 쓰지 않는 조회는 목록을 남기지 않으므로 그때는 None
        """
        if self._store is None:
            return None
        return tuple((entry.filename, entry.filesize_bytes, entry.mtime_ns) for entry in entries)

    @property
    def cache(self) -> TableCache | None:
        """현재 작업 폴더 범위의 인메모리 캐시 인스턴스 반환"""
        return self._registry.get(self.root_dir, recursive=self.recursive)

    def pending_count(self, include_keyframe: bool) -> int:
        """분석 시 ffprobe가 필요한 영상 수, 인메모리 캐시가 요청 범위를 이미 덮으면 0"""
        cache = self.cache
        if cache is not None and cache.legacy_names:
            # - 이전 형식 행은 항상 다시 읽고, 키프레임 분석이면 키프레임 값이 없는 나머지 행도 함께 읽음
            keyframe_gap = (
                sum(
                    1
                    for vid in cache.data
                    if vid.filename not in cache.legacy_names and vid.keyframe_interval is None
                )
                if include_keyframe
                else 0
            )
            return len(cache.legacy_names) + keyframe_gap
        if cache is not None and (
            not include_keyframe or all(v.keyframe_interval is not None for v in cache.data)
        ):
            return 0
        return count_pending_files(
            self._store,
            self.root_dir,
            include_keyframe=include_keyframe,
            recursive=self.recursive,
            excluded_dirnames=self._excluded_dirnames(),
        )

    def known_video_filenames(self) -> set[str]:
        """작업 폴더에 실제로 있는 비디오 논리 파일명 집합 반환"""
        return set(
            get_video_filenames(
                self.root_dir, recursive=self.recursive, excluded_dirnames=self._excluded_dirnames()
            )
        )

    def designation_sizes(self) -> set[int]:
        """지정 행(경로 지정 표와 출력 해상도 직접 지정)이 남긴 파일 크기 집합

        - 내용이 같은 파일은 크기도 같으므로, 스캔이 이 크기의 파일만 지문을 확인하게 좁힘
        """
        db_path = self._store.db_path if self._store is not None else None
        if db_path is None:
            return set()
        sizes = self._store.manual_target_sizes() if self._store is not None else set()
        store = FrameAnalysisStore(db_path)
        try:
            return sizes | store.designation_sizes()
        finally:
            store.close()

    def refresh_manual_target_identities(self, filenames: Sequence[str]) -> int:
        """현재 작업 폴더 지정 행의 내용 키를 지금 파일에서 다시 읽어 채움. 채운 행 수 반환

        - 사용자가 그 영상에 지정을 새로 고를 때 불러, 같은 경로의 내용이 바뀐 뒤에도 새 지정이 붙게 함
        - 분석 캐시 저장소가 없으면 지정도 저장되지 않으므로 아무 것도 하지 않음
        """
        if self._store is None:
            return 0
        return self._store.refresh_manual_target_identities(self.root_dir, filenames)

    def scan_content_identities(self) -> dict[str, ContentIdentity]:
        """작업 폴더 영상 중 지정 행과 크기가 같은 파일의 내용 키(크기, 지문)를 확인

        - 지정 행이 남긴 크기의 파일만 확인해, 지정이 없으면 파일을 읽지 않음
        - 지문은 분석 캐시가 이미 아는 값을 먼저 쓰고 모자란 파일만 읽음(판정마다 같은 영상을 다시 읽지 않게)
        - 지문을 확인하지 못한 파일은 빠져 종전처럼 경로로만 지정을 판정
        """
        cache = self.cache
        if cache is None:
            return {}
        sizes = self.designation_sizes()
        if not sizes:
            return {}
        entries: list[tuple[FileKey, str]] = []
        for video in cache.data:
            path = resolve_source_filepath(self.root_dir, video.filename)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            if stat.st_size not in sizes:
                continue
            entries.append(((video.filename, stat.st_size, stat.st_mtime_ns), path))
        if not entries:
            return {}
        db_path = self._store.db_path if self._store is not None else None
        if db_path is None:
            return {}
        store = FrameAnalysisStore(db_path)
        try:
            fingerprints = store.resolve_content_keys(entries)
        finally:
            store.close()
        return {
            path: (key[1], fingerprint)
            for key, path in entries
            if (fingerprint := fingerprints.get(key)) is not None
        }

    def sync_designations(self) -> ManualTargetSync:
        """스캔이 확인한 내용 키로 영상별 직접 지정을 새 경로(다른 작업 폴더 포함)에 붙임

        - 경로 지정 표(수동 VR 보호, 잘라내기 제외, 사용자 지정 활성 화면)와 출력 해상도 직접 지정을 함께 옮김
        - 옛 경로의 파일이 사라졌으면 옮기고 남아 있으면 복사하며, 이미 지정이 있는 경로는 건드리지 않음
        """
        found = self.scan_content_identities()
        db_path = self._store.db_path if self._store is not None else None
        if db_path is not None and found:
            store = FrameAnalysisStore(db_path)
            try:
                store.sync_designations(found)
            finally:
                store.close()
        if self._store is None:
            return ManualTargetSync()
        return self._store.sync_manual_target_designations(found, self.root_dir)

    def _resolve_encoded_evidence(
        self, videos: Sequence[VideoProps]
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        """인코딩 이력 DB에서 태그 이름 목록과 지문 일치 파일명 집합을 조회"""
        db_path = self._store.db_path if self._store is not None else None
        return _resolve_encoded_evidence_for(
            self.root_dir, videos, db_path, self.config.execution.encode_tag_name
        )

    def evaluate_all(
        self, ensure_keyframe: bool = False, *, prescan: StoredRows | None = None
    ) -> SummaryReport:
        """현재 캐시에 등록된 모든 비디오 대상 종합 평가 수행 및 리포트 반환

        prescan은 시작 복원 판정이 읽은 결과. 캐시가 없어 새로 분석할 때 행 조회와 폴더 재조회를 줄임
        """
        cache = self.prepare_cache(force_keyframe=ensure_keyframe, prescan=prescan)

        tag_names, matched_names = self._resolve_encoded_evidence(cache.data)
        db_path = self._store.db_path if self._store is not None else None
        frame_cache = _resolve_frame_cache_for(
            self.root_dir,
            cache.data,
            db_path,
            analysis_version=analysis_version_for_mode(
                self.config.encode.frame_analysis_mode, self.config.encode.scale_algorithm
            ),
            crop_enabled=self.config.encode.crop_black_bars,
            thresholds=frame_thresholds_from_config(self.config),
            frame_targets_enabled=self.config.encode.auto_apply_frame_target,
        )
        return VideoEvaluator.evaluate_all(
            self.root_dir,
            cache.data,
            self.config,
            self.exception_rules,
            policies=_policies_for(cache.legacy_names),
            manual_targets=self.manual_targets,
            active_frames=frame_cache.active_frames,
            frame_targets=frame_cache.frame_targets,
            known_encode_tag_names=tag_names,
            encoded_history_filenames=matched_names,
            manual_active_frames=frame_cache.manual_frames,
        )

    def install_stored_cache(self, stored: StoredRows) -> None:
        """저장된 결과 행으로 현재 작업 폴더의 인메모리 캐시를 등록(이전 형식 파일명 포함, 분석은 시작하지 않음)

        이미 읽은 파일 목록을 시그니처로 넘겨 폴더를 다시 훑지 않음
        """
        self._registry.put(
            self.root_dir,
            TableCache(
                self.root_dir,
                list(stored.rows),
                include_keyframe=stored.include_keyframe,
                legacy_names=stored.legacy_names,
            ),
            signature=stored.dir_signature,
            recursive=self.recursive,
            excluded_dirnames=self._excluded_dirnames(),
        )


    def cached_report_job(self) -> Callable[[], SummaryReport] | None:
        """메모리 캐시 평가를 GUI 스레드 밖에서 실행할 작업을 만듦, 입력은 호출 시점의 복사본을 씀"""
        cache = self.cache
        if cache is None:
            return None
        videos = list(cache.data)
        root_dir = self.root_dir
        config = self.config
        rules = list(self.exception_rules)
        targets = dict(self.manual_targets)
        policies = _policies_for(cache.legacy_names)
        db_path = self._store.db_path if self._store is not None else None
        current_tag_name = config.execution.encode_tag_name
        active_version = analysis_version_for_mode(
            config.encode.frame_analysis_mode, config.encode.scale_algorithm
        )
        crop_enabled = config.encode.crop_black_bars

        def job() -> SummaryReport:
            tag_names, matched_names = _resolve_encoded_evidence_for(
                root_dir, videos, db_path, current_tag_name
            )
            frame_cache = _resolve_frame_cache_for(
                root_dir,
                videos,
                db_path,
                analysis_version=active_version,
                crop_enabled=crop_enabled,
                thresholds=frame_thresholds_from_config(config),
                frame_targets_enabled=config.encode.auto_apply_frame_target,
            )
            return VideoEvaluator.evaluate_all(
                root_dir,
                videos,
                config,
                rules,
                policies=policies,
                manual_targets=targets,
                active_frames=frame_cache.active_frames,
                frame_targets=frame_cache.frame_targets,
                known_encode_tag_names=tag_names,
                encoded_history_filenames=matched_names,
                manual_active_frames=frame_cache.manual_frames,
            )

        return job

    def evaluate_cached(self) -> SummaryReport | None:
        """디스크 조회 없이 현재 메모리 캐시만으로 평가 리포트를 반환하고 캐시가 없으면 None 반환"""
        cache = self.cache
        if cache is None:
            return None
        return VideoEvaluator.evaluate_all(
            self.root_dir,
            cache.data,
            self.config,
            self.exception_rules,
            manual_targets=self.manual_targets,
            known_encode_tag_names=(self.config.execution.encode_tag_name,),
        )
