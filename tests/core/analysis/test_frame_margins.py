"""비검은 평탄 여백과 밝은 경계 과충격 보정 규칙 검증"""

import unittest
from collections.abc import Sequence

from src.core.analysis.frame_analysis import CropBox
from src.core.analysis.frame_margins import (
    EDGE_OVERSHOOT_LEVELS,
    EMPTY_FLAT_SIDES,
    NON_DARK_MIN_LINES,
    NON_DARK_SPREAD_LIMIT,
    NON_DARK_STEP_MIN,
    FlatSide,
    FlatSides,
    agreed_runs,
    apply_flat_margin_cuts,
    fix_bright_overshoot,
    margin_cuts,
    measure_flat_sides,
    transpose_flat_sides,
)

# - 검은 띠 규칙과 같은 어두운 띠 상한. 이보다 어두운 줄은 비검은 여백으로 보지 않음
DARK_LIMIT: float = 24.0
# - 검은 여백 규칙이 띠를 인정하지 않는 중간 회색. 비검은 여백 규칙이 잡아야 하는 밝기
GREY: int = 64


def _plane(rows: Sequence[Sequence[int]]) -> bytes:
    """행 목록을 Y 평면 바이트로 만듦"""
    return b"".join(bytes(row) for row in rows)


def _texture(row_index: int, column: int, low: int = 90, span: int = 60) -> int:
    """그림 픽셀 휘도. 줄과 열 번호가 섞여 표본 간격과 맞물리지 않는 무늬를 만듦"""
    return low + (column * 7 + row_index * 13) % span


def _textured_pillar(
    *,
    margin: int = 20,
    width: int = 80,
    height: int = 60,
    margin_luma: int = GREY,
    picture_low: int = 90,
    picture_span: int = 60,
) -> bytes:
    """왼쪽 평탄 여백과 오른쪽 무늬 그림으로 이루어진 평면

    - 그림은 줄과 열 모두 밝기가 흩어져 편차가 커서 평탄 구간이 여백에서 멈춤
    """
    rows: list[list[int]] = []
    for row_index in range(height):
        picture = [_texture(row_index, column, picture_low, picture_span) for column in range(width - margin)]
        rows.append([margin_luma] * margin + picture)
    return _plane(rows)


def _side(run: int, *, median: float = 48.0, step: float = 20.0, blend: int = 0) -> FlatSide:
    """지점 한 면 평탄 여구 요약. 기본은 모든 조건을 넉넉히 넘는 값"""
    return FlatSide(run=run, mean=median + 2, median=median, spread=1.0, step=step, blend=blend)


def _sides(
    *,
    left: FlatSide | None = None,
    right: FlatSide | None = None,
    top: FlatSide | None = None,
    bottom: FlatSide | None = None,
) -> FlatSides:
    """지정한 면만 채우고 나머지는 빈 요약인 네 면 요약"""
    empty = FlatSide(run=0, mean=0.0, median=0.0, spread=0.0, step=0.0, blend=0)
    return FlatSides(
        top=empty if top is None else top,
        bottom=empty if bottom is None else bottom,
        left=empty if left is None else left,
        right=empty if right is None else right,
    )


