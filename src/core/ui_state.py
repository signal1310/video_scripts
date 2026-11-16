"""GUI 표현 상태(테마, 스캔 모드, 검색 필터, 창 레이아웃) 스키마 정의 모듈"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from src.core.config import setting_meta
from src.core.resolution_tiers import copy_manual_targets

DEFAULT_ANALYZE_MODE: str = "fast"
VALID_ANALYZE_MODES: tuple[str, ...] = ("fast", "full")

DEFAULT_FILTER_SEARCH: str = ""

DEFAULT_THEME_MODE: str = "system"
VALID_THEME_MODES: tuple[str, ...] = ("system", "dark", "light")

PREVIEW_BACKEND_BUILTIN: str = "builtin"
PREVIEW_BACKEND_QUICKLOOK: str = "quicklook"
DEFAULT_PREVIEW_BACKEND: str = PREVIEW_BACKEND_BUILTIN
VALID_PREVIEW_BACKENDS: tuple[str, ...] = (PREVIEW_BACKEND_BUILTIN, PREVIEW_BACKEND_QUICKLOOK)
DEFAULT_PREVIEW_SEEK_SECONDS: int = 5
VALID_PREVIEW_SEEK_SECONDS: tuple[int, ...] = (1, 3, 5, 10, 30)
PREVIEW_VOLUME_MAX: int = 100
DEFAULT_PREVIEW_VOLUME: int = PREVIEW_VOLUME_MAX
DEFAULT_PREVIEW_MUTED: bool = False

TEXT_MODE_CONTAINS: str = "contains"
TEXT_MODE_NOT_CONTAINS: str = "not_contains"
TEXT_MODE_STARTS_WITH: str = "starts_with"
TEXT_MODE_ENDS_WITH: str = "ends_with"
VALID_TEXT_MODES: tuple[str, ...] = (
    TEXT_MODE_CONTAINS,
    TEXT_MODE_NOT_CONTAINS,
    TEXT_MODE_STARTS_WITH,
    TEXT_MODE_ENDS_WITH,
)


@dataclass(frozen=True, slots=True)
class ColumnFilterSnapshot:
    """열별 필터 조건의 저장 형태, GUI의 ColumnFilterRule과 값으로 대응"""

    selected_values: tuple[str, ...] | None = None
    min_value: float | None = None
    max_value: float | None = None
    include_null: bool = True
    null_only: bool = False
    text_query: str = ""
    text_mode: str = TEXT_MODE_CONTAINS


@dataclass(slots=True)
class UiFilterState:
    """GUI 테이블 검색과 열별 필터 상태"""

    search: str = field(
        default=DEFAULT_FILTER_SEARCH,
        metadata=setting_meta("파일명, 코덱 검색어"),
    )
    columns: dict[str, ColumnFilterSnapshot] = field(
        default_factory=dict,
        metadata=setting_meta("열별 필터 조건, 열 ID -> 조건"),
    )
    sort_keys: list[tuple[str, bool]] = field(
        default_factory=list,
        metadata=setting_meta("정렬 우선순위, (열 ID, 오름차순 여부) 목록, 앞쪽이 주 정렬"),
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
        metadata=setting_meta("마지막으로 연 설정 탭 인덱스 (0=환경 설정, 1=판별 기준, 2=단축키)"),
    )
    help_tab: int = field(
        default=0,
        metadata=setting_meta("마지막으로 연 도움말 탭 인덱스 (0=프로그램 안내, 1=판정 원리, 2=고급 활용)"),
    )
    top_area_collapsed: bool = field(
        default=False,
        metadata=setting_meta("상단 영역(작업 폴더, 분석 요약, 필터) 접힘 여부"),
    )
    table_top_filename: str = field(
        default="",
        metadata=setting_meta("종료 시 표 맨 위에 보이던 영상 파일명, 시작 시 같은 영상으로 스크롤 복원"),
    )
    table_top_row: int = field(
        default=0,
        metadata=setting_meta("종료 시 표 맨 위 행 번호, 저장한 파일이 표에 없을 때 가까운 위치로 사용"),
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
    preview_backend: str = field(
        default=DEFAULT_PREVIEW_BACKEND,
        metadata=setting_meta('미리 보기 방식: "builtin" | "quicklook"'),
    )
    preview_seek_seconds: int = field(
        default=DEFAULT_PREVIEW_SEEK_SECONDS,
        metadata=setting_meta("내장 미리 보기에서 좌우 방향키 구간 이동 간격(초)"),
    )
    preview_volume: int = field(
        default=DEFAULT_PREVIEW_VOLUME,
        metadata=setting_meta("내장 미리 보기 볼륨(0~100)"),
    )
    preview_muted: bool = field(
        default=DEFAULT_PREVIEW_MUTED,
        metadata=setting_meta("내장 미리 보기 음소거 여부"),
    )
    recursive_scan: bool = field(
        default=False,
        metadata=setting_meta("작업 폴더의 하위 폴더 포함 분석 여부"),
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
        # - 전용 DB 테이블이 원본이라 상태 파일에는 기록하지 않음
        metadata=setting_meta("작업 폴더별 출력 해상도 직접 지정, 폴더 -> 파일명 -> 규격 키 또는 keep")
        | {"storage": "db"},
    )
    shortcuts: dict[str, str] = field(
        default_factory=dict,
        metadata=setting_meta("사용자 지정 단축키 (명령 ID -> 단축키 문자열, 빈 문자열은 해제)"),
    )
    filters: UiFilterState = field(default_factory=UiFilterState)
    layout: UiLayoutState = field(default_factory=UiLayoutState)

    def copy(self) -> UiState:
        """중첩 컬렉션과 하위 상태를 포함한 독립 복사본 반환"""
        return UiState(
            theme_mode=self.theme_mode,
            analyze_mode=self.analyze_mode,
            preview_backend=self.preview_backend,
            preview_seek_seconds=self.preview_seek_seconds,
            preview_volume=self.preview_volume,
            preview_muted=self.preview_muted,
            recursive_scan=self.recursive_scan,
            show_setting_hints=self.show_setting_hints,
            exception_tokens=list(self.exception_tokens),
            disabled_exception_tokens=list(self.disabled_exception_tokens),
            manual_target_tiers=copy_manual_targets(self.manual_target_tiers),
            shortcuts=dict(self.shortcuts),
            filters=replace(
                self.filters,
                columns=dict(self.filters.columns),
                sort_keys=list(self.filters.sort_keys),
            ),
            layout=replace(self.layout),
        )

