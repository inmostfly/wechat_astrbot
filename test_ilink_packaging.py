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
import document_mcp_client
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

    def test_exe_guide_is_copied_next_to_output(self) -> None:
        guide = (ILINK_DIR / "EXE使用说明.txt").read_text(encoding="utf-8")
        build_script = (ILINK_DIR / "打包程序.bat").read_text(encoding="utf-8")
        self.assertIn("不能只拿走 Catgirl微信机器人.exe", guide)
        self.assertIn("_internal", guide)
        self.assertIn("用户自定义", guide)
        self.assertIn("退出并重新启动 EXE", guide)
        self.assertIn('copy /Y "EXE使用说明.txt"', build_script)
        self.assertIn('copy /Y "mcp_servers.example.json"', build_script)

    def test_editable_resources_are_preferred_and_packaged_separately(self) -> None:
        editable = bot.PROJECT_DIR / "用户自定义" / "聊天助手.txt"
        candidates = bot.resource_path.__doc__ or ""
        source = (ILINK_DIR / "bot.py").read_text(encoding="utf-8")
        build_script = (ILINK_DIR / "打包程序.bat").read_text(encoding="utf-8")
        self.assertIn("user-editable", candidates)
        self.assertIn('PROJECT_DIR / "用户自定义" / name', source)
        self.assertIn("用户自定义\\聊天助手.txt", build_script)
        self.assertEqual(editable.parent.name, "用户自定义")

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

    def test_internal_document_server_is_dispatched(self) -> None:
        runner = mock.Mock()
        fake_module = SimpleNamespace(run_server=runner)
        with mock.patch.object(sys, "argv", ["catgirl.exe", "--document-mcp-server"]):
            with mock.patch.dict(sys.modules, {"document_mcp_server": fake_module}):
                self.assertTrue(ilink_main.run_internal_server())
        runner.assert_called_once_with()

    def test_frozen_clients_restart_same_executable_as_mcp_server(self) -> None:
        executable = str(PROJECT_DIR / "Catgirl微信机器人.exe")
        with mock.patch.object(sys, "frozen", True, create=True):
            with mock.patch.object(sys, "executable", executable):
                weather = weather_mcp_client.server_parameters()
                web = web_mcp_client.server_parameters()
                document = document_mcp_client.server_parameters()
        self.assertEqual(weather.command, executable)
        self.assertEqual(weather.args, ["--weather-mcp-server"])
        self.assertEqual(web.command, executable)
        self.assertEqual(web.args, ["--web-mcp-server"])
        self.assertEqual(document.command, executable)
        self.assertEqual(document.args, ["--document-mcp-server"])

    def test_lightweight_spec_does_not_include_abandoned_uia(self) -> None:
        spec = (ILINK_DIR / "ilink_catgirl.spec").read_text(encoding="utf-8")
        self.assertNotIn('"UIA"', spec)
        self.assertNotIn('"wxauto"', spec)
        self.assertIn('"主动问候语.txt"', spec)
        self.assertIn('"定时提醒开场白.txt"', spec)
        self.assertIn('"weather_mcp_server"', spec)
        self.assertIn('"web_mcp_server"', spec)
        self.assertIn('"document_mcp_server"', spec)
        self.assertIn('"mcp_servers.example.json"', spec)

    def test_vision_model_and_crypto_dependency_are_packaged(self) -> None:
        environment = (ILINK_DIR / ".env.example").read_text(encoding="utf-8")
        requirements = (ILINK_DIR / "requirements.txt").read_text(encoding="utf-8")
        self.assertIn(
            "VISION_MODEL_2=deepseek-v4-flash-vision-exp",
            environment,
        )
        self.assertIn("cryptography", requirements)
        self.assertIn("pypdf", requirements)
        self.assertIn("python-docx", requirements)
        self.assertIn("openpyxl", requirements)
        self.assertIn("python-pptx", requirements)


if __name__ == "__main__":
    unittest.main()
