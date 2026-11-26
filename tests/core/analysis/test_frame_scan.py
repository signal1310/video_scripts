"""화질 분석 9지점 키프레임 스캔의 Y 평면 계산, CLI 폴백, 회전 전치 검증"""

import os
import tempfile
import threading
import unittest
from collections.abc import Iterator, Sequence
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, ClassVar, cast
from unittest import mock

from src.core.analysis.frame_analysis import ActiveRegion, CropBox, EdgeDarkness, merge_crop_union
from src.core.analysis.frame_scan import (
    BLACK_FRAME_PIXEL_RATIO,
    KeyframeScanResult,
    ScanCommandRunner,
    _declared_rotation,
    _luma_bytes,
    _scan_with_pyav,
    build_scan_command,
    luma_frame_stats,
    parse_gray_frame,
    scan_keyframe_points,
    scan_with_cli,
    transpose_crop_box,
    transpose_edge_darkness,
)
from tests._frame_analysis_fakes import FakeStderrRunner

if TYPE_CHECKING:
    from av.video.frame import VideoFrame
    from av.video.stream import VideoStream


def _plane(rows: list[list[int]], line_size: int | None = None) -> bytes:
    """행 목록을 Y 평면 바이트로 만듦. 행 바이트 수가 더 크면 0으로 채움"""
    width = len(rows[0])
    stride = line_size if line_size is not None else width
    data = bytearray()
    for row in rows:
        data.extend(row)
        data.extend([0] * (stride - width))
    return bytes(data)


def _noisy_row(width: int, luma: int, amplitude: int = 2) -> list[int]:
    """지정 휘도 주변에서 +-amplitude로 번갈아 흔들리는 띠 줄"""
    return [luma + (amplitude if index % 2 == 0 else -amplitude) for index in range(width)]


def _gradient_row(width: int, mean: float, deviation: float) -> list[int]:
    """평균과 평균 편차를 맞춘 그라데이션 줄. 픽셀을 -1, +1로 번갈아 편차만큼 벌림"""
    low = max(0, round(mean - deviation))
    high = min(255, round(mean + deviation))
    return [low if index % 2 == 0 else high for index in range(width)]


class TestLumaStats(unittest.TestCase):
    """Y 평면 검은 픽셀 비율과 검은 여백 상자 계산 검증"""

    def test_black_frame_returns_no_box(self) -> None:
        box, ratio, _edges = luma_frame_stats(bytes([16] * 64), 8, 8, 8)
        self.assertIsNone(box)
        self.assertEqual(ratio, 1.0)

    def test_letterbox_rows_are_cropped(self) -> None:
        rows = [[16] * 8, [16] * 8] + [[100] * 8 for _ in range(4)] + [[16] * 8, [16] * 8]
        box, ratio, _edges = luma_frame_stats(_plane(rows), 8, 8, 8)
        self.assertEqual(box, CropBox(8, 4, 0, 2))
        self.assertEqual(ratio, 0.5)

    def test_pillar_columns_are_cropped(self) -> None:
        rows = [[16, 16, 100, 100, 100, 100, 16, 16] for _ in range(8)]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), 8, 8, 8)
        self.assertEqual(box, CropBox(4, 8, 2, 0))

    def test_full_content_keeps_full_frame(self) -> None:
        rows = [[30] * 8 for _ in range(8)]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), 8, 8, 8)
        self.assertEqual(box, CropBox(8, 8, 0, 0))

    def test_dark_edge_without_step_is_picture(self) -> None:
        """그림과 밝기 차가 계단 기준 미만인 어두운 가장자리는 여백이 아님"""
        rows = [[120] * 8 for _ in range(4)] + [[40] * 8, [32] * 8, [26] * 8, [22] * 8, [20] * 8, [18] * 8]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), 8, 8, 10)
        self.assertEqual(box, CropBox(8, 10, 0, 0))

    def test_padded_rows_and_invalid_sizes(self) -> None:
        rows = [[16] * 8, [100] * 8, [100] * 8, [16] * 8]
        box, _ratio, _edges = luma_frame_stats(_plane(rows, line_size=10), 10, 8, 4)
        self.assertEqual(box, CropBox(8, 2, 0, 1))
        self.assertEqual(luma_frame_stats(b"", 0, 0, 0), (None, 1.0, EdgeDarkness(0, 0, 0, 0)))
        self.assertEqual(luma_frame_stats(b"\x00", 4, 8, 8), (None, 1.0, EdgeDarkness(0, 0, 0, 0)))

    def test_dark_edges_covering_everything_returns_no_box(self) -> None:
        box, _ratio, _edges = luma_frame_stats(bytes([25] * 64), 8, 8, 8)
        self.assertIsNone(box)


