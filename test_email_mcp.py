"""Tests for the read-only IMAP MCP message parser and configuration."""

from __future__ import annotations

import os
from unittest import mock
import unittest

from email_mcp_server import EmailConfig, EmailToolError, parse_email_message


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


if __name__ == "__main__":
    unittest.main()
