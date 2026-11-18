"""사용 가이드의 판정 원리 탭, 판별 수식 해설과 현재 설정값 기반 계산 예시 HTML 조립 모듈"""

from __future__ import annotations

import html
from collections.abc import Mapping

from src.core.classification_policy import (
    CODEC_UPGRADE_MIN_GAIN_RATIO,
    INTRA_CODEC_MIN_WEIGHT,
    LEGACY_BLOCK_NOISE_MAX_RATIO,
    LEGACY_BLOCK_NOISE_MIN_WEIGHT,
)
from src.core.config import (
    SAVINGS_BASIS_ENCODE,
    BitrateModelConfig,
    ClassifierConfig,
    get_encode_codec_family,
    get_encode_codec_label,
)
from src.core.evaluator import LOW_QUALITY_PRESET_SPEED_MULTIPLIER
from src.core.messages import format_over_bitrate_desc
from src.core.quality_metrics import (
    FPS_HYSTERESIS_TOLERANCE,
    MIN_OPTIMAL_BITRATE_KBPS,
    REFERENCE_ENCODE_FPS,
    REFERENCE_ENCODE_HEIGHT,
    REFERENCE_ENCODE_WIDTH,
    SAME_OR_BETTER_CODEC_OVER_MARGIN,
    TEN_BIT_SDR_OVER_MARGIN,
)
from src.core.quality_target import (
    calc_base_bitrate_kbps,
    format_cq_value,
    protect_cq_value,
)
from src.core.shortcuts import DEFAULT_SHORTCUTS
from src.core.size_estimator import (
    DEFAULT_UNKNOWN_KF_SEC,
    FPS_KF_CORRECTION_EXPONENT,
    FPS_KF_FACTOR_MAX,
    FPS_KF_FACTOR_MIN,
    KF_CLAMP_MAX_SEC,
    KF_CLAMP_MIN_SEC,
    STATIC_SCREEN_RATIO_FLOOR,
    STATIC_SCREEN_RATIO_THRESHOLD,
)
from src.gui.dialogs.help_blocks import (
    GAP_HTML,
    callout_html,
    card_html,
    formula_html,
    fraction_html,
    inline_cmd,
    note_html,
    render_styles,
)
from src.gui.styles.palettes import ThemePalette

_EXAMPLE_WIDTH: int = 3840
_EXAMPLE_HEIGHT: int = 2160
_EXAMPLE_FPS: float = 60.0
_EXAMPLE_BITRATE_MULTIPLE: float = 2.0
_EXAMPLE_SIZE_MB: float = 1000.0
_EXAMPLE_STREAM_MB: float = 950.0
_EXAMPLE_DURATION_MIN: float = 10.0
_EXAMPLE_KEYFRAME_SEC: float = 8.0


def _area_factor(bm: BitrateModelConfig, width: int, height: int) -> float:
    return (width * height / (bm.base_width * bm.base_height)) ** bm.area_scaling_exponent


def _fps_factor(bm: BitrateModelConfig, fps: float) -> float:
    if fps > bm.base_fps + FPS_HYSTERESIS_TOLERANCE:
        return (fps / bm.base_fps) ** bm.fps_scaling_exponent
    return 1.0


def _workload_ratio(width: int, height: int, fps: float) -> float:
    """1080p 30fps 대비 초당 화소 처리량 배수(W_ratio)"""
    return (width * height * fps) / (
        REFERENCE_ENCODE_WIDTH * REFERENCE_ENCODE_HEIGHT * REFERENCE_ENCODE_FPS
    )


def _outline_html() -> str:
    return (
        "<h2>판정 원리, 수식 해설</h2>"
        "<p>이 프로그램은 영상마다 <b>&quot;이 정도 화질이면 비트레이트가 얼마면 충분한가&quot;</b>를 먼저 계산하고, "
        "실제 영상과 비교해 재인코딩 여부를 정합니다. 아래 여섯 단계 순서로 읽으면 흐름이 이어집니다.</p>"
        "<ol>"
        "<li><b>최적 비트레이트</b>: 원본 코덱과 출력 코덱 각각의 충분한 비트레이트 기준선 계산</li>"
        "<li><b>BR 비율</b>: 실제 영상을 두 기준선과 비교해 화질 3구간으로 판정</li>"
        "<li><b>예상 용량과 절약</b>: 재인코딩 후 용량 예측</li>"
        "<li><b>절약 선별</b>: 작업 시간 1분당 얻는 용량 절약으로 재인코딩할 가치 판단</li>"
        "<li><b>예상 시간</b>: 재인코딩에 걸리는 시간 어림</li>"
        "<li><b>판정 순서</b>: 예외, HDR, 화질 구간의 우선순위</li>"
        "</ol>"
        "<p>수식이 어렵게 느껴지면 각 절의 <b>쉽게 말하면</b> 상자와 <b>계산 예시</b>만 읽어도 충분합니다. "
        "표시된 숫자는 모두 현재 설정값을 반영하며, 설정을 바꾸면 이 화면도 함께 바뀝니다.</p>"
    )