class TestBarEdgeRules(unittest.TestCase):
    """평탄도, 경계 계단, 띠 안 자막 섬 규칙으로 검은 여백을 판정하는지 검증"""

    def test_four_side_bars_are_cropped(self) -> None:
        """4면 띠가 모두 있으면 각 면의 띠 두께만큼 상자를 줄임"""
        rows = [[16] * 32 for _ in range(3)]
        rows += [[16] * 4 + [120] * 26 + [16] * 2 for _ in range(16)]
        rows += [[16] * 32 for _ in range(5)]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), 32, 32, len(rows))
        self.assertEqual(box, CropBox(26, 16, 4, 3))

    def test_asymmetric_side_bars_are_cropped(self) -> None:
        """한쪽에만 있는 띠도 그 두께만큼 자름"""
        rows = [[16] * 6 + [120] * 26 for _ in range(24)]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), 32, 32, 24)
        self.assertEqual(box, CropBox(26, 24, 6, 0))

    def test_noisy_bars_are_cropped(self) -> None:
        """띠 위 +-2 노이즈는 평탄도 기준 안이라 띠로 인정"""
        rows = [_noisy_row(32, 8) for _ in range(4)] + [[120] * 32 for _ in range(20)]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), 32, 32, 24)
        self.assertEqual(box, CropBox(32, 20, 0, 4))

    def test_limited_range_bar_is_cropped(self) -> None:
        """제한 범위 검은색 16에 노이즈가 있는 아래 띠도 자름"""
        rows = [[120] * 32 for _ in range(18)] + [_noisy_row(32, 16) for _ in range(6)]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), 32, 32, 24)
        self.assertEqual(box, CropBox(32, 18, 0, 0))

    def test_vignette_gradient_is_not_a_bar(self) -> None:
        """어두운 장면의 완만한 비네트는 평탄도와 계단 조건을 못 넘어 여백이 아님"""
        width, ramp = 32, 44
        rows = [[120] * width for _ in range(30)]
        for step in range(ramp):
            ratio = step / (ramp - 1)
            rows.append(_gradient_row(width, 27 - 22 * ratio, 15 - 13 * ratio))
        box, _ratio, _edges = luma_frame_stats(_plane(rows), width, width, len(rows))
        self.assertEqual(box, CropBox(width, len(rows), 0, 0))

    def test_text_island_inside_top_bar_crops_below_text(self) -> None:
        """띠 안 자막 줄은 섬으로 보고 지나쳐, 자막 아래 띠 경계를 씀"""
        rows = [[16] * 200 for _ in range(40)]
        rows += [_gradient_row(200, 150, 40) for _ in range(12)]
        rows += [[16] * 200 for _ in range(30)]
        rows += [[130] * 200 for _ in range(158)]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), 200, 200, len(rows))
        self.assertEqual(box, CropBox(200, 158, 0, 82))

    def test_text_attached_to_picture_is_not_skipped(self) -> None:
        """띠 간격 없이 그림에 붙은 자막은 그림으로 남겨 잘라내지 않음"""
        rows = [[16] * 32 for _ in range(4)]
        rows += [_gradient_row(32, 150, 40) for _ in range(3)]
        rows += [[130] * 32 for _ in range(17)]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), 32, 32, len(rows))
        self.assertEqual(box, CropBox(32, 20, 0, 4))

    def test_thick_text_block_is_not_skipped(self) -> None:
        """축 길이 대비 섬 상한보다 두꺼운 비-띠 구간은 그림으로 보고 지나치지 않음"""
        rows = [[16] * 200 for _ in range(6)]
        rows += [[120] * 200 for _ in range(8)]
        rows += [[16] * 200 for _ in range(6)]
        rows += [[120] * 200 for _ in range(40)]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), 200, 200, len(rows))
        self.assertEqual(box, CropBox(200, 54, 0, 6))

    def test_dark_scene_without_black_ratio_has_no_bars(self) -> None:
        """검은 화면 기준에 못 미치는 어두운 장면은 띠 없이 전체 프레임"""
        rows = [[20 + step] * 32 for step in range(21)]
        box, ratio, _edges = luma_frame_stats(_plane(rows), 32, 32, 21)
        self.assertLess(ratio, BLACK_FRAME_PIXEL_RATIO)
        self.assertEqual(box, CropBox(32, 21, 0, 0))

    def test_text_island_inside_left_pillar_crops_right_of_text(self) -> None:
        """왼쪽 기둥 안 세로 자막 열은 섬으로 보고 지나쳐, 자막 오른쪽 띠 경계를 씀"""
        width, height = 200, 60
        rows = [
            [16] * 20 + [110 if row_index % 2 == 0 else 190] * 8 + [16] * 10 + [130] * 162
            for row_index in range(height)
        ]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), width, width, height)
        self.assertEqual(box, CropBox(162, 60, 38, 0))

    def test_text_island_inside_right_pillar_crops_left_of_text(self) -> None:
        """오른쪽 기둥 안 세로 자막 열도 섬으로 보고 지나쳐, 자막 왼쪽 띠 경계를 씀"""
        width, height = 200, 60
        rows = [
            [130] * 162 + [16] * 10 + [110 if row_index % 2 == 0 else 190] * 8 + [16] * 20
            for row_index in range(height)
        ]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), width, width, height)
        self.assertEqual(box, CropBox(162, 60, 0, 0))

    def test_horizontal_vignette_is_not_a_column_bar(self) -> None:
        """좌우 어두운 완만한 비네트는 열 평탄도와 계단 조건을 못 넘어 여백이 아님"""
        width, height, ramp, picture = 60, 40, 22, 38
        rows = []
        for row_index in range(height):
            values = []
            for column in range(ramp):
                ratio = column / (ramp - 1)
                mean = 5 + 22 * ratio
                deviation = 2 + 13 * ratio
                values.append(round(mean + (deviation if row_index % 2 == 0 else -deviation)))
            rows.append(values + [120] * picture)
        box, _ratio, _edges = luma_frame_stats(_plane(rows), width, width, height)
        self.assertEqual(box, CropBox(width, height, 0, 0))

    def test_union_keeps_the_thickest_bar_of_each_side(self) -> None:
        """프레임별 상자 합집합은 어느 표본에서도 그림을 자르지 않음"""
        frames = [
            _plane([[16] * 32 for _ in range(10)] + [[130] * 32 for _ in range(78)] + [[16] * 32 for _ in range(12)]),
            _plane([[16] * 32 for _ in range(14)] + [[130] * 32 for _ in range(74)] + [[16] * 32 for _ in range(12)]),
        ]
        boxes = [box for data in frames if (box := luma_frame_stats(data, 32, 32, 100)[0]) is not None]
        region = merge_crop_union(boxes, 32, 100)
        self.assertTrue(region.has_black_bars)
        self.assertEqual((region.width, region.height, region.x, region.y), (32, 78, 0, 10))

    def test_union_prefers_the_thickness_most_points_agree_on(self) -> None:
        """어두운 근거가 없으면 다수 두께를 채택하지 않고 바깥 좌표를 씀(그림 보호)"""
        boxes = [CropBox(242, 180, 39, 30)] * 5 + [CropBox(242, 210, 39, 0)]
        region = merge_crop_union(boxes, 320, 240)
        self.assertEqual((region.width, region.height, region.x, region.y), (242, 210, 38, 0))
        self.assertTrue(region.has_black_bars)
        self.assertFalse(region.dark_outliers_ignored)

    def test_union_ignores_outlier_with_dark_evidence(self) -> None:
        """바깥 지점이 더하는 띠가 어둡다는 근거가 있으면 다수 두께를 채택하고 안내 플래그를 세움"""
        boxes = [CropBox(242, 180, 39, 30)] * 5 + [CropBox(242, 240, 39, 0)]
        darkness = [EdgeDarkness(30, 30, 39, 39)] * 5 + [EdgeDarkness(30, 40, 39, 39)]
        region = merge_crop_union(boxes, 320, 240, darkness=darkness)
        self.assertEqual((region.width, region.height, region.x, region.y), (242, 180, 38, 30))
        self.assertTrue(region.has_black_bars)
        self.assertTrue(region.dark_outliers_ignored)

    def test_union_keeps_outer_union_without_enough_agreement(self) -> None:
        """두께가 반씩 갈리면 다수 두께가 없어 전체 바깥 좌표를 씀"""
        boxes = [CropBox(242, 180, 39, 30)] * 2 + [CropBox(242, 240, 39, 0)] * 2
        region = merge_crop_union(boxes, 320, 240)
        self.assertEqual((region.width, region.height, region.x, region.y), (242, 240, 38, 0))

    def test_union_of_full_frame_boxes_has_no_bars(self) -> None:
        """완만한 그라데이션만 있는 표본에서는 상자가 프레임 전체라 검은 띠 없음"""
        frames = []
        for _ in range(2):
            rows = [[120] * 32 for _ in range(10)]
            rows += [_gradient_row(32, 27 - 22 * step / 21, 15 - 13 * step / 21) for step in range(22)]
            frames.append(_plane(rows))
        boxes = [box for data in frames if (box := luma_frame_stats(data, 32, 32, 32)[0]) is not None]
        region = merge_crop_union(boxes, 32, 32)
        self.assertFalse(region.has_black_bars)
        self.assertEqual((region.width, region.height, region.x, region.y), (32, 32, 0, 0))



