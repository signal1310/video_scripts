import unittest

from src.core.config import ClassifierConfig, SavingsConfig, ThresholdsConfig
from src.gui.table.column_filters import (
    TEXT_MODE_CONTAINS,
    TEXT_MODE_ENDS_WITH,
    TEXT_MODE_NOT_CONTAINS,
    TEXT_MODE_STARTS_WITH,
    ColumnDataType,
    ColumnFilterRule,
    ColumnSpec,
    rule_to_snapshot,
    snapshot_to_rule,
)
from src.gui.table.numeric_buckets import NumericBucketKind, build_numeric_buckets

_TEXT = ColumnSpec(0, ColumnDataType.TEXT)
_ENUM = ColumnSpec(0, ColumnDataType.ENUM)
_NUMBER = ColumnSpec(
    0, ColumnDataType.NUMBER, unit="초", nullable_label="미측정", bucket_kind=NumericBucketKind.KEYFRAME
)
_CFG = ClassifierConfig()
_KF_LE, _KF_MID, _KF_GT = "kf:le_2.2", "kf:2.2_5", "kf:gt_5"


def _labels(kind: NumericBucketKind, **thresholds: float) -> list[str]:
    cfg = ClassifierConfig(thresholds=ThresholdsConfig(**thresholds))
    return [b.label for b in build_numeric_buckets(kind, cfg)]


def _keys(kind: NumericBucketKind, cfg: ClassifierConfig = _CFG) -> list[str]:
    return [b.key for b in build_numeric_buckets(kind, cfg)]


class TestColumnFilterSnapshot(unittest.TestCase):
    """열 필터 규칙과 저장 값 객체 사이의 변환이 조건을 잃지 않고 왕복함을 검증"""

    def test_rule_roundtrips_through_snapshot(self) -> None:
        """선택 값, 수치 범위, 빈 값 포함 여부, 텍스트 조건이 변환 후에도 같은 규칙이 됨을 검증"""
        rule = ColumnFilterRule(
            selected_values=frozenset({"HEVC", "H.264"}),
            min_value=1.0,
            max_value=None,
            include_null=False,
            text_query="sam",
            text_mode=TEXT_MODE_STARTS_WITH,
        )
        snapshot = rule_to_snapshot(rule)
        self.assertEqual(snapshot.selected_values, ("H.264", "HEVC"))
        self.assertEqual(snapshot_to_rule(snapshot), rule)

    def test_rule_without_selection_keeps_none(self) -> None:
        """선택 값 목록이 없는 규칙은 저장 값에서도 목록 없음으로 남음을 검증"""
        self.assertIsNone(rule_to_snapshot(ColumnFilterRule()).selected_values)
        self.assertEqual(snapshot_to_rule(rule_to_snapshot(ColumnFilterRule())), ColumnFilterRule())


