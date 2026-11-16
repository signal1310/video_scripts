"""사용 가이드의 프로그램 안내, 고급 활용(예외 규칙, 단축키) 탭 HTML 조립 모듈"""

from __future__ import annotations

import html
from collections.abc import Mapping

from src.core.config import ClassifierConfig
from src.core.exception_rules import get_exception_syntax_reference
from src.core.shortcuts import (
    DEFAULT_SHORTCUTS,
    REDO_SECONDARY_SHORTCUT,
    SHORTCUT_SPECS,
    find_conflict_action,
)
from src.core.size_estimator import DEFAULT_UNKNOWN_KF_SEC
from src.gui.dialogs.help_blocks import (
    GAP_HTML,
    callout_html,
    card_html,
    inline_cmd,
    render_styles,
    striped_table_html,
)
from src.gui.styles.palettes import ThemePalette

_SYNTAX_COLUMN_WIDTH_PCT: int = 36
_PLAY_ACTION_ID: str = "classify.play_selected"
_REDO_ACTION_ID: str = "edit.redo"
_PLAY_DOUBLE_CLICK_TEXT: str = "더블클릭"


def build_shortcut_table_rows(shortcuts: Mapping[str, str]) -> list[tuple[str, str]]:
    """유효 단축키를 도움말 표의 (키, 설명) 쌍으로 변환하며 단축키가 없는 명령은 표에서 뺌

    - 다시 실행은 기본 키와 보조 키가 모두 동작할 때 두 키를 함께 표시
    - 선택 영상 재생은 더블클릭이 항상 동작하므로 키 유무와 관계없이 표시
    """
    rows: list[tuple[str, str]] = []
    for spec in SHORTCUT_SPECS:
        key = shortcuts.get(spec.action_id, "").strip()
        if spec.action_id == _PLAY_ACTION_ID:
            label = f"{key} / {_PLAY_DOUBLE_CLICK_TEXT}" if key else _PLAY_DOUBLE_CLICK_TEXT
            rows.append((label, spec.description))
            continue
        if not key:
            continue
        if (
            spec.action_id == _REDO_ACTION_ID
            and key == DEFAULT_SHORTCUTS[_REDO_ACTION_ID]
            and find_conflict_action(shortcuts, _REDO_ACTION_ID, REDO_SECONDARY_SHORTCUT) is None
        ):
            key = f"{key} / {REDO_SECONDARY_SHORTCUT}"
        rows.append((key, spec.description))
    return rows


def _basics_html() -> str:
    return (
        "<h3>먼저 알아두면 좋은 영상 상식 3가지</h3>"
        "<p>이 프로그램의 판정은 아래 세 가지 개념 위에서 움직입니다. "
        "이 정도만 알면 이후 화면의 용어가 훨씬 쉽게 읽힙니다.</p>"
        "<ul>"
        "<li><b>비트레이트</b>: 영상 1초를 표현하는 데 쓰는 데이터량입니다(단위 kbps). "
        "수도꼭지에서 1초에 흘러나오는 물의 양과 비슷하며, 높을수록 용량이 커집니다. "
        "다만 일정 수준을 넘으면 눈에 보이는 화질은 거의 늘지 않고 용량만 낭비됩니다.</li>"
        "<li><b>코덱</b>: 영상을 압축하는 방식입니다. H.264, HEVC(H.265), AV1 순으로 새로울수록 "
        "같은 화질을 더 적은 비트레이트로 담습니다. 그래서 코덱마다 &quot;충분한 비트레이트&quot;의 기준이 다릅니다.</li>"
        "<li><b>키프레임</b>: 영상은 모든 장면을 통째로 저장하지 않고, 가끔 <b>온전한 한 장</b>(키프레임)만 저장한 뒤 "
        "그 사이는 &quot;앞 장면과 달라진 부분&quot;만 기록합니다. 책갈피가 촘촘할수록 원하는 쪽을 빨리 펼치듯, "
        "키프레임 간격이 길면 구간 이동 직후 플레이어가 직전 키프레임부터 다시 계산하느라 멈칫거립니다.</li>"
        "</ul>"
    )


