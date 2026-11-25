"""행 묶기 접힘 상태 저장 도우미의 저장, 복원, 초기화 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from src.core.config import ClassifierConfig
from src.core.settings.ui_state import GROUP_BY_COLUMN_PREFIX, GROUP_BY_FOLDER, UiState
from src.gui.table.group_collapse_state import CollapsedGroupStore
from src.gui.table.proxy_model import VideoSortFilterProxyModel
from src.gui.table.table_grouping import VideoGroupProxyModel
from src.gui.table.table_model import VideoTableModel
from tests._factories import make_eval, make_video

_ROOT_DIR: str = "C:/work"


def _make_group_model() -> VideoGroupProxyModel:
    """폴더와 코덱이 다른 표본으로 연결한 그룹 프록시"""
    model = VideoTableModel(config=ClassifierConfig())
    model.set_data_source(
        [
            make_eval(video=make_video(filename="a/one.mp4", codec="h264")),
            make_eval(video=make_video(filename="a/two.mp4", codec="hevc")),
            make_eval(video=make_video(filename="b/three.mp4", codec="h264")),
        ],
        root_dir=_ROOT_DIR,
    )
    proxy = VideoSortFilterProxyModel()
    proxy.setSourceModel(model)
    group = VideoGroupProxyModel()
    group.setSourceModel(proxy)
    return group


class CollapsedGroupStoreTests(unittest.TestCase):
    """접힘 저장소가 묶기 키별 이름을 주고받고 초기화에 저장값까지 비우는지 검증"""

    def setUp(self) -> None:
        self.group = _make_group_model()
        self.ui = UiState()
        self.store = CollapsedGroupStore(self.group, self.ui)

    def test_store_keeps_only_the_current_group_key(self) -> None:
        """지금 묶기 키의 이름만 기록하고 접힘이 없으면 그 키를 지우는지 검증"""
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.store.store()
        self.assertEqual(self.ui.filters.collapsed_groups, {})
        self.group.toggle_group(0)
        self.store.store()
        self.assertEqual(self.ui.filters.collapsed_groups, {GROUP_BY_FOLDER: ["a"]})
        self.group.expand_all_groups()
        self.store.store()
        self.assertEqual(self.ui.filters.collapsed_groups, {})

    def test_store_does_not_touch_other_group_keys(self) -> None:
        """다른 묶기 키에 남은 이름을 지우지 않고 이번 키만 다시 쓰는지 검증"""
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.ui.filters.collapsed_groups = {codec_key: ["hevc"]}
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.group.toggle_group(0)
        self.store.store()
        self.assertEqual(
            self.ui.filters.collapsed_groups,
            {codec_key: ["hevc"], GROUP_BY_FOLDER: ["a"]},
        )

    def test_restore_applies_saved_labels_for_the_current_key(self) -> None:
        """저장된 이름이 지금 묶기 키에만 적용되는지 검증"""
        codec_key = f"{GROUP_BY_COLUMN_PREFIX}{VideoTableModel.COL_CODEC}"
        self.ui.filters.collapsed_groups = {GROUP_BY_FOLDER: ["b"], codec_key: ["hevc"]}
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.store.restore()
        self.assertEqual(self.group.collapsed_labels, ["b"])
        self.group.set_group_by(codec_key)
        self.store.restore()
        self.assertEqual(self.group.collapsed_labels, ["hevc"])

    def test_reset_clears_model_and_state(self) -> None:
        """초기화가 표의 접힘과 저장된 이름을 함께 비우는지 검증"""
        self.group.set_group_by(GROUP_BY_FOLDER)
        self.group.toggle_group(0)
        self.store.store()
        self.store.reset()
        self.assertEqual(self.group.collapsed_labels, [])
        self.assertEqual(self.ui.filters.collapsed_groups, {})


if __name__ == "__main__":
    unittest.main()
