"""사용 가이드의 프로그램 안내, 고급 활용(예외 규칙, 단축키) 탭 HTML 조립 모듈"""

from __future__ import annotations

import html
from collections.abc import Mapping

from src.core.evaluation.exception_rules import get_exception_syntax_reference
from src.core.evaluation.size_estimator import DEFAULT_UNKNOWN_KF_SEC
from src.core.settings.shortcuts import (
    DEFAULT_SHORTCUTS,
)
from src.gui.help.help_blocks import (
    GAP_HTML,
    callout_html,
    card_html,
    inline_cmd,
    render_styles,
    striped_table_html,
)
from src.gui.styles.palettes import ThemePalette

_SYNTAX_COLUMN_WIDTH_PCT: int = 36


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
        "영상별로 출력 해상도를 정하려면 행 우클릭 또는 <b>편집 &gt; 출력 해상도 지정</b>에서 규격을 고르세요. "
        "선택은 재분석 없이 바로 반영되고 저장되며, <b>자동</b>으로 되돌리거나 <b>직접 지정 모두 해제</b>로 한꺼번에 풀 수 있습니다.</li>"
        "<li><b>판정 확인</b>: 표의 <b>권장 처리</b>와 <b>판정 사유</b> 열로 영상별 판정을 확인합니다. "
        "이 프로그램은 판정까지만 맡으며 파일을 이동하거나 바꾸지 않습니다.</li>"
        "</ol>"
    )


def _undo_html(shortcuts: Mapping[str, str]) -> str:
    undo = inline_cmd("되돌리기", shortcuts, "edit.undo")
    redo = inline_cmd("다시 실행", shortcuts, "edit.redo")
    return (
        "<h3>실수했을 때: 되돌리기와 다시 실행</h3>"
        f"<p><b>{undo}</b>로 직전 변경을 취소하고, <b>{redo}</b>로 취소한 변경을 다시 적용합니다. "
        "최대 50단계까지 기록하며, 메뉴의 편집 항목에서 다음에 되돌릴 내용을 확인할 수 있습니다.</p>"
        "<p><b>되돌릴 수 있는 변경</b></p>"
        "<ul>"
        "<li>출력 해상도 지정과 직접 지정 모두 해제</li>"
        "<li>예외 규칙 편집과 변경</li>"
        "<li>판별 기준 조정 (연속으로 움직인 슬라이더는 한 단계로 묶입니다)</li>"
        "<li>환경 설정 변경과 전체 초기화</li>"
        "<li>단축키 지정, 해제와 초기화</li>"
        "<li>열 너비, 열 표시, 열 순서 변경과 기본 열 너비, 표시 복원</li>"
        "</ul>"
        "<p><b>되돌릴 수 없는 작업</b></p>"
        "<ul>"
        "<li>분석 실행, 정렬, 열 필터, 상단 영역 접기</li>"
        "</ul>"
        "<p>입력칸(검색어 등)에 커서가 있으면 그 칸의 되돌리기가 먼저 동작합니다. "
        "작업 폴더를 바꾸면 이전 폴더의 되돌리기 기록은 지워집니다.</p>"
    )


