import os
import tempfile
import unittest
from unittest.mock import patch

from src.core.models import VideoProps
from src.service.cache import TableCache
from src.service.classifier_service import VideoClassifierService


class TestServiceAndCacheModule(unittest.TestCase):
    """TableCache 인메모리 수명주기, 디렉터리별 캐시 격리, 시그니처 자동 감지 및 이동 실패 원복을 검증"""

    def tearDown(self) -> None:
        VideoClassifierService.invalidate_shared_cache()

    def test_table_cache_empty_and_invalid_dir(self) -> None:
        """존재하지 않는 경로 지정 시 FileNotFoundError 발생 및 빈 디렉터리 정상 인식을 검증"""
        with self.assertRaises(FileNotFoundError):
            TableCache("C:/non_existent_folder_xyz_123")

        with tempfile.TemporaryDirectory() as tmpdir:
            cache = TableCache(tmpdir)
            self.assertEqual(cache.data, [])

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

    def test_service_detects_new_file_after_partial_disk_move(self) -> None:
        """일부 파일이 하위 폴더로 이동된 이후에도 루트 폴더에 새 파일이 추가되면 캐시를 정확히 무효화함을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            vid_path = os.path.join(tmpdir, "over_bitrate.mp4")
            with open(vid_path, "wb") as f:
                f.write(b"video-data")

            over_vid = VideoProps(
                filename="over_bitrate.mp4",
                width=1280,
                height=720,
                rotate_type=0,
                fps=30.0,
                vid_kbps=9000,
                aud_kbps=128,
                vid_size_MB=80.0,
                duration=70.0,
                codec="h264",
                keyframe_interval=1.0,
            )
            cache = TableCache.__new__(TableCache)
            cache._root_dir = tmpdir
            cache._data = [over_vid]
            cache._include_keyframe = True
            VideoClassifierService.inject_cache(tmpdir, cache)

            svc = VideoClassifierService(root_dir=tmpdir)
            svc.pseudo_classify = False
            svc.classify()

            # 이동 직후 캐시는 최신 루트 시그니처로 유지됨
            svc_after_move = VideoClassifierService(root_dir=tmpdir)
            self.assertIsNotNone(svc_after_move.cache)

            # 이후 외부에서 루트에 새 파일 추가 시 시그니처 변화가 감지되어 캐시 무효화됨
            with open(os.path.join(tmpdir, "incoming.mp4"), "wb") as f:
                f.write(b"new-file")

            svc_after_add = VideoClassifierService(root_dir=tmpdir)
            self.assertIsNone(svc_after_add.cache)

    def test_service_classify_rolls_back_on_partial_io_failure(self) -> None:
        """실제 분류 중 중간 파일 이동이 실패할 때 앞서 이동된 파일까지 루트로 원복됨을 검증"""
        import shutil

        with tempfile.TemporaryDirectory() as tmpdir:
            v1_path = os.path.join(tmpdir, "first.mp4")
            v2_path = os.path.join(tmpdir, "second.mp4")
            with open(v1_path, "wb") as f:
                f.write(b"one")
            with open(v2_path, "wb") as f:
                f.write(b"two")

            videos = [
                VideoProps("first.mp4", 1280, 720, 0, 30.0, 9000, 128, 80.0, 70.0, "h264", 1.0),
                VideoProps("second.mp4", 1280, 720, 0, 30.0, 9000, 128, 80.0, 70.0, "h264", 1.0),
            ]
            cache = TableCache.__new__(TableCache)
            cache._root_dir = tmpdir
            cache._data = videos
            cache._include_keyframe = True
            VideoClassifierService.inject_cache(tmpdir, cache)

            orig_move = shutil.move
            call_count = 0

            def flaky_move(src: str, dst: str) -> str:
                nonlocal call_count
                call_count += 1
                if call_count == 2:
                    raise PermissionError("simulated file lock")
                return orig_move(src, dst)

            svc = VideoClassifierService(root_dir=tmpdir)
            svc.pseudo_classify = False

            with patch("src.core.file_manager.shutil.move", side_effect=flaky_move), self.assertRaises(RuntimeError):
                svc.classify()

            # 첫 번째 파일이 루트로 원복되고 메모리 상태도 초기화되어야 함
            self.assertTrue(os.path.isfile(v1_path))
            self.assertTrue(os.path.isfile(v2_path))
            self.assertIsNone(videos[0].moved_dirname)
            self.assertIsNone(videos[1].moved_dirname)

    def test_service_pseudo_classify_leaves_disk_untouched(self) -> None:
        """pseudo_classify_mode에서는 디스크 파일 이동 없이 메모리 상의 moved_dirname만 갱신됨을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "test.mp4")
            with open(fpath, "wb") as f:
                f.write(b"data")

            svc = VideoClassifierService(root_dir=tmpdir)
            svc.pseudo_classify = True

            # 모의 분류 시 파일이 여전히 루트에 존재하는지 확인
            svc.classify()
            self.assertTrue(os.path.isfile(fpath))

    def test_service_unclassify_files_restores_disk_and_keeps_signature_valid(self) -> None:
        """VideoClassifierService가 실이동 분류 후 unclassify_files 실행 시 디스크 복구와 디렉터리 시그니처 일관성을 유지하는지 검증"""
        self.addCleanup(VideoClassifierService.invalidate_shared_cache)
        with tempfile.TemporaryDirectory() as tmpdir:
            fake_env = os.path.join(tmpdir, ".env")
            fake_rootdir = os.path.join(tmpdir, ".rootdir")
            with open(fake_env, "w", encoding="utf-8") as f:
                f.write("PSEUDO_CLASSIFY_MODE=False\n")
            with open(fake_rootdir, "w", encoding="utf-8") as f:
                f.write(tmpdir)

            fpath = os.path.join(tmpdir, "over.mp4")
            with open(fpath, "wb") as f:
                f.write(b"video-data")

            over_vid = VideoProps(
                "over.mp4", 1280, 720, 0, 30.0, 5000, 128, 120.0, 10.0, "h264", 1.0
            )
            with (
                patch.dict(os.environ, {}, clear=False),
                patch("src.core.config.get_env_path", return_value=fake_env),
                patch("src.core.config.get_rootdir_path", return_value=fake_rootdir),
                patch("src.service.cache.probe_directory", return_value=[over_vid]) as mock_probe,
                patch("src.service.cache.populate_keyframes") as mock_kf,
            ):
                VideoClassifierService.invalidate_shared_cache()
                svc = VideoClassifierService(tmpdir)

                report = svc.classify()
                moved_fpath = os.path.join(tmpdir, "_1_표준인코딩_CQ22", "over.mp4")
                self.assertFalse(os.path.isfile(fpath))
                self.assertTrue(os.path.isfile(moved_fpath))
                self.assertEqual(over_vid.moved_dirname, "_1_표준인코딩_CQ22")
                self.assertEqual(len(report.evaluations), 1)
                mock_probe.assert_called_once()

                restored_cnt, warnings = svc.unclassify_files(unmark_pseudo_classified_only=False)
                self.assertEqual(restored_cnt, 1)
                self.assertEqual(warnings, [])
                self.assertTrue(os.path.isfile(fpath))
                self.assertFalse(os.path.isdir(os.path.join(tmpdir, "_1_표준인코딩_CQ22")))
                self.assertIsNone(over_vid.moved_dirname)

                # 복구 후 재평가 시 시그니처가 유효하게 유지되어 probe_directory 재호출 없이 캐시 재사용
                _ = svc.evaluate_all()
                mock_probe.assert_called_once()
                mock_kf.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
