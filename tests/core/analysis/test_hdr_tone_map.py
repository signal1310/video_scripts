"""HDR 톤 매핑 체인 정의와 필터 가용성 판정 검증"""

import unittest

from src.core.analysis.hdr_tone_map import (
    TONEMAP_FILTER_NAMES,
    TONEMAP_UNAVAILABLE_REASON,
    ToneMapUnavailableError,
    is_tonemap_filter_error,
    missing_tonemap_filters,
    tonemap_filter_chain,
    tonemap_filter_nodes,
    tonemap_graph_prefix,
)

_EXPECTED_CHAIN: str = (
    "zscale=t=linear:npl=100,format=gbrpf32le,zscale=p=bt709,"
    "tonemap=hable:desat=0,zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
)


class ToneMapChainTests(unittest.TestCase):
    """PQ, HLG, SDR 입력의 필터 체인 선택 검증"""

    def test_hdr_uses_the_same_chain_for_pq_and_hlg(self) -> None:
        """PQ와 HLG는 전달 함수별 분기 없이 같은 zscale, tonemap 체인을 씀"""
        self.assertEqual(tonemap_filter_chain(hdr=True), _EXPECTED_CHAIN)
        self.assertEqual(tonemap_graph_prefix(hdr=True), f"{_EXPECTED_CHAIN},")
        nodes = tonemap_filter_nodes(hdr=True)
        self.assertEqual([name for name, _args in nodes], ["zscale", "format", "zscale", "tonemap", "zscale", "format"])
        self.assertEqual(nodes[3], ("tonemap", "hable:desat=0"))
        # - 마지막이 8-bit 4:2:0이라 회색조 변환이 8-bit Y를 그대로 씀
        self.assertEqual(nodes[-1], ("format", "yuv420p"))
        self.assertEqual(TONEMAP_FILTER_NAMES, ("zscale", "format", "tonemap"))

    def test_sdr_keeps_the_plain_chain(self) -> None:
        """SDR 입력에는 톤 매핑 필터를 넣지 않아 기존 측정 결과가 바뀌지 않음"""
        self.assertEqual(tonemap_filter_chain(hdr=False), "")
        self.assertEqual(tonemap_graph_prefix(hdr=False), "")
        self.assertEqual(tonemap_filter_nodes(hdr=False), ())

    def test_missing_filters_are_named(self) -> None:
        """빌드에 없는 톤 매핑 필터 이름만 사유로 남김"""
        self.assertEqual(missing_tonemap_filters(frozenset({"zscale", "format", "tonemap"})), ())
        self.assertEqual(
            missing_tonemap_filters(frozenset({"format", "tonemap"})), ("zscale",)
        )
        self.assertEqual(
            missing_tonemap_filters(frozenset()), ("zscale", "format", "tonemap")
        )

    def test_filter_errors_are_recognised(self) -> None:
        """필터를 만들지 못한 표준 오류만 톤 매핑 불가로 보고 다른 실패는 그대로 둠"""
        self.assertTrue(is_tonemap_filter_error("[AVFilterGraph] No such filter: 'zscale'"))
        self.assertTrue(is_tonemap_filter_error("Error reinitializing filters!"))
        self.assertTrue(is_tonemap_filter_error("No such filter: 'tonemap'"))
        self.assertFalse(is_tonemap_filter_error("Invalid data found when processing input"))
        self.assertFalse(is_tonemap_filter_error(""))

    def test_unavailable_reason_is_a_polite_sentence(self) -> None:
        """측정 불가 사유가 존댓말 문장으로 zscale과 tonemap을 이름 지음"""
        self.assertIn("zscale", TONEMAP_UNAVAILABLE_REASON)
        self.assertIn("tonemap", TONEMAP_UNAVAILABLE_REASON)
        self.assertTrue(TONEMAP_UNAVAILABLE_REASON.endswith("."))
        with self.assertRaises(RuntimeError):
            raise ToneMapUnavailableError(TONEMAP_UNAVAILABLE_REASON)


if __name__ == "__main__":
    unittest.main()
