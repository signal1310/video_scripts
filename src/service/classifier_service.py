"""비디오 스캔, 캐시 동기화 및 종합 평가 리포트 생성 서비스 모듈"""

from collections.abc import Callable

from src.core.classification_policy import ExceptionRule
from src.core.config import ClassifierConfig
from src.core.evaluator import VideoEvaluator
from src.core.exceptions import DomainFileNotFoundError, ErrorCode
from src.core.file_manager import get_video_filenames
from src.core.messages import format_invalid_root_dir_msg
from src.core.models import SummaryReport
from src.core.prober import CancelCheck, ProgressCallback
from src.core.workspace_paths import are_all_valid_dirs, parse_root_dirs
from src.service.analysis_probe import count_pending_files
from src.service.analysis_store import AnalysisStore
from src.service.cache import DEFAULT_CACHE_REGISTRY, CacheRegistry, TableCache


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

    def sync_cache_state(self) -> None:
        """외부 파사드에서 작업 경로 변경 후 캐시 일관성을 동기화"""
        self._registry.drop_if_stale(self.root_dir, recursive=self.recursive)

    def has_stale_cache(self) -> bool:
        """현재 등록된 캐시의 작업 폴더 시그니처가 디스크와 달라졌는지 반환"""
        return self._registry.is_stale(self.root_dir, recursive=self.recursive)

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
            )
            self._reuse_store = True
            self._registry.put(self.root_dir, cache, recursive=self.recursive)
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
        )

    def known_video_filenames(self) -> set[str]:
        """작업 폴더에 실제로 있는 비디오 논리 파일명 집합 반환"""
        return set(get_video_filenames(self.root_dir, recursive=self.recursive))

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

        def job() -> SummaryReport:
            return VideoEvaluator.evaluate_all(
                root_dir, videos, config, rules, manual_targets=targets
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
        )
