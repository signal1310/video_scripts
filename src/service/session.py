"""UI 무관 분류 세션 모듈(GUI, 노트북 파사드 공용)"""

import logging
import os
from collections.abc import Callable, Iterable, Sequence

from src.core.classification_policy import ExceptionRule
from src.core.config import ClassifierConfig
from src.core.exception_rules import validate_exception_token
from src.core.exceptions import DomainFileNotFoundError, DomainValueError, ErrorCode
from src.core.messages import (
    format_invalid_root_dir_msg,
    format_move_issue,
)
from src.core.models import SummaryReport, VideoProps
from src.core.prober import CancelCheck, ProgressCallback
from src.core.resolution_tiers import copy_manual_targets, is_valid_manual_target
from src.core.workspace_paths import are_all_valid_dirs, format_root_dirs, parse_root_dirs
from src.service.analysis_store import AnalysisStore
from src.service.analysis_timing import (
    MODE_FAST,
    MODE_FULL,
    AnalysisEstimate,
    expected_analysis_seconds,
)
from src.service.cache import DEFAULT_CACHE_REGISTRY, CacheRegistry
from src.service.classifier_service import VideoClassifierService

logger = logging.getLogger(__name__)


def normalize_root_key(root_dir: str) -> str:
    """작업 폴더 목록을 구분자, 대소문자, 끝 구분자 표기와 무관한 직접 지정 저장 키로 변환

    - 조회, 저장, 불러오기가 모두 이 함수를 거쳐야 같은 폴더 목록이 한 항목으로 묶임
    """
    return ";".join(
        os.path.normcase(os.path.normpath(path)) for path in parse_root_dirs(root_dir)
    )