class TestWeakStepAndResidualBars(unittest.TestCase):
    """계단이 약한 평탄 띠, 4면 독립 판정, 잔여 여백 줄 처리 검증"""

    def test_flat_bar_with_weak_step_and_soft_ramp_is_cropped(self) -> None:
        """줄 편차가 0에 가까운 긴 띠는 경계 계단이 약해도 자르고, 끝의 얕은 경사도 함께 자름"""
        width = 320
        rows = [[16] * width for _ in range(23)]
        rows += [[16 + index // 4] * width for index in range(7)]
        for row_index in range(210):
            rows.append([40 if (column + row_index) % 2 == 0 else 4 for column in range(width)])
        box, _ratio, _edges = luma_frame_stats(_plane(rows), width, width, len(rows))
        self.assertEqual(box, CropBox(width, 210, 0, 30))

    def test_frame_margin_crops_every_side_independently(self) -> None:
        """4면 액자형 여백은 한 면이 어두운 그림과 붙어도 나머지 면을 각각 판정해 모두 자름"""
        width, height = 128, 96
        rows = [[16] * width for _ in range(12)]
        for _ in range(72):
            rows.append([16] * 10 + [110] * 108 + [16] * 10)
        rows += [[16] * width for _ in range(12)]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), width, width, height)
        self.assertEqual(box, CropBox(108, 72, 10, 12))

    def test_thin_residual_margin_after_bar_is_removed(self) -> None:
        """경계 뒤에 남은 얇은 잔여 여백 줄은 다음 줄과의 차이가 있으면 함께 자름"""
        width, height = 160, 120
        rows = [[16] * width for _ in range(20)]
        rows += [[27] * width, [31] * width]
        rows += [[120] * width for _ in range(height - 22)]
        box, _ratio, _edges = luma_frame_stats(_plane(rows), width, width, height)
        self.assertEqual(box, CropBox(width, height - 22, 0, 22))


