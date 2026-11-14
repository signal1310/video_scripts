"""프로그램 목적, 핵심 개념(KPI·최적 BR), 예외 조건식 및 단축키 통합 가이드 대화상자 모듈"""

from __future__ import annotations

import html
from collections.abc import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QPushButton,
    QTabWidget,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from src.core.config import ClassifierConfig
from src.core.exception_rules import get_exception_syntax_reference
from src.core.messages import format_over_bitrate_desc
from src.gui.styles.icons import get_icon
from src.gui.styles.palettes import ThemePalette
from src.gui.styles.stylesheet import build_stylesheet
from src.gui.theme import ThemeAwareMixin, ThemeManager

_DIALOG_DEFAULT_WIDTH: int = 700
_DIALOG_DEFAULT_HEIGHT: int = 560
_DIALOG_MIN_WIDTH: int = 540
_DIALOG_MIN_HEIGHT: int = 420
_DIALOG_MARGINS: tuple[int, int, int, int] = (14, 12, 14, 14)
_DIALOG_SPACING: int = 10
_PRESET_ICON_SIZE: int = 14

HELP_SHORTCUT_ITEMS: tuple[tuple[str, str], ...] = (
    ("Ctrl+O", "작업 폴더 열기"),
    ("Ctrl+E", "현재 필터·정렬된 테이블을 CSV로 내보내기"),
    ("Ctrl+Q", "애플리케이션 종료"),
    ("Ctrl+F", "파일명·코덱 검색 입력창 포커스"),
    ("Ctrl+T", "화질 · 효율 기준 실시간 조정 탭 열기"),
    ("Ctrl+Shift+E", "여러 줄 예외 규칙 목록 편집기 열기"),
    ("Ctrl+,", "환경 · 세부 설정 탭 열기"),
    ("Ctrl+L", "작업 로그 뷰어 열기"),
    ("F5 / Shift+F5", "현재 모드로 스캔 / 캐시 무시 강제 재스캔"),
    ("Ctrl+Shift+C", "선택한 행의 전체 절대 경로 클립보드 복사"),
    ("Enter / 더블클릭", "선택한 영상을 기본 미디어 플레이어로 재생"),
    ("Esc", "실행 중인 스캔·분류 작업 즉시 중단"),
    ("F1", "사용 가이드 · 예외 조건식·단축키 창 열기"),
)


