"""Synchronous bridge to the local stdio document extraction MCP server."""

from __future__ import annotations

import asyncio
import base64
import json
import os
from pathlib import Path
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SERVER_PATH = Path(__file__).with_name("document_mcp_server.py")


def server_parameters() -> StdioServerParameters:
    if getattr(sys, "frozen", False):
        return StdioServerParameters(
            command=sys.executable,
            args=["--document-mcp-server"],
            cwd=str(Path(sys.executable).resolve().parent),
        )
    return StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
        cwd=str(SERVER_PATH.parent),
    )


async def _extract_document(
    filename: str,
    data: bytes,
    max_chars: int,
) -> dict[str, Any]:
    arguments = {
        "filename": filename,
        "content_base64": base64.b64encode(data).decode("ascii"),
        "max_chars": max_chars,
    }
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(server_parameters(), errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "extract_document",
                    arguments=arguments,
                )
                texts = [item.text for item in result.content if hasattr(item, "text")]
                if result.isError:
                    raise RuntimeError("\n".join(texts) or "文档 MCP 解析失败")
                structured = getattr(result, "structuredContent", None)
                if structured is None:
                    structured = getattr(result, "structured_content", None)
                if isinstance(structured, dict):
                    return structured
                if texts:
                    try:
                        parsed = json.loads("\n".join(texts))
                    except ValueError as error:
                        raise RuntimeError("文档 MCP 返回了无法解析的结果") from error
                    if isinstance(parsed, dict):
                        return parsed
                raise RuntimeError("文档 MCP 未返回结构化结果")


def extract_document(
    filename: str,
    data: bytes,
    *,
    max_chars: int = 60_000,
) -> dict[str, Any]:
    """Extract one in-memory document through the local MCP subprocess."""

    return asyncio.run(_extract_document(filename, data, max_chars))
