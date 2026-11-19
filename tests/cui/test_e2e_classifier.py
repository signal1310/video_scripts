import os
import subprocess
import tempfile
import unittest

from src import (
    VideoClassifier,
)


def _create_real_video(
    filepath: str,
    width: int = 640,
    height: int = 360,
    duration: float = 3.0,
    fps: int = 30,
    codec: str = "libx264",
    bitrate_k: int = 1000,
    keyint: int = 30,
    rotate: int = 0,
    add_noise: bool = False,
) -> None:
    """ffmpeg CLI를 실행하여 테스트용 가상 비디오 파일을 생성"""
    vf_args = ["-vf", "noise=alls=60:allf=t"] if add_noise else []
    target_out = filepath if rotate == 0 else filepath + ".norot.mp4"

    cmd = [
        "ffmpeg",
        "-y",
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=duration={duration}:size={width}x{height}:rate={fps}",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=440:duration={duration}",
        *vf_args,
        "-c:v",
        codec,
        "-b:v",
        f"{bitrate_k}k",
        "-minrate",
        f"{bitrate_k}k",
        "-maxrate",
        f"{bitrate_k}k",
        "-bufsize",
        f"{bitrate_k * 2}k",
        "-g",
        str(keyint),
        "-keyint_min",
        str(keyint),
        "-sc_threshold",
        "0",
        "-c:a",
        "aac",
        "-b:a",
        "64k",
        target_out,
    ]
    subprocess.run(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=True,
    )
    if rotate != 0:
        rot_cmd = [
            "ffmpeg",
            "-y",
            "-display_rotation:v:0",
            str(rotate),
            "-i",
            target_out,
            "-c",
            "copy",
            filepath,
        ]
        subprocess.run(
            rot_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        if os.path.exists(target_out):
            os.remove(target_out)


class TestEndToEndRealFFmpeg(unittest.TestCase):
    """실제 ffmpeg CLI를 실행하여 생성된 파일들로 프로빙, 가분류, 테이블 출력, 실제 이동, 충돌 보호, 원복을 총괄 검증"""

    def test_real_ffmpeg_incremental_probing_and_cache_reload(self) -> None:
        """증분 키프레임 분석 시 기존 키프레임 값 유지 및 디렉터리 파일 변경 감지 캐시 자동 갱신을 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            v1_path = os.path.join(tmpdir, "v1.mp4")
            _create_real_video(v1_path, width=640, height=360, duration=3.0, fps=30, bitrate_k=500, keyint=30)

            vc = VideoClassifier(root_dir=tmpdir, include_keyframe_interval=False)
            self.assertEqual(len(vc.video_props), 1)
            self.assertIsNone(vc.video_props[0].keyframe_interval)

            vc.include_keyframe_interval(True)
            self.assertIsNotNone(vc.video_props[0].keyframe_interval)
            first_kf = vc.video_props[0].keyframe_interval

            vc.include_keyframe_interval(True)
            self.assertEqual(vc.video_props[0].keyframe_interval, first_kf)

            v2_path = os.path.join(tmpdir, "v2.mp4")
            _create_real_video(v2_path, width=640, height=360, duration=2.5, fps=30, bitrate_k=500, keyint=30)
            vc_reloaded = VideoClassifier(root_dir=tmpdir, include_keyframe_interval=True)
            self.assertEqual(len(vc_reloaded.video_props), 2)

if __name__ == "__main__":
    unittest.main(verbosity=2)
