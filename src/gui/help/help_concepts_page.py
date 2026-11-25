"""사용 가이드의 판정 원리 탭, 판별 수식 해설과 현재 설정값 기반 계산 예시 HTML 조립 모듈"""

from __future__ import annotations

import html
from collections.abc import Mapping

from src.core.analysis.frame_analysis import (
    EDGE_SNAP_MIN_PX,
    EDGE_SNAP_RATIO,
    SSIM_CONFIRMED_FAIL_OFFSET,
    SSIM_CONFIRMED_PASS_OFFSET,
    VMAF_CONFIRMED_OFFSET,
    VMAF_SUBSAMPLE,
    VR_ASPECT_RATIO_MAX,
    VR_ASPECT_RATIO_MIN,
    VR_MIN_LONG_SIDE,
)
from src.core.analysis.frame_scan import (
    BLACK_EDGE_MEAN_THRESHOLD,
    BLACK_FRAME_PIXEL_RATIO,
    BLACK_LUMA_THRESHOLD,
)
from src.core.config import (
    SAVINGS_BASIS_ENCODE,
    BitrateModelConfig,
    ClassifierConfig,
)
from src.core.encode.encode_gate import (
    DURATION_TOLERANCE_MIN_SEC,
    DURATION_TOLERANCE_RATIO,
)
from src.core.encode.precise_tune_policy import PRECISE_TUNE_BOUNDARY_MARGIN
from src.core.encode.quality_tuning import SAMPLE_TARGET_TOLERANCE, TUNE_RETRY_MIN_DELTA_Q
from src.core.encoder_specs import get_encode_codec_family, get_encode_codec_label
from src.core.evaluation.classification_policy import (
    INTRA_CODEC_MIN_WEIGHT,
    LEGACY_BLOCK_NOISE_MAX_RATIO,
    LEGACY_BLOCK_NOISE_MIN_WEIGHT,
)
from src.core.evaluation.evaluator import LOW_QUALITY_PRESET_SPEED_MULTIPLIER, VideoEvaluator
from src.core.evaluation.quality_metrics import (
    FPS_HYSTERESIS_TOLERANCE,
    MIN_OPTIMAL_BITRATE_KBPS,
    REFERENCE_ENCODE_FPS,
    REFERENCE_ENCODE_HEIGHT,
    REFERENCE_ENCODE_WIDTH,
    calc_optimal_bitrate,
)
from src.core.evaluation.size_estimator import (
    DEFAULT_UNKNOWN_KF_SEC,
    FPS_KF_CORRECTION_EXPONENT,
    FPS_KF_FACTOR_MAX,
    FPS_KF_FACTOR_MIN,
    KF_CLAMP_MAX_SEC,
    KF_CLAMP_MIN_SEC,
    STATIC_SCREEN_RATIO_FLOOR,
    STATIC_SCREEN_RATIO_THRESHOLD,
    calc_keyframe_increase_ratio,
    estimate_after_size_mb,
    estimate_video_stream_mb,
)
from src.core.messages import format_over_bitrate_desc
from src.core.models import BYTES_PER_MB, VideoProps
from src.core.probe_parser import BITS_PER_BYTE, BITS_PER_KBIT
from src.core.quality_target import (
    ENCODER_QUALITY_BITRATE_K,
    ENCODER_SIZE_FACTORS,
    MAXRATE_HEADROOM_MULTIPLIER,
    MAXRATE_ROUND_KBPS,
    PROTECT_CQ_DELTA_MAX,
    QUALITY_BITRATE_K,
    ROUNDTRIP_SSIM_THRESHOLD,
    ROUNDTRIP_VMAF_THRESHOLD,
    calc_base_bitrate_kbps,
    effective_quality_bitrate_k,
    encoder_size_factor,
    format_cq_value,
    protect_cq_value,
    quality_size_factor,
)
from src.core.settings.shortcuts import DEFAULT_SHORTCUTS
from src.gui.help.help_blocks import (
    GAP_HTML,
    callout_html,
    card_html,
    formula_html,
    fraction_html,
    inline_cmd,
    note_html,
    render_styles,
    striped_table_html,
)
from src.gui.styles.palettes import ThemePalette

_EXAMPLE_WIDTH: int = 3840
_EXAMPLE_HEIGHT: int = 2160
_EXAMPLE_FPS: float = 60.0
_EXAMPLE_BITRATE_MULTIPLE: float = 3.0
_EXAMPLE_AUD_KBPS: int = 192
_EXAMPLE_DURATION_MIN: float = 10.0
_EXAMPLE_FIXED_MB: float = 60.0
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
        "실제 영상과 비교해 인코딩 여부를 정합니다. 아래 아홉 단계 순서로 읽으면 흐름이 이어집니다.</p>"
        "<ol>"
        "<li><b>최적 비트레이트</b>: 원본 코덱과 출력 코덱 각각의 충분한 비트레이트 기준선 계산</li>"
        "<li><b>BR 비율</b>: 실제 영상을 두 기준선과 비교해 화질 3구간으로 판정</li>"
        "<li><b>예상 용량과 절약</b>: 인코딩 후 용량 예측</li>"
        "<li><b>절약 선별</b>: 작업 시간 1분당 얻는 용량 절약으로 인코딩할 가치 판단</li>"
        "<li><b>예상 시간</b>: 인코딩에 걸리는 시간 어림</li>"
        "<li><b>판정 순서</b>: 예외, HDR, 화질 구간의 우선순위</li>"
        "<li><b>최대 해상도 축소 인코딩 판정</b>: 상한을 넘는 영상을 줄이는 조건</li>"
        "<li><b>화질 분석과 검은 여백 감지 원리</b>: 검은 여백과 축소 후보를 재는 원리</li>"
        "<li><b>내부 고정 기준값 안내</b>: 프로그램이 실제로 쓰는 고정 상수</li>"
        "</ol>"
        "<p>수식이 어렵게 느껴지면 각 절의 <b>쉽게 말하면</b> 상자와 <b>계산 예시</b>만 읽어도 충분합니다. "
        "표시된 숫자는 모두 현재 설정값을 반영하며, 설정을 바꾸면 이 화면도 함께 바뀝니다.</p>"
    )