def _optimal_bitrate_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    bm = cfg.bitrate_model
    cw = cfg.codec_weights
    codec = cfg.encode.encode_codec
    label = html.escape(get_encode_codec_label(codec))
    out_weight = cw.get_weight(get_encode_codec_family(codec))
    area_scale_4x = 4.0**bm.area_scaling_exponent
    fps_60_scale = (60.0 / max(bm.base_fps, 1.0)) ** bm.fps_scaling_exponent
    area = _area_factor(bm, _EXAMPLE_WIDTH, _EXAMPLE_HEIGHT)
    fps = _fps_factor(bm, _EXAMPLE_FPS)
    base_kbps = calc_base_bitrate_kbps(bm.base_target_kbps, cfg.encode.baseline_cq)
    raw_kbps = base_kbps * area * fps * out_weight
    optimal = max(round(raw_kbps), MIN_OPTIMAL_BITRATE_KBPS)
    example = (
        f"<b>계산 예시</b>: {_EXAMPLE_WIDTH}×{_EXAMPLE_HEIGHT}, {_EXAMPLE_FPS:g}fps 영상을 {label}로 인코딩할 때<br/>"
        f"K<sub>면적</sub> = {area:.2f}, K<sub>fps</sub> = {fps:.2f}, C<sub>코덱</sub> = {out_weight:.2f}<br/>"
        f"B<sub>opt</sub> = {round(base_kbps):,} × {area:.2f} × {fps:.2f} × {out_weight:.2f} "
        f"≈ <b>{optimal:,} kbps</b>"
    )
    return "".join(
        (
            "<h3>1. 최적 비트레이트(최적 BR) 계산식</h3>",
            callout_html(
                p,
                "<b>쉽게 말하면</b> 화면이 크고, 프레임이 많고, 오래된 코덱일수록 같은 화질을 위해 "
                "더 많은 데이터가 필요합니다. 그 &quot;충분한 양&quot;을 영상마다 계산한 값이 최적 BR입니다.",
            ),
            "<p><b>화질 기준값</b>(기본 CQ22.0)은 육안으로 원본과 차이를 거의 느끼기 어려운 고정 화질 목표입니다. "
            "같은 화질이어도 해상도, 프레임레이트(FPS), 코덱 압축 성능에 따라 필요한 비트레이트가 달라지므로, "
            "기준 영상 하나를 정해 두고 그 대비 얼마나 더 필요한지 곱해서 "
            "<b>최적 BR(B<sub>opt</sub>)</b>을 계산합니다. 코덱 계수만 다르게 넣어 "
            "<b>원본 코덱</b> 기준과 <b>목표 인코딩 코덱</b>(출력) 기준 두 값을 구합니다.</p>",
            formula_html(
                p,
                (
                    (
                        f"B<sub>opt</sub> = max( round( B<sub>기준</sub> × K<sub>면적</sub> × K<sub>fps</sub> × C<sub>코덱</sub> ), "
                        f"{MIN_OPTIMAL_BITRATE_KBPS} kbps )",
                    ),
                    (
                        "K<sub>면적</sub> = (",
                        fraction_html(p, "너비 × 높이", f"{bm.base_width} × {bm.base_height}"),
                        f")<sup>{bm.area_scaling_exponent:.2f}</sup>",
                    ),
                    (
                        "K<sub>fps</sub> = (",
                        fraction_html(p, "fps", f"{bm.base_fps:g}"),
                        f")<sup>{bm.fps_scaling_exponent:.2f}</sup>",
                    ),
                    (
                        note_html(
                            p,
                            f"fps &gt; {bm.base_fps + FPS_HYSTERESIS_TOLERANCE:g}일 때만 적용, 그 외 1.0",
                        ),
                    ),
                ),
            ),
            "<ul>",
            f"<li><b>기준선 B<sub>기준</sub></b>: <code>{bm.base_width}×{bm.base_height}, {bm.base_fps:g}fps, H.264</code>에서 "
            f"목표 화질을 만족하는 기준 비트레이트(현재 <b>{bm.base_target_kbps:,} kbps</b>)입니다. "
            "화질 기준값을 바꾸면 이 값을 기준 품질값에 맞게 환산해 씁니다. 모든 계산의 출발점입니다.</li>",
            f"<li><b>해상도 지수 α ({bm.area_scaling_exponent:.2f})</b>: 화면 면적이 4배(예: 1080p→4K) 늘어도 "
            "이웃한 픽셀끼리 비슷해 압축 성능이 좋아지므로 비트레이트가 4배까지는 필요하지 않습니다. "
            f"그래서 면적 비율에 지수를 적용해 <code>4<sup>{bm.area_scaling_exponent:.2f}</sup> ≈ {area_scale_4x:.2f}배</code>"
            "만 요구합니다.</li>",
            f"<li><b>프레임 지수 β ({bm.fps_scaling_exponent:.2f})</b>: 프레임이 많을수록 데이터가 늘지만 "
            "연속된 장면은 서로 비슷해 비례하지는 않습니다. 29.97fps와 30fps처럼 미세한 차이는 같은 구간으로 보고, "
            f"기준 FPS + {FPS_HYSTERESIS_TOLERANCE:g}fps를 넘을 때만 가중합니다. "
            f"(예: 60fps는 <code>{fps_60_scale:.2f}배</code> 보정)</li>",
            f"<li><b>코덱 가중치 C<sub>코덱</sub></b>: H.264(<code>{cw.h264:.2f}</code>) 대비 같은 화질에 필요한 비율입니다. "
            f"현재 설정은 AV1 <code>{cw.av1:.2f}</code>, HEVC/VP9 <code>{cw.hevc_vp9:.2f}</code>, "
            f"구형 코덱 <code>{cw.legacy:.2f}</code>이며 압축 성능이 높은 최신 코덱일수록 기준이 낮아집니다.</li>",
            f"<li><b>하한 {MIN_OPTIMAL_BITRATE_KBPS} kbps</b>: 해상도가 아주 작은 영상에서 기준이 "
            "비현실적으로 낮아지는 것을 막습니다.</li>",
            "</ul>",
            card_html(p, example),
        )
    )


