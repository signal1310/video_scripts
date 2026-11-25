"""인코딩 작업 입력(encode_job)의 설정 사본 왕복과 작업 직렬화 단위 테스트"""

import json
import os
import tempfile
import unittest

from src.core.config import ClassifierConfig
from src.core.encode.encode_gate import GatePurpose
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN
from src.core.models import ActiveFrame
from src.core.resolution_tiers import RESOLUTION_TIER_BY_KEY, build_scale_expression
from src.core.smart_action import SmartAction
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_job import (
    EncodeJob,
    EncodeSettings,
    create_batch,
    job_from_json,
    job_to_json,
    jobs_from_evaluations,
)
from tests._factories import make_eval, make_video


class TestEncodeSettingsSnapshot(unittest.TestCase):
    """EncodeSettings의 from_config, to_json, from_json 왕복과 기본값 복원을 검증"""

    def test_from_config_and_json_round_trip_preserves_tag_settings(self) -> None:
        config = ClassifierConfig()
        config.execution.encode_tag_mode = ENCODE_TAG_MODE_HIDDEN
        config.execution.encode_tag_name = "custom_tag"
        settings = EncodeSettings.from_config(config)
        restored = EncodeSettings.from_json(settings.to_json())
        self.assertEqual(restored, settings)
        self.assertEqual(restored.encode_tag_mode, ENCODE_TAG_MODE_HIDDEN)
        self.assertEqual(restored.encode_tag_name, "custom_tag")

    def test_from_json_falls_back_when_tag_keys_are_absent(self) -> None:
        """이전 배치 JSON에 태그 키가 없어도 기본값으로 복원됨을 검증"""
        raw = EncodeSettings.from_config(ClassifierConfig()).to_json()
        payload = json.loads(raw)
        for key in ("encode_tag_mode", "encode_tag_name", "protect_cq_delta_max",
                    "sample_saving_discard_factor", "early_stop_margin"):
            del payload[key]
        restored = EncodeSettings.from_json(json.dumps(payload))
        self.assertEqual(restored.encode_tag_mode, "visible")
        self.assertEqual(restored.encode_tag_name, "re_encoded")
        self.assertEqual(restored.protect_cq_delta_max, 6.0)
        self.assertEqual(restored.sample_saving_discard_factor, 1.05)
        self.assertEqual(restored.early_stop_margin, 1.20)

    def test_from_config_copies_coefficient_settings(self) -> None:
        """품질값 자동 조절 한도와 중단 기준이 배치 설정 사본에 함께 담김을 검증"""
        config = ClassifierConfig()
        config.execution.protect_cq_delta_max = 4.0
        config.execution.sample_saving_discard_factor = 1.10
        config.execution.early_stop_margin = 1.30
        settings = EncodeSettings.from_config(config)
        self.assertEqual(settings.protect_cq_delta_max, 4.0)
        self.assertEqual(settings.sample_saving_discard_factor, 1.10)
        self.assertEqual(settings.early_stop_margin, 1.30)
        self.assertEqual(EncodeSettings.from_json(settings.to_json()), settings)