def _decision_table_html(p: ThemePalette) -> str:
    rows = (
        ("<b>HDR</b> (PQ, HLG, Dolby Vision)", "무관", "원본 보존 (HDR 보존, 화질 구간보다 우선)"),
        ("<b>최대 해상도 초과</b> (최대 해상도 설정 시)", "무관", "대상 (축소 인코딩, 절약 기준 통과 시, 키프레임 초과 또는 구형 코덱 호환이면 기준과 무관)"),
        ("<b>구형 코덱 호환 옵션</b> (WMV3, VC-1, VP8 등)", "무관", "대상 (코덱 호환, 절약 기준 없이 인코딩)"),
        ("<b>인트라 전용 코덱</b> (MJPEG, ProRes, DNxHD)", "무관", "대상 (코덱 전환, 절약 기준 통과 시)"),
        ("<b>과화질</b> (출력 코덱 기준)", "정상 또는 초과", "대상 (절약 기준 통과 시, 키프레임 초과면 기준과 무관)"),
        ("<b>적정</b> (코덱 압축 성능 차이 ≥ 1.25배, 기본 설정에서 H.264 원본 포함)", "정상", "대상 (코덱 전환, 절약 기준 통과 시), 미달이면 원본 보존 (전환 절약 미달)"),
        ("<b>적정</b>", "정상", "원본 보존"),
        ("<b>적정</b>", "초과", "대상 (품질 22 또는 위험 시 품질 26 + 1초 키프레임)"),
        ("<b>저화질</b> (원본 코덱 기준)", "정상", "원본 보존"),
        ("<b>저화질</b> (원본 코덱 기준)", "초과", "대상 (품질 26 + 1초 키프레임)"),
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
        "<li><b>HDR 영상은 왜 원본으로 남나요?</b> HDR은 밝기와 색 정보를 함께 담는데, 현재 인코딩 설정은 "
        "8비트 main 프로파일이라 인코딩하면 이 정보를 잃을 수 있습니다. 그래서 HDR 영상은 원본을 보존하고 "
        "요약 로그에 개수를 경고로 표시합니다.</li>"
        "<li><b>절약 선별 기준은 무엇인가요?</b> 기본값인 재생 시간 기준은 재생 시간 1분당 절약, 예상 인코딩 시간 기준은 예상 인코딩 "
        "1분당 절약으로 판단합니다. 재생 시간 기준만 대용량 절약 예외를 적용합니다. 설정의 절약 선별 기준에서 고를 수 있습니다.</li>"
        "<li><b>숫자나 용어가 낯설어요.</b> <b>판정 원리</b> 탭에서 계산 원리를, <b>용어 사전</b> 탭에서 "
        "화면에 나오는 낱말의 뜻을 찾아볼 수 있습니다.</li>"
        "</ul>"
    )


def _quality_analysis_html() -> str:
    return (
        "<h3>화질 분석과 육안 확인</h3>"
        "<p>분석 메뉴의 <b>선택 영상 화질 분석</b>과 <b>전체 영상 화질 분석</b>은 영상에서 검은 띠를 뺀 "
        "<b>활성 화면</b>을 찾고, 그 화면을 규격별로 줄였다 되돌렸을 때의 화질 차이를 재어 "
        "<b>축소 후보</b> 해상도를 제안합니다. 결과는 표의 활성 화면, 축소 후보, 측정 지표 열에 표시되며, "
        "측정 지표 열은 보기 메뉴의 표시할 열에서 켤 수 있습니다.</p>"
        "<ul>"
        "<li><b>링잉과 할로(경계 이중선)</b>: 물체나 글자 경계에 얇은 선이 겹쳐 보이면 축소와 인코딩으로 "
        "경계가 뭉개진 것입니다. 원본과 결과를 같은 화면 크기로 나란히 두고 경계를 확인하세요.</li>"
        "<li><b>뭉개짐과 계단 현상</b>: 세부 무늬가 사라지거나 대각선이 계단처럼 보이면 화질이 낮아진 것입니다. "
        "얼굴, 머리카락, 잔디처럼 세밀한 부분을 확대해 확인하세요.</li>"
        "<li><b>화면 대비 과도하게 큰 블록</b>: 같은 색 넓은 면이 눈에 띄는 사각형으로 갈라지면 압축 손실이 큽니다. "
        "하늘, 벽, 어두운 장면처럼 완만한 면에서 확인하세요.</li>"
        "<li><b>화질 비교 창</b>: 내장 <b>화질 비교</b> 창에서 원본과 결과를 같은 시점의 정지 프레임으로 나란히 봅니다. "
        "원본 픽셀 1:1부터 200%, 400%까지 확대하고, 분할선, 즉시 전환, 좌우 비교 세 방식으로 나눠 볼 수 있습니다. "
        "미리 보기 창의 화질 비교 버튼은 지금 재생 시점으로, 메인 표 우클릭과 인코딩 진행 창 결과 목록은 그 영상으로 엽니다. "
        "축소 비교 항목은 원본 프레임을 그 규격으로 줄였다 원본 크기로 되돌린 프레임이라 축소 손실을 직접 확인할 수 있습니다. "
        "외부 플레이어도 함께 열어 1:1로 확인할 수 있습니다.</li>"
        "</ul>"
        "<p>화질 분석은 판정을 돕는 추정값입니다. 축소 후보가 있어도 육안 확인에서 손실이 보이면 "
        "출력 해상도 지정에서 <b>축소 안 함</b>을 고르거나 후보 적용을 하지 않는 편이 안전합니다.</p>"
    )


