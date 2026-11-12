import os
import tempfile
import unittest

from src.core.models import VideoProps
from src.service.cache import TableCache
from src.service.classifier_service import VideoClassifierService


class TestServiceAndCacheModule(unittest.TestCase):
    """
    - TableCache 인메모리 수명주기, 디렉터리 시그니처 자동 감지 및 가분류 격리를 검증.
    """

    def test_table_cache_empty_and_invalid_dir(self) -> None:
        """
        - 존재하지 않는 경로 지정 시 FileNotFoundError 발생 및 빈 디렉터리 정상 인식을 검증.
        """
        with self.assertRaises(FileNotFoundError):
            TableCache("C:/non_existent_folder_xyz_123")

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TableCache(tmpdir)
            self.assertEqual(cache.data, [])

    def test_service_cache_invalidation_on_root_dir_switch(self) -> None:
        """
        - 작업 디렉터리가 변경되었을 때 클래스 공유 캐시가 안전하게 초기화되는지 검증.
        """
        with tempfile.TemporaryDirectory() as dir_a, tempfile.TemporaryDirectory() as dir_b:
            svc_a = VideoClassifierService(root_dir=dir_a)
            svc_a.prepare_cache()
            self.assertEqual(VideoClassifierService._shared_cache.root_dir, dir_a)

            svc_b = VideoClassifierService(root_dir=dir_b)
            svc_b.prepare_cache()
            self.assertEqual(VideoClassifierService._shared_cache.root_dir, dir_b)

    def test_service_cache_invalidation_on_file_change(self) -> None:
        """
        - 폴더 내 파일 변경 시 시그니처 불일치를 감지하여 캐시를 자동 무효화하는지 검증.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            svc = VideoClassifierService(root_dir=tmpdir)
            svc.prepare_cache()
            self.assertEqual(len(svc.cache.data), 0)

            # 새 파일 생성
            with open(os.path.join(tmpdir, "new.mp4"), "wb") as f:
                f.write(b"data")

            # 새 서비스 인스턴스 생성 시 캐시 갱신 확인
            svc2 = VideoClassifierService(root_dir=tmpdir)
            # 시그니처가 달라졌으므로 캐시가 비워지고 다시 로드될 준비가 됨
            self.assertIsNone(VideoClassifierService._shared_cache)

    def test_service_pseudo_classify_leaves_disk_untouched(self) -> None:
        """
        - pseudo_classify_mode에서는 디스크 파일 이동 없이 메모리 상의 moved_dirname만 갱신됨을 검증.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "test.mp4")
            with open(fpath, "wb") as f:
                f.write(b"data")

            svc = VideoClassifierService(root_dir=tmpdir)
            svc.pseudo_classify = True

            # 모의 분류 시 파일이 여전히 루트에 존재하는지 확인
            report = svc.classify()
            self.assertTrue(os.path.isfile(fpath))


if __name__ == "__main__":
    unittest.main(verbosity=2)