def _ratio_html(p: ThemePalette, cfg: ClassifierConfig, shortcuts: Mapping[str, str]) -> str:
    t = cfg.thresholds
    tuner = inline_cmd("판별 기준", shortcuts, "edit.open_tuner")
    label = html.escape(get_encode_codec_label(cfg.encode.encode_codec))
    over_example = html.escape(format_over_bitrate_desc(cfg, t.over_bitrate_ratio))
    return "".join(
        (
            "<h3>2. BR 비율과 화질 3구간 판정</h3>",
            callout_html(
                p,
                "<b>쉽게 말하면</b> 실제 영상이 &quot;충분한 양&quot;의 몇 배인지 재는 값입니다. "
                "1배면 딱 맞고, 크면 낭비, 작으면 이미 많이 줄여 둔 상태입니다. "
                "원본 코덱 기준과 출력 코덱 기준 두 값을 함께 봅니다.",
            ),
            formula_html(
                p,
                (
                    (
                        "BR 비율(r<sub>원본</sub>) = ",
                        fraction_html(
                            p, "현재 영상 비트레이트(B<sub>curr</sub>)", "최적 BR, 원본 코덱(B<sub>opt</sub>)"
                        ),
                    ),
                    (
                        "출력 BR 비율(r<sub>출력</sub>) = ",
                        fraction_html(
                            p,
                            "현재 영상 비트레이트(B<sub>curr</sub>)",
                            f"최적 BR, {label} 기준(B<sub>opt,출력</sub>)",
                        ),
                    ),
                ),
            ),
            "<ul>",
            f"<li><b>저화질</b> (<code>r<sub>원본</sub> &lt; {t.low_bitrate_ratio:.2f}배</code>): 이미 많이 압축된 영상입니다. "
            "원본 코덱 기준으로 판단하며, 여기서 더 압축하면 눈에 띄게 나빠질 수 있어 재인코딩으로부터 보호합니다. "
            "이 조건이 가장 먼저 적용됩니다.</li>",
            f"<li><b>과화질</b> (<code>r<sub>출력</sub> &gt; 유효 상한</code>): 저화질이 아니면서 "
            f"목표 코덱({label}) 기준으로도 필요 이상으로 비트레이트가 높습니다. "
            f"기본 상한은 <code>{t.over_bitrate_ratio:.2f}배</code>(예: {over_example})이며, "
            f"원본이 목표 코덱 이상 성능(<code>C<sub>원본</sub> ≤ C<sub>출력</sub></code>)이면 "
            f"세대 손실 방어를 위해 <code>+{SAME_OR_BETTER_CODEC_OVER_MARGIN:.2f}</code>, "
            f"비-HDR 10-bit 영상이면 컬러 밴딩 방어를 위해 <code>+{TEN_BIT_SDR_OVER_MARGIN:.2f}</code>를 더합니다.</li>",
            "<li><b>적정</b>: 위 두 조건에 해당하지 않는 나머지입니다. 키프레임이 정상이면 보통 보존하지만, "
            f"원본이 구형 코덱이어서 목표 코덱 대비 <code>{CODEC_UPGRADE_MIN_GAIN_RATIO:.2f}배</code> 이상 "
            "압축 성능 차이가 있으면 코덱 전환 절약 대상으로 검토합니다.</li>",
            "</ul>",
            "<p>출력 코덱을 바꾸면 과화질 판정이 달라집니다. 예를 들어 H.264 원본 1500 kbps 720p 영상은 "
            "HEVC 출력 기준으로 1.25배라 과화질이지만, H.264 출력 기준으로는 1.00배라 적정입니다. "
            "기본 가중치에서는 H.264 원본도 HEVC 목표 기준 1.25배라 적정 영상이 코덱 전환 후보가 됩니다. 이는 의도된 동작입니다. "
            f"경계값은 <b>{tuner}</b>에서 바꿀 수 있습니다.</p>",
        )
    )


