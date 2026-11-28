"""JSONC(주석 포함 JSON) 파싱 및 메타데이터 기반 계층형 통합 설정 직렬화 코덱 모듈"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass, replace
from typing import Any

from src.core.config import (
    MAX_RECENT_DIRS,
    AnalysisConfig,
    ClassifierConfig,
    CuiConfig,
    DiagnosticsConfig,
    DisplayConfig,
    ExecutionConfig,
    PathsConfig,
)
from src.core.messages import ColumnId
from src.core.resolution_tiers import is_valid_manual_target
from src.core.settings.preview_info import (
    DEFAULT_DETAIL_INFO_KEYS,
    DEFAULT_INFO_ANCHOR_X,
    DEFAULT_INFO_ANCHOR_Y,
    DEFAULT_INFO_BOX_HEIGHT,
    DEFAULT_INFO_BOX_WIDTH,
    DEFAULT_INFO_LAYOUT,
    DEFAULT_INFO_MODE,
    DEFAULT_INFO_SCALE,
    DEFAULT_SUMMARY_INFO_KEYS,
    INFO_ANCHOR_MAX,
    INFO_ANCHOR_MIN,
    INFO_SCALE_MAX,
    INFO_SCALE_MIN,
    VALID_INFO_LAYOUTS,
    VALID_INFO_MODES,
    normalize_info_keys,
)
from src.core.settings.shortcuts import sanitize_shortcut_overrides
from src.core.settings.ui_state import (
    DEFAULT_ANALYZE_MODE,
    DEFAULT_COLUMN_PRESET,
    DEFAULT_COMPARE_TOP_BAR_COLLAPSED,
    DEFAULT_FILTER_SEARCH,
    DEFAULT_GROUP_BY,
    DEFAULT_PREVIEW_ALWAYS_ON_TOP,
    DEFAULT_PREVIEW_BACKGROUND,
    DEFAULT_PREVIEW_FRAME_OUTLINE,
    DEFAULT_PREVIEW_FULLSCREEN,
    DEFAULT_PREVIEW_MUTED,
    DEFAULT_PREVIEW_PLAYBACK_RATE,
    DEFAULT_PREVIEW_SEEK_SECONDS,
    DEFAULT_PREVIEW_VOLUME,
    DEFAULT_THEME_MODE,
    GROUP_BY_NONE,
    MAX_COLLAPSED_GROUP_LABELS,
    PREVIEW_VOLUME_MAX,
    TEXT_MODE_CONTAINS,
    VALID_ANALYZE_MODES,
    VALID_COLUMN_PRESETS,
    VALID_PREVIEW_BACKGROUNDS,
    VALID_PREVIEW_PLAYBACK_RATES,
    VALID_PREVIEW_SEEK_SECONDS,
    VALID_TEXT_MODES,
    VALID_THEME_MODES,
    ColumnFilterSnapshot,
    UiFilterState,
    UiLayoutState,
    UiPreviewInfoState,
    UiState,
    is_valid_group_by,
)

_COMMENT_ALIGN_COL = 54


@dataclass(slots=True)
class AppState:
    """data/config.json의 5대 섹션(paths, analysis, display, cui, ui)과 동일한 깊이의 통합 상태 컨테이너"""

    paths: PathsConfig = field(
        default_factory=PathsConfig,
        metadata={"banner": "// 1. 경로"},
    )
    analysis: AnalysisConfig = field(
        default_factory=AnalysisConfig,
        metadata={"banner": "// 2. 화질 및 키프레임 분석 기준"},
    )
    display: DisplayConfig = field(
        default_factory=DisplayConfig,
        metadata={"banner": "// 3. CUI, GUI 공통 표 표시 서식"},
    )
    cui: CuiConfig = field(
        default_factory=CuiConfig,
        metadata={"banner": "// 4. CUI(노트북, 터미널) 표 출력"},
    )
    execution: ExecutionConfig = field(
        default_factory=ExecutionConfig,
        metadata={"banner": "// 5. 인코딩 실행 (FFmpeg 경로, 결과 파일 처리, 동시성, 절전, 완료 후 동작)"},
    )
    diagnostics: DiagnosticsConfig = field(
        default_factory=DiagnosticsConfig,
        metadata={"banner": "// 6. 진단 및 개발자 모드 (상세 로그, 화면 멈춤 감시)"},
    )
    ui: UiState = field(
        default_factory=UiState,
        metadata={"banner": "// 7. GUI 화면, 필터, 예외 상태 (앱 사용 시 자동 저장)"},
    )

    def copy(self) -> AppState:
        """하위 설정, 리스트, UI 상태를 모두 복제한 독립 복사본 반환"""
        return AppState(
            paths=PathsConfig(recent_dirs=list(self.paths.recent_dirs)),
            analysis=self.analysis.copy(),
            display=replace(self.display),
            cui=replace(self.cui),
            execution=replace(self.execution),
            diagnostics=replace(self.diagnostics),
            ui=self.ui.copy(),
        )

    def to_classifier_config(self) -> ClassifierConfig:
        """현재 상태에서 분류기 런타임 설정(ClassifierConfig) 독립 사본을 추출"""
        an = self.analysis
        return ClassifierConfig(
            thresholds=replace(an.thresholds),
            savings=replace(an.savings),
            encode=replace(an.encode),
            codec_weights=replace(an.codec_weights),
            bitrate_model=replace(an.bitrate_model),
            display=replace(self.display),
            cui=replace(self.cui),
            execution=replace(self.execution),
            diagnostics=replace(self.diagnostics),
        )

    def apply_classifier_config(self, cfg: ClassifierConfig) -> None:
        """분류기 런타임 설정(ClassifierConfig)을 analysis, display, cui 섹션에 반영"""
        self.analysis = AnalysisConfig(
            thresholds=replace(cfg.thresholds),
            savings=replace(cfg.savings),
            encode=replace(cfg.encode),
            codec_weights=replace(cfg.codec_weights),
            bitrate_model=replace(cfg.bitrate_model),
        )
        self.display = replace(cfg.display)
        self.cui = replace(cfg.cui)
        self.execution = replace(cfg.execution)
        self.diagnostics = replace(cfg.diagnostics)

    def to_dict(self) -> dict[str, Any]:
        """계층화된 2~3단계 통합 설정 스키마 사전 표현으로 변환"""
        cfg_dict = self.to_classifier_config().to_dict()
        ui = self.ui
        flt = ui.filters
        lay = ui.layout
        info = ui.preview_info
        return {
            "paths": {
                "recent_dirs": list(self.paths.recent_dirs),
            },
            "analysis": cfg_dict["analysis"],
            "display": cfg_dict["display"],
            "cui": cfg_dict["cui"],
            "execution": cfg_dict["execution"],
            "diagnostics": cfg_dict["diagnostics"],
            "ui": {
                "theme_mode": ui.theme_mode,
                "analyze_mode": ui.analyze_mode,
                "preview_seek_seconds": ui.preview_seek_seconds,
                "preview_volume": ui.preview_volume,
                "preview_playback_rate": ui.preview_playback_rate,
                "preview_fullscreen": ui.preview_fullscreen,
                "preview_always_on_top": ui.preview_always_on_top,
                "preview_background": ui.preview_background,
                "preview_frame_outline": ui.preview_frame_outline,
                "compare_top_bar_collapsed": ui.compare_top_bar_collapsed,
                "preview_muted": ui.preview_muted,
                "recursive_scan": ui.recursive_scan,
                "show_setting_hints": ui.show_setting_hints,
                "compact_table": ui.compact_table,
                "exception_tokens": list(ui.exception_tokens),
                "disabled_exception_tokens": list(ui.disabled_exception_tokens),
                "shortcuts": dict(ui.shortcuts),
                "preview_info": {
                    "mode": info.mode,
                    "layout": info.layout,
                    "summary_items": list(info.summary_items),
                    "detail_items": list(info.detail_items),
                    "anchor_x": info.anchor_x,
                    "anchor_y": info.anchor_y,
                    "scale": info.scale,
                    "box_width": info.box_width,
                    "box_height": info.box_height,
                },
                "filters": {
                    "search": flt.search,
                    "columns": {key: asdict(snap) for key, snap in flt.columns.items()},
                    "sort_keys": [[key, asc] for key, asc in flt.sort_keys],
                    "group_by": flt.group_by,
                    "collapsed_groups": {
                        key: list(labels) for key, labels in flt.collapsed_groups.items()
                    },
                },
                "layout": {
                    "window_geometry": lay.window_geometry,
                    "table_header_state": lay.table_header_state,
                    "settings_tab": lay.settings_tab,
                    "help_tab": lay.help_tab,
                    "top_area_collapsed": lay.top_area_collapsed,
                    "table_top_filename": lay.table_top_filename,
                    "table_top_row": lay.table_top_row,
                    "table_left_scroll": lay.table_left_scroll,
                    "table_current_filename": lay.table_current_filename,
                    "column_preset": lay.column_preset,
                    "column_preset_layouts": dict(lay.column_preset_layouts),
                },
            },
        }


def _scan_jsonc(text: str, *, mode: str) -> str:
    out: list[str] = []
    in_string = False
    escaped = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            i += 1
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            i += 1
            continue

        if mode == "comments" and ch == "/" and i + 1 < n:
            nxt = text[i + 1]
            if nxt == "/":
                i += 2
                while i < n and text[i] != "\n":
                    i += 1
                continue
            if nxt == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    if text[i] == "\n":
                        out.append("\n")
                    i += 1
                i += 2
                continue

        if mode == "commas" and ch == ",":
            j = i + 1
            while j < n and text[j] in (" ", "\t", "\r", "\n"):
                j += 1
            if j < n and text[j] in ("}", "]"):
                i += 1
                continue

        out.append(ch)
        i += 1
    return "".join(out)


def strip_jsonc_comments(text: str) -> str:
    """문자열 리터럴 내부를 보존하며 JSONC의 주석과 후행 쉼표를 제거"""
    return _scan_jsonc(_scan_jsonc(text, mode="comments"), mode="commas")


_COLUMN_ID_VALUES: frozenset[str] = frozenset(member.value for member in ColumnId)


def _sanitize_choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text if text in allowed else default


def _sanitize_int_choice(value: Any, allowed: tuple[int, ...], default: int) -> int:
    return value if type(value) is int and value in allowed else default


def _sanitize_float_choice(value: Any, allowed: tuple[float, ...], default: float) -> float:
    number = _sanitize_number(value)
    return number if number is not None and number in allowed else default


def _sanitize_int_range(value: Any, minimum: int, maximum: int, default: int) -> int:
    return value if type(value) is int and minimum <= value <= maximum else default


def _sanitize_non_negative_int(raw: Any) -> int:
    return raw if type(raw) is int and raw >= 0 else 0


def _sanitize_str_field(raw: Any) -> str:
    # - 옛 저장 파일에 숫자, 목록이 들어 있어도 문자열 상태를 깨지 않도록 문자열만 받음
    return raw if isinstance(raw, str) else ""


def _sanitize_number(raw: Any) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    return float(raw)


def _sanitize_info_keys(raw: Any, default: tuple[str, ...]) -> list[str]:
    """정보 표시 항목 키 목록에서 알 수 없는 키와 중복을 버리고 표시 순서로 정렬, 목록이 아니면 기본값"""
    if not isinstance(raw, list):
        return list(default)
    return normalize_info_keys([key for key in raw if isinstance(key, str)])


def _sanitize_ratio(raw: Any, default: float) -> float:
    """정보 표시 위치와 상자 크기 비율이 0~1 범위의 유한한 수일 때만 사용"""
    number = _sanitize_number(raw)
    if number is None or not INFO_ANCHOR_MIN <= number <= INFO_ANCHOR_MAX:
        return default
    return number


def _sanitize_preview_info(raw: Any) -> UiPreviewInfoState:
    """정보 표시 설정 사전을 검증해 복원, 잘못된 값은 기본값으로 대체"""
    info_raw: dict[str, Any] = raw if isinstance(raw, dict) else {}
    return UiPreviewInfoState(
        mode=_sanitize_choice(info_raw.get("mode"), VALID_INFO_MODES, DEFAULT_INFO_MODE),
        layout=_sanitize_choice(info_raw.get("layout"), VALID_INFO_LAYOUTS, DEFAULT_INFO_LAYOUT),
        summary_items=_sanitize_info_keys(info_raw.get("summary_items"), DEFAULT_SUMMARY_INFO_KEYS),
        detail_items=_sanitize_info_keys(info_raw.get("detail_items"), DEFAULT_DETAIL_INFO_KEYS),
        anchor_x=_sanitize_ratio(info_raw.get("anchor_x"), DEFAULT_INFO_ANCHOR_X),
        anchor_y=_sanitize_ratio(info_raw.get("anchor_y"), DEFAULT_INFO_ANCHOR_Y),
        box_width=_sanitize_ratio(info_raw.get("box_width"), DEFAULT_INFO_BOX_WIDTH),
        box_height=_sanitize_ratio(info_raw.get("box_height"), DEFAULT_INFO_BOX_HEIGHT),
        scale=_sanitize_int_range(
            info_raw.get("scale"), INFO_SCALE_MIN, INFO_SCALE_MAX, DEFAULT_INFO_SCALE
        ),
    )


def _sanitize_column_snapshot(raw: dict[str, Any]) -> ColumnFilterSnapshot:
    selected_raw = raw.get("selected_values")
    selected = (
        tuple(str(item) for item in selected_raw) if isinstance(selected_raw, list) else None
    )
    text_query = raw.get("text_query")
    return ColumnFilterSnapshot(
        selected_values=selected,
        min_value=_sanitize_number(raw.get("min_value")),
        max_value=_sanitize_number(raw.get("max_value")),
        include_null=_sanitize_bool(raw.get("include_null"), True),
        null_only=_sanitize_bool(raw.get("null_only"), False),
        include_null_only=_sanitize_bool(raw.get("include_null_only"), False),
        text_query=text_query if isinstance(text_query, str) else "",
        text_mode=_sanitize_choice(raw.get("text_mode"), VALID_TEXT_MODES, TEXT_MODE_CONTAINS),
    )


def _sanitize_column_filters(raw: Any) -> dict[str, ColumnFilterSnapshot]:
    """열 ID 키와 조건 객체가 올바른 항목만 남기고 나머지는 버림"""
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): _sanitize_column_snapshot(value)
        for key, value in raw.items()
        if str(key) in _COLUMN_ID_VALUES and isinstance(value, dict)
    }


def _sanitize_sort_keys(raw: Any) -> list[tuple[str, bool]]:
    """(열 ID, 오름차순 여부) 쌍 중 알려진 열 ID와 bool 값만 순서대로 남김"""
    if not isinstance(raw, list):
        return []
    keys: list[tuple[str, bool]] = []
    for item in raw:
        if (
            isinstance(item, list)
            and len(item) == 2
            and isinstance(item[0], str)
            and item[0] in _COLUMN_ID_VALUES
            and isinstance(item[1], bool)
        ):
            keys.append((item[0], item[1]))
    return keys


def _sanitize_group_by(raw: Any) -> str:
    """저장된 행 묶기 키가 없음, 폴더, 열 키 중 하나일 때만 사용하고 나머지는 없음으로 되돌림"""
    if not isinstance(raw, str):
        return DEFAULT_GROUP_BY
    text = raw.strip()
    return text if is_valid_group_by(text) else DEFAULT_GROUP_BY


def _sanitize_collapsed_groups(raw: Any) -> dict[str, list[str]]:
    """행 묶기 키별 접힌 그룹 이름 맵에서 알 수 없는 키와 빈 이름을 버리고 상한까지만 남김"""
    if not isinstance(raw, dict):
        return {}
    raw_map: dict[Any, Any] = raw
    store: dict[str, list[str]] = {}
    remaining = MAX_COLLAPSED_GROUP_LABELS
    for key, labels in raw_map.items():
        if remaining <= 0:
            break
        if not isinstance(key, str) or not is_valid_group_by(key) or key == GROUP_BY_NONE:
            continue
        if not isinstance(labels, list):
            continue
        cleaned = list(
            dict.fromkeys(
                label.strip() for label in labels if isinstance(label, str) and label.strip()
            )
        )[:remaining]
        if not cleaned:
            continue
        store[key] = cleaned
        remaining -= len(cleaned)
    return store


def _sanitize_str_list(raw: Any, *, max_items: int | None = None) -> list[str]:
    if not isinstance(raw, list):
        return []
    result: list[str] = []
    for item in raw:
        text = str(item).strip()
        if text:
            result.append(text)
        if max_items is not None and len(result) >= max_items:
            break
    return result


def _sanitize_tab_index(raw: Any, *, max_index: int = 1) -> int:
    if isinstance(raw, bool):
        return 0
    if isinstance(raw, int):
        return raw if 0 <= raw <= max_index else 0
    if isinstance(raw, str):
        try:
            parsed = int(raw.strip())
            return parsed if 0 <= parsed <= max_index else 0
        except ValueError:
            return 0
    return 0


def _sanitize_bool(raw: Any, default: bool) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes", "on"):
            return True
        if lowered in ("false", "0", "no", "off"):
            return False
    return default


def _get_dict(parent: dict[str, Any], key: str) -> dict[str, Any]:
    val = parent.get(key)
    return val if isinstance(val, dict) else {}


def _sanitize_manual_target_tiers(raw: Any) -> dict[str, dict[str, str]]:
    """작업 폴더별 직접 지정 맵에서 문자열이며 규격 키 또는 축소 안 함인 항목만 남기고 나머지는 자동 판정으로 되돌림"""
    if not isinstance(raw, dict):
        return {}
    raw_map: dict[str, Any] = raw
    store: dict[str, dict[str, str]] = {}
    for root, entries in raw_map.items():
        if not isinstance(entries, dict):
            continue
        entry_map: dict[str, Any] = entries
        valid = {
            name: tier
            for name, tier in entry_map.items()
            if isinstance(tier, str) and is_valid_manual_target(tier)
        }
        if valid:
            store[root] = valid
    return store


def _sanitize_preset_layouts(raw: Any) -> dict[str, str]:
    """프리셋 키와 열 상태 JSON 문자열 쌍만 남기고 알 수 없는 키는 버림"""
    if not isinstance(raw, dict):
        return {}
    raw_map: dict[Any, Any] = raw
    return {
        key: text
        for key, text in raw_map.items()
        if isinstance(key, str) and key in VALID_COLUMN_PRESETS and isinstance(text, str) and text
    }


def _sanitize_column_preset(raw: Any) -> str:
    """저장된 열 프리셋 키가 목록에 있을 때만 사용하고 나머지는 기본 프리셋으로 되돌림"""
    return _sanitize_choice(raw, VALID_COLUMN_PRESETS, DEFAULT_COLUMN_PRESET)


def parse_state_mapping(raw: dict[str, Any]) -> AppState:
    """계층화된 통합 설정 사전을 AppState 객체로 복원"""
    cfg = ClassifierConfig.from_dict(raw)
    paths_raw = _get_dict(raw, "paths")
    ui_raw = _get_dict(raw, "ui")
    filters_raw = _get_dict(ui_raw, "filters")
    layout_raw = _get_dict(ui_raw, "layout")
    search_raw = filters_raw.get("search")

    state = AppState(
        paths=PathsConfig(
            recent_dirs=_sanitize_str_list(
                paths_raw.get("recent_dirs"), max_items=MAX_RECENT_DIRS
            ),
        ),
        ui=UiState(
            theme_mode=_sanitize_choice(
                ui_raw.get("theme_mode"), VALID_THEME_MODES, DEFAULT_THEME_MODE
            ),
            analyze_mode=_sanitize_choice(
                ui_raw.get("analyze_mode", ui_raw.get("scan_mode")),
                VALID_ANALYZE_MODES,
                DEFAULT_ANALYZE_MODE,
            ),
            preview_seek_seconds=_sanitize_int_choice(
                ui_raw.get("preview_seek_seconds"),
                VALID_PREVIEW_SEEK_SECONDS,
                DEFAULT_PREVIEW_SEEK_SECONDS,
            ),
            preview_volume=_sanitize_int_range(
                ui_raw.get("preview_volume"), 0, PREVIEW_VOLUME_MAX, DEFAULT_PREVIEW_VOLUME
            ),
            preview_muted=_sanitize_bool(ui_raw.get("preview_muted"), DEFAULT_PREVIEW_MUTED),
            preview_fullscreen=_sanitize_bool(ui_raw.get("preview_fullscreen"), DEFAULT_PREVIEW_FULLSCREEN),
            preview_always_on_top=_sanitize_bool(ui_raw.get("preview_always_on_top"), DEFAULT_PREVIEW_ALWAYS_ON_TOP),
            preview_background=_sanitize_choice(
                ui_raw.get("preview_background"),
                VALID_PREVIEW_BACKGROUNDS,
                DEFAULT_PREVIEW_BACKGROUND,
            ),
            preview_frame_outline=_sanitize_bool(
                ui_raw.get("preview_frame_outline"), DEFAULT_PREVIEW_FRAME_OUTLINE
            ),
            compare_top_bar_collapsed=_sanitize_bool(
                ui_raw.get("compare_top_bar_collapsed"), DEFAULT_COMPARE_TOP_BAR_COLLAPSED
            ),
            preview_playback_rate=_sanitize_float_choice(
                ui_raw.get("preview_playback_rate"),
                VALID_PREVIEW_PLAYBACK_RATES,
                DEFAULT_PREVIEW_PLAYBACK_RATE,
            ),
            recursive_scan=_sanitize_bool(ui_raw.get("recursive_scan"), False),
            show_setting_hints=_sanitize_bool(
                ui_raw.get("show_setting_hints"), True
            ),
            compact_table=_sanitize_bool(ui_raw.get("compact_table"), False),
            exception_tokens=_sanitize_str_list(ui_raw.get("exception_tokens")),
            disabled_exception_tokens=_sanitize_str_list(
                ui_raw.get("disabled_exception_tokens")
            ),
            manual_target_tiers=_sanitize_manual_target_tiers(
                ui_raw.get("manual_target_tiers")
            ),
            shortcuts=sanitize_shortcut_overrides(ui_raw.get("shortcuts")),
            preview_info=_sanitize_preview_info(ui_raw.get("preview_info")),
            filters=UiFilterState(
                search=str(search_raw if search_raw is not None else DEFAULT_FILTER_SEARCH),
                columns=_sanitize_column_filters(filters_raw.get("columns")),
                sort_keys=_sanitize_sort_keys(filters_raw.get("sort_keys")),
                group_by=_sanitize_group_by(filters_raw.get("group_by")),
                collapsed_groups=_sanitize_collapsed_groups(
                    filters_raw.get("collapsed_groups")
                ),
            ),
            layout=UiLayoutState(
                window_geometry=str(layout_raw.get("window_geometry", "") or ""),
                table_header_state=str(layout_raw.get("table_header_state", "") or ""),
                settings_tab=_sanitize_tab_index(
                    layout_raw.get("settings_tab"), max_index=4
                ),
                help_tab=_sanitize_tab_index(
                    layout_raw.get("help_tab"), max_index=3
                ),
                top_area_collapsed=_sanitize_bool(
                    layout_raw.get("top_area_collapsed"), False
                ),
                table_top_filename=_sanitize_str_field(layout_raw.get("table_top_filename")),
                table_top_row=_sanitize_non_negative_int(layout_raw.get("table_top_row")),
                table_left_scroll=_sanitize_non_negative_int(
                    layout_raw.get("table_left_scroll")
                ),
                table_current_filename=_sanitize_str_field(
                    layout_raw.get("table_current_filename")
                ),
                column_preset=_sanitize_column_preset(layout_raw.get("column_preset")),
                column_preset_layouts=_sanitize_preset_layouts(
                    layout_raw.get("column_preset_layouts")
                ),
            ),
        ),
    )
    state.apply_classifier_config(cfg)
    return state


def _format_kv_line(
    indent: int,
    key: str,
    value: Any,
    comment: str,
    *,
    trailing_comma: bool = True,
) -> str:
    prefix = " " * indent
    clean_val = round(value, 4) if isinstance(value, float) else value
    encoded_key = json.dumps(key, ensure_ascii=False)
    encoded_val = json.dumps(clean_val, ensure_ascii=False)
    comma = "," if trailing_comma else ""
    body = f"{prefix}{encoded_key}: {encoded_val}{comma}"
    if not comment:
        return body
    pad = max(2, _COMMENT_ALIGN_COL - len(body))
    return f"{body}{' ' * pad}// {comment}"


def _render_dataclass_fields(lines: list[str], indent: int, obj: Any) -> None:
    # - storage가 db인 필드는 상태 파일이 아닌 전용 저장소에 기록되므로 제외
    obj_fields = [f for f in fields(obj) if f.metadata.get("storage") != "db"]
    last_idx = len(obj_fields) - 1
    prefix = " " * indent
    for idx, f in enumerate(obj_fields):
        trailing = idx < last_idx
        banner = f.metadata.get("banner")
        if banner:
            if idx > 0:
                lines.append("")
            lines.append(f"{prefix}{banner}")

        val = getattr(obj, f.name)
        if isinstance(val, dict) and any(is_dataclass(item) for item in val.values()):
            val = {
                str(key): asdict(item) if is_dataclass(item) and not isinstance(item, type) else item
                for key, item in val.items()
            }
        if is_dataclass(val):
            lines.append(f'{prefix}{json.dumps(f.name, ensure_ascii=False)}: {{')
            _render_dataclass_fields(lines, indent + 2, val)
            lines.append(f"{prefix}}}," if trailing else f"{prefix}}}")
        else:
            comment = str(f.metadata.get("comment", ""))
            lines.append(
                _format_kv_line(
                    indent,
                    f.name,
                    val,
                    comment,
                    trailing_comma=trailing,
                )
            )


def render_state_jsonc(state: AppState) -> str:
    """AppState와 필드 메타데이터 주석을 순회해 2~3단계 계층형 JSONC 문자열을 자동 직렬화"""
    lines: list[str] = [
        "// Video Classifier Studio 통합 설정, 상태 파일 (JSONC 형식, // 주석 지원)",
        "// - 기본 작업 폴더 경로는 data/rootdir.txt 파일에서 단일 관리됩니다.",
        "{",
    ]
    _render_dataclass_fields(lines, 2, state)
    lines.extend(["}", ""])
    return "\n".join(lines)
