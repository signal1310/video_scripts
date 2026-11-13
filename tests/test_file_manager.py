import os
import tempfile
import unittest

from src.core.file_manager import (
    classify_files_batch,
    compute_dir_signature,
    get_dirnames,
    get_video_filenames,
    is_video_file,
    move_file,
    resolve_video_filepath,
    unclassify_all_files,
)
from src.core.models import VideoProps


class TestFileManagerModule(unittest.TestCase):
    """파일 확장자 감지, 폴더명 오인 방지, 파일 이동 및 충돌 보호 원복의 무결성을 검증"""

    def test_is_video_file_extensions(self) -> None:
        """대소문자 혼합 비디오 확장자 인식 및 비비디오 포맷 배제를 검증"""
        for ext in (".mp4", ".MKV", ".Avi", ".webm", ".flv", ".ts", ".mov", ".wmv", ".m2ts"):
            self.assertTrue(is_video_file(f"clip{ext}"))

        for non_ext in (".srt", ".txt", ".jpg", ".ini", ".png", ".nfo", ""):
            self.assertFalse(is_video_file(f"file{non_ext}"))

    def test_get_video_filenames_excludes_directories_and_non_videos(self) -> None:
        """디렉터리 이름이 'folder.mp4'여도 비디오 파일로 취급하지 않음을 검증"""
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
        """비디오가 루트 경로에 있거나 하위 이동 폴더에 위치한 경우 정확한 물리 경로를 찾아내는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "vid.mp4")
            with open(fpath, "wb") as f:
                f.write(b"video")

            vid = VideoProps("vid.mp4", 1280, 720, 0, 30.0, 1500, 128, 10.0, 5.0, "h264")
            resolved = resolve_video_filepath(tmpdir, vid)
            self.assertEqual(os.path.abspath(resolved), os.path.abspath(fpath))

            sub_dir = os.path.join(tmpdir, "_1_표준인코딩_CQ22")
            os.makedirs(sub_dir, exist_ok=True)
            sub_fpath = os.path.join(sub_dir, "moved_vid.mp4")
            with open(sub_fpath, "wb") as f:
                f.write(b"moved-video")

            vid_moved = VideoProps(
                "moved_vid.mp4",
                1280,
                720,
                0,
                30.0,
                3000,
                128,
                20.0,
                5.0,
                "h264",
                moved_dirname="_1_표준인코딩_CQ22",
            )
            resolved_moved = resolve_video_filepath(tmpdir, vid_moved)
            self.assertEqual(os.path.abspath(resolved_moved), os.path.abspath(sub_fpath))

    def test_move_file_atomicity_and_collision(self) -> None:
        """대상 디렉터리가 없을 때 자동 생성 및 목적지에 동일 파일 존재 시 덮어쓰지 않고 False를 반환함을 검증"""
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
        """원복 시 루트 경로에 동일 파일이 존재할 경우 덮어쓰지 않고 하위 폴더에 보존함을 검증"""
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

    def test_unclassify_all_files_restores_clean_files_and_removes_empty_dirs(self) -> None:
        """unclassify_all_files가 분류 하위 폴더의 영상을 루트로 복구하고 빈 폴더를 삭제하며, 루트 충돌 파일은 덮어쓰지 않고 경고하는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            sub1 = os.path.join(tmpdir, "_1_표준인코딩_CQ22")
            sub2 = os.path.join(tmpdir, "_2_저화질_키프레임교정")
            os.makedirs(sub1, exist_ok=True)
            os.makedirs(sub2, exist_ok=True)

            with open(os.path.join(sub1, "clean1.mp4"), "wb") as f:
                f.write(b"clean1")
            with open(os.path.join(sub2, "clean2.mp4"), "wb") as f:
                f.write(b"clean2")
            with open(os.path.join(tmpdir, "dup.mp4"), "wb") as f:
                f.write(b"root-keep")
            with open(os.path.join(sub1, "dup.mp4"), "wb") as f:
                f.write(b"moved-skip")

            cached: list[VideoProps] = [
                VideoProps(
                    "clean1.mp4",
                    1280,
                    720,
                    0,
                    30.0,
                    3000,
                    128,
                    120.0,
                    10.0,
                    "h264",
                    moved_dirname="_1_표준인코딩_CQ22",
                ),
                VideoProps(
                    "clean2.mp4",
                    1280,
                    720,
                    0,
                    30.0,
                    800,
                    128,
                    80.0,
                    10.0,
                    "h264",
                    5.0,
                    moved_dirname="_2_저화질_키프레임교정",
                ),
                VideoProps(
                    "dup.mp4",
                    1280,
                    720,
                    0,
                    30.0,
                    3000,
                    128,
                    100.0,
                    10.0,
                    "h264",
                    moved_dirname="_1_표준인코딩_CQ22",
                ),
            ]

            moved_cnt, warnings = unclassify_all_files(
                tmpdir, cached_videos=cached, unmark_pseudo_classified_only=False
            )
            self.assertEqual(moved_cnt, 2)
            self.assertGreaterEqual(len(warnings), 1)
            for w in warnings:
                self.assertFalse(w.startswith(("[WARN]", "[ERROR]")))
                self.assertIn("작업 폴더", w)
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "clean1.mp4")))
            self.assertTrue(os.path.isfile(os.path.join(tmpdir, "clean2.mp4")))
            self.assertFalse(os.path.isdir(sub2))
            self.assertTrue(os.path.isdir(sub1))

            with open(os.path.join(tmpdir, "dup.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"root-keep")
            with open(os.path.join(sub1, "dup.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"moved-skip")

            self.assertIsNone(cached[0].moved_dirname)
            self.assertIsNone(cached[1].moved_dirname)

    def test_unclassify_all_files_pseudo_mode(self) -> None:
        """unmark_pseudo_classified_only=True 옵션 시 디스크 파일 이동 없이 메모리 마크만 초기화됨을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            fpath = os.path.join(tmpdir, "pseudo.mp4")
            with open(fpath, "wb") as f:
                f.write(b"pseudo")

            vid = VideoProps("pseudo.mp4", 1280, 720, 0, 30.0, 1500, 128, 10.0, 5.0, "h264", moved_dirname="_target")
            moved_cnt, _warnings = unclassify_all_files(tmpdir, cached_videos=[vid], unmark_pseudo_classified_only=True)

            self.assertEqual(moved_cnt, 0)
            self.assertIsNone(vid.moved_dirname)
            self.assertTrue(os.path.isfile(fpath))

    def test_compute_dir_signature_consistency(self) -> None:
        """디렉터리 내 파일 수정 또는 추가 시 시그니처가 정확히 변경되는지 검증"""
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

    def test_classify_files_batch_collision_skip_and_rollback(self) -> None:
        """배치 파일 이동 시 기존 대상 파일 충돌은 덮어쓰지 않고 건너뛰며, I/O 오류 발생 시 이전 이동을 역순 원복함을 검증"""
        import shutil
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmpdir:
            sub = os.path.join(tmpdir, "_1_target")
            os.makedirs(sub, exist_ok=True)
            # 이미 대상 경로에 존재하는 충돌 파일
            with open(os.path.join(sub, "dup.mp4"), "wb") as f:
                f.write(b"KEEP_TARGET")
            with open(os.path.join(tmpdir, "dup.mp4"), "wb") as f:
                f.write(b"SRC_DUP")
            with open(os.path.join(tmpdir, "ok.mp4"), "wb") as f:
                f.write(b"SRC_OK")

            vid_dup = VideoProps("dup.mp4", 1280, 720, 0, 30.0, 3000, 128, 20.0, 10.0, "h264", 1.0)
            vid_ok = VideoProps("ok.mp4", 1280, 720, 0, 30.0, 3000, 128, 20.0, 10.0, "h264", 1.0)

            moved, warnings = classify_files_batch(
                tmpdir, [(vid_dup, "_1_target"), (vid_ok, "_1_target")]
            )
            self.assertEqual(moved, 1)
            self.assertEqual(len(warnings), 1)
            self.assertIsNone(vid_dup.moved_dirname)
            self.assertEqual(vid_ok.moved_dirname, "_1_target")
            with open(os.path.join(sub, "dup.mp4"), "rb") as f:
                self.assertEqual(f.read(), b"KEEP_TARGET")

        with tempfile.TemporaryDirectory() as tmpdir2:
            f1 = os.path.join(tmpdir2, "a.mp4")
            f2 = os.path.join(tmpdir2, "b.mp4")
            with open(f1, "wb") as f:
                f.write(b"A")
            with open(f2, "wb") as f:
                f.write(b"B")

            va = VideoProps("a.mp4", 1280, 720, 0, 30.0, 3000, 128, 20.0, 10.0, "h264", 1.0)
            vb = VideoProps("b.mp4", 1280, 720, 0, 30.0, 3000, 128, 20.0, 10.0, "h264", 1.0)

            orig_move = shutil.move
            step = 0

            def fail_on_second(src: str, dst: str) -> str:
                nonlocal step
                step += 1
                if step == 2:
                    raise OSError("disk full")
                return orig_move(src, dst)

            with patch("src.core.file_manager.shutil.move", side_effect=fail_on_second), self.assertRaises(RuntimeError):
                classify_files_batch(tmpdir2, [(va, "_2_new_dir"), (vb, "_2_new_dir")])

            self.assertTrue(os.path.isfile(f1))
            self.assertTrue(os.path.isfile(f2))
            self.assertIsNone(va.moved_dirname)
            self.assertIsNone(vb.moved_dirname)
            self.assertFalse(os.path.exists(os.path.join(tmpdir2, "_2_new_dir")))


if __name__ == "__main__":
    unittest.main(verbosity=2)
