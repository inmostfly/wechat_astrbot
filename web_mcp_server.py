"""Local MCP server for safe webpage reading and pluggable web search."""

from __future__ import annotations

import asyncio
from html import unescape
from html.parser import HTMLParser
import ipaddress
import json
import os
import re
import socket
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from mcp.server.fastmcp import FastMCP

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parent
if load_dotenv is not None:
    load_dotenv(PROJECT_DIR / ".env", override=False)
    load_dotenv(PROJECT_DIR / "ilink_catgirl" / ".env", override=True)


mcp = FastMCP(
    "catgirl-web",
    instructions=(
        "搜索公开互联网或读取用户提供的公开网页。回答必须标明实际使用的来源网址；"
        "不要把搜索摘要当作未经核对的绝对事实。"
    ),
)

USER_AGENT = "CatgirlWebMCP/1.0 (+personal assistant; respectful fetcher)"
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024
MAX_CONTENT_CHARS = 20_000
MAX_REDIRECTS = 5
BLOCKED_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "metadata.google.internal",
}
BLOCKED_METADATA_IPS = {
    ipaddress.ip_address("169.254.169.254"),
    ipaddress.ip_address("100.100.100.200"),
}


class WebToolError(RuntimeError):
    """Raised for invalid configuration, unsafe URLs, and request failures."""


class TextExtractor(HTMLParser):
    """Small dependency-free HTML text extractor."""

    ignored_tags = {
        "script",
        "style",
        "noscript",
        "svg",
        "canvas",
        "form",
        "nav",
        "footer",
        "header",
    }
    block_tags = {
        "article",
        "aside",
        "blockquote",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "li",
        "main",
        "p",
        "pre",
        "section",
        "table",
        "td",
        "th",
        "tr",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self._in_title = False
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in self.ignored_tags:
            self._ignored_depth += 1
            return
        if self._ignored_depth:
            return
        if lowered == "title":
            self._in_title = True
        if lowered in self.block_tags:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self.ignored_tags and self._ignored_depth:
            self._ignored_depth -= 1
            return
        if self._ignored_depth:
            return
        if lowered == "title":
            self._in_title = False
        if lowered in self.block_tags:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        self.text_parts.append(data)


def compact_text(value: str) -> str:
    value = unescape(value).replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for raw_line in value.split("\n"):
        line = re.sub(r"[\t\f\v ]+", " ", raw_line).strip()
        if line and (not lines or line != lines[-1]):
            lines.append(line)
    return "\n".join(lines)


def extract_html_text(html: str) -> tuple[str, str]:
    parser = TextExtractor()
    parser.feed(html)
    parser.close()
    title = compact_text(" ".join(parser.title_parts))[:500]
    content = compact_text("".join(parser.text_parts))
    return title, content


def normalize_url(url: str) -> str:
    url = url.strip()
    if not url:
        raise WebToolError("网址不能为空")
    parts = urlsplit(url)
    if parts.scheme.lower() not in {"http", "https"}:
        raise WebToolError("只允许读取 http 或 https 网址")
    if parts.username or parts.password:
        raise WebToolError("网址不能包含用户名或密码")
    if not parts.hostname:
        raise WebToolError("网址缺少有效主机名")
    hostname = parts.hostname.rstrip(".").lower()
    if hostname in BLOCKED_HOSTS or hostname.endswith(".localhost"):
        raise WebToolError("禁止访问本机或云服务器元数据地址")
    try:
        port = parts.port
    except ValueError as error:
        raise WebToolError("网址端口格式无效") from error
    netloc = hostname
    if ":" in hostname and not hostname.startswith("["):
        netloc = f"[{hostname}]"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return urlunsplit((parts.scheme.lower(), netloc, parts.path or "/", parts.query, ""))


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() not in {"0", "false", "no", "off", ""}


def ensure_public_ip(address: str) -> None:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as error:
        raise WebToolError(f"无法识别目标 IP：{address}") from error
    if ip in BLOCKED_METADATA_IPS:
        raise WebToolError(f"禁止访问云服务器元数据地址：{ip}")
    if ip.is_loopback:
        raise WebToolError(f"禁止访问本机回环地址：{ip}")
    if ip.is_link_local:
        raise WebToolError(f"禁止访问链路本地地址：{ip}")
    if ip.is_unspecified or ip.is_multicast:
        raise WebToolError(f"禁止访问无效目标地址：{ip}")
    if ip.is_global:
        return
    if not env_bool("WEB_ALLOW_PRIVATE_ADDRESS"):
        raise WebToolError(
            f"禁止访问非公网地址：{ip}；如需访问可信内网或 Clash Fake-IP，"
            "请设置 WEB_ALLOW_PRIVATE_ADDRESS=true"
        )


async def validate_public_url(url: str) -> str:
    normalized = normalize_url(url)
    hostname = urlsplit(normalized).hostname
    assert hostname is not None
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        ensure_public_ip(str(literal_ip))
        return normalized

    try:
        records = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            None,
            socket.AF_UNSPEC,
            socket.SOCK_STREAM,
        )
    except socket.gaierror as error:
        raise WebToolError(f"无法解析网址主机：{hostname}") from error
    addresses = {record[4][0] for record in records}
    if not addresses:
        raise WebToolError(f"网址主机没有可用 IP：{hostname}")
    for address in addresses:
        ensure_public_ip(address)
    return normalized


