"""테이블 열 데이터 타입 스키마 및 동적 필터 조건 모델 모듈"""

from dataclasses import dataclass
from enum import Enum

from src.core.config import ClassifierConfig
from src.gui.numeric_buckets import (
    NumericBucketKind,
    build_numeric_buckets,
    resolve_numeric_bucket,
)

TEXT_MODE_CONTAINS: str = "contains"
TEXT_MODE_NOT_CONTAINS: str = "not_contains"
TEXT_MODE_STARTS_WITH: str = "starts_with"
TEXT_MODE_ENDS_WITH: str = "ends_with"

TEXT_FILTER_MODES: tuple[tuple[str, str], ...] = (
    ("포함", TEXT_MODE_CONTAINS),
    ("제외", TEXT_MODE_NOT_CONTAINS),
    ("시작 문자", TEXT_MODE_STARTS_WITH),
    ("끝 문자", TEXT_MODE_ENDS_WITH),
)


class ColumnDataType(Enum):
    """테이블 열의 동적 필터링 방식과 팝오버 UI를 결정하는 데이터 타입"""

    ENUM = "enum"
    CATEGORY = "category"
    NUMBER = "number"
    TEXT = "text"

    @property
    def label(self) -> str:
        """팝오버 헤더에 표시할 한국어 데이터 타입 명칭"""
        return {
            ColumnDataType.ENUM: "열거형",
            ColumnDataType.CATEGORY: "범주형",
            ColumnDataType.NUMBER: "수치",
        }.get(self, "문자열")


@dataclass(frozen=True, slots=True)
class ColumnSpec:
    """개별 테이블 열의 데이터 타입, 단위, 소수점, 스텝 및 버킷 메타데이터"""

    col: int
    data_type: ColumnDataType
    unit: str = ""
    decimals: int = 2
    filter_step: float = 1.0
    nullable_label: str = ""
    preset_values: tuple[str, ...] = ()
    bucket_kind: NumericBucketKind = NumericBucketKind.NONE


def _summarize_preview_items(items: list[str], limit: int) -> str:
    preview = ", ".join(items[:limit])
    if len(items) > limit:
        preview += f" 외 {len(items) - limit}개"
    return preview


@dataclass(frozen=True, slots=True)
class ColumnFilterRule:
    """개별 테이블 열에 적용되는 동적 필터 조건"""

    selected_values: frozenset[str] | None = None
    min_value: float | None = None
    max_value: float | None = None
    include_null: bool = True
    null_only: bool = False
    text_query: str = ""
    text_mode: str = TEXT_MODE_CONTAINS

    def is_active(self, data_type: ColumnDataType) -> bool:
        """해당 데이터 타입 기준으로 실제 필터링이 활성화되었는지 반환"""
        if data_type in (ColumnDataType.ENUM, ColumnDataType.CATEGORY):
            return self.selected_values is not None
        if data_type is ColumnDataType.NUMBER:
            return (
                self.null_only
                or self.selected_values is not None
                or self.min_value is not None
                or self.max_value is not None
                or not self.include_null
            )
        return bool(self.text_query.strip())

    def matches(
        self,
        value: str | float | None,
        spec: ColumnSpec,
        config: ClassifierConfig | None = None,
    ) -> bool:
        """셀 필터 값이 현재 필터 조건과 일치하는지 판별"""
        if spec.data_type in (ColumnDataType.ENUM, ColumnDataType.CATEGORY):
            return self.selected_values is None or str(value or "") in self.selected_values

        if spec.data_type is ColumnDataType.NUMBER:
            if self.null_only:
                return value is None
            if value is None:
                return self.include_null
            if isinstance(value, str):
                return False
            num = float(value)
            if self.selected_values is not None:
                buckets = build_numeric_buckets(spec.bucket_kind, config)
                matched = resolve_numeric_bucket(num, buckets)
                if matched is None or matched.key not in self.selected_values:
                    return False
            if self.min_value is not None and num < self.min_value:
                return False
            return self.max_value is None or num <= self.max_value

        query = self.text_query.strip().lower()
        if not query:
            return True
        text = str(value or "").lower()
        if self.text_mode == TEXT_MODE_NOT_CONTAINS:
            return query not in text
        if self.text_mode == TEXT_MODE_STARTS_WITH:
            return text.startswith(query)
        if self.text_mode == TEXT_MODE_ENDS_WITH:
            return text.endswith(query)
        return query in text

    def describe(
        self,
        spec: ColumnSpec,
        config: ClassifierConfig | None = None,
    ) -> str:
        """헤더 툴팁 등에 표시할 필터 조건 한 줄 요약 문자열 반환"""
        if spec.data_type in (ColumnDataType.ENUM, ColumnDataType.CATEGORY):
            if self.selected_values is None:
                return ""
            if not self.selected_values:
                return "선택 없음 (0개)"
            return f"선택: {_summarize_preview_items(sorted(self.selected_values), 3)}"

        if spec.data_type is ColumnDataType.NUMBER:
            if self.null_only and spec.nullable_label:
                return f"{spec.nullable_label}만 표시"
            unit = f" {spec.unit}" if spec.unit else ""
            parts: list[str] = []
            if self.min_value is not None and self.max_value is not None:
                parts.append(f"{self.min_value:g} ~ {self.max_value:g}{unit}")
            elif self.min_value is not None:
                parts.append(f">= {self.min_value:g}{unit}")
            elif self.max_value is not None:
                parts.append(f"<= {self.max_value:g}{unit}")
            if self.selected_values is not None:
                if not self.selected_values:
                    parts.append("구간 선택 없음")
                else:
                    buckets = build_numeric_buckets(spec.bucket_kind, config)
                    label_map = {b.key: b.label for b in buckets}
                    ordered_labels = [
                        b.label for b in buckets if b.key in self.selected_values
                    ]
                    ordered_labels.extend(
                        sorted(k for k in self.selected_values if k not in label_map)
                    )
                    parts.append(f"구간: {_summarize_preview_items(ordered_labels, 2)}")
            if not self.include_null and spec.nullable_label:
                parts.append(f"{spec.nullable_label} 제외")
            return ", ".join(parts)

        query = self.text_query.strip()
        if not query:
            return ""
        mode_labels = {mode: label for label, mode in TEXT_FILTER_MODES}
        return f"{mode_labels.get(self.text_mode, '포함')}: '{query}'"
