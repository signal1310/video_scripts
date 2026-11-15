"""사용 가이드의 핵심 개념 탭, 판별 수식 해설과 현재 설정값 기반 계산 예시 HTML 조립 모듈"""

from __future__ import annotations

import html

from src.core.config import BitrateModelConfig, ClassifierConfig, get_encode_codec_label
from src.core.evaluator import (
    DEFAULT_UNKNOWN_KF_SEC,
    FPS_HYSTERESIS_TOLERANCE,
    MAX_CLAMP_KF_SEC,
    MIN_CLAMP_KF_SEC,
    MIN_OPTIMAL_BITRATE_KBPS,
    OVER_KEYFRAME_INCREASE_FACTOR,
    REFERENCE_ENCODE_HEIGHT,
    REFERENCE_ENCODE_WIDTH,
)
from src.core.messages import format_over_bitrate_desc
from src.gui.dialogs.help_blocks import (
    GAP_HTML,
    callout_html,
    card_html,
    formula_html,
    fraction_html,
    note_html,
    render_styles,
)
from src.gui.styles.palettes import ThemePalette

_EXAMPLE_WIDTH: int = 3840
_EXAMPLE_HEIGHT: int = 2160
_EXAMPLE_FPS: float = 60.0
_EXAMPLE_BITRATE_MULTIPLE: float = 2.0
_EXAMPLE_SIZE_MB: float = 1000.0
_EXAMPLE_DURATION_MIN: float = 10.0
_EXAMPLE_KEYFRAME_SEC: float = 8.0


def _area_factor(bm: BitrateModelConfig, width: int, height: int) -> float:
    return (width * height / (bm.base_width * bm.base_height)) ** bm.area_scaling_exponent


def _fps_factor(bm: BitrateModelConfig, fps: float) -> float:
    if fps > bm.base_fps + FPS_HYSTERESIS_TOLERANCE:
        return (fps / bm.base_fps) ** bm.fps_scaling_exponent
    return 1.0


def _outline_html() -> str:
    return (
        "<h2>핵심 개념, 판별 수식 해설</h2>"
        "<p>이 프로그램은 영상마다 <b>&quot;이 정도 화질이면 비트레이트가 얼마면 충분한가&quot;</b>를 먼저 계산하고, "
        "실제 영상과 비교해 재인코딩 여부를 정합니다. 아래 다섯 단계 순서로 읽으면 흐름이 이어집니다.</p>"
        "<ol>"
        "<li><b>최적 비트레이트</b>: 충분한 비트레이트의 기준선 계산</li>"
        "<li><b>BR 비율</b>: 실제 영상을 기준선과 비교해 화질 3구간으로 분류</li>"
        "<li><b>예상 용량과 절약</b>: 재인코딩 후 용량 예측</li>"
        "<li><b>분당 절약</b>: 시간을 들일 가치가 있는지 선별</li>"
        "<li><b>예상 시간</b>: 재인코딩에 걸리는 시간 어림</li>"
        "</ol>"
        "<p>수식이 어렵게 느껴지면 각 절의 <b>쉽게 말하면</b> 상자와 <b>계산 예시</b>만 읽어도 충분합니다. "
        "표시된 숫자는 모두 현재 설정값을 반영하며, 설정을 바꾸면 이 화면도 함께 바뀝니다.</p>"
    )


