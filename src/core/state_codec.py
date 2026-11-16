"""JSONC(주석 포함 JSON) 파싱 및 메타데이터 기반 계층형 통합 설정 직렬화 코덱 모듈"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, fields, is_dataclass, replace
from typing import Any

from src.core.config import (
    DEFAULT_DIR_LOW_QUALITY_KEYFRAME,
    DEFAULT_DIR_STANDARD_CQ22,
    MAX_RECENT_DIRS,
    AnalysisConfig,
    ClassifierConfig,
    CuiConfig,
    DisplayConfig,
    PathsConfig,
)
from src.core.resolution_tiers import is_valid_manual_target
from src.core.ui_state import (
    DEFAULT_ANALYZE_MODE,
    DEFAULT_FILTER_SEARCH,
    DEFAULT_THEME_MODE,
    VALID_ANALYZE_MODES,
    VALID_THEME_MODES,
    UiFilterState,
    UiLayoutState,
    UiState,
)

_COMMENT_ALIGN_COL = 54


@dataclass(slots=True)
class AppState:
    """data/config.json의 5대 섹션(paths, analysis, display, cui, ui)과 동일한 깊이의 통합 상태 컨테이너"""

    paths: PathsConfig = field(
        default_factory=PathsConfig,
        metadata={"banner": "// 1. 경로 및 분류 폴더"},
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
    ui: UiState = field(
        default_factory=UiState,
        metadata={"banner": "// 5. GUI 화면, 필터, 예외 상태 (앱 사용 시 자동 저장)"},
    )

    def copy(self) -> AppState:
        """하위 설정, 리스트, UI 상태를 모두 복제한 독립 복사본 반환"""
        return AppState(
            paths=PathsConfig(
                recent_dirs=list(self.paths.recent_dirs),
                dir_standard_cq22=self.paths.dir_standard_cq22,
                dir_low_quality_keyframe=self.paths.dir_low_quality_keyframe,
                dir_downscale_prefix=self.paths.dir_downscale_prefix,
            ),
            analysis=self.analysis.copy(),
            display=replace(self.display),
            cui=replace(self.cui),
            ui=self.ui.copy(),
        )

    def to_classifier_config(self) -> ClassifierConfig:
        """현재 상태에서 분류기 런타임 설정(ClassifierConfig) 독립 사본을 추출"""
        an = self.analysis
        return ClassifierConfig(
            dir_standard_cq22=self.paths.dir_standard_cq22,
            dir_low_quality_keyframe=self.paths.dir_low_quality_keyframe,
            dir_downscale_prefix=self.paths.dir_downscale_prefix,
            thresholds=replace(an.thresholds),
            savings=replace(an.savings),
            encode=replace(an.encode),
            codec_weights=replace(an.codec_weights),
            bitrate_model=replace(an.bitrate_model),
            display=replace(self.display),
            cui=replace(self.cui),
        )

    def apply_classifier_config(self, cfg: ClassifierConfig) -> None:
        """분류기 런타임 설정(ClassifierConfig)을 paths, analysis, display, cui 섹션에 반영"""
        self.paths.dir_standard_cq22 = cfg.dir_standard_cq22
        self.paths.dir_low_quality_keyframe = cfg.dir_low_quality_keyframe
        self.paths.dir_downscale_prefix = cfg.dir_downscale_prefix
        self.analysis = AnalysisConfig(
            thresholds=replace(cfg.thresholds),
            savings=replace(cfg.savings),
            encode=replace(cfg.encode),
            codec_weights=replace(cfg.codec_weights),
            bitrate_model=replace(cfg.bitrate_model),
        )
        self.display = replace(cfg.display)
        self.cui = replace(cfg.cui)

    def to_dict(self) -> dict[str, Any]:
        """계층화된 2~3단계 통합 설정 스키마 사전 표현으로 변환"""
        cfg_dict = self.to_classifier_config().to_dict()
        ui = self.ui
        flt = ui.filters
        lay = ui.layout
        return {
            "paths": {
                "recent_dirs": list(self.paths.recent_dirs),
                **cfg_dict["paths"],
            },
            "analysis": cfg_dict["analysis"],
            "display": cfg_dict["display"],
            "cui": cfg_dict["cui"],
            "ui": {
                "theme_mode": ui.theme_mode,
                "analyze_mode": ui.analyze_mode,
                "show_setting_hints": ui.show_setting_hints,
                "exception_tokens": list(ui.exception_tokens),
                "disabled_exception_tokens": list(ui.disabled_exception_tokens),
                "manual_target_tiers": {
                    root: dict(entries) for root, entries in ui.manual_target_tiers.items()
                },
                "filters": {
                    "search": flt.search,
                },
                "layout": {
                    "window_geometry": lay.window_geometry,
                    "table_header_state": lay.table_header_state,
                    "settings_tab": lay.settings_tab,
                    "help_tab": lay.help_tab,
                    "top_area_collapsed": lay.top_area_collapsed,
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


def _sanitize_choice(value: Any, allowed: tuple[str, ...], default: str) -> str:
    text = str(value).strip() if value is not None else ""
    return text if text in allowed else default


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
            dir_standard_cq22=cfg.dir_standard_cq22 or DEFAULT_DIR_STANDARD_CQ22,
            dir_low_quality_keyframe=(
                cfg.dir_low_quality_keyframe or DEFAULT_DIR_LOW_QUALITY_KEYFRAME
            ),
            dir_downscale_prefix=cfg.dir_downscale_prefix,
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
            show_setting_hints=_sanitize_bool(
                ui_raw.get("show_setting_hints"), True
            ),
            exception_tokens=_sanitize_str_list(ui_raw.get("exception_tokens")),
            disabled_exception_tokens=_sanitize_str_list(
                ui_raw.get("disabled_exception_tokens")
            ),
            manual_target_tiers=_sanitize_manual_target_tiers(
                ui_raw.get("manual_target_tiers")
            ),
            filters=UiFilterState(
                search=str(search_raw if search_raw is not None else DEFAULT_FILTER_SEARCH),
            ),
            layout=UiLayoutState(
                window_geometry=str(layout_raw.get("window_geometry", "") or ""),
                table_header_state=str(layout_raw.get("table_header_state", "") or ""),
                settings_tab=_sanitize_tab_index(
                    layout_raw.get("settings_tab"), max_index=1
                ),
                help_tab=_sanitize_tab_index(
                    layout_raw.get("help_tab"), max_index=3
                ),
                top_area_collapsed=bool(layout_raw.get("top_area_collapsed", False)),
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
    obj_fields = fields(obj)
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
