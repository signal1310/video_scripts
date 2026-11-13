"""JSONC(주석 포함 JSON) 파싱 및 계층화 통합 설정 스키마 직렬화 코덱 모듈"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from src.core.config import (
    DEFAULT_CONFIGS,
    DEFAULT_FILTER_SEARCH,
    DEFAULT_FILTER_TARGET,
    DEFAULT_FILTER_ZONE,
    DEFAULT_ROOT_DIR,
    DEFAULT_SCAN_MODE,
    DEFAULT_THEME_MODE,
    VALID_FILTER_TARGETS,
    VALID_FILTER_ZONES,
    VALID_SCAN_MODES,
    VALID_THEME_MODES,
    ClassifierConfig,
)

MAX_RECENT_DIRS = 10
_COMMENT_ALIGN_COL = 54

# (섹션 경로, JSON 키 이름, DEFAULT_CONFIGS 키, 값 타입, 주석 설명)
_CONFIG_FIELD_SPECS: tuple[tuple[str, str, str, type[Any], str], ...] = (
    ("paths", "dir_standard_cq22", "DIR_STANDARD_CQ22", str, "과화질·키프레임 교정 표준 인코딩 폴더명"),
    ("paths", "dir_low_quality_keyframe", "DIR_LOW_QUALITY_KEYFRAME", str, "저화질 키프레임 불량 교정 이동 폴더명"),
    ("analysis.thresholds", "over_bitrate_ratio", "THRESHOLD_OPTIMAL_BITRATE_RATE", float, "최적 비트레이트 대비 과화질 판별 배수"),
    ("analysis.thresholds", "low_bitrate_ratio", "THRESHOLD_LOW_BITRATE_RATE", float, "최적 비트레이트 대비 저화질 판별 배수"),
    ("analysis.thresholds", "keyframe_interval_sec", "THRESHOLD_KEYFRAME_INTERVAL", float, "허용 최대 키프레임 간격 (초)"),
    ("analysis.small_footage", "size_mb", "SMALL_FOOTAGE_SIZE_MB", float, "소형 영상 기준 용량 (MB)"),
    ("analysis.small_footage", "min_saved_mb", "SMALL_FOOTAGE_MIN_SAVED_MB", float, "개별 소형 영상 최소 절감량 하한 (MB)"),
    ("analysis.small_footage", "cumulative_ratio", "SMALL_FOOTAGE_CUMULATIVE_RATIO", float, "전체 절감량 중 소형 영상 누적 비중 임계값"),
    ("analysis.small_footage", "cumulative_mb", "SMALL_FOOTAGE_CUMULATIVE_MB", float, "소형 영상 누적 절감 합계 임계값 (MB)"),
    ("analysis.small_footage", "keyframe_1s_overhead_rate", "KEYFRAME_1S_OVERHEAD_RATE", float, "1초 키프레임 교정 시 예상 용량 증가율"),
    ("analysis.codec_weights", "av1", "CODEC_WEIGHT_AV1", float, "AV1 상대 비트레이트 비율 (H.264=1.0 기준)"),
    ("analysis.codec_weights", "hevc_vp9", "CODEC_WEIGHT_HEVC_VP9", float, "HEVC / VP9 상대 비트레이트 비율"),
    ("analysis.codec_weights", "h264", "CODEC_WEIGHT_H264", float, "H.264 표준 기준 가중치"),
    ("analysis.codec_weights", "legacy", "CODEC_WEIGHT_LEGACY", float, "구형 레거시 코덱 상대 비트레이트 비율"),
    ("analysis.bitrate_model", "base_width", "BASE_RESOLUTION_WIDTH", int, "기준 가로 해상도 (px)"),
    ("analysis.bitrate_model", "base_height", "BASE_RESOLUTION_HEIGHT", int, "기준 세로 해상도 (px)"),
    ("analysis.bitrate_model", "base_target_kbps", "BASE_TARGET_BITRATE", int, "기준 해상도·FPS 목표 비트레이트 (kbps)"),
    ("analysis.bitrate_model", "base_fps", "BASE_FPS", float, "기준 프레임레이트 (fps)"),
    ("analysis.bitrate_model", "area_scaling_exponent", "AREA_SCALING_EXPONENT", float, "해상도 면적 스케일링 지수"),
    ("analysis.bitrate_model", "fps_scaling_exponent", "FPS_SCALING_EXPONENT", float, "프레임레이트 보정 지수"),
    ("cui", "float_format", "TABULATE_FLOATFMT", str, "실수 소수점 출력 포맷"),
    ("cui", "filename_maxlen", "TABULATE_FILENAME_MAXLEN", int, "콘솔 표 파일명 최대 표시 길이 (자)"),
)


@dataclass
class AppState:
    """분석 설정·작업 경로·최근 폴더·예외 규칙·UI 필터·레이아웃을 통합 보관하는 단일 상태 컨테이너"""

    config: ClassifierConfig = field(default_factory=ClassifierConfig)
    root_dir: str = DEFAULT_ROOT_DIR
    recent_dirs: list[str] = field(default_factory=list)
    exception_tokens: list[str] = field(default_factory=list)
    scan_mode: str = DEFAULT_SCAN_MODE
    filter_search: str = DEFAULT_FILTER_SEARCH
    filter_zone: str = DEFAULT_FILTER_ZONE
    filter_target: str = DEFAULT_FILTER_TARGET
    theme_mode: str = DEFAULT_THEME_MODE
    window_geometry: str = ""
    table_header_state: str = ""

    def to_dict(self) -> dict[str, Any]:
        """계층화된 통합 설정 스키마 사전 표현으로 변환"""
        raw_cfg = self.config.to_dict()
        paths: dict[str, Any] = {
            "root_dir": self.root_dir,
            "recent_dirs": list(self.recent_dirs),
        }
        thresholds: dict[str, Any] = {}
        small_footage: dict[str, Any] = {}
        codec_weights: dict[str, Any] = {}
        bitrate_model: dict[str, Any] = {}
        cui: dict[str, Any] = {}

        section_targets: dict[str, dict[str, Any]] = {
            "paths": paths,
            "analysis.thresholds": thresholds,
            "analysis.small_footage": small_footage,
            "analysis.codec_weights": codec_weights,
            "analysis.bitrate_model": bitrate_model,
            "cui": cui,
        }
        for section, field_key, config_key, val_type, _ in _CONFIG_FIELD_SPECS:
            section_targets[section][field_key] = _coerce_typed_value(
                raw_cfg[config_key], val_type
            )

        return {
            "paths": paths,
            "analysis": {
                "thresholds": thresholds,
                "small_footage": small_footage,
                "codec_weights": codec_weights,
                "bitrate_model": bitrate_model,
            },
            "cui": cui,
            "ui": {
                "theme_mode": self.theme_mode,
                "scan_mode": self.scan_mode,
                "exception_tokens": list(self.exception_tokens),
                "filters": {
                    "search": self.filter_search,
                    "zone": self.filter_zone,
                    "target": self.filter_target,
                },
                "layout": {
                    "window_geometry": self.window_geometry,
                    "table_header_state": self.table_header_state,
                },
            },
        }


def _coerce_typed_value(raw: Any, val_type: type[Any]) -> Any:
    if val_type is int:
        return int(float(str(raw)))
    if val_type is float:
        return round(float(str(raw)), 4)
    return str(raw)


def _strip_comments_pass(text: str) -> str:
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

        if ch == "/" and i + 1 < n:
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

        out.append(ch)
        i += 1
    return "".join(out)


def _strip_trailing_commas_pass(text: str) -> str:
    out: list[str] = []
    in_string = False
    escaped = False
    n = len(text)
    for i, ch in enumerate(text):
        if in_string:
            out.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            out.append(ch)
            continue

        if ch == ",":
            j = i + 1
            while j < n and text[j] in (" ", "\t", "\r", "\n"):
                j += 1
            if j < n and text[j] in ("}", "]"):
                continue

        out.append(ch)
    return "".join(out)


def strip_jsonc_comments(text: str) -> str:
    """문자열 리터럴 내부를 보존하며 JSONC의 주석과 후행 쉼표를 제거"""
    return _strip_trailing_commas_pass(_strip_comments_pass(text))


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


def _dig_mapping(raw: dict[str, Any], path: str) -> dict[str, Any]:
    cur: Any = raw
    for part in path.split("."):
        if not isinstance(cur, dict):
            return {}
        cur = cur.get(part)
    return cur if isinstance(cur, dict) else {}


def extract_fallback_root_dir(raw: dict[str, Any]) -> str:
    """통합 설정 사전에서 paths.root_dir 기본 작업 폴더 경로 추출"""
    paths = _dig_mapping(raw, "paths")
    return str(paths.get("root_dir", DEFAULT_ROOT_DIR) or "").strip()


def _parse_classifier_config(raw: dict[str, Any]) -> ClassifierConfig:
    merged = dict(DEFAULT_CONFIGS)
    for section_path, field_key, config_key, _, _ in _CONFIG_FIELD_SPECS:
        sec_map = _dig_mapping(raw, section_path)
        if field_key in sec_map and sec_map[field_key] is not None:
            merged[config_key] = str(sec_map[field_key])
    return ClassifierConfig.from_mapping(merged)


def parse_state_mapping(raw: dict[str, Any], *, effective_root: str) -> AppState:
    """계층화된 통합 설정 사전을 AppState 객체로 복원"""
    cfg = _parse_classifier_config(raw)
    paths = _dig_mapping(raw, "paths")
    ui = _dig_mapping(raw, "ui")
    filters = _dig_mapping(ui, "filters") if ui else {}
    layout = _dig_mapping(ui, "layout") if ui else {}

    search_raw = filters.get("search")
    return AppState(
        config=cfg,
        root_dir=effective_root,
        recent_dirs=_sanitize_str_list(
            paths.get("recent_dirs"), max_items=MAX_RECENT_DIRS
        ),
        exception_tokens=_sanitize_str_list(ui.get("exception_tokens")),
        scan_mode=_sanitize_choice(
            ui.get("scan_mode"), VALID_SCAN_MODES, DEFAULT_SCAN_MODE
        ),
        filter_search=str(search_raw if search_raw is not None else DEFAULT_FILTER_SEARCH),
        filter_zone=_sanitize_choice(
            filters.get("zone"), VALID_FILTER_ZONES, DEFAULT_FILTER_ZONE
        ),
        filter_target=_sanitize_choice(
            filters.get("target"), VALID_FILTER_TARGETS, DEFAULT_FILTER_TARGET
        ),
        theme_mode=_sanitize_choice(
            ui.get("theme_mode"), VALID_THEME_MODES, DEFAULT_THEME_MODE
        ),
        window_geometry=str(layout.get("window_geometry", "") or ""),
        table_header_state=str(layout.get("table_header_state", "") or ""),
    )


def _format_kv_line(
    indent: int,
    key: str,
    value: Any,
    comment: str,
    *,
    trailing_comma: bool = True,
) -> str:
    prefix = " " * indent
    encoded_key = json.dumps(key, ensure_ascii=False)
    encoded_val = json.dumps(value, ensure_ascii=False)
    comma = "," if trailing_comma else ""
    body = f"{prefix}{encoded_key}: {encoded_val}{comma}"
    if not comment:
        return body
    pad = max(2, _COMMENT_ALIGN_COL - len(body))
    return f"{body}{' ' * pad}// {comment}"


def render_state_jsonc(state: AppState) -> str:
    """AppState를 섹션 구분과 인라인 한국어 주석이 포함된 가독성 높은 JSONC 문자열로 직렬화"""
    data = state.to_dict()
    comment_map: dict[tuple[str, str], str] = {
        (section, field_key): desc for section, field_key, _, _, desc in _CONFIG_FIELD_SPECS
    }

    paths: dict[str, Any] = data["paths"]
    analysis: dict[str, dict[str, Any]] = data["analysis"]
    cui: dict[str, Any] = data["cui"]
    ui: dict[str, Any] = data["ui"]
    filters: dict[str, Any] = ui["filters"]
    layout: dict[str, Any] = ui["layout"]

    lines: list[str] = [
        "// Video Classifier Studio 통합 설정·상태 파일 (JSONC 형식, // 주석 지원)",
        "// - 루트 .rootdir 파일에 경로가 입력되어 있으면 paths.root_dir보다 항상 우선 적용됩니다.",
        "{",
        "  // 1. 경로 및 분류 폴더",
        '  "paths": {',
        _format_kv_line(4, "root_dir", paths["root_dir"], "기본 작업 폴더 (비어 있으면 .rootdir 또는 최근 선택 경로 사용)"),
        _format_kv_line(4, "recent_dirs", paths["recent_dirs"], f"최근 작업 폴더 목록 (최대 {MAX_RECENT_DIRS}개)"),
        _format_kv_line(4, "dir_standard_cq22", paths["dir_standard_cq22"], comment_map[("paths", "dir_standard_cq22")]),
        _format_kv_line(4, "dir_low_quality_keyframe", paths["dir_low_quality_keyframe"], comment_map[("paths", "dir_low_quality_keyframe")], trailing_comma=False),
        "  },",
        "",
        "  // 2. 화질 및 키프레임 분석 기준",
        '  "analysis": {',
        '    "thresholds": {',
        _format_kv_line(6, "over_bitrate_ratio", analysis["thresholds"]["over_bitrate_ratio"], comment_map[("analysis.thresholds", "over_bitrate_ratio")]),
        _format_kv_line(6, "low_bitrate_ratio", analysis["thresholds"]["low_bitrate_ratio"], comment_map[("analysis.thresholds", "low_bitrate_ratio")]),
        _format_kv_line(6, "keyframe_interval_sec", analysis["thresholds"]["keyframe_interval_sec"], comment_map[("analysis.thresholds", "keyframe_interval_sec")], trailing_comma=False),
        "    },",
        '    "small_footage": {',
        _format_kv_line(6, "size_mb", analysis["small_footage"]["size_mb"], comment_map[("analysis.small_footage", "size_mb")]),
        _format_kv_line(6, "min_saved_mb", analysis["small_footage"]["min_saved_mb"], comment_map[("analysis.small_footage", "min_saved_mb")]),
        _format_kv_line(6, "cumulative_ratio", analysis["small_footage"]["cumulative_ratio"], comment_map[("analysis.small_footage", "cumulative_ratio")]),
        _format_kv_line(6, "cumulative_mb", analysis["small_footage"]["cumulative_mb"], comment_map[("analysis.small_footage", "cumulative_mb")]),
        _format_kv_line(6, "keyframe_1s_overhead_rate", analysis["small_footage"]["keyframe_1s_overhead_rate"], comment_map[("analysis.small_footage", "keyframe_1s_overhead_rate")], trailing_comma=False),
        "    },",
        '    "codec_weights": {',
        _format_kv_line(6, "av1", analysis["codec_weights"]["av1"], comment_map[("analysis.codec_weights", "av1")]),
        _format_kv_line(6, "hevc_vp9", analysis["codec_weights"]["hevc_vp9"], comment_map[("analysis.codec_weights", "hevc_vp9")]),
        _format_kv_line(6, "h264", analysis["codec_weights"]["h264"], comment_map[("analysis.codec_weights", "h264")]),
        _format_kv_line(6, "legacy", analysis["codec_weights"]["legacy"], comment_map[("analysis.codec_weights", "legacy")], trailing_comma=False),
        "    },",
        '    "bitrate_model": {',
        _format_kv_line(6, "base_width", analysis["bitrate_model"]["base_width"], comment_map[("analysis.bitrate_model", "base_width")]),
        _format_kv_line(6, "base_height", analysis["bitrate_model"]["base_height"], comment_map[("analysis.bitrate_model", "base_height")]),
        _format_kv_line(6, "base_target_kbps", analysis["bitrate_model"]["base_target_kbps"], comment_map[("analysis.bitrate_model", "base_target_kbps")]),
        _format_kv_line(6, "base_fps", analysis["bitrate_model"]["base_fps"], comment_map[("analysis.bitrate_model", "base_fps")]),
        _format_kv_line(6, "area_scaling_exponent", analysis["bitrate_model"]["area_scaling_exponent"], comment_map[("analysis.bitrate_model", "area_scaling_exponent")]),
        _format_kv_line(6, "fps_scaling_exponent", analysis["bitrate_model"]["fps_scaling_exponent"], comment_map[("analysis.bitrate_model", "fps_scaling_exponent")], trailing_comma=False),
        "    }",
        "  },",
        "",
        "  // 3. CUI(노트북·터미널) 표 출력",
        '  "cui": {',
        _format_kv_line(4, "float_format", cui["float_format"], comment_map[("cui", "float_format")]),
        _format_kv_line(4, "filename_maxlen", cui["filename_maxlen"], comment_map[("cui", "filename_maxlen")], trailing_comma=False),
        "  },",
        "",
        "  // 4. GUI 화면·필터·예외 상태 (앱 사용 시 자동 저장)",
        '  "ui": {',
        _format_kv_line(4, "theme_mode", ui["theme_mode"], '테마: "system" | "dark" | "light"'),
        _format_kv_line(4, "scan_mode", ui["scan_mode"], '스캔 모드: "fast" | "full"'),
        _format_kv_line(4, "exception_tokens", ui["exception_tokens"], "인코딩 제외 키워드·AST 조건식 목록"),
        '    "filters": {',
        _format_kv_line(6, "search", filters["search"], "파일명·코덱 검색어"),
        _format_kv_line(6, "zone", filters["zone"], '화질 필터: "ALL" | "OVER" | "OPTIMAL" | "LOW"'),
        _format_kv_line(6, "target", filters["target"], '대상 필터: "ALL" | "ENCODE_ONLY" | "PASS_ONLY" | "DEFECT_KF"', trailing_comma=False),
        "    },",
        '    "layout": {',
        _format_kv_line(6, "window_geometry", layout["window_geometry"], "창 크기·위치 상태 (Base64)"),
        _format_kv_line(6, "table_header_state", layout["table_header_state"], "테이블 열 배치 상태 (Base64)", trailing_comma=False),
        "    }",
        "  }",
        "}",
        "",
    ]
    return "\n".join(lines)
