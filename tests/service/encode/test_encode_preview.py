"""표에 보여 줄 예정 FFmpeg 명령 계산(encode_preview) 단위 테스트"""

import os
import tempfile
import unittest
from unittest import mock

from src.core.config import (
    NAME_CONFLICT_NUMBER,
    NAME_CONFLICT_OVERWRITE,
    NAME_CONFLICT_SETTINGS_SUBDIR,
    NAME_CONFLICT_SKIP,
    RESULT_POLICY_SEPARATE,
    ClassifierConfig,
)
from src.core.models import DownscaleReason
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY
from src.core.smart_action import SmartAction
from src.service.encode.encode_preview import (
    PlannedCommand,
    plan_approximation_note,
    planned_command_for_evaluation,
)
from src.service.encode.encode_reasons import NAME_EXISTS
from tests._factories import make_eval, make_video


def _config(**execution: object) -> ClassifierConfig:
    """기본 설정에서 실행 섹션 값만 덮어쓴 설정"""
    config = ClassifierConfig()
    for key, value in execution.items():
        setattr(config.execution, key, value)
    return config


_LAYOUT_SUMMARY = (
    '[{"kind": "video", "codec": "h264", "attached_pic": false}]'
)


class PlannedCommandTests(unittest.TestCase):
    """예정 명령이 실제 계획 빌더 입력에서 나오는지 검증"""

    def test_encode_needed_row_returns_command_without_approximation(self) -> None:
        """스트림 요약이 있는 인코딩 대상 행은 실제 스트림 구성으로 명령을 만듦"""
        ev = make_eval(
            make_video(filename="clip.mp4", stream_summary=_LAYOUT_SUMMARY),
            smart_action=SmartAction.ENCODE_OVER,
        )
        planned = planned_command_for_evaluation(ev, _config())
        self.assertIsNotNone(planned)
        assert planned is not None
        self.assertFalse(planned.approximate)
        self.assertTrue(planned.command_line.startswith("ffmpeg "))
        self.assertIn("-c:v:0", planned.command_line)
        self.assertIn("cq", planned.summary)

    def test_downscaled_row_carries_scale_filter(self) -> None:
        """축소 인코딩 행은 축소 필터와 최대 비트레이트가 명령과 요약에 들어감"""
        ev = make_eval(
            make_video(filename="big.mp4", stream_summary=_LAYOUT_SUMMARY),
            smart_action=SmartAction.ENCODE_DOWNSCALE,
            target_width=1280,
            target_height=720,
            target_tier=RESOLUTION_TIER_BY_KEY["720p"],
            target_optimal_kbps=1500,
            downscale_reason=DownscaleReason.FRAME_ANALYSIS,
        )
        planned = planned_command_for_evaluation(ev, _config())
        self.assertIsNotNone(planned)
        assert planned is not None
        self.assertIn("-filter:v:0", planned.command_line)
        self.assertIn("scale=", planned.summary)
        self.assertIn("maxrate", planned.summary)

    def test_passthrough_row_has_no_planned_command(self) -> None:
        """보존 행은 배치가 인코딩하지 않으므로 예정 명령을 만들지 않음"""
        ev = make_eval(
            make_video(filename="keep.mp4"),
            smart_action=SmartAction.PASS_OPTIMAL,
        )
        self.assertFalse(ev.is_encode_target)
        self.assertIsNone(planned_command_for_evaluation(ev, _config()))

    def test_reanalysis_row_has_no_planned_command(self) -> None:
        """재분석 필요 행도 인코딩 대상이 아니므로 예정 명령을 만들지 않음"""
        ev = make_eval(
            make_video(filename="legacy.mp4"),
            smart_action=SmartAction.LEGACY_FORMAT,
        )
        self.assertFalse(ev.is_encode_target)
        self.assertIsNone(planned_command_for_evaluation(ev, _config()))

    def test_missing_stream_summary_falls_back_to_single_video_stream(self) -> None:
        """스트림 요약이 없으면 주 영상 하나로 근사하고 그 사실을 표시"""
        ev = make_eval(make_video(filename="clip.mp4"), smart_action=SmartAction.ENCODE_OVER)
        planned = planned_command_for_evaluation(ev, _config())
        self.assertIsNotNone(planned)
        assert planned is not None
        self.assertTrue(planned.approximate)
        self.assertIn("-map 0:0", planned.command_line)

    def test_ffmpeg_path_from_settings_is_used(self) -> None:
        """설정의 ffmpeg 경로가 명령 첫 인자로 들어가고 공백이 있으면 인용"""
        ev = make_eval(make_video(filename="clip.mp4"), smart_action=SmartAction.ENCODE_OVER)
        config = _config(ffmpeg_path="C:\\tools dir\\ffmpeg.exe")
        planned = planned_command_for_evaluation(ev, config)
        self.assertIsNotNone(planned)
        assert planned is not None
        self.assertTrue(planned.command_line.startswith('"C:\\tools dir\\ffmpeg.exe"'))

    def test_returns_none_when_plan_cannot_be_built(self) -> None:
        """계획 빌더가 실패하면 None을 돌려줌"""
        ev = make_eval(make_video(filename="clip.mp4"), smart_action=SmartAction.ENCODE_OVER)
        with mock.patch(
            "src.service.encode.encode_preview._plan_for_job",
            side_effect=ValueError("boom"),
        ):
            self.assertIsNone(planned_command_for_evaluation(ev, _config()))

    def test_does_not_spawn_any_process(self) -> None:
        """명령 계산은 FFmpeg, ffprobe를 실행하지 않음"""
        ev = make_eval(make_video(filename="clip.mp4"), smart_action=SmartAction.ENCODE_OVER)
        with mock.patch("subprocess.run") as run, mock.patch("subprocess.Popen") as popen:
            planned = planned_command_for_evaluation(ev, _config())
        self.assertIsNotNone(planned)
        run.assert_not_called()
        popen.assert_not_called()

    def test_approximation_note_is_empty_for_exact_plan(self) -> None:
        """정확한 계획에는 근사 문구를 붙이지 않음"""
        exact = PlannedCommand(command_line="ffmpeg", summary="", approximate=False)
        self.assertEqual(plan_approximation_note(exact), "")


