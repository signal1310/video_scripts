"""재인코딩 태그 기록 값 계산(encode_tag_plan) 단위 테스트"""

import unittest

from src.core.encode.encode_tag_plan import format_mkv_keywords, parse_keyword_list, plan_encode_tags
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN, ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE


class TestPlanEncodeTags(unittest.TestCase):
    """기록 방식 3종과 키워드 병합, 제거, 중복 제거 규칙을 검증"""

    def test_visible_merges_dedups_and_appends_current_name(self) -> None:
        """visible은 한글, 대소문자, 앞뒤 공백을 정규화해 중복 제거하고 현재 이름을 끝에 붙임"""
        plan = plan_encode_tags(
            mode=ENCODE_TAG_MODE_VISIBLE,
            tag_name=" Re_Encoded ",
            source_keywords=["  여행 ", "여행", "RE_ENCODED", "old_tag", "Family", "family", "  "],
            known_tag_names=["OLD_TAG"],
            detail="v=1;enc=libx265;q=22.0",
        )
        self.assertEqual(plan.mode, ENCODE_TAG_MODE_VISIBLE)
        self.assertEqual(plan.tag_name, "Re_Encoded")
        self.assertEqual(plan.keywords, ("여행", "Family", "Re_Encoded"))
        self.assertEqual(plan.detail, "v=1;enc=libx265;q=22.0")

    def test_hidden_removes_our_names_and_keeps_detail(self) -> None:
        """hidden은 우리 태그 이름만 빼고 사용자 키워드와 상세 값을 유지함을 검증"""
        plan = plan_encode_tags(
            mode=ENCODE_TAG_MODE_HIDDEN,
            tag_name="re_encoded",
            source_keywords=["여행", "Re_Encoded", "old_name", "풍경"],
            known_tag_names=["OLD_NAME"],
            detail="v=1;enc=hevc_nvenc",
        )
        self.assertEqual(plan.keywords, ("여행", "풍경"))
        self.assertEqual(plan.detail, "v=1;enc=hevc_nvenc")

    def test_off_removes_our_names_and_clears_detail(self) -> None:
        """off는 우리 태그 이름과 상세 값을 모두 비움을 검증"""
        plan = plan_encode_tags(
            mode=ENCODE_TAG_MODE_OFF,
            tag_name="re_encoded",
            source_keywords=["re_encoded", "사용자태그"],
            detail="v=1;enc=hevc_nvenc",
        )
        self.assertEqual(plan.keywords, ("사용자태그",))
        self.assertIsNone(plan.detail)

    def test_invalid_mode_or_tag_name_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            plan_encode_tags(mode="invalid", tag_name="re_encoded", source_keywords=())
        with self.assertRaises(ValueError):
            plan_encode_tags(mode=ENCODE_TAG_MODE_VISIBLE, tag_name="bad;name", source_keywords=())

    def test_keyword_list_parsing_and_mkv_formatting(self) -> None:
        self.assertEqual(parse_keyword_list(" 여행 ; ; family "), ("여행", "family"))
        self.assertEqual(parse_keyword_list(None), ())
        self.assertEqual(format_mkv_keywords(("여행", "re_encoded")), "여행;re_encoded")
        self.assertEqual(format_mkv_keywords(()), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