class TestMeasureFlatSides(unittest.TestCase):
    """면별 평탄 구간, 편차, 경계 계단, 혼합 줄 측정 검증"""

    def test_grey_flat_margin_is_measured_with_a_step(self) -> None:
        plane = _textured_pillar()
        sides = measure_flat_sides(plane, 80, 80, 60, CropBox(80, 60, 0, 0), dark_limit=DARK_LIMIT)
        self.assertEqual(sides.left.run, 20)
        self.assertAlmostEqual(sides.left.median, GREY)
        self.assertAlmostEqual(sides.left.spread, 0.0)
        self.assertGreaterEqual(sides.left.step, NON_DARK_STEP_MIN)
        self.assertEqual((sides.top.run, sides.bottom.run, sides.right.run), (0, 0, 0))

    def test_white_flat_margin_is_measured_as_a_run(self) -> None:
        """흰 여백도 어두운 띠 상한을 넘고 평탄해 구간으로 재며, 어두운 그림과의 차이도 계단이 됨"""
        plane = _textured_pillar(margin=30, margin_luma=240, picture_low=60)
        sides = measure_flat_sides(plane, 80, 80, 60, CropBox(80, 60, 0, 0), dark_limit=DARK_LIMIT)
        self.assertEqual(sides.left.run, 30)
        self.assertAlmostEqual(sides.left.median, 240.0)
        # - 그림이 여백보다 어두워도 밝기 차의 절댓값으로 계단을 세므로 자를 근거가 됨
        self.assertGreaterEqual(sides.left.step, NON_DARK_STEP_MIN)

    def test_light_grey_margin_above_a_darker_picture_has_a_step(self) -> None:
        """밝은 회색 여백 앞의 어두운 그림도 절댓값 차이로 계단을 세 자름"""
        plane = _textured_pillar(margin=24, margin_luma=200, picture_low=40, picture_span=20)
        sides = measure_flat_sides(plane, 80, 80, 60, CropBox(80, 60, 0, 0), dark_limit=DARK_LIMIT)
        self.assertEqual(sides.left.run, 24)
        self.assertGreaterEqual(sides.left.step, NON_DARK_STEP_MIN)

    def test_row_samples_use_the_box_span_inside_black_pillars(self) -> None:
        """검은 기둥 안쪽의 회색 레터박스도 행 표본이 상자 가로 구간만 봐서 잼"""
        width, height, pillar, margin = 240, 180, 30, 12
        span = width - pillar * 2
        rows: list[list[int]] = []
        for row_index in range(height):
            if row_index < margin or row_index >= height - margin:
                rows.append([0] * pillar + [GREY] * span + [0] * pillar)
            else:
                picture = [_texture(row_index, column) for column in range(span)]
                rows.append([0] * pillar + picture + [0] * pillar)
        box = CropBox(span, height, pillar, 0)
        sides = measure_flat_sides(_plane(rows), width, width, height, box, dark_limit=DARK_LIMIT)
        self.assertEqual(sides.top.run, margin)
        self.assertEqual(sides.bottom.run, margin)
        self.assertGreaterEqual(sides.top.step, NON_DARK_STEP_MIN)
        self.assertAlmostEqual(sides.top.median, GREY)

    def test_flat_run_starts_at_the_dark_bar_box_edge(self) -> None:
        """검은 띠 안쪽의 회색 여백은 띠 상자 끝에서 시작해 프레임 끝까지의 길이로 잼"""
        width, height, bar, margin = 80, 60, 10, 14
        rows: list[list[int]] = []
        for row_index in range(height):
            picture = [_texture(row_index, column, low=40, span=20) for column in range(width - bar - margin)]
            rows.append([16] * bar + [GREY] * margin + picture)
        box = CropBox(width - bar, height, bar, 0)
        sides = measure_flat_sides(_plane(rows), width, width, height, box, dark_limit=DARK_LIMIT)
        self.assertEqual(sides.left.run, bar + margin)
        self.assertGreaterEqual(sides.left.step, NON_DARK_STEP_MIN)

    def test_margin_above_a_brighter_picture_has_a_step(self) -> None:
        """그림이 여백보다 밝으면 계단이 서서 자를 근거가 됨"""
        plane = _textured_pillar(margin=20, margin_luma=40)
        sides = measure_flat_sides(plane, 80, 80, 60, CropBox(80, 60, 0, 0), dark_limit=DARK_LIMIT)
        self.assertEqual(sides.left.run, 20)
        self.assertGreaterEqual(sides.left.step, NON_DARK_STEP_MIN)

    def test_watermark_partly_bright_line_stays_flat(self) -> None:
        """한 줄 일부만 밝은 덧그림은 위아래 10%를 뺀 편차에 들어오지 않아 평탄으로 남음"""
        rows: list[list[int]] = []
        for row_index in range(60):
            picture = [_texture(row_index, column) for column in range(60)]
            row = [GREY] * 20 + picture
            row[2:6] = [250] * 4
            rows.append(row)
        sides = measure_flat_sides(_plane(rows), 80, 80, 60, CropBox(80, 60, 0, 0), dark_limit=DARK_LIMIT)
        self.assertEqual(sides.left.run, 20)
        # - 평균은 덧그림에 끌리지만 편차는 위아래 10%를 뺀 값이라 평탄 판정을 유지
        self.assertGreater(sides.left.mean, GREY)
        self.assertLessEqual(sides.left.spread, NON_DARK_SPREAD_LIMIT)

    def test_uniform_flat_picture_has_no_edge_step(self) -> None:
        """한 면 전체가 같은 밝기면 경계 계단이 없어 자를 근거가 없음"""
        sides = measure_flat_sides(
            _plane([[120] * 80 for _ in range(60)]), 80, 80, 60, CropBox(80, 60, 0, 0), dark_limit=DARK_LIMIT
        )
        self.assertEqual(sides.left.run, 80)
        self.assertEqual(sides.left.step, 0.0)
        self.assertEqual(sides.top.run, 60)
        self.assertEqual(sides.top.step, 0.0)

    def test_dark_strip_is_not_a_non_dark_margin(self) -> None:
        """어두운 띠는 비검은 여백 요약에 들어오지 않음(검은 띠 규칙이 맡음)"""
        plane = _textured_pillar(margin_luma=16)
        sides = measure_flat_sides(plane, 80, 80, 60, CropBox(80, 60, 0, 0), dark_limit=DARK_LIMIT)
        self.assertEqual(sides.left.run, 0)

    def test_blend_column_is_counted_in_the_run(self) -> None:
        """평탄 끝의 반화소 혼합 열은 중앙값이 여백 그대로라 평탄 구간에 들어옴"""
        rows: list[list[int]] = []
        for row_index in range(60):
            picture = [_texture(row_index, column) for column in range(59)]
            rows.append([GREY] * 20 + [80] + picture)
        sides = measure_flat_sides(_plane(rows), 80, 80, 60, CropBox(80, 60, 0, 0), dark_limit=DARK_LIMIT)
        self.assertEqual(sides.left.run, 21)
        self.assertGreaterEqual(sides.left.step, NON_DARK_STEP_MIN)

    def test_missing_or_invalid_plane_returns_empty_sides(self) -> None:
        self.assertEqual(measure_flat_sides(b"", 0, 0, 0, None, dark_limit=DARK_LIMIT), EMPTY_FLAT_SIDES)
        self.assertEqual(measure_flat_sides(b"\x00" * 8, 8, 8, 8, None, dark_limit=DARK_LIMIT), EMPTY_FLAT_SIDES)


