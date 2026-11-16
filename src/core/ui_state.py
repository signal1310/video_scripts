"""GUI 표현 상태(테마, 스캔 모드, 검색 필터, 창 레이아웃) 스키마 정의 모듈"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.core.config import setting_meta

DEFAULT_ANALYZE_MODE: str = "fast"
VALID_ANALYZE_MODES: tuple[str, ...] = ("fast", "full")

DEFAULT_FILTER_SEARCH: str = ""

DEFAULT_THEME_MODE: str = "system"
VALID_THEME_MODES: tuple[str, ...] = ("system", "dark", "light")


@dataclass(slots=True)
class UiFilterState:
    """GUI 테이블 검색 필터 상태"""

    search: str = field(
        default=DEFAULT_FILTER_SEARCH,
        metadata=setting_meta("파일명, 코덱 검색어"),
    )


@dataclass(slots=True)
class UiLayoutState:
    """GUI 창 기하 배치(Base64) 및 테이블 열 상태(JSON)"""

    window_geometry: str = field(
        default="",
        metadata=setting_meta("창 크기, 위치 상태 (Base64)"),
    )
    table_header_state: str = field(
        default="",
        metadata=setting_meta("테이블 열 배치 상태 (JSON, 열 키별)"),
    )
    settings_tab: int = field(
        default=0,
        metadata=setting_meta("마지막으로 연 설정 탭 인덱스 (0=환경 설정, 1=판별 기준)"),
    )
    help_tab: int = field(
        default=0,
        metadata=setting_meta("마지막으로 연 도움말 탭 인덱스 (0=프로그램 안내, 1=판정 원리, 2=고급 활용)"),
    )
    top_area_collapsed: bool = field(
        default=False,
        metadata=setting_meta("상단 영역(작업 폴더, 분석 요약, 필터) 접힘 여부"),
    )


@dataclass(slots=True)
class UiState:
    """GUI 화면 테마, 스캔 모드, 예외 규칙, 필터, 레이아웃 통합 상태"""

    theme_mode: str = field(
        default=DEFAULT_THEME_MODE,
        metadata=setting_meta('테마: "system" | "dark" | "light"'),
    )
    analyze_mode: str = field(
        default=DEFAULT_ANALYZE_MODE,
        metadata=setting_meta('분석 모드: "fast" | "full"'),
    )
    show_setting_hints: bool = field(
        default=True,
        metadata=setting_meta("설정 창 내 쉬운 설명, 예시 표시 여부"),
    )
    exception_tokens: list[str] = field(
        default_factory=list,
        metadata=setting_meta("인코딩 제외 키워드, AST 조건식 목록"),
    )
    disabled_exception_tokens: list[str] = field(
        default_factory=list,
        metadata=setting_meta("비활성화된 예외 규칙(키워드, AST 조건식) 목록"),
    )
    manual_target_tiers: dict[str, dict[str, str]] = field(
        default_factory=dict,
        metadata=setting_meta("작업 폴더별 출력 해상도 직접 지정, 폴더 -> 파일명 -> 규격 키 또는 keep"),
    )
    filters: UiFilterState = field(default_factory=UiFilterState)
    layout: UiLayoutState = field(default_factory=UiLayoutState)

    def copy(self) -> UiState:
        """중첩 컬렉션과 하위 상태를 포함한 독립 복사본 반환"""
        return UiState(
            theme_mode=self.theme_mode,
            analyze_mode=self.analyze_mode,
            show_setting_hints=self.show_setting_hints,
            exception_tokens=list(self.exception_tokens),
            disabled_exception_tokens=list(self.disabled_exception_tokens),
            manual_target_tiers={
                root: dict(entries) for root, entries in self.manual_target_tiers.items()
            },
            filters=replace(self.filters),
            layout=replace(self.layout),
        )

