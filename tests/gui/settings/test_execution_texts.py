"""인코딩 탭 문구 모듈의 출력 형식 설명이 계열별 저장 규칙과 맞는지 검증"""

import unittest

from src.gui.settings.execution_texts import TOOLTIP_CODEC, build_container_desc

_H264_DESC: str = (
    "결과 파일의 형식입니다. 현재 목표 인코딩 방식(H.264) 기준입니다. "
    "원본 형식 유지(기본): MP4, MOV, MKV 원본은 같은 형식으로 저장하고, 그 밖의 형식은 MP4로 저장합니다. "
    "MP4로 바뀌는 형식(원본 형식 유지일 때): AVI(인코딩 태그를 기록할 수 없음), WMV(H.264 코덱을 표준으로 담지 못함), "
    "FLV(인코딩 태그를 기록할 수 없음), WebM(H.264 코덱을 표준으로 담지 못함), TS(인코딩 태그를 기록할 수 없음). "
    "항상 MP4: 모든 결과를 MP4로 저장합니다. "
    "비트맵 자막(PGS, VobSub 등)이 있는 영상은 두 방식 모두 MKV로 저장합니다. MP4에 저장할 수 없기 때문입니다."
)
_HEVC_DESC: str = (
    "결과 파일의 형식입니다. 현재 목표 인코딩 방식(HEVC) 기준입니다. "
    "원본 형식 유지(기본): MP4, MOV, MKV 원본은 같은 형식으로 저장하고, 그 밖의 형식은 MP4로 저장합니다. "
    "MP4로 바뀌는 형식(원본 형식 유지일 때): AVI(HEVC 코덱을 표준으로 담지 못함), WMV(HEVC 코덱을 표준으로 담지 못함), "
    "FLV(HEVC 코덱을 표준으로 담지 못함), WebM(HEVC 코덱을 표준으로 담지 못함), TS(인코딩 태그를 기록할 수 없음). "
    "항상 MP4: 모든 결과를 MP4로 저장합니다. "
    "비트맵 자막(PGS, VobSub 등)이 있는 영상은 두 방식 모두 MKV로 저장합니다. MP4에 저장할 수 없기 때문입니다."
)


class BuildContainerDescTests(unittest.TestCase):
    """계열별 출력 형식 설명 문장을 검증. H.264와 HEVC 문장은 규칙 도입 전 문장과 같아야 함"""

    def test_h264_sentence_is_unchanged_from_the_fixed_text(self) -> None:
        """H.264 설명이 계열 표에서 만든 값과 기존 문장이 같은지 검증"""
        self.assertEqual(build_container_desc("h264"), _H264_DESC)

    def test_hevc_sentence_is_unchanged_from_the_fixed_text(self) -> None:
        """HEVC 설명이 계열 표에서 만든 값과 기존 문장이 같은지 검증"""
        self.assertEqual(build_container_desc("hevc"), _HEVC_DESC)

    def test_av1_sentence_defers_the_rule_to_stage_5(self) -> None:
        """AV1은 원본 형식 목록을 확인하지 않아 5단계 안내만 있고 MP4로 바뀌는 목록이 없는지 검증"""
        desc = build_container_desc("av1")
        self.assertIn("원본 형식 유지(기본)와 MP4로 바뀌는 형식은 5단계에서 확인합니다.", desc)
        self.assertNotIn("AVI(", desc)

    def test_codec_tooltip_names_the_speed_row_it_controls(self) -> None:
        """목표 인코딩 방식 툴팁이 행 이름과 같은 기준 인코딩 배속을 가리키는지 검증"""
        self.assertIn("기준 인코딩 배속", TOOLTIP_CODEC)
        self.assertNotIn("1080p 예상 배속", TOOLTIP_CODEC)


if __name__ == "__main__":
    unittest.main(verbosity=2)
