"""CUI와 GUI가 공유하는 VideoClassifier 통합 파사드 모듈"""

import importlib
import logging
from collections.abc import Callable
from typing import Any

from src.core.app_state import (
    AppState,
    load_app_state,
    record_recent_dir,
    resolve_effective_root_dir,
    save_app_state,
)
from src.core.config import ClassifierConfig, get_rootdir_path
from src.core.evaluator import ExceptionRule
from src.core.exception_rules import parse_exception_token
from src.core.exceptions import DomainFileNotFoundError, DomainValueError, ErrorCode
from src.core.logging_setup import ensure_console_logging
from src.core.messages import (
    MOVE_FAILED_PREFIX,
    format_pseudo_reset_done,
    format_unclassify_done,
)
from src.core.models import SummaryReport, VideoProps
from src.core.prober import CancelCheck, ProgressCallback
from src.cui.cui_printer import CUIPrinter
from src.service.classifier_service import VideoClassifierService


class VideoClassifier:
    """비디오 스캔, 분석, 평가 및 파일 분류 작업을 총괄하는 파사드 클래스"""

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
            load_app_state()
            if root_dir is None or config is None
            else None
        )
        if root_dir is not None:
            target_root = root_dir
        else:
            fallback_root = loaded_state.root_dir if loaded_state is not None else ""
            target_root = resolve_effective_root_dir(fallback_root)
            if not target_root:
                raise DomainFileNotFoundError(
                    f".rootdir 파일을 찾을 수 없습니다: {get_rootdir_path()}",
                    code=ErrorCode.ROOT_DIR_NOT_FOUND,
                )

        if config is not None:
            cfg = config
        elif loaded_state is not None:
            cfg = loaded_state.config
        else:
            cfg = ClassifierConfig()
        if over_bitrate_ratio is not None:
            cfg.over_bitrate_ratio = over_bitrate_ratio

        self._service = VideoClassifierService(
            root_dir=target_root,
            config=cfg,
            on_progress=on_progress,
            on_is_cancelled=on_is_cancelled,
        )
        self._service.include_keyframe = include_keyframe_interval
        self._filename_maxlen: int = cfg.tabulate_filename_maxlen
        self._custom_exception_rules: list[ExceptionRule] = []
        self._exception_tokens: list[str] = []
        if config is None and loaded_state is not None and loaded_state.exception_tokens:
            self.set_exception_tokens(loaded_state.exception_tokens)

    @property
    def service(self) -> VideoClassifierService:
        """내부 서비스 컨트롤러 인스턴스를 반환"""
        return self._service

    @property
    def config(self) -> ClassifierConfig:
        """현재 적용된 분류기 설정 객체 반환"""
        return self._service.config

    @property
    def root_dir(self) -> str:
        """현재 설정된 작업 폴더 루트 경로 반환"""
        return self._service.root_dir

    @property
    def is_pseudo_classify(self) -> bool:
        """현재 모의 분류 모드 활성화 여부"""
        return self._service.pseudo_classify

    @property
    def include_keyframe(self) -> bool:
        """마지막 스캔에 키프레임 정밀 분석이 포함되었는지 여부"""
        return self._service.include_keyframe

    @property
    def has_cache(self) -> bool:
        """메모리 내 스캔 캐시(TableCache)가 존재하는지 여부"""
        return self._service.cache is not None

    @property
    def filename_maxlen(self) -> int:
        """콘솔 출력 시 파일명 열의 최대 표시 너비"""
        return self._filename_maxlen

    def set_config(self, config: ClassifierConfig) -> None:
        """분류기 내부 설정(ClassifierConfig)을 갱신"""
        self._service.config = config
        self._filename_maxlen = config.tabulate_filename_maxlen

    def set_root_dir(self, root_dir: str) -> None:
        """대상 작업 폴더를 변경하고 캐시 상태를 동기화"""
        if not root_dir:
            raise DomainValueError(
                "유효한 작업 폴더 경로를 지정해야 합니다.",
                code=ErrorCode.ROOT_DIR_INVALID,
            )
        self._service.root_dir = root_dir
        self._service.sync_cache_state()

    def set_callbacks(
        self,
        on_progress: ProgressCallback = None,
        on_is_cancelled: CancelCheck = None,
    ) -> None:
        """진행률 보고 및 작업 중단 확인용 콜백 함수 설정"""
        self._service.on_progress = on_progress
        self._service.on_is_cancelled = on_is_cancelled

    def invalidate_cache(self) -> None:
        """공유 인메모리 캐시를 명시적으로 초기화하여 다음 조회 시 디스크 재스캔을 유도"""
        VideoClassifierService.invalidate_shared_cache()

    @property
    def video_props(self) -> list[VideoProps]:
        """현재 작업 대상 폴더 내 비디오 파일들의 메타데이터 목록을 반환"""
        return self._service.prepare_cache().data

    @property
    def exception_rules(self) -> list[Callable[[VideoProps], bool]]:
        """등록된 사용자 정의 예외 규칙 함수 목록 반환"""
        return self._service.exception_rules

    @property
    def exception_tokens(self) -> list[str]:
        """문자열/표현식 토큰으로 등록된 예외 규칙 목록 반환"""
        return list(self._exception_tokens)

    def _rebuild_exception_rules(self) -> None:
        token_rules: list[ExceptionRule] = []
        for token in self._exception_tokens:
            parsed = parse_exception_token(token)
            if parsed is not None:
                token_rules.append(parsed)
        self._service.exception_rules = [*self._custom_exception_rules, *token_rules]

    def set_exception_tokens(self, tokens: list[str]) -> None:
        """문자열/조건식 토큰 리스트로 예외 규칙을 일괄 동기화 (함수형 커스텀 규칙 보존)"""
        self._exception_tokens = [tok.strip() for tok in tokens if tok.strip()]
        self._rebuild_exception_rules()

    def set_filename_max_length(self, max_length: int) -> None:
        """콘솔 테이블 출력 시 파일명 열의 최대 표시 너비를 설정"""
        self._filename_maxlen = max_length
        self._service.config.tabulate_filename_maxlen = max_length

    def set_over_bitrate_ratio(self, ratio: float) -> None:
        """과화질 판별 경계 배수를 런타임에 동적으로 변경"""
        self._service.config.over_bitrate_ratio = ratio

    def set_low_bitrate_ratio(self, ratio: float) -> None:
        """저화질 판별 경계 배수를 런타임에 동적으로 변경"""
        self._service.config.low_bitrate_ratio = ratio

    def set_keyframe_threshold(self, seconds: float) -> None:
        """허용 가능한 키프레임 간격의 상한선(초)을 런타임에 동적으로 변경"""
        self._service.config.keyframe_threshold = seconds

    def set_small_footage_mb(self, size_mb: float) -> None:
        """소형 영상으로 분류할 기준 용량(MB)을 런타임에 동적으로 변경"""
        self._service.config.small_footage_mb = size_mb

    def include_keyframe_interval(self, flag: bool = True) -> None:
        """단순 조회(print) 시 키프레임 실측 포함 여부 지정

        classify() 실행 시에는 키프레임 분석 자동 활성화로 별도 호출 불필요
        """
        self._service.include_keyframe = flag
        if flag and self._service.cache is not None:
            self._service.prepare_cache(force_keyframe=True)

    def pseudo_classify_mode(self, flag: bool = True) -> None:
        """실제 디스크 파일 이동 없이 메모리 상에서 모의 분류 결과만 확인하는 모드를 활성화"""
        self._service.pseudo_classify = flag

    def add_exception_rule(self, pred: Callable[[VideoProps], bool]) -> None:
        """분류에서 제외하고 원본 보존할 사용자 정의 조건식(람다 함수)을 등록"""
        self._custom_exception_rules.append(pred)
        self._rebuild_exception_rules()

    def clear_exception_rules(self) -> None:
        """등록되어 있는 모든 예외 조건식을 초기화하여 기본 분류 상태로 복귀"""
        self._custom_exception_rules.clear()
        self._exception_tokens.clear()
        self._service.exception_rules.clear()

    def save_state(self, *, save_root_dir: bool = False) -> AppState:
        """현재 CUI 설정·예외 토큰·작업 경로를 통합 설정 파일(data/config.json)에 저장"""
        state = load_app_state()
        state.config = self._service.config
        state.exception_tokens = list(self._exception_tokens)
        if save_root_dir:
            state.root_dir = self._service.root_dir
            state.recent_dirs = record_recent_dir(
                state.recent_dirs, self._service.root_dir
            )
        save_app_state(state, sync_rootdir=save_root_dir)
        return state

    def reload_state(self) -> AppState:
        """통합 설정 파일(data/config.json)과 .rootdir 최신 값을 현재 VideoClassifier에 재적용"""
        state = load_app_state()
        self.set_config(state.config)
        self.set_exception_tokens(state.exception_tokens)
        if state.root_dir and state.root_dir != self._service.root_dir:
            self.set_root_dir(state.root_dir)
        return state

    def get_summary_report(self, ensure_keyframe: bool = False) -> SummaryReport:
        """현재 작업 폴더에 대한 구조화된 종합 분석 리포트 객체(SummaryReport)를 반환"""
        return self._service.evaluate_all(ensure_keyframe=ensure_keyframe)

    def classify(self) -> SummaryReport:
        """퀄리티(CQ 22), 1초 키프레임 보장, 소형 영상 분포 분석을 반영하여 영상을 목적 폴더로 분류"""
        return self._service.classify()

    def print(
        self,
        *,
        sanitize_emoji: bool = True,
        sort_key: Callable[[dict[str, Any]], tuple[Any, ...] | list[Any]] | None = None,
    ) -> None:
        """분류 그룹별 테이블과 3단 용량 시뮬레이션 요약 리포트를 서식에 맞추어 콘솔에 출력"""
        cache = self._service.prepare_cache(force_keyframe=self._service.include_keyframe)
        report = self._service.evaluate_all(ensure_keyframe=self._service.include_keyframe)

        CUIPrinter.print_report(
            cache=cache,
            report=report,
            config=self._service.config,
            sort_key=sort_key,
            filename_maxlen=self._filename_maxlen,
            sanitize_emoji=sanitize_emoji,
        )

    def unclassify_files(
        self, *, unmark_pseudo_classified_only: bool = False
    ) -> tuple[int, list[str]]:
        """하위 분류 폴더의 파일들을 다시 작업 폴더로 안전하게 복원하고 빈 폴더를 정리"""
        moved_count, warnings = self._service.unclassify_files(
            unmark_pseudo_classified_only=unmark_pseudo_classified_only
        )
        log = logging.getLogger(__name__)
        for w in warnings:
            if w.startswith(MOVE_FAILED_PREFIX):
                log.error("%s", w)
            else:
                log.warning("%s", w)
        if unmark_pseudo_classified_only:
            log.info("%s", f"{format_pseudo_reset_done()}.")
        else:
            log.info("%s", f"{format_unclassify_done(moved_count)}.")
        return moved_count, warnings

    def gui(self) -> int:
        """현재 VideoClassifier 인스턴스의 설정과 경로를 기반으로 데스크톱 GUI 창을 실행"""
        gui_mod = importlib.import_module("src.gui")
        return int(gui_mod.run_gui(classifier=self))
