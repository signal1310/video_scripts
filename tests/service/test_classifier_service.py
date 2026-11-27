"""분류 서비스(VideoClassifierService)의 인코딩 근거 3종 판정과 5->6 캐시 업그레이드 통합 단위 테스트"""

import contextlib
import os
import sqlite3
import struct
import tempfile
import unittest
from unittest import mock

from src.core.analysis.frame_analysis import (
    CROP_SCAN_VERSION,
    FRAME_ANALYSIS_VERSION,
    TierScore,
)
from src.core.analysis.probe_version_rules import KEYFRAME_MEASURE_VERSION
from src.core.config import (
    RESULT_POLICY_ARCHIVE,
    RESULT_POLICY_REPLACE,
    RESULT_POLICY_SEPARATE,
    ClassifierConfig,
)
from src.core.encode.encode_gate import GatePurpose
from src.core.encode_fingerprint import compute_encode_fingerprint
from src.core.encode_tag import ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE
from src.core.models import ActiveFrame, QualityZone
from src.core.probe_parser import compress_probe_response
from src.core.smart_action import DownscaleReason, SmartAction
from src.core.workspace_paths import normalize_root_key
from src.service import analysis_probe as probe_mod
from src.service.analysis_probe import load_stored_rows
from src.service.analysis_store import (
    ANALYSIS_TABLE_NAME,
    AnalysisStore,
    StoredEntry,
    compute_fingerprint,
)
from src.service.cache import CacheRegistry, TableCache
from src.service.classifier_service import VideoClassifierService, _resolve_frame_cache_for
from src.service.encode.encode_job import jobs_from_evaluations
from src.service.encode.encode_output_history import EncodeOutputHistory, record_done_output
from src.service.frame_analysis_record import FrameAnalysisRecord
from src.service.frame_analysis_store import FrameAnalysisStore
from tests._factories import make_video


def _box(box_type: bytes, payload: bytes) -> bytes:
    return struct.pack(">I4s", 8 + len(payload), box_type) + payload


def _mp4_bytes(mdat_payload: bytes) -> bytes:
    return _box(b"ftyp", b"isom") + _box(b"mdat", mdat_payload) + _box(b"moov", b"")


