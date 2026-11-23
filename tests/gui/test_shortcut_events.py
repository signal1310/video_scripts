"""단축키 변경 허브 검증: 싱글톤 인스턴스와 유효 단축키 사본 전달"""

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication

from src.gui.shortcut_events import ShortcutEvents, shortcut_events

_APP = QApplication.instance() or QApplication([])


class ShortcutEventsTests(unittest.TestCase):
    """앱 공용 신호 허브"""

    def test_hub_is_a_singleton(self) -> None:
        self.assertIs(shortcut_events(), shortcut_events())
        self.assertIsInstance(shortcut_events(), ShortcutEvents)

    def test_publish_sends_a_copy_of_the_shortcuts(self) -> None:
        received: list[object] = []
        hub = shortcut_events()
        hub.changed.connect(received.append)
        source = {"preview.toggle_playback": "K"}
        hub.publish(source)
        source["preview.toggle_playback"] = "X"
        self.assertEqual(received, [{"preview.toggle_playback": "K"}])


if __name__ == "__main__":
    unittest.main(verbosity=2)