def example_video(cfg: ClassifierConfig) -> VideoProps:
    """판정 원리 계산 예시에 쓰는 표본 영상(4K 60fps H.264, 원본 최적 BR의 3배, 고정 60 MB, 10분, 8초 키프레임)"""
    source_optimal = calc_optimal_bitrate(
        _EXAMPLE_WIDTH,
        _EXAMPLE_HEIGHT,
        cfg.bitrate_model,
        cfg.codec_weights,
        _EXAMPLE_FPS,
        "h264",
        cfg.encode.baseline_cq,
    )
    vid_kbps = round(_EXAMPLE_BITRATE_MULTIPLE * source_optimal)
    duration_sec = _EXAMPLE_DURATION_MIN * 60.0
    stream_mb = vid_kbps * BITS_PER_KBIT * duration_sec / BITS_PER_BYTE / BYTES_PER_MB
    return VideoProps(
        filename="example.mp4",
        width=_EXAMPLE_WIDTH,
        height=_EXAMPLE_HEIGHT,
        rotate_type=0,
        fps=_EXAMPLE_FPS,
        vid_kbps=vid_kbps,
        aud_kbps=_EXAMPLE_AUD_KBPS,
        vid_size_MB=stream_mb + _EXAMPLE_FIXED_MB,
        duration=duration_sec,
        codec="h264",
        keyframe_interval=_EXAMPLE_KEYFRAME_SEC,
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
    base_kbps = calc_base_bitrate_kbps(
        bm.base_target_kbps, cfg.encode.baseline_cq, encoder=codec
    )
    optimal = calc_optimal_bitrate(
        _EXAMPLE_WIDTH,
        _EXAMPLE_HEIGHT,
        bm,
        cw,
        _EXAMPLE_FPS,
        get_encode_codec_family(codec),
        cfg.encode.baseline_cq,
        encoder=codec,
    )
    example = (
        f"<b>계산 예시</b>: {_EXAMPLE_WIDTH}×{_EXAMPLE_HEIGHT}, {_EXAMPLE_FPS:g}fps 영상을 {label}로 인코딩할 때<br/>"
        f"K<sub>면적</sub> = {area:.2f}, K<sub>fps</sub> = {fps:.2f}, C<sub>코덱</sub> = {out_weight:.2f}, "
        f"K<sub>유효</sub> = {effective_quality_bitrate_k(codec):.2f}<br/>"
        f"B<sub>opt</sub> = {round(base_kbps):,} × {area:.2f} × {fps:.2f} × {out_weight:.2f} "
        f"≈ <b>{optimal:,} kbps</b>"
    )
    return "".join(
        (
            "<a name=\"optimal-bitrate\"></a><h3>1. 최적 비트레이트(최적 BR) 계산식</h3>",
            callout_html(
                p,
                "<b>쉽게 말하면</b> 화면이 크고, 프레임이 많고, 오래된 코덱일수록 같은 화질을 위해 "
                "더 많은 데이터가 필요합니다. 그 &quot;충분한 양&quot;을 영상마다 계산한 값이 최적 BR입니다.",
            ),
            "<p><b>화질 기준값</b>(기본 22.0)은 육안으로 원본과 차이를 거의 느끼기 어려운 고정 화질 목표입니다. "
            "다른 프로그램의 CQ(NVENC), CRF(x264, x265)에 해당합니다. 낮을수록 고화질이며, 같은 값이어도 인코더에 따라 결과 크기와 화질이 다릅니다. "
            "같은 화질이어도 해상도, FPS, 코덱 압축 성능에 따라 필요한 비트레이트가 달라지므로, "
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
            f"<li><b>해상도 지수 α ({bm.area_scaling_exponent:.2f})</b>: 화면 면적이 4배(예: 1080p에서 4K) 늘어도 "
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
            "<a name=\"ratio\"></a><h3>2. BR 비율과 화질 3구간 판정</h3>",
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
            "원본 코덱 기준으로 판단하며, 여기서 더 압축하면 눈에 띄게 나빠질 수 있어 인코딩으로부터 보호합니다. "
            "이 조건이 가장 먼저 적용됩니다.</li>",
            f"<li><b>과화질</b> (<code>r<sub>출력</sub> &gt; 유효 상한</code>): 저화질이 아니면서 "
            f"목표 코덱({label}) 기준으로도 필요 이상으로 비트레이트가 높습니다. "
            f"기본 상한은 <code>{t.over_bitrate_ratio:.2f}배</code>(예: {over_example})이며, "
            f"원본이 목표 코덱 이상 성능(<code>C<sub>원본</sub> ≤ C<sub>출력</sub></code>)이면 "
            f"세대 손실 방어를 위해 <code>+{t.same_or_better_codec_over_margin:.2f}</code>, "
            f"비-HDR 10-bit 영상이면 컬러 밴딩 방어를 위해 <code>+{t.ten_bit_sdr_over_margin:.2f}</code>를 더합니다.</li>",
            "<li><b>적정</b>: 위 두 조건에 해당하지 않는 나머지입니다. 키프레임이 정상이면 보통 보존하지만, "
            f"원본이 구형 코덱이어서 목표 코덱 대비 <code>{t.codec_upgrade_min_gain_ratio:.2f}배</code> 이상 "
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
    codec = cfg.encode.encode_codec
    family = get_encode_codec_family(codec)
    vid = example_video(cfg)
    evaluation = VideoEvaluator.evaluate_video(vid, cfg)
    stream_mb = estimate_video_stream_mb(vid)
    other_mb = vid.vid_size_MB - stream_mb
    source_ratio = vid.vid_kbps / evaluation.optimal_kbps
    ratio = evaluation.target_bitrate_ratio
    source_weight = cfg.get_codec_weight(vid.codec)
    codec_weight_ratio = cfg.get_codec_weight(family) / source_weight
    output_cq = (
        evaluation.output_cq
        if evaluation.output_cq is not None
        else cfg.encode.baseline_cq
    )
    output_delta = output_cq - cfg.encode.baseline_cq
    size_factor = encoder_size_factor(codec)
    quality_factor = quality_size_factor(output_delta, encoder=codec)
    kf_ratio = calc_keyframe_increase_ratio(
        vid.keyframe_interval,
        rate,
        fps=vid.fps,
        base_fps=cfg.bitrate_model.base_fps,
        source_ratio=source_ratio,
    )
    plain_mb = estimate_after_size_mb(
        vid,
        ratio,
        rate,
        cap_at_original=True,
        source_ratio=source_ratio,
        codec_weight_ratio=codec_weight_ratio,
        base_fps=cfg.bitrate_model.base_fps,
        output_delta_cq=output_delta,
        encoder=codec,
    )
    kf_target_mb = other_mb + stream_mb * (1.0 + kf_ratio)
    over_example = (
        f"<b>계산 예시 (판정표 예상 용량)</b>: {vid.vid_size_MB:,.0f} MB 영상(영상 스트림 {stream_mb:,.1f} MB, "
        f"나머지 {other_mb:,.1f} MB), 키프레임 간격 {vid.keyframe_interval:g}초<br/>"
        f"r<sub>출력</sub> = <b>{ratio:.3f}배</b>, E = {size_factor:.2f}, "
        f"품질 계수 = {quality_factor:.4f}(출력 품질값 {format_cq_value(output_cq)}), "
        f"증가율 = {kf_ratio:.4f}<br/>"
        f"factor<sub>br</sub> = min(E, E ÷ r<sub>출력</sub>, C<sub>출력</sub>/C<sub>원본</sub> × E)<br/>"
        f"상한 전 = {other_mb:,.1f} + {stream_mb:,.1f} × factor<sub>br</sub> × (1 + {kf_ratio:.4f}) × "
        f"{quality_factor:.4f} = {plain_mb:,.1f} MB<br/>"
        f"키프레임 목표 = {other_mb:,.1f} + {stream_mb:,.1f} × (1 + {kf_ratio:.4f}) = {kf_target_mb:,.1f} MB, "
        f"원본 상한 = {vid.vid_size_MB:,.1f} MB<br/>"
        f"판정 예상 용량 = 위 셋 중 가장 작은 값 = <b>{evaluation.expected_after_mb:,.1f} MB</b>, "
        f"절약 ΔS = <b>{evaluation.expected_delta_mb:,.1f} MB</b>"
    )
    return "".join(
        (
            "<a name=\"size\"></a><h3>3. 예상 용량, 예상 절약, 키프레임 인코딩 용량 증가율</h3>",
            callout_html(
                p,
                "<b>쉽게 말하면</b> 인코딩하면 용량이 얼마나 줄지 미리 어림해 보는 단계입니다. "
                "오디오처럼 바뀌지 않는 부분은 그대로 두고, 영상 부분만 줄이거나 늘립니다.",
            ),
            formula_html(
                p,
                (
                    (note_html(p, "영상 스트림과 고정 부분 나누기"),),
                    ("영상 스트림 용량 = min( 현재 용량, 비트레이트 × 길이 )",),
                    ("고정 용량(오디오 등) = 현재 용량 − 영상 스트림 용량",),
                    (note_html(p, "예상 용량 계산 (실제 인코딩과 원본 보존 행의 가상 값 모두 같은 식)"),),
                    (
                        "예상 용량 = 고정 용량 + 영상 스트림 용량 × factor<sub>br</sub> × (1 + 증가율) × 품질 계수",
                    ),
                    (
                        "factor<sub>br</sub> = min( E, ",
                        fraction_html(p, "E", "r<sub>출력</sub>"),
                        ", ",
                        fraction_html(p, "C<sub>출력</sub>", "C<sub>원본</sub>"),
                        " × E )",
                        note_html(
                            p,
                            "E는 인코더 크기 보정 계수. C<sub>원본</sub> &lt; C<sub>출력</sub>(코덱 역전)이고 "
                            "r<sub>출력</sub> &lt; 1이면 min(E ÷ r<sub>출력</sub>, C<sub>출력</sub>/C<sub>원본</sub> × E)",
                        ),
                    ),
                    (
                        "품질 계수 = 2<sup>−ΔQ ÷ K<sub>유효</sub></sup>",
                        note_html(p, "ΔQ는 출력 품질값 − 화질 기준값, 저화질 보호 프리셋이 아니면 0"),
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
                    (
                        note_html(
                            p,
                            "과화질 인코딩은 원본 용량을 넘지 않도록 상한 적용, "
                            "키프레임 초과 영상은 목표 크기를 넘지 않도록 한 번 더 상한 적용(결정 33)",
                        ),
                    ),
                    ("ΔS = 현재 용량 − 예상 용량",),
                    (note_html(p, "+: 절약, −: 키프레임 인코딩 또는 코덱 역전에 따른 증가"),),
                ),
            ),
            "<ul>",
            "<li><b>오디오는 바뀌지 않습니다</b>: 비디오 비트레이트 조절은 영상 스트림에만 적용하고, "
            "오디오와 컨테이너 용량은 그대로 둡니다. 스트림 비트레이트가 비어 있는 <code>pcm_*</code> 무손실 오디오는 "
            "채널 수 × 샘플레이트 × 샘플당 비트로 오디오 비트레이트를 역산 차감해 비디오 비중을 과대 추정하지 않습니다.</li>",
            f"<li><b>인코더 크기 보정</b>: 같은 압축 성능 기준을 써도 인코더마다 실제 출력이 달라 "
            f"현재 <b>{html.escape(get_encode_codec_label(codec))}</b>에 <code>{size_factor:.2f}배</code>를 곱합니다. "
            "이 값은 목표 비트레이트와 최대 비트레이트에도 함께 적용됩니다.</li>",
            "<li><b>코덱 전환과 코덱 역전 반영</b>: <code>r<sub>원본</sub> &lt; 1.0</code>인 구형 코덱도 "
            "<code>C<sub>출력</sub> ÷ C<sub>원본</sub></code> 압축 향상분을 반영해 예상 용량을 계산하며, "
            "반대로 <code>AV1</code>을 <code>H.264</code>로 바꾸는 코덱 역전이 일어나면 용량 증가를 그대로 반영합니다.</li>",
            "<li><b>품질값 차이 반영</b>: 저화질 보호 프리셋 영상은 기준 품질값보다 높은 품질값으로 인코딩하므로 "
            "<code>2<sup>−ΔQ ÷ K<sub>유효</sub></sup></code> 계수로 용량을 다시 낮춥니다. "
            "화질 기준값 그대로 인코딩하면 ΔQ가 0이라 계수도 1입니다.</li>",
            f"<li><b>키프레임 인코딩 증가(-)</b>: 간격이 <b>{t.keyframe_interval_sec:.1f}초</b>를 넘으면 "
            f"1초 키프레임을 넣으며 용량이 소폭 늘어납니다. 고FPS 영상은 GOP 비중이 낮아 "
            f"<code>({cfg.bitrate_model.base_fps:g}/fps)<sup>{FPS_KF_CORRECTION_EXPONENT:.1f}</sup></code>로 과대 예측을 보정하고, "
            f"<code>r<sub>원본</sub> &lt; {STATIC_SCREEN_RATIO_THRESHOLD:.2f}</code>인 초저비트레이트 정적 화면은 "
            "정적 화면 가중치를 반영합니다.</li>",
            "<li><b>간격 계산의 상하한</b>: 키프레임 간격은 계산할 때 "
            f"{KF_CLAMP_MIN_SEC:.1f}초 이상 {KF_CLAMP_MAX_SEC:.1f}초 이하로 맞춰 극단값이 결과를 왜곡하지 않게 합니다.</li>",
            "<li><b>순 절약량</b>: 상단 요약 카드는 과화질 인코딩 절약(<code>+</code>)에서 키프레임 인코딩 증가분(<code>-</code>)을 "
            "뺀 실제 순 절약 용량을 보여줍니다. (표의 회색 수치는 원본 보존 행을 인코딩한다고 가정한 참고값입니다.)</li>",
            "</ul>",
            card_html(p, over_example),
        )
    )


def _savings_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    savings = cfg.savings
    vid = example_video(cfg)
    evaluation = VideoEvaluator.evaluate_video(vid, cfg)
    duration_min = vid.duration / 60.0
    potential = vid.vid_size_MB - evaluation.expected_after_mb
    playback_per_min = potential / duration_min
    encode_min = evaluation.estimated_encode_sec / 60.0
    encode_per_min = potential / encode_min if encode_min > 0 else 0.0
    is_encode_basis = savings.selection_basis == SAVINGS_BASIS_ENCODE
    basis_name = "예상 인코딩 시간 기준" if is_encode_basis else "재생 시간 기준"
    selected_per_min = encode_per_min if is_encode_basis else playback_per_min
    passes = potential >= savings.min_saved_mb and selected_per_min >= savings.min_mb_per_min
    verdict = "두 기준을 모두 넘으므로 인코딩 대상" if passes else "기준에 못 미쳐 원본 보존"
    exempt_line = (
        f"재생 시간 기준에서는 총 예상 절약량이 <b>{savings.exempt_saved_mb:.0f} MB 이상</b>이면 분당 절약이 낮아도 예외적으로 포함합니다. "
        "예상 인코딩 시간 기준에서는 이 예외를 적용하지 않습니다."
        if savings.exempt_saved_mb > 0
        else "현재 대용량 절약 예외 허용은 비활성화(0 MB)되어 있습니다."
    )
    example = (
        f"<b>계산 예시</b>: 3절 예시 영상({vid.vid_size_MB:,.1f} MB, {duration_min:g}분 4K 60fps)의 "
        f"판정 예상 용량이 {evaluation.expected_after_mb:,.1f} MB이면<br/>"
        f"S<sub>pot</sub> = {vid.vid_size_MB:,.1f} − {evaluation.expected_after_mb:,.1f} = {potential:,.1f} MB<br/>"
        f"재생 시간 1분당 절약 = {potential:,.1f} ÷ {duration_min:g} = <b>{playback_per_min:,.1f} MB/분</b><br/>"
        f"예상 인코딩 시간 1분당 절약 = {potential:,.1f} ÷ {encode_min:,.1f}분 = <b>{encode_per_min:,.1f} MB/분</b> "
        f"(예상 인코딩 시간 {encode_min:,.1f}분)<br/>"
        f"현재 선택은 <b>{basis_name}</b>이며 기준({savings.min_saved_mb:.1f} MB, {savings.min_mb_per_min:.1f} MB/분)과 "
        f"비교하면 {verdict}입니다."
    )
    return "".join(
        (
            "<a name=\"savings\"></a><h3>4. 분당 절약(MB/분), 재생 시간 기준과 예상 인코딩 시간 기준</h3>",
            callout_html(
                p,
                "<b>쉽게 말하면</b> 분당 절약은 <b>작업 1분당 얻는 용량</b>입니다. 예를 들어 작업에 1분을 들였더니 "
                "용량이 5 MB 줄었다면 분당 절약은 5 MB/분입니다. 인코딩에는 시간이 드니, 시간에 비해 얻는 용량이 "
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
                        "이론 절약 잠재량(S<sub>pot</sub>) = 현재 용량 − 예상 용량(3절)",
                        note_html(p, "키프레임 인코딩 증가분과 인코더 크기 보정까지 반영한 값"),
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
            "인코딩이 필요하므로 이 선별을 거치지 않고 대상에 포함됩니다.</li>",
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
            "<a name=\"time\"></a><h3>5. 예상 시간, 무엇에 대한 시간인가요?</h3>",
            callout_html(
                p,
                "<b>예상 시간은 폴더 분석 시간이 아니라, 실제 인코딩을 실행할 때 걸리는 예상 시간입니다.</b>",
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
            f"저화질 프리셋 인코딩이면 경량 프리셋 계수(<code>P<sub>목표</sub> = {LOW_QUALITY_PRESET_SPEED_MULTIPLIER:.2f}</code>)도 곱합니다. "
            "이 두 계수는 미실측 추정치라 절약 선별에는 쓰지 않습니다. <b>배속</b>은 영상 길이 대비 인코딩이 얼마나 빠른지 나타내며, "
            f"예컨대 <code>2.0배속</code>이면 1080p 10분 H.264 영상 인코딩에 약 5분이 걸립니다.</li>",
            "<li><b>집계 기준</b>: 상단 <b>인코딩 작업 분량</b> 카드의 예상 시간은 <b>실제 인코딩 대상 파일만</b> 합산합니다. "
            "테이블의 원본 보존 행에 회색으로 표시되는 시간은 &quot;이 파일을 인코딩한다고 가정한&quot; 참고 시간이며, "
            "행을 선택했을 때 하단 푸터 툴팁에서 <b>실제 인코딩 대상 기준 예상 시간</b>도 별도로 확인할 수 있습니다.</li>",
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
    upgrade_gain = cfg.thresholds.codec_upgrade_min_gain_ratio
    codec = cfg.encode.encode_codec
    return (
        "<a name=\"order\"></a><h3>6. 한 영상이 판정되는 순서</h3>"
        "<ol>"
        "<li><b>예외 규칙</b>에 일치하면 즉시 원본 보존 (<b>예외 규칙</b>)</li>"
        "<li>이미 인코딩된 영상은 원본 보존. 출력 해상도를 직접 지정한 경우에만 아래 순서로 다시 판정 (<b>이미 인코딩</b>)</li>"
        "<li><b>HDR</b>(PQ, HLG, Dolby Vision) 영상은 색 정보 보호를 위해 원본 보존 (<b>HDR 보존</b>)</li>"
        f"<li><b>활성 화면</b>: 검은 여백을 뺀 활성 화면 크기로 기준 비트레이트를 다시 계산합니다. "
        "전역 잘라내기가 꺼져 있거나 VR 보호 영상이면 자르지 않고, 직접 지정한 활성 화면이 있으면 "
        "자동 감지보다 그 영역을 씁니다 (<b>5b 자동 부분 크롭</b>)</li>"
        f"<li><b>구형 코덱 호환 인코딩</b> 옵션이 켜져 있고 하드웨어 디코딩 미지원 코덱이면 절약 선별 없이 인코딩 (<b>코덱 호환</b>)</li>"
        f"<li><b>인트라 전용 코덱</b>(<code>C<sub>원본</sub> ≥ {INTRA_CODEC_MIN_WEIGHT:.1f}</code>, MJPEG, ProRes, DNxHD): "
        "화질 등급과 무관하게 절약 선별을 거쳐 인코딩 (<b>코덱 전환</b>), 기준 미달이면 원본 보존 (<b>전환 절약 미달</b>)</li>"
        "<li><b>과화질</b>(출력 BR 비율이 유효 상한 초과): 키프레임 초과면 즉시, 정상이면 절약 선별을 통과해야 인코딩 (<b>과화질</b>)</li>"
        "<li><b>적정</b>: 키프레임 초과면 인코딩(<b>KF 초과</b>), 정상이더라도 "
        f"<code>C<sub>원본</sub> ÷ C<sub>출력</sub> ≥ {upgrade_gain:.2f}</code>이면 절약 선별을 거쳐 코덱 전환(<b>코덱 전환</b>, 기준 미달이면 <b>전환 절약 미달</b>로 보존), "
        "아니면 원본 보존(<b>적정</b>)</li>"
        "<li><b>저화질</b>(원본 BR 비율이 하한 미만): 키프레임 간격이 초과면 인코딩(<b>KF 초과</b>), 정상이면 원본 보존(<b>화질 보호</b>)</li>"
        "</ol>"
        f"<p>인코딩 대상은 저화질 구간이거나, 블록 노이즈 증폭 위험 구형 코덱(<code>C<sub>원본</sub> ≥ {LEGACY_BLOCK_NOISE_MIN_WEIGHT:.2f}</code> "
        f"및 <code>r<sub>원본</sub> &lt; {LEGACY_BLOCK_NOISE_MAX_RATIO:.2f}</code>), 코덱 역전(<code>C<sub>원본</sub> &lt; C<sub>출력</sub></code> 및 "
        f"<code>r<sub>출력</sub> &lt; 1.0</code>), 초저비트레이트 정적 화면(<code>r<sub>원본</sub> &lt; {STATIC_SCREEN_RATIO_THRESHOLD:.2f}</code>)이면 "
        f"<b>저화질 프리셋</b>으로, 그 외에는 <b>품질 {std_cq}</b>으로 판정합니다.</p>"
        f"<p><b>보호 품질값 연속 규칙</b>: 저화질 프리셋은 품질 {std_cq}에 고정 오프셋을 더하지 않고, "
        f"비트레이트 비율 r(현재 비트레이트 ÷ 보정 목표 비트레이트)에 따라 "
        f"<code>품질 = {std_cq} + clamp(K<sub>유효</sub> × log<sub>2</sub>(1/r), 0, "
        f"{format_cq_value(PROTECT_CQ_DELTA_MAX)})</code>로 정합니다. "
        f"목표에 가까울수록 올리는 폭이 작아지고, 상한은 {protect_cq}입니다. "
        f"현재 <b>{html.escape(get_encode_codec_label(codec))}</b>의 <code>K<sub>유효</sub></code>는 "
        f"<code>{effective_quality_bitrate_k(codec):.2f}</code>입니다.</p>"
        f"<p><b>최대 비트레이트</b>: 축소 인코딩과 일반 인코딩 모두 "
        f"<code>-maxrate = min(규격 상한, {MAXRATE_ROUND_KBPS}k 단위 올림(보정 목표 × {MAXRATE_HEADROOM_MULTIPLIER:.1f}))</code>를 씁니다. "
        f"보정 목표는 <code>목표 비트레이트 × 인코더 크기 보정 {encoder_size_factor(codec):.2f}배</code>입니다.</p>"
        f"<p><b>품질값 자동 조정</b>: 절약 목적 사전 예측에서 대표 구간 샘플의 예상 비트레이트가 "
        f"보정 목표의 <code>{SAMPLE_TARGET_TOLERANCE:.2f}배</code>를 넘으면, 샘플을 더 돌리지 않고 "
        f"계산만으로 품질값을 올립니다. 올린 폭이 {TUNE_RETRY_MIN_DELTA_Q:.1f} 미만이면 편차로 보고 그대로 두며, "
        f"상한은 품질 {protect_cq}입니다.</p>"
        f"<p><b>품질값 정밀 결정</b>(6a): 설정의 <code>품질값 정밀 결정</code>이 켜져 있으면 본 인코딩 전에 "
        f"대표 구간 몇 곳을 짧게 인코딩해 화질 기준(VMAF {ROUNDTRIP_VMAF_THRESHOLD:.1f}점, "
        f"SSIM {ROUNDTRIP_SSIM_THRESHOLD:.3f})을 지키는 가장 높은 품질값을 이진 탐색으로 찾습니다. "
        f"<code>선택 적용 (기본)</code>은 절약 목적 인코딩 대상 중 축소 후보이거나 비트레이트 비율이 "
        f"과화질/저화질 경계에서 {PRECISE_TUNE_BOUNDARY_MARGIN:.2f}배 안쪽인 영상만, "
        f"<code>모든 대상</code>은 모든 절약 목적 대상, <code>사용 안 함</code>은 건너뜁니다. "
        f"결정 33의 1초 키프레임 인코딩과 코덱 호환은 절약 목적이 아니므로 대상이 아닙니다. "
        f"원본(크롭) 해상도와 축소 해상도를 비교하는 축소 비교는 아직 판정에 연결하지 않았습니다.</p>"
    )


def _downscale_html() -> str:
    return (
        "<a name=\"downscale\"></a><h3>7. 최대 해상도 축소 인코딩 판정</h3>"
        "<p>전체 설정 <b>최대 해상도</b>를 넘는 영상은 출력 해상도로 줄여 인코딩합니다. 판정 순서는 다음과 같습니다.</p>"
        "<ol>"
        "<li>예외 규칙과 HDR에 해당하면 축소를 검토하지 않고 원본 보존</li>"
        "<li>축소 후보는 <b>직접 지정</b>, <b>화질 분석 축소 후보</b>, 최대 해상도 상한, 비트레이트 맞춤 규격 중에서 고릅니다. "
        "우선순위는 사용자가 직접 지정한 규격이 가장 높고, 그다음이 화질 분석 축소 후보(설정이 켜져 있고 분석 결과가 있을 때)이며, "
        "최대 해상도와 비트레이트 맞춤은 남은 후보입니다. 후보가 여러 개면 출력 화소 수가 가장 작은 것을 쓰고, "
        "같은 크기면 최대 해상도, 화질 분석 축소, 비트레이트 맞춤 순으로 사유를 정합니다. "
        "축소 안 함을 직접 고르면 어떤 후보도 적용하지 않습니다.</li>"
        "<li>줄였을 때 화소 수가 원본의 95% 이하여야 축소 대상입니다. 상한을 살짝 넘는 정도면 사유만 남기고 원본을 유지합니다.</li>"
        "<li>축소 기준 절약이 최소 기준에 못 미치면 원본 보존(키프레임 초과, 구형 코덱 호환은 예외)</li>"
        "<li>축소 후에도 저화질, 블록 노이즈, 코덱 역전, 정적 화면에 해당하면 축소하지 않고 원본 해상도 기준으로 판정</li>"
        "</ol>"
        "<p><b>최대 비트레이트 대가</b>: 축소 영상에는 최대 비트레이트(<code>-maxrate</code>)가 적용되어 "
        "움직임이 많은 장면은 상한에 걸려 화질이 낮아질 수 있습니다. 이 상한은 "
        "<code>min(규격 상한, 100k 단위 올림(보정 목표 비트레이트 × 1.5))</code>이며, "
        "보정 목표는 목표 비트레이트에 인코더 크기 보정을 곱한 값입니다. "
        "여유 배수가 경고 기준 미만이면 출력 해상도 셀에 <b>최대 BR 근접</b>으로 표시합니다.</p>"
        "<p><b>직접 지정 우선</b>: 영상별 출력 해상도를 직접 고르면 최대 해상도와 비트레이트 맞춤보다 우선합니다. "
        "<b>축소 안 함</b>을 고르면 상한도 적용하지 않습니다.</p>"
        "<p><b>비트레이트 맞춤 축소</b>(기본 꺼짐): 원본이 저화질이면 원본보다 낮은 규격을 높은 순으로 살펴 "
        "현재 비트레이트가 적정해지는 첫 규격으로 줄입니다. 360p까지 맞지 않으면 줄이지 않습니다. "
        "최대 해상도와 함께 켜져 있으면 두 규격 중 낮은 쪽을 적용하고, 같은 규격이면 <b>최대 해상도 초과</b>로 표시합니다.</p>"
        "<p><b>비트레이트 맞춤 위험</b>: 화면 녹화, 애니메이션처럼 비트레이트가 낮아도 세부가 중요한 영상은 글자가 뭉개질 수 있습니다. "
        "필요한 경우에만 켜고, 예외 규칙으로 특정 영상을 제외해 주세요.</p>"
        "<p><b>품질값 정밀 결정</b>: 6절의 정밀 결정은 본 인코딩 전에 확정 품질값만 정합니다. "
        "원본(크롭) 해상도 구성과 축소 해상도 구성을 직접 비교해 축소 여부를 뒤집는 축소 비교 판정은 아직 연결하지 않았습니다.</p>"
    )


def _frame_analysis_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    enc = cfg.encode
    rows = (
        (
            "1. 검은 여백, 검은 화면 확인",
            "키프레임 9지점의 휘도(Y)를 읽어 고정된 검은 여백과 검은 화면을 찾습니다.",
            f"같은 지점이 {BLACK_FRAME_PIXEL_RATIO:.0%} 이상 어두우면 검은 화면으로 봅니다",
        ),
        (
            "2. 축소 후보 선별",
            "규격별로 줄였다 되돌린 프레임과 원본의 차이를 SSIM으로 먼저 재고, 통과한 후보만 VMAF로 확인합니다.",
            f"SSIM {enc.roundtrip_ssim_threshold:.3f} 이상, VMAF {enc.roundtrip_vmaf_threshold:.1f}점 이상",
        ),
        (
            "3. 판정 반영",
            "가장 낮은 통과 규격을 축소 후보로 정하고, 자동 적용이 켜져 있으면 출력 해상도와 권장 처리에 반영합니다. "
            "직접 지정한 출력 해상도가 있으면 그 값이 우선합니다.",
            "화질 분석 축소 후보 자동 적용: "
            + ("사용" if enc.auto_apply_frame_target else "사용 안 함"),
        ),
    )
    table = striped_table_html(
        p, ("단계", "하는 일", "현재 기준"), rows, first_col_width_pct=24
    )
    return "".join(
        (
            '<a name="frame-analysis"></a><h3>8. 화질 분석과 검은 여백 감지 원리</h3>',
            callout_html(
                p,
                "<b>쉽게 말하면</b> 원본을 더 작은 해상도로 줄이면 눈에 띄는 차이가 생기는지 먼저 재는 단계입니다. "
                "검은 여백을 빼고 실제 화면만 비교하며, 차이가 기준 안에 드는 가장 낮은 해상도를 축소 후보로 알려 줍니다.",
            ),
            formula_html(
                p,
                (
                    (note_html(p, "검은 여백 제거 후 활성 화면을 기준으로 비교"),),
                    ("활성 화면 = 원본 크기 - 검은 여백(상, 하, 좌, 우)",),
                    (
                        "축소 후보 = 왕복 SSIM, VMAF 기준을 모두 넘는 가장 낮은 규격",
                    ),
                ),
            ),
            table,
            "<ul>",
            f"<li><b>검은 여백 무시 면적 상한</b>: 활성 화면이 원본 면적의 "
            f"{enc.active_area_no_bars_ratio:.0%} 이상이면 검은 여백이 없다고 보고 잘라내지 않습니다.</li>",
            f"<li><b>가장자리 미세 여백 무시</b>: 면마다 {EDGE_SNAP_RATIO:.0%} 또는 {EDGE_SNAP_MIN_PX}px 미만의 "
            "어두운 가장자리는 노이즈로 보고 잘라내지 않습니다.</li>",
            "<li><b>VR 보호</b>: 구면, 입체 정보가 있는 영상은 검은 여백을 잘라내지 않습니다. "
            "축소 후보는 정상으로 측정합니다.</li>",
            "<li><b>직접 지정한 활성 화면</b>: 미리 보기의 여백 미리 보기에서 손잡이를 끌면 그 영역을 "
            "영상별 활성 화면으로 저장합니다. 전역 잘라내기 끔과 VR 보호가 가장 우선하고, "
            "그다음이 직접 지정, 영상별 제외(전체 화면 유지), 자동 감지 순서입니다. "
            "구간마다 검은 여백이 달라 손잡이로 맞추기 어려운 영상은 직접 지정하지 않는 편이 안전합니다.</li>",
            "<li><b>축소 후보 적용</b>: 화질 분석 축소 후보는 자동 적용이 켜져 있을 때만 판정에 반영하며, "
            "출력 해상도를 직접 지정했다면 그 규격만 씁니다.</li>",
            "<li><b>대상 해상도</b>: 설정한 해상도 미만인 영상은 검은 여백만 빠르게 확인하고 축소 후보 측정은 건너뜁니다.</li>",
            "</ul>",
        )
    )


def _internal_thresholds_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    encoder_rows = tuple(
        (
            html.escape(name),
            f"{k:.1f}",
            f"{ENCODER_SIZE_FACTORS.get(name, 1.0):.2f}",
        )
        for name, k in ENCODER_QUALITY_BITRATE_K.items()
    )
    encoder_table = striped_table_html(
        p, ("인코더", "기울기 K", "크기 보정"), encoder_rows, first_col_width_pct=52
    )
    fixed_rows = (
        (
            "화질 기준값 비트레이트 기울기",
            f"{QUALITY_BITRATE_K:.1f}",
            "화질 기준값 1 차이마다 기준 비트레이트가 두 배가 되는 폭을 정합니다.",
        ),
        (
            "검은 화면 밝기 기준",
            f"평균 휘도 {BLACK_LUMA_THRESHOLD} 미만",
            "키프레임 휘도가 이 값보다 어두우면 검은 화면 후보로 봅니다.",
        ),
        (
            "검은 화면 판정 비율",
            f"{BLACK_FRAME_PIXEL_RATIO:.0%}",
            "화면에서 어두운 픽셀 비율이 이 값 이상이면 그 지점을 검은 화면으로 봅니다.",
        ),
        (
            "가장자리 어두움 기준",
            f"평균 휘도 {BLACK_EDGE_MEAN_THRESHOLD:.0f}",
            "가장자리 띠가 이보다 어두울 때 검은 여백으로 인정합니다.",
        ),
        (
            "가장자리 미세 여백 무시",
            f"{EDGE_SNAP_RATIO:.0%} 또는 {EDGE_SNAP_MIN_PX}px",
            "이보다 얇은 띠는 노이즈로 보고 원본 경계를 유지합니다.",
        ),
        (
            "VR 자동 보호",
            f"{VR_ASPECT_RATIO_MIN:.2f}~{VR_ASPECT_RATIO_MAX:.2f}:1, 장변 {VR_MIN_LONG_SIDE}px 이상",
            "이 조건과 구면, 입체 정보가 함께면 검은 여백을 잘라내지 않습니다.",
        ),
        (
            "VMAF 표본 간격",
            f"{VMAF_SUBSAMPLE}",
            "VMAF를 확인할 때 프레임 몇 장마다 잴지 정합니다.",
        ),
        (
            "조기 확정 여유폭",
            f"SSIM +{SSIM_CONFIRMED_PASS_OFFSET:.3f} / -{SSIM_CONFIRMED_FAIL_OFFSET:.3f}, "
            f"VMAF +{VMAF_CONFIRMED_OFFSET:.1f} / -{VMAF_CONFIRMED_OFFSET:.1f}",
            "기준에서 이만큼 떨어져 있으면 더 재지 않고 통과, 탈락을 바로 확정합니다.",
        ),
        (
            "FPS 미세 오차 무시",
            f"+{FPS_HYSTERESIS_TOLERANCE:g} FPS",
            "기준 FPS와 이만큼 차이는 같은 FPS로 보고 보정하지 않습니다.",
        ),
        (
            "결과 파일 손상 검사 길이 오차",
            f"{DURATION_TOLERANCE_MIN_SEC:g}초 또는 {DURATION_TOLERANCE_RATIO:.1%}",
            "결과 영상 길이가 원본과 이 안에서 다르면 정상으로 봅니다.",
        ),
    )
    fixed_table = striped_table_html(
        p, ("기준", "값", "뜻"), fixed_rows, first_col_width_pct=34
    )
    return "".join(
        (
            '<a name="internal-thresholds"></a><h3>9. 내부 고정 기준값 안내</h3>',
            callout_html(
                p,
                "<b>읽기 전용</b> 화면입니다. 아래 값은 프로그램이 계산과 판정에 실제로 쓰는 상수이며, "
                "사용자가 바꾸는 설정은 위 절과 설정 화면에서 확인합니다.",
            ),
            "<h4>인코더별 화질 기준값 기울기와 크기 보정</h4>",
            encoder_table,
            GAP_HTML,
            "<h4>화질 분석, 검은 여백, 손상 검사 기준</h4>",
            fixed_table,
            GAP_HTML,
            f"<p>현재 화질 기준값은 <b>{format_cq_value(cfg.encode.baseline_cq)}</b>이며, "
            "설정을 바꾸면 화질 분석 기준과 예상 용량 계산에 함께 반영됩니다.</p>",
        )
    )


def build_concepts_html(
    p: ThemePalette,
    cfg: ClassifierConfig,
    shortcuts: Mapping[str, str] | None = None,
) -> str:
    """판정 원리 탭, 아홉 단계 수식 해설과 축소 판정, 화질 분석, 내부 기준값 절에 쉬운 요약과 현재 설정값 계산 예시 결합"""
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
            _frame_analysis_html(p, cfg),
            _internal_thresholds_html(p, cfg),
        )
    )
