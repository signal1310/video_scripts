"""비디오 스캔, 캐시 동기화 및 종합 평가 리포트 생성 서비스 모듈"""

import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.core.analysis.file_manager import get_video_filenames
from src.core.analysis.frame_analysis import (
    FRAME_ANALYSIS_VERSION,
    analysis_version_for_mode,
    is_usable_analysis_version,
    resolve_crop_frame,
)
from src.core.analysis.prober import CancelCheck, ProgressCallback, resolve_ffprobe_executable
from src.core.config import ClassifierConfig
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
from src.core.workspace_paths import are_all_valid_dirs, parse_root_dirs, resolve_source_filepath
from src.service.analysis_probe import StoredRows, count_pending_files
from src.service.analysis_store import AnalysisStore
from src.service.cache import DEFAULT_CACHE_REGISTRY, CacheRegistry, DirSignature, TableCache
from src.service.encode.encode_output_history import EncodeOutputHistory
from src.service.frame_analysis_service import FrameAnalysisStore


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
    """재인코딩 이력 DB에서 태그 이름 목록과 지문 일치 파일명 집합을 조회"""
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


def _resolve_active_frames_for(
    root_dir: str,
    videos: Sequence[VideoProps],
    db_path: str | None,
    *,
    analysis_version: int = FRAME_ANALYSIS_VERSION,
    crop_enabled: bool = True,
) -> dict[str, ActiveFrame]:
    """화질 분석 캐시에서 잘라내기 확정 영상만 파일명별 판정 입력으로 조회

    - 캐시가 비어 있으면 원본 파일 stat을 하지 않고 돌려줌
    - 현재 파일 크기와 수정 시각이 같은 행만 써서 바뀐 파일의 낡은 측정을 쓰지 않음
    - 설정 끔, 영상별 제외, VR 보호, 검은 여백 없음이면 전체 프레임 유지로 보고 넣지 않음
    """
    if db_path is None:
        return {}
    store = FrameAnalysisStore(db_path)
    try:
        if store.count() == 0:
            return {}
        keys: list[tuple[str, int, int]] = []
        path_by_name: dict[str, str] = {}
        for video in videos:
            path = resolve_source_filepath(root_dir, video.filename)
            try:
                stat = os.stat(path)
            except OSError:
                continue
            path_by_name[video.filename] = path
            keys.append((video.filename, stat.st_size, stat.st_mtime_ns))
        excluded = store.load_crop_exclusions(list(path_by_name.values()))
        frames: dict[str, ActiveFrame] = {}
        for key, record in store.load_many(keys).items():
            if not is_usable_analysis_version(record.analysis_version, analysis_version):
                continue
            frame = resolve_crop_frame(
                record.active_region,
                crop_enabled=crop_enabled,
                excluded=path_by_name.get(key[0], "") in excluded,
                is_vr_protected=record.is_vr_protected,
            )
            if frame is not None:
                frames[key[0]] = frame
        return frames
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
        """스캔에서 뺄 재인코딩 폴더 이름(보관 폴더, 별도 출력 폴더)"""
        return excluded_folder_names(self.config.execution)

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

    def prepare_cache(self, force_keyframe: bool = False) -> TableCache:
        """인메모리 캐시의 유효성을 점검하고, 필요한 경우 키프레임 간격 정보를 추가로 로드하여 준비"""
        need_keyframe: bool = self.include_keyframe or force_keyframe
        self.sync_cache_state()

        cache = self._registry.get(self.root_dir, recursive=self.recursive)
        # - 이전 형식 행이 있는 캐시는 저장 결과 표시용이므로 다시 조회. 이전 형식 행만 프로빙되고 나머지는 저장 행을 재사용
        if cache is None or cache.legacy_names:
            cache = TableCache.probe(
                self.root_dir,
                include_keyframe=need_keyframe,
                on_progress=self.on_progress,
                is_cancelled=self.on_is_cancelled,
                store=self._store,
                reuse_store=self._reuse_store,
                recursive=self.recursive,
                excluded_dirnames=self._excluded_dirnames(),
                ffprobe=resolve_ffprobe_executable(self.config.execution.ffprobe_path),
            )
            self._reuse_store = True
            self._registry.put(
                self.root_dir,
                cache,
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

    def _resolve_encoded_evidence(
        self, videos: Sequence[VideoProps]
    ) -> tuple[tuple[str, ...], frozenset[str]]:
        """재인코딩 이력 DB에서 태그 이름 목록과 지문 일치 파일명 집합을 조회"""
        db_path = self._store.db_path if self._store is not None else None
        return _resolve_encoded_evidence_for(
            self.root_dir, videos, db_path, self.config.execution.encode_tag_name
        )

    def evaluate_all(self, ensure_keyframe: bool = False) -> SummaryReport:
        """현재 캐시에 등록된 모든 비디오 대상 종합 평가 수행 및 리포트 반환"""
        cache = self.prepare_cache(force_keyframe=ensure_keyframe)
        tag_names, matched_names = self._resolve_encoded_evidence(cache.data)
        db_path = self._store.db_path if self._store is not None else None
        active_frames = _resolve_active_frames_for(
            self.root_dir,
            cache.data,
            db_path,
            analysis_version=analysis_version_for_mode(self.config.encode.frame_analysis_mode),
            crop_enabled=self.config.encode.crop_black_bars,
        )
        return VideoEvaluator.evaluate_all(
            self.root_dir,
            cache.data,
            self.config,
            self.exception_rules,
            policies=_policies_for(cache.legacy_names),
            manual_targets=self.manual_targets,
            active_frames=active_frames,
            known_encode_tag_names=tag_names,
            encoded_history_filenames=matched_names,
        )

    def install_stored_cache(self, stored: StoredRows) -> None:
        """저장된 결과 행으로 현재 작업 폴더의 인메모리 캐시를 등록(이전 형식 파일명 포함, 분석은 시작하지 않음)"""
        self._registry.put(
            self.root_dir,
            TableCache(
                self.root_dir,
                list(stored.rows),
                include_keyframe=stored.include_keyframe,
                legacy_names=stored.legacy_names,
            ),
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
        active_version = analysis_version_for_mode(config.encode.frame_analysis_mode)
        crop_enabled = config.encode.crop_black_bars

        def job() -> SummaryReport:
            tag_names, matched_names = _resolve_encoded_evidence_for(
                root_dir, videos, db_path, current_tag_name
            )
            active_frames = _resolve_active_frames_for(
                root_dir,
                videos,
                db_path,
                analysis_version=active_version,
                crop_enabled=crop_enabled,
            )
            return VideoEvaluator.evaluate_all(
                root_dir,
                videos,
                config,
                rules,
                policies=policies,
                manual_targets=targets,
                active_frames=active_frames,
                known_encode_tag_names=tag_names,
                encoded_history_filenames=matched_names,
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
