"""Tests for the allowlisted generic stdio MCP manager."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys
import tempfile
import unittest


PROJECT_DIR = Path(__file__).resolve().parent
ILINK_DIR = PROJECT_DIR / "ilink_catgirl"
if str(ILINK_DIR) not in sys.path:
    sys.path.insert(0, str(ILINK_DIR))

from generic_mcp_manager import GenericMCPManager


class GenericMCPManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.config_path = Path(self.temp_dir.name) / "mcp_servers.json"
        self.chat_log = mock.Mock()

    def write_config(self, server: dict) -> None:
        self.config_path.write_text(
            json.dumps({"servers": {"demo server": server}}, ensure_ascii=False),
            encoding="utf-8",
        )

    def test_only_explicitly_allowed_tools_are_exposed_with_namespace(self) -> None:
        self.write_config(
            {
                "enabled": True,
                "command": "demo-server",
                "allowed_tools": ["search"],
            }
        )
        manager = GenericMCPManager(self.config_path, self.chat_log)
        discovered = [
            SimpleNamespace(
                name="search",
                description="Search data",
                inputSchema={"type": "object", "properties": {}},
            ),
            SimpleNamespace(
                name="delete_everything",
                description="Dangerous",
                inputSchema={"type": "object", "properties": {}},
            ),
        ]
        with mock.patch.object(
            manager,
            "_list_one",
            new=mock.AsyncMock(return_value=discovered),
        ):
            schemas = manager.list_tools()

        self.assertEqual(len(schemas), 1)
        self.assertEqual(schemas[0]["function"]["name"], "demo_server__search")
        self.assertNotIn("delete_everything", str(schemas))

    def test_secret_is_read_from_environment_name_not_literal_config(self) -> None:
        self.write_config(
            {
                "enabled": True,
                "command": "demo-server",
                "allowed_tools": ["search"],
                "env": {"SERVER_TOKEN": "TEST_MCP_TOKEN"},
            }
        )
        manager = GenericMCPManager(self.config_path, self.chat_log)
        with mock.patch.dict(os.environ, {"TEST_MCP_TOKEN": "secret-value"}):
            server = manager._load_config()[0]

        self.assertEqual(server.environment["SERVER_TOKEN"], "secret-value")
        self.assertNotIn("secret-value", self.config_path.read_text(encoding="utf-8"))

    def test_enabled_server_requires_nonempty_allowlist(self) -> None:
        self.write_config({"enabled": True, "command": "demo-server"})
        manager = GenericMCPManager(self.config_path, self.chat_log)

        self.assertEqual(manager._load_config(), [])
        self.chat_log.error.assert_called_once()

    def test_tool_result_is_capped(self) -> None:
        result = SimpleNamespace(
            content=[SimpleNamespace(text="x" * 600)],
            isError=False,
            structuredContent=None,
        )
        output = GenericMCPManager._format_result(result, "search", 500)
        self.assertLessEqual(len(output), 500)
        self.assertTrue(output.endswith("[结果已截断]"))


if __name__ == "__main__":
    unittest.main()