def _steps_html(shortcuts: Mapping[str, str]) -> str:
    tuner = inline_cmd("판별 기준", shortcuts, "edit.open_tuner")
    rules = inline_cmd("예외 규칙", shortcuts, "edit.exception_editor")
    return (
        "<h3>기본 작업 흐름 (4단계)</h3>"
        "<ol>"
        "<li><b>작업 폴더 선택 및 분석</b>: 상단 폴더 버튼을 누르거나 창으로 폴더를 드래그 앤 드롭한 뒤 "
        "분석을 실행합니다. <b>빠른 분석</b>은 해상도, 비트레이트만 빠르게 조회하고, "
        "<b>정밀 분석</b>은 키프레임 간격까지 실제로 측정합니다. "
        "구간 이동 문제까지 정확히 가려내려면 정밀 분석을 권장합니다. "
        "분석은 영상 정보를 읽기만 하며 파일을 바꾸지 않습니다.</li>"
        f"<li><b>기준 실시간 조정 및 예외 지정</b>: <b>{tuner}</b> 슬라이더나 "
        f"<b>{rules}</b>을 바꾸면 재분석 없이 결과를 즉시 미리 볼 수 있습니다. "
        "&quot;이 기준이면 몇 개가 대상이 될까&quot;를 부담 없이 시험해 보세요. "
        "영상별로 출력 해상도를 정하려면 행 우클릭 또는 <b>분류 &gt; 출력 해상도 지정</b>에서 규격을 고르세요. "
        "선택은 재분석 없이 바로 반영되고 저장되며, <b>자동</b>으로 되돌리거나 <b>직접 지정 모두 해제</b>로 한꺼번에 풀 수 있습니다.</li>"
        "<li><b>분류 폴더로 이동</b>: 하단 <b>분류 폴더로 이동</b>을 실행하면 대상 파일만 하위 폴더로 이동하며, "
        "이름이 겹치는 기존 파일은 절대 덮어쓰지 않습니다. "
        "(<b>작업 폴더로 복원</b>으로 언제든 원위치 복구가 가능합니다.)</li>"
        "<li><b>샤나인코더 일괄 재인코딩</b>: 상단 <b>도움말 &gt; 샤나인코더 프리셋 폴더 열기</b> 메뉴에서 "
        "<code>shana_presets</code> XML 프리셋을 불러와 분류 폴더별로 1:1 재인코딩을 실행합니다. "
        "폴더 안의 영상은 모두 그 폴더의 프리셋으로 처리됩니다. "
        "이 프로그램은 분류까지만 맡고, 실제 인코딩은 샤나인코더가 수행합니다.</li>"
        "</ol>"
    )


def _folders_html(std_dir: str, low_dir: str, downscale_prefix: str) -> str:
    return (
        "<h3>분류 폴더별 역할</h3>"
        "<ul>"
        f"<li><code>{std_dir}</code>: <b>과화질</b>(용량 대폭 절약) 또는 "
        "<b>적정 + 키프레임 간격 초과</b> 영상이 이동합니다. 원본 해상도, 화면 비율을 유지한 채 "
        "<code>CQ 22</code> <code>+ 1초 키프레임</code> 프리셋으로 재인코딩합니다.</li>"
        f"<li><code>{low_dir}</code>: 이미 <b>저화질</b>이면서 키프레임 간격이 초과한 영상과 블록 노이즈 증폭 위험 구형 코덱, "
        "코덱 역전, 초저비트레이트 정적 화면 영상이 이동합니다. 노이즈 재현과 용량 폭증을 막는 <code>CQ 26 + 1초 키프레임</code> "
        "프리셋으로 재인코딩합니다.</li>"
        f"<li><code>{downscale_prefix}</code> + 규격 키(예: <code>720p</code>): 원본이 <b>최대 해상도</b>를 넘어 "
        "축소 재인코딩 대상이 된 영상이 규격별로 모입니다. 규격에 맞춰 줄인 해상도로 "
        "<code>CQ 22 + 최대 비트레이트 + 1초 키프레임</code> 프리셋을 적용합니다.</li>"
        "<li><b>작업 폴더 유지 (원본 보존)</b>: 화질과 키프레임이 모두 정상이거나, "
        "재인코딩해도 절약 효과가 기준 미만인 영상은 건드리지 않고 그대로 보존합니다.</li>"
        "</ul>"
        "<p><b>CQ(고정 화질)</b>는 &quot;화질 목표를 정해 두고 용량은 장면에 맞게 알아서 정하는&quot; 방식입니다. "
        "숫자가 작을수록 고화질, 대용량입니다. 22는 육안으로 원본과 구분하기 어려운 수준, "
        "26은 그보다 한 단계 가볍게 압축하는 값입니다.</p>"
    )


