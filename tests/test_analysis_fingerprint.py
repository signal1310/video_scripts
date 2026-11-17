"""분석 지문 병렬 계산이 순차 계산과 같은 값을 내고, 읽기 실패 파일을 제외하며, 저장 장치 상한을 넘지 않는지 검증"""

import os
import tempfile
import unittest
from unittest.mock import patch

from src.core.storage_probe import StorageInfo, StorageKind, default_worker_count
from src.service import analysis_probe as probe_mod
from src.service.analysis_store import compute_fingerprint

_FIXED_CPU_COUNT: int = 8


class TestParallelFingerprint(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def _write_files(self, count: int) -> list[probe_mod.FileEntry]:
        entries: list[probe_mod.FileEntry] = []
        for i in range(count):
            name = f"clip{i:02d}.mp4"
            content = bytes([i]) * (1024 * (i + 1))
            with open(os.path.join(self.root, name), "wb") as f:
                f.write(content)
            entries.append(probe_mod.FileEntry(name, len(content), 0))
        return entries

    def test_parallel_result_matches_sequential_fingerprints(self) -> None:
        """병렬 계산 결과가 파일별 순차 지문과 모두 같은지 확인"""
        entries = self._write_files(12)
        expected = {
            entry.filename: compute_fingerprint(
                os.path.join(self.root, entry.filename), entry.filesize_bytes
            )
            for entry in entries
        }
        self.assertEqual(probe_mod._fingerprints_for(self.root, entries), expected)

    def test_unreadable_file_is_excluded_with_warning(self) -> None:
        """읽지 못한 파일은 결과에서 빠지고 경고 로그가 남는지 확인"""
        entries = self._write_files(2)
        missing = probe_mod.FileEntry("missing.mp4", 10, 0)
        with self.assertLogs("src.service.analysis_probe", level="WARNING") as logs:
            result = probe_mod._fingerprints_for(self.root, [*entries, missing])
        self.assertEqual(set(result), {entry.filename for entry in entries})
        self.assertTrue(any("missing.mp4" in line for line in logs.output))

    def test_empty_input_returns_empty_without_probing_storage(self) -> None:
        """대상이 없으면 저장 장치 조회 없이 빈 결과를 반환하는지 확인"""
        with patch.object(probe_mod, "probe_storage") as probed:
            self.assertEqual(probe_mod._fingerprints_for(self.root, []), {})
        probed.assert_not_called()

    def test_worker_count_follows_storage_limit(self) -> None:
        """저장 장치 종류에 따라 병렬 수가 정책 상한을 넘지 않는지 확인"""
        full = default_worker_count(_FIXED_CPU_COUNT)
        with patch.object(os, "cpu_count", return_value=_FIXED_CPU_COUNT):
            with patch.object(probe_mod, "probe_storage", return_value=StorageInfo(StorageKind.SSD)):
                self.assertEqual(probe_mod._fingerprint_workers(self.root), full)
            with patch.object(
                probe_mod, "probe_storage", return_value=StorageInfo(StorageKind.NETWORK)
            ):
                self.assertEqual(probe_mod._fingerprint_workers(self.root), max(1, full // 2))


if __name__ == "__main__":
    unittest.main()