def _optimal_bitrate_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    bm = cfg.bitrate_model
    cw = cfg.codec_weights
    area_scale_4x = 4.0**bm.area_scaling_exponent
    fps_60_scale = (60.0 / max(bm.base_fps, 1.0)) ** bm.fps_scaling_exponent
    area = _area_factor(bm, _EXAMPLE_WIDTH, _EXAMPLE_HEIGHT)
    fps = _fps_factor(bm, _EXAMPLE_FPS)
    raw_kbps = bm.base_target_kbps * area * fps * cw.hevc_vp9
    optimal = max(round(raw_kbps), MIN_OPTIMAL_BITRATE_KBPS)
    example = (
        f"<b>계산 예시</b>: {_EXAMPLE_WIDTH}×{_EXAMPLE_HEIGHT}, {_EXAMPLE_FPS:g}fps HEVC 영상<br/>"
        f"K<sub>면적</sub> = {area:.2f}, K<sub>fps</sub> = {fps:.2f}, C<sub>코덱</sub> = {cw.hevc_vp9:.2f}<br/>"
        f"B<sub>opt</sub> = {bm.base_target_kbps:,} × {area:.2f} × {fps:.2f} × {cw.hevc_vp9:.2f} "
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
            "<p><b>CQ22</b>는 육안으로 원본과 차이를 거의 느끼기 어려운 고정 화질 목표입니다. "
            "같은 화질이어도 해상도, 프레임레이트(FPS), 코덱 압축 효율에 따라 필요한 비트레이트가 달라지므로, "
            "기준 영상 하나를 정해 두고 그 대비 얼마나 더 필요한지 곱해서 "
            "<b>최적 BR(B<sub>opt</sub>)</b>을 계산합니다.</p>",
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
            "모든 계산의 출발점입니다.</li>",
            f"<li><b>해상도 지수 α ({bm.area_scaling_exponent:.2f})</b>: 화면 면적이 4배(예: 1080p→4K) 늘어도 "
            "이웃한 픽셀끼리 비슷해 압축 효율이 좋아지므로 비트레이트가 4배까지는 필요하지 않습니다. "
            f"그래서 면적 비율에 지수를 적용해 <code>4<sup>{bm.area_scaling_exponent:.2f}</sup> ≈ {area_scale_4x:.2f}배</code>"
            "만 요구합니다.</li>",
            f"<li><b>프레임 지수 β ({bm.fps_scaling_exponent:.2f})</b>: 프레임이 많을수록 데이터가 늘지만 "
            "연속된 장면은 서로 비슷해 비례하지는 않습니다. 또 29.97/30fps 같은 미세한 차이에 흔들리지 않도록 "
            f"기준 FPS + {FPS_HYSTERESIS_TOLERANCE:g}fps 여유를 넘길 때만 가중합니다. "
            f"(예: 60fps는 <code>{fps_60_scale:.2f}배</code> 보정)</li>",
            f"<li><b>코덱 가중치 C<sub>코덱</sub></b>: H.264(<code>{cw.h264:.2f}</code>) 대비 같은 화질에 필요한 비율입니다. "
            f"현재 설정은 AV1 <code>{cw.av1:.2f}</code>, HEVC/VP9 <code>{cw.hevc_vp9:.2f}</code>, "
            f"레거시 <code>{cw.legacy:.2f}</code>이며 압축 효율이 높은 최신 코덱일수록 기준이 낮아집니다.</li>",
            f"<li><b>하한 {MIN_OPTIMAL_BITRATE_KBPS} kbps</b>: 해상도가 아주 작은 영상에서 기준이 "
            "비현실적으로 낮아지는 것을 막습니다.</li>",
            "</ul>",
            card_html(p, example),
        )
    )


