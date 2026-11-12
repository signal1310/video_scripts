import os
import tempfile
import unittest

from src.core.file_manager import (
    is_video_file,
    get_video_filenames,
    get_dirnames,
    file_exists_in,
    resolve_video_filepath,
    compute_dir_signature,
    move_file,
    unclassify_all_files,
)
from src.core.models import VideoProps


class TestFileManagerModule(unittest.TestCase):
    """
    - 파일 확장자 감지, 폴더명 오인 방지, 파일 이동 및 충돌 보호 원복의 무결성을 검증.
    """

    def test_is_video_file_extensions(self) -> None:
        """
        - 대소문자 혼합 비디오 확장자 인식 및 비비디오 포맷 배제를 검증.
        """
        for ext in (".mp4", ".MKV", ".Avi", ".webm", ".flv", ".ts", ".mov", ".wmv", ".m2ts"):
            self.assertTrue(is_video_file(f"clip{ext}"))

        for non_ext in (".srt", ".txt", ".jpg", ".ini", ".png", ".nfo", ""):
            self.assertFalse(is_video_file(f"file{non_ext}"))

    def test_get_video_filenames_excludes_directories_and_non_videos(self) -> None:
        """
        - 디렉터리 이름이 'folder.mp4'여도 비디오 파일로 취급하지 않음을 검증.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            real_file = os.path.join(tmpdir, "sample.mp4")
            with open(real_file, "wb") as f:
                f.write(b"data")

            srt_file = os.path.join(tmpdir, "sample.srt")
            with open(srt_file, "wb") as f:
                f.write(b"sub")

            dir_with_ext = os.path.join(tmpdir, "folder.mp4")
            os.makedirs(dir_with_ext, exist_ok=True)

            fnames = get_video_filenames(tmpdir)
            self.assertEqual(fnames, ["sample.mp4"])

            dirnames = get_dirnames(tmpdir)
            self.assertEqual(dirnames, ["folder.mp4"])

    def test_resolve_video_filepath(self) -> None:
        """
        - 비디오가 루트 경로에 있거나 하위 이동 폴더에 위치한 경우 정확한 물리 경로를 찾아내는지 검증.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "vid.mp4")
            with open(fpath, "wb") as f:
                f.write(b"video")

            vid = VideoProps("vid.mp4", 1280, 720, 0, 30.0, 1500, 128, 10.0, 5.0, "h264")
            resolved = resolve_video_filepath(tmpdir, vid)
            self.assertEqual(os.path.abspath(resolved), os.path.abspath(fpath))

    def test_move_file_atomicity_and_collision(self) -> None:
        """
        - 대상 디렉터리가 없을 때 자동 생성 및 목적지에 동일 파일 존재 시 덮어쓰지 않고 False를 반환함을 검증.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "test.mp4")
            with open(src, "wb") as f:
                f.write(b"SOURCE_DATA")

            # 1. 정상 이동
            ok = move_file(tmpdir, "test.mp4", "subfolder")
            self.assertTrue(ok)
            self.assertFalse(os.path.isfile(src))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "subfolder", "test.mp4")))

            # 2. 동일 파일 충돌 시 덮어쓰기 방지
            with open(src, "wb") as f:
                f.write(b"NEW_DATA")
            ok_collision = move_file(tmpdir, "test.mp4", "subfolder")
            self.assertFalse(ok_collision)
            # 기존 파일 보존 확인
            with open(os.path.join(tmpdir, "subfolder", "test.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"SOURCE_DATA")

    def test_unclassify_all_files_collision_protection(self) -> None:
        """
        - 원복 시 루트 경로에 동일 파일이 존재할 경우 덮어쓰지 않고 하위 폴더에 보존함을 검증.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            sub = os.path.join(tmpdir, "_1_sub")
            os.makedirs(sub, exist_ok=True)

            sub_file = os.path.join(sub, "collision.mp4")
            with open(sub_file, "wb") as f:
                f.write(b"SUB_DATA")

            root_file = os.path.join(tmpdir, "collision.mp4")
            with open(root_file, "wb") as f:
                f.write(b"ROOT_DATA")

            moved_cnt, warnings = unclassify_all_files(tmpdir)
            self.assertEqual(moved_cnt, 0)
            self.assertEqual(len(warnings), 1)

            # 루트 파일 내용 보존 확인
            with open(root_file, "rb") as f:
                self.assertEqual(f.read(), b"ROOT_DATA")
            # 하위 폴더 파일도 유지 확인
            self.assertTrue(os.path.isfile(sub_file))

    def test_unclassify_all_files_pseudo_mode(self) -> None:
        """
        - unmark_pseudo_classified_only=True 옵션 시 디스크 파일 이동 없이 메모리 마크만 초기화됨을 검증.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "pseudo.mp4")
            with open(fpath, "wb") as f:
                f.write(b"pseudo")

            vid = VideoProps("pseudo.mp4", 1280, 720, 0, 30.0, 1500, 128, 10.0, 5.0, "h264", moved_dirname="_target")
            moved_cnt, warnings = unclassify_all_files(tmpdir, cached_videos=[vid], unmark_pseudo_classified_only=True)

            self.assertEqual(moved_cnt, 0)
            self.assertIsNone(vid.moved_dirname)
            self.assertTrue(os.path.isfile(fpath))

    def test_compute_dir_signature_consistency(self) -> None:
        """
        - 디렉터리 내 파일 수정 또는 추가 시 시그니처가 정확히 변경되는지 검증.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            sig1 = compute_dir_signature(tmpdir)
            self.assertEqual(sig1, ())

            fpath = os.path.join(tmpdir, "vid.mp4")
            with open(fpath, "wb") as f:
                f.write(b"0123456789")

            sig2 = compute_dir_signature(tmpdir)
            self.assertEqual(len(sig2), 1)
            self.assertEqual(sig2[0][0], "vid.mp4")
            self.assertEqual(sig2[0][1], 10)

            # 파일 추가 시 변경
            fpath2 = os.path.join(tmpdir, "vid2.mp4")
            with open(fpath2, "wb") as f:
                f.write(b"9876543210")
            sig3 = compute_dir_signature(tmpdir)
            self.assertNotEqual(sig2, sig3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
