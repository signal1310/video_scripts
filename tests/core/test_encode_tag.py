"""인코딩 태그의 상세 값 app 필드, 태그 이름 검증 경계, 대소문자 무시 비교를 검증하는 단위 테스트"""

import unittest
from datetime import datetime, timezone

from src.core.app_version import APP_VERSION
from src.core.encode_tag import (
    ENCODE_TAG_NAME_MAX_CHARS,
    build_encode_tag,
    has_tag_name_keyword,
    normalize_tag_name,
    parse_encode_tag,
)

_TAG_TIME = datetime(2026, 11, 18, 5, 0, 0, tzinfo=timezone.utc)


class TestBuildEncodeTagAppField(unittest.TestCase):
    """상세 값에 프로그램 버전 필드가 붙고 읽을 때 모르는 필드로 무시됨"""

    def test_build_appends_app_version_as_last_field(self) -> None:
        """상세 값 마지막에 app 필드가 프로그램 버전으로 붙음을 검증"""
        value = build_encode_tag("libx265", 22.0, _TAG_TIME)
        self.assertEqual(value, f"v=1;enc=libx265;q=22.0;at=2026-11-18T05:00:00Z;app={APP_VERSION}")

    def test_parse_reads_known_fields_when_app_is_present(self) -> None:
        """app 필드가 있는 상세 값을 읽어도 기존 필드 값이 그대로 읽힘을 검증"""
        tag = parse_encode_tag({"video_scripts_encode": build_encode_tag("libx265", 22.0, _TAG_TIME)})
        assert tag is not None
        self.assertEqual(tag.version, 1)
        self.assertEqual(tag.encoder, "libx265")
        self.assertEqual(tag.quality, "22.0")
        self.assertEqual(tag.at, "2026-11-18T05:00:00Z")


class TestNormalizeTagName(unittest.TestCase):
    """태그 이름 검증 경계와 앞뒤 공백 제거"""

    def test_accepts_name_at_max_length_and_rejects_one_more(self) -> None:
        """64자는 통과하고 65자는 거부됨을 검증(문자 수 기준)"""
        at_limit = "가" * ENCODE_TAG_NAME_MAX_CHARS
        self.assertEqual(normalize_tag_name(at_limit), at_limit)
        with self.assertRaises(ValueError):
            normalize_tag_name("가" * (ENCODE_TAG_NAME_MAX_CHARS + 1))

    def test_strips_surrounding_whitespace_before_checking(self) -> None:
        """앞뒤 공백을 뗀 뒤 길이를 세므로 공백이 붙은 64자 이름은 통과함을 검증"""
        self.assertEqual(normalize_tag_name("  re_encoded  "), "re_encoded")
        self.assertEqual(normalize_tag_name(" " * 3 + "a" * ENCODE_TAG_NAME_MAX_CHARS + " "), "a" * ENCODE_TAG_NAME_MAX_CHARS)

    def test_rejects_empty_and_whitespace_only(self) -> None:
        """빈 값과 공백만 있는 값을 거부함을 검증"""
        for raw in ("", "   ", "\t"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_tag_name(raw)

    def test_rejects_semicolon_anywhere(self) -> None:
        """세미콜론이 가운데나 끝에 있으면 거부함을 검증(MKV KEYWORDS와 Windows 태그 구분자)"""
        for raw in ("a;b", "a;", ";a"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_tag_name(raw)

    def test_rejects_control_characters(self) -> None:
        """제어 문자가 가운데 있으면 거부하고, 일반 공백과 한글은 통과함을 검증"""
        for raw in ("a\tb", "a\nb", "a\x00b"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                normalize_tag_name(raw)
        self.assertEqual(normalize_tag_name("인코딩 태그"), "인코딩 태그")


class TestHasTagNameKeyword(unittest.TestCase):
    """키워드와 태그 이름의 대소문자 무시 비교"""

    def test_matches_ignoring_case_and_surrounding_spaces(self) -> None:
        """대소문자와 키워드 앞뒤 공백이 달라도 일치로 봄을 검증"""
        self.assertTrue(has_tag_name_keyword([" RE_ENCODED "], ["re_encoded"]))
        self.assertTrue(has_tag_name_keyword(["other", "Re_Encoded"], ["RE_ENCODED"]))

    def test_matches_any_previous_name(self) -> None:
        """현재 이름이 아니라 이전 이름과 같아도 일치로 봄을 검증"""
        self.assertTrue(has_tag_name_keyword(["old_name"], ["new_name", "old_name"]))

    def test_no_match_and_empty_values_do_not_match(self) -> None:
        """다른 키워드, 빈 키워드, 빈 태그 이름은 일치로 보지 않음을 검증"""
        self.assertFalse(has_tag_name_keyword(["unrelated"], ["re_encoded"]))
        self.assertFalse(has_tag_name_keyword([""], ["", "  "]))
        self.assertFalse(has_tag_name_keyword([], ["re_encoded"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
