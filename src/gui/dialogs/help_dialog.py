"""프로그램 목적, 핵심 개념(KPI, 수식 해설), 예외 조건식 및 단축키 통합 가이드 대화상자 모듈"""

from __future__ import annotations

import html

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QDialog,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.core.config import ClassifierConfig, get_encode_codec_label
from src.core.evaluator import (
    DEFAULT_UNKNOWN_KF_SEC,
    FPS_HYSTERESIS_TOLERANCE,
    MAX_CLAMP_KF_SEC,
    MIN_CLAMP_KF_SEC,
    MIN_OPTIMAL_BITRATE_KBPS,
    OVER_KEYFRAME_OVERHEAD_FACTOR,
    REFERENCE_ENCODE_HEIGHT,
    REFERENCE_ENCODE_WIDTH,
)
from src.core.exception_rules import get_exception_syntax_reference
from src.core.messages import format_over_bitrate_desc
from src.gui.constants import FONT_MONO_FAMILY, FONT_UI_FAMILY
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.theme import ThemeAwareMixin, ThemeManager

_DIALOG_DEFAULT_WIDTH: int = 780
_DIALOG_DEFAULT_HEIGHT: int = 640
_DIALOG_MIN_WIDTH: int = 560
_DIALOG_MIN_HEIGHT: int = 440
_DIALOG_MARGINS: tuple[int, int, int, int] = (14, 12, 14, 14)
_DIALOG_SPACING: int = 10
_HELP_BODY_FONT_PT: int = 11

HELP_SHORTCUT_ITEMS: tuple[tuple[str, str], ...] = (
    ("Ctrl+O", "작업 폴더 열기"),
    ("Ctrl+E", "현재 필터, 정렬된 테이블을 CSV로 내보내기"),
    ("Ctrl+Q", "애플리케이션 종료"),
    ("Ctrl+F", "파일명, 코덱 검색 입력창 포커스"),
    ("Ctrl+T", "화질/효율 기준 실시간 조정 탭 열기"),
    ("Ctrl+Shift+E", "여러 줄 예외 규칙 목록 편집기 열기"),
    ("Ctrl+,", "환경/세부 설정 탭 열기"),
    ("Ctrl+L", "작업 로그 뷰어 열기"),
    ("F5 / Shift+F5", "현재 모드로 스캔 / 캐시 무시 강제 재스캔"),
    ("Ctrl+Shift+C", "선택한 행의 전체 절대 경로 클립보드 복사"),
    ("Enter / 더블클릭", "선택한 영상을 기본 미디어 플레이어로 재생"),
    ("Esc", "실행 중인 스캔, 분류 작업 즉시 중단"),
    ("F1", "사용 가이드 창 열기"),
)


def _render_styles(p: ThemePalette) -> str:
    ui_fonts = f'"{FONT_UI_FAMILY}", "Segoe UI", sans-serif'
    mono_fonts = f'"{FONT_MONO_FAMILY}", "{FONT_UI_FAMILY}", monospace'
    return (
        "<style>"
        f"body {{ font-family: {ui_fonts}; font-size: {_HELP_BODY_FONT_PT}pt; "
        f"color: {p.text_primary}; line-height: 1.7; margin: 0; }}"
        f"h2 {{ color: {p.accent_blue}; font-size: 14pt; margin: 14px 0 8px 0; }}"
        f"h3 {{ color: {p.text_primary}; font-size: 12pt; margin: 16px 0 6px 0; }}"
        f"p {{ color: {p.text_secondary}; margin: 4px 0 9px 0; }}"
        f"ul, ol {{ color: {p.text_secondary}; margin: 4px 0 10px 0; padding-left: 20px; }}"
        "li { margin-bottom: 5px; }"
        f"b, strong {{ color: {p.text_primary}; }}"
        f"code {{ background-color: {p.bg_elevated}; color: {p.text_primary}; "
        f"padding: 1px 6px; border-radius: 4px; font-family: {mono_fonts}; font-size: 10pt; }}"
        f".callout {{ background-color: {p.bg_elevated}; border: 1px solid {p.border_subtle}; "
        "border-radius: 8px; padding: 11px 14px; margin: 8px 0 12px 0; }}"
        f".formula {{ background-color: {p.bg_elevated}; border-left: 3px solid {p.accent_blue}; "
        f"border-radius: 6px; padding: 9px 13px; margin: 6px 0 10px 0; "
        f"font-family: {mono_fonts}; font-size: 10pt; color: {p.text_primary}; line-height: 1.6; }}"
        "table { border-collapse: collapse; width: 100%; margin: 8px 0 12px 0; }"
        f"th, td {{ border: 1px solid {p.border_subtle}; padding: 7px 10px; text-align: left; }}"
        f"th {{ background-color: {p.bg_elevated}; color: {p.text_primary}; }}"
        f"td {{ color: {p.text_secondary}; }}"
        "</style>"
    )