class TestColumnFilterRule(unittest.TestCase):
    """ColumnFilterRule의 일치 판정, 활성 여부, 조건 요약 문구를 검증"""

    def test_text_modes_match_distinct_outcomes(self) -> None:
        """문자열 모드마다 참, 거짓이 갈리는 값으로 모드 혼동을 구분"""
        value = "Clip_final.mp4"
        cases = [
            ("final", TEXT_MODE_CONTAINS, True),
            ("zzz", TEXT_MODE_CONTAINS, False),
            ("final", TEXT_MODE_NOT_CONTAINS, False),
            ("zzz", TEXT_MODE_NOT_CONTAINS, True),
            ("clip", TEXT_MODE_STARTS_WITH, True),
            ("final", TEXT_MODE_STARTS_WITH, False),
            (".mp4", TEXT_MODE_ENDS_WITH, True),
            ("clip", TEXT_MODE_ENDS_WITH, False),
            ("  CLIP ", TEXT_MODE_STARTS_WITH, True),
            ("", TEXT_MODE_NOT_CONTAINS, True),
        ]
        for query, mode, expected in cases:
            with self.subTest(query=query, mode=mode):
                rule = ColumnFilterRule(text_query=query, text_mode=mode)
                self.assertEqual(rule.matches(value, _TEXT), expected)

        self.assertFalse(ColumnFilterRule(text_query="x").matches(None, _TEXT))
        self.assertTrue(ColumnFilterRule(text_query="x", text_mode=TEXT_MODE_NOT_CONTAINS).matches(None, _TEXT))

    def test_enum_matches_selected_values(self) -> None:
        """미선택은 전체 통과, 빈 선택은 전체 차단, None 값은 빈 문자열로 취급"""
        self.assertTrue(ColumnFilterRule().matches("a", _ENUM))
        self.assertTrue(ColumnFilterRule(selected_values=frozenset({"a"})).matches("a", _ENUM))
        self.assertFalse(ColumnFilterRule(selected_values=frozenset({"a"})).matches("b", _ENUM))
        self.assertFalse(ColumnFilterRule(selected_values=frozenset({"a"})).matches(None, _ENUM))
        self.assertTrue(ColumnFilterRule(selected_values=frozenset({""})).matches(None, _ENUM))
        self.assertFalse(ColumnFilterRule(selected_values=frozenset()).matches("a", _ENUM))

    def test_number_range_null_and_bucket_matching(self) -> None:
        """수치 범위 경계 포함, null 처리, 문자열 값, 버킷 선택과 범위의 결합을 검증"""
        both = ColumnFilterRule(min_value=1.0, max_value=5.0)
        for value, expected in [(0.9, False), (1.0, True), (5.0, True), (5.1, False)]:
            with self.subTest(range=value):
                self.assertEqual(both.matches(value, _NUMBER), expected)
        self.assertTrue(ColumnFilterRule(min_value=1.0).matches(1000.0, _NUMBER))
        self.assertFalse(ColumnFilterRule(min_value=1.0).matches(0.5, _NUMBER))
        self.assertTrue(ColumnFilterRule(max_value=5.0).matches(-3.0, _NUMBER))
        self.assertFalse(ColumnFilterRule(max_value=5.0).matches(6.0, _NUMBER))

        self.assertTrue(ColumnFilterRule().matches(None, _NUMBER))
        self.assertFalse(ColumnFilterRule(include_null=False).matches(None, _NUMBER))
        self.assertTrue(ColumnFilterRule(null_only=True).matches(None, _NUMBER))
        self.assertFalse(ColumnFilterRule(null_only=True).matches(1.0, _NUMBER))
        self.assertFalse(ColumnFilterRule().matches("n/a", _NUMBER))

        first = ColumnFilterRule(selected_values=frozenset({_KF_LE}))
        for value, expected in [(1.0, True), (2.2, True), (3.0, False)]:
            with self.subTest(bucket=value):
                self.assertEqual(first.matches(value, _NUMBER, _CFG), expected)
        combined = ColumnFilterRule(selected_values=frozenset({_KF_LE}), min_value=1.5)
        self.assertFalse(combined.matches(1.0, _NUMBER, _CFG))
        self.assertTrue(combined.matches(2.0, _NUMBER, _CFG))

    def test_is_active_per_data_type(self) -> None:
        """기본값은 비활성이고 각 조건 하나만 바뀌어도 활성으로 판정"""
        number = ColumnDataType.NUMBER
        self.assertFalse(ColumnFilterRule().is_active(number))
        for rule in (
            ColumnFilterRule(null_only=True),
            ColumnFilterRule(selected_values=frozenset()),
            ColumnFilterRule(min_value=0.0),
            ColumnFilterRule(max_value=0.0),
            ColumnFilterRule(include_null=False),
        ):
            with self.subTest(rule=rule):
                self.assertTrue(rule.is_active(number))

        self.assertFalse(ColumnFilterRule().is_active(ColumnDataType.ENUM))
        self.assertTrue(ColumnFilterRule(selected_values=frozenset()).is_active(ColumnDataType.ENUM))
        self.assertFalse(ColumnFilterRule(text_query="  ").is_active(ColumnDataType.TEXT))
        self.assertTrue(ColumnFilterRule(text_query="a").is_active(ColumnDataType.TEXT))

    def test_describe_summaries(self) -> None:
        """열거형, 수치, 문자열 필터의 헤더 툴팁 요약 문구를 검증"""
        self.assertEqual(ColumnFilterRule().describe(_ENUM), "")
        self.assertEqual(ColumnFilterRule(selected_values=frozenset()).describe(_ENUM), "선택 없음 (0개)")
        self.assertEqual(ColumnFilterRule(selected_values=frozenset("cba")).describe(_ENUM), "선택: a, b, c")
        self.assertEqual(
            ColumnFilterRule(selected_values=frozenset("dcba")).describe(_ENUM), "선택: a, b, c 외 1개"
        )

        numeric_cases = [
            (ColumnFilterRule(min_value=1, max_value=5), "1 ~ 5 초"),
            (ColumnFilterRule(min_value=1), ">= 1 초"),
            (ColumnFilterRule(max_value=5), "<= 5 초"),
            (ColumnFilterRule(min_value=1, include_null=False), ">= 1 초, 미측정 제외"),
            (ColumnFilterRule(null_only=True), "미측정만 표시"),
            (ColumnFilterRule(selected_values=frozenset()), "구간 선택 없음"),
            (ColumnFilterRule(selected_values=frozenset({_KF_LE})), "구간: 정상 (≤ 2.2초)"),
            (
                ColumnFilterRule(selected_values=frozenset({_KF_LE, _KF_MID, _KF_GT})),
                "구간: 정상 (≤ 2.2초), 주의 (2.2 ~ 5초) 외 1개",
            ),
            (ColumnFilterRule(selected_values=frozenset({"kf:unknown"})), "구간: kf:unknown"),
        ]
        for rule, expected in numeric_cases:
            with self.subTest(expected=expected):
                self.assertEqual(rule.describe(_NUMBER, _CFG), expected)
        self.assertEqual(ColumnFilterRule(include_null=False).describe(ColumnSpec(0, ColumnDataType.NUMBER)), "")

        self.assertEqual(ColumnFilterRule(text_query="  ").describe(_TEXT), "")
        self.assertEqual(ColumnFilterRule(text_query="ab").describe(_TEXT), "포함: 'ab'")
        self.assertEqual(
            ColumnFilterRule(text_query="ab", text_mode=TEXT_MODE_ENDS_WITH).describe(_TEXT), "끝 문자: 'ab'"
        )


