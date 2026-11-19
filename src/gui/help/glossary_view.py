"""사용 가이드 용어 사전 탭의 검색, 분류 필터, 현재 판별 기준 반영 및 HTML 조립 모듈"""

from __future__ import annotations

import html

from src.core.config import ClassifierConfig
from src.core.quality_target import PROTECT_CQ_OFFSET, format_cq_value, protect_cq_value
from src.gui.help.glossary_data import GLOSSARY_CATEGORIES, GLOSSARY_TERMS, GlossaryTerm
from src.gui.help.help_blocks import GAP_HTML, card_html
from src.gui.styles.palettes import ThemePalette

GLOSSARY_ALL_CATEGORY: str = "all"
GLOSSARY_CATEGORY_CHOICES: tuple[tuple[str, str], ...] = (
    (GLOSSARY_ALL_CATEGORY, "전체"),
    *GLOSSARY_CATEGORIES,
)


def filter_glossary_terms(query: str, category: str) -> tuple[GlossaryTerm, ...]:
    """검색어와 분류 조건에 맞는 용어를 데이터 정의 순서대로 반환"""
    needle = query.strip().casefold()
    return tuple(
        term
        for term in GLOSSARY_TERMS
        if (category == GLOSSARY_ALL_CATEGORY or term.category == category)
        and (not needle or needle in _search_text(term))
    )


def _search_text(term: GlossaryTerm) -> str:
    parts = [term.term, term.where, term.desc, term.keywords, term.example, *term.shorts]
    for value in term.values:
        parts.extend((value.name, value.meaning))
    return " ".join(parts).casefold()


def glossary_placeholder_values(config: ClassifierConfig) -> dict[str, str]:
    """설명문의 기준값 자리표시자를 현재 판별 기준 문자열로 변환"""
    thresholds = config.thresholds
    savings = config.savings
    return {
        "over_ratio": f"{thresholds.over_bitrate_ratio:g}",
        "low_ratio": f"{thresholds.low_bitrate_ratio:g}",
        "kf_limit": f"{thresholds.keyframe_interval_sec:g}",
        "min_saved": f"{savings.min_saved_mb:g}",
        "min_rate": f"{savings.min_mb_per_min:g}",
        "exempt_saved": f"{savings.exempt_saved_mb:g}",
        "baseline_cq": format_cq_value(config.encode.baseline_cq),
        "protect_cq": format_cq_value(protect_cq_value(config.encode.baseline_cq)),
        "protect_offset": format_cq_value(PROTECT_CQ_OFFSET),
    }


def build_glossary_html(
    terms: tuple[GlossaryTerm, ...], config: ClassifierConfig, palette: ThemePalette
) -> str:
    """선택된 용어 목록을 사용 가이드 본문용 카드 HTML로 조립, 카드 사이는 좁은 간격으로 나눔"""
    if not terms:
        return "<p>검색 결과가 없습니다. 검색어나 분류를 바꿔 보세요.</p>"
    values = glossary_placeholder_values(config)
    return GAP_HTML.join(card_html(palette, _render_term(term, values, palette)) for term in terms)


def _render_term(term: GlossaryTerm, values: dict[str, str], palette: ThemePalette) -> str:
    parts = [f"<h3 style='margin: 0 0 6px 0; font-size: 13pt;'>{html.escape(term.term)}</h3>"]
    if term.shorts:
        shorts = ", ".join(html.escape(short) for short in term.shorts)
        parts.append(_meta_html(palette, "약어", shorts))
    if term.where:
        parts.append(_meta_html(palette, "보이는 곳", html.escape(term.where)))
    parts.append(f"<p style='margin: 10px 0 0 0;'>{term.desc.format_map(values)}</p>")
    if term.values:
        items = "".join(
            f"<li><b>{html.escape(value.name)}</b>: {value.meaning.format_map(values)}</li>"
            for value in term.values
        )
        parts.append(f"<ul style='margin: 8px 0 0 0;'>{items}</ul>")
    if term.example:
        parts.append(
            f"<p style='margin: 10px 0 0 0; color: {palette.text_muted};'>"
            f"{term.example.format_map(values)}</p>"
        )
    return "".join(parts)


def _meta_html(palette: ThemePalette, label: str, value: str) -> str:
    """약어, 보이는 곳 등 메타 정보 한 줄, 라벨은 흐린 색으로 낮춤"""
    return (
        f"<p style='margin: 0 0 2px 0; font-size: 10pt;'>"
        f"<span style='color: {palette.text_muted};'>{label}:</span> {value}</p>"
    )