async def fetch_public_page(url: str) -> dict[str, Any]:
    current_url = await validate_public_url(url)
    timeout = httpx.Timeout(20.0, connect=8.0)
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,text/plain,application/xhtml+xml,application/json;q=0.8",
    }
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            try:
                async with client.stream("GET", current_url, follow_redirects=False) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        if not location:
                            raise WebToolError("网页重定向响应缺少 Location")
                        current_url = await validate_public_url(
                            urljoin(current_url, location)
                        )
                        continue
                    response.raise_for_status()
                    content_type = response.headers.get("content-type", "").lower()
                    allowed = (
                        content_type.startswith("text/")
                        or "application/xhtml+xml" in content_type
                        or "application/json" in content_type
                        or not content_type
                    )
                    if not allowed:
                        raise WebToolError(f"暂不支持读取此内容类型：{content_type}")

                    chunks: list[bytes] = []
                    size = 0
                    async for chunk in response.aiter_bytes():
                        size += len(chunk)
                        if size > MAX_DOWNLOAD_BYTES:
                            raise WebToolError("网页内容超过 2 MiB 安全上限")
                        chunks.append(chunk)
                    encoding = response.charset_encoding or "utf-8"
                    raw_text = b"".join(chunks).decode(encoding, errors="replace")
                    final_url = str(response.url)
                    status_code = response.status_code
                break
            except httpx.TimeoutException as error:
                raise WebToolError("读取网页超时") from error
            except httpx.HTTPStatusError as error:
                raise WebToolError(
                    f"网页返回 HTTP {error.response.status_code}"
                ) from error
            except httpx.HTTPError as error:
                raise WebToolError(f"读取网页失败：{error}") from error
        else:
            raise WebToolError(f"网页重定向超过 {MAX_REDIRECTS} 次")

    if "html" in content_type or content_type.startswith("text/"):
        title, content = extract_html_text(raw_text)
    elif "json" in content_type:
        title = "JSON document"
        try:
            content = json.dumps(json.loads(raw_text), ensure_ascii=False, indent=2)
        except ValueError:
            content = compact_text(raw_text)
    else:
        title, content = "", compact_text(raw_text)

    truncated = len(content) > MAX_CONTENT_CHARS
    if truncated:
        content = content[:MAX_CONTENT_CHARS].rstrip() + "\n[内容已截断]"
    return {
        "url": final_url,
        "title": title,
        "status_code": status_code,
        "content_type": content_type,
        "content": content,
        "truncated": truncated,
    }


