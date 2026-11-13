import os
from collections.abc import Callable
from typing import ClassVar

from src.core.config import ClassifierConfig
from src.core.evaluator import ExceptionRule, VideoEvaluator
from src.core.file_manager import (
    classify_files_batch,
    compute_dir_signature,
    file_exists_in,
    unclassify_all_files,
)
from src.core.models import SummaryReport, VideoEvaluation, VideoProps
from src.core.prober import CancelCheck, ProgressCallback
from src.service.cache import TableCache

DirSignature = tuple[tuple[str, int, int], ...]


class VideoClassifierService:
    """
    - 비디오 스캔, 루트 경로별 캐시 동기화, 종합 평가 리포트 생성 및 파일 이동/복원을 처리하는 서비스.
    """

    _shared_caches: ClassVar[dict[str, TableCache]] = {}
    _shared_signatures: ClassVar[dict[str, DirSignature]] = {}

    @staticmethod
    def _normalize_root_key(root_dir: str) -> str:
        return os.path.normcase(os.path.abspath(root_dir))

    def __init__(
        self,
        root_dir: str,
        config: ClassifierConfig | None = None,
        on_progress: ProgressCallback = None,
        on_log: Callable[[str], None] | None = None,
        on_is_cancelled: CancelCheck = None,
    ) -> None:
        if not root_dir or not os.path.isdir(root_dir):
            raise FileNotFoundError(
                f"지정된 루트 디렉터리를 찾을 수 없거나 유효하지 않음: '{root_dir}'"
            )

        self.root_dir: str = root_dir
        self.config: ClassifierConfig = config or ClassifierConfig.from_env()
        self.on_progress: ProgressCallback = on_progress
        self.on_log: Callable[[str], None] | None = on_log
        self.on_is_cancelled: CancelCheck = on_is_cancelled

        self.include_keyframe: bool = False
        self.pseudo_classify: bool = False
        self.exception_rules: list[ExceptionRule] = []

        self._sync_cache_state()

    def sync_cache_state(self) -> None:
        """외부 파사드에서 작업 경로 변경 후 캐시 일관성을 동기화."""
        self._sync_cache_state()

    @classmethod
    def get_shared_cache(cls, root_dir: str) -> TableCache | None:
        """지정한 루트 디렉터리에 연결된 인메모리 캐시를 반환."""
        key = cls._normalize_root_key(root_dir)
        return cls._shared_caches.get(key)

    @classmethod
    def inject_cache(
        cls,
        root_dir: str,
        cache: TableCache,
        signature: DirSignature | None = None,
    ) -> None:
        """지정한 루트 디렉터리의 공유 인메모리 캐시와 디렉터리 시그니처를 등록."""
        key = cls._normalize_root_key(root_dir)
        cls._shared_caches[key] = cache
        cls._shared_signatures[key] = (
            signature if signature is not None else compute_dir_signature(root_dir)
        )

    @classmethod
    def invalidate_shared_cache(cls, root_dir: str | None = None) -> None:
        """지정된 경로(생략 시 전체)의 인메모리 캐시와 디렉터리 시그니처를 초기화."""
        if root_dir is None:
            cls._shared_caches.clear()
            cls._shared_signatures.clear()
            return
        key = cls._normalize_root_key(root_dir)
        cls._shared_caches.pop(key, None)
        cls._shared_signatures.pop(key, None)

    def _update_cache_signature(self) -> None:
        key = self._normalize_root_key(self.root_dir)
        if key in self._shared_caches:
            self._shared_signatures[key] = compute_dir_signature(self.root_dir)

    def _sync_cache_state(self) -> None:
        """
        - 현재 작업 경로의 캐시에 대해 디렉터리 파일 시그니처 변화를 감지하고 불일치 시 무효화.
        """
        key = self._normalize_root_key(self.root_dir)
        if key not in self._shared_caches:
            return

        saved_sig = self._shared_signatures.get(key)
        curr_sig = compute_dir_signature(self.root_dir)
        if saved_sig is not None and curr_sig != saved_sig:
            self._shared_caches.pop(key, None)
            self._shared_signatures.pop(key, None)

    def prepare_cache(self, force_keyframe: bool = False) -> TableCache:
        """
        - 인메모리 캐시의 유효성을 점검하고, 필요한 경우 키프레임 간격 정보를 추가로 로드하여 준비.
        """
        need_keyframe: bool = self.include_keyframe or force_keyframe
        self._sync_cache_state()

        key = self._normalize_root_key(self.root_dir)
        cache = self._shared_caches.get(key)
        if cache is None:
            cache = TableCache(
                self.root_dir,
                init_keyframe_flag=need_keyframe,
                on_progress=self.on_progress,
                on_log=self.on_log,
                is_cancelled=self.on_is_cancelled,
            )
            self._shared_caches[key] = cache
            self._shared_signatures[key] = compute_dir_signature(self.root_dir)
        else:
            cache.update_keyframe(
                need_keyframe,
                on_progress=self.on_progress,
                on_log=self.on_log,
                is_cancelled=self.on_is_cancelled,
            )

        return cache

    @property
    def cache(self) -> TableCache | None:
        return self.get_shared_cache(self.root_dir)

    def evaluate_all(self, ensure_keyframe: bool = False) -> SummaryReport:
        """
        - 현재 캐시에 등록된 모든 비디오를 대상으로 종합 평가를 수행하여 리포트 객체를 생성.
        """
        cache = self.prepare_cache(force_keyframe=ensure_keyframe)
        return VideoEvaluator.evaluate_all(
            self.root_dir, cache.data, self.config, self.exception_rules
        )

    def classify(self) -> SummaryReport:
        """
        - 비디오 평가 결과에 따라 파일을 분류.
        - 실제 파일 이동 모드에서는 중간 실패 시 이미 이동된 파일을 원복하며, 완료 후 루트 시그니처를 갱신.
        """
        cache = self.prepare_cache(force_keyframe=True)
        report = VideoEvaluator.evaluate_all(
            self.root_dir, cache.data, self.config, self.exception_rules
        )
        eval_map: dict[str, VideoEvaluation] = {
            ev.video.filename: ev for ev in report.evaluations
        }

        if self.pseudo_classify:
            for vid in cache.data:
                if not file_exists_in(cache.root_dir, vid.filename):
                    continue
                ev = eval_map.get(vid.filename)
                vid.moved_dirname = ev.target_dirname if ev else None
            return report

        planned_moves: list[tuple[VideoProps, str]] = []
        for vid in cache.data:
            if not file_exists_in(cache.root_dir, vid.filename):
                continue
            ev = eval_map.get(vid.filename)
            if ev is not None and ev.target_dirname:
                planned_moves.append((vid, ev.target_dirname))
            else:
                vid.moved_dirname = None

        try:
            _, warnings = classify_files_batch(cache.root_dir, planned_moves)
            for w in warnings:
                if self.on_log is not None:
                    self.on_log(w)
        finally:
            self._update_cache_signature()

        return report

    def unclassify_files(
        self, unmark_pseudo_classified_only: bool = False
    ) -> tuple[int, list[str]]:
        """
        - 하위 분류 폴더의 파일들을 최상위 디렉터리로 복구하거나 가분류 표시를 해제.
        """
        curr_cache = self.cache
        cached_data = curr_cache.data if curr_cache is not None else None
        moved_count, warnings = unclassify_all_files(
            self.root_dir,
            cached_videos=cached_data,
            unmark_pseudo_classified_only=unmark_pseudo_classified_only,
        )
        if not unmark_pseudo_classified_only and moved_count > 0:
            self._update_cache_signature()
        return moved_count, warnings