class ClassifierSession:
    """작업 폴더, 분류 설정, 예외 규칙 상태를 보유하고 스캔, 평가, 분류 요청을 위임하는 세션"""

    def __init__(
        self,
        root_dir: str,
        config: ClassifierConfig,
        *,
        on_progress: ProgressCallback = None,
        on_is_cancelled: CancelCheck = None,
        registry: CacheRegistry = DEFAULT_CACHE_REGISTRY,
        store: AnalysisStore | None = None,
    ) -> None:
        self._service = VideoClassifierService(
            root_dir=root_dir,
            config=config,
            on_progress=on_progress,
            on_is_cancelled=on_is_cancelled,
            registry=registry,
            store=store,
        )
        self._custom_exception_rules: list[ExceptionRule] = []
        self._exception_tokens: list[str] = []
        # - 작업 폴더 경로 -> 파일명 -> 규격 키 또는 축소 안 함 값. 현재 작업 폴더 몫만 서비스에 동기화
        self._manual_target_store: dict[str, dict[str, str]] = {}

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
        """현재 설정된 작업 폴더 루트 경로 반환(다중 폴더면 세미콜론 구분)"""
        return self._service.root_dir

    @property
    def root_dirs(self) -> tuple[str, ...]:
        """현재 작업 폴더 목록 반환(주 작업 폴더가 첫 번째)"""
        return self._service.root_dirs

    @property
    def recursive(self) -> bool:
        """하위 폴더 포함 분석 여부"""
        return self._service.recursive

    def set_recursive(self, flag: bool) -> None:
        """하위 폴더 포함 분석 여부를 바꾸고 캐시 범위를 동기화"""
        self._service.recursive = bool(flag)
        self._service.sync_cache_state()

    @property
    def is_pseudo_classify(self) -> bool:
        """현재 모의 분류 모드 활성화 여부"""
        return self._service.pseudo_classify

    @property
    def include_keyframe(self) -> bool:
        """키프레임 정밀 분석을 포함하도록 요청된 상태인지 여부"""
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
        """대상 작업 폴더를 변경하고 캐시 상태를 동기화(세미콜론 구분 다중 경로 허용)"""
        if not root_dir:
            raise DomainValueError(
                "유효한 작업 폴더 경로를 지정해야 합니다.",
                code=ErrorCode.ROOT_DIR_INVALID,
            )
        if not are_all_valid_dirs(root_dir):
            raise DomainFileNotFoundError(
                format_invalid_root_dir_msg(root_dir),
                code=ErrorCode.ROOT_DIR_INVALID,
            )
        self._service.root_dir = root_dir
        self._service.sync_cache_state()
        self._sync_manual_targets()

    def set_root_dirs(self, paths: Sequence[str]) -> None:
        """작업 폴더 목록을 한 번에 바꿈(모든 경로가 실존 폴더여야 함)"""
        self.set_root_dir(format_root_dirs(parse_root_dirs(paths)))

    def add_root_dir(self, path: str) -> bool:
        """현재 작업 폴더를 유지한 채 새 작업 폴더를 목록 끝에 추가

        - 이미 등록된 폴더면 아무 것도 바꾸지 않고 False 반환
        """
        current = self.root_dirs
        merged = parse_root_dirs([*current, path])
        if len(merged) == len(current):
            return False
        self.set_root_dirs(merged)
        return True

    def set_callbacks(
        self,
        on_progress: ProgressCallback = None,
        on_is_cancelled: CancelCheck = None,
    ) -> None:
        """진행률 보고 및 작업 중단 확인용 콜백 함수 설정"""
        self._service.on_progress = on_progress
        self._service.on_is_cancelled = on_is_cancelled

    def invalidate_cache(self) -> None:
        """현재 작업 폴더의 인메모리 캐시를 초기화하여 다음 조회 시 디스크 재스캔을 유도"""
        self._service.invalidate_cache()

    def _rebuild_exception_rules(self) -> None:
        token_rules: list[ExceptionRule] = []
        for token in self._exception_tokens:
            parsed, error = validate_exception_token(token)
            if parsed is not None:
                token_rules.append(parsed)
            elif error is not None:
                # - 무효 토큰은 보호 규칙 없이 분류가 진행될 수 있으므로 조용히 건너뛰지 않고 알림
                logger.warning("예외 규칙을 적용하지 못했습니다: %s", error)
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

    @property
    def manual_target_tiers(self) -> dict[str, str]:
        """현재 작업 폴더의 출력 해상도 직접 지정 맵(파일명 -> 규격 키 또는 축소 안 함) 사본 반환"""
        return dict(self._manual_target_store.get(normalize_root_key(self.root_dir), {}))

    @property
    def manual_target_store(self) -> dict[str, dict[str, str]]:
        """전체 작업 폴더의 출력 해상도 직접 지정 맵 사본 반환(저장용, 키는 정규화된 폴더 경로, 항목이 없는 폴더 제외)"""
        return copy_manual_targets(self._manual_target_store, drop_empty=True)

    def set_manual_target_store(self, store: dict[str, dict[str, str]]) -> None:
        """저장된 전체 작업 폴더의 직접 지정 맵을 적용하고 현재 작업 폴더 몫을 평가에 반영

        - 표기가 다른 옛 키(구분자, 대소문자 차이)가 같은 폴더면 한 항목으로 합침
        """
        merged: dict[str, dict[str, str]] = {}
        for root, entries in store.items():
            merged.setdefault(normalize_root_key(root), {}).update(entries)
        self._manual_target_store = {root: entries for root, entries in merged.items() if entries}
        self._sync_manual_targets()

    def set_manual_target_tier(self, filenames: Iterable[str], value: str | None) -> None:
        """현재 작업 폴더의 파일들에 출력 해상도 직접 지정을 설정하고, None이면 자동 판정으로 되돌림"""
        if value is not None and not is_valid_manual_target(value):
            raise DomainValueError(
                "출력 해상도 지정 값이 올바르지 않습니다.",
                code=ErrorCode.MANUAL_TARGET_INVALID,
            )
        entries = self._manual_target_store.setdefault(normalize_root_key(self.root_dir), {})
        for name in filenames:
            if value is None:
                entries.pop(name, None)
            else:
                entries[name] = value
        self._drop_empty_manual_targets()
        self._sync_manual_targets()

    def clear_manual_target_tiers(self) -> None:
        """현재 작업 폴더의 출력 해상도 직접 지정을 모두 해제"""
        self._manual_target_store.pop(normalize_root_key(self.root_dir), None)
        self._sync_manual_targets()

    def _prune_manual_target_tiers(self) -> None:
        """분석 시점에 작업 폴더와 분류 폴더 어디에도 없는 파일명의 직접 지정 항목을 삭제"""
        entries = self._manual_target_store.get(normalize_root_key(self.root_dir))
        if not entries:
            return
        known = self._service.known_video_filenames()
        for name in [name for name in entries if name not in known]:
            del entries[name]
        self._drop_empty_manual_targets()
        self._sync_manual_targets()

    def _drop_empty_manual_targets(self) -> None:
        """항목이 모두 해제된 작업 폴더를 저장소에서 제거"""
        key = normalize_root_key(self.root_dir)
        entries = self._manual_target_store.get(key)
        if entries is not None and not entries:
            del self._manual_target_store[key]

    def _sync_manual_targets(self) -> None:
        """현재 작업 폴더의 직접 지정 맵을 서비스에 복사해 전달(평가 경로가 세션 저장소를 직접 참조하지 않도록 함)"""
        self._service.manual_targets = self.manual_target_tiers

    def sync_renamed_manual_targets(self) -> bool:
        """지문 매칭으로 복구된 이름 변경 쌍에 맞춰 출력 해상도 직접 지정 키를 이관"""
        cache = self._service.cache
        if cache is None or not cache.renamed_pairs:
            return False
        entries = self._manual_target_store.get(normalize_root_key(self.root_dir))
        if not entries:
            return False
        changed = False
        for old_name, new_name in cache.renamed_pairs.items():
            if old_name in entries and new_name not in entries:
                entries[new_name] = entries.pop(old_name)
                changed = True
        if changed:
            self._sync_manual_targets()
        return changed

    def get_summary_report(self, ensure_keyframe: bool = False) -> SummaryReport:
        """현재 작업 폴더에 대한 구조화된 종합 분석 리포트 객체(SummaryReport)를 반환"""
        self._service.prepare_cache(force_keyframe=ensure_keyframe)
        self.sync_renamed_manual_targets()
        self._prune_manual_target_tiers()
        return self._service.evaluate_all(ensure_keyframe=ensure_keyframe)

    def cached_report_job(self) -> Callable[[], SummaryReport] | None:
        """캐시 기준 재평가를 백그라운드에서 실행할 작업, 캐시가 없으면 None"""
        return self._service.cached_report_job()

    def get_cached_report(self) -> SummaryReport | None:
        """디스크 재스캔 없이 메모리 캐시 기준 리포트를 반환하고 캐시가 없으면 None 반환"""
        return self._service.evaluate_cached()

    def estimate_analysis(self, include_keyframe: bool) -> AnalysisEstimate:
        """분석 시작 전 대상 영상 수와 이력 기반 예상 소요 초를 반환"""
        count = self._service.pending_count(include_keyframe)
        mode = MODE_FULL if include_keyframe else MODE_FAST
        return AnalysisEstimate(count, expected_analysis_seconds(mode, count))

    def classify(self, *, dry_run: bool | None = None) -> SummaryReport:
        """퀄리티(CQ 22), 1초 키프레임 보장, 소형 영상 분포 분석을 반영하여 영상을 목적 폴더로 분류

        dry_run 생략 시 모의 분류 모드 설정을 따름
        """
        self._service.prepare_cache(force_keyframe=True)
        self.sync_renamed_manual_targets()
        self._prune_manual_target_tiers()
        return self._service.classify(dry_run=dry_run)

    def classify_with_result(
        self, *, dry_run: bool | None = None
    ) -> tuple[SummaryReport, int, list[str]]:
        """분류 후 (리포트, 실제 이동 파일 수, 충돌 안내 문구 리스트)를 반환"""
        self._service.prepare_cache(force_keyframe=True)
        self.sync_renamed_manual_targets()
        self._prune_manual_target_tiers()
        report, moved_count, issues = self._service.classify_with_result(dry_run=dry_run)
        return report, moved_count, [format_move_issue(issue) for issue in issues]

    def preview_unclassify(self) -> tuple[int, int]:
        """파일을 이동하지 않고 (복원 예정 영상 수, 이름 충돌 영상 수) 반환"""
        return self._service.preview_unclassify()

    def unclassify_files(self) -> tuple[int, list[str]]:
        """하위 분류 폴더의 파일들을 다시 작업 폴더로 안전하게 복원하고 빈 폴더를 정리"""
        moved_count, issues = self._service.unclassify_files()
        log = logging.getLogger(__name__)
        for issue in issues:
            if issue.is_failure:
                log.error("%s", format_move_issue(issue))
            else:
                log.warning("%s", format_move_issue(issue))
        return moved_count, [format_move_issue(issue) for issue in issues]