def _decision_table_html(p: ThemePalette) -> str:
    rows = (
        ("<b>HDR</b> (PQ, HLG, Dolby Vision)", "무관", "원본 보존 (HDR 보존, 화질 구간보다 우선)"),
        ("<b>최대 해상도 초과</b> (최대 해상도 설정 시)", "무관", "대상 (축소 재인코딩, 절약 기준 통과 시, 키프레임 초과 또는 구형 코덱 호환이면 기준과 무관)"),
        ("<b>구형 코덱 호환 옵션</b> (WMV3, VC-1, VP8 등)", "무관", "대상 (코덱 호환, 절약 기준 없이 재인코딩)"),
        ("<b>인트라 전용 코덱</b> (MJPEG, ProRes, DNxHD)", "무관", "대상 (코덱 전환, 절약 기준 통과 시)"),
        ("<b>과화질</b> (출력 코덱 기준)", "정상 또는 초과", "대상 (절약 기준 통과 시, 키프레임 초과면 기준과 무관)"),
        ("<b>적정</b> (코덱 압축 성능 차이 ≥ 1.25배, 기본 설정에서 H.264 원본 포함)", "정상", "대상 (코덱 전환, 절약 기준 통과 시), 미달이면 원본 보존 (전환 절약 미달)"),
        ("<b>적정</b>", "정상", "원본 보존"),
        ("<b>적정</b>", "초과", "대상 (CQ 22 또는 위험 시 CQ 26 + 1초 키프레임)"),
        ("<b>저화질</b> (원본 코덱 기준)", "정상", "원본 보존"),
        ("<b>저화질</b> (원본 코덱 기준)", "초과", "대상 (CQ 26 + 1초 키프레임)"),
    )
    return (
        "<h3>판정 한눈에 보기</h3>"
        "<p>영상마다 <b>화질 구간</b>(비트레이트가 충분한지)과 <b>키프레임 간격</b>(구간 이동이 쾌적한지)을 "
        "확인해 아래처럼 결정합니다. 저화질은 원본 코덱 기준, 과화질은 목표 인코딩 코덱 기준으로 나눕니다. "
        "예외 규칙에 걸린 영상과 HDR 영상은 이 표보다 먼저 무조건 원본으로 보존됩니다.</p>"
        + striped_table_html(p, ("화질 구간", "키프레임 간격", "결과"), rows, 22)
    )


def _faq_html() -> str:
    return (
        "<h3>자주 묻는 질문</h3>"
        "<ul>"
        "<li><b>어떤 영상은 왜 원본 그대로 남나요?</b> 화질과 키프레임이 이미 괜찮거나, 이미 많이 압축되어 "
        "더 줄이면 화질만 나빠지거나, 줄여도 얻는 용량이 너무 작거나, 예외 규칙에 걸린 경우입니다. "
        "표의 판정 사유 배지에서 이유를 확인할 수 있습니다.</li>"
        "<li><b>정밀 분석을 꼭 해야 하나요?</b> 용량 절약만 보려면 빠른 분석으로 충분합니다. "
        "다만 키프레임 간격은 정밀 분석에서만 실제로 측정되며, "
        f"측정 전에는 보수적인 기준({DEFAULT_UNKNOWN_KF_SEC:.1f}초)으로 예상 증가량을 어림합니다.</li>"
        "<li><b>출력 코덱을 바꾸면 결과가 달라지나요?</b> 네. 과화질 판정과 예상 용량은 설정한 목표 인코딩 코덱의 "
        "최적 비트레이트를 기준으로 계산합니다. 예를 들어 H.264 영상은 HEVC 출력 기준으로 더 많이 과화질로 잡히고, "
        "H.264 출력으로 바꾸면 적정으로 남을 수 있습니다. 저화질 판정은 원본 코덱 기준이라 바뀌지 않습니다.</li>"
        "<li><b>HDR 영상은 왜 원본으로 남나요?</b> HDR은 밝기와 색 정보를 함께 담는데, 현재 샤나인코더 프리셋은 "
        "8비트 main 프로파일이라 재인코딩하면 이 정보를 잃을 수 있습니다. 그래서 HDR 영상은 원본을 보존하고 "
        "요약 로그에 개수를 경고로 표시합니다.</li>"
        "<li><b>절약 선별 기준은 무엇인가요?</b> 기본값인 재생 기준은 재생 1분당 절약, 인코딩 기준은 예상 인코딩 "
        "1분당 절약으로 판단합니다. 재생 기준만 대용량 절약 예외를 적용합니다. 설정의 절약 선별 기준에서 고를 수 있습니다.</li>"
        "<li><b>잘못 이동했어요.</b> <b>작업 폴더로 복원</b>을 실행하면 분류 폴더의 파일이 원래 위치로 돌아옵니다.</li>"
        "<li><b>숫자나 용어가 낯설어요.</b> <b>판정 원리</b> 탭에서 계산 원리를, <b>용어 사전</b> 탭에서 "
        "화면에 나오는 낱말의 뜻을 찾아볼 수 있습니다.</li>"
        "</ul>"
    )


