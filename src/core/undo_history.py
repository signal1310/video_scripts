"""다단계 되돌리기, 다시 실행 히스토리 스택과 연속 변경 병합 규칙을 정의하는 순수 Python 모듈"""

from __future__ import annotations

from dataclasses import dataclass, field

from src.core.config import ClassifierConfig
from src.core.resolution_tiers import copy_manual_targets
from src.core.settings.ui_state import DEFAULT_ANALYZE_MODE, DEFAULT_FILTER_SEARCH, DEFAULT_THEME_MODE

MAX_UNDO_STEPS: int = 50
UNDO_COALESCE_WINDOW_MS: int = 800


@dataclass(frozen=True, slots=True)
class UndoableSnapshot:
    """되돌리기 대상 상태의 스냅샷, 선택 영상 목록은 상태 비교에서 제외

    - 선택 영상은 복원 시 행 선택을 맞추기 위한 보조 정보이므로 변경 여부 판정에 쓰지 않음
    - 열 상태는 열 편집 단계에서만 채우고, 그 외 단계는 빈 문자열로 두어 자동 열 너비 변화가 다른 단계에 섞이지 않게 함
    """

    config: ClassifierConfig
    exception_tokens: tuple[str, ...]
    disabled_exception_tokens: tuple[str, ...]
    manual_target_tiers: dict[str, dict[str, str]]
    selected_filenames: frozenset[str] = field(default_factory=frozenset)
    shortcuts: dict[str, str] = field(default_factory=dict)
    theme_mode: str = DEFAULT_THEME_MODE
    analyze_mode: str = DEFAULT_ANALYZE_MODE
    recursive_scan: bool = False
    show_setting_hints: bool = True
    search: str = DEFAULT_FILTER_SEARCH
    column_layout: str = ""

    def same_data(self, other: UndoableSnapshot) -> bool:
        """분류 결과에 영향을 주는 설정, 예외 규칙, 직접 지정 상태가 같은지 비교"""
        return (
            self.config == other.config
            and self.exception_tokens == other.exception_tokens
            and self.disabled_exception_tokens == other.disabled_exception_tokens
            and self.manual_target_tiers == other.manual_target_tiers
        )

    def same_state(self, other: UndoableSnapshot) -> bool:
        """선택 영상을 제외한 모든 되돌리기 대상 상태가 같은지 비교"""
        return (
            self.same_data(other)
            and self.shortcuts == other.shortcuts
            and self.theme_mode == other.theme_mode
            and self.analyze_mode == other.analyze_mode
            and self.recursive_scan == other.recursive_scan
            and self.show_setting_hints == other.show_setting_hints
            and self.search == other.search
            and self.column_layout == other.column_layout
        )

    def clone(self) -> UndoableSnapshot:
        """가변 컬렉션을 분리한 독립 복사본 반환"""
        return UndoableSnapshot(
            config=self.config.copy(),
            exception_tokens=self.exception_tokens,
            disabled_exception_tokens=self.disabled_exception_tokens,
            manual_target_tiers=copy_manual_targets(self.manual_target_tiers),
            selected_filenames=self.selected_filenames,
            shortcuts=dict(self.shortcuts),
            theme_mode=self.theme_mode,
            analyze_mode=self.analyze_mode,
            recursive_scan=self.recursive_scan,
            show_setting_hints=self.show_setting_hints,
            search=self.search,
            column_layout=self.column_layout,
        )


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """히스토리 한 단계의 표시 라벨, 변경 전후 스냅샷, 병합 키와 기록 시각"""

    label: str
    before: UndoableSnapshot
    after: UndoableSnapshot
    coalesce_key: str | None = None
    timestamp_ms: int = 0


class UndoHistory:
    """되돌리기와 다시 실행 스택을 관리하며 시간 창 안의 연속 변경을 한 단계로 병합"""

    def __init__(self) -> None:
        self._undo_stack: list[HistoryEntry] = []
        self._redo_stack: list[HistoryEntry] = []

    @property
    def can_undo(self) -> bool:
        """되돌릴 단계가 있는지 반환"""
        return bool(self._undo_stack)

    @property
    def can_redo(self) -> bool:
        """다시 실행할 단계가 있는지 반환"""
        return bool(self._redo_stack)

    @property
    def undo_label(self) -> str | None:
        """다음에 되돌릴 단계의 라벨, 없으면 None"""
        return self._undo_stack[-1].label if self._undo_stack else None

    @property
    def redo_label(self) -> str | None:
        """다음에 다시 실행할 단계의 라벨, 없으면 None"""
        return self._redo_stack[-1].label if self._redo_stack else None

    @property
    def undo_count(self) -> int:
        """되돌리기 스택의 단계 수 반환"""
        return len(self._undo_stack)

    @property
    def redo_count(self) -> int:
        """다시 실행 스택의 단계 수 반환"""
        return len(self._redo_stack)

    def clear(self) -> None:
        """두 스택을 모두 비움"""
        self._undo_stack.clear()
        self._redo_stack.clear()

    def record(
        self,
        label: str,
        before: UndoableSnapshot,
        after: UndoableSnapshot,
        *,
        coalesce_key: str | None = None,
        timestamp_ms: int = 0,
    ) -> bool:
        """변경 전후 상태를 한 단계로 기록하고 실제 상태 변화가 있었는지 반환

        - 상태 변화가 없으면 스택을 건드리지 않음
        - 새 변경이 기록되면 다시 실행 스택은 비움(표준 Undo/Redo 모델)
        - 같은 병합 키의 연속 변경이 병합 창 안에 발생하면 마지막 단계의 after만 갱신
        """
        if before.same_state(after):
            return False
        self._redo_stack.clear()
        if coalesce_key is not None and self._undo_stack:
            top = self._undo_stack[-1]
            if self._can_coalesce(top, coalesce_key, timestamp_ms):
                if top.before.same_state(after):
                    self._undo_stack.pop()
                    return False
                self._undo_stack[-1] = HistoryEntry(
                    label=label,
                    before=top.before,
                    after=after.clone(),
                    coalesce_key=coalesce_key,
                    timestamp_ms=timestamp_ms,
                )
                return True
        self._undo_stack.append(
            HistoryEntry(
                label=label,
                before=before.clone(),
                after=after.clone(),
                coalesce_key=coalesce_key,
                timestamp_ms=timestamp_ms,
            )
        )
        if len(self._undo_stack) > MAX_UNDO_STEPS:
            del self._undo_stack[0]
        return True

    def undo(self) -> HistoryEntry | None:
        """최근 단계를 다시 실행 스택으로 옮기고 반환, 호출자는 entry.before를 적용"""
        if not self._undo_stack:
            return None
        entry = self._undo_stack.pop()
        self._redo_stack.append(entry)
        return entry

    def redo(self) -> HistoryEntry | None:
        """다시 실행 스택의 최근 단계를 되돌리기 스택으로 옮기고 반환, 호출자는 entry.after를 적용"""
        if not self._redo_stack:
            return None
        entry = self._redo_stack.pop()
        self._undo_stack.append(entry)
        return entry

    @staticmethod
    def _can_coalesce(top: HistoryEntry, coalesce_key: str, timestamp_ms: int) -> bool:
        """같은 병합 키이고 직전 기록 이후 병합 창 안에 들어온 변경인지 판정"""
        if top.coalesce_key != coalesce_key:
            return False
        return 0 <= timestamp_ms - top.timestamp_ms <= UNDO_COALESCE_WINDOW_MS
