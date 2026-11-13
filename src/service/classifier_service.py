"""비디오 스캔, 캐시 동기화 및 파일 분류 오케스트레이션 서비스 모듈"""

import logging
import os
from typing import ClassVar

from src.core.config import ClassifierConfig
from src.core.evaluator import ExceptionRule, VideoEvaluator
from src.core.exceptions import DomainFileNotFoundError, ErrorCode
from src.core.file_manager import (
    classify_files_batch,
    compute_dir_signature,
    unclassify_all_files,
)
from src.core.messages import format_invalid_root_dir_msg
from src.core.models import SummaryReport, VideoEvaluation, VideoProps
from src.core.prober import CancelCheck, ProgressCallback
from src.service.cache import TableCache

DirSignature = tuple[tuple[str, int, int], ...]


class VideoClassifierService:
    """비디오 스캔, 작업 폴더별 캐시 동기화, 종합 평가 리포트 생성 및 파일 이동·복원을 처리하는 서비스"""

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
        on_is_cancelled: CancelCheck = None,
    ) -> None:
        if not root_dir or not os.path.isdir(root_dir):
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
        self.exception_rules: list[ExceptionRule] = []

        self._sync_cache_state()

    def sync_cache_state(self) -> None:
        """외부 파사드에서 작업 경로 변경 후 캐시 일관성을 동기화"""
        self._sync_cache_state()

    @classmethod
    def get_shared_cache(cls, root_dir: str) -> TableCache | None:
        """지정한 루트 디렉터리에 연결된 인메모리 캐시를 반환"""
        key = cls._normalize_root_key(root_dir)
        return cls._shared_caches.get(key)

    @classmethod
    def inject_cache(
        cls,
        root_dir: str,
        cache: TableCache,
        signature: DirSignature | None = None,
    ) -> None:
        """지정한 루트 디렉터리의 공유 인메모리 캐시와 디렉터리 시그니처를 등록"""
        key = cls._normalize_root_key(root_dir)
        cls._shared_caches[key] = cache
        cls._shared_signatures[key] = (
            signature if signature is not None else compute_dir_signature(root_dir)
        )

    @classmethod
    def invalidate_shared_cache(cls, root_dir: str | None = None) -> None:
        """지정된 경로(생략 시 전체)의 인메모리 캐시와 디렉터리 시그니처를 초기화"""
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
        """현재 작업 경로의 캐시에 대해 디렉터리 파일 시그니처 변화를 감지하고 불일치 시 무효화"""
        key = self._normalize_root_key(self.root_dir)
        if key not in self._shared_caches:
            return

        saved_sig = self._shared_signatures.get(key)
        curr_sig = compute_dir_signature(self.root_dir)
        if saved_sig is not None and curr_sig != saved_sig:
            self._shared_caches.pop(key, None)
            self._shared_signatures.pop(key, None)

    def prepare_cache(self, force_keyframe: bool = False) -> TableCache:
        """인메모리 캐시의 유효성을 점검하고, 필요한 경우 키프레임 간격 정보를 추가로 로드하여 준비"""
        need_keyframe: bool = self.include_keyframe or force_keyframe
        self._sync_cache_state()

        key = self._normalize_root_key(self.root_dir)
        cache = self._shared_caches.get(key)
        if cache is None:
            cache = TableCache(
                self.root_dir,
                init_keyframe_flag=need_keyframe,
                on_progress=self.on_progress,
                is_cancelled=self.on_is_cancelled,
            )
            self._shared_caches[key] = cache
            self._shared_signatures[key] = compute_dir_signature(self.root_dir)
        else:
            cache.update_keyframe(
                need_keyframe,
                on_progress=self.on_progress,
                is_cancelled=self.on_is_cancelled,
            )

        return cache

    @property
    def cache(self) -> TableCache | None:
        """현재 작업 폴더의 인메모리 캐시 인스턴스 반환"""
        return self.get_shared_cache(self.root_dir)

    def evaluate_all(self, ensure_keyframe: bool = False) -> SummaryReport:
        """현재 캐시에 등록된 모든 비디오 대상 종합 평가 수행 및 리포트 반환"""
        cache = self.prepare_cache(force_keyframe=ensure_keyframe)
        return VideoEvaluator.evaluate_all(
            self.root_dir, cache.data, self.config, self.exception_rules
        )

    def classify(self) -> SummaryReport:
        """비디오 평가 결과 기반 파일 분류 및 요약 리포트 반환

        실제 이동 모드에서는 중간 실패 시 이미 이동된 파일 롤백, 완료 후 디렉터리 시그니처 갱신
        """
        cache = self.prepare_cache(force_keyframe=True)
        report = VideoEvaluator.evaluate_all(
            self.root_dir, cache.data, self.config, self.exception_rules
        )
        eval_map: dict[str, VideoEvaluation] = {
            ev.video.filename: ev for ev in report.evaluations
        }

        if self.pseudo_classify:
            return report

        planned_moves: list[tuple[VideoProps, str]] = []
        for vid in cache.data:
            if vid.moved_dirname is not None:
                continue
            ev = eval_map.get(vid.filename)
            if ev is not None and ev.target_dirname:
                planned_moves.append((vid, ev.target_dirname))

        try:
            _, warnings = classify_files_batch(cache.root_dir, planned_moves)
            for w in warnings:
                logging.getLogger(__name__).warning("%s", w)
        finally:
            self._update_cache_signature()

        return report

    def unclassify_files(self) -> tuple[int, list[str]]:
        """하위 분류 폴더의 파일들을 작업 폴더로 복원"""
        curr_cache = self.cache
        cached_data = curr_cache.data if curr_cache is not None else None
        moved_count, warnings = unclassify_all_files(
            self.root_dir,
            cached_videos=cached_data,
        )
        if moved_count > 0:
            self._update_cache_signature()
        return moved_count, warnings
