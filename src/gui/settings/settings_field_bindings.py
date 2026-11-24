"""설정 행 키와 ClassifierConfig 필드 사이의 읽기, 쓰기 변환 기준 정의 모듈

- 행 키는 저장 파일과 같은 계층 표기(analysis.encode.baseline_cq, display.size_unit)를 씀
- 값 위젯 종류, 퍼센트 표시 배율, 빈 문자열 기본값 규칙을 한 곳에서 처리해 화면과 저장이 어긋나지 않게 함
"""

from dataclasses import replace
from typing import Any

from PyQt6.QtWidgets import QComboBox, QDoubleSpinBox, QLineEdit, QSpinBox

from src.core.config import (
    BitrateModelConfig,
    ClassifierConfig,
    CodecWeightsConfig,
    CuiConfig,
    DisplayConfig,
    EncodeConfig,
    ExecutionConfig,
    SavingsConfig,
    ThresholdsConfig,
)
from src.gui.settings.setting_row import (
    SettingRow,
    resolve_field_default,
)
from src.gui.settings.tuner_slider_row import TunerSliderRow
from src.gui.widgets.custom_spinbox import CustomIntegerSpinBox

FLOAT_SETTING_ROUND_DIGITS: int = 4
VALUE_SCALE_PROPERTY: str = "settingScale"

# - 행 키의 첫 마디(section 이름)와 설정 데이터클래스 대응. analysis 하위는 두 번째 마디를 씀
SETTING_SECTION_TYPES: dict[str, type[Any]] = {
    "thresholds": ThresholdsConfig,
    "savings": SavingsConfig,
    "encode": EncodeConfig,
    "codec_weights": CodecWeightsConfig,
    "bitrate_model": BitrateModelConfig,
    "display": DisplayConfig,
    "cui": CuiConfig,
    "execution": ExecutionConfig,
}

# - 설정 데이터클래스 필드가 아닌, 화면에서만 다루는 행 키
NON_CONFIG_SETTING_KEYS: frozenset[str] = frozenset({"root_dir"})


def split_setting_key(key: str) -> tuple[str, str] | None:
    """행 키를 (설정 섹션 이름, 필드 이름)으로 나누고, 설정 필드가 아니면 None 반환"""
    if key in NON_CONFIG_SETTING_KEYS:
        return None
    parts = key.split(".")
    if len(parts) == 3 and parts[0] == "analysis" and parts[1] in SETTING_SECTION_TYPES:
        return parts[1], parts[2]
    if len(parts) == 2 and parts[0] in SETTING_SECTION_TYPES:
        return parts[0], parts[1]
    return None


def _widget_value(widget: Any) -> Any:
    """입력 위젯에서 저장 값을 읽고, 퍼센트 표시 위젯은 배율로 나눠 배수로 되돌림"""
    if isinstance(widget, QComboBox):
        return widget.currentData()
    scale = widget.property(VALUE_SCALE_PROPERTY)
    factor = float(scale) if scale else 1.0
    if isinstance(widget, CustomIntegerSpinBox):
        return int(widget.value())
    if isinstance(widget, QSpinBox):
        return int(widget.value())
    if isinstance(widget, QDoubleSpinBox):
        return round(widget.value() / factor, FLOAT_SETTING_ROUND_DIGITS)
    if isinstance(widget, QLineEdit):
        return widget.text().strip()
    raise TypeError(f"지원하지 않는 설정 위젯 유형: {type(widget).__name__}")


def read_row_value(row: SettingRow | TunerSliderRow) -> Any:
    """설정 행의 현재 표시 값을 저장 값으로 변환"""
    if isinstance(row, TunerSliderRow):
        return row.value()
    return _widget_value(row.value_widget)


def read_setting_value(cfg: ClassifierConfig, key: str) -> Any:
    """설정 객체에서 행 키에 해당하는 현재 값을 조회"""
    target = split_setting_key(key)
    if target is None:
        return None
    section, field_name = target
    return getattr(getattr(cfg, section), field_name)


def apply_setting_values(
    cfg: ClassifierConfig,
    rows: dict[str, SettingRow | TunerSliderRow],
) -> ClassifierConfig:
    """설정 행 값 모음을 ClassifierConfig에 반영한 새 설정 반환

    - 빈 문자열 행은 그 필드의 기본값으로 되돌려 폴더 이름 같은 필수 값이 비지 않게 함
    """
    updates: dict[str, dict[str, Any]] = {}
    for key, row in rows.items():
        target = split_setting_key(key)
        if target is None:
            continue
        section, field_name = target
        value = read_row_value(row)
        if isinstance(value, str) and not value:
            value = str(resolve_field_default(SETTING_SECTION_TYPES[section], field_name))
        updates.setdefault(section, {})[field_name] = value
    replaced: dict[str, Any] = {
        section: replace(getattr(cfg, section), **fields_map)
        for section, fields_map in updates.items()
    }
    return replace(cfg, **replaced)


def set_row_value(row: SettingRow | TunerSliderRow, value: Any) -> None:
    """저장 값을 설정 행의 입력 위젯 표시 값으로 반영"""
    if isinstance(row, TunerSliderRow):
        row.set_value(float(value))
        return
    widget = row.value_widget
    if isinstance(widget, QComboBox):
        index = widget.findData(value)
        if index >= 0:
            widget.setCurrentIndex(index)
        return
    if isinstance(widget, (QSpinBox, CustomIntegerSpinBox)):
        widget.setValue(int(value))
        return
    if isinstance(widget, QDoubleSpinBox):
        scale = widget.property(VALUE_SCALE_PROPERTY)
        factor = float(scale) if scale else 1.0
        widget.setValue(float(value) * factor)
        return
    if isinstance(widget, QLineEdit):
        widget.setText(str(value))