def _ratio_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    t = cfg.thresholds
    over_example = html.escape(format_over_bitrate_desc(cfg.bitrate_model, t.over_bitrate_ratio))
    return "".join(
        (
            "<h3>2. BR 비율(r)과 화질 3구간 판정</h3>",
            callout_html(
                p,
                "<b>쉽게 말하면</b> 실제 영상이 &quot;충분한 양&quot;의 몇 배인지 재는 값입니다. "
                "1배면 딱 맞고, 크면 낭비, 작으면 이미 많이 줄여 둔 상태입니다.",
            ),
            formula_html(
                p,
                (
                    (
                        "BR 비율(r) = ",
                        fraction_html(
                            p, "현재 영상 비트레이트(B<sub>curr</sub>)", "최적 BR(B<sub>opt</sub>)"
                        ),
                    ),
                ),
            ),
            "<ul>",
            f"<li><b>과화질</b> (<code>r &gt; {t.over_bitrate_ratio:.2f}배</code>): 필요 이상으로 비트레이트가 높습니다. "
            f"화질 손실 없이 용량을 줄일 수 있습니다. (예: {over_example})</li>",
            f"<li><b>적정</b> (<code>{t.low_bitrate_ratio:.2f}배 ≤ r ≤ {t.over_bitrate_ratio:.2f}배</code>): "
            "균형 잡힌 상태이므로 키프레임이 정상이면 원본을 보존합니다.</li>",
            f"<li><b>저화질</b> (<code>r &lt; {t.low_bitrate_ratio:.2f}배</code>): 이미 많이 압축된 영상입니다. "
            "여기서 더 압축하면 눈에 띄게 나빠지거나, 흐릿한 부분과 압축 노이즈까지 정밀하게 되살려 "
            "오히려 용량이 늘 수 있어 재인코딩으로부터 보호합니다.</li>",
            "</ul>",
            "<p>경계값은 <b>판별 기준(Ctrl+T)</b>에서 바꿀 수 있습니다. "
            "과화질 경계를 낮추면 더 많은 영상이, 높이면 확실히 낭비가 큰 영상만 대상이 됩니다.</p>",
        )
    )


