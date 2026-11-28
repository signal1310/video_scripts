"""영구 인덱스를 파일명, 그룹 이름 기준으로 새 행에 이어 주는 도우미 검증"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QPersistentModelIndex,
    pyqtSignal,
)
from PyQt6.QtWidgets import QApplication

from src.gui.table.table_relayout import LayoutRemapController, remap_persistent_rows

_APP = QApplication.instance() or QApplication([])
_EMPTY_INDEX = QModelIndex()


class _KeyModel(QAbstractTableModel):
    """표시 행마다 이어 주기 기준 키만 들고 있는 최소 모델"""

    def __init__(self, keys: list[str], labels: dict[int, str] | None = None) -> None:
        super().__init__()
        self.keys = list(keys)
        self.labels = dict(labels or {})

    def rowCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        return 0 if parent.isValid() else len(self.keys)

    def columnCount(self, parent: QModelIndex = _EMPTY_INDEX) -> int:
        return 0 if parent.isValid() else 2

    def data(self, index: QModelIndex, role: int = 0) -> object:
        _ = (index, role)
        return None

    def display_row_keys(self) -> tuple[list[str], dict[int, str]]:
        """이어 주기 기준 키와 머리글 행의 그룹 이름"""
        return list(self.keys), dict(self.labels)

    def reorder(self, keys: list[str], labels: dict[int, str] | None = None) -> None:
        """재구성된 표시 행 구성을 반영"""
        self.keys = list(keys)
        self.labels = dict(labels or {})


class RemapTests(unittest.TestCase):
    """영구 인덱스 이어 주기 검증"""

    def setUp(self) -> None:
        self._keepalive: list[QPersistentModelIndex] = []

    def _persistent(self, model: _KeyModel, row: int, column: int = 0) -> QPersistentModelIndex:
        """시험 동안 살아 있는 영구 인덱스를 만들어 반환"""
        persistent = QPersistentModelIndex(model.index(row, column))
        self._keepalive.append(persistent)
        return persistent

    def test_persistent_index_follows_filename(self) -> None:
        """순서가 바뀌어도 영구 인덱스가 같은 파일명 행을 따라가는지 검증"""
        model = _KeyModel(["a.mp4", "b.mp4", "c.mp4"])
        persistent = self._persistent(model, 0)
        model.reorder(["c.mp4", "a.mp4", "b.mp4"])
        remap_persistent_rows(model, ["a.mp4", "b.mp4", "c.mp4"], {})
        self.assertTrue(persistent.isValid())
        self.assertEqual(persistent.row(), 1)

    def test_removed_row_maps_to_invalid(self) -> None:
        """사라진 행의 영구 인덱스는 무효가 되는지 검증"""
        model = _KeyModel(["a.mp4", "b.mp4", "c.mp4"])
        persistent = self._persistent(model, 0)
        model.reorder(["c.mp4"])
        remap_persistent_rows(model, ["a.mp4", "b.mp4", "c.mp4"], {})
        self.assertFalse(persistent.isValid())

    def test_header_row_follows_label(self) -> None:
        """머리글 행은 그룹 이름으로 새 위치를 찾는지 검증"""
        model = _KeyModel(["", "a.mp4"], {0: "그룹"})
        persistent = self._persistent(model, 0)
        model.reorder(["a.mp4", ""], {1: "그룹"})
        remap_persistent_rows(model, ["", "a.mp4"], {0: "그룹"})
        self.assertTrue(persistent.isValid())
        self.assertEqual(persistent.row(), 1)

    def test_header_row_without_label_maps_to_invalid(self) -> None:
        """머리글 행의 그룹 이름을 기억하지 못했으면 무효가 되는지 검증"""
        model = _KeyModel(["", "a.mp4"])
        persistent = self._persistent(model, 0)
        model.reorder(["a.mp4", ""])
        remap_persistent_rows(model, ["", "a.mp4"], {})
        self.assertFalse(persistent.isValid())

    def test_column_is_preserved(self) -> None:
        """이어 준 인덱스가 원래 열을 그대로 쓰는지 검증"""
        model = _KeyModel(["a.mp4", "b.mp4"])
        persistent = self._persistent(model, 0, 1)
        model.reorder(["b.mp4", "a.mp4"])
        remap_persistent_rows(model, ["a.mp4", "b.mp4"], {})
        self.assertEqual(persistent.column(), 1)

    def test_row_beyond_old_keys_maps_to_invalid(self) -> None:
        """이전 기준 목록에 없는 행은 무효가 되는지 검증"""
        model = _KeyModel(["a.mp4", "b.mp4"])
        persistent = self._persistent(model, 1)
        model.reorder(["a.mp4", "b.mp4"])
        remap_persistent_rows(model, ["a.mp4"], {})
        self.assertFalse(persistent.isValid())


class _GroupLikeModel(_KeyModel):
    """그룹 프록시처럼 레이아웃 신호와 재구성 알림을 갖는 모델"""

    groups_rebuilt = pyqtSignal()

    def __init__(self, keys: list[str]) -> None:
        super().__init__(keys)
        self.events: list[str] = []
        self.layoutAboutToBeChanged.connect(lambda: self.events.append("about"))
        self.layoutChanged.connect(lambda: self.events.append("changed"))

    def build_rows(self) -> None:
        """재구성 결과를 반영"""
        self.events.append("build")

    def rebuild_rows(self) -> None:
        """초기화 재구성 표시"""
        self.events.append("rebuild")


class LayoutRemapControllerTests(unittest.TestCase):
    """묶음 안팎의 레이아웃 변경, 초기화 선택 검증"""

    def setUp(self) -> None:
        self.model = _GroupLikeModel(["a.mp4", "b.mp4"])
        self._keepalive: list[QPersistentModelIndex] = []
        self.controller = LayoutRemapController(
            self.model, self.model.build_rows, self.model.rebuild_rows
        )

    def test_remember_captures_current_keys(self) -> None:
        self.controller.remember()
        self.model.reorder(["b.mp4", "a.mp4"])
        self.controller.apply()
        self.assertEqual(self.model.events, ["about", "build", "changed"])

    def test_defer_outside_batch_returns_false(self) -> None:
        self.assertFalse(self.controller.defer_layout())
        self.assertFalse(self.controller.defer_structure())
        self.assertEqual(self.model.events, [])

    def test_layout_only_batch_rebuilds_without_rebuild(self) -> None:
        """묶음이 순서만 바꿨으면 초기화 없이 레이아웃 변경으로 끝나는지 검증"""
        self.controller.start_batch()
        self.assertTrue(self.controller.defer_layout())
        self.model.reorder(["b.mp4", "a.mp4"])
        self.controller.finish_batch()
        self.assertEqual(self.model.events, ["about", "build", "changed"])

    def test_remember_inside_batch_keeps_batch_start_keys(self) -> None:
        """묶음 안의 재정렬 전 기억 요청이 묶음 시작 기준을 덮어쓰지 않는지 검증"""
        self.controller.start_batch()
        persistent = self._persistent_row(0)
        self.model.reorder(["b.mp4", "a.mp4"])
        self.controller.remember()
        self.controller.defer_layout()
        self.controller.finish_batch()
        # - 묶음 시작 기준(a.mp4=0)을 썼으므로 a.mp4 인덱스가 새 위치 1로 이동
        self.assertEqual(persistent.row(), 1)

    def _persistent_row(self, row: int) -> QPersistentModelIndex:
        """시험 동안 살아 있는 영구 인덱스를 만들어 반환"""
        persistent = QPersistentModelIndex(self.model.index(row, 0))
        self._keepalive.append(persistent)
        return persistent

    def test_structure_batch_rebuilds_with_reset(self) -> None:
        """묶음이 행 집합까지 바꿨으면 초기화 재구성으로 끝나는지 검증"""
        self.controller.start_batch()
        self.controller.defer_structure()
        self.controller.finish_batch()
        self.assertEqual(self.model.events, ["rebuild"])

    def test_structure_takes_precedence_over_layout(self) -> None:
        self.controller.start_batch()
        self.controller.defer_layout()
        self.controller.defer_structure()
        self.controller.finish_batch()
        self.assertEqual(self.model.events, ["rebuild"])

    def test_nested_batch_finishes_once(self) -> None:
        """겹친 묶음은 가장 바깥 묶음이 끝날 때 한 번만 재구성하는지 검증"""
        self.controller.start_batch()
        self.controller.start_batch()
        self.controller.defer_layout()
        self.controller.finish_batch()
        self.assertEqual(self.model.events, [])
        self.controller.finish_batch()
        self.assertEqual(self.model.events, ["about", "build", "changed"])

    def test_finish_without_batch_does_nothing(self) -> None:
        self.controller.finish_batch()
        self.assertEqual(self.model.events, [])

    def test_finish_without_pending_does_nothing(self) -> None:
        self.controller.start_batch()
        self.controller.finish_batch()
        self.assertEqual(self.model.events, [])

    def test_reset_drops_pending_state(self) -> None:
        """묶음 도중 연결이 끊기면 모아 둔 변경을 버리는지 검증"""
        self.controller.start_batch()
        self.controller.defer_layout()
        self.controller.reset()
        self.assertFalse(self.controller.defer_layout())
        self.controller.finish_batch()
        self.assertEqual(self.model.events, [])


if __name__ == "__main__":
    unittest.main()
