"""Offline regression tests for WeChat's native topmost toggle."""

from __future__ import annotations

import sys
from types import ModuleType
import unittest
from unittest.mock import patch


try:
    from wechat_uia import WeChat
except ModuleNotFoundError as error:
    # These tests exercise state transitions only, so CI can use lightweight
    # module placeholders when Windows UI packages are not installed.
    windows_modules = {
        "pywinauto",
        "win32api",
        "win32con",
        "win32gui",
        "win32process",
    }
    if error.name not in windows_modules:
        raise
    for module_name in windows_modules:
        sys.modules.setdefault(module_name, ModuleType(module_name))
    sys.modules["pywinauto"].Desktop = object
    from wechat_uia import WeChat


class FakeButton:
    def __init__(self, name: str) -> None:
        self.name = name
        self.clicks = 0

    def window_text(self) -> str:
        return self.name

    def click_input(self) -> None:
        self.clicks += 1
        self.name = "取消置顶" if self.name == "置顶" else "置顶"


class TopmostTests(unittest.TestCase):
    def make_wechat(self, button_name: str) -> tuple[WeChat, FakeButton]:
        wx = WeChat.__new__(WeChat)
        wx.timeout = 0.2
        button = FakeButton(button_name)
        wx._topmost_button = lambda: button
        return wx, button

    def test_enables_topmost_when_current_action_is_pin(self) -> None:
        wx, button = self.make_wechat("置顶")
        self.assertTrue(wx.SetAlwaysOnTop(True))
        self.assertEqual(button.name, "取消置顶")
        self.assertEqual(button.clicks, 1)

    def test_keeps_existing_topmost_state_without_clicking(self) -> None:
        wx, button = self.make_wechat("取消置顶")
        self.assertFalse(wx.SetAlwaysOnTop(True))
        self.assertEqual(button.clicks, 0)

    def test_close_does_not_cancel_topmost(self) -> None:
        wx, button = self.make_wechat("取消置顶")
        wx.Close()
        self.assertEqual(button.name, "取消置顶")
        self.assertEqual(button.clicks, 0)

    def test_constructor_can_skip_enabling_topmost(self) -> None:
        fake_window = type("FakeWindow", (), {"handle": 123})()
        with (
            patch.object(WeChat, "_connect_window", return_value=fake_window),
            patch.object(WeChat, "BringToFront") as bring_to_front,
            patch.object(WeChat, "SetAlwaysOnTop") as set_topmost,
        ):
            WeChat(always_on_top=False)
        bring_to_front.assert_called_once_with()
        set_topmost.assert_not_called()

    def test_constructor_enables_topmost_by_default(self) -> None:
        fake_window = type("FakeWindow", (), {"handle": 123})()
        with (
            patch.object(WeChat, "_connect_window", return_value=fake_window),
            patch.object(WeChat, "BringToFront"),
            patch.object(WeChat, "SetAlwaysOnTop") as set_topmost,
        ):
            WeChat()
        set_topmost.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
