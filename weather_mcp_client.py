"""Synchronous bridge from the bot to the local stdio weather MCP server."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SERVER_PATH = Path(__file__).with_name("weather_mcp_server.py")


def server_parameters() -> StdioServerParameters:
    if getattr(sys, "frozen", False):
        return StdioServerParameters(
            command=sys.executable,
            args=["--weather-mcp-server"],
            cwd=str(Path(sys.executable).resolve().parent),
        )
    return StdioServerParameters(
        command=sys.executable,
        args=[str(SERVER_PATH)],
        cwd=str(SERVER_PATH.parent),
    )


async def _list_tools() -> list[dict[str, Any]]:
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(server_parameters(), errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                response = await session.list_tools()
                return [
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description or "",
                            "parameters": tool.inputSchema,
                        },
                    }
                    for tool in response.tools
                ]


def list_weather_tools() -> list[dict[str, Any]]:
    """Return MCP tools converted to Chat Completions function schemas."""

    return asyncio.run(_list_tools())


async def _call_tool(name: str, arguments: dict[str, Any]) -> str:
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(server_parameters(), errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments=arguments)
                if result.isError:
                    texts = [
                        item.text for item in result.content if hasattr(item, "text")
                    ]
                    raise RuntimeError(
                        "\n".join(texts) or f"MCP 工具 {name} 调用失败"
                    )

                structured = getattr(result, "structuredContent", None)
                if structured is None:
                    structured = getattr(result, "structured_content", None)
                if structured is not None:
                    return json.dumps(structured, ensure_ascii=False)

                texts = [item.text for item in result.content if hasattr(item, "text")]
                return "\n".join(texts)


def call_weather_tool(name: str, arguments: dict[str, Any]) -> str:
    """Call one tool on the local weather MCP server."""

    return asyncio.run(_call_tool(name, arguments))