class TestFlatMarginCuts(unittest.TestCase):
    """9지점 합의로 비검은 평탄 여백을 자르는지 검증"""

    width: int = 480
    height: int = 360

    def _box(self) -> CropBox:
        return CropBox(self.width, self.height, 0, 0)

    def _cut(self, sides: list[FlatSides]) -> list[CropBox]:
        return apply_flat_margin_cuts([self._box() for _ in sides], sides, self.width, self.height)

    def test_all_points_agreeing_grey_margin_is_cut(self) -> None:
        sides = [_sides(left=_side(120)) for _ in range(9)]
        self.assertEqual(margin_cuts(sides, self.width, self.height), {"left": 120})
        self.assertEqual(self._cut(sides)[0], CropBox(self.width - 120, self.height, 120, 0))

    def test_picture_area_without_a_step_is_not_cut(self) -> None:
        """평탄 구간은 길지만 그림과의 계단이 없으면 자르지 않음(video2 위, video8 아래 근거)"""
        sides = [_sides(top=_side(200, step=0.0)) for _ in range(9)]
        self.assertEqual(margin_cuts(sides, self.width, self.height), {})
        self.assertEqual(self._cut(sides)[0], self._box())

    def test_levels_differing_across_points_are_not_cut(self) -> None:
        """지점마다 여백 밝기가 다르면 단색 장면으로 보고 자르지 않음"""
        sides = [_sides(left=_side(120, median=40.0 + index * 10)) for index in range(9)]
        self.assertEqual(margin_cuts(sides, self.width, self.height), {})

    def test_levels_within_tolerance_are_cut(self) -> None:
        """밝기 차가 허용 폭 안이면 같은 단색 여백으로 봄"""
        sides = [_sides(left=_side(120, median=48.0 + index % 2)) for index in range(9)]
        self.assertEqual(margin_cuts(sides, self.width, self.height), {"left": 120})

    def test_fewer_than_seven_agreeing_points_are_not_cut(self) -> None:
        """9지점 중 7지점이 같은 길이를 보여야 자름(6지점은 부족)"""
        agree = [_sides(left=_side(120)) for _ in range(6)]
        disagree = [_sides(left=_side(0)) for _ in range(3)]
        self.assertEqual(margin_cuts(agree + disagree, self.width, self.height), {})

    def test_weak_step_or_run_shorter_than_the_minimum_is_not_cut(self) -> None:
        weak = [_sides(left=_side(120, step=NON_DARK_STEP_MIN - 1)) for _ in range(9)]
        short = [_sides(left=_side(NON_DARK_MIN_LINES - 1)) for _ in range(9)]
        self.assertEqual(margin_cuts(weak, self.width, self.height), {})
        self.assertEqual(margin_cuts(short, self.width, self.height), {})

    def test_shortest_run_of_the_agreeing_cluster_is_used(self) -> None:
        """합의 묶음 안에서 가장 짧은 구간을 써 그림을 자르지 않음"""
        sides = [_sides(left=_side(120)) for _ in range(8)] + [_sides(left=_side(122))]
        self.assertEqual(margin_cuts(sides, self.width, self.height), {"left": 120})

    def test_blend_lines_extend_the_cut(self) -> None:
        """평탄 구간 끝의 혼합 줄은 함께 잘라냄"""
        sides = [_sides(left=_side(118, blend=2)) for _ in range(9)]
        self.assertEqual(margin_cuts(sides, self.width, self.height), {"left": 120})

    def test_bottom_and_right_cuts_keep_the_other_sides(self) -> None:
        sides = [_sides(bottom=_side(60), right=_side(90)) for _ in range(9)]
        self.assertEqual(margin_cuts(sides, self.width, self.height), {"bottom": 60, "right": 90})
        self.assertEqual(self._cut(sides)[0], CropBox(self.width - 90, self.height - 60, 0, 0))

    def test_point_without_its_own_run_also_receives_the_agreed_cut(self) -> None:
        """자기 면 평탄 구간이 짧은 지점도 합의한 길이를 받아 합집합이 프레임 끝으로 벌어지지 않음"""
        sides = [_sides(left=_side(120)) for _ in range(8)] + [_sides(left=_side(NON_DARK_MIN_LINES - 1))]
        result = self._cut(sides)
        self.assertEqual(result[0], CropBox(self.width - 120, self.height, 120, 0))
        self.assertEqual(result[8], CropBox(self.width - 120, self.height, 120, 0))

    def test_point_with_a_shorter_run_is_cut_only_to_its_own_run(self) -> None:
        """합의보다 짧은 구간을 본 지점은 자기 구간까지만 잘라 그림과 구간별 차이를 보존함"""
        sides = [_sides(left=_side(300)) for _ in range(7)] + [_sides(left=_side(100)) for _ in range(2)]
        self.assertEqual(self._cut(sides)[7], CropBox(self.width - 100, self.height, 100, 0))
        self.assertEqual(self._cut(sides)[0], CropBox(self.width - 300, self.height, 300, 0))

    def test_one_point_without_a_step_does_not_veto_the_consensus(self) -> None:
        """계단 없는 한 지점(평탄한 그림으로 이어짐)은 합의 판정에서 빠져 8지점 합의를 막지 않음"""
        sides = [_sides(left=_side(120)) for _ in range(8)] + [_sides(left=_side(480, step=0.0))]
        self.assertEqual(margin_cuts(sides, self.width, self.height), {"left": 120})

    def test_one_point_with_a_different_level_does_not_veto_the_consensus(self) -> None:
        """밝기가 다른 한 지점은 합의 묶음에서 빠져 8지점 합의를 막지 않음"""
        sides = [_sides(left=_side(120)) for _ in range(8)] + [_sides(left=_side(120, median=200.0))]
        self.assertEqual(margin_cuts(sides, self.width, self.height), {"left": 120})

    def test_one_or_two_boxes_cannot_trigger_the_flat_rule(self) -> None:
        """상자를 낸 지점이 한두 개뿐이면 그 평탄 구간으로 자르지 않음"""
        self.assertEqual(margin_cuts([_sides(left=_side(300))], self.width, self.height), {})
        self.assertEqual(margin_cuts([_sides(left=_side(300))] * 2, self.width, self.height), {})

    def test_three_agreeing_boxes_meet_the_absolute_minimum(self) -> None:
        sides = [_sides(left=_side(300))] * 3
        self.assertEqual(margin_cuts(sides, self.width, self.height), {"left": 300})

    def test_cut_covering_everything_falls_back_to_the_full_frame(self) -> None:
        sides = [_sides(left=_side(300), right=_side(300)) for _ in range(9)]
        self.assertEqual(self._cut(sides)[0], self._box())

    def test_mismatched_input_keeps_the_boxes(self) -> None:
        boxes = [self._box()]
        self.assertEqual(apply_flat_margin_cuts(boxes, [], self.width, self.height), boxes)
        self.assertEqual(apply_flat_margin_cuts([], [], self.width, self.height), [])

    def test_agreed_runs_requires_a_three_quarter_cluster(self) -> None:
        self.assertIsNone(agreed_runs([], 100))
        self.assertEqual(agreed_runs([40, 40, 41], 100), 40)
        self.assertIsNone(agreed_runs([40, 41, 90], 100))