def search_provider() -> str:
    configured = os.getenv("WEB_SEARCH_PROVIDER", "auto").strip().lower()
    if configured not in {"auto", "tavily", "searxng"}:
        raise WebToolError("WEB_SEARCH_PROVIDER 只能是 auto、tavily 或 searxng")
    if configured != "auto":
        return configured
    if os.getenv("TAVILY_API_KEY", "").strip():
        return "tavily"
    if os.getenv("SEARXNG_URL", "").strip():
        return "searxng"
    raise WebToolError(
        "尚未配置搜索后端：填写 TAVILY_API_KEY，或设置 SEARXNG_URL"
    )


async def tavily_search(query: str, max_results: int) -> dict[str, Any]:
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        raise WebToolError("已选择 Tavily，但缺少 TAVILY_API_KEY")
    endpoint = os.getenv("TAVILY_API_URL", "https://api.tavily.com").rstrip("/")
    timeout = httpx.Timeout(25.0, connect=8.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                endpoint + "/search",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "query": query,
                    "search_depth": "basic",
                    "max_results": max_results,
                    "include_answer": False,
                    "include_raw_content": False,
                },
            )
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException as error:
        raise WebToolError("Tavily 搜索超时") from error
    except httpx.HTTPStatusError as error:
        raise WebToolError(f"Tavily 返回 HTTP {error.response.status_code}") from error
    except (httpx.HTTPError, ValueError) as error:
        raise WebToolError(f"Tavily 搜索失败：{error}") from error

    results = []
    for item in (data.get("results") or [])[:max_results]:
        results.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("content") or "")[:2000],
                "score": item.get("score"),
            }
        )
    return {"provider": "tavily", "query": query, "results": results}


async def searxng_search(query: str, max_results: int) -> dict[str, Any]:
    base_url = os.getenv("SEARXNG_URL", "").strip().rstrip("/")
    if not base_url:
        raise WebToolError("已选择 SearXNG，但缺少 SEARXNG_URL")
    api_key = os.getenv("SEARXNG_API_KEY", "").strip()
    headers = {"Accept": "application/json", "User-Agent": USER_AGENT}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    timeout = httpx.Timeout(25.0, connect=8.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
            response = await client.get(
                base_url + "/search",
                params={"q": query, "format": "json", "safesearch": 1},
            )
            response.raise_for_status()
            data = response.json()
    except httpx.TimeoutException as error:
        raise WebToolError("SearXNG 搜索超时") from error
    except httpx.HTTPStatusError as error:
        raise WebToolError(
            f"SearXNG 返回 HTTP {error.response.status_code}；请确认已启用 JSON 格式"
        ) from error
    except (httpx.HTTPError, ValueError) as error:
        raise WebToolError(f"SearXNG 搜索失败：{error}") from error

    results = []
    for item in (data.get("results") or [])[:max_results]:
        results.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "snippet": str(item.get("content") or "")[:2000],
                "engine": str(item.get("engine") or ""),
                "published_date": item.get("publishedDate"),
            }
        )
    return {"provider": "searxng", "query": query, "results": results}


@mcp.tool()
async def search_web(query: str, max_results: int = 5) -> dict[str, Any]:
    """搜索公开互联网并返回标题、网址和摘要。

    Args:
        query: 搜索关键词或完整问题。需要最新信息时应包含明确主题。
        max_results: 返回结果数量，范围1到10，默认5。
    """

    query = query.strip()
    if len(query) < 2:
        return {"error": "搜索内容至少需要两个字符"}
    max_results = max(1, min(int(max_results), 10))
    try:
        provider = search_provider()
        if provider == "tavily":
            return await tavily_search(query, max_results)
        return await searxng_search(query, max_results)
    except WebToolError as error:
        return {"error": str(error)}


@mcp.tool()
async def read_webpage(url: str) -> dict[str, Any]:
    """读取一个公开 HTTP/HTTPS 网页的标题和正文，适合分析用户给出的网址。

    Args:
        url: 完整公开网址，例如 https://example.com/article 。
    """

    try:
        return await fetch_public_page(url)
    except WebToolError as error:
        return {"url": url, "error": str(error)}


def run_server() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    run_server()
