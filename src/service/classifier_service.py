import os
from collections.abc import Callable

from src.core.config import ClassifierConfig
from src.core.evaluator import ExceptionRule, VideoEvaluator
from src.core.file_manager import (
    compute_dir_signature,
    file_exists_in,
    move_file,
    unclassify_all_files,
)
from src.core.models import SummaryReport, VideoEvaluation
from src.core.prober import ProgressCallback
from src.service.cache import TableCache


class VideoClassifierService:
    """
    - 비디오 스캔, 캐시 동기화, 종합 평가 리포트 생성 및 파일 이동/복원을 처리하는 서비스.
    """

    _shared_cache: TableCache | None = None
    _shared_signature: tuple[tuple[str, int, int], ...] | None = None

    def __init__(
        self,
        root_dir: str,
        config: ClassifierConfig | None = None,
        on_progress: ProgressCallback = None,
        on_log: Callable[[str], None] | None = None,
    ) -> None:
        if not root_dir or not os.path.isdir(root_dir):
            raise FileNotFoundError(
                f"지정된 루트 디렉터리를 찾을 수 없거나 유효하지 않음: '{root_dir}'"
            )

        self.root_dir: str = root_dir
        self.config: ClassifierConfig = config or ClassifierConfig.from_env()
        self.on_progress: ProgressCallback = on_progress
        self.on_log: Callable[[str], None] | None = on_log

        self.include_keyframe: bool = False
        self.pseudo_classify: bool = False
        self.exception_rules: list[ExceptionRule] = []

        self._sync_cache_state()

    def sync_cache_state(self) -> None:
        """외부 파사드에서 작업 경로 변경 후 캐시 일관성을 동기화."""
        self._sync_cache_state()

    @classmethod
    def invalidate_shared_cache(cls) -> None:
        """공유 인메모리 캐시와 디렉터리 시그니처를 초기화."""
        cls._shared_cache = None
        cls._shared_signature = None

    def _sync_cache_state(self) -> None:
        """
        - 작업 디렉터리가 변경되었거나 폴더 내 파일 변경이 감지되면 캐시를 갱신.
        """
        cache = VideoClassifierService._shared_cache
        if cache is None:
            return

        if os.path.abspath(cache.root_dir) != os.path.abspath(self.root_dir):
            VideoClassifierService._shared_cache = None
            VideoClassifierService._shared_signature = None
            return

        # - 실제 디스크 파일 이동이 발생하지 않은 상태에서 파일 시그니처가 달라졌다면 캐시를 재설정.
        any_moved_on_disk = any(
            v.moved_dirname is not None
            and not file_exists_in(self.root_dir, v.filename)
            for v in cache.data
        )
        if not any_moved_on_disk:
            curr_sig = compute_dir_signature(self.root_dir)
            if (
                VideoClassifierService._shared_signature is not None
                and curr_sig != VideoClassifierService._shared_signature
            ):
                VideoClassifierService._shared_cache = None
                VideoClassifierService._shared_signature = None

    def prepare_cache(self, force_keyframe: bool = False) -> TableCache:
        """
        - 인메모리 캐시의 유효성을 점검하고, 필요한 경우 키프레임 간격 정보를 추가로 로드하여 준비.
        """
        need_keyframe: bool = self.include_keyframe or force_keyframe
        self._sync_cache_state()

        if VideoClassifierService._shared_cache is None:
            VideoClassifierService._shared_cache = TableCache(
                self.root_dir,
                init_keyframe_flag=need_keyframe,
                on_progress=self.on_progress,
                on_log=self.on_log,
            )
            VideoClassifierService._shared_signature = compute_dir_signature(
                self.root_dir
            )
        else:
            VideoClassifierService._shared_cache.update_keyframe(
                need_keyframe,
                on_progress=self.on_progress,
                on_log=self.on_log,
            )

        return VideoClassifierService._shared_cache

    @property
    def cache(self) -> TableCache | None:
        return VideoClassifierService._shared_cache

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
        - 키프레임 분석이 포함되며, pseudo_classify가 True인 경우 디스크 이동 없이 메모리에만 기록.
        """
        cache = self.prepare_cache(force_keyframe=True)
        report = VideoEvaluator.evaluate_all(
            self.root_dir, cache.data, self.config, self.exception_rules
        )
        eval_map: dict[str, VideoEvaluation] = {
            ev.video.filename: ev for ev in report.evaluations
        }

        for vid in cache.data:
            # - 디스크 상에서 이미 하위 폴더로 실제 이동 완료된 파일은 중복 이동하지 않음.
            if not file_exists_in(cache.root_dir, vid.filename):
                continue

            ev = eval_map.get(vid.filename)
            vid.moved_dirname = ev.target_dirname if ev else None

            # - 실제 분류 모드인 경우 대상 하위 폴더로 파일 이동.
            if not self.pseudo_classify and vid.moved_dirname:
                moved = move_file(cache.root_dir, vid.filename, vid.moved_dirname)
                if not moved:
                    vid.moved_dirname = None

        return report

    def unclassify_files(
        self, unmark_pseudo_classified_only: bool = False
    ) -> tuple[int, list[str]]:
        """
        - 하위 분류 폴더의 파일들을 최상위 디렉터리로 복구하거나 가분류 표시를 해제.
        """
        cached_data = (
            VideoClassifierService._shared_cache.data
            if VideoClassifierService._shared_cache
            else None
        )
        moved_count, warnings = unclassify_all_files(
            self.root_dir,
            cached_videos=cached_data,
            unmark_pseudo_classified_only=unmark_pseudo_classified_only,
        )
        if not unmark_pseudo_classified_only and moved_count > 0:
            VideoClassifierService._shared_signature = compute_dir_signature(
                self.root_dir
            )
        return moved_count, warnings