def _size_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    t = cfg.thresholds
    rate = t.keyframe_1s_increase_rate
    ratio = _EXAMPLE_BITRATE_MULTIPLE
    other_mb = _EXAMPLE_SIZE_MB - _EXAMPLE_STREAM_MB
    over_after = other_mb + _EXAMPLE_STREAM_MB / ratio
    kf_clamped = min(max(_EXAMPLE_KEYFRAME_SEC, KF_CLAMP_MIN_SEC), KF_CLAMP_MAX_SEC)
    kf_ratio = rate * (1.0 - 1.0 / kf_clamped)
    kf_after = other_mb + _EXAMPLE_STREAM_MB * (1.0 + kf_ratio)
    over_example = (
        f"<b>계산 예시 (과화질)</b>: {_EXAMPLE_SIZE_MB:,.0f} MB 영상(영상 스트림 {_EXAMPLE_STREAM_MB:,.0f} MB, "
        f"나머지 {other_mb:,.0f} MB)의 r<sub>출력</sub>이 {ratio:.1f}배이고 키프레임이 정상이면<br/>"
        f"예상 용량 = {other_mb:,.0f} + {_EXAMPLE_STREAM_MB:,.0f} ÷ {ratio:.1f} = <b>{over_after:,.0f} MB</b>, "
        f"절약 ΔS = <b>{_EXAMPLE_SIZE_MB - over_after:,.0f} MB</b>"
    )
    kf_example = (
        f"<b>계산 예시 (1초 키프레임 재인코딩)</b>: 같은 영상의 r<sub>출력</sub>이 1.0배이고 키프레임 간격이 {_EXAMPLE_KEYFRAME_SEC:g}초이면<br/>"
        f"증가율 = {rate:.2f} × (1 − 1/{kf_clamped:g}) = {kf_ratio:.4f}, "
        f"예상 용량 = {other_mb:,.0f} + {_EXAMPLE_STREAM_MB:,.0f} × (1 + {kf_ratio:.4f}) = <b>{kf_after:,.1f} MB</b> "
        f"(<b>{kf_after - _EXAMPLE_SIZE_MB:+,.1f} MB</b>)"
    )
    return "".join(
        (
            "<h3>3. 예상 용량, 예상 절약, 키프레임 재인코딩 용량 증가율</h3>",
            callout_html(
                p,
                "<b>쉽게 말하면</b> 재인코딩하면 용량이 얼마나 줄지 미리 어림해 보는 단계입니다. "
                "오디오처럼 바뀌지 않는 부분은 그대로 두고, 영상 부분만 줄이거나 늘립니다.",
            ),
            formula_html(
                p,
                (
                    (note_html(p, "영상 스트림과 고정 부분 나누기"),),
                    ("영상 스트림 용량 = min( 현재 용량, 비트레이트 × 길이 )",),
                    ("고정 용량(오디오 등) = 현재 용량 − 영상 스트림 용량",),
                    (note_html(p, "예상 용량 계산 (실제 재인코딩과 원본 보존 행의 가상 값 모두 같은 식)"),),
                    (
                        "예상 용량 = 고정 용량 + 영상 스트림 용량 × factor<sub>br</sub> × (1 + 증가율)",
                    ),
                    (
                        "factor<sub>br</sub> = min(1, ",
                        fraction_html(p, "1", "r<sub>출력</sub>"),
                        ", ",
                        fraction_html(p, "C<sub>출력</sub>", "C<sub>원본</sub>"),
                        ")",
                        note_html(
                            p,
                            "C<sub>원본</sub> &lt; C<sub>출력</sub>(코덱 역전)이고 r<sub>출력</sub> &lt; 1이면 min(1/r<sub>출력</sub>, C<sub>출력</sub>/C<sub>원본</sub>)",
                        ),
                    ),
                    (
                        f"증가율 = {rate:.2f} × (1 − ",
                        fraction_html(
                            p,
                            "1",
                            f"clamp(키프레임 간격, {KF_CLAMP_MIN_SEC:.1f}, {KF_CLAMP_MAX_SEC:.1f})",
                        ),
                        f") × K<sub>fps,kf</sub> × W<sub>정적</sub> (간격 미측정 시 {DEFAULT_UNKNOWN_KF_SEC:.0f}초)",
                    ),
                    (
                        "K<sub>fps,kf</sub> = clamp((",
                        fraction_html(p, f"{cfg.bitrate_model.base_fps:g}", "fps"),
                        f")<sup>{FPS_KF_CORRECTION_EXPONENT:.1f}</sup>, {FPS_KF_FACTOR_MIN:.1f}, {FPS_KF_FACTOR_MAX:.1f}),  "
                        f"W<sub>정적</sub> = r<sub>원본</sub> &lt; {STATIC_SCREEN_RATIO_THRESHOLD:.2f}일 때 "
                        f"{STATIC_SCREEN_RATIO_THRESHOLD:.2f} / max(r<sub>원본</sub>, {STATIC_SCREEN_RATIO_FLOOR:.2f})",
                    ),
                    (note_html(p, "과화질 재인코딩은 원본 용량을 넘지 않도록 상한 적용"),),
                    ("ΔS = 현재 용량 − 예상 용량",),
                    (note_html(p, "+: 절약, −: 키프레임 재인코딩 또는 코덱 역전에 따른 증가"),),
                ),
            ),
            "<ul>",
            "<li><b>오디오는 바뀌지 않습니다</b>: 비디오 비트레이트 조절은 영상 스트림에만 적용하고, "
            "오디오와 컨테이너 용량은 그대로 둡니다. 스트림 비트레이트가 비어 있는 <code>pcm_*</code> 무손실 오디오는 "
            "채널 수 × 샘플레이트 × 샘플당 비트로 오디오 비트레이트를 역산 차감해 비디오 비중을 과대 추정하지 않습니다.</li>",
            "<li><b>코덱 전환과 코덱 역전 반영</b>: <code>r<sub>원본</sub> &lt; 1.0</code>인 구형 코덱도 "
            "<code>C<sub>출력</sub> ÷ C<sub>원본</sub></code> 압축 향상분을 반영해 예상 용량을 계산하며, "
            "반대로 <code>AV1 → H.264</code>처럼 코덱 역전이 일어나면 용량 증가를 그대로 반영합니다.</li>",
            f"<li><b>키프레임 재인코딩 증가(-)</b>: 간격이 <b>{t.keyframe_interval_sec:.1f}초</b>를 넘으면 "
            f"1초 키프레임을 넣으며 용량이 소폭 늘어납니다. 고FPS 영상은 GOP 비중이 낮아 "
            f"<code>({cfg.bitrate_model.base_fps:g}/fps)<sup>{FPS_KF_CORRECTION_EXPONENT:.1f}</sup></code>로 과대 예측을 보정하고, "
            f"<code>r<sub>원본</sub> &lt; {STATIC_SCREEN_RATIO_THRESHOLD:.2f}</code>인 초저비트레이트 정적 화면은 "
            "정적 화면 가중치를 반영합니다.</li>",
            "<li><b>간격 계산의 상하한</b>: 키프레임 간격은 계산할 때 "
            f"{KF_CLAMP_MIN_SEC:.1f}초 이상 {KF_CLAMP_MAX_SEC:.1f}초 이하로 맞춰 극단값이 결과를 왜곡하지 않게 합니다.</li>",
            "<li><b>순 절약량</b>: 상단 요약 카드는 과화질 재인코딩 절약(<code>+</code>)에서 키프레임 재인코딩 증가분(<code>-</code>)을 "
            "뺀 실제 순 절약 용량을 보여줍니다. (표의 회색 수치는 원본 보존 행을 재인코딩한다고 가정한 참고값입니다.)</li>",
            "</ul>",
            card_html(p, over_example),
            GAP_HTML,
            card_html(p, kf_example),
        )
    )