def _build_guide_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    std_dir = html.escape(cfg.dir_standard_cq22)
    low_dir = html.escape(cfg.dir_low_quality_keyframe)
    return (
        f"{_render_styles(p)}"
        "<h2>이 프로그램은 왜 사용하나요?</h2>"
        "<div class='callout'>"
        "보관 중인 영상들을 무작정 다시 인코딩하면 화질이 나빠지거나 시간만 낭비되기 쉽습니다. "
        "이 도구는 <b>눈에 띄는 화질 손실 없이 용량을 줄일 수 있는 영상</b>과 "
        "<b>재생 탐색(앞/뒤 건너뛰기)이 버벅이는 영상</b>만 정확히 골라내, "
        "샤나인코더 프리셋으로 바로 일괄 인코딩할 수 있게 안전하게 분류해 줍니다."
        "</div>"
        "<h3>기본 작업 흐름 (4단계)</h3>"
        "<ol>"
        "<li><b>작업 폴더 선택 및 스캔</b>: 상단 폴더 버튼 또는 창으로 폴더를 드래그 앤 드롭한 뒤 "
        "<b>빠른 스캔</b>(해상도, 비트레이트 고속 조회) 또는 <b>정밀 분석</b>(키프레임 간격 실측 포함)을 실행합니다.</li>"
        "<li><b>기준 실시간 조정 및 예외 지정</b>: <b>화질/효율 기준(Ctrl+T)</b> 슬라이더나 "
        "<b>예외 목록(Ctrl+Shift+E)</b>을 바꾸면 재스캔 없이 결과를 즉시 미리 볼 수 있습니다.</li>"
        "<li><b>폴더 분류 이동</b>: 하단 <b>폴더 분류 이동</b>을 실행하면 대상 파일만 하위 폴더로 이동하며, "
        "이름이 겹치는 기존 파일은 절대 덮어쓰지 않습니다. (<b>분류 복원</b>으로 언제든 원위치 복구가 가능합니다.)</li>"
        "<li><b>샤나인코더 일괄 인코딩</b>: 상단 <b>도움말 &gt; 샤나인코더 프리셋 폴더 열기</b> 메뉴에서 "
        "<code>shana_presets</code> XML 프리셋을 불러와 분류 폴더별로 1:1 인코딩을 실행합니다.</li>"
        "</ol>"
        "<h3>분류 폴더별 역할</h3>"
        "<ul>"
        f"<li><code>{std_dir}</code>: <b>과화질</b>(용량 대폭 감축) 또는 "
        "<b>적정화질 + 키프레임 불량</b> 영상이 이동합니다. 원본 해상도, 화면 비율을 유지한 채 "
        "<code>CQ 22 + 1초 키프레임</code> 프리셋으로 인코딩합니다.</li>"
        f"<li><code>{low_dir}</code>: 이미 <b>저화질</b>이지만 키프레임이 불량한 영상이 이동합니다. "
        "압축 노이즈가 복원되며 용량이 부풀어 오르는 것을 막는 <code>CQ 26 + 1초 키프레임</code> 프리셋을 적용합니다.</li>"
        "<li><b>루트 폴더 유지 (원본 보존)</b>: 화질과 키프레임이 모두 정상이거나, "
        "재인코딩해도 용량 절감 실익이 기준 미만인 영상은 건드리지 않고 그대로 보존합니다.</li>"
        "</ul>"
    )