def build_guide_html(
    p: ThemePalette,
    cfg: ClassifierConfig,
    shortcuts: Mapping[str, str] | None = None,
) -> str:
    """프로그램 안내 탭, 목적, 영상 상식, 작업 흐름, 분류 폴더, 판정표, 자주 묻는 질문"""
    effective = DEFAULT_SHORTCUTS if shortcuts is None else shortcuts
    std_dir = html.escape(cfg.dir_standard_cq22)
    low_dir = html.escape(cfg.dir_low_quality_keyframe)
    downscale_prefix = html.escape(cfg.dir_downscale_prefix)
    intro = (
        "보관 중인 영상들을 무작정 다시 인코딩하면 화질이 나빠지거나 시간만 낭비되기 쉽습니다. "
        "재인코딩은 이미 압축된 영상을 한 번 더 압축하는 일이라 할 때마다 화질이 조금씩 손상되기 때문입니다. "
        "이 도구는 <b>눈에 띄는 화질 손실 없이 용량을 줄일 수 있는 영상</b>과 "
        "<b>구간 이동(앞/뒤 건너뛰기)이 버벅이는 영상</b>만 정확히 골라내, "
        "샤나인코더 프리셋으로 바로 일괄 재인코딩할 수 있게 안전하게 분류해 줍니다."
    )
    return "".join(
        (
            render_styles(p),
            "<h2>이 프로그램은 왜 사용하나요?</h2>",
            callout_html(p, intro),
            _basics_html(),
            _steps_html(effective),
            _folders_html(std_dir, low_dir, downscale_prefix),
            _decision_table_html(p),
            _faq_html(),
        )
    )


def _rule_intro_html(shortcuts: Mapping[str, str]) -> str:
    rules = inline_cmd("예외 규칙", shortcuts, "edit.exception_editor")
    return (
        "<h2>예외 조건식 문법, 단축키</h2>"
        "<h3>1. 예외 규칙이란?</h3>"
        "<p>&quot;이 영상만큼은 건드리지 말아 주세요&quot;라고 미리 알려 두는 장치입니다. "
        "예외 규칙에 일치한 영상은 화질, 키프레임 판정과 무관하게 재인코딩 대상에서 제외되어 원본으로 보존됩니다. "
        "판정 순서상 <b>가장 먼저</b> 확인되므로 다른 기준보다 항상 우선합니다. "
        f"<b>{rules}</b>이나 우클릭 메뉴에서 관리할 수 있습니다.</p>"
        "<p>이런 영상에 쓰면 좋습니다: 편집 원본처럼 화질을 절대 낮추면 안 되는 파일, "
        "이미 직접 인코딩해 둔 파일, 용량이 작아 굳이 손댈 이유가 없는 짧은 클립.</p>"
    )


