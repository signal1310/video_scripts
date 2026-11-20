"""재인코딩 배치 한 번의 항목 상태를 모아 상태 값, 툴팁 문장, 하단 요약 문구를 만드는 순수 모듈

- 항목은 원본 경로로 식별하고, 이벤트 전달과 화면 갱신은 호출자(GUI 컨트롤러)가 맡음
- 배치를 시작하면 이전 배치의 상태는 모두 버림. 기록이 없는 행은 상태 값이 없음
- 일시중지는 진행 중 항목에만 덧붙는 표시이며, 상태 코드는 바꾸지 않음
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from src.core.encode.encode_state import EncodeState

STATUS_PENDING: str = "대기"
STATUS_RUNNING: str = "진행 중"
STATUS_RUNNING_PAUSED: str = "진행 중 (일시중지)"
STATUS_DONE: str = "완료"
STATUS_DISCARDED: str = "결과 폐기"
STATUS_FAILED: str = "실패"
STATUS_SKIPPED: str = "건너뜀"
STATUS_REPLACE_PENDING: str = "교체 대기"

STATUS_BY_STATE: dict[EncodeState, str] = {
    EncodeState.PENDING: STATUS_PENDING,
    EncodeState.RUNNING: STATUS_RUNNING,
    EncodeState.DONE: STATUS_DONE,
    EncodeState.DISCARDED: STATUS_DISCARDED,
    EncodeState.FAILED: STATUS_FAILED,
    EncodeState.SKIPPED: STATUS_SKIPPED,
    EncodeState.REPLACE_PENDING: STATUS_REPLACE_PENDING,
}
# - 요약 툴팁의 줄 순서. 일시중지 항목은 진행 중 줄에 함께 셈
SUMMARY_ORDER: tuple[str, ...] = (
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_DONE,
    STATUS_DISCARDED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    STATUS_REPLACE_PENDING,
)
# - 처리 수는 시도가 끝난 항목만 셈. 교체 대기도 결과 파일 처리 단계를 마친 항목으로 봄
PROCESSED_STATES: frozenset[EncodeState] = frozenset(
    {
        EncodeState.DONE,
        EncodeState.DISCARDED,
        EncodeState.FAILED,
        EncodeState.SKIPPED,
        EncodeState.REPLACE_PENDING,
    }
)
# - 결과 사유를 문장 뒤에 붙이는 상태. 대기, 진행 중, 완료는 사유가 없음
REASON_STATES: frozenset[EncodeState] = frozenset(
    {EncodeState.DISCARDED, EncodeState.FAILED, EncodeState.SKIPPED, EncodeState.REPLACE_PENDING}
)
_SENTENCE_BY_STATE: dict[EncodeState, str] = {
    EncodeState.PENDING: "재인코딩 순서를 기다리는 중입니다.",
    EncodeState.RUNNING: "재인코딩하는 중입니다.",
    EncodeState.DONE: "재인코딩을 마쳤습니다.",
    EncodeState.DISCARDED: "재인코딩 결과를 쓰지 않고 지웠습니다. 원본은 그대로입니다.",
    EncodeState.FAILED: "재인코딩하지 못했습니다. 원본은 그대로입니다.",
    EncodeState.SKIPPED: "재인코딩하지 않고 건너뛰었습니다. 원본은 그대로입니다.",
    EncodeState.REPLACE_PENDING: "결과 파일을 원본 자리에 아직 옮기지 못했습니다. 원본과 결과 파일이 모두 남아 있습니다.",
}
PAUSED_TOOLTIP: str = "재인코딩이 일시중지되어 있습니다."
SUMMARY_HEADING: str = "상태별 개수"
PROCESS_SUMMARY_TEMPLATE: str = "재인코딩 ({done}/{total} 처리)"


def status_label(state: EncodeState, paused: bool) -> str:
    """항목 상태의 화면 표기. 일시중지 중인 진행 중 항목만 괄호 표시를 붙임"""
    if state is EncodeState.RUNNING and paused:
        return STATUS_RUNNING_PAUSED
    return STATUS_BY_STATE[state]


def status_tooltip(state: EncodeState, paused: bool, reason: str) -> str:
    """항목 상태의 툴팁 문장. 사유가 있는 상태만 둘째 줄에 결과 사유를 붙임"""
    if state is EncodeState.RUNNING and paused:
        return PAUSED_TOOLTIP
    sentence = _SENTENCE_BY_STATE[state]
    if state in REASON_STATES and reason:
        return f"{sentence}\n사유: {reason}"
    return sentence


@dataclass
class EncodeBatchBook:
    """현재 배치의 경로별 상태와 결과 사유, 일시중지 여부를 담는 저장소"""

    _states: dict[str, EncodeState] = field(default_factory=dict)
    _reasons: dict[str, str] = field(default_factory=dict)
    _paused: bool = False
    _paused_items: set[str] = field(default_factory=set)
    _has_batch: bool = False

    @property
    def has_batch(self) -> bool:
        """배치를 시작한 적이 있으면 참"""
        return self._has_batch

    @property
    def paused(self) -> bool:
        """배치가 일시중지 상태이면 참"""
        return self._paused

    def start(self, paths: Iterable[str]) -> None:
        """새 배치의 대상 경로를 모두 대기로 두고 이전 배치 상태를 버림"""
        self._states = {path: EncodeState.PENDING for path in paths}
        self._reasons = {}
        self._paused = False
        self._paused_items = set()
        self._has_batch = True

    def apply(self, path: str, state: EncodeState, reason: str = "") -> None:
        """항목의 상태와 결과 사유를 갱신. 배치 대상이 아닌 경로는 무시"""
        if path not in self._states:
            return
        self._states[path] = state
        self._reasons[path] = reason
        self._paused_items.discard(path)

    def set_paused(self, paused: bool) -> None:
        """배치 전체의 일시중지 여부를 바꿈. 전체 재개는 개별 일시중지 표시도 모두 지움(실행기와 같은 규칙)"""
        self._paused = paused
        if not paused:
            self._paused_items.clear()

    def set_item_paused(self, path: str, paused: bool) -> None:
        """항목 하나의 일시중지 여부를 바꿈. 배치 대상이 아니면 무시"""
        if path not in self._states:
            return
        if paused:
            self._paused_items.add(path)
        else:
            self._paused_items.discard(path)

    def is_item_paused(self, path: str) -> bool:
        """항목이 개별로 일시중지 상태이면 참"""
        return path in self._paused_items

    def any_paused(self) -> bool:
        """전체 또는 개별 일시중지가 하나라도 있으면 참"""
        return self._paused or bool(self._paused_items)

    def items(self) -> tuple[tuple[str, EncodeState, str], ...]:
        """배치 대상의 (경로, 상태, 결과 사유)를 대상 순서대로 반환"""
        return tuple((path, state, self._reasons.get(path, "")) for path, state in self._states.items())

    def _is_paused(self, path: str) -> bool:
        return self._paused or path in self._paused_items

    def state_of(self, path: str) -> EncodeState | None:
        """경로의 상태 코드. 배치 대상이 아니면 None"""
        return self._states.get(path)

    def label_of(self, path: str) -> str | None:
        """경로의 상태 표기. 배치 대상이 아니면 None"""
        state = self._states.get(path)
        return None if state is None else status_label(state, self._is_paused(path))

    def tooltip_of(self, path: str) -> str | None:
        """경로의 상태 툴팁 문장. 배치 대상이 아니면 None"""
        state = self._states.get(path)
        if state is None:
            return None
        return status_tooltip(state, self._is_paused(path), self._reasons.get(path, ""))

    def total(self) -> int:
        """배치 대상 항목 수"""
        return len(self._states)

    def processed(self) -> int:
        """시도가 끝난 항목 수"""
        return sum(1 for state in self._states.values() if state in PROCESSED_STATES)

    def counts_by_status(self) -> dict[str, int]:
        """요약 툴팁 순서대로 상태 표기별 항목 수. 일시중지 항목은 진행 중에 셈"""
        counts = dict.fromkeys(SUMMARY_ORDER, 0)
        for state in self._states.values():
            counts[STATUS_BY_STATE[state]] += 1
        return counts

    def summary_text(self) -> str:
        """하단 요약 칸 문구"""
        return PROCESS_SUMMARY_TEMPLATE.format(done=self.processed(), total=self.total())

    def summary_tooltip(self) -> str:
        """하단 요약 칸 툴팁. 첫 줄은 제목, 다음 줄부터 상태별 개수"""
        lines = [SUMMARY_HEADING]
        lines.extend(f"{status} {count}개" for status, count in self.counts_by_status().items())
        return "\n".join(lines)