class TestCropConsensusDarkEvidence(unittest.TestCase):
    """합집합이 띠 근거로만 다수 두께를 채택하고, 무시한 지점을 안내 플래그로 남기는지 검증"""

    width: int = 32
    height: int = 240
    bar: int = 120

    def _stats(
        self,
        *,
        top_bar: int,
        left_bar: int = 0,
        noisy_top: bool = False,
        dim_top: bool = False,
    ) -> tuple[CropBox, EdgeDarkness]:
        """상단 띠 두께와 왼쪽 기둥 두께를 지정한 합성 프레임의 상자와 어두운 면 두께

        - `noisy_top`은 위쪽이 어둡지만 편차가 커서 띠로도 근거로도 인정되지 않는 지점
        - `dim_top`은 위쪽이 어둡고 평탄하지만 그림과의 계단이 약해 띠로는 인정되지 않고 근거만 남는 지점
        """
        picture = 28 if dim_top else 120
        rows: list[list[int]] = []
        for row_index in range(self.height):
            if row_index < top_bar:
                if noisy_top:
                    rows.append(_noisy_row(self.width, 23, 17))
                elif dim_top:
                    rows.append(_noisy_row(self.width, 23, 5))
                else:
                    rows.append([16] * self.width)
            else:
                rows.append([16] * left_bar + [picture] * (self.width - left_bar))
        box, _ratio, edges = luma_frame_stats(_plane(rows), self.width, self.width, self.height)
        self.assertIsNotNone(box)
        assert box is not None
        return box, edges

    def _merge(self, points: list[tuple[CropBox, EdgeDarkness]]) -> ActiveRegion:
        return merge_crop_union(
            [box for box, _edges in points],
            self.width,
            self.height,
            darkness=[edges for _box, edges in points],
        )

    def test_picture_points_at_the_frame_edge_are_not_cut(self) -> None:
        """7지점은 띠, 2지점은 프레임 끝까지 그림이면 그 면을 자르지 않음"""
        points = [self._stats(top_bar=self.bar) for _ in range(7)]
        points += [self._stats(top_bar=0) for _ in range(2)]
        region = self._merge(points)
        self.assertEqual((region.width, region.height, region.x, region.y), (self.width, self.height, 0, 0))
        self.assertFalse(region.has_black_bars)
        self.assertFalse(region.dark_outliers_ignored)

    def test_dark_but_noisy_points_are_not_evidence_and_keep_the_full_frame(self) -> None:
        """평균은 어둡고 편차만 큰 2지점은 띠 근거가 아니므로 다수 두께를 채택하지 않음"""
        points = [self._stats(top_bar=self.bar) for _ in range(7)]
        points += [self._stats(top_bar=self.bar, noisy_top=True) for _ in range(2)]
        region = self._merge(points)
        self.assertEqual((region.width, region.height, region.x, region.y), (self.width, self.height, 0, 0))
        self.assertFalse(region.has_black_bars)
        self.assertFalse(region.dark_outliers_ignored)

    def test_flat_dark_evidence_keeps_the_crop_and_flags_review(self) -> None:
        """경계 계단이 약해 띠로 인정되지 않아도 위쪽이 어둡고 평탄하면 근거로 무시해 자름"""
        points = [self._stats(top_bar=self.bar) for _ in range(7)]
        points += [self._stats(top_bar=self.bar, dim_top=True) for _ in range(2)]
        region = self._merge(points)
        self.assertEqual((region.width, region.height, region.x, region.y), (self.width, self.bar, 0, self.bar))
        self.assertTrue(region.has_black_bars)
        self.assertTrue(region.dark_outliers_ignored)

    def test_full_agreement_keeps_the_crop_without_review_flag(self) -> None:
        """모든 지점이 같은 두께면 무시한 지점이 없어 안내 플래그를 세우지 않음"""
        region = self._merge([self._stats(top_bar=self.bar) for _ in range(9)])
        self.assertEqual((region.width, region.height, region.x, region.y), (self.width, self.bar, 0, self.bar))
        self.assertTrue(region.has_black_bars)
        self.assertFalse(region.dark_outliers_ignored)

    def test_each_edge_uses_its_own_dark_evidence(self) -> None:
        """위쪽은 평탄한 근거로 무시해 자르고, 왼쪽은 근거가 없어 프레임 끝까지 남김"""
        points = [self._stats(top_bar=self.bar, left_bar=8) for _ in range(7)]
        points += [self._stats(top_bar=self.bar, dim_top=True) for _ in range(2)]
        region = self._merge(points)
        self.assertEqual((region.width, region.height, region.x, region.y), (self.width, self.bar, 0, self.bar))
        self.assertTrue(region.dark_outliers_ignored)