def _savings_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    savings = cfg.savings
    ratio = _EXAMPLE_BITRATE_MULTIPLE
    potential = _EXAMPLE_SIZE_MB * (1.0 - 1.0 / ratio)
    playback_per_min = potential / _EXAMPLE_DURATION_MIN
    workload = _workload_ratio(1920, 1080, 30.0)
    encode_min = _EXAMPLE_DURATION_MIN * workload / max(cfg.encode.encode_speed_factor, 0.01)
    encode_per_min = potential / encode_min
    is_encode_basis = savings.selection_basis == SAVINGS_BASIS_ENCODE
    basis_name = "예상 인코딩 시간 기준" if is_encode_basis else "재생 시간 기준"
    selected_per_min = encode_per_min if is_encode_basis else playback_per_min
    passes = potential >= savings.min_saved_mb and selected_per_min >= savings.min_mb_per_min
    verdict = "두 기준을 모두 넘으므로 재인코딩 대상" if passes else "기준에 못 미쳐 원본 보존"
    exempt_line = (
        f"재생 시간 기준에서는 총 예상 절약량이 <b>{savings.exempt_saved_mb:.0f} MB 이상</b>이면 분당 절약이 낮아도 예외적으로 포함합니다. "
        "예상 인코딩 시간 기준에서는 이 예외를 적용하지 않습니다."
        if savings.exempt_saved_mb > 0
        else "현재 대용량 절약 예외 허용은 비활성화(0 MB)되어 있습니다."
    )
    example = (
        f"<b>계산 예시</b>: {_EXAMPLE_SIZE_MB:,.0f} MB, {_EXAMPLE_DURATION_MIN:g}분 1080p 30fps 영상의 r<sub>출력</sub>이 {ratio:.1f}배이면<br/>"
        f"S<sub>pot</sub> = {_EXAMPLE_SIZE_MB:,.0f} × (1 − 1/{ratio:.1f}) = {potential:,.0f} MB<br/>"
        f"재생 시간 1분당 절약 = {potential:,.0f} ÷ {_EXAMPLE_DURATION_MIN:g} = <b>{playback_per_min:,.1f} MB/분</b><br/>"
        f"예상 인코딩 시간 1분당 절약 = {potential:,.0f} ÷ {encode_min:,.1f}분 = <b>{encode_per_min:,.1f} MB/분</b> "
        f"(예상 인코딩 시간 {encode_min:,.1f}분)<br/>"
        f"현재 선택은 <b>{basis_name}</b>이며 기준({savings.min_saved_mb:.1f} MB, {savings.min_mb_per_min:.1f} MB/분)과 "
        f"비교하면 {verdict}입니다."
    )
    return "".join(
        (
            "<h3>4. 분당 절약(MB/분) — 재생 시간 기준과 예상 인코딩 시간 기준</h3>",
            callout_html(
                p,
                "<b>쉽게 말하면</b> 분당 절약은 <b>작업 1분당 얻는 용량</b>입니다. 예를 들어 작업에 1분을 들였더니 "
                "용량이 5 MB 줄었다면 분당 절약은 5 MB/분입니다. 재인코딩에는 시간이 드니, 시간에 비해 얻는 용량이 "
                "적은 영상은 건너뜁니다. 작업 시간을 영상 길이로 잴지, 실제 인코딩 시간으로 잴지가 두 기준의 차이이며 "
                "설정에서 고를 수 있습니다.",
            ),
            formula_html(
                p,
                (
                    (
                        "재생 시간 기준 = ",
                        fraction_html(
                            p,
                            "예상 절약량(MB)",
                            fraction_html(p, "영상 재생 길이(초)", "60"),
                        ),
                    ),
                    (
                        "예상 인코딩 시간 기준 = ",
                        fraction_html(
                            p,
                            "예상 절약량(MB)",
                            fraction_html(p, "예상 인코딩 시간(초)", "60"),
                        ),
                    ),
                    (
                        "이론 절약 잠재량(S<sub>pot</sub>) = 현재 용량 × (1 − ",
                        fraction_html(p, "1", "r<sub>출력</sub>"),
                        ")",
                    ),
                ),
            ),
            "<ul>",
            "<li><b>재생 시간 기준</b>(기본값): 작업 시간을 <b>영상 길이</b>로 봅니다. 인코딩 시간은 대체로 영상 길이에 "
            "비례해 늘어나므로 길이를 작업량의 기준으로 씁니다. 예를 들어 10분짜리 영상에서 50 MB가 줄어들면 "
            "분당 절약은 <code>+5.0 MB/분</code>입니다. 예외 허용 규칙도 이 기준에서만 적용합니다.</li>",
            "<li><b>예상 인코딩 시간 기준</b>: 작업 시간을 <b>예상 인코딩 시간</b>(해상도와 인코더 속도 반영)으로 봅니다. "
            "같은 영상이라도 인코딩이 오래 걸리면 분당 절약이 낮아지므로, 고해상도나 느린 인코더를 쓸수록 기준이 더 엄격하게 작동합니다.</li>",
            f"<li><b>절약 미달 제외</b>: 키프레임이 정상인 과화질 영상이라도 <code>S<sub>pot</sub> &lt; {savings.min_saved_mb:.1f} MB</code>이면 "
            "절약 효과가 작아 원본을 보존합니다.</li>",
            f"<li><b>분당 미달 제외</b>: 선택한 기준의 분당 절약이 <code>{savings.min_mb_per_min:.1f} MB/분</code> 미만이면 "
            f"절약이 적어 자동으로 건너뜁니다. ({exempt_line})</li>",
            "<li><b>키프레임 간격 초과 영상은 예외</b>: 구간 이동이 버벅이는 영상은 절약량과 무관하게 "
            "재인코딩이 필요하므로 이 선별을 거치지 않고 대상에 포함됩니다.</li>",
            "</ul>",
            card_html(p, example),
        )
    )