def _rule_kinds_html(p: ThemePalette) -> str:
    attrs, funcs, methods = get_exception_syntax_reference()
    attrs_html = ", ".join(f"<code>{html.escape(a)}</code>" for a in attrs)
    funcs_html = ", ".join(f"<code>{html.escape(fn)}</code>" for fn in funcs)
    methods_html = ", ".join(f"<code>{html.escape(m)}()</code>" for m in methods)
    rows = (
        ("10초 미만 짧은 영상", "<code>v.duration &lt; 10</code>"),
        ("60fps 이상 영상", "<code>v.fps &gt;= 60</code>"),
        ("4K 이상 HEVC 영상", "<code>v.codec == 'hevc' and v.width &gt;= 3840</code>"),
        (
            "50MB 미만이거나 raw_로 시작하는 파일",
            "<code>lambda v: v.vid_size_MB &lt; 50 or v.filename.startswith('raw_')</code>",
        ),
    )
    return "".join(
        (
            "<h3>2. 규칙을 쓰는 두 가지 방법</h3>",
            "<ul>"
            "<li><b>파일명 키워드</b>: 일반 문자열을 입력하면 대소문자 구분 없이 파일명에 "
            "그 글자가 들어 있는 영상을 제외합니다. 여러 개는 쉼표로 나눕니다. "
            "(예: <code>sample, 무손실, keep_origin</code>)</li>"
            "<li><b>안전 조건식</b>: <code>v.</code>로 시작하는 비교식을 쓰면 용량, 길이, 코덱처럼 "
            "영상의 속성으로 정밀하게 제외할 수 있습니다. <code>v</code>는 검사 중인 영상 한 개를 뜻하고, "
            "<code>v.fps</code>는 그 영상의 프레임레이트입니다.</li>"
            "</ul>",
            "<p><b>조건식 읽는 법</b>: <code>&lt;</code> 작다, <code>&lt;=</code> 작거나 같다, "
            "<code>&gt;</code> 크다, <code>&gt;=</code> 크거나 같다, <code>==</code> 같다, "
            "<code>!=</code> 다르다. 조건은 <code>and</code>(모두 만족), <code>or</code>(하나라도 만족)로 "
            "이을 수 있고, 문자열 값은 따옴표로 감쌉니다.</p>",
            striped_table_html(p, ("이럴 때", "이렇게 쓰세요"), rows, _SYNTAX_COLUMN_WIDTH_PCT),
            GAP_HTML,
            card_html(
                p,
                "<b>안전 조건식이란?</b> 입력한 식을 그대로 실행하지 않고, 아래 허용 목록에 있는 "
                "속성과 함수만 쓰도록 먼저 검사합니다. 목록 밖의 코드는 실행되지 않으므로 "
                "파일이나 시스템을 건드리는 식은 쓸 수 없습니다.",
            ),
            f"<ul><li><b>허용 속성 (<code>v.*</code>)</b>: {attrs_html}</li>"
            f"<li><b>허용 내장 함수</b>: {funcs_html}</li>"
            f"<li><b>허용 문자열 메서드</b>: {methods_html}</li></ul>",
        )
    )


def _shortcuts_html(p: ThemePalette, shortcuts: Mapping[str, str]) -> str:
    rows = tuple(
        (f"<code>{html.escape(key)}</code>", html.escape(desc))
        for key, desc in build_shortcut_table_rows(shortcuts)
    )
    return (
        "<h3>3. 키보드 단축키 및 조작 팁</h3>"
        "<p>테이블 열 헤더 우클릭 또는 <b>보기 &gt; 표시할 열</b>에서 기본 숨김 열"
        "(<code>최적 BR</code>, <code>BR 비율</code> 등)을 켜 판정 근거를 직접 확인할 수 있습니다. "
        "열별 필터 팝오버로 원하는 영상만 추려 보거나, 폴더를 창으로 끌어다 놓아 바로 열 수도 있습니다.</p>"
        + striped_table_html(p, ("단축키 / 조작", "동작"), rows, 30)
    )


def build_syntax_shortcuts_html(
    p: ThemePalette,
    shortcuts: Mapping[str, str] | None = None,
) -> str:
    """고급 활용 탭, 예외 규칙의 개념, 작성법, 예시, 허용 문법과 단축키 표"""
    effective = DEFAULT_SHORTCUTS if shortcuts is None else shortcuts
    return "".join(
        (
            render_styles(p),
            _rule_intro_html(effective),
            _rule_kinds_html(p),
            _shortcuts_html(p, effective),
        )
    )
