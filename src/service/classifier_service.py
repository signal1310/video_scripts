"""비디오 스캔, 캐시 동기화 및 파일 분류 오케스트레이션 서비스 모듈"""

import logging
import os

from src.core.classification_policy import ExceptionRule
from src.core.classify_manifest import load_manifest_records
from src.core.config import ClassifierConfig
from src.core.evaluator import VideoEvaluator
from src.core.exceptions import DomainFileNotFoundError, ErrorCode
from src.core.file_manager import (
    MoveIssue,
    classify_files_batch,
    get_video_filenames,
    preview_unclassify_all,
    unclassify_all_files,
)
from src.core.messages import format_invalid_root_dir_msg, format_move_issue
from src.core.models import SummaryReport, VideoEvaluation, VideoProps
from src.core.prober import CancelCheck, ProgressCallback
from src.core.workspace_paths import are_all_valid_dirs, parse_root_dirs, primary_root_dir
from src.service.analysis_probe import count_pending_files
from src.service.analysis_store import AnalysisStore
from src.service.cache import DEFAULT_CACHE_REGISTRY, CacheRegistry, TableCache


class VideoClassifierService:
    """비디오 스캔, 작업 폴더별 캐시 동기화, 종합 평가 리포트 생성 및 파일 이동, 복원을 처리하는 서비스"""

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
        self.pseudo_classify: bool = False
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

    def sync_cache_state(self) -> None:
        """외부 파사드에서 작업 경로 변경 후 캐시 일관성을 동기화"""
        self._registry.drop_if_stale(
            self.root_dir,
            recursive=self.recursive,
            excluded_dirnames=self.class_dirnames(),
            downscale_prefix=self.config.dir_downscale_prefix,
        )

    def invalidate_cache(self) -> None:
        """인메모리 캐시를 비우고 다음 분석은 분석 캐시 행을 읽지 않고 새 결과로 덮어쓰도록 설정"""
        self._registry.invalidate(self.root_dir, recursive=self.recursive)
        self._reuse_store = False

    def prepare_cache(self, force_keyframe: bool = False) -> TableCache:
        """인메모리 캐시의 유효성을 점검하고, 필요한 경우 키프레임 간격 정보를 추가로 로드하여 준비"""
        need_keyframe: bool = self.include_keyframe or force_keyframe
        self.sync_cache_state()

        cache = self._registry.get(self.root_dir, recursive=self.recursive)
        if cache is None:
            cache = TableCache.probe(
                self.root_dir,
                include_keyframe=need_keyframe,
                on_progress=self.on_progress,
                is_cancelled=self.on_is_cancelled,
                store=self._store,
                reuse_store=self._reuse_store,
                recursive=self.recursive,
                excluded_dirnames=self.class_dirnames(),
                downscale_prefix=self.config.dir_downscale_prefix,
            )
            self._reuse_store = True
            self._registry.put(
                self.root_dir,
                cache,
                recursive=self.recursive,
                excluded_dirnames=self.class_dirnames(),
                downscale_prefix=self.config.dir_downscale_prefix,
            )
        else:
            cache.update_keyframe(
                need_keyframe,
                on_progress=self.on_progress,
                is_cancelled=self.on_is_cancelled,
                store=self._store,
            )

        return cache

    @property
    def cache(self) -> TableCache | None:
        """현재 작업 폴더 범위의 인메모리 캐시 인스턴스 반환"""
        return self._registry.get(self.root_dir, recursive=self.recursive)

    def pending_count(self, include_keyframe: bool) -> int:
        """분석 시 ffprobe가 필요한 영상 수, 인메모리 캐시가 요청 범위를 이미 덮으면 0"""
        cache = self.cache
        if cache is not None and (
            not include_keyframe or all(v.keyframe_interval is not None for v in cache.data)
        ):
            return 0
        return count_pending_files(
            self._store,
            self.root_dir,
            include_keyframe=include_keyframe,
            recursive=self.recursive,
            excluded_dirnames=self.class_dirnames(),
            downscale_prefix=self.config.dir_downscale_prefix,
        )

    def evaluate_all(self, ensure_keyframe: bool = False) -> SummaryReport:
        """현재 캐시에 등록된 모든 비디오 대상 종합 평가 수행 및 리포트 반환"""
        cache = self.prepare_cache(force_keyframe=ensure_keyframe)
        return VideoEvaluator.evaluate_all(
            self.root_dir,
            cache.data,
            self.config,
            self.exception_rules,
            manual_targets=self.manual_targets,
        )

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
        )

    def classify(self, *, dry_run: bool | None = None) -> SummaryReport:
        """비디오 평가 결과 기반 파일 분류 및 요약 리포트 반환

        dry_run 생략 시 pseudo_classify 설정을 따르며, 명시한 값은 설정보다 우선
        """
        return self.classify_with_result(dry_run=dry_run)[0]

    def classify_with_result(
        self, *, dry_run: bool | None = None
    ) -> tuple[SummaryReport, int, list[MoveIssue]]:
        """분류를 수행하고 (리포트, 실제 이동 파일 수, 이동 이슈 리스트) 반환

        실제 이동 모드에서는 중간 실패 시 이미 이동된 파일 롤백, 완료 후 디렉터리 시그니처 갱신
        모의 분류에서는 이동 파일 수 0과 빈 이슈 리스트 반환
        """
        cache = self.prepare_cache(force_keyframe=True)
        report = VideoEvaluator.evaluate_all(
            self.root_dir,
            cache.data,
            self.config,
            self.exception_rules,
            manual_targets=self.manual_targets,
        )
        eval_map: dict[str, VideoEvaluation] = {
            ev.video.filename: ev for ev in report.evaluations
        }

        is_dry_run = self.pseudo_classify if dry_run is None else dry_run
        if is_dry_run:
            return report, 0, []

        planned_moves: list[tuple[VideoProps, str]] = []
        for vid in cache.data:
            if vid.moved_dirname is not None:
                continue
            ev = eval_map.get(vid.filename)
            if ev is not None and ev.target_dirname:
                planned_moves.append((vid, ev.target_dirname))

        try:
            moved_count, issues = classify_files_batch(cache.root_dir, planned_moves)
            for issue in issues:
                logging.getLogger(__name__).warning("%s", format_move_issue(issue))
        finally:
            self._registry.refresh_signature(
                self.root_dir,
                recursive=self.recursive,
                excluded_dirnames=self.class_dirnames(),
                downscale_prefix=self.config.dir_downscale_prefix,
            )

        return report, moved_count, issues

    def unclassify_files(self) -> tuple[int, list[MoveIssue]]:
        """설정된 분류 폴더의 파일들을 작업 폴더로 복원"""
        curr_cache = self.cache
        cached_data = curr_cache.data if curr_cache is not None else None
        moved_count, issues = unclassify_all_files(
            self.root_dir,
            cached_videos=cached_data,
            dirnames=self._unclassify_dirnames(cached_data),
        )
        if moved_count > 0:
            self._sync_cache_after_restore(curr_cache)
        return moved_count, issues

    def preview_unclassify(self) -> tuple[int, int]:
        """파일을 이동하지 않고 (복원 예정 영상 수, 이름 충돌 영상 수) 반환"""
        curr_cache = self.cache
        cached_data = curr_cache.data if curr_cache is not None else None
        return preview_unclassify_all(
            self.root_dir,
            dirnames=self._unclassify_dirnames(cached_data),
        )

    def _unclassify_dirnames(self, cached_data: list[VideoProps] | None) -> list[str]:
        """설정된 분류 폴더명과 캐시에 기록된 실제 하위 폴더명을 합친 복원 대상 목록"""
        class_dirnames = self.class_dirnames()
        if cached_data:
            class_dirnames.extend(
                v.moved_dirname for v in cached_data if v.moved_dirname is not None
            )
        return class_dirnames

    def class_dirnames(self) -> list[str]:
        """현재 설정의 분류 폴더명 목록 반환"""
        return self.config.class_dirnames()

    def known_video_filenames(self) -> set[str]:
        """작업 폴더, 분류 폴더, 매니페스트에 실제로 있는 비디오 논리 파일명 집합 반환

        - 캐시는 현재 범위만 담으므로 재시작 후에도 분류된 영상을 찾도록 매니페스트와 분류 폴더를 함께 조회
        """
        names = set(self._scan_names())
        primary = primary_root_dir(self.root_dir)
        for record in load_manifest_records().values():
            if os.path.normcase(record.primary_root) == os.path.normcase(primary):
                names.add(record.logical_filename)
        for dirname in self.class_dirnames():
            names.update(get_video_filenames(os.path.join(primary, dirname)))
        return names

    def _scan_names(self) -> list[str]:
        """현재 재귀 설정과 분류 폴더 제외 규칙으로 얻은 작업 폴더 파일명 목록"""
        return get_video_filenames(
            self.root_dir,
            recursive=self.recursive,
            excluded_dirnames=self.class_dirnames(),
            downscale_prefix=self.config.dir_downscale_prefix,
        )

    def _sync_cache_after_restore(self, cache: TableCache | None) -> None:
        """복원 후 캐시가 실제 폴더 구성과 다르면 무효화하고 같으면 시그니처만 갱신

        - 재시작 직후 캐시에는 하위 폴더 파일이 없으므로 시그니처만 갱신하면 복원된 파일이 영구 누락됨
        """
        if cache is None:
            return
        cached_names = {v.filename for v in cache.data}
        if cached_names != set(self._scan_names()):
            self._registry.invalidate(self.root_dir, recursive=self.recursive)
        else:
            self._registry.refresh_signature(
                self.root_dir,
                recursive=self.recursive,
                excluded_dirnames=self.class_dirnames(),
                downscale_prefix=self.config.dir_downscale_prefix,
            )