class TestEdgeDarkness(unittest.TestCase):
    """지점별 어두운 면 두께 계산과 표시 회전 전치 검증"""

    def test_bar_thickness_is_reported_per_side(self) -> None:
        """어둡고 평탄한 위 4줄, 아래 6줄 검은 띠는 그 두께를 어두운 구간으로 남김"""
        rows = [_noisy_row(32, 23, 5) for _ in range(4)]
        rows += [[120] * 32 for _ in range(14)]
        rows += [[16] * 32 for _ in range(6)]
        _box, _ratio, edges = luma_frame_stats(_plane(rows), 32, 32, len(rows))
        self.assertEqual((edges.top, edges.bottom), (4, 6))

    def test_dark_textured_picture_is_not_band_evidence(self) -> None:
        """평균은 어둡지만 편차가 큰 그림 구간은 띠 근거로 쓰지 않음

        - 줄마다 30/10으로 번갈아 평균 20이 되는 위 200줄과, 줄 안에서 30/10으로 번갈아 편차가 큰 위 200줄 모두
          띠 판정을 통과하지 못하므로 근거 두께가 0
        """
        width, dark_rows = 320, 200
        alternating_rows = [[30 if row_index % 2 == 0 else 10] * width for row_index in range(dark_rows)]
        alternating_pixels = [
            [30 if column % 2 == 0 else 10 for column in range(width)] for _ in range(dark_rows)
        ]
        for dark in (alternating_rows, alternating_pixels):
            rows = dark + [[120] * width for _ in range(40)]
            box, _ratio, edges = luma_frame_stats(_plane(rows), width, width, len(rows))
            self.assertEqual(box, CropBox(width, 240, 0, 0))
            self.assertEqual(edges.top, 0)

    def test_picture_edges_have_no_dark_band(self) -> None:
        """프레임 끝까지 밝은 그림이면 어두운 구간 두께는 0"""
        _box, _ratio, edges = luma_frame_stats(_plane([[120] * 32 for _ in range(24)]), 32, 32, 24)
        self.assertEqual(edges, EdgeDarkness(0, 0, 0, 0))

    def test_transpose_follows_the_crop_box_rotation(self) -> None:
        """면별 두께를 상자 전치와 같은 면 대응으로 회전"""
        edges = EdgeDarkness(top=1, bottom=2, left=3, right=4)
        self.assertEqual(transpose_edge_darkness(edges, 0), edges)
        self.assertEqual(transpose_edge_darkness(edges, 180), EdgeDarkness(2, 1, 4, 3))
        self.assertEqual(transpose_edge_darkness(edges, 90), EdgeDarkness(3, 4, 2, 1))
        self.assertEqual(transpose_edge_darkness(edges, 270), EdgeDarkness(4, 3, 1, 2))