class TestNumericBucketBuilders(unittest.TestCase):
    """설정 임계값에서 파생되는 동적 버킷 경계와 키 안정성을 검증"""

    def test_ratio_buckets_follow_thresholds(self) -> None:
        self.assertEqual(
            _keys(NumericBucketKind.RATIO),
            [
                "ratio:lt_0.40", "ratio:0.40_0.60", "ratio:0.60_0.80", "ratio:0.80_1.00",
                "ratio:1.00_1.20", "ratio:gt_1.20_1.50", "ratio:1.50_2.00", "ratio:2.00_3.00", "ratio:ge_3.00",
            ],
        )
        custom = ClassifierConfig(
            thresholds=ThresholdsConfig(low_bitrate_ratio=0.5, over_bitrate_ratio=2.5)
        )
        self.assertEqual(
            _keys(NumericBucketKind.RATIO, custom),
            [
                "ratio:lt_0.25", "ratio:0.25_0.38", "ratio:0.38_0.50", "ratio:0.50_1.00",
                "ratio:1.00_2.50", "ratio:gt_2.50_3.00", "ratio:3.00_4.17", "ratio:ge_4.17",
            ],
        )

    def test_keyframe_buckets_follow_limit(self) -> None:
        self.assertEqual(_keys(NumericBucketKind.KEYFRAME), [_KF_LE, _KF_MID, _KF_GT])
        custom = ClassifierConfig(thresholds=ThresholdsConfig(keyframe_interval_sec=5.0))
        self.assertEqual(_keys(NumericBucketKind.KEYFRAME, custom), ["kf:le_5", "kf:5_10", "kf:gt_10"])

    def test_ratio_labels_cover_threshold_edge_layouts(self) -> None:
        """저화질 하한이 아주 작은 설정, 적정 구간이 1.0을 포함하지 않는 설정, 과화질 상한이 큰 설정의 라벨을 검증"""
        tail = ["1.20 ~ 1.50x (과화질)", "1.50 ~ 2.00x (과화질)", "2.00 ~ 3.00x (과화질)", "3.00x 이상 (과화질)"]
        cases: list[tuple[dict[str, float], list[str]]] = [
            (
                {},
                [
                    "0.40x 미만 (저화질)", "0.40 ~ 0.60x (저화질)", "0.60 ~ 0.80x (저화질)",
                    "0.80 ~ 1.00x (적정)", "1.00 ~ 1.20x (적정)", *tail,
                ],
            ),
            (
                {"low_bitrate_ratio": 0.1},
                [
                    "0.05x 미만 (저화질)", "0.05 ~ 0.10x (저화질)",
                    "0.10 ~ 1.00x (적정)", "1.00 ~ 1.20x (적정)", *tail,
                ],
            ),
            (
                {"low_bitrate_ratio": 1.1, "over_bitrate_ratio": 1.3},
                [
                    "0.55x 미만 (저화질)", "0.55 ~ 0.83x (저화질)", "0.83 ~ 1.10x (저화질)",
                    "1.10 ~ 1.30x (적정)", "1.30 ~ 1.50x (과화질)", *tail[1:],
                ],
            ),
            (
                {"over_bitrate_ratio": 5.0},
                [
                    "0.40x 미만 (저화질)", "0.40 ~ 0.60x (저화질)", "0.60 ~ 0.80x (저화질)",
                    "0.80 ~ 1.00x (적정)", "1.00 ~ 5.00x (적정)", "5.00 ~ 6.25x (과화질)",
                    "6.25 ~ 8.35x (과화질)", "8.35x 이상 (과화질)",
                ],
            ),
        ]
        for thresholds, expected in cases:
            with self.subTest(thresholds=thresholds):
                self.assertEqual(_labels(NumericBucketKind.RATIO, **thresholds), expected)

    def test_keyframe_labels_format_limit_and_warn_cap(self) -> None:
        """정수형 한계는 소수점 없이, 4.5초 초과 한계는 경고 상한을 2배로 표기"""
        cases = [
            ({}, ["정상 (≤ 2.2초)", "주의 (2.2 ~ 5초)", "초과 (> 5초)"]),
            ({"keyframe_interval_sec": 3.0}, ["정상 (≤ 3초)", "주의 (3 ~ 5초)", "초과 (> 5초)"]),
            ({"keyframe_interval_sec": 4.5}, ["정상 (≤ 4.5초)", "주의 (4.5 ~ 5초)", "초과 (> 5초)"]),
            ({"keyframe_interval_sec": 4.6}, ["정상 (≤ 4.6초)", "주의 (4.6 ~ 9.2초)", "초과 (> 9.2초)"]),
        ]
        for thresholds, expected in cases:
            with self.subTest(thresholds=thresholds):
                self.assertEqual(_labels(NumericBucketKind.KEYFRAME, **thresholds), expected)

    def test_saved_size_buckets_include_threshold_only_when_positive(self) -> None:
        self.assertIn("saved:0_5", _keys(NumericBucketKind.SAVED_SIZE))
        zero = ClassifierConfig(savings=SavingsConfig(min_saved_mb=0.0, min_mb_per_min=0.0))
        self.assertNotIn("saved:0_5", _keys(NumericBucketKind.SAVED_SIZE, zero))
        self.assertIn("saved:0_30", _keys(NumericBucketKind.SAVED_SIZE, zero))

    def test_width_buckets_follow_resolution_tier_table(self) -> None:
        """너비 열 17구간 라벨이 해상도 규격 표의 경계와 가로형, 세로형 방향을 따름"""
        self.assertEqual(
            _labels(NumericBucketKind.WIDTH),
            [
                "360 미만",
                "360~479 (360p 세로형)", "480~539 (480p 세로형)", "540~639 (540p 세로형)",
                "640~719 (360p 가로형)", "720~853 (720p 세로형)", "854~899 (480p 가로형)",
                "900~959 (900p 세로형)", "960~1079 (540p 가로형)", "1080~1279 (1080p 세로형)",
                "1280~1439 (720p 가로형)", "1440~1599 (1440p 세로형)", "1600~1919 (900p 가로형)",
                "1920~2159 (1080p 가로형)", "2160~2559 (4K 세로형)", "2560~3839 (1440p 가로형)",
                "3840 이상 (4K 가로형)",
            ],
        )

    def test_height_buckets_mirror_width_orientation(self) -> None:
        """높이 열은 같은 경계를 쓰되 가로형, 세로형이 너비 열과 반대이고 경계 목록은 동일"""
        height_labels = _labels(NumericBucketKind.HEIGHT)
        self.assertEqual(
            height_labels,
            [
                "360 미만",
                "360~479 (360p 가로형)", "480~539 (480p 가로형)", "540~639 (540p 가로형)",
                "640~719 (360p 세로형)", "720~853 (720p 가로형)", "854~899 (480p 세로형)",
                "900~959 (900p 가로형)", "960~1079 (540p 세로형)", "1080~1279 (1080p 가로형)",
                "1280~1439 (720p 세로형)", "1440~1599 (1440p 가로형)", "1600~1919 (900p 세로형)",
                "1920~2159 (1080p 세로형)", "2160~2559 (4K 가로형)", "2560~3839 (1440p 세로형)",
                "3840 이상 (4K 세로형)",
            ],
        )
        width_keys = _keys(NumericBucketKind.WIDTH)
        height_keys = _keys(NumericBucketKind.HEIGHT)
        self.assertEqual(len(width_keys), 17)
        self.assertEqual(
            [key.split(":", 1)[1] for key in width_keys],
            [key.split(":", 1)[1] for key in height_keys],
        )

    def test_resolution_bucket_keys_partition_without_overlap(self) -> None:
        """너비 열 구간 경계가 겹치거나 빈틈없이 이어지고 옛 버킷 키를 더는 사용하지 않음"""
        buckets = build_numeric_buckets(NumericBucketKind.WIDTH)
        for sample in (0, 359, 360, 479, 640, 853, 854, 1079, 1080, 3839, 3840, 8000):
            with self.subTest(sample=sample):
                matched = [b.key for b in buckets if b.contains(float(sample))]
                self.assertEqual(len(matched), 1)
        self.assertNotIn("w:lt_480", _keys(NumericBucketKind.WIDTH))
        self.assertNotIn("w:854_1079", _keys(NumericBucketKind.WIDTH))


if __name__ == "__main__":
    unittest.main()
