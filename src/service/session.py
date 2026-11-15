"""UI 무관 분류 세션 모듈(GUI, 노트북 파사드 공용)"""

import logging
from collections.abc import Callable

from src.core.config import ClassifierConfig
from src.core.evaluator import ExceptionRule
from src.core.exception_rules import parse_exception_token
from src.core.exceptions import DomainValueError, ErrorCode
from src.core.messages import format_move_issue, format_unclassify_done
from src.core.models import SummaryReport, VideoProps
from src.core.prober import CancelCheck, ProgressCallback
from src.service.classifier_service import VideoClassifierService


class ClassifierSession:
    """작업 폴더, 분류 설정, 예외 규칙 상태를 보유하고 스캔, 평가, 분류 요청을 위임하는 세션"""

    def __init__(
        self,
        root_dir: str,
        config: ClassifierConfig,
        *,
        on_progress: ProgressCallback = None,
        on_is_cancelled: CancelCheck = None,
    ) -> None:
        self._service = VideoClassifierService(
            root_dir=root_dir,
            config=config,
            on_progress=on_progress,
            on_is_cancelled=on_is_cancelled,
        )
        self._custom_exception_rules: list[ExceptionRule] = []
        self._exception_tokens: list[str] = []

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
    def exception_rules(self) -> list[ExceptionRule]:
        """등록된 사용자 정의 예외 규칙 함수 목록 반환"""
        return self._service.exception_rules

    @property
    def exception_tokens(self) -> list[str]:
        """문자열/표현식 토큰으로 등록된 예외 규칙 목록 반환"""
        return list(self._exception_tokens)

    def set_config(self, config: ClassifierConfig) -> None:
        """분류기 내부 설정(ClassifierConfig)을 갱신"""
        self._service.config = config

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

    def include_keyframe_interval(
        self, flag: bool = True, *, eager_prepare: bool = True
    ) -> None:
        """단순 조회(print) 시 키프레임 실측 포함 여부 지정

        classify() 실행 시에는 키프레임 분석 자동 활성화로 별도 호출 불필요
        """
        self._service.include_keyframe = flag
        if eager_prepare and flag and self._service.cache is not None:
            self._service.prepare_cache(force_keyframe=True)

    def pseudo_classify_mode(self, flag: bool = True) -> None:
        """실제 디스크 파일 이동 없이 비파괴 모의 분류 결과만 반환하는 모드를 설정"""
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

    def get_summary_report(self, ensure_keyframe: bool = False) -> SummaryReport:
        """현재 작업 폴더에 대한 구조화된 종합 분석 리포트 객체(SummaryReport)를 반환"""
        return self._service.evaluate_all(ensure_keyframe=ensure_keyframe)

    def get_cached_report(self) -> SummaryReport | None:
        """디스크 재스캔 없이 메모리 캐시 기준 리포트를 반환하고 캐시가 없으면 None 반환"""
        return self._service.evaluate_cached()

    def classify(self) -> SummaryReport:
        """퀄리티(CQ 22), 1초 키프레임 보장, 소형 영상 분포 분석을 반영하여 영상을 목적 폴더로 분류"""
        return self._service.classify()

    def unclassify_files(self) -> tuple[int, list[str]]:
        """하위 분류 폴더의 파일들을 다시 작업 폴더로 안전하게 복원하고 빈 폴더를 정리"""
        moved_count, issues = self._service.unclassify_files()
        log = logging.getLogger(__name__)
        for issue in issues:
            if issue.is_failure:
                log.error("%s", format_move_issue(issue))
            else:
                log.warning("%s", format_move_issue(issue))
        log.info("%s", f"{format_unclassify_done(moved_count)}.")
        return moved_count, [format_move_issue(issue) for issue in issues]