class TestBrightOvershoot(unittest.TestCase):
    """첫 유지 줄의 밝은 과충격을 한 줄 안쪽으로 옮기는지 검증"""

    width: int = 60
    height: int = 40

    def test_ringing_first_column_moves_the_box_inward_and_aligns_even(self) -> None:
        """첫 유지 열이 이웃보다 밝으면 한 열 안쪽으로 옮기고 짝수로 맞춤"""
        rows = [[16] * 30 + [200] + [120] * 29 for _ in range(self.height)]
        fixed = fix_bright_overshoot(
            _plane(rows), self.width, self.width, self.height, CropBox(31, self.height, 30, 0)
        )
        # - 유지 열 30이 이웃 31보다 밝아 안쪽 31로 옮기고, 홀수라 짝수 올림해 32
        self.assertEqual(fixed, CropBox(28, 40, 32, 0))
        self.assertGreater(200 - 120, EDGE_OVERSHOOT_LEVELS)

    def test_first_column_that_is_darker_is_left_alone(self) -> None:
        """그림이 섞여 첫 유지 열이 이웃보다 어두운 쪽은 검은 띠 규칙이 맡음"""
        rows = [[16] * 30 + [60] + [120] * 29 for _ in range(self.height)]
        box = CropBox(30, self.height, 30, 0)
        self.assertEqual(fix_bright_overshoot(_plane(rows), 60, 60, 40, box), box)

    def test_brightness_within_the_limit_is_left_alone(self) -> None:
        rows = [[16] * 30 + [121] + [120] * 29 for _ in range(self.height)]
        box = CropBox(31, self.height, 30, 0)
        self.assertEqual(fix_bright_overshoot(_plane(rows), 60, 60, 40, box), box)

    def test_top_row_overshoot_moves_down_and_aligns_even(self) -> None:
        """위쪽을 자른 뒤 첫 유지 줄이 밝은 과충격이면 한 줄 내려 짝수로 맞춤"""
        top = 19
        rows = [[120] * self.width for _ in range(self.height)]
        rows[top] = [190] * self.width
        remaining = self.height - top
        box = CropBox(60, remaining, 0, top)
        fixed = fix_bright_overshoot(_plane(rows), self.width, self.width, self.height, box)
        # - 위를 한 줄 내린 20에서 아래 끝 40까지라 높이는 20
        self.assertEqual(fixed, CropBox(60, 20, 0, top + 1))

    def test_side_at_the_frame_edge_is_not_touched(self) -> None:
        """상자가 프레임 끝에 닿은 면은 그림 첫 줄이라 밝기 비교를 하지 않음"""
        rows = [[200] * self.width] + [[120] * self.width for _ in range(self.height - 1)]
        box = CropBox(60, self.height, 0, 0)
        self.assertEqual(fix_bright_overshoot(_plane(rows), 60, 60, 40, box), box)

    def test_degenerate_box_is_returned_unchanged(self) -> None:
        box = CropBox(0, 0, 0, 0)
        self.assertEqual(fix_bright_overshoot(b"", 0, 0, 0, box), box)