class NameConflictPreviewTests(unittest.TestCase):
    """별도 출력 폴더에 같은 이름 파일이 있을 때 명령을 만들지 않는지 검증"""

    def test_skip_policy_with_existing_output_offers_no_command(self) -> None:
        """건너뜀 정책이면 배치가 건너뛰는 영상의 명령을 만들지 않고 사유만 남김"""
        with tempfile.TemporaryDirectory() as root:
            output_dir = os.path.join(root, "encoded")
            os.makedirs(output_dir)
            open(os.path.join(root, "clip.mp4"), "wb").close()
            open(os.path.join(output_dir, "clip.mp4"), "wb").close()
            ev = make_eval(
                make_video(filename="clip.mp4", stream_summary=_LAYOUT_SUMMARY),
                smart_action=SmartAction.ENCODE_OVER,
            )
            config = _config(
                result_policy=RESULT_POLICY_SEPARATE,
                output_dirname="encoded",
                name_conflict=NAME_CONFLICT_SKIP,
            )
            planned = planned_command_for_evaluation(ev, config, root_dir=root)
        self.assertIsNotNone(planned)
        assert planned is not None
        self.assertEqual(planned.command_line, "")
        self.assertEqual(planned.skip_reason, NAME_EXISTS)
        self.assertEqual(planned.display_text, NAME_EXISTS)

    def test_number_policy_with_free_number_still_offers_command(self) -> None:
        """번호 정책이면 비어 있는 번호 경로로 명령을 만듦"""
        with tempfile.TemporaryDirectory() as root:
            output_dir = os.path.join(root, "encoded")
            os.makedirs(output_dir)
            open(os.path.join(root, "clip.mp4"), "wb").close()
            open(os.path.join(output_dir, "clip.mp4"), "wb").close()
            ev = make_eval(
                make_video(filename="clip.mp4", stream_summary=_LAYOUT_SUMMARY),
                smart_action=SmartAction.ENCODE_OVER,
            )
            config = _config(
                result_policy=RESULT_POLICY_SEPARATE,
                output_dirname="encoded",
                name_conflict=NAME_CONFLICT_NUMBER,
            )
            planned = planned_command_for_evaluation(ev, config, root_dir=root)
        self.assertIsNotNone(planned)
        assert planned is not None
        self.assertEqual(planned.skip_reason, "")
        self.assertIn("clip (1).mp4", planned.command_line)

    def test_overwrite_policy_keeps_the_target_path_instead_of_skipping(self) -> None:
        """별도 폴더 + 덮어쓰기는 같은 이름이 있어도 건너뜀으로 오판하지 않고 대상 경로를 유지함"""
        with tempfile.TemporaryDirectory() as root:
            output_dir = os.path.join(root, "encoded")
            os.makedirs(output_dir)
            open(os.path.join(root, "clip.mp4"), "wb").close()
            open(os.path.join(output_dir, "clip.mp4"), "wb").close()
            ev = make_eval(
                make_video(filename="clip.mp4", stream_summary=_LAYOUT_SUMMARY),
                smart_action=SmartAction.ENCODE_OVER,
            )
            config = _config(
                result_policy=RESULT_POLICY_SEPARATE,
                output_dirname="encoded",
                name_conflict=NAME_CONFLICT_OVERWRITE,
            )
            planned = planned_command_for_evaluation(ev, config, root_dir=root)
        self.assertIsNotNone(planned)
        assert planned is not None
        self.assertEqual(planned.skip_reason, "")
        self.assertIn(os.path.join("encoded", "clip.mp4"), planned.command_line)
        self.assertNotIn("clip (1).mp4", planned.command_line)

    def test_settings_subdir_policy_previews_the_setting_folder(self) -> None:
        """설정별 하위 폴더 정책은 예정 명령도 그 하위 폴더 경로로 만듦"""
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "encoded", "hevc_cq22"))
            open(os.path.join(root, "clip.mp4"), "wb").close()
            ev = make_eval(
                make_video(filename="clip.mp4", stream_summary=_LAYOUT_SUMMARY),
                smart_action=SmartAction.ENCODE_OVER,
            )
            config = _config(
                result_policy=RESULT_POLICY_SEPARATE,
                output_dirname="encoded",
                name_conflict=NAME_CONFLICT_SETTINGS_SUBDIR,
            )
            planned = planned_command_for_evaluation(ev, config, root_dir=root)
        self.assertIsNotNone(planned)
        assert planned is not None
        self.assertEqual(planned.skip_reason, "")
        self.assertIn(os.path.join("encoded", "hevc_cq22", "clip.mp4"), planned.command_line)


if __name__ == "__main__":
    unittest.main(verbosity=2)