def _size_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    t = cfg.thresholds
    over_kf_pct = (OVER_KEYFRAME_INCREASE_FACTOR - 1.0) * 100.0
    kf_inc_pct = t.keyframe_1s_increase_rate * 100.0
    ratio = _EXAMPLE_BITRATE_MULTIPLE
    after_mb = min(_EXAMPLE_SIZE_MB, _EXAMPLE_SIZE_MB / ratio)
    kf_clamped = min(max(_EXAMPLE_KEYFRAME_SEC, MIN_CLAMP_KF_SEC), MAX_CLAMP_KF_SEC)
    kf_ratio = t.keyframe_1s_increase_rate * (1.0 - 1.0 / kf_clamped)
    over_example = (
        f"<b>계산 예시 (과화질)</b>: {_EXAMPLE_SIZE_MB:,.0f} MB 영상의 r이 {ratio:.1f}배이고 키프레임이 정상이면<br/>"
        f"예상 용량 = {_EXAMPLE_SIZE_MB:,.0f} ÷ {ratio:.1f} = <b>{after_mb:,.0f} MB</b>, "
        f"절약 ΔS = <b>{_EXAMPLE_SIZE_MB - after_mb:,.0f} MB</b>"
    )
    kf_example = (
        f"<b>계산 예시 (키프레임 교정)</b>: {_EXAMPLE_SIZE_MB:,.0f} MB 영상의 키프레임 간격이 {_EXAMPLE_KEYFRAME_SEC:g}초이면<br/>"
        f"증가율 = {t.keyframe_1s_increase_rate:.2f} × (1 − 1/{kf_clamped:g}) = {kf_ratio:.3f}, "
        f"용량 변화 ≈ <b>+{_EXAMPLE_SIZE_MB * kf_ratio:,.1f} MB</b>"
    )
    return "".join(
        (
            "<h3>3. 예상 용량, 예상 절약, 키프레임 재인코딩 용량 증가율</h3>",
            callout_html(
                p,
                "<b>쉽게 말하면</b> 재인코딩하면 용량이 얼마나 줄지 미리 어림해 보는 단계입니다. "
                "과화질 영상은 줄고, 키프레임만 고치는 영상은 오히려 조금 늡니다.",
            ),
            formula_html(
                p,
                (
                    (note_html(p, "과화질 재인코딩"),),
                    (
                        "예상 용량 = min( 현재 용량, 현재 용량 × ",
                        fraction_html(p, "1", "r"),
                        " × K<sub>kf</sub> )",
                    ),
                    (
                        note_html(
                            p,
                            f"K<sub>kf</sub> = 키프레임 간격 초과 시 {OVER_KEYFRAME_INCREASE_FACTOR:.2f}, 정상 시 1.00",
                        ),
                    ),
                    (note_html(p, "적정, 저화질 키프레임 재인코딩"),),
                    (
                        f"증가율 = {t.keyframe_1s_increase_rate:.2f} × (1 − ",
                        fraction_html(
                            p,
                            "1",
                            f"clamp(키프레임 간격, {MIN_CLAMP_KF_SEC:.1f}, {MAX_CLAMP_KF_SEC:.1f})",
                        ),
                        ")",
                    ),
                    (note_html(p, "예상 절약"),),
                    ("ΔS = 현재 용량 − 예상 용량",),
                    (note_html(p, "+: 절약, −: 키프레임 재인코딩에 따른 증가"),),
                ),
            ),
            "<ul>",
            "<li><b>과화질 용량 예측</b>: 같은 화질이면 용량은 비트레이트에 비례합니다. 그래서 목표 비트레이트로 "
            "수렴하면 용량이 대략 <code>1 ÷ r</code> 비율로 줄어듭니다. "
            f"키프레임 간격 초과 재인코딩이 겹치면 1초 I-프레임 삽입 여유(<code>+{over_kf_pct:.0f}%</code>)를 함께 반영합니다.</li>",
            f"<li><b>키프레임 재인코딩 증가(-)</b>: 비트레이트는 적정/저화질이지만 간격이 <b>{t.keyframe_interval_sec:.1f}초</b>를 "
            "넘으면 구간 이동 시 멈칫거립니다. 1초마다 온전한 프레임(키프레임)을 추가로 넣으면 구간 이동은 빨라지는 "
            f"대신 온전한 그림이 늘어 최대 <code>{kf_inc_pct:.1f}%</code> 범위 내 비율로 용량이 소폭 늘어납니다. "
            f"(미측정 시 보수값 {DEFAULT_UNKNOWN_KF_SEC:.1f}초 기준)</li>",
            "<li><b>간격 계산의 상하한</b>: 키프레임 간격은 계산할 때 "
            f"{MIN_CLAMP_KF_SEC:.1f}초 이상 {MAX_CLAMP_KF_SEC:.1f}초 이하로 맞춰 극단값이 결과를 왜곡하지 않게 합니다.</li>",
            "<li><b>순 절약량</b>: 상단 요약 카드는 과화질 재인코딩 절약(<code>+</code>)에서 키프레임 재인코딩 증가분(<code>-</code>)을 "
            "뺀 실제 순 절약 용량을 보여줍니다. (표의 회색 수치는 원본 보존 행을 재인코딩한다고 가정한 참고값입니다.)</li>",
            "</ul>",
            card_html(p, over_example),
            GAP_HTML,
            card_html(p, kf_example),
        )
    )