def _time_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    enc = cfg.encode
    codec_label = html.escape(get_encode_codec_label(enc.encode_codec))
    work = _workload_ratio(_EXAMPLE_WIDTH, _EXAMPLE_HEIGHT, _EXAMPLE_FPS)
    speed = max(enc.encode_speed_factor, 0.01)
    minutes = _EXAMPLE_DURATION_MIN * work / speed
    example = (
        f"<b>계산 예시</b>: {_EXAMPLE_WIDTH}×{_EXAMPLE_HEIGHT}, {_EXAMPLE_FPS:g}fps, {_EXAMPLE_DURATION_MIN:g}분 영상<br/>"
        f"W<sub>ratio</sub> = {work:.2f}, 예상 시간 = {_EXAMPLE_DURATION_MIN:g}분 × {work:.2f} ÷ {speed:.1f} "
        f"= <b>{minutes:,.1f}분</b>"
    )
    return "".join(
        (
            "<h3>5. 예상 시간 — 무엇에 대한 시간인가요?</h3>",
            callout_html(
                p,
                "<b>예상 시간은 폴더 분석 시간이 아니라, 실제 재인코딩을 실행할 때 걸리는 예상 시간입니다.</b>",
            ),
            formula_html(
                p,
                (
                    (
                        "상대 작업량 배수(W<sub>ratio</sub>) = ",
                        fraction_html(
                            p,
                            "너비 × 높이 × fps",
                            f"{REFERENCE_ENCODE_WIDTH} × {REFERENCE_ENCODE_HEIGHT} × {REFERENCE_ENCODE_FPS:g}",
                        ),
                    ),
                    (
                        "예상 시간(초) = ",
                        fraction_html(
                            p,
                            "영상 재생 길이(초) × W<sub>ratio</sub> × D<sub>원본</sub> × P<sub>목표</sub>",
                            f"기준 배속({enc.encode_speed_factor:.1f}배속)",
                        ),
                    ),
                ),
            ),
            "<ul>",
            "<li><b>해석</b>: <code>1080p 30fps</code>를 작업량 <code>1.0배</code>로 두고, 화소 처리량(너비 × 높이 × fps)에 "
            "비례해 작업량이 늘어납니다. 이 값을 설정된 "
            f"<b>목표 코덱(<code>{codec_label}</code>) 기준 배속</b>(<code>{enc.encode_speed_factor:.1f}배속</code>)으로 "
            "나누어 절약 선별용 기준 시간을 산출합니다. 화면에 표시되는 예상 시간은 여기에 원본 디코딩 부하 계수"
            "(<code>D<sub>원본</sub></code>: AV1 <code>1.15</code>, ProRes/DNxHD <code>1.10</code> 등)를 곱하고, "
            f"저화질 프리셋 재인코딩이면 경량 프리셋 계수(<code>P<sub>목표</sub> = {LOW_QUALITY_PRESET_SPEED_MULTIPLIER:.2f}</code>)도 곱합니다. "
            "이 두 계수는 미실측 추정치라 절약 선별에는 쓰지 않습니다. <b>배속</b>은 영상 길이 대비 인코딩이 얼마나 빠른지 나타내며, "
            f"예컨대 <code>2.0배속</code>이면 1080p 10분 H.264 영상 재인코딩에 약 5분이 걸립니다.</li>",
            "<li><b>집계 기준</b>: 상단 <b>재인코딩 작업 분량</b> 카드의 예상 시간은 <b>실제 재인코딩 대상 파일만</b> 합산합니다. "
            "테이블의 원본 보존 행에 회색으로 표시되는 시간은 &quot;이 파일을 재인코딩한다고 가정한&quot; 참고 시간이며, "
            "행을 선택했을 때 하단 푸터 툴팁에서 <b>실제 재인코딩 대상 기준 예상 시간</b>도 별도로 확인할 수 있습니다.</li>",
            "<li><b>어디까지나 어림값</b>: 컴퓨터 성능, 인코딩 설정에 따라 실제 시간은 달라집니다. "
            "설정의 배속 값을 본인 환경에서 실제로 걸린 시간에 맞춰 두면 정확도가 올라갑니다. "
            "고해상도에서는 화소 비례 식보다 실제 시간이 더 늘어날 수 있습니다.</li>",
            "</ul>",
            card_html(p, example),
        )
    )


