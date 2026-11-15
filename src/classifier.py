"""CUI, 노트북 진입점용 분류 세션 파사드 모듈"""

from src.core.app_state import (
    AppState,
    ensure_default_config_files,
    load_app_state,
    record_recent_dir,
    resolve_effective_root_dir,
    save_app_state,
)
from src.core.config import ClassifierConfig, get_rootdir_path
from src.core.exceptions import DomainFileNotFoundError, ErrorCode
from src.core.logging_setup import ensure_console_logging
from src.core.models import SummaryReport, VideoProps
from src.core.prober import CancelCheck, ProgressCallback
from src.cui.cui_printer import CUIPrinter, SortKey, print_console_progress
from src.cui.sorting import SortTarget
from src.service.session import ClassifierSession


class VideoClassifier(ClassifierSession):
    """분류 세션에 상태 파일 로드, 콘솔 출력, GUI 실행을 더한 노트북/CUI 파사드 클래스"""

    def __init__(
        self,
        root_dir: str | None = None,
        config: ClassifierConfig | None = None,
        over_bitrate_ratio: float | None = None,
        include_keyframe_interval: bool = False,
        on_progress: ProgressCallback = None,
        on_is_cancelled: CancelCheck = None,
    ) -> None:
        ensure_console_logging()
        loaded_state = (
            ensure_default_config_files()
            if root_dir is None or config is None
            else None
        )
        if root_dir is not None:
            target_root = root_dir
        else:
            target_root = resolve_effective_root_dir()
            if not target_root:
                raise DomainFileNotFoundError(
                    f"작업 폴더 경로가 지정되지 않았습니다: {get_rootdir_path()}",
                    code=ErrorCode.ROOT_DIR_NOT_FOUND,
                )

        if config is not None:
            cfg = config
        elif loaded_state is not None:
            cfg = loaded_state.to_classifier_config()
        else:
            cfg = ClassifierConfig()
        if over_bitrate_ratio is not None:
            cfg.thresholds.over_bitrate_ratio = over_bitrate_ratio

        super().__init__(
            target_root,
            cfg,
            on_progress=on_progress or print_console_progress,
            on_is_cancelled=on_is_cancelled,
        )
        self.service.include_keyframe = include_keyframe_interval
        self._filename_maxlen: int = cfg.cui.filename_maxlen
        if (
            config is None
            and loaded_state is not None
            and loaded_state.ui.exception_tokens
        ):
            self.set_exception_tokens(loaded_state.ui.exception_tokens)

    @property
    def filename_maxlen(self) -> int:
        """콘솔 출력 시 파일명 열의 최대 표시 너비"""
        return self._filename_maxlen

    def set_config(self, config: ClassifierConfig) -> None:
        """분류기 내부 설정(ClassifierConfig)을 갱신하고 콘솔 파일명 너비를 동기화"""
        super().set_config(config)
        self._filename_maxlen = config.cui.filename_maxlen

    def set_callbacks(
        self,
        on_progress: ProgressCallback = None,
        on_is_cancelled: CancelCheck = None,
    ) -> None:
        """진행률 보고 및 작업 중단 확인용 콜백 설정, 진행률 생략 시 콘솔 출력 사용"""
        super().set_callbacks(on_progress or print_console_progress, on_is_cancelled)

    @property
    def video_props(self) -> list[VideoProps]:
        """현재 작업 대상 폴더 내 비디오 파일들의 메타데이터 목록을 반환"""
        return self.service.prepare_cache().data

    def set_filename_max_length(self, max_length: int) -> None:
        """콘솔 테이블 출력 시 파일명 열의 최대 표시 너비를 설정"""
        self._filename_maxlen = max_length
        self.config.cui.filename_maxlen = max_length

    def set_over_bitrate_ratio(self, ratio: float) -> None:
        """과화질 판별 경계 배수를 런타임에 동적으로 변경"""
        self.config.thresholds.over_bitrate_ratio = ratio

    def set_low_bitrate_ratio(self, ratio: float) -> None:
        """저화질 판별 경계 배수를 런타임에 동적으로 변경"""
        self.config.thresholds.low_bitrate_ratio = ratio

    def set_keyframe_threshold(self, seconds: float) -> None:
        """허용 가능한 키프레임 간격의 상한선(초)을 런타임에 동적으로 변경"""
        self.config.thresholds.keyframe_interval_sec = seconds

    def set_min_saved_mb(self, min_saved_mb: float) -> None:
        """과화질 영상 인코딩에 필요한 최소 용량 절감 하한(MB)을 런타임에 동적으로 변경"""
        self.config.efficiency.min_saved_mb = min_saved_mb

    def set_min_mb_per_min(self, min_mb_per_min: float) -> None:
        """재생 1분당 요구되는 최소 용량 절감 효율(MB/분)을 런타임에 동적으로 변경"""
        self.config.efficiency.min_mb_per_min = min_mb_per_min

    def set_exempt_saved_mb(self, exempt_saved_mb: float) -> None:
        """시간 효율 검사를 면제할 최소 절대 절감량(MB, 0=비활성)을 런타임에 동적으로 변경"""
        self.config.efficiency.exempt_saved_mb = exempt_saved_mb

    def save_state(self, *, save_root_dir: bool = False) -> AppState:
        """현재 CUI 설정, 예외 토큰, 작업 경로를 data/* 영속 파일에 저장"""
        state = load_app_state()
        state.apply_classifier_config(self.config)
        state.ui.exception_tokens = self.exception_tokens
        if save_root_dir:
            state.paths.recent_dirs = record_recent_dir(
                state.paths.recent_dirs, self.root_dir
            )
        save_app_state(
            state,
            root_dir=self.root_dir if save_root_dir else None,
        )
        return state

    def reload_state(self) -> AppState:
        """통합 설정(data/*) 최신 값을 현재 VideoClassifier에 재적용"""
        state = load_app_state()
        self.set_config(state.to_classifier_config())
        self.set_exception_tokens(state.ui.exception_tokens)
        effective_root = resolve_effective_root_dir()
        if effective_root and effective_root != self.root_dir:
            self.set_root_dir(effective_root)
        return state

    def _evaluate_for_print(self) -> SummaryReport:
        return self.get_summary_report(ensure_keyframe=self.include_keyframe)

    def print(
        self,
        *,
        limit: int | None = None,
        summary_only: bool = False,
        include_summary: bool = True,
        sort_by: SortTarget | None = None,
        descending: bool | None = None,
        sanitize_emoji: bool = True,
        sort_key: SortKey = None,
    ) -> None:
        """분류 그룹별 테이블과 3단 용량 시뮬레이션 요약 리포트를 서식에 맞추어 콘솔에 출력

        sort_by는 리터럴 문자열/ColumnId 자동완성을 지원하며, sort_key 미지정 시 기본값은 파일명 자연 정렬(오름차순)
        """
        CUIPrinter.print_report(
            report=self._evaluate_for_print(),
            config=self.config,
            sort_key=sort_key,
            filename_maxlen=self._filename_maxlen,
            sanitize_emoji=sanitize_emoji,
            limit=limit,
            summary_only=summary_only,
            include_summary=include_summary,
            sort_by=sort_by,
            descending=descending,
        )

    def print_tables(
        self,
        *,
        limit: int | None = None,
        sort_by: SortTarget | None = None,
        descending: bool | None = None,
        sanitize_emoji: bool = True,
        sort_key: SortKey = None,
    ) -> None:
        """요약 리포트 없이 분류 그룹별 상세 테이블만 콘솔에 출력"""
        CUIPrinter.print_tables(
            report=self._evaluate_for_print(),
            config=self.config,
            sort_key=sort_key,
            filename_maxlen=self._filename_maxlen,
            sanitize_emoji=sanitize_emoji,
            limit=limit,
            sort_by=sort_by,
            descending=descending,
        )

    def print_summary(self) -> None:
        """상세 테이블 없이 3단 용량 시뮬레이션 요약 리포트만 콘솔에 출력"""
        CUIPrinter.print_summary_report(
            report=self._evaluate_for_print(),
            config=self.config,
        )

    def gui(self) -> int:
        """현재 분류 세션의 설정과 경로를 기반으로 데스크톱 GUI 창을 실행"""
        from src.gui import run_gui

        return run_gui(classifier=self)
