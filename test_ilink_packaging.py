"""Regression tests for the lightweight iLink frozen executable wiring."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys
import unittest


PROJECT_DIR = Path(__file__).resolve().parent
ILINK_DIR = PROJECT_DIR / "ilink_catgirl"
if str(ILINK_DIR) not in sys.path:
    sys.path.insert(0, str(ILINK_DIR))

import main as ilink_main
import bot
import weather_mcp_client
import web_mcp_client


class ILinkPackagingTests(unittest.TestCase):
    def test_numbered_message_file_is_loaded_without_list_number(self) -> None:
        with mock.patch.object(
            bot,
            "resource_path",
            return_value=ILINK_DIR / "主动问候语.txt",
        ):
            messages = bot.load_checkin_messages()
        self.assertGreaterEqual(len(messages), 1)
        self.assertFalse(any(message.startswith("1. ") for message in messages))

    def test_message_resources_match_current_persona(self) -> None:
        for filename in ("主动问候语.txt", "定时提醒开场白.txt"):
            content = (ILINK_DIR / filename).read_text(encoding="utf-8")
            self.assertIn("安洁", content)
            self.assertNotIn("爱丽丝", content)

    def test_internal_weather_server_is_dispatched(self) -> None:
        runner = mock.Mock()
        fake_module = SimpleNamespace(run_server=runner)
        with mock.patch.object(sys, "argv", ["catgirl.exe", "--weather-mcp-server"]):
            with mock.patch.dict(sys.modules, {"weather_mcp_server": fake_module}):
                self.assertTrue(ilink_main.run_internal_server())
        runner.assert_called_once_with()

    def test_internal_web_server_is_dispatched(self) -> None:
        runner = mock.Mock()
        fake_module = SimpleNamespace(run_server=runner)
        with mock.patch.object(sys, "argv", ["catgirl.exe", "--web-mcp-server"]):
            with mock.patch.dict(sys.modules, {"web_mcp_server": fake_module}):
                self.assertTrue(ilink_main.run_internal_server())
        runner.assert_called_once_with()

    def test_frozen_clients_restart_same_executable_as_mcp_server(self) -> None:
        executable = str(PROJECT_DIR / "Catgirl微信机器人.exe")
        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch.object(sys, "executable", executable):
                weather = weather_mcp_client.server_parameters()
                web = web_mcp_client.server_parameters()
        self.assertEqual(weather.command, executable)
        self.assertEqual(weather.args, ["--weather-mcp-server"])
        self.assertEqual(web.command, executable)
        self.assertEqual(web.args, ["--web-mcp-server"])

    def test_lightweight_spec_does_not_include_abandoned_uia(self) -> None:
        spec = (ILINK_DIR / "ilink_catgirl.spec").read_text(encoding="utf-8")
        self.assertNotIn('"UIA"', spec)
        self.assertNotIn('"wxauto"', spec)
        self.assertIn('"主动问候语.txt"', spec)
        self.assertIn('"定时提醒开场白.txt"', spec)
        self.assertIn('"weather_mcp_server"', spec)
        self.assertIn('"web_mcp_server"', spec)


if __name__ == "__main__":
    unittest.main()
