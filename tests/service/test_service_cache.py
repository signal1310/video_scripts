"""TableCache 수명주기, 레지스트리 주입, 시그니처 무효화, 이동 원복을 검증"""

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

from src.core.models import VideoProps
from src.service.cache import DEFAULT_CACHE_REGISTRY, CacheRegistry, TableCache
from src.service.classifier_service import VideoClassifierService


class TestServiceAndCacheModule(unittest.TestCase):
    """TableCache 인메모리 수명주기, 디렉터리별 캐시 격리, 시그니처 자동 감지 및 이동 실패 원복을 검증"""

    def tearDown(self) -> None:
        """기본 레지스트리 캐시를 초기화해 테스트 간 상태 누수를 막음"""
        DEFAULT_CACHE_REGISTRY.invalidate()

    def test_table_cache_empty_and_invalid_dir(self) -> None:
        """존재하지 않는 경로 지정 시 FileNotFoundError 발생 및 빈 디렉터리 정상 인식을 검증"""
        with self.assertRaises(FileNotFoundError):
            TableCache.probe("C:/non_existent_folder_xyz_123", include_keyframe=False)

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TableCache.probe(tmpdir, include_keyframe=False)
            self.assertEqual(cache.data, [])

    def test_table_cache_probe_is_silent_without_callback(self) -> None:
        """on_progress 미지정 시 probe가 캐리지 리턴 기반 진행률 출력을 하지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch("src.service.cache.probe_directory", return_value=[]), redirect_stdout(io.StringIO()) as out:
                TableCache.probe(tmpdir, include_keyframe=False)
            self.assertNotIn("\r", out.getvalue())

    def test_service_cache_isolation_across_multiple_root_dirs(self) -> None:
        """서로 다른 작업 디렉터리의 서비스 인스턴스가 각자의 공유 캐시를 덮어쓰지 않고 독립 보관함을 검증"""
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            svc_a = VideoClassifierService(root_dir=dir_a)
            svc_a.prepare_cache()
            cache_a = svc_a.cache
            assert cache_a is not None
            self.assertEqual(cache_a.root_dir, dir_a)

            svc_b = VideoClassifierService(root_dir=dir_b)
            svc_b.prepare_cache()
            cache_b = svc_b.cache
            assert cache_b is not None
            self.assertEqual(cache_b.root_dir, dir_b)

            # dir_b 초기화가 dir_a 캐시를 훼손하지 않아야 함
            self.assertIs(svc_a.cache, cache_a)
            self.assertIsNot(cache_a, cache_b)

    def test_service_cache_invalidation_on_file_change(self) -> None:
        """폴더 내 파일 변경 시 시그니처 불일치를 감지하여 해당 경로의 캐시만 자동 무효화하는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = VideoClassifierService(root_dir=tmpdir)
            svc.prepare_cache()
            assert svc.cache is not None
            self.assertEqual(len(svc.cache.data), 0)

            # 새 파일 생성
            with open(os.path.join(tmpdir, "new.mp4"), "wb") as f:
                f.write(b"data")

            # 새 서비스 인스턴스 생성 시 시그니처 불일치로 해당 경로 캐시 무효화
            svc_next = VideoClassifierService(root_dir=tmpdir)
            self.assertIsNone(svc_next.cache)

    def test_evaluate_cached_returns_none_without_cache(self) -> None:
        """캐시가 없는 작업 폴더에서 evaluate_cached가 디스크 조회 없이 None을 반환함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = VideoClassifierService(root_dir=tmpdir)
            self.assertIsNone(svc.evaluate_cached())

    def test_evaluate_cached_skips_disk_after_external_change(self) -> None:
        """외부에서 폴더가 바뀌어도 evaluate_cached가 시그니처 계산과 프로빙 없이 메모리 캐시만 평가함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            over_vid = VideoProps(
                "over.mp4", 1280, 720, 0, 30.0, 9000, 128, 80.0, 70.0, "h264", 1.0
            )
            cache = TableCache(tmpdir, [over_vid], include_keyframe=True)
            DEFAULT_CACHE_REGISTRY.put(tmpdir, cache)

            svc = VideoClassifierService(root_dir=tmpdir)
            with open(os.path.join(tmpdir, "incoming.mp4"), "wb") as f:
                f.write(b"new-file")

            with (
                patch("src.service.cache.probe_directory") as mock_probe,
                patch("src.service.cache.compute_dir_signature") as mock_sig,
            ):
                rep = svc.evaluate_cached()

            self.assertIsNotNone(rep)
            assert rep is not None
            self.assertEqual(len(rep.evaluations), 1)
            mock_probe.assert_not_called()
            mock_sig.assert_not_called()

    def test_separate_registries_do_not_share_cache(self) -> None:
        """서로 다른 CacheRegistry를 주입한 서비스는 같은 작업 폴더라도 캐시를 공유하지 않음을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc_a = VideoClassifierService(root_dir=tmpdir, registry=CacheRegistry())
            svc_b = VideoClassifierService(root_dir=tmpdir, registry=CacheRegistry())
            svc_a.prepare_cache()
            self.assertIsNotNone(svc_a.cache)
            self.assertIsNone(svc_b.cache)

    def test_default_registry_is_shared_by_services(self) -> None:
        """레지스트리를 생략한 서비스는 기본 레지스트리를 공유해 같은 작업 폴더 캐시를 재사용함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            svc_a = VideoClassifierService(root_dir=tmpdir)
            svc_a.prepare_cache()
            svc_b = VideoClassifierService(root_dir=tmpdir)
            self.assertIs(svc_b.cache, svc_a.cache)

if __name__ == "__main__":
    unittest.main(verbosity=2)
