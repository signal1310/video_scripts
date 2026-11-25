"""테이블 셀 표시 값과 정렬 값 비교 키를 계산하는 공용 모듈

- 표 모델과 CSV 내보내기, 미리 보기 정보 표시가 같은 함수를 써서 값이 어긋나지 않게 함
- 워커 스레드에서도 호출 가능하며 Qt 객체에 접근하지 않음
"""

from collections.abc import Mapping
from typing import Any

from src.core.config import ClassifierConfig
from src.core.models import VideoEvaluation
from src.core.resolution_tiers import ResolutionTier
from src.gui.styles.palettes import ThemePalette
from src.gui.table.frame_analysis_columns import FrameAnalysisResults, is_frame_analysis_column
from src.gui.table.table_column_formats import (
    OUTPUT_RES_NOT_DOWNSCALED_CELL,
    encode_state_foreground,
)
from src.gui.table.table_columns import COL_DEF_MAP

OUTPUT_RES_COLUMN_INDEX: int = 18


def foreground_color(
    ev: VideoEvaluation,
    col: int,
    palette: ThemePalette,
    *,
    frame_view: FrameAnalysisResults,
    encode_label: str | None,
    encode_state_col: int,
) -> str | None:
    """셀 글자색. 인코딩 상태, 화질 분석 열, 열 정의 순서로 결정

    - 화질 분석 활성 화면은 잘라내기 제외 영상을 흐린 색으로 표시
    """
    if col == encode_state_col:
        return encode_state_foreground(encode_label, palette)
    if is_frame_analysis_column(col):
        return frame_view.foreground(ev.video.filename, col, palette)
    col_def = COL_DEF_MAP.get(col)
    if col_def is None or col_def.foreground_getter is None:
        return None
    return col_def.foreground_getter(ev, palette)


def display_text_for(
    ev: VideoEvaluation,
    col: int,
    config: ClassifierConfig,
    pending_tiers: Mapping[str, ResolutionTier | None],
    output_res_col: int = OUTPUT_RES_COLUMN_INDEX,
) -> Any:
    """셀의 표시 값을 설정과 임시 출력 해상도 입력만으로 계산

    - 화면 표시와 CSV 저장이 같은 함수를 써서 값이 어긋나지 않게 하며, 워커 스레드에서도 호출 가능
    - 출력 해상도 열 번호는 기본값으로 두고, 호출자가 다른 값을 넘길 수 있음
    """
    col_def = COL_DEF_MAP.get(col)
    if col_def is None:
        return None
    if col == output_res_col and ev.video.filename in pending_tiers:
        tier = pending_tiers[ev.video.filename]
        return tier.display if tier is not None else OUTPUT_RES_NOT_DOWNSCALED_CELL
    if col_def.display_value is not None:
        return col_def.display_value(ev, config)
    val = col_def.filter_value(ev)
    if col_def.numeric_format is not None and isinstance(val, int | float):
        return format(val, col_def.numeric_format)
    return val


def comparable_sort_key(value: Any) -> tuple[Any, ...]:
    """정렬 값을 튜플 키로 바꿔 None을 가장 앞, 자연 정렬 토큰은 int < str 순서로 비교되게 함

    - 파일명 토큰 목록은 타입이 섞여도 비교가 되도록 토큰마다 (타입 순위, 값)으로 감쌈
    """
    if value is None:
        return (0,)
    if isinstance(value, list):
        return (1, tuple((0, tok) if isinstance(tok, int) else (1, tok) for tok in value))
    return (1, value)
