import glob
import json
import os
import unittest
import xml.etree.ElementTree as ET


class TestPresetsAndNotebooks(unittest.TestCase):
    """
    - ShanaEncoder XML 프리셋 파일들의 문법 유효성 및 Jupyter 노트북(.ipynb) 구조 무결성을 검증.
    """

    def test_shana_presets_well_formed_xml(self) -> None:
        """
        - shana_presets 디렉터리 내의 모든 .xml 프리셋 파일이 파싱 오류 없는 유효한 XML인지 검증.
        """
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

    def test_notebooks_valid_json_structure(self) -> None:
        """
        - 프로젝트 루트의 Jupyter 노트북 파일들이 깨지지 않고 올바른 JSON 구조를 유지하는지 검증.
        """
        root_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        notebook_files = glob.glob(os.path.join(root_dir, "*.ipynb"))

        for nb_path in notebook_files:
            with self.subTest(notebook=os.path.basename(nb_path)):
                with open(nb_path, "r", encoding="utf-8") as f:
                    nb_data = json.load(f)
                self.assertIn("cells", nb_data)
                self.assertIn("metadata", nb_data)
                self.assertIsInstance(nb_data["cells"], list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
