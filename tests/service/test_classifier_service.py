"""분류 서비스(VideoClassifierService)의 재인코딩 근거 3종 판정과 5->6 캐시 업그레이드 통합 단위 테스트"""

import contextlib
import os
import sqlite3
import struct
import tempfile
import unittest
from unittest import mock

from src.core.analysis.probe_version_rules import KEYFRAME_MEASURE_VERSION
from src.core.config import ClassifierConfig
from src.core.encode_fingerprint import compute_encode_fingerprint
from src.core.encode_tag import ENCODE_TAG_MODE_OFF, ENCODE_TAG_MODE_VISIBLE
from src.core.probe_parser import compress_probe_response
from src.core.smart_action import SmartAction
from src.service.analysis_probe import load_stored_rows
from src.service.analysis_store import ANALYSIS_TABLE_NAME, AnalysisStore, StoredEntry
from src.service.cache import CacheRegistry
from src.service.classifier_service import VideoClassifierService
from src.service.encode.encode_output_history import EncodeOutputHistory, record_done_output
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

        # 3. 직접 지정 축소가 있으면 이미 재인코딩 영상도 대상에 포함
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