def _order_html(cfg: ClassifierConfig) -> str:
    std_cq = format_cq_value(cfg.encode.baseline_cq)
    protect_cq = format_cq_value(protect_cq_value(cfg.encode.baseline_cq))
    return (
        "<h3>6. 한 영상이 판정되는 순서</h3>"
        "<ol>"
        "<li><b>예외 규칙</b>에 일치하면 즉시 원본 보존 (<b>예외 규칙</b>)</li>"
        "<li>이미 재인코딩된 영상은 원본 보존. 출력 해상도를 직접 지정한 경우에만 아래 순서로 다시 판정 (<b>이미 재인코딩</b>)</li>"
        "<li><b>HDR</b>(PQ, HLG, Dolby Vision) 영상은 색 정보 보호를 위해 원본 보존 (<b>HDR 보존</b>)</li>"
        "<li><b>구형 코덱 호환 재인코딩</b> 옵션이 켜져 있고 하드웨어 디코딩 미지원 코덱이면 절약 선별 없이 재인코딩 (<b>코덱 호환</b>)</li>"
        f"<li><b>인트라 전용 코덱</b>(<code>C<sub>원본</sub> ≥ {INTRA_CODEC_MIN_WEIGHT:.1f}</code>, MJPEG, ProRes, DNxHD): "
        "화질 등급과 무관하게 절약 선별을 거쳐 재인코딩 (<b>코덱 전환</b>), 기준 미달이면 원본 보존 (<b>전환 절약 미달</b>)</li>"
        "<li><b>과화질</b>(출력 BR 비율이 유효 상한 초과): 키프레임 초과면 즉시, 정상이면 절약 선별을 통과해야 재인코딩 (<b>과화질</b>)</li>"
        "<li><b>적정</b>: 키프레임 초과면 재인코딩(<b>KF 초과</b>), 정상이더라도 "
        f"<code>C<sub>원본</sub> ÷ C<sub>출력</sub> ≥ {CODEC_UPGRADE_MIN_GAIN_RATIO:.2f}</code>이면 절약 선별을 거쳐 코덱 전환(<b>코덱 전환</b>, 기준 미달이면 <b>전환 절약 미달</b>로 보존), "
        "아니면 원본 보존(<b>적정</b>)</li>"
        "<li><b>저화질</b>(원본 BR 비율이 하한 미만): 키프레임 간격이 초과면 재인코딩(<b>KF 초과</b>), 정상이면 원본 보존(<b>화질 보호</b>)</li>"
        "</ol>"
        f"<p>재인코딩 대상은 저화질 구간이거나, 블록 노이즈 증폭 위험 구형 코덱(<code>C<sub>원본</sub> ≥ {LEGACY_BLOCK_NOISE_MIN_WEIGHT:.2f}</code> "
        f"및 <code>r<sub>원본</sub> &lt; {LEGACY_BLOCK_NOISE_MAX_RATIO:.2f}</code>), 코덱 역전(<code>C<sub>원본</sub> &lt; C<sub>출력</sub></code> 및 "
        f"<code>r<sub>출력</sub> &lt; 1.0</code>), 초저비트레이트 정적 화면(<code>r<sub>원본</sub> &lt; {STATIC_SCREEN_RATIO_THRESHOLD:.2f}</code>)이면 "
        f"<b>저화질 프리셋</b>(CQ{protect_cq})으로, 그 외에는 <b>CQ{std_cq}</b>으로 판정합니다.</p>"
    )


