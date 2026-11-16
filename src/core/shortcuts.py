"""단축키 명령 카탈로그, 기본값 정규화, 중복 해소, 툴팁 표기 기준 정의 모듈"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from src.core.resolution_tiers import RESOLUTION_TIERS, Orientation, ResolutionTier

REDO_SECONDARY_SHORTCUT: str = "Ctrl+Shift+Z"
# - 단독 Enter는 Return과 같은 입력이므로 기본값 비교가 어긋나지 않도록 한 표기로 통일
_ENTER_ALIAS: str = "Enter"
_ENTER_CANONICAL: str = "Return"
_SHIFT_TOKEN: str = "Shift"
# 미국 자판 기준 Shift+숫자가 전달하는 기호 키, 키보드 이벤트는 숫자 대신 이 기호로 도착함
_SHIFTED_DIGIT_SYMBOLS: dict[str, str] = {
    "0": ")",
    "1": "!",
    "2": "@",
    "3": "#",
    "4": "$",
    "5": "%",
    "6": "^",
    "7": "&",
    "8": "*",
    "9": "(",
}
_SYMBOL_DIGITS: dict[str, str] = {symbol: digit for digit, symbol in _SHIFTED_DIGIT_SYMBOLS.items()}


@dataclass(frozen=True, slots=True)
class ShortcutGroup:
    """단축키 설정 화면의 기능 그룹 하나(식별 키, 제목, 아이콘 이름)"""

    key: str
    title: str
    icon_name: str


@dataclass(frozen=True, slots=True)
class ShortcutSpec:
    """명령 하나의 식별자, 표시명, 기본 단축키, 도움말 설명"""

    group_key: str
    action_id: str
    label: str
    default_key: str
    description: str


SHORTCUT_GROUPS: tuple[ShortcutGroup, ...] = (
    ShortcutGroup("file", "작업 폴더 및 파일", "folder"),
    ShortcutGroup("analyze", "영상 분석 및 캐시", "analyze"),
    ShortcutGroup("selection", "선택 영상 조작", "play"),
    ShortcutGroup("manual_target", "출력 해상도 지정", "monitor"),
    ShortcutGroup("exception", "예외 규칙 관리", "ban"),
    ShortcutGroup("classify", "분류 이동 및 복원", "folder-open"),
    ShortcutGroup("filter_table", "검색, 필터 및 표 정렬", "filter"),
    ShortcutGroup("app_view", "설정, 화면 및 도움말", "settings"),
)


def _build_tier_specs() -> tuple[ShortcutSpec, ...]:
    """규격 오름차순으로 Ctrl+1부터 순서대로 단축키를 배정한 출력 해상도 명령 생성"""
    specs: list[ShortcutSpec] = []
    for index, tier in enumerate(RESOLUTION_TIERS, start=1):
        specs.append(_tier_spec(index, tier))
    return tuple(specs)


def _tier_spec(index: int, tier: ResolutionTier) -> ShortcutSpec:
    """해상도 규격 하나를 출력 해상도 지정 명령으로 변환"""
    width, height = tier.box(Orientation.LANDSCAPE)
    resolution = f"{tier.display} ({width}x{height})"
    return ShortcutSpec(
        group_key="manual_target",
        action_id=f"manual_target.{tier.key}",
        label=f"출력 해상도 지정: {resolution}",
        default_key=f"Ctrl+{index}",
        description=f"선택한 영상의 출력 해상도를 {resolution} 규격으로 지정합니다.",
    )


_TIER_SPECS: tuple[ShortcutSpec, ...] = _build_tier_specs()

SHORTCUT_SPECS: tuple[ShortcutSpec, ...] = (
    ShortcutSpec("file", "file.open_dir", "작업 폴더 열기", "Ctrl+O", "분석할 작업 비디오 폴더를 선택합니다."),
    ShortcutSpec("file", "file.add_dir", "작업 폴더 추가", "Ctrl+Shift+O", "현재 작업 폴더를 유지한 채 다른 작업 폴더를 함께 등록합니다."),
    ShortcutSpec("file", "file.edit_path", "작업 폴더 경로 직접 입력", "Alt+D", "상단 작업 폴더 경로 입력창으로 포커스를 이동합니다."),
    ShortcutSpec("file", "file.open_explorer", "탐색기에서 작업 폴더 열기", "", "현재 작업 폴더를 파일 탐색기에서 엽니다."),
    ShortcutSpec("file", "file.clear_recent", "최근 작업 폴더 목록 비우기", "", "최근 작업 폴더 기록을 모두 비웁니다."),
    ShortcutSpec("file", "file.export_csv", "CSV 내보내기", "Ctrl+E", "현재 필터, 정렬된 테이블 결과를 CSV 파일로 저장합니다."),
    ShortcutSpec("file", "file.exit", "종료", "Ctrl+Q", "애플리케이션을 종료합니다."),
    ShortcutSpec("analyze", "analyze.run_default", "현재 모드로 분석", "F5", "현재 선택된 분석 모드(빠른 분석 또는 정밀 분석)를 실행합니다."),
    ShortcutSpec("analyze", "analyze.force_reanalyze", "강제 재분석 (캐시 무시 정밀 분석)", "Shift+F5", "기존 캐시를 무시하고 작업 폴더 전체를 다시 정밀 분석합니다."),
    ShortcutSpec("analyze", "analyze.run_fast", "빠른 분석 (고속)", "", "키프레임 측정을 생략하고 해상도, 비트레이트를 빠르게 분석합니다."),
    ShortcutSpec("analyze", "analyze.run_full", "정밀 분석 (키프레임 포함)", "", "키프레임 간격 실측을 포함한 전체 정밀 분석을 실행합니다."),
    ShortcutSpec("analyze", "analyze.cancel", "진행 중인 작업 중단", "Esc", "실행 중인 백그라운드 분석, 분류 작업을 중단합니다."),
    ShortcutSpec("analyze", "analyze.mode_fast", "기본 분석 모드: 빠른 분석", "", "기본 분석 모드를 빠른 분석으로 전환합니다."),
    ShortcutSpec("analyze", "analyze.mode_full", "기본 분석 모드: 정밀 분석", "", "기본 분석 모드를 정밀 분석으로 전환합니다."),
    ShortcutSpec("analyze", "analyze.toggle_recursive", "하위 폴더 포함", "", "작업 폴더 아래의 하위 폴더 영상까지 함께 분석할지 전환합니다."),
    ShortcutSpec("analyze", "file.clear_cache", "분석 캐시 비우기", "", "저장된 분석 결과를 모두 삭제합니다."),
    ShortcutSpec("selection", "edit.undo", "되돌리기", "Ctrl+Z", "직전에 변경한 출력 해상도 지정, 예외 규칙, 판별 기준, 설정, 단축키, 열 너비, 표시, 순서를 이전 상태로 되돌립니다."),
    ShortcutSpec("selection", "edit.redo", "다시 실행", "Ctrl+Y", "되돌린 출력 해상도 지정, 예외 규칙, 판별 기준, 설정, 단축키, 열 너비, 표시, 순서 변경을 다시 적용합니다."),
    ShortcutSpec("selection", "classify.play_selected", "선택 영상 재생", "Return", "선택한 영상을 기본 미디어 플레이어로 재생합니다."),
    ShortcutSpec("selection", "classify.reveal_selected", "탐색기에서 위치 열기", "Ctrl+Shift+R", "선택한 영상 파일의 위치를 파일 탐색기에서 엽니다."),
    ShortcutSpec("selection", "classify.copy_names", "선택 파일명 복사", "Ctrl+C", "선택한 행의 파일명을 클립보드에 복사합니다."),
    ShortcutSpec("selection", "classify.copy_paths", "선택 전체 경로 복사", "Ctrl+Shift+C", "선택한 행의 전체 절대 경로를 클립보드에 복사합니다."),
    ShortcutSpec("manual_target", "manual_target.open_menu", "출력 해상도 지정 메뉴 열기", "Ctrl+R", "선택한 행 위치에 출력 해상도 지정 팝업 메뉴를 엽니다."),
    ShortcutSpec("manual_target", "manual_target.auto", "출력 해상도 지정: 자동", "Ctrl+0", "선택한 영상의 출력 해상도 직접 지정을 해제하고 자동 판정으로 되돌립니다."),
    ShortcutSpec("manual_target", "manual_target.keep", "출력 해상도 지정: 축소 안 함", "Ctrl+9", "선택한 영상을 해상도 축소 없이 원본 해상도로 유지합니다."),
    *_TIER_SPECS,
    ShortcutSpec("manual_target", "manual_target.clear_all", "출력 해상도 직접 지정 모두 해제", "Ctrl+Shift+0", "현재 작업 폴더의 모든 출력 해상도 직접 지정을 한꺼번에 해제합니다."),
    ShortcutSpec("exception", "edit.exception_editor", "예외 규칙 편집기", "Ctrl+Shift+E", "여러 줄 예외 규칙 목록과 활성 여부를 편집합니다."),
    ShortcutSpec("exception", "classify.add_exception", "선택 항목 예외 규칙에 추가", "Delete", "선택한 파일의 이름 키워드를 예외 규칙에 추가합니다."),
    ShortcutSpec("exception", "classify.remove_exception", "선택 항목 예외 규칙에서 제거", "Shift+Delete", "선택한 파일의 이름 키워드를 예외 규칙에서 제거합니다."),
    ShortcutSpec("classify", "classify.real_classify", "분류 폴더로 이동", "Ctrl+M", "분류 대상 영상을 목적 하위 폴더로 실제 이동합니다."),
    ShortcutSpec("classify", "classify.unclassify_all", "작업 폴더로 복원", "Ctrl+Shift+U", "하위 폴더로 이동된 영상을 원래 작업 폴더로 복원합니다."),
    ShortcutSpec("filter_table", "edit.focus_search", "검색 포커스", "Ctrl+F", "파일명, 코덱 검색 입력창으로 포커스를 이동합니다."),
    ShortcutSpec("filter_table", "edit.reset_filters", "필터 초기화", "Ctrl+Shift+F", "검색어 및 모든 열 필터를 기본값으로 초기화합니다."),
    ShortcutSpec("filter_table", "edit.clear_column_filters", "모든 열 필터 해제", "", "적용된 모든 열 헤더 필터를 해제합니다."),
    ShortcutSpec("filter_table", "view.reset_sort", "기본 정렬로 복원", "", "테이블 정렬 상태를 기본 파일명 자연 정렬로 초기화합니다."),
    ShortcutSpec("filter_table", "view.reset_columns", "기본 열 너비, 표시 복원", "", "숨겨진 열을 다시 표시하고 기본 열 너비와 순서로 복원합니다."),
    ShortcutSpec("app_view", "edit.open_settings", "환경 설정", "Ctrl+,", "환경 설정 탭을 엽니다."),
    ShortcutSpec("app_view", "edit.open_tuner", "판별 기준 조정", "Ctrl+T", "판별 기준 실시간 조정 탭을 엽니다."),
    ShortcutSpec("app_view", "edit.open_shortcuts", "단축키 설정", "Ctrl+K", "단축키 사용자 지정 탭을 엽니다."),
    ShortcutSpec("app_view", "view.show_logs", "작업 로그 보기", "Ctrl+L", "분석, 분류 작업 로그 뷰어 대화상자를 엽니다."),
    ShortcutSpec("app_view", "view.toggle_top_area", "상단 영역 접기/펼치기", "Ctrl+F1", "작업 폴더, 분석 요약, 필터 영역을 접거나 펼칩니다."),
    ShortcutSpec("app_view", "view.theme_system", "테마: 시스템 설정 따라가기", "", "운영체제 테마 설정을 따르도록 전환합니다."),
    ShortcutSpec("app_view", "view.theme_dark", "테마: 다크 테마", "", "다크 테마로 전환합니다."),
    ShortcutSpec("app_view", "view.theme_light", "테마: 라이트 테마", "", "라이트 테마로 전환합니다."),
    ShortcutSpec("app_view", "help.open_guide", "사용 가이드", "F1", "프로그램 안내, 판정 원리, 예외 조건식 및 단축키 가이드를 엽니다."),
    ShortcutSpec("app_view", "help.open_presets_dir", "샤나인코더 프리셋 폴더 열기", "", "shana_presets 폴더를 파일 탐색기에서 엽니다."),
)

SHORTCUT_SPEC_BY_ID: dict[str, ShortcutSpec] = {spec.action_id: spec for spec in SHORTCUT_SPECS}
DEFAULT_SHORTCUTS: dict[str, str] = {spec.action_id: spec.default_key for spec in SHORTCUT_SPECS}


def normalize_shortcut(raw: str) -> str:
    """단축키 문자열의 앞뒤 공백을 제거하고, 단독 Enter는 Return으로, Shift 조합의 기호는 숫자로 통일

    - Shift+숫자를 누르면 입력 기호(예: `Ctrl+Shift+(`)가 들어오므로 저장 표기는 숫자로 되돌림
    """
    text = raw.strip()
    if text == _ENTER_ALIAS:
        return _ENTER_CANONICAL
    head, sep, last = text.rpartition("+")
    if sep and last in _SYMBOL_DIGITS and _SHIFT_TOKEN in head.split("+"):
        return f"{head}+{_SYMBOL_DIGITS[last]}"
    return text


def shifted_symbol_variant(key: str) -> str | None:
    """Shift+숫자 단축키의 기호 키 표기를 반환하며, 해당하지 않으면 None 반환

    - 숫자 표기만 바인딩하면 Shift를 누른 입력이 매칭되지 않으므로 기호 표기를 함께 바인딩하는 데 씀
    """
    head, sep, last = key.rpartition("+")
    if not sep or last not in _SHIFTED_DIGIT_SYMBOLS or _SHIFT_TOKEN not in head.split("+"):
        return None
    return f"{head}+{_SHIFTED_DIGIT_SYMBOLS[last]}"


def sanitize_shortcut_overrides(raw: Any) -> dict[str, str]:
    """저장값에서 알려진 명령의 문자열 단축키만 수용하고 중복, 기본값과 같은 항목을 제거

    - 같은 키가 여러 명령에 중복되면 먼저 등장한 항목만 유지
    - 기본값과 같은 항목은 저장하지 않아 오버라이드 사전을 최소 상태로 유지
    """
    if not isinstance(raw, dict):
        return {}
    raw_map: dict[str, Any] = raw
    accepted: dict[str, str] = {}
    claimed_keys: set[str] = set()
    for action_id, value in raw_map.items():
        spec = SHORTCUT_SPEC_BY_ID.get(action_id)
        if spec is None or not isinstance(value, str):
            continue
        key = normalize_shortcut(value)
        if key:
            if key.lower() in claimed_keys:
                continue
            claimed_keys.add(key.lower())
        if key != spec.default_key:
            accepted[action_id] = key
    return accepted


def resolve_shortcuts(overrides: Mapping[str, str] | None = None) -> dict[str, str]:
    """기본값에 오버라이드를 적용하고, 새로 배정된 키와 겹치는 기본 배정은 비워 중복을 방지"""
    effective: dict[str, str] = dict(DEFAULT_SHORTCUTS)
    if not overrides:
        return effective
    applied: dict[str, str] = {
        action_id: normalize_shortcut(key)
        for action_id, key in overrides.items()
        if action_id in SHORTCUT_SPEC_BY_ID
    }
    effective.update(applied)
    for key in applied.values():
        if not key:
            continue
        lowered = key.lower()
        for spec in SHORTCUT_SPECS:
            if spec.action_id not in applied and spec.default_key.lower() == lowered:
                effective[spec.action_id] = ""
    return effective


def diff_shortcut_overrides(effective: Mapping[str, str]) -> dict[str, str]:
    """유효 단축키 중 기본값과 다른 항목만 골라 최소 오버라이드 사전으로 변환"""
    overrides: dict[str, str] = {}
    for spec in SHORTCUT_SPECS:
        key = normalize_shortcut(effective.get(spec.action_id, spec.default_key))
        if key != spec.default_key:
            overrides[spec.action_id] = key
    return overrides


def find_conflict_action(
    effective: Mapping[str, str],
    target_action_id: str,
    new_key: str,
) -> ShortcutSpec | None:
    """대상 명령을 제외하고 같은 키를 쓰는 첫 명령을 대소문자 무시로 찾아 반환"""
    norm = normalize_shortcut(new_key)
    if not norm:
        return None
    lowered = norm.lower()
    for spec in SHORTCUT_SPECS:
        if spec.action_id == target_action_id:
            continue
        if normalize_shortcut(effective.get(spec.action_id, spec.default_key)).lower() == lowered:
            return spec
    return None


def format_tooltip_with_shortcut(
    base_text: str,
    shortcut: str,
    *,
    bracket: Literal["square", "round"] = "square",
) -> str:
    """툴팁 문구 뒤에 단축키를 괄호로 덧붙이며, 단축키가 없으면 문구만 반환"""
    if not shortcut:
        return base_text
    if bracket == "round":
        return f"{base_text} ({shortcut})"
    return f"{base_text} [{shortcut}]"
