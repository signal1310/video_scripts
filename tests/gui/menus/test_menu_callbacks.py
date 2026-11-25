"""메뉴 콜백 묶음의 인코딩 콜백 필드가 기본으로 비어 있고 대입으로 연결되는지 검증"""

import unittest

from tests._gui_helpers import make_menu_callbacks


class EncodeCallbackFieldTests(unittest.TestCase):
    """인코딩 콜백 필드의 기본값과 연결 가능성을 검증"""

    def test_encode_fields_default_to_none(self) -> None:
        """인코딩 콜백 세 필드가 기본으로 None이라 기존 호출자를 깨지 않는지 검증"""
        callbacks = make_menu_callbacks()
        self.assertIsNone(callbacks.on_start_encode)
        self.assertIsNone(callbacks.on_open_encode_settings)
        self.assertIsNone(callbacks.get_encode_state)

    def test_encode_fields_can_be_assigned_after_creation(self) -> None:
        """생성 뒤에 인코딩 콜백을 대입할 수 있는지 검증"""
        callbacks = make_menu_callbacks()
        seen: list[str] = []
        callbacks.on_start_encode = lambda: seen.append("start")
        callbacks.on_start_encode()
        self.assertEqual(seen, ["start"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