def build_guide_html(
    p: ThemePalette,
    shortcuts: Mapping[str, str] | None = None,
) -> str:
    """프로그램 안내 탭, 목적, 작업 흐름, 되돌리기, 판정표, 자주 묻는 질문"""
    effective = DEFAULT_SHORTCUTS if shortcuts is None else shortcuts
    intro = (
        "보관 중인 영상들을 무작정 다시 인코딩하면 화질이 나빠지거나 시간만 낭비되기 쉽습니다. "
        "인코딩은 이미 압축된 영상을 한 번 더 압축하는 일이라 할 때마다 화질이 조금씩 손상되기 때문입니다. "
        "이 도구는 <b>눈에 띄는 화질 손실 없이 용량을 줄일 수 있는 영상</b>과 "
        "<b>구간 이동(앞/뒤 건너뛰기)이 버벅이는 영상</b>만 골라내 인코딩 대상으로 판정해 줍니다."
    )
    return "".join(
        (
            render_styles(p),
            "<h2>이 프로그램은 왜 사용하나요?</h2>",
            callout_html(p, intro),
            _basics_html(),
            _steps_html(effective),
            _undo_html(effective),
            _decision_table_html(p),
            _quality_analysis_html(),
            _faq_html(),
        )
    )


def _rule_intro_html(shortcuts: Mapping[str, str]) -> str:
    rules = inline_cmd("예외 규칙", shortcuts, "edit.exception_editor")
    return (
        "<h2>예외 조건식 문법과 조작 팁</h2>"
        "<h3>1. 예외 규칙이란?</h3>"
        "<p>&quot;이 영상만큼은 건드리지 말아 주세요&quot;라고 미리 알려 두는 장치입니다. "
        "예외 규칙에 일치한 영상은 화질, 키프레임 판정과 무관하게 인코딩 대상에서 제외되어 원본으로 보존됩니다. "
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
            "<code>v.fps</code>는 그 영상의 FPS입니다.</li>"
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


def _tips_html(shortcuts: Mapping[str, str]) -> str:
    shortcut_settings = inline_cmd("단축키 설정", shortcuts, "edit.open_shortcuts")
    return (
        "<h3>3. 조작 팁</h3>"
        f"<p>명령별 단축키와 기본값은 <b>{shortcut_settings}</b> 창의 단축키 탭에서 확인하고 바꿀 수 있습니다. "
        "바꾼 단축키는 메뉴 항목과 버튼 툴팁에도 바로 반영됩니다.</p>"
        "<p>테이블 열 헤더 우클릭 또는 <b>보기 &gt; 표시할 열</b>에서 기본 숨김 열"
        "(<code>최적 BR</code>, <code>BR 비율</code> 등)을 켜 판정 근거를 직접 확인할 수 있습니다. "
        "열별 필터 팝오버로 원하는 영상만 추려 보거나, 폴더를 창으로 끌어다 놓아 바로 열 수도 있습니다.</p>"
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
            _tips_html(effective),
        )
    )