def _downscale_html() -> str:
    return (
        "<h3>7. 최대 해상도 축소 재인코딩 판정</h3>"
        "<p>전체 설정 <b>최대 해상도</b>를 넘는 영상은 출력 해상도로 줄여 재인코딩합니다. 판정 순서는 다음과 같습니다.</p>"
        "<ol>"
        "<li>예외 규칙과 HDR에 해당하면 축소를 검토하지 않고 원본 보존</li>"
        "<li>축소 후보는 최대 해상도 상한 또는 비트레이트 맞춤 규격이며, 줄였을 때 화소 수가 원본의 95% 이하여야 축소 대상</li>"
        "<li>축소 기준 절약이 최소 기준에 못 미치면 원본 보존(키프레임 초과, 구형 코덱 호환은 예외)</li>"
        "<li>축소 후에도 저화질, 블록 노이즈, 코덱 역전, 정적 화면에 해당하면 축소하지 않고 원본 해상도 기준으로 판정</li>"
        "</ol>"
        "<p><b>최대 비트레이트 대가</b>: 축소 영상에는 최대 비트레이트(<code>-maxrate</code>)가 적용되어 "
        "움직임이 많은 장면은 상한에 걸려 화질이 낮아질 수 있습니다. 여유 배수가 경고 기준 미만이면 "
        "출력 해상도 셀에 <b>최대 BR 근접</b>으로 표시합니다.</p>"
        "<p><b>직접 지정 우선</b>: 영상별 출력 해상도를 직접 고르면 최대 해상도와 비트레이트 맞춤보다 우선합니다. "
        "<b>축소 안 함</b>을 고르면 상한도 적용하지 않습니다.</p>"
        "<p><b>비트레이트 맞춤 축소</b>(기본 꺼짐): 원본이 저화질이면 원본보다 낮은 규격을 높은 순으로 살펴 "
        "현재 비트레이트가 적정해지는 첫 규격으로 줄입니다. 360p까지 맞지 않으면 줄이지 않습니다. "
        "최대 해상도와 함께 켜져 있으면 두 규격 중 낮은 쪽을 적용하고, 같은 규격이면 <b>최대 해상도 초과</b>로 표시합니다.</p>"
        "<p><b>비트레이트 맞춤 위험</b>: 화면 녹화, 애니메이션처럼 비트레이트가 낮아도 세부가 중요한 영상은 글자가 뭉개질 수 있습니다. "
        "필요한 경우에만 켜고, 예외 규칙으로 특정 영상을 제외해 주세요.</p>"
    )


def build_concepts_html(
    p: ThemePalette,
    cfg: ClassifierConfig,
    shortcuts: Mapping[str, str] | None = None,
) -> str:
    """판정 원리 탭, 여섯 단계 수식 해설과 축소 판정 절에 쉬운 요약과 현재 설정값 계산 예시 결합"""
    effective = DEFAULT_SHORTCUTS if shortcuts is None else shortcuts
    return "".join(
        (
            render_styles(p),
            _outline_html(),
            _optimal_bitrate_html(p, cfg),
            _ratio_html(p, cfg, effective),
            _size_html(p, cfg),
            _savings_html(p, cfg),
            _time_html(p, cfg),
            _order_html(cfg),
            _downscale_html(),
        )
    )