class TestClassifierServiceEncodedEvidence(unittest.TestCase):
    """판정 근거 3종(상세 항목, 현재+이전 태그 이름, 이력 지문)과 직접 지정 예외를 검증"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "work")
        os.makedirs(self.root)
        self.db_path = os.path.join(self._tmp.name, "video_cache.sqlite3")
        self.store = AnalysisStore(self.db_path)
        self.registry = CacheRegistry()
        self.config = ClassifierConfig()

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _service(self, *, manual_targets: dict[str, str] | None = None) -> VideoClassifierService:
        svc = VideoClassifierService(
            self.root,
            self.config,
            registry=self.registry,
            store=self.store,
        )
        if manual_targets is not None:
            svc.manual_targets = dict(manual_targets)
        return svc

    def test_three_encoded_evidences_and_manual_override(self) -> None:
        """상세 항목, 현재+이전 태그 이름, hidden/off 이력 지문 판정과 직접 지정 예외를 검증"""
        # 1. 이력 DB에 이전 태그 이름과 off 모드 완료 파일 지문 기록
        off_file = os.path.join(self.root, "history_only.mp4")
        data = _mp4_bytes(b"\x11" * 128)
        with open(off_file, "wb") as handle:
            handle.write(data)
        fp = compute_encode_fingerprint(off_file)

        out_hist = EncodeOutputHistory(self.db_path, sleep=lambda _s: None)
        try:
            out_hist.record_tag_name("Old_Tag")
            record_done_output(
                out_hist,
                output_path=off_file,
                encoder="hevc_nvenc",
                quality=22.0,
                tag_mode=ENCODE_TAG_MODE_OFF,
                tag_name="re_encoded",
            )
        finally:
            out_hist.close()

        # 2. 4개 과화질 후보를 캐시에 직접 적재
        v_detail = make_video(filename="detail.mp4", vid_kbps=20_000, has_encode_tag=True)
        v_old_kw = make_video(filename="old_kw.mp4", vid_kbps=20_000, encode_keywords="가족;old_tag")
        v_history = make_video(filename="history_only.mp4", vid_kbps=20_000, fingerprint_lookup_size=fp.size)
        v_fresh = make_video(filename="fresh.mp4", vid_kbps=20_000)

        svc = self._service()
        from src.service.cache import TableCache

        self.registry.put(self.root, TableCache(self.root, [v_detail, v_old_kw, v_history, v_fresh], include_keyframe=False))
        report = svc.evaluate_all()
        by_name = {ev.video.filename: ev.smart_action for ev in report.evaluations}
        self.assertIs(by_name["detail.mp4"], SmartAction.PASS_ALREADY_ENCODED)
        self.assertIs(by_name["old_kw.mp4"], SmartAction.PASS_ALREADY_ENCODED)
        self.assertIs(by_name["history_only.mp4"], SmartAction.PASS_ALREADY_ENCODED)
        self.assertIs(by_name["fresh.mp4"], SmartAction.ENCODE_OVER)

        # 3. 직접 지정 축소가 있으면 이미 인코딩 영상도 대상에 포함
        svc_manual = self._service(manual_targets={"history_only.mp4": "1080p"})
        self.assertIs(svc_manual.registry, self.registry)
        manual_report = svc_manual.evaluate_cached()
        assert manual_report is not None
        self.assertEqual(manual_report.total_count, 4)

        # cached_report_job() 생성 시점에는 DB/디스크 조회를 하지 않고 job() 실행 시점에 수행함을 검증
        with mock.patch("src.service.classifier_service.EncodeOutputHistory") as hist_cls:
            job = svc.cached_report_job()
            hist_cls.assert_not_called()
        assert job is not None
        job_actions = {ev.video.filename: ev.smart_action for ev in job().evaluations}
        self.assertIs(job_actions["history_only.mp4"], SmartAction.PASS_ALREADY_ENCODED)

        svc.invalidate_cache()
        self.assertIsNone(svc.cache)

    def test_v5_rows_upgrade_to_v6_with_zero_ffprobe_calls_and_fill_stream_summary(self) -> None:
        """5 -> 6 업그레이드 시 ffprobe 호출 0회, 저장 응답으로 stream_summary 보완, 키프레임 버전 유지를 검증"""
        clip = os.path.join(self.root, "v5_clip.mp4")
        with open(clip, "wb") as handle:
            handle.write(_mp4_bytes(b"\x22" * 64))
        stat = os.stat(clip)
        raw_probe = compress_probe_response(
            {"streams": [{"codec_type": "video", "codec_name": "h264", "disposition": {"attached_pic": 0}}]}
        )
        v5_props = make_video(filename="v5_clip.mp4", keyframe_interval=2.0)
        entry = StoredEntry(
            filename="v5_clip.mp4",
            filesize_bytes=stat.st_size,
            mtime_ns=stat.st_mtime_ns,
            fingerprint="fp",
            props=v5_props,
            probe_version=6,
            keyframe_version=KEYFRAME_MEASURE_VERSION,
            probe_raw=raw_probe,
        )
        self.store.apply([], [entry])
        # DB의 probe_version을 5로 낮추고 stream_summary를 비워 5버전 행 시뮬레이션
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute(f"UPDATE {ANALYSIS_TABLE_NAME} SET probe_version = 5, stream_summary = ''")
            conn.commit()

        stored = load_stored_rows(self.store, self.root)
        assert stored is not None
        self.assertEqual(len(stored.rows), 1)
        self.assertIn("h264", stored.rows[0].stream_summary)

        svc = self._service()
        with mock.patch("src.core.analysis.prober.run_ffprobe", side_effect=AssertionError("ffprobe 호출 금지")):
            cache = svc.prepare_cache(force_keyframe=True)
        self.assertEqual(len(cache.data), 1)
        self.assertIn("h264", cache.data[0].stream_summary)
        self.assertEqual(cache.data[0].keyframe_interval, 2.0)

        # 기록된 행의 probe_version이 6, keyframe_version이 유지되었는지 확인
        fetched = self.store.fetch_by_names(["v5_clip.mp4"])
        self.assertEqual(fetched[0].probe_version, 6)
        self.assertEqual(fetched[0].keyframe_version, KEYFRAME_MEASURE_VERSION)
        self.assertEqual(ENCODE_TAG_MODE_VISIBLE, "visible")

    def test_cached_active_frame_changes_optimal_bitrate_and_zone(self) -> None:
        """화질 분석 캐시의 활성 화면이 판정 B_opt와 화질 등급에 반영되는지 검증"""
        clip = os.path.join(self.root, "video5.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"v" * 2048)
        stat = os.stat(clip)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.save(
                FrameAnalysisRecord(
                    filename="video5.mp4",
                    filesize_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    fingerprint=compute_fingerprint(clip, stat.st_size),
                    active_width=1216,
                    active_height=2160,
                    active_x=1312,
                    active_y=0,
                    has_black_bars=True,
                    metric_used="ssim",
                    vmaf_model="",
                    tier_scores=(),
                    candidate_tier=None,
                    valid_segments=1,
                    total_segments=1,
                    unmeasurable=False,
                    is_vr_protected=False,
                    measured_at="2026-01-01T00:00:00Z",
                    analysis_version=FRAME_ANALYSIS_VERSION,
                )
            )
        finally:
            frame_store.close()

        vid = make_video(
            filename="video5.mp4", width=3840, height=2160, vid_kbps=3000, vid_size_MB=23.0
        )
        svc = self._service()
        self.registry.put(self.root, TableCache(self.root, [vid], include_keyframe=False))
        without = svc.evaluate_cached()
        assert without is not None
        with_active = svc.evaluate_all()
        self.assertLess(with_active.evaluations[0].optimal_kbps, without.evaluations[0].optimal_kbps)
        self.assertIs(without.evaluations[0].quality_zone, QualityZone.LOW)
        self.assertIs(with_active.evaluations[0].quality_zone, QualityZone.OPTIMAL)

        job = svc.cached_report_job()
        assert job is not None
        from_job = job()
        self.assertEqual(
            from_job.evaluations[0].optimal_kbps, with_active.evaluations[0].optimal_kbps
        )
        self.assertEqual(
            with_active.evaluations[0].active_frame, ActiveFrame(1216, 2160, 1312, 0)
        )

    def test_crop_only_cache_informs_judgment_after_a_mode_change(self) -> None:
        """화질 분석 방식을 바꿔도 검은 여백 감지 전용 행을 판정의 활성 화면으로 쓰는지 검증"""
        clip = os.path.join(self.root, "detect.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"v" * 2048)
        stat = os.stat(clip)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.save(
                FrameAnalysisRecord(
                    filename="detect.mp4",
                    filesize_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    fingerprint=compute_fingerprint(clip, stat.st_size),
                    active_width=1216,
                    active_height=2160,
                    active_x=1312,
                    active_y=0,
                    has_black_bars=True,
                    metric_used="",
                    vmaf_model="",
                    tier_scores=(),
                    candidate_tier=None,
                    valid_segments=9,
                    total_segments=9,
                    unmeasurable=False,
                    is_vr_protected=False,
                    measured_at="2026-01-01T00:00:00Z",
                    analysis_version=CROP_SCAN_VERSION,
                )
            )
        finally:
            frame_store.close()
        vid = make_video(
            filename="detect.mp4", width=3840, height=2160, vid_kbps=3000, vid_size_MB=23.0
        )
        self.registry.put(self.root, TableCache(self.root, [vid], include_keyframe=False))
        self.config.encode.frame_analysis_mode = "precise"
        report = self._service().evaluate_all()
        self.assertEqual(
            report.evaluations[0].active_frame, ActiveFrame(1216, 2160, 1312, 0)
        )

    def test_crop_setting_and_exclusion_keep_full_frame_judgment(self) -> None:
        """잘라내기 설정을 끄거나 영상별로 제외하면 전체 프레임 기준으로 다시 판정하는지 검증"""
        clip = os.path.join(self.root, "crop.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"v" * 2048)
        stat = os.stat(clip)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.save(
                FrameAnalysisRecord(
                    filename="crop.mp4",
                    filesize_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    fingerprint=compute_fingerprint(clip, stat.st_size),
                    active_width=1600,
                    active_height=1000,
                    active_x=260,
                    active_y=40,
                    has_black_bars=True,
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(),
                    candidate_tier=None,
                    valid_segments=1,
                    total_segments=1,
                    unmeasurable=False,
                    is_vr_protected=False,
                    measured_at="2026-01-01T00:00:00Z",
                    analysis_version=FRAME_ANALYSIS_VERSION,
                )
            )
        finally:
            frame_store.close()

        vid = make_video(
            filename="crop.mp4", width=1920, height=1080, vid_kbps=3000, vid_size_MB=23.0
        )
        self.registry.put(self.root, TableCache(self.root, [vid], include_keyframe=False))
        svc = self._service()
        cropped = svc.evaluate_all()
        self.assertEqual(
            cropped.evaluations[0].active_frame, ActiveFrame(1600, 1000, 260, 40)
        )

        self.config.encode.crop_black_bars = False
        disabled = svc.evaluate_all()
        self.assertIsNone(disabled.evaluations[0].active_frame)
        self.assertGreater(
            disabled.evaluations[0].optimal_kbps, cropped.evaluations[0].optimal_kbps
        )

        self.config.encode.crop_black_bars = True
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.set_crop_exclusion(clip, True)
        finally:
            frame_store.close()
        excluded = svc.evaluate_all()
        self.assertIsNone(excluded.evaluations[0].active_frame)
        job = svc.cached_report_job()
        assert job is not None
        self.assertIsNone(job().evaluations[0].active_frame)

    def test_shared_content_row_keeps_per_path_vr_protection(self) -> None:
        """내용이 같아 한 캐시 행을 공유하는 두 사본도 경로별 VR 보호를 각자 따르는지 검증

        - 저장된 VR 플래그는 대표 파일명 기준이라, 다른 사본에 그대로 적용하면 VR 아닌 사본이 전체 화면으로
          판정되거나 VR 사본이 잘림
        """
        data = b"v" * 2048
        protected = os.path.join(self.root, "protected.mp4")
        plain = os.path.join(self.root, "plain.mp4")
        for path in (protected, plain):
            with open(path, "wb") as handle:
                handle.write(data)
        stat = os.stat(plain)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.set_vr_override(source_path=protected, filename="protected.mp4", manual_vr=True)
            frame_store.save(
                FrameAnalysisRecord(
                    filename="protected.mp4",
                    filesize_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    fingerprint=compute_fingerprint(plain, stat.st_size),
                    active_width=1600,
                    active_height=1000,
                    active_x=260,
                    active_y=40,
                    has_black_bars=True,
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(),
                    candidate_tier=None,
                    valid_segments=1,
                    total_segments=1,
                    unmeasurable=False,
                    is_vr_protected=True,
                    measured_at="2026-01-01T00:00:00Z",
                    analysis_version=FRAME_ANALYSIS_VERSION,
                )
            )
        finally:
            frame_store.close()
        videos = [
            make_video(filename="protected.mp4", vid_kbps=3000, vid_size_MB=23.0),
            make_video(filename="plain.mp4", vid_kbps=3000, vid_size_MB=23.0),
        ]
        self.registry.put(self.root, TableCache(self.root, videos, include_keyframe=False))
        report = self._service().evaluate_all()
        by_name = {ev.video.filename: ev for ev in report.evaluations}
        self.assertIsNone(by_name["protected.mp4"].active_frame)
        self.assertEqual(by_name["plain.mp4"].active_frame, ActiveFrame(1600, 1000, 260, 40))

    def test_manual_active_frame_overrides_detection_and_reaches_encode_job(self) -> None:
        """사용자 지정 활성 화면이 자동 감지 대신 판정되고 인코딩 작업 crop 필터로 넘어가는지 검증"""
        clip = os.path.join(self.root, "manual.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"v" * 2048)
        stat = os.stat(clip)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.save(
                FrameAnalysisRecord(
                    filename="manual.mp4",
                    filesize_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    fingerprint=compute_fingerprint(clip, stat.st_size),
                    active_width=1600,
                    active_height=1000,
                    active_x=260,
                    active_y=40,
                    has_black_bars=True,
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(),
                    candidate_tier=None,
                    valid_segments=1,
                    total_segments=1,
                    unmeasurable=False,
                    is_vr_protected=False,
                    measured_at="2026-01-01T00:00:00Z",
                    analysis_version=FRAME_ANALYSIS_VERSION,
                )
            )
            manual = ActiveFrame(1280, 720, 0, 180)
            frame_store.set_manual_active_frame(
                clip, manual, frame_width=1920, frame_height=1080
            )
        finally:
            frame_store.close()

        vid = make_video(
            filename="manual.mp4", width=1920, height=1080, vid_kbps=10000, vid_size_MB=200.0
        )
        self.registry.put(self.root, TableCache(self.root, [vid], include_keyframe=False))
        report = self._service().evaluate_all()
        self.assertEqual(report.evaluations[0].active_frame, manual)
        jobs = jobs_from_evaluations(report.evaluations, self.root)
        self.assertEqual(jobs[0].crop_filter, "crop=1280:720:0:180")

    def test_manual_active_frame_judges_as_manual_encode_with_transform_gate(self) -> None:
        """저장된 사용자 지정 활성 화면이 판정에 전달되어 품질 인코딩과 변환 우선 게이트로 이어지는지 검증"""
        clip = os.path.join(self.root, "manual_gate.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"v" * 2048)
        stat = os.stat(clip)
        manual = ActiveFrame(1280, 720, 0, 180)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.save(
                FrameAnalysisRecord(
                    filename="manual_gate.mp4",
                    filesize_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    fingerprint=compute_fingerprint(clip, stat.st_size),
                    active_width=1920,
                    active_height=1080,
                    active_x=0,
                    active_y=0,
                    has_black_bars=False,
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(),
                    candidate_tier=None,
                    valid_segments=1,
                    total_segments=1,
                    unmeasurable=False,
                    is_vr_protected=False,
                    measured_at="2026-01-01T00:00:00Z",
                    analysis_version=FRAME_ANALYSIS_VERSION,
                )
            )
            frame_store.set_manual_active_frame(
                clip, manual, frame_width=1920, frame_height=1080
            )
        finally:
            frame_store.close()

        # - 과화질이지만 축소 후보가 없으므로 사용자 지정 활성 화면이 직접 지정 품질 인코딩으로 판정됨
        vid = make_video(
            filename="manual_gate.mp4", width=1920, height=1080, vid_kbps=10000, vid_size_MB=200.0
        )
        self.registry.put(self.root, TableCache(self.root, [vid], include_keyframe=False))
        ev = self._service().evaluate_all().evaluations[0]
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_MANUAL)
        self.assertTrue(ev.manual_active_frame_applied)
        self.assertEqual(ev.active_frame, manual)

        job = jobs_from_evaluations([ev], self.root)[0]
        self.assertIs(job.purpose, GatePurpose.TRANSFORM)
        self.assertEqual(job.crop_filter, manual.crop_filter_expr())

    def test_manual_active_frame_wins_over_crop_exclusion(self) -> None:
        """영상별 잘라내기 제외가 있어도 사용자 지정 활성 화면은 기존 규칙대로 덮어써 판정에 쓰임"""
        clip = os.path.join(self.root, "excluded.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"v" * 2048)
        stat = os.stat(clip)
        manual = ActiveFrame(1280, 720, 0, 180)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.save(
                FrameAnalysisRecord(
                    filename="excluded.mp4",
                    filesize_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    fingerprint=compute_fingerprint(clip, stat.st_size),
                    active_width=1920,
                    active_height=1080,
                    active_x=0,
                    active_y=0,
                    has_black_bars=True,
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(),
                    candidate_tier=None,
                    valid_segments=1,
                    total_segments=1,
                    unmeasurable=False,
                    is_vr_protected=False,
                    measured_at="2026-01-01T00:00:00Z",
                    analysis_version=FRAME_ANALYSIS_VERSION,
                )
            )
            frame_store.set_manual_active_frame(
                clip, manual, frame_width=1920, frame_height=1080
            )
            frame_store.set_crop_exclusion(clip, True)
        finally:
            frame_store.close()

        vid = make_video(
            filename="excluded.mp4", width=1920, height=1080, vid_kbps=2500, vid_size_MB=120.0
        )
        self.registry.put(self.root, TableCache(self.root, [vid], include_keyframe=False))
        ev = self._service().evaluate_all().evaluations[0]
        self.assertEqual(ev.active_frame, manual)
        self.assertTrue(ev.manual_active_frame_applied)
        self.assertEqual(ev.smart_action, SmartAction.ENCODE_MANUAL)

    def test_manual_active_frame_is_ignored_when_crop_setting_is_off(self) -> None:
        """전역 잘라내기 설정이 꺼져 있으면 사용자 지정 활성 화면을 판정에 넘기지 않음"""
        clip = os.path.join(self.root, "no_crop.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"v" * 2048)
        stat = os.stat(clip)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.set_vr_override(source_path=clip, filename="no_crop.mp4", manual_vr=False)
            frame_store.save(
                FrameAnalysisRecord(
                    filename="no_crop.mp4",
                    filesize_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    fingerprint=compute_fingerprint(clip, stat.st_size),
                    active_width=1920,
                    active_height=1080,
                    active_x=0,
                    active_y=0,
                    has_black_bars=False,
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(),
                    candidate_tier=None,
                    valid_segments=1,
                    total_segments=1,
                    unmeasurable=False,
                    is_vr_protected=False,
                    measured_at="2026-01-01T00:00:00Z",
                    analysis_version=FRAME_ANALYSIS_VERSION,
                )
            )
            frame_store.set_manual_active_frame(
                clip, ActiveFrame(1280, 720, 0, 180), frame_width=1920, frame_height=1080
            )
        finally:
            frame_store.close()

        self.config.encode.crop_black_bars = False
        vid = make_video(
            filename="no_crop.mp4", width=1920, height=1080, vid_kbps=10000, vid_size_MB=200.0
        )
        self.registry.put(self.root, TableCache(self.root, [vid], include_keyframe=False))
        ev = self._service().evaluate_all().evaluations[0]
        self.assertIsNone(ev.active_frame)
        self.assertFalse(ev.manual_active_frame_applied)
        self.assertIsNot(ev.smart_action, SmartAction.ENCODE_MANUAL)

    def test_crop_review_hold_applies_only_to_original_replacement(self) -> None:
        """어두운 근거로 지점을 무시한 자동 크롭은 원본 교체에서 보류되고 별도 출력에서는 유지되는지 검증"""
    def test_crop_review_hold_applies_to_every_output_mode(self) -> None:
        """어두운 근거로 지점을 무시한 자동 크롭은 출력 방식과 무관하게 보류되는지 검증"""
        clip = os.path.join(self.root, "review.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"v" * 2048)
        stat = os.stat(clip)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.save(
                FrameAnalysisRecord(
                    filename="review.mp4",
                    filesize_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    fingerprint=compute_fingerprint(clip, stat.st_size),
                    active_width=1600,
                    active_height=1000,
                    active_x=260,
                    active_y=40,
                    has_black_bars=True,
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(),
                    candidate_tier=None,
                    valid_segments=1,
                    total_segments=1,
                    unmeasurable=False,
                    is_vr_protected=False,
                    measured_at="2026-01-01T00:00:00Z",
                    analysis_version=FRAME_ANALYSIS_VERSION,
                    dark_outliers_ignored=True,
                )
            )
        finally:
            frame_store.close()

        vid = make_video(
            filename="review.mp4", width=1920, height=1080, vid_kbps=10000, vid_size_MB=200.0
        )
        self.registry.put(self.root, TableCache(self.root, [vid], include_keyframe=False))
        svc = self._service()

        for policy in (RESULT_POLICY_REPLACE, RESULT_POLICY_SEPARATE, RESULT_POLICY_ARCHIVE):
            self.config.execution.result_policy = policy
            report = svc.evaluate_all()
            self.assertIsNone(report.evaluations[0].active_frame, policy)
            self.assertIsNone(
                jobs_from_evaluations(report.evaluations, self.root)[0].crop_filter, policy
            )

        # 사용자 지정 활성 화면은 출력 방식과 무관하게 사람 확인 보류를 풂
        manual = ActiveFrame(1280, 720, 0, 180)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.set_manual_active_frame(
                clip, manual, frame_width=1920, frame_height=1080
            )
        finally:
            frame_store.close()
        self.config.execution.result_policy = RESULT_POLICY_REPLACE
        lifted = svc.evaluate_all()
        self.assertEqual(lifted.evaluations[0].active_frame, manual)
        self.assertEqual(
            jobs_from_evaluations(lifted.evaluations, self.root)[0].crop_filter,
            manual.crop_filter_expr(),
        )

    def test_frame_analysis_target_feeds_downscale_judgment(self) -> None:
        """화질 분석 캐시의 축소 후보가 자동 적용되어 화질 분석 축소 사유로 판정되는지 검증"""
        clip = os.path.join(self.root, "target.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"v" * 2048)
        stat = os.stat(clip)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.save(
                FrameAnalysisRecord(
                    filename="target.mp4",
                    filesize_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    fingerprint=compute_fingerprint(clip, stat.st_size),
                    active_width=1920,
                    active_height=1080,
                    active_x=0,
                    active_y=0,
                    has_black_bars=False,
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(),
                    candidate_tier="720p",
                    valid_segments=9,
                    total_segments=9,
                    unmeasurable=False,
                    is_vr_protected=False,
                    measured_at="2026-01-01T00:00:00Z",
                    analysis_version=FRAME_ANALYSIS_VERSION,
                )
            )
        finally:
            frame_store.close()

        vid = make_video(
            filename="target.mp4", width=1920, height=1080, vid_kbps=2500, vid_size_MB=120.0
        )
        self.registry.put(self.root, TableCache(self.root, [vid], include_keyframe=False))
        applied = self._service().evaluate_all().evaluations[0]
        self.assertEqual(applied.downscale_reason, DownscaleReason.FRAME_ANALYSIS)
        self.assertEqual((applied.target_width, applied.target_height), (1280, 720))
        self.assertIsNone(applied.active_frame)

        self.config.encode.auto_apply_frame_target = False
        ignored = self._service().evaluate_all().evaluations[0]
        self.assertIsNone(ignored.downscale_reason)

    def test_scale_algorithm_change_marks_frame_cache_stale(self) -> None:
        """크기 조절 방식을 바꾸면 저장된 화질 분석 후보를 쓰지 않아 다시 분석 대상이 되는지 검증"""
        clip = os.path.join(self.root, "algo.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"v" * 2048)
        stat = os.stat(clip)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.save(
                FrameAnalysisRecord(
                    filename="algo.mp4",
                    filesize_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    fingerprint=compute_fingerprint(clip, stat.st_size),
                    active_width=1920,
                    active_height=1080,
                    active_x=0,
                    active_y=0,
                    has_black_bars=False,
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(),
                    candidate_tier="720p",
                    valid_segments=9,
                    total_segments=9,
                    unmeasurable=False,
                    is_vr_protected=False,
                    measured_at="2026-01-01T00:00:00Z",
                    analysis_version=FRAME_ANALYSIS_VERSION,
                )
            )
        finally:
            frame_store.close()

        vid = make_video(
            filename="algo.mp4", width=1920, height=1080, vid_kbps=2500, vid_size_MB=120.0
        )
        self.registry.put(self.root, TableCache(self.root, [vid], include_keyframe=False))
        applied = self._service().evaluate_all().evaluations[0]
        self.assertEqual(applied.downscale_reason, DownscaleReason.FRAME_ANALYSIS)

        self.config.encode.scale_algorithm = "lanczos"
        stale = self._service().evaluate_all().evaluations[0]
        self.assertIsNone(stale.downscale_reason)

    def test_relaxed_thresholds_rejudge_the_frame_cache_candidate(self) -> None:
        """환경 설정에서 기준을 바꾸면 저장 점수로 다시 판정한 후보가 판정에 쓰이는지 검증"""
        clip = os.path.join(self.root, "target.mp4")
        with open(clip, "wb") as handle:
            handle.write(b"v" * 2048)
        stat = os.stat(clip)
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.save(
                FrameAnalysisRecord(
                    filename="target.mp4",
                    filesize_bytes=stat.st_size,
                    mtime_ns=stat.st_mtime_ns,
                    fingerprint=compute_fingerprint(clip, stat.st_size),
                    active_width=1920,
                    active_height=1080,
                    active_x=0,
                    active_y=0,
                    has_black_bars=False,
                    metric_used="vmaf",
                    vmaf_model="vmaf_v0.6.1",
                    tier_scores=(
                        TierScore("900p", 0.997, 98.5),
                        TierScore("720p", 0.994, 96.0),
                        TierScore("540p", 0.992, 95.2),
                        TierScore("480p", 0.988, 92.0),
                    ),
                    candidate_tier="720p",
                    valid_segments=9,
                    total_segments=9,
                    unmeasurable=False,
                    is_vr_protected=False,
                    measured_at="2026-01-01T00:00:00Z",
                    analysis_version=FRAME_ANALYSIS_VERSION,
                )
            )
        finally:
            frame_store.close()

        self.config.encode.roundtrip_ssim_threshold = 0.990
        self.config.encode.roundtrip_vmaf_threshold = 95.0
        vid = make_video(
            filename="target.mp4", width=1920, height=1080, vid_kbps=2500, vid_size_MB=120.0
        )
        self.registry.put(self.root, TableCache(self.root, [vid], include_keyframe=False))
        applied = self._service().evaluate_all().evaluations[0]
        self.assertEqual(applied.downscale_reason, DownscaleReason.FRAME_ANALYSIS)
        self.assertEqual((applied.target_width, applied.target_height), (960, 540))

    def test_empty_frame_cache_skips_file_stat(self) -> None:
        """화질 분석 캐시가 비어 있으면 원본 파일 stat을 하지 않는지 검증"""
        empty_db = os.path.join(self._tmp.name, "empty_cache.sqlite3")
        vid = make_video(filename="missing.mp4")
        real_stat = os.stat

        def _guard(path: object, *args: object, **kwargs: object) -> os.stat_result:
            if str(path).endswith("missing.mp4"):
                raise AssertionError("영상 파일 stat 금지")
            return real_stat(path, *args, **kwargs)  # type: ignore[arg-type]

        with mock.patch("src.service.classifier_service.os.stat", side_effect=_guard):
            for view in (
                _resolve_frame_cache_for(self.root, [vid], empty_db),
                _resolve_frame_cache_for(self.root, [vid], None),
                _resolve_frame_cache_for(self.root, [vid], empty_db, frame_targets_enabled=False),
            ):
                self.assertEqual(view.manual_frames, {})
                self.assertEqual(view.active_frames, {})
                self.assertEqual(view.frame_targets, {})


class TestServiceDesignationFollowsContent(unittest.TestCase):
    """서비스가 지정 행 크기의 파일만 지문을 확인하고 새 경로에 지정을 붙이는지"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "work")
        os.makedirs(self.root)
        self.db_path = os.path.join(self._tmp.name, "video_cache.sqlite3")
        self.store = AnalysisStore(self.db_path)
        self.addCleanup(self.store.close)
        self.registry = CacheRegistry()
        self.clip = os.path.join(self.root, "clip.mp4")
        with open(self.clip, "wb") as handle:
            handle.write(b"v" * 4096)
        self.other = os.path.join(self.root, "other.mp4")
        with open(self.other, "wb") as handle:
            handle.write(b"o" * 32)
        self.props = [
            make_video(filename="clip.mp4"),
            make_video(filename="other.mp4"),
        ]
        self.registry.put(self.root, TableCache(self.root, list(self.props), include_keyframe=False))

    def _service(self) -> VideoClassifierService:
        return VideoClassifierService(
            self.root, ClassifierConfig(), registry=self.registry, store=self.store
        )

    def _identity(self, path: str) -> tuple[int, str]:
        stat = os.stat(path)
        return (stat.st_size, compute_fingerprint(path, stat.st_size))

    def test_no_designation_means_no_file_is_read(self) -> None:
        with mock.patch(
            "src.service.frame_analysis_store.computed_fingerprint",
            side_effect=AssertionError("지정이 없으면 지문을 계산하지 않음"),
        ):
            self.assertEqual(self._service().scan_content_identities(), {})

    def test_only_files_with_a_designation_size_are_fingerprinted(self) -> None:
        self.store.write_manual_targets({self.root: {"clip.mp4": "720p"}})
        found = self._service().scan_content_identities()
        self.assertEqual(found, {self.clip: self._identity(self.clip)})

    def test_fingerprint_known_to_the_analysis_cache_is_reused(self) -> None:
        """분석 캐시가 이미 아는 지문은 영상을 다시 읽지 않고 씀"""
        self.store.write_manual_targets({self.root: {"clip.mp4": "720p"}})
        with mock.patch(
            "src.core.analysis.file_fingerprint.compute_fingerprint",
            side_effect=AssertionError("분석 캐시가 아는 지문은 다시 계산하지 않음"),
        ):
            found = self._service().scan_content_identities()
        self.assertEqual(found[self.clip][0], os.stat(self.clip).st_size)

    def test_sync_moves_the_path_rows_and_the_manual_target(self) -> None:
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            frame_store.set_crop_exclusion(self.clip, True)
        finally:
            frame_store.close()
        self.store.write_manual_targets({normalize_root_key(self.root): {"clip.mp4": "720p"}})
        renamed = os.path.join(self.root, "renamed.mp4")
        os.rename(self.clip, renamed)
        self.registry.put(
            self.root,
            TableCache(
                self.root,
                [make_video(filename="renamed.mp4"), make_video(filename="other.mp4")],
                include_keyframe=False,
            ),
        )
        service = self._service()
        service.manual_targets = {"clip.mp4": "720p"}
        result = service.sync_designations()
        key = normalize_root_key(self.root)
        self.assertEqual(
            result.moved,
            [((key, "clip.mp4"), (key, "renamed.mp4"))],
        )
        self.assertEqual(
            self.store.read_manual_targets({}),
            {normalize_root_key(self.root): {"renamed.mp4": "720p"}},
        )
        frame_store = FrameAnalysisStore(self.db_path)
        try:
            self.assertEqual(frame_store.load_crop_exclusions([renamed]), frozenset({renamed}))
        finally:
            frame_store.close()