class TestTransposeFlatSides(unittest.TestCase):
    """회전 영상에서 네 면 평탄 요약을 표시 방향으로 옮기는지 검증"""

    def _four_sides(self) -> FlatSides:
        return _sides(top=_side(1), bottom=_side(2), left=_side(3), right=_side(4))

    def test_no_rotation_keeps_the_sides(self) -> None:
        self.assertEqual(transpose_flat_sides(self._four_sides(), 0), self._four_sides())

    def test_half_turn_swaps_opposite_sides(self) -> None:
        moved = transpose_flat_sides(self._four_sides(), 180)
        self.assertEqual((moved.top.run, moved.bottom.run, moved.left.run, moved.right.run), (2, 1, 4, 3))

    def test_quarter_turn_moves_sides_clockwise(self) -> None:
        moved = transpose_flat_sides(self._four_sides(), 90)
        self.assertEqual((moved.top.run, moved.right.run, moved.bottom.run, moved.left.run), (3, 1, 4, 2))


class TestInvalidPlane(unittest.TestCase):
    """평면이 비었거나 상자가 없으면 빈 요약을 내는지 검증"""

    def test_missing_or_invalid_plane_returns_empty_sides(self) -> None:
        self.assertEqual(measure_flat_sides(b"", 0, 0, 0, None, dark_limit=DARK_LIMIT), EMPTY_FLAT_SIDES)
        self.assertEqual(measure_flat_sides(b"\x00" * 8, 8, 8, 8, None, dark_limit=DARK_LIMIT), EMPTY_FLAT_SIDES)


if __name__ == "__main__":
    unittest.main()
