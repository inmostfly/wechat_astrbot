"""Offline tests for web extraction and SSRF defenses."""

from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import patch

from web_mcp_server import (
    WebToolError,
    ensure_public_ip,
    extract_html_text,
    normalize_url,
    validate_public_url,
)


class WebMcpTests(unittest.TestCase):
    def test_extracts_visible_text_and_ignores_scripts(self) -> None:
        title, content = extract_html_text(
            """
            <html><head><title> 测试 页面 </title><script>bad()</script></head>
            <body><nav>菜单</nav><main><h1>新闻标题</h1><p>第一段内容。</p></main></body>
            </html>
            """
        )
        self.assertEqual(title, "测试 页面")
        self.assertIn("新闻标题", content)
        self.assertIn("第一段内容。", content)
        self.assertNotIn("bad", content)
        self.assertNotIn("菜单", content)

    def test_rejects_non_http_scheme_and_credentials(self) -> None:
        with self.assertRaises(WebToolError):
            normalize_url("file:///etc/passwd")
        with self.assertRaises(WebToolError):
            normalize_url("https://user:pass@example.com/")

    def test_rejects_private_addresses_by_default(self) -> None:
        for address in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"):
            with self.subTest(address=address):
                with patch.dict(os.environ, {"WEB_ALLOW_PRIVATE_ADDRESS": "false"}):
                    with self.assertRaises(WebToolError):
                        ensure_public_ip(address)
        with self.assertRaises(WebToolError):
            asyncio.run(validate_public_url("http://localhost/admin"))

    def test_allows_private_and_clash_fake_ip_when_enabled(self) -> None:
        with patch.dict(os.environ, {"WEB_ALLOW_PRIVATE_ADDRESS": "true"}):
            ensure_public_ip("10.0.0.1")
            ensure_public_ip("192.168.1.10")
            ensure_public_ip("198.18.0.58")

    def test_still_rejects_local_and_metadata_addresses_when_enabled(self) -> None:
        blocked = (
            "127.0.0.1",
            "::1",
            "169.254.169.254",
            "100.100.100.200",
            "0.0.0.0",
            "224.0.0.1",
        )
        with patch.dict(os.environ, {"WEB_ALLOW_PRIVATE_ADDRESS": "true"}):
            for address in blocked:
                with self.subTest(address=address):
                    with self.assertRaises(WebToolError):
                        ensure_public_ip(address)

    def test_accepts_public_literal_address(self) -> None:
        normalized = asyncio.run(validate_public_url("https://1.1.1.1/path#fragment"))
        self.assertEqual(normalized, "https://1.1.1.1/path")


if __name__ == "__main__":
    unittest.main()