def _build_concepts_html(p: ThemePalette, cfg: ClassifierConfig) -> str:
    t = cfg.thresholds
    eff = cfg.efficiency
    bm = cfg.bitrate_model
    cw = cfg.codec_weights
    codec_label = html.escape(get_encode_codec_label(eff.encode_codec))
    over_example = html.escape(format_over_bitrate_desc(bm, t.over_bitrate_ratio))
    area_scale_4x = 4.0 ** bm.area_scaling_exponent
    fps_60_scale = (60.0 / max(bm.base_fps, 1.0)) ** bm.fps_scaling_exponent
    kf_ovh_pct = t.keyframe_1s_overhead_rate * 100.0
    over_kf_pct = (OVER_KEYFRAME_OVERHEAD_FACTOR - 1.0) * 100.0
    exempt_line = (
        f"단, 총 예상 절감량이 <b>{eff.exempt_saved_mb:.0f} MB 이상</b>이면 분당 효율이 낮아도 예외적으로 포함합니다."
        if eff.exempt_saved_mb > 0
        else "현재 대용량 절감 예외 면제는 비활성화(0 MB)되어 있습니다."
    )
    return (
        f"{_render_styles(p)}"
        "<h2>핵심 개념, 판별 수식 해설</h2>"
        "<h3>1. 최적 비트레이트(최적 BR) 계산식</h3>"
        "<p><b>CQ22</b>는 육안으로 원본과 차이를 거의 느끼기 어려운 고정 화질 목표입니다. "
        "해상도, 프레임레이트(FPS), 코덱 효율에 따라 적정 비트레이트가 달라지므로 아래 비선형 모델로 "
        "<b>최적 BR(B<sub>opt</sub>)</b>을 계산합니다.</p>"
        "<div class='formula'>"
        "B<sub>opt</sub> = max( round( B<sub>기준</sub> × K<sub>면적</sub> × K<sub>fps</sub> × C<sub>코덱</sub> ), "
        f"{MIN_OPTIMAL_BITRATE_KBPS} kbps )<br/>"
        f"• K<sub>면적</sub> = ( (가로 × 세로) ÷ ({bm.base_width} × {bm.base_height}) )<sup>{bm.area_scaling_exponent:.2f}</sup><br/>"
        f"• K<sub>fps</sub> = ( fps ÷ {bm.base_fps:g} )<sup>{bm.fps_scaling_exponent:.2f}</sup> "
        f"(fps &gt; {bm.base_fps + FPS_HYSTERESIS_TOLERANCE:g}일 때만 적용, 그 외 1.0)"
        "</div>"
        "<ul>"
        f"<li><b>기준선 B<sub>기준</sub></b>: <code>{bm.base_width}×{bm.base_height}, {bm.base_fps:g}fps, H.264</code>에서 "
        f"목표 화질을 만족하는 기준 비트레이트(현재 <b>{bm.base_target_kbps:,} kbps</b>)입니다.</li>"
        f"<li><b>해상도 지수 α ({bm.area_scaling_exponent:.2f})</b>: 해상도 면적이 4배(예: 720p→1440p) 늘어나도 "
        f"인접 픽셀 중복 덕분에 비트레이트는 4배가 아니라 <code>4<sup>{bm.area_scaling_exponent:.2f}</sup> ≈ {area_scale_4x:.2f}배</code>만 "
        "필요하므로 지수 감쇠를 적용합니다.</li>"
        f"<li><b>프레임 지수 β ({bm.fps_scaling_exponent:.2f})</b>: 29.97/30fps 미세 오차에 흔들리지 않도록 "
        f"기준 FPS + {FPS_HYSTERESIS_TOLERANCE:g}fps 여유를 넘길 때만 가중합니다. "
        f"(예: 60fps는 <code>{fps_60_scale:.2f}배</code> 보정)</li>"
        f"<li><b>코덱 가중치 C<sub>코덱</sub></b>: H.264(<code>{cw.h264:.2f}</code>) 대비 동일 화질에 필요한 비율입니다. "
        f"현재 설정은 AV1 <code>{cw.av1:.2f}</code>, HEVC/VP9 <code>{cw.hevc_vp9:.2f}</code>, 레거시 <code>{cw.legacy:.2f}</code>이며 "
        "압축 효율이 높은 최신 코덱일수록 기준이 낮아집니다.</li>"
        "</ul>"
        "<h3>2. BR 비율(r)과 화질 3구간 판정</h3>"
        "<div class='formula'>"
        "BR 비율(r) = 현재 영상 비트레이트(B<sub>curr</sub>) ÷ 최적 BR(B<sub>opt</sub>)"
        "</div>"
        "<ul>"
        f"<li><b>과화질</b> (<code>r &gt; {t.over_bitrate_ratio:.2f}배</code>): 필요 이상으로 비트레이트가 높습니다. "
        f"화질 손실 없이 용량을 줄일 수 있습니다. (예: {over_example})</li>"
        f"<li><b>적정화질</b> (<code>{t.low_bitrate_ratio:.2f}배 ≤ r ≤ {t.over_bitrate_ratio:.2f}배</code>): "
        "균형 잡힌 상태이므로 키프레임이 정상이면 원본을 보존합니다.</li>"
        f"<li><b>저화질</b> (<code>r &lt; {t.low_bitrate_ratio:.2f}배</code>): 이미 많이 압축된 영상이므로 "
        "재인코딩으로 화질이 더 떨어지지 않도록 보호합니다.</li>"
        "</ul>"
        "<h3>3. 예상 용량, 예상 절감량, 1초 키프레임 오버헤드</h3>"
        "<div class='formula'>"
        "• 과화질 재인코딩: 예상 용량 = min( 원본, 원본 × (1 ÷ r) × K<sub>kf</sub> ) &nbsp; "
        f"(K<sub>kf</sub> = 키프레임 불량 시 {OVER_KEYFRAME_OVERHEAD_FACTOR:.2f}, 정상 시 1.00)<br/>"
        "• 적정, 저화질 키프레임 교정: 증가율 = "
        f"{t.keyframe_1s_overhead_rate:.2f} × (1 − 1 ÷ clamp(키프레임 간격, {MIN_CLAMP_KF_SEC:.1f}, {MAX_CLAMP_KF_SEC:.1f}))<br/>"
        "• 예상 절감량(ΔS) = 원본 용량 − 예상 용량 &nbsp;(+: 용량 감소, −: 키프레임 추가로 증가)"
        "</div>"
        "<ul>"
        f"<li><b>과화질 용량 예측</b>: 목표 비트레이트로 수렴하면 용량이 대략 <code>1 ÷ r</code> 비율로 줄어들며, "
        f"키프레임 불량 교정이 겹치면 1초 I-프레임 삽입 여유(<code>+{over_kf_pct:.0f}%</code>)를 함께 반영합니다.</li>"
        f"<li><b>1초 키프레임 교정(+)</b>: 비트레이트는 적정/저화질이지만 간격이 <b>{t.keyframe_interval_sec:.1f}초</b>를 넘으면 "
        "탐색 시 멈칫거립니다. 1초마다 기준 프레임을 추가 삽입하면 탐색은 빨라지는 대신 최대 "
        f"<code>{kf_ovh_pct:.1f}%</code> 범위 내 비율로 용량이 소폭 늘어납니다. (미측정 시 보수값 {DEFAULT_UNKNOWN_KF_SEC:.1f}초 기준)</li>"
        "<li><b>순수 절감량(Net)</b>: 상단 요약 카드는 과화질 감축분(<code>-</code>)과 키프레임 교정 증가분(<code>+</code>)을 "
        "모두 합산한 실제 순 절감 용량을 보여줍니다. (표의 회색 수치는 원본 보존 행을 만약 인코딩했을 때의 참고치입니다.)</li>"
        "</ul>"
        "<h3>4. 절감 효율 (MB/분) — 재생 길이 대비 가성비 선별</h3>"
        "<div class='formula'>"
        "절감 효율(MB/분) = 예상 절감량(MB) ÷ (영상 재생 길이(초) ÷ 60)<br/>"
        "이론 절감 잠재량(S<sub>pot</sub>) = 원본 용량 × (1 − 1 ÷ r)"
        "</div>"
        "<ul>"
        "<li><b>무엇을 뜻하나요?</b> 원본 영상 <b>재생 1분당 몇 MB를 줄일 수 있는지</b> 나타내는 시간 가성비입니다. "
        "예를 들어 10분짜리 영상에서 50 MB가 줄어들면 절감 효율은 <code>+5.0 MB/분</code>입니다.</li>"
        f"<li><b>절감 미미 제외</b>: 키프레임이 정상인 과화질 영상이라도 <code>S<sub>pot</sub> &lt; {eff.min_saved_mb:.1f} MB</code>이면 "
        "실익이 너무 작아 원본을 유지합니다.</li>"
        f"<li><b>시간 비효율 제외</b>: 분당 효율이 <code>{eff.min_mb_per_min:.1f} MB/분</code> 미만이면 인코딩에 들이는 노력 대비 "
        f"절감 이득이 적어 자동으로 건너뜁니다. ({exempt_line})</li>"
        "</ul>"
        "<h3>5. 예상 소요 — 무엇에 대한 시간인가요?</h3>"
        "<div class='callout'>"
        "<b>예상 소요는 폴더 스캔이나 파일 이동 시간이 아니라, 분류 후 샤나인코더 등으로 실제로 재인코딩(트랜스코딩)을 "
        "실행할 때 걸리는 예상 인코딩 소요 시간입니다.</b>"
        "</div>"
        "<div class='formula'>"
        "상대 작업량 배수(W<sub>ratio</sub>) = ( K<sub>면적</sub>(현재) × K<sub>fps</sub>(현재) ) ÷ "
        f"( K<sub>면적</sub>({REFERENCE_ENCODE_WIDTH}×{REFERENCE_ENCODE_HEIGHT}) × K<sub>fps</sub>({bm.base_fps:g}) )<br/>"
        f"예상 인코딩 소요(초) = ( 영상 재생 길이(초) × W<sub>ratio</sub> ) ÷ 기준 배속({eff.encode_speed_factor:.1f}배속)"
        "</div>"
        "<ul>"
        f"<li><b>해석</b>: <code>1080p {bm.base_fps:g}fps</code>를 작업량 <code>1.0배</code>로 두고, 4K, 고주사율 영상은 "
        f"화소, 프레임 증가에 비례해 작업량이 늘어나며, 설정된 <b>목표 코덱(<code>{codec_label}</code>) 기준 배속</b>"
        f"(<code>{eff.encode_speed_factor:.1f}배속</code>)으로 나누어 산출합니다. 예컨대 <code>2.0배속</code>이면 "
        "1080p 10분 영상 인코딩에 약 5분이 걸립니다.</li>"
        "<li><b>집계 기준</b>: 상단 <b>인코딩 작업 분량</b> 카드의 예상 소요는 <b>실제 인코딩 대상 파일만</b> 합산합니다. "
        "테이블의 원본 보존 행에 회색으로 표시되는 시간은 \"만약 이 파일을 인코딩했다면\" 기준의 참고 시간이며, "
        "행을 선택했을 때 하단 푸터 툴팁에서 <b>실제 인코딩 대상 기준 소요 시간</b>도 별도로 확인할 수 있습니다.</li>"
        "</ul>"
    )