class TestEncodeJobSerialization(unittest.TestCase):
    """EncodeJob 직렬화와 배치 생성을 검증"""

    def test_job_json_round_trip_and_create_batch(self) -> None:
        job = EncodeJob(
            filename="clip.mp4",
            root_dir="/root",
            source_path="/root/clip.mp4",
            source_size=1234,
            source_mtime_ns=5678,
            source_duration_sec=12.5,
            source_ext="mp4",
            fps=30.0,
            quality=22.0,
            purpose=GatePurpose.SAVING,
            output_size=(1280, 720),
            scale_filter="scale=1280:720",
            maxrate_kbps=4000,
        )
        self.assertEqual(job_from_json(job_to_json(job)), job)
        with tempfile.TemporaryDirectory() as tmp:
            history = EncodeHistory(f"{tmp}/h.sqlite3", sleep=lambda _s: None)
            try:
                batch_id = create_batch(history, EncodeSettings.from_config(ClassifierConfig()), "/root", [job])
                self.assertEqual(len(history.list_items(batch_id)), 1)
            finally:
                history.close()

    def test_crop_filter_round_trip_and_legacy_json_reads_none(self) -> None:
        """잘라내기 필터가 왕복하고, 이전 기록 JSON에 없으면 None으로 읽히는지 검증"""
        job = EncodeJob(
            filename="clip.mp4",
            root_dir="/root",
            source_path="/root/clip.mp4",
            source_size=1234,
            source_mtime_ns=5678,
            source_duration_sec=12.5,
            source_ext="mp4",
            fps=30.0,
            quality=22.0,
            purpose=GatePurpose.SAVING,
            output_size=None,
            scale_filter=None,
            maxrate_kbps=None,
            crop_filter="crop=1600:1000:260:40",
        )
        self.assertEqual(job_from_json(job_to_json(job)).crop_filter, "crop=1600:1000:260:40")
        legacy = json.loads(job_to_json(job))
        del legacy["crop_filter"]
        self.assertIsNone(job_from_json(json.dumps(legacy)).crop_filter)

    def test_jobs_carry_crop_filter_with_and_without_scale(self) -> None:
        """잘라내기 확정 영상은 축소 여부와 관계없이 crop 필터를 넘기고 축소 크기를 유지하는지 검증"""
        active = ActiveFrame(1600, 1000, 260, 40)
        tier = RESOLUTION_TIER_BY_KEY["1080p"]
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "clip.mp4"), "wb") as handle:
                handle.write(b"video")
            plain = make_eval(make_video(filename="clip.mp4"))
            crop_only = make_eval(make_video(filename="clip.mp4"), active_frame=active)
            downscaled = make_eval(
                make_video(filename="clip.mp4"),
                smart_action=SmartAction.ENCODE_DOWNSCALE,
                target_tier=tier,
                target_width=1600,
                target_height=1000,
                active_frame=active,
            )
            jobs = jobs_from_evaluations([plain, crop_only, downscaled], tmp)
        self.assertIsNone(jobs[0].crop_filter)
        self.assertEqual(jobs[1].crop_filter, "crop=1600:1000:260:40")
        self.assertIsNone(jobs[1].output_size)
        self.assertIsNone(jobs[1].scale_filter)
        self.assertEqual(jobs[2].crop_filter, "crop=1600:1000:260:40")
        self.assertEqual(jobs[2].output_size, (1600, 1000))
        self.assertEqual(jobs[2].scale_filter, build_scale_expression(tier))

    def test_scale_algorithm_reaches_the_encode_filter(self) -> None:
        """설정한 보간 방식이 축소 대상의 scale 필터 flags에 들어가는지 검증"""
        tier = RESOLUTION_TIER_BY_KEY["1080p"]
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "clip.mp4"), "wb") as handle:
                handle.write(b"video")
            downscaled = make_eval(
                make_video(filename="clip.mp4"),
                smart_action=SmartAction.ENCODE_DOWNSCALE,
                target_tier=tier,
                target_width=1600,
                target_height=1000,
            )
            (job,) = jobs_from_evaluations(
                [downscaled], tmp, scale_algorithm="lanczos"
            )
        assert job.scale_filter is not None
        self.assertTrue(job.scale_filter.endswith(":flags=lanczos"))
        self.assertEqual(job.scale_filter, build_scale_expression(tier, "lanczos"))

    def test_corrected_target_and_maxrate_follow_encoder_factor(self) -> None:
        """계획 입력이 보정 목표 비트레이트와 규격 상한 이내의 여유 있는 최대 비트레이트를 OS 실측과 같은 정의로 담는지 검증"""
        tier = RESOLUTION_TIER_BY_KEY["720p"]
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "clip.mp4"), "wb") as handle:
                handle.write(b"video")
            downscaled = make_eval(
                make_video(filename="clip.mp4"),
                smart_action=SmartAction.ENCODE_DOWNSCALE,
                target_tier=tier,
                target_width=1280,
                target_height=720,
                target_optimal_kbps=1500,
            )
            (job,) = jobs_from_evaluations([downscaled], tmp, encoder="hevc_nvenc")
            (plain,) = jobs_from_evaluations([downscaled], tmp)
        self.assertEqual(job.corrected_target_kbps, 3900.0)
        self.assertEqual(job.maxrate_kbps, tier.max_bitrate_kbps)
        # - 인코더를 주지 않으면 계수 1.0이므로 보정값이 모델 목표 그대로이고 여유 배수만 적용
        self.assertEqual(plain.corrected_target_kbps, 1500.0)
        self.assertEqual(plain.maxrate_kbps, 2300)

        restored = job_from_json(job_to_json(job))
        self.assertEqual(restored.corrected_target_kbps, 3900.0)
        legacy = json.loads(job_to_json(job))
        del legacy["corrected_target_kbps"]
        self.assertIsNone(job_from_json(json.dumps(legacy)).corrected_target_kbps)

    def test_expected_sizes_round_trip_and_missing_keys_read_as_none(self) -> None:
        """판정 예상 용량과 예상 절약이 왕복하고, 이전 기록 JSON에 없으면 None으로 읽히는지 검증"""
        job = EncodeJob(
            filename="clip.mp4",
            root_dir="/root",
            source_path="/root/clip.mp4",
            source_size=1234,
            source_mtime_ns=5678,
            source_duration_sec=12.5,
            source_ext="mp4",
            fps=30.0,
            quality=22.0,
            purpose=GatePurpose.SAVING,
            output_size=None,
            scale_filter=None,
            maxrate_kbps=None,
            expected_after_mb=398.5,
            expected_delta_mb=-12.25,
        )
        restored = job_from_json(job_to_json(job))
        self.assertEqual(restored.expected_after_mb, 398.5)
        self.assertEqual(restored.expected_delta_mb, -12.25)
        legacy = json.loads(job_to_json(job))
        del legacy["expected_after_mb"]
        del legacy["expected_delta_mb"]
        read_back = job_from_json(json.dumps(legacy))
        self.assertIsNone(read_back.expected_after_mb)
        self.assertIsNone(read_back.expected_delta_mb)

    def test_baseline_quality_round_trip_and_legacy_json_reads_none(self) -> None:
        """기준 품질값이 작업 기록을 왕복하고, 이전 기록 JSON에 없으면 None으로 읽히는지 검증"""
        job = EncodeJob(
            filename="clip.mp4",
            root_dir="/root",
            source_path="/root/clip.mp4",
            source_size=1234,
            source_mtime_ns=5678,
            source_duration_sec=12.5,
            source_ext="mp4",
            fps=30.0,
            quality=28.0,
            purpose=GatePurpose.SAVING,
            output_size=None,
            scale_filter=None,
            maxrate_kbps=None,
            baseline_quality=22.0,
        )
        restored = job_from_json(job_to_json(job))
        self.assertEqual(restored.baseline_quality, 22.0)
        self.assertEqual(restored.quality, 28.0)
        legacy = json.loads(job_to_json(job))
        del legacy["baseline_quality"]
        self.assertIsNone(job_from_json(json.dumps(legacy)).baseline_quality)

    def test_jobs_carry_configured_baseline_quality(self) -> None:
        """설정의 화질 기준값을 넘기면 작업마다 담기고, 넘기지 않으면 None인지 검증"""
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "clip.mp4"), "wb") as handle:
                handle.write(b"video")
            evaluation = make_eval(make_video(filename="clip.mp4"))
            (configured,) = jobs_from_evaluations([evaluation], tmp, baseline_quality=22.0)
            (absent,) = jobs_from_evaluations([evaluation], tmp)
        self.assertEqual(configured.baseline_quality, 22.0)
        self.assertEqual(configured.quality, 22.0)
        self.assertIsNone(absent.baseline_quality)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPurposeByKeyframe(unittest.TestCase):
    """계획 입력의 게이트 목적: 키프레임 간격이 초과된 영상은 판정 종류와 무관하게 KF 교정, 정상이면 판정 종류대로"""

    def _purpose(self, action: SmartAction, keyframe_exceeded: bool) -> GatePurpose:
        with tempfile.TemporaryDirectory() as tmp:
            with open(os.path.join(tmp, "clip.mp4"), "wb") as handle:
                handle.write(b"video")
            evaluation = make_eval(
                make_video(filename="clip.mp4"),
                smart_action=action,
                is_keyframe_exceeded=keyframe_exceeded,
            )
            (job,) = jobs_from_evaluations([evaluation], tmp)
        return job.purpose

    def test_size_judged_actions_become_keyframe_fix_when_the_keyframe_is_exceeded(self) -> None:
        """과화질, 축소, 코덱 전환은 키프레임 초과면 크기로 폐기하지 않도록 KF 교정 목적을 받음"""
        for action in (SmartAction.ENCODE_OVER, SmartAction.ENCODE_DOWNSCALE, SmartAction.ENCODE_CODEC_UPGRADE):
            with self.subTest(action=action):
                self.assertIs(self._purpose(action, True), GatePurpose.KEYFRAME_FIX)

    def test_normal_keyframe_keeps_the_saving_purpose_of_size_judged_actions(self) -> None:
        """키프레임이 정상이면 과화질, 축소, 코덱 전환은 절약 목적을 유지"""
        for action in (SmartAction.ENCODE_OVER, SmartAction.ENCODE_DOWNSCALE, SmartAction.ENCODE_CODEC_UPGRADE):
            with self.subTest(action=action):
                self.assertIs(self._purpose(action, False), GatePurpose.SAVING)

    def test_compatibility_target_is_keyframe_fix_only_when_exceeded(self) -> None:
        """호환 목적은 키프레임 초과일 때만 KF 교정이 되고, 정상이면 그대로 호환 목적"""
        self.assertIs(self._purpose(SmartAction.ENCODE_COMPAT, True), GatePurpose.KEYFRAME_FIX)
        self.assertIs(self._purpose(SmartAction.ENCODE_COMPAT, False), GatePurpose.COMPAT)