class TestPrepareCachePrescanReuse(unittest.TestCase):
    """시작 복원 판정이 읽은 행과 목록을 이어 받아 같은 분석을 두 번 하지 않는지 검증"""

    _NAMES: tuple[str, ...] = ("a.mp4", "b.mp4")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = os.path.join(self._tmp.name, "work")
        os.makedirs(self.root)
        self.store = AnalysisStore(os.path.join(self._tmp.name, "video_cache.sqlite3"))
        self.config = ClassifierConfig()
        for name in self._NAMES:
            with open(os.path.join(self.root, name), "wb") as handle:
                handle.write(name.encode("utf-8").ljust(64, b"."))
        self.store.apply((), [self._entry(name) for name in self._NAMES])

    def tearDown(self) -> None:
        self.store.close()
        self._tmp.cleanup()

    def _entry(self, name: str) -> StoredEntry:
        path = os.path.join(self.root, name)
        stat = os.stat(path)
        return StoredEntry(
            name,
            stat.st_size,
            stat.st_mtime_ns,
            compute_fingerprint(path, stat.st_size),
            make_video(filename=name, keyframe_interval=None),
        )

    def _service(self) -> VideoClassifierService:
        return VideoClassifierService(
            self.root, self.config, registry=CacheRegistry(), store=self.store
        )

    def _stored(self) -> probe_mod.StoredRows:
        # - 실제 복원 판정과 같이 서비스가 스캔에서 빼는 폴더 이름을 넘겨 범위를 맞춤
        stored = load_stored_rows(
            self.store,
            self.root,
            excluded_dirnames=self._service().excluded_dirnames,
        )
        assert stored is not None
        return stored

    def test_matching_scope_reuses_the_scanned_rows_without_a_name_query(self) -> None:
        """판정 범위가 같으면 행 조회를 다시 하지 않고 같은 캐시를 얻는지 검증"""
        stored = self._stored()
        svc = self._service()

        with (
            mock.patch.object(
                self.store,
                "fetch_by_names",
                side_effect=AssertionError("행 조회를 다시 하면 안 됨"),
            ),
            mock.patch(
                "src.service.analysis_probe.probe_files", side_effect=AssertionError("ffprobe 금지")
            ),
        ):
            cache = svc.prepare_cache(prescan=stored)

        self.assertEqual([vid.filename for vid in cache.data], ["a.mp4", "b.mp4"])
        # - 다시 나열한 목록을 시그니처로 저장해 다음 점검이 폴더를 다시 훑지 않게 함
        self.assertEqual(svc.registry.saved_signature(self.root), stored.dir_signature)

    def test_scope_change_falls_back_to_a_direct_query(self) -> None:
        """판정 범위와 분석 범위가 다르면 판정 결과를 재사용하지 않는지 검증"""
        stored = self._stored()
        svc = self._service()
        # - 판정은 하위 폴더 제외로 훑었으므로 포함 분석은 같은 목록을 쓸 수 없음
        svc.recursive = True

        with mock.patch.object(
            self.store, "fetch_by_names", wraps=self.store.fetch_by_names
        ) as fetch:
            svc.prepare_cache(prescan=stored)

        self.assertGreater(fetch.call_count, 0)

    def test_reuse_is_disabled_when_the_store_reuse_is_off(self) -> None:
        """강제 재분석으로 저장 행 재사용을 끄면 판정 결과를 무시하고 전부 다시 분석하는지 검증"""
        stored = self._stored()
        svc = self._service()
        svc.invalidate_cache()

        with (
            mock.patch.object(
                self.store,
                "fetch_by_names",
                side_effect=AssertionError("행 조회를 다시 하면 안 됨"),
            ),
            mock.patch(
                "src.service.analysis_probe.probe_files",
                return_value=[make_video(filename=name, keyframe_interval=None) for name in self._NAMES],
            ) as probe,
        ):
            cache = svc.prepare_cache(prescan=stored)

        probe.assert_called_once()
        self.assertEqual(sorted(vid.filename for vid in cache.data), ["a.mp4", "b.mp4"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