def _build_syntax_shortcuts_html(p: ThemePalette) -> str:
    attrs, funcs, methods = get_exception_syntax_reference()
    attrs_html = ", ".join(f"<code>{html.escape(a)}</code>" for a in attrs)
    funcs_html = ", ".join(f"<code>{html.escape(fn)}</code>" for fn in funcs)
    methods_html = ", ".join(f"<code>{html.escape(m)}()</code>" for m in methods)
    shortcut_rows = "".join(
        f"<tr><td><code>{html.escape(key)}</code></td><td>{html.escape(desc)}</td></tr>"
        for key, desc in HELP_SHORTCUT_ITEMS
    )
    return (
        f"{_render_styles(p)}"
        "<h2>예외 조건식 문법, 단축키</h2>"
        "<h3>1. 예외 목록 규칙 (키워드, 안전 조건식)</h3>"
        "<p>일치하는 영상은 화질, 키프레임 판정과 무관하게 인코딩 대상에서 제외되어 원본으로 보존됩니다. "
        "<b>예외 목록(Ctrl+Shift+E)</b>이나 우클릭 메뉴에서 관리할 수 있습니다.</p>"
        "<ul>"
        "<li><b>파일명 키워드</b>: 일반 문자열을 입력하면 대소문자 구분 없이 파일명 부분 일치로 제외합니다. "
        "(예: <code>sample, 무손실, keep_origin</code>)</li>"
        "<li><b>안전 조건식 (<code>v.*</code> 또는 <code>lambda v: ...</code>)</b>: 속성 비교식으로 정밀하게 제외할 수 있습니다.<br/>"
        "예: <code>v.duration &lt; 10</code>, <code>v.fps &gt;= 60</code>, "
        "<code>v.codec == 'hevc' and v.width &gt;= 3840</code>, "
        "<code>lambda v: v.vid_size_MB &lt; 50 or v.filename.startswith('raw_')</code></li>"
        f"<li><b>허용 속성 (<code>v.*</code>)</b>: {attrs_html}</li>"
        f"<li><b>허용 내장 함수</b>: {funcs_html} / <b>문자열 메서드</b>: {methods_html}</li>"
        "</ul>"
        "<h3>2. 키보드 단축키 및 탐색 팁</h3>"
        "<p>테이블 열 헤더 우클릭 또는 <b>보기 &gt; 표시할 열</b>에서 기본 숨김 열(<code>최적 BR</code>, <code>BR 비율</code> 등)을 켜거나 "
        "열별 필터 팝오버, 폴더 드래그 앤 드롭을 바로 활용할 수 있습니다.</p>"
        "<table>"
        "<thead><tr><th style='width:30%'>단축키 / 조작</th><th>동작</th></tr></thead>"
        f"<tbody>{shortcut_rows}</tbody>"
        "</table>"
    )


