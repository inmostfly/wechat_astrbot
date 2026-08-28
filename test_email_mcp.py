"""Tests for the read-only IMAP MCP message parser and configuration."""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from types import SimpleNamespace
from unittest import mock
import unittest


if importlib.util.find_spec("mcp") is None:
    mcp_module = types.ModuleType("mcp")
    mcp_client_module = types.ModuleType("mcp.client")
    mcp_stdio_module = types.ModuleType("mcp.client.stdio")
    mcp_server_module = types.ModuleType("mcp.server")
    mcp_fastmcp_module = types.ModuleType("mcp.server.fastmcp")

    class _DummyServerParameters:
        def __init__(self, **values: object) -> None:
            self.__dict__.update(values)

    class _DummyFastMCP:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def tool(self):
            return lambda function: function

    mcp_module.ClientSession = object
    mcp_module.StdioServerParameters = _DummyServerParameters
    mcp_stdio_module.stdio_client = lambda *args, **kwargs: None
    mcp_fastmcp_module.FastMCP = _DummyFastMCP
    sys.modules.update(
        {
            "mcp": mcp_module,
            "mcp.client": mcp_client_module,
            "mcp.client.stdio": mcp_stdio_module,
            "mcp.server": mcp_server_module,
            "mcp.server.fastmcp": mcp_fastmcp_module,
        }
    )

import email_mcp_client
from email_mcp_server import (
    EmailConfig,
    EmailToolError,
    _mailbox_highest_uid,
    parse_email_message,
)


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self.value = value
        self.saw_exception = False

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> bool:
        self.saw_exception = exc_type is not None
        return False


class _FakeSession:
    def __init__(self, result: object) -> None:
        self.result = result

    async def initialize(self) -> None:
        return None

    async def call_tool(self, name: str, arguments: dict[str, object]) -> object:
        return self.result


class EmailMCPTests(unittest.TestCase):
    def test_mime_message_decodes_headers_html_and_attachment_names(self) -> None:
        raw = (
            b"From: =?UTF-8?B?5rWL6K+V5Lq6?= <sender@example.com>\r\n"
            b"To: owner@example.com\r\n"
            b"Subject: =?UTF-8?B?5paw6YKu5Lu2?=\r\n"
            b"Date: Fri, 28 Aug 2026 10:00:00 +0800\r\n"
            b"Message-ID: <message-1@example.com>\r\n"
            b"MIME-Version: 1.0\r\n"
            b"Content-Type: multipart/mixed; boundary=abc\r\n\r\n"
            b"--abc\r\nContent-Type: text/html; charset=utf-8\r\n\r\n"
            b"<p>Hello <b>mail</b></p><p>Second line</p>\r\n"
            b"--abc\r\nContent-Type: application/pdf\r\n"
            b"Content-Disposition: attachment; filename=report.pdf\r\n\r\nPDF\r\n"
            b"--abc--\r\n"
        )

        result = parse_email_message(42, raw, max_body_chars=1000)

        self.assertEqual(result["uid"], 42)
        self.assertEqual(result["subject"], "新邮件")
        self.assertIn("测试人", result["from"])
        self.assertIn("Hello mail", result["body"])
        self.assertIn("Second line", result["body"])
        self.assertEqual(result["attachments"], ["report.pdf"])

    def test_body_is_bounded(self) -> None:
        raw = b"Subject: long\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n" + b"x" * 500
        result = parse_email_message(1, raw, max_body_chars=120)
        self.assertEqual(len(result["body"]), 120)
        self.assertTrue(result["body_truncated"])

    def test_gmail_password_configuration_uses_app_password_field(self) -> None:
        environment = {
            "EMAIL_IMAP_HOST": "imap.gmail.com",
            "EMAIL_IMAP_PORT": "993",
            "EMAIL_IMAP_USERNAME": "owner@gmail.com",
            "EMAIL_IMAP_AUTH": "password",
            "EMAIL_IMAP_PASSWORD": "app-password",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            config = EmailConfig.from_environment()
        self.assertEqual(config.host, "imap.gmail.com")
        self.assertEqual(config.password, "app-password")

    def test_missing_secret_is_rejected_without_echoing_it(self) -> None:
        with mock.patch.dict(
            os.environ,
            {"EMAIL_IMAP_HOST": "imap.gmail.com", "EMAIL_IMAP_USERNAME": "owner"},
            clear=True,
        ):
            with self.assertRaises(EmailToolError) as context:
                EmailConfig.from_environment()
        self.assertIn("EMAIL_IMAP_PASSWORD", str(context.exception))

    def test_selected_mailbox_uidnext_is_preferred(self) -> None:
        connection = mock.Mock()
        connection.response.return_value = ("UIDNEXT", [b"73"])

        self.assertEqual(_mailbox_highest_uid(connection, "INBOX"), 72)
        connection.status.assert_not_called()

    def test_mailbox_status_is_used_when_selected_uidnext_is_missing(self) -> None:
        connection = mock.Mock()
        connection.response.return_value = (None, [None])
        connection.status.return_value = ("OK", [b'"INBOX" (UIDNEXT 19)'])

        self.assertEqual(_mailbox_highest_uid(connection, "INBOX"), 18)

    def test_tool_error_is_raised_after_async_contexts_close(self) -> None:
        result = SimpleNamespace(
            isError=True,
            content=[SimpleNamespace(text="连接邮箱失败：测试错误")],
        )
        transport = _AsyncContext((object(), object()))
        session_context = _AsyncContext(_FakeSession(result))

        with (
            mock.patch.object(email_mcp_client, "stdio_client", return_value=transport),
            mock.patch.object(
                email_mcp_client,
                "ClientSession",
                return_value=session_context,
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "连接邮箱失败：测试错误"):
                email_mcp_client.call_email_tool("mailbox_status", {})

        self.assertFalse(session_context.saw_exception)
        self.assertFalse(transport.saw_exception)

    def test_exception_group_detail_is_flattened_and_secret_is_redacted(self) -> None:
        grouped = ExceptionGroup(
            "outer",
            [RuntimeError("TLS 失败"), ExceptionGroup("inner", [OSError("连接重置")])],
        )
        self.assertEqual(
            email_mcp_client._exception_detail(grouped),
            "TLS 失败；连接重置",
        )
        with mock.patch.dict(os.environ, {"EMAIL_IMAP_PASSWORD": "example-secret"}):
            self.assertEqual(
                email_mcp_client._redact_secrets("登录失败 example-secret"),
                "登录失败 ***",
            )


if __name__ == "__main__":
    unittest.main()
