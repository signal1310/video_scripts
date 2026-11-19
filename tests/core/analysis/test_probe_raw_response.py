"""분석 응답 저장: 키프레임 패킷 목록 제외 압축, 저장 왕복, 저장된 응답으로 메타데이터 재계산, 풀에서 응답 전달을 검증"""

import contextlib
import os
import sqlite3
import tempfile
import unittest
from typing import Any
from unittest.mock import patch

from src.core.analysis import prober as prober_mod
from src.core.analysis.probe_version_rules import ROW_REREAD, row_reuse_action
from src.core.probe_parser import PROBE_VERSION, build_video_props, compress_probe_response, decompress_probe_response
from src.service import analysis_probe as probe_mod
from src.service.analysis_store import AnalysisStore, StoredEntry, compute_fingerprint
from tests._factories import make_video

_FILE_BYTES: bytes = b"r" * 64


def _probe(duration: str = "10.0", packets: bool = False) -> dict[str, Any]:
    fmt: dict[str, Any] = {"duration": duration, "size": "64", "bit_rate": "800000"}
    stream: dict[str, Any] = {
        "index": 0,
        "codec_type": "video",
        "codec_name": "h264",
        "width": 1920,
        "height": 1080,
        "avg_frame_rate": "30/1",
        "r_frame_rate": "30/1",
        "bit_rate": "700000",
        "duration": duration,
    }
    probe: dict[str, Any] = {"streams": [stream], "format": fmt}
    if packets:
        probe["packets"] = [{"stream_index": 0, "pts_time": "0.0", "flags": "K_"}] * 50
    return probe


class CompressedResponseTests(unittest.TestCase):
    def test_packets_are_dropped_and_the_rest_round_trips(self) -> None:
        """키프레임 패킷 목록은 저장하지 않고 나머지 응답은 그대로 복원되는지 검증"""
        blob = compress_probe_response(_probe(packets=True))
        restored = decompress_probe_response(blob)
        self.assertNotIn("packets", restored)
        self.assertEqual(restored, _probe(packets=False))


class StoredResponseFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "work")
        os.makedirs(self.root)
        self.db_path = os.path.join(self._tmp.name, "cache.sqlite3")
        self.store = AnalysisStore(self.db_path)
        self.addCleanup(self.store.close)

    def _write(self, name: str) -> os.stat_result:
        path = os.path.join(self.root, name)
        with open(path, "wb") as f:
            f.write(name.encode("utf-8").ljust(64, b"."))
        return os.stat(path)

    def _store_row(self, name: str, *, stored_duration: float, raw_probe: dict[str, Any] | None, version: int) -> None:
        stat = self._write(name)
        path = os.path.join(self.root, name)
        entry = StoredEntry(
            name,
            stat.st_size,
            stat.st_mtime_ns,
            compute_fingerprint(path, stat.st_size),
            make_video(filename=name, duration=stored_duration),
            probe_raw=compress_probe_response(raw_probe) if raw_probe is not None else None,
        )
        self.store.apply((), [entry])
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            conn.execute("UPDATE video_props SET probe_version = ? WHERE filename = ?", (version, name))
            conn.commit()

    def test_raw_response_is_stored_and_read_back(self) -> None:
        """저장한 압축 응답이 행에 남고 조회 때 같은 바이트로 돌아오는지 검증"""
        self._store_row("kept.mp4", stored_duration=10.0, raw_probe=_probe(), version=5)
        fetched = self.store.fetch_by_names(["kept.mp4"])
        self.assertEqual(len(fetched), 1)
        raw = fetched[0].probe_raw
        assert raw is not None
        self.assertEqual(decompress_probe_response(raw), _probe())

    def test_reread_row_is_recomputed_from_stored_response_without_ffprobe(self) -> None:
        """MKV 같은 다시 읽기 행은 저장된 응답으로 현재 규칙의 메타데이터를 만들고 ffprobe를 호출하지 않는지 검증"""
        self._store_row("long.mkv", stored_duration=5.0, raw_probe=_probe(duration="10.0"), version=4)
        self.assertEqual(row_reuse_action(4, "long.mkv"), ROW_REREAD)
        with patch.object(probe_mod, "probe_files", side_effect=AssertionError("ffprobe 호출 없어야 함")):
            result = probe_mod.probe_directory_with_store(self.root, include_keyframe=False, store=self.store)
        self.assertEqual([v.duration for v in result], [10.0])
        expected = build_video_props("long.mkv", os.path.getsize(os.path.join(self.root, "long.mkv")), _probe("10.0"))
        self.assertEqual(result[0].duration, expected.duration)
        with contextlib.closing(sqlite3.connect(self.db_path)) as conn:
            self.assertEqual(conn.execute("SELECT probe_version FROM video_props").fetchone()[0], PROBE_VERSION)

    def test_reread_row_without_stored_response_is_probed_again(self) -> None:
        """저장된 응답이 없는 옛 행은 필요한 필드가 없으므로 다시 조회하는지 검증"""
        self._store_row("old.mkv", stored_duration=5.0, raw_probe=None, version=4)
        calls: list[list[str]] = []

        def fake_probe(root: str, names: list[str], include_keyframe: bool, **_kwargs: object) -> list[object]:
            calls.append(list(names))
            return [make_video(filename=name, duration=10.0) for name in names]

        with patch.object(probe_mod, "probe_files", side_effect=fake_probe):
            probe_mod.probe_directory_with_store(self.root, include_keyframe=False, store=self.store)
        self.assertEqual(calls, [["old.mkv"]])


class ProbePoolResponseTests(unittest.TestCase):
    def test_probe_files_fills_compressed_responses_without_packets(self) -> None:
        """프로빙 풀이 파일명별 압축 응답을 채우고 키프레임 패킷 목록은 빼는지 검증"""
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "clip.mp4"), "wb") as f:
                f.write(_FILE_BYTES)
            with patch.object(prober_mod, "run_ffprobe", return_value=_probe(packets=True)):
                raw: dict[str, bytes] = {}
                props = prober_mod.probe_files(root, ["clip.mp4"], False, raw_out=raw)
        self.assertEqual([p.filename for p in props], ["clip.mp4"])
        self.assertEqual(decompress_probe_response(raw["clip.mp4"]), _probe())


if __name__ == "__main__":
    unittest.main()