class HelpDialog(QDialog, ThemeAwareMixin):
    """프로그램 목적, 핵심 KPI 개념, 예외 조건식 및 단축키를 안내하는 비모달 가이드 대화상자"""

    TAB_GUIDE: int = 0
    TAB_CONCEPTS: int = 1
    TAB_SYNTAX_SHORTCUTS: int = 2

    tab_changed = pyqtSignal(int)

    def __init__(
        self,
        config: ClassifierConfig | None = None,
        *,
        active_tab: int = TAB_GUIDE,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("사용 가이드/핵심 개념")
        self.resize(_DIALOG_DEFAULT_WIDTH, _DIALOG_DEFAULT_HEIGHT)
        self.setMinimumSize(_DIALOG_MIN_WIDTH, _DIALOG_MIN_HEIGHT)
        self._is_loading: bool = True

        self._config: ClassifierConfig = (
            config.copy() if config is not None else ClassifierConfig()
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*_DIALOG_MARGINS)
        layout.setSpacing(_DIALOG_SPACING)

        self.tab_widget = QTabWidget(self)
        self.tab_widget.setObjectName("SettingsTabs")
        self.tab_widget.setDocumentMode(True)
        self.tab_widget.currentChanged.connect(self._on_tab_widget_changed)
        tab_bar = self.tab_widget.tabBar()
        if tab_bar is not None:
            tab_bar.setDrawBase(False)

        self.browser_guide = self._create_browser()
        self.browser_concepts = self._create_browser()
        self.browser_syntax = self._create_browser()

        self.tab_widget.addTab(self.browser_guide, "프로그램 안내")
        self.tab_widget.addTab(self.browser_concepts, "핵심 개념/KPI")
        self.tab_widget.addTab(self.browser_syntax, "예외 조건식/단축키")
        layout.addWidget(self.tab_widget, stretch=1)

        self.set_active_tab(active_tab)
        self.init_theme_listener()
        self._is_loading = False

    @staticmethod
    def _create_browser() -> QTextBrowser:
        browser = QTextBrowser()
        browser.setObjectName("HelpViewer")
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)
        font = QFont(FONT_UI_FAMILY, _HELP_BODY_FONT_PT)
        font.setStyleStrategy(
            QFont.StyleStrategy.PreferAntialias | QFont.StyleStrategy.PreferQuality
        )
        browser.setFont(font)
        doc = browser.document()
        if doc is not None:
            doc.setDefaultFont(font)
        return browser

    @classmethod
    def normalize_tab_index(cls, index: int) -> int:
        """허용된 도움말 탭 범위(0~2)로 인덱스 정규화"""
        if index in (cls.TAB_GUIDE, cls.TAB_CONCEPTS, cls.TAB_SYNTAX_SHORTCUTS):
            return index
        return cls.TAB_GUIDE

    def _on_tab_widget_changed(self, index: int) -> None:
        if self._is_loading:
            return
        self.tab_changed.emit(self.normalize_tab_index(index))

    def set_active_tab(self, index: int) -> None:
        """지정된 도움말 탭 인덱스로 전환"""
        prev = self._is_loading
        self._is_loading = True
        try:
            self.tab_widget.setCurrentIndex(self.normalize_tab_index(index))
        finally:
            self._is_loading = prev

    def update_config(self, config: ClassifierConfig) -> None:
        """변경된 판별 기준, 분류 폴더명 설정을 가이드 본문에 즉시 반영"""
        self._config = config.copy()
        self._render_contents(ThemeManager().current_palette)

    def _render_contents(self, palette: ThemePalette) -> None:
        self.browser_guide.setHtml(_build_guide_html(palette, self._config))
        self.browser_concepts.setHtml(_build_concepts_html(palette, self._config))
        self.browser_syntax.setHtml(_build_syntax_shortcuts_html(palette))

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 스타일시트 및 HTML 고대비 색상 갱신"""
        self.setStyleSheet(build_stylesheet(palette))
        self._render_contents(palette)