def _per_minute_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    savings = cfg.savings
    ratio = _EXAMPLE_BITRATE_MULTIPLE
    potential = _EXAMPLE_SIZE_MB * (1.0 - 1.0 / ratio)
    per_min = potential / _EXAMPLE_DURATION_MIN
    passes = potential >= savings.min_saved_mb and per_min >= savings.min_mb_per_min
    verdict = "두 기준을 모두 넘으므로 재인코딩 대상" if passes else "기준에 못 미쳐 원본 보존"
    exempt_line = (
        f"단, 총 예상 절약량이 <b>{savings.exempt_saved_mb:.0f} MB 이상</b>이면 분당 절약이 낮아도 예외적으로 포함합니다."
        if savings.exempt_saved_mb > 0
        else "현재 대용량 절약 예외 허용은 비활성화(0 MB)되어 있습니다."
    )
    example = (
        f"<b>계산 예시</b>: {_EXAMPLE_SIZE_MB:,.0f} MB, {_EXAMPLE_DURATION_MIN:g}분 영상의 r이 {ratio:.1f}배이면<br/>"
        f"S<sub>pot</sub> = {_EXAMPLE_SIZE_MB:,.0f} × (1 − 1/{ratio:.1f}) = {potential:,.0f} MB, "
        f"분당 절약 = {potential:,.0f} ÷ {_EXAMPLE_DURATION_MIN:g} = <b>{per_min:,.1f} MB/분</b><br/>"
        f"기준({savings.min_saved_mb:.1f} MB, {savings.min_mb_per_min:.1f} MB/분)과 비교하면 {verdict}입니다."
    )
    return "".join(
        (
            "<h3>4. 분당 절약 (MB/분) — 재생 길이 대비 절약 선별</h3>",
            callout_html(
                p,
                "<b>쉽게 말하면</b> 인코딩에는 시간과 전기가 듭니다. 그 수고에 비해 얻는 용량이 너무 작은 영상은 "
                "건너뛰기 위한 &quot;가성비&quot; 기준입니다.",
            ),
            formula_html(
                p,
                (
                    (
                        "분당 절약(MB/분) = ",
                        fraction_html(
                            p,
                            "예상 절약량(MB)",
                            fraction_html(p, "영상 재생 길이(초)", "60"),
                        ),
                    ),
                    (
                        "이론 절약 잠재량(S<sub>pot</sub>) = 현재 용량 × (1 − ",
                        fraction_html(p, "1", "r"),
                        ")",
                    ),
                ),
            ),
            "<ul>",
            "<li><b>무엇을 뜻하나요?</b> 원본 영상 <b>재생 1분당 몇 MB를 절약할 수 있는지</b> 나타냅니다. "
            "예를 들어 10분짜리 영상에서 50 MB가 줄어들면 분당 절약은 <code>+5.0 MB/분</code>입니다.</li>",
            f"<li><b>절약 미달 제외</b>: 키프레임이 정상인 과화질 영상이라도 <code>S<sub>pot</sub> &lt; {savings.min_saved_mb:.1f} MB</code>이면 "
            "절약 효과가 작아 원본을 보존합니다.</li>",
            f"<li><b>분당 미달 제외</b>: 분당 절약이 <code>{savings.min_mb_per_min:.1f} MB/분</code> 미만이면 재인코딩 시간 대비 "
            f"절약이 적어 자동으로 건너뜁니다. ({exempt_line})</li>",
            "<li><b>키프레임 불량 영상은 예외</b>: 구간 이동이 버벅이는 영상은 절약량과 무관하게 "
            "교정이 필요하므로 이 선별을 거치지 않고 대상에 포함됩니다.</li>",
            "</ul>",
            card_html(p, example),
        )
    )