class TestScanCommandAndGrayFrame(unittest.TestCase):
    """CLI 스캔 명령과 회색조 PGM 해석 검증"""

    def test_scan_command_writes_one_gray_frame(self) -> None:

        args = build_scan_command(
            ffmpeg_executable="ffmpeg",
            source_path="movie.mp4",
            start_sec=1.5,
            fast_keyframe_seek=True,
            frame_path="frame.pgm",
        )
        self.assertEqual(args[args.index("-ss") + 1], "1.500")
        self.assertIn("-noaccurate_seek", args)
        self.assertEqual(args[args.index("-skip_frame") + 1], "nokey")
        self.assertEqual(args[args.index("-vf") + 1], "format=gray")
        self.assertEqual(args[args.index("-c:v") + 1], "pgm")
        self.assertEqual(args[-1], "frame.pgm")
        self.assertNotIn("-", args)
        self.assertNotIn("blackframe=amount=0:threshold=24,cropdetect=24:2:0:0", args)
        slow = build_scan_command(
            ffmpeg_executable="ffmpeg",
            source_path="movie.mp4",
            start_sec=0.0,
            fast_keyframe_seek=False,
            frame_path="frame.pgm",
        )
        self.assertNotIn("-noaccurate_seek", slow)
        self.assertIn("-y", slow)

    def test_parse_gray_frame_reads_header_and_plane(self) -> None:
        data = b"P5\n4 2\n255\n" + bytes(range(8))
        self.assertEqual(parse_gray_frame(data), (bytes(range(8)), 4, 2))
        commented = b"P5\n# comment\n4 2\n255\n" + bytes(range(8))
        self.assertEqual(parse_gray_frame(commented), (bytes(range(8)), 4, 2))

    def test_parse_gray_frame_rejects_other_shapes(self) -> None:
        self.assertIsNone(parse_gray_frame(b""))
        self.assertIsNone(parse_gray_frame(b"P2\n4 2\n255\n" + bytes(range(8))))
        self.assertIsNone(parse_gray_frame(b"P5\n4 2\n65535\n" + bytes(16)))
        self.assertIsNone(parse_gray_frame(b"P5\n0 2\n255\n"))
        self.assertIsNone(parse_gray_frame(b"P5\n4 2\n255\n" + bytes(7)))
        self.assertIsNone(parse_gray_frame(b"P5\nwide 2\n255\n" + bytes(8)))