def _render_styles(p: ThemePalette) -> str:
    return (
        "<style>"
        f"body {{ color: {p.text_primary}; line-height: 1.55; margin: 0; }}"
        f"h2 {{ color: {p.accent_blue}; font-size: 12.5pt; margin: 12px 0 6px 0; }}"
        f"h3 {{ color: {p.text_primary}; font-size: 10.5pt; margin: 12px 0 4px 0; }}"
        f"p {{ color: {p.text_secondary}; margin: 4px 0 8px 0; }}"
        f"ul, ol {{ color: {p.text_secondary}; margin: 4px 0 10px 0; padding-left: 18px; }}"
        "li { margin-bottom: 4px; }"
        f"b, strong {{ color: {p.text_primary}; }}"
        f"code {{ background-color: {p.bg_elevated}; color: {p.text_primary}; "
        "padding: 1px 5px; border-radius: 4px; font-family: Consolas, monospace; font-size: 9pt; }}"
        f".callout {{ background-color: {p.bg_elevated}; border: 1px solid {p.border_subtle}; "
        "border-radius: 8px; padding: 10px 12px; margin: 8px 0 12px 0; }}"
        "table { border-collapse: collapse; width: 100%; margin: 6px 0 10px 0; }"
        f"th, td {{ border: 1px solid {p.border_subtle}; padding: 6px 8px; text-align: left; }}"
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
        "<b>빠른 스캔</b>(해상도·비트레이트 고속 조회) 또는 <b>정밀 분석</b>(키프레임 간격 실측 포함)을 실행합니다.</li>"
        "<li><b>기준 실시간 조정 및 예외 지정</b>: <b>화질 · 효율 기준(Ctrl+T)</b> 슬라이더나 "
        "<b>예외 목록(Ctrl+Shift+E)</b>을 바꾸면 재스캔 없이 결과를 즉시 미리 볼 수 있습니다.</li>"
        "<li><b>폴더 분류 이동</b>: 하단 <b>폴더 분류 이동</b>을 실행하면 대상 파일만 하위 폴더로 이동하며, "
        "이름이 겹치는 기존 파일은 절대 덮어쓰지 않습니다. (<b>분류 복원</b>으로 언제든 원위치 복구가 가능합니다.)</li>"
        "<li><b>샤나인코더 일괄 인코딩</b>: 하단 또는 도움말 메뉴의 <b>샤나인코더 프리셋 폴더 열기</b>로 "
        "<code>shana_presets</code> XML 프리셋을 불러와 분류 폴더별로 1:1 인코딩을 실행합니다.</li>"
        "</ol>"
        "<h3>분류 폴더별 역할</h3>"
        "<ul>"
        f"<li><code>{std_dir}</code>: <b>과화질</b>(용량 대폭 감축) 또는 "
        "<b>적정화질 + 키프레임 불량</b> 영상이 이동합니다. 원본 해상도·화면 비율을 유지한 채 "
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
    over_example = html.escape(
        format_over_bitrate_desc(cfg.bitrate_model, t.over_bitrate_ratio)
    )
    exempt_line = (
        f"단, 총 예상 절감량이 <b>{eff.exempt_saved_mb:.0f} MB 이상</b>이면 시간 효율과 무관하게 포함합니다."
        if eff.exempt_saved_mb > 0
        else "현재 대용량 절감 예외 면제는 비활성화되어 있습니다."
    )
    return (
        f"{_render_styles(p)}"
        "<h2>핵심 개념과 판별 원리</h2>"
        "<h3>1. 최적 비트레이트(최적 BR)와 CQ22</h3>"
        "<p><b>CQ22</b>는 육안으로 원본과 차이를 거의 느끼기 어려운 고화질 고정 품질 목표를 뜻합니다. "
        "같은 화질을 내는 데 필요한 비트레이트는 <b>해상도·프레임레이트(FPS)·코덱(AV1/HEVC/H.264 등)</b>에 따라 달라지므로, "
        "각 영상의 조건에 맞춰 계산한 기준선이 <b>최적 BR</b>입니다.</p>"
        "<ul>"
        f"<li><b>BR 비율</b> = <code>현재 비트레이트 ÷ 최적 BR</code></li>"
        f"<li><b>과화질</b> (<code>{t.over_bitrate_ratio:.2f}배 초과</code>): 필요 이상으로 용량이 큰 상태입니다. "
        f"(예: {over_example})</li>"
        f"<li><b>적정화질</b> (<code>{t.low_bitrate_ratio:.2f}~{t.over_bitrate_ratio:.2f}배</code>): 균형 잡힌 화질이므로 "
        "키프레임이 정상이면 원본을 유지합니다.</li>"
        f"<li><b>저화질</b> (<code>{t.low_bitrate_ratio:.2f}배 미만</code>): 이미 많이 압축된 영상이므로 "
        "불필요한 재인코딩으로 화질이 더 떨어지지 않게 보호합니다.</li>"
        "</ul>"
        "<h3>2. 키프레임 간격과 용량 증가(+)의 이유</h3>"
        f"<p>키프레임은 영상을 건너뛰기(탐색)할 때 즉시 화면을 복원할 수 있는 기준 프레임입니다. "
        f"간격이 <b>{t.keyframe_interval_sec:.1f}초</b>를 넘으면 탐색 시 화면이 잠깐 멈추거나 깨질 수 있습니다.</p>"
        "<p>1초마다 기준 프레임을 심으면 탐색은 매끄러워지지만 프레임 수만큼 용량이 소폭 늘어날 수 있습니다(<code>+</code>). "
        "상단 <b>예상 용량 절감</b> 카드는 과화질 감축분(<code>-</code>)과 키프레임 교정 증가분(<code>+</code>)을 합산한 "
        "<b>순수 절감량(Net)</b>을 보여줍니다.</p>"
        "<h3>3. 인코딩 작업 분량과 제외된 과화질 (가성비 선별)</h3>"
        "<ul>"
        "<li><b>인코딩 작업 분량</b>: 인코딩 대상 영상들의 <b>총 재생 길이</b>입니다. "
        "내가 감당할 인코딩 작업 규모와 1분당 평균 절감 효율(<code>MB/분</code>)을 가늠하는 지표입니다.</li>"
        f"<li><b>제외된 과화질</b>: 과화질이더라도 예상 절감량이 <b>{eff.min_saved_mb:.1f} MB</b> 미만이거나 "
        f"재생 1분당 절감량이 <b>{eff.min_mb_per_min:.1f} MB/분</b> 미만이면, 긴 인코딩 시간 대비 얻는 용량 이득이 작아 "
        f"자동으로 제외하고 원본을 유지합니다. ({exempt_line})</li>"
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
        "<h2>예외 조건식 문법 · 단축키</h2>"
        "<h3>1. 예외 목록 규칙 (키워드 · 안전 조건식)</h3>"
        "<p>일치하는 영상은 화질·키프레임 판정과 무관하게 인코딩 대상에서 제외되어 원본으로 보존됩니다. "
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
        "열별 필터 팝오버·폴더 드래그 앤 드롭을 바로 활용할 수 있습니다.</p>"
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

    def __init__(
        self,
        config: ClassifierConfig | None = None,
        *,
        active_tab: int = TAB_GUIDE,
        on_open_presets_dir: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsDialog")
        self.setWindowTitle("사용 가이드 · 핵심 개념")
        self.resize(_DIALOG_DEFAULT_WIDTH, _DIALOG_DEFAULT_HEIGHT)
        self.setMinimumSize(_DIALOG_MIN_WIDTH, _DIALOG_MIN_HEIGHT)

        self._config: ClassifierConfig = (
            config.copy() if config is not None else ClassifierConfig()
        )
        self._on_open_presets_dir = on_open_presets_dir

        layout = QVBoxLayout(self)
        layout.setContentsMargins(*_DIALOG_MARGINS)
        layout.setSpacing(_DIALOG_SPACING)

        self.tab_widget = QTabWidget(self)
        self.tab_widget.setObjectName("SettingsTabs")
        self.tab_widget.setDocumentMode(True)
        tab_bar = self.tab_widget.tabBar()
        if tab_bar is not None:
            tab_bar.setDrawBase(False)

        self.browser_guide = self._create_browser()
        self.browser_concepts = self._create_browser()
        self.browser_syntax = self._create_browser()

        self.tab_widget.addTab(self.browser_guide, "프로그램 안내")
        self.tab_widget.addTab(self.browser_concepts, "핵심 개념 · KPI")
        self.tab_widget.addTab(self.browser_syntax, "예외 조건식 · 단축키")
        layout.addWidget(self.tab_widget, stretch=1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        self.btn_open_presets = QPushButton("샤나인코더 프리셋 폴더 열기", self)
        self.btn_open_presets.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open_presets.setToolTip(
            "shana_presets 폴더를 열어 분류 폴더와 매칭되는 XML 프리셋을 확인합니다."
        )
        if on_open_presets_dir is not None:
            self.btn_open_presets.clicked.connect(on_open_presets_dir)
        else:
            self.btn_open_presets.setVisible(False)
        footer.addWidget(self.btn_open_presets)
        footer.addStretch(1)

        self.btn_close = QPushButton("닫기", self)
        self.btn_close.setObjectName("PrimaryButton")
        self.btn_close.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_close.clicked.connect(self.close)
        footer.addWidget(self.btn_close)
        layout.addLayout(footer)

        self.set_active_tab(active_tab)
        self.apply_theme(ThemeManager().current_palette)
        self.init_theme_listener()

    @staticmethod
    def _create_browser() -> QTextBrowser:
        browser = QTextBrowser()
        browser.setObjectName("HelpViewer")
        browser.setOpenExternalLinks(False)
        browser.setOpenLinks(False)
        return browser

    @classmethod
    def normalize_tab_index(cls, index: int) -> int:
        """허용된 도움말 탭 범위(0~2)로 인덱스 정규화"""
        if index in (cls.TAB_GUIDE, cls.TAB_CONCEPTS, cls.TAB_SYNTAX_SHORTCUTS):
            return index
        return cls.TAB_GUIDE

    def set_active_tab(self, index: int) -> None:
        """지정된 도움말 탭 인덱스로 전환"""
        self.tab_widget.setCurrentIndex(self.normalize_tab_index(index))

    def update_config(self, config: ClassifierConfig) -> None:
        """변경된 판별 기준·분류 폴더명 설정을 가이드 본문에 즉시 반영"""
        self._config = config.copy()
        self._render_contents(ThemeManager().current_palette)

    def _render_contents(self, palette: ThemePalette) -> None:
        self.browser_guide.setHtml(_build_guide_html(palette, self._config))
        self.browser_concepts.setHtml(_build_concepts_html(palette, self._config))
        self.browser_syntax.setHtml(_build_syntax_shortcuts_html(palette))

    def apply_theme(self, palette: ThemePalette) -> None:
        """활성 테마 팔레트에 맞춰 스타일시트·아이콘 및 HTML 고대비 색상 갱신"""
        self.setStyleSheet(build_stylesheet(palette))
        self.btn_open_presets.setIcon(
            get_icon("folder", palette=palette, size=_PRESET_ICON_SIZE, pad_right=6)
        )
        self._render_contents(palette)
