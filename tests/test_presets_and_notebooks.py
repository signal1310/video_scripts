import glob
import json
import os
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest.mock import patch

from src.core.app_state import NOTEBOOK_FILENAME, ensure_default_notebook_file


class TestPresetsAndNotebooks(unittest.TestCase):
    """ShanaEncoder XML 프리셋 파일들의 문법 유효성 및 Jupyter 노트북(.ipynb) 구조 무결성을 검증"""

    def test_shana_presets_well_formed_xml(self) -> None:
        """shana_presets 디렉터리 내의 모든 .xml 프리셋 파일이 파싱 오류 없는 유효한 XML인지 검증"""
        preset_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "shana_presets")
        )
        if not os.path.isdir(preset_dir):
            self.skipTest("shana_presets 디렉터리가 존재하지 않음")

        preset_files = glob.glob(os.path.join(preset_dir, "*.xml"))
        self.assertGreater(len(preset_files), 0, "프리셋 XML 파일이 최소 1개 이상 존재해야 함")

        for xml_path in preset_files:
            with self.subTest(preset=os.path.basename(xml_path)):
                try:
                    tree = ET.parse(xml_path)
                    root = tree.getroot()
                    self.assertIsNotNone(root)
                except Exception as e:
                    self.fail(f"XML 파싱 실패: {os.path.basename(xml_path)} ({e})")

    def test_default_notebook_template_valid_json_and_api_contract(self) -> None:
        """자동 생성되는 classify.ipynb 템플릿이 유효한 nbformat JSON 및 공개 API 호출 계약을 유지하는지 검증"""
        with tempfile.TemporaryDirectory() as tmpdir:
            nb_path = os.path.join(tmpdir, NOTEBOOK_FILENAME)
            with patch("src.core.app_state.get_notebook_path", return_value=nb_path):
                created = ensure_default_notebook_file()
                self.assertEqual(created, nb_path)
                self.assertTrue(os.path.isfile(nb_path))
                with open(nb_path, encoding="utf-8") as f:
                    nb_data = json.load(f)

        self.assertEqual(nb_data.get("nbformat"), 4)
        self.assertIn("cells", nb_data)
        self.assertIn("metadata", nb_data)
        self.assertIsInstance(nb_data["cells"], list)
        self.assertGreaterEqual(len(nb_data["cells"]), 6)

        code_sources = "\n".join(
            "".join(cell.get("source", []))
            for cell in nb_data["cells"]
            if cell.get("cell_type") == "code"
        )
        for token in (
            "from src import *",
            "VideoClassifier()",
            "classifier.classify()",
            "classifier.print(",
            "classifier.unclassify_files()",
        ):
            self.assertIn(token, code_sources)

        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        for existing_nb in glob.glob(os.path.join(root_dir, "*.ipynb")):
            with self.subTest(notebook=os.path.basename(existing_nb)), open(
                existing_nb, encoding="utf-8"
            ) as f:
                existing_data = json.load(f)
                self.assertIn("cells", existing_data)
                self.assertIn("metadata", existing_data)
                self.assertIsInstance(existing_data["cells"], list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
