"""재인코딩 작업 입력(encode_job)의 설정 사본 왕복과 작업 직렬화 단위 테스트"""

import tempfile
import unittest

from src.core.config import ClassifierConfig
from src.core.encode.encode_gate import GatePurpose
from src.core.encode_tag import ENCODE_TAG_MODE_HIDDEN
from src.service.encode.encode_history import EncodeHistory
from src.service.encode.encode_job import (
    EncodeJob,
    EncodeSettings,
    create_batch,
    job_from_json,
    job_to_json,
)


class TestEncodeSettingsSnapshot(unittest.TestCase):
    """EncodeSettings의 from_config, to_json, from_json 왕복과 기본값 복원을 검증"""

    def test_from_config_and_json_round_trip_preserves_tag_settings(self) -> None:
        config = ClassifierConfig()
        config.execution.encode_tag_mode = ENCODE_TAG_MODE_HIDDEN
        config.execution.encode_tag_name = "custom_tag"
        settings = EncodeSettings.from_config(config)
        restored = EncodeSettings.from_json(settings.to_json())
        self.assertEqual(restored, settings)
        self.assertEqual(restored.encode_tag_mode, ENCODE_TAG_MODE_HIDDEN)
        self.assertEqual(restored.encode_tag_name, "custom_tag")

    def test_from_json_falls_back_when_tag_keys_are_absent(self) -> None:
        """이전 배치 JSON에 태그 키가 없어도 기본값으로 복원됨을 검증"""
        raw = EncodeSettings.from_config(ClassifierConfig()).to_json()
        import json

        payload = json.loads(raw)
        del payload["encode_tag_mode"]
        del payload["encode_tag_name"]
        restored = EncodeSettings.from_json(json.dumps(payload))
        self.assertEqual(restored.encode_tag_mode, "visible")
        self.assertEqual(restored.encode_tag_name, "re_encoded")


class TestEncodeJobSerialization(unittest.TestCase):
    """EncodeJob 직렬화와 배치 생성을 검증"""

    def test_job_json_round_trip_and_create_batch(self) -> None:
        job = EncodeJob(
            filename="clip.mp4",
            root_dir="/root",
            source_path="/root/clip.mp4",
            source_size=1234,
            source_mtime_ns=5678,
            source_duration_sec=12.5,
            source_ext="mp4",
            fps=30.0,
            quality=22.0,
            purpose=GatePurpose.SAVING,
            output_size=(1280, 720),
            scale_filter="scale=1280:720",
            maxrate_kbps=4000,
        )
        self.assertEqual(job_from_json(job_to_json(job)), job)
        with tempfile.TemporaryDirectory() as tmp:
            history = EncodeHistory(f"{tmp}/h.sqlite3", sleep=lambda _s: None)
            try:
                batch_id = create_batch(history, EncodeSettings.from_config(ClassifierConfig()), "/root", [job])
                self.assertEqual(len(history.list_items(batch_id)), 1)
            finally:
                history.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