class TestRotation(unittest.TestCase):
    """회전 각도 읽기와 상자 전치 검증"""

    def test_declared_rotation_reads_metadata(self) -> None:
        class _Stream:
            def __init__(self, metadata: dict[str, str]) -> None:
                self.metadata = metadata

        self.assertEqual(_declared_rotation(cast("VideoStream", _Stream({"rotate": "90"}))), 90)
        self.assertEqual(_declared_rotation(cast("VideoStream", _Stream({"rotate": "bad"}))), 0)
        self.assertEqual(_declared_rotation(cast("VideoStream", _Stream({}))), 0)

    def test_transpose_quarter_turns(self) -> None:
        box = CropBox(600, 720, 100, 200)
        self.assertEqual(transpose_crop_box(box, 1080, 1920, 0), box)
        self.assertEqual(transpose_crop_box(box, 1080, 1920, 90), CropBox(720, 600, 1000, 100))
        self.assertEqual(transpose_crop_box(box, 1080, 1920, 270), CropBox(720, 600, 200, 380))

    def test_transpose_half_turn_mirrors_asymmetric_box(self) -> None:
        """180도 회전에서 비대칭 여백 상자의 x, y를 코딩 크기 기준으로 반전하는지 검증"""
        box = CropBox(600, 720, 100, 200)
        self.assertEqual(transpose_crop_box(box, 1080, 1920, 180), CropBox(600, 720, 380, 1000))

    def test_pyav_scan_uses_frame_rotation(self) -> None:
        """디스플레이 매트릭스 회전(-90, 90, 180)을 시계 방향 전치에 반영하는지 검증"""
        self.assertEqual(self._scan_boxes(-90), (CropBox(40, 70, 0, 10),))
        self.assertEqual(self._scan_boxes(90), (CropBox(40, 70, 0, 0),))
        self.assertEqual(self._scan_boxes(180), (CropBox(70, 40, 0, 0),))

    def test_pyav_scan_falls_back_to_declared_rotation(self) -> None:
        """프레임 회전이 0이면 스트림 메타데이터 회전을 쓰고 -90과 같은 방향인지 검증"""
        self.assertEqual(self._scan_boxes(0, rotate="90"), (CropBox(40, 70, 0, 10),))
        self.assertEqual(self._scan_boxes(0), (CropBox(70, 40, 10, 0),))

    def _scan_boxes(self, rotation: int, *, rotate: str | None = None) -> tuple[CropBox, ...]:
        """왼쪽 10픽셀 검은 기둥이 있는 80x40 합성 프레임을 PyAV 스캔으로 처리한 상자"""
        data = b"".join(bytes([16] * 10 + [100] * 70) for _ in range(40))

        class _Plane:
            line_size = 80

            def __bytes__(self) -> bytes:
                return data

        class _Frame:
            format: object = SimpleNamespace(name="gray")
            planes: ClassVar[list[_Plane]] = [_Plane()]
            width = 80
            height = 40

            def __init__(self) -> None:
                self.rotation = rotation

        class _Stream:
            metadata: ClassVar[dict[str, str]] = {} if rotate is None else {"rotate": rotate}
            time_base = 0.001

        class _Container:
            streams: ClassVar[SimpleNamespace] = SimpleNamespace(video=[_Stream()])
            closed = False

            def seek(self, offset: int, **kwargs: object) -> None:
                del offset, kwargs

            def decode(self, video: int = 0) -> Iterator[_Frame]:
                del video
                return iter([_Frame()])

            def close(self) -> None:
                self.closed = True

        container = _Container()
        with (
            mock.patch("src.core.analysis.frame_scan.open_shared_binary", return_value=None),
            mock.patch("av.open", return_value=container),
        ):
            result = _scan_with_pyav("movie.mp4", [0.0], cancel_event=None, on_point=None)
        self.assertTrue(container.closed)
        return result.boxes

    def test_luma_bytes_uses_plane_or_reformats(self) -> None:
        class _Plane:
            line_size = 4

            def __bytes__(self) -> bytes:
                return b"\x10" * 4

        class _Format:
            name = "yuv420p"

        class _Frame:
            format: object = _Format()
            planes: ClassVar[list[_Plane]] = [_Plane()]
            width = 4
            height = 1
            reformatted = 0

            def reformat(self, format: str) -> "_Frame":
                del format
                self.reformatted += 1
                return self

        frame = _Frame()
        self.assertEqual(_luma_bytes(cast("VideoFrame", frame)), (b"\x10" * 4, 4, 4, 1))
        self.assertEqual(frame.reformatted, 0)
        frame.format = type("F", (), {"name": "yuv420p10le"})()
        self.assertEqual(_luma_bytes(cast("VideoFrame", frame)), (b"\x10" * 4, 4, 4, 1))
        self.assertEqual(frame.reformatted, 1)