def _time_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    bm = cfg.bitrate_model
    enc = cfg.encode
    codec_label = html.escape(get_encode_codec_label(enc.encode_codec))
    ref_work = _area_factor(bm, REFERENCE_ENCODE_WIDTH, REFERENCE_ENCODE_HEIGHT) * _fps_factor(
        bm, bm.base_fps
    )
    work = _area_factor(bm, _EXAMPLE_WIDTH, _EXAMPLE_HEIGHT) * _fps_factor(bm, _EXAMPLE_FPS)
    w_ratio = work / ref_work
    speed = max(enc.encode_speed_factor, 0.01)
    minutes = _EXAMPLE_DURATION_MIN * w_ratio / speed
    example = (
        f"<b>계산 예시</b>: {_EXAMPLE_WIDTH}×{_EXAMPLE_HEIGHT}, {_EXAMPLE_FPS:g}fps, {_EXAMPLE_DURATION_MIN:g}분 영상<br/>"
        f"W<sub>ratio</sub> ≈ {w_ratio:.2f}, 예상 시간 = {_EXAMPLE_DURATION_MIN:g}분 × {w_ratio:.2f} ÷ {speed:.1f} "
        f"≈ <b>{minutes:,.1f}분</b>"
    )
    return "".join(
        (
            "<h3>5. 예상 시간 — 무엇에 대한 시간인가요?</h3>",
            callout_html(
                p,
                "<b>예상 시간은 폴더 분석이나 파일 이동 시간이 아니라, 분류 후 샤나인코더 등으로 "
                "실제 재인코딩을 실행할 때 걸리는 예상 시간입니다.</b>",
            ),
            formula_html(
                p,
                (
                    (
                        "상대 작업량 배수(W<sub>ratio</sub>) = ",
                        fraction_html(
                            p,
                            "K<sub>면적</sub>(현재) × K<sub>fps</sub>(현재)",
                            f"K<sub>면적</sub>({REFERENCE_ENCODE_WIDTH}×{REFERENCE_ENCODE_HEIGHT}) × K<sub>fps</sub>({bm.base_fps:g})",
                        ),
                    ),
                    (
                        "예상 시간(초) = ",
                        fraction_html(
                            p,
                            "영상 재생 길이(초) × W<sub>ratio</sub>",
                            f"기준 배속({enc.encode_speed_factor:.1f}배속)",
                        ),
                    ),
                ),
            ),
            "<ul>",
            f"<li><b>해석</b>: <code>1080p {bm.base_fps:g}fps</code>를 작업량 <code>1.0배</code>로 두고, 4K, 고주사율 영상은 "
            "화소, 프레임 증가에 비례해 작업량이 늘어납니다. 이 작업량을 설정된 "
            f"<b>목표 코덱(<code>{codec_label}</code>) 기준 배속</b>(<code>{enc.encode_speed_factor:.1f}배속</code>)으로 "
            "나누어 시간을 산출합니다. <b>배속</b>은 영상 길이 대비 인코딩이 얼마나 빠른지 나타내며, "
            f"예컨대 <code>2.0배속</code>이면 1080p 10분 영상 재인코딩에 약 5분이 걸립니다.</li>",
            "<li><b>집계 기준</b>: 상단 <b>재인코딩 작업 분량</b> 카드의 예상 시간은 <b>실제 재인코딩 대상 파일만</b> 합산합니다. "
            "테이블의 원본 보존 행에 회색으로 표시되는 시간은 &quot;이 파일을 재인코딩한다고 가정한&quot; 참고 시간이며, "
            "행을 선택했을 때 하단 푸터 툴팁에서 <b>실제 재인코딩 대상 기준 예상 시간</b>도 별도로 확인할 수 있습니다.</li>",
            "<li><b>어디까지나 어림값</b>: 컴퓨터 성능, 인코딩 설정에 따라 실제 시간은 달라집니다. "
            "설정의 배속 값을 본인 환경에서 실제로 걸린 시간에 맞춰 두면 정확도가 올라갑니다.</li>",
            "</ul>",
            card_html(p, example),
        )
    )


def _order_html() -> str:
    return (
        "<h3>6. 한 영상이 판정되는 순서</h3>"
        "<ol>"
        "<li><b>예외 규칙</b>에 일치하면 즉시 원본 보존</li>"
        "<li><b>최적 BR</b>을 계산하고 <b>BR 비율</b>로 과화질, 적정, 저화질 구간 결정</li>"
        "<li><b>과화질</b>: 키프레임이 정상이면 절약량과 분당 절약 기준을 통과해야 대상, 키프레임이 불량이면 바로 대상</li>"
        "<li><b>적정, 저화질</b>: 키프레임 간격이 초과일 때만 대상, 정상이면 원본 보존</li>"
        "</ol>"
    )


def build_concepts_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    """핵심 개념 탭, 다섯 단계 수식 해설에 쉬운 요약과 현재 설정값 계산 예시 결합"""
    return "".join(
        (
            render_styles(p),
            _outline_html(),
            _optimal_bitrate_html(p, cfg),
            _ratio_html(p, cfg),
            _size_html(p, cfg),
            _per_minute_html(p, cfg),
            _time_html(p, cfg),
            _order_html(),
        )
    )
