"""표 셀별 영상 맞춤 툴팁 문구가 그 영상의 값과 판정 사유만 담는지 검증"""

import unittest

from src.core.messages import LABEL_UNMEASURED_DETAIL
from src.core.smart_action import SmartAction
from src.gui.table.table_cell_tooltips import (
    action_cell_tip,
    after_size_cell_tip,
    bitrate_cell_tip,
    codec_cell_tip,
    duration_cell_tip,
    encode_state_cell_tip,
    est_encode_time_cell_tip,
    ffmpeg_args_line,
    fps_cell_tip,
    height_cell_tip,
    keyframe_cell_tip,
    optimal_bitrate_cell_tip,
    out_bitrate_cell_tip,
    quality_value_cell_tip,
    ratio_cell_tip,
    reason_cell_tip,
    rotate_cell_tip,
    saved_per_min_cell_tip,
    saved_size_cell_tip,
    size_cell_tip,
    target_ratio_cell_tip,
    width_cell_tip,
    zone_cell_tip,
)
from src.service.encode.encode_preview import PlannedCommand
from tests._factories import make_eval, make_video


class CellTooltipTests(unittest.TestCase):
    """셀별 툴팁이 영상 값과 판정 사유를 한 줄로 보여 주는지 검증"""

    def test_size_and_bitrate_cells_show_this_video_values(self) -> None:
        """해상도, 회전, 코덱, FPS, 길이, 비트레이트 셀이 영상 값을 쓰는지 검증"""
        ev = make_eval(make_video(
            width=1280, height=720, rotate_type=90, rotate_source="Display Matrix -90°",
            codec="h264", fps=29.97, duration=125.0, vid_kbps=4321, vid_size_MB=321.5,
        ))
        self.assertEqual(width_cell_tip(ev), "회전 보정 반영 너비 1280 px")
        self.assertEqual(height_cell_tip(ev), "회전 보정 반영 높이 720 px")
        self.assertEqual(rotate_cell_tip(ev), "회전 90° (원본: Display Matrix -90°)")
        self.assertEqual(codec_cell_tip(ev), "현재 코덱 h264")
        self.assertEqual(fps_cell_tip(ev), "현재 29.970 fps")
        self.assertEqual(duration_cell_tip(ev), "재생 시간 02:05")
        self.assertEqual(bitrate_cell_tip(ev), "현재 비트레이트 4,321 kbps")
        self.assertEqual(size_cell_tip(ev), "현재 용량 321.50 MB")

    def test_rotation_without_source_omits_the_source_note(self) -> None:
        """회전 출처가 없으면 출처 괄호를 붙이지 않는지 검증"""
        ev = make_eval(make_video(rotate_type=0, rotate_source=None))
        self.assertEqual(rotate_cell_tip(ev), "회전 0°")

    def test_ratio_and_bitrate_cells_show_this_video_ratios(self) -> None:
        """BR 비율, 출력 BR 비율, 최적 BR, 출력 BR 셀이 이 영상의 수치를 쓰는지 검증"""
        ev = make_eval(make_video(), optimal_kbps=2000, bitrate_ratio=1.5, target_bitrate_ratio=1.25,
                       target_optimal_kbps=1800)
        self.assertEqual(optimal_bitrate_cell_tip(ev), "해상도, FPS, 코덱 기준 최적 비트레이트 2,000 kbps")
        self.assertEqual(ratio_cell_tip(ev), "현재 비트레이트는 최적 비트레이트의 1.500배")
        self.assertEqual(target_ratio_cell_tip(ev), "출력 코덱 기준 현재 비트레이트는 최적 비트레이트의 1.250배")
        self.assertEqual(out_bitrate_cell_tip(ev), "인코딩 후 예상 비트레이트 1,800 kbps")
        self.assertIn(ev.quality_zone.value, zone_cell_tip(ev))

    def test_keyframe_cell_marks_unmeasured_and_exceeded_intervals(self) -> None:
        """키프레임 간격 셀이 미측정과 허용 상한 초과를 구분하는지 검증"""
        unmeasured = make_eval(make_video(keyframe_interval=None))
        self.assertEqual(keyframe_cell_tip(unmeasured), f"키프레임 간격 {LABEL_UNMEASURED_DETAIL}")
        exceeded = make_eval(make_video(keyframe_interval=4.5), is_keyframe_exceeded=True)
        self.assertEqual(keyframe_cell_tip(exceeded), "키프레임 간격 4.500초 (허용 상한 초과)")
        normal = make_eval(make_video(keyframe_interval=2.0), is_keyframe_exceeded=False)
        self.assertEqual(keyframe_cell_tip(normal), "키프레임 간격 2.000초")

    def test_estimate_cells_add_the_reference_note_only_for_kept_rows(self) -> None:
        """예상 용량, 절약, 분당 절약 셀이 원본 보존 행에만 참고값 안내를 붙이는지 검증"""
        target = make_eval(make_video(vid_size_MB=100.0, duration=60.0))
        self.assertNotIn("참고값", after_size_cell_tip(target))
        kept = make_eval(make_video(vid_size_MB=100.0, duration=60.0), smart_action=SmartAction.PASS_OPTIMAL)
        self.assertIn("참고값", after_size_cell_tip(kept))
        self.assertIn("참고값", saved_size_cell_tip(kept))
        self.assertIn("참고값", saved_per_min_cell_tip(kept))
        self.assertIn("예상 인코딩 시간 약", est_encode_time_cell_tip(kept))

    def test_action_and_reason_cells_show_only_this_video_case(self) -> None:
        """권장 처리와 판정 사유 셀이 이 영상의 사유 한 건만 담는지 검증"""
        ev = make_eval(make_video(), smart_action=SmartAction.PASS_OPTIMAL)
        self.assertEqual(action_cell_tip(ev), f"{ev.action_label}, {ev.option_label}")
        self.assertEqual(
            reason_cell_tip(ev),
            f"{SmartAction.PASS_OPTIMAL.reason_label}: {SmartAction.PASS_OPTIMAL.reason_description}",
        )

    def test_quality_value_cell_tip_separates_preserved_and_reanalysis_rows(self) -> None:
        """품질값 셀 툴팁이 원본 보존 행과 재분석 필요 행에 서로 다른 사유를 알리는지 검증"""
        preserved = make_eval(
            make_video(keyframe_interval=1.0), smart_action=SmartAction.PASS_OPTIMAL
        )
        self.assertEqual(
            quality_value_cell_tip(preserved),
            "원본을 보존하는 영상이라 계획한 품질값이 없습니다.",
        )
        legacy = make_eval(
            make_video(keyframe_interval=1.0), smart_action=SmartAction.LEGACY_FORMAT
        )
        legacy_tip = quality_value_cell_tip(legacy)
        self.assertNotEqual(legacy_tip, quality_value_cell_tip(preserved))
        self.assertIn("재분석 필요", legacy_tip)
        self.assertIn("품질값이 없습니다", legacy_tip)
        self.assertNotIn("원본을 보존", legacy_tip)
        self.assertTrue(legacy_tip.endswith("정해집니다."))

    def test_encode_state_default_tip_names_non_target_rows(self) -> None:
        """배치 밖 행의 기본 상태 툴팁이 대상 아님을 알리는지 검증"""
        self.assertEqual(encode_state_cell_tip(make_eval(make_video())), "인코딩 배치 대상이 아닙니다.")


class FfmpegArgsLineTests(unittest.TestCase):
    """예정 FFmpeg 인자 한 줄 문구를 검증"""

    def test_exact_plan_line_has_no_approximation_note(self) -> None:
        """정확한 계획은 요약만 담음"""
        planned = PlannedCommand(command_line="ffmpeg -i clip.mp4", summary="hevc_nvenc cq 22.0", approximate=False)
        self.assertEqual(ffmpeg_args_line(planned), "FFmpeg 인자: hevc_nvenc cq 22.0")

    def test_approximate_plan_appends_the_note(self) -> None:
        """보존 행처럼 가정한 계획은 근사 안내를 붙임"""
        planned = PlannedCommand(command_line="ffmpeg -i clip.mp4", summary="hevc_nvenc cq 22.0", approximate=True)
        line = ffmpeg_args_line(planned)
        self.assertTrue(line.startswith("FFmpeg 인자: hevc_nvenc cq 22.0"))
        self.assertIn("추정", line)

    def test_missing_plan_returns_empty_text(self) -> None:
        """명령을 만들 수 없으면 빈 문자열을 돌려 툴팁이 붙지 않게 함"""
        self.assertEqual(ffmpeg_args_line(None), "")