class TestScanWithCli(unittest.TestCase):
    """CLI 9지점 스캔 결과와 PyAV 실패 폴백 검증"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.work_dir = tmp.name

    def _scan(
        self,
        runner: FakeStderrRunner | ScanCommandRunner,
        *,
        points: Sequence[float] = (0.0,),
        cancel_event: threading.Event | None = None,
    ) -> KeyframeScanResult:
        return scan_with_cli(
            source_path="movie.mp4",
            points=list(points),
            ffmpeg_executable="ffmpeg",
            runner=runner,
            work_dir=self.work_dir,
            fast_keyframe_seek=True,
            cancel_event=cancel_event,
            on_point=None,
        )

    def test_cli_scan_collects_boxes_and_black_points(self) -> None:
        runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        seen: list[tuple[int, int]] = []
        result = scan_with_cli(
            source_path="movie.mp4",
            points=[0.0, 5.0, 10.0],
            ffmpeg_executable="ffmpeg",
            runner=runner,
            work_dir=self.work_dir,
            fast_keyframe_seek=True,
            cancel_event=None,
            on_point=lambda done, total: seen.append((done, total)),
        )
        self.assertEqual(result.boxes, (CropBox(720, 1080, 600, 0),) * 3)
        self.assertEqual(result.black_points, frozenset())
        self.assertEqual(result.darkness, (EdgeDarkness(0, 0, 600, 600),) * 3)
        self.assertEqual(seen, [(1, 3), (2, 3), (3, 3)])

    def test_cli_scan_uses_one_process_per_point_and_leaves_no_file(self) -> None:
        runner = FakeStderrRunner(boxes=[(720, 1080, 600, 0)])
        self._scan(runner, points=(0.0, 5.0))
        self.assertEqual(len(runner.commands), 2)
        self.assertEqual(os.listdir(self.work_dir), [])
        self.assertTrue(Path(runner.commands[0][-1]).name.startswith("scan_"))

    def test_cli_scan_keeps_full_frame_for_dark_vignette(self) -> None:
        """완만한 어두운 비네트는 CLI 경로에서도 여백이 아니라 전체 프레임"""
        runner = FakeStderrRunner(boxes=[(1920, 1080, 0, 0)])
        result = self._scan(runner)
        self.assertEqual(result.boxes, (CropBox(1920, 1080, 0, 0),))

    def test_cli_scan_treats_black_or_failed_point_as_invalid(self) -> None:
        black = self._scan(FakeStderrRunner(black_frame=True))
        self.assertEqual(black.black_points, frozenset({0}))
        failed = self._scan(FakeStderrRunner(black_returncode=1))
        self.assertEqual(failed.black_points, frozenset({0}))

    def test_cli_scan_treats_unreadable_frame_as_invalid(self) -> None:
        def truncated(args: list[str], work_dir: str) -> tuple[int, str]:
            _ = work_dir
            Path(args[-1]).write_bytes(b"P5\n4 2\n255\nshort")
            return (0, "")

        result = self._scan(truncated)
        self.assertEqual(result.boxes, ())
        self.assertEqual(result.black_points, frozenset({0}))
        self.assertEqual(os.listdir(self.work_dir), [])

    def test_cli_scan_removes_frame_when_runner_raises(self) -> None:
        def failing(args: list[str], work_dir: str) -> tuple[int, str]:
            _ = work_dir
            Path(args[-1]).write_bytes(b"P5\n4 2\n255\n" + bytes(8))
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            self._scan(failing)
        self.assertEqual(os.listdir(self.work_dir), [])

    def test_pyav_failure_falls_back_to_cli(self) -> None:
        runner = FakeStderrRunner(boxes=[(1280, 720, 0, 0)])
        result = scan_keyframe_points(
            source_path=str(__file__) + ".missing",
            points=[0.0],
            ffmpeg_executable="ffmpeg",
            runner=runner,
            work_dir=self.work_dir,
            fast_keyframe_seek=True,
            prefer_pyav=True,
        )
        self.assertEqual(result.boxes, (CropBox(1280, 720, 0, 0),))
        self.assertEqual(runner.count_commands("pgm"), 1)

    def test_prefer_pyav_off_uses_cli_and_cancel_stops(self) -> None:
        runner = FakeStderrRunner()
        scan_keyframe_points(
            source_path="movie.mp4",
            points=[0.0],
            ffmpeg_executable="ffmpeg",
            runner=runner,
            work_dir=self.work_dir,
            fast_keyframe_seek=False,
            prefer_pyav=False,
        )
        self.assertEqual(runner.count_commands("pgm"), 1)
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(InterruptedError):
            scan_keyframe_points(
                source_path="movie.mp4",
                points=[0.0],
                ffmpeg_executable="ffmpeg",
                runner=runner,
                work_dir=self.work_dir,
                fast_keyframe_seek=False,
                cancel_event=cancel,
                prefer_pyav=False,
            )
        self.assertEqual(runner.count_commands("pgm"), 1)

    def test_scan_result_keeps_order(self) -> None:
        result = KeyframeScanResult([CropBox(1, 1, 0, 0), CropBox(2, 2, 0, 0)], [1, 1, 2])
        self.assertEqual(len(result.boxes), 2)
        self.assertEqual(result.black_points, frozenset({1, 2}))
        self.assertEqual(result.darkness, (None, None))

    def test_scan_result_keeps_parallel_darkness(self) -> None:
        edges = EdgeDarkness(1, 2, 3, 4)
        result = KeyframeScanResult([CropBox(1, 1, 0, 0)], [], [edges])
        self.assertEqual(result.darkness, (edges,))


if __name__ == "__main__":
    unittest.main()

