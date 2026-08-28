"""Synchronous bridge to the local read-only IMAP MCP server."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import sys
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SERVER_PATH = Path(__file__).with_name("email_mcp_server.py")


def _redact_secrets(message: str) -> str:
    """Prevent credentials from leaking through transport or server errors."""

    result = message
    for variable in ("EMAIL_IMAP_PASSWORD", "EMAIL_IMAP_OAUTH_ACCESS_TOKEN"):
        secret = os.getenv(variable, "")
        if secret:
            result = result.replace(secret, "***")
    return result


def _exception_detail(error: BaseException) -> str:
    """Flatten ExceptionGroup so users see the actual MCP transport failure."""

    if isinstance(error, BaseExceptionGroup):
        details: list[str] = []
        for child in error.exceptions:
            detail = _exception_detail(child)
            if detail and detail not in details:
                details.append(detail)
        return "；".join(details) or str(error)
    return str(error)


def _run(coroutine: Any) -> Any:
    try:
        return asyncio.run(coroutine)
    except BaseExceptionGroup as error:
        detail = _redact_secrets(_exception_detail(error))
        raise RuntimeError(f"邮箱 MCP 通信失败：{detail}") from error


def server_parameters() -> StdioServerParameters:
    if getattr(sys, "frozen", False):
        return StdioServerParameters(
            command=sys.executable,
            args=["--email-mcp-server"],
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


def list_email_tools() -> list[dict[str, Any]]:
    return _run(_list_tools())


async def _call_tool(name: str, arguments: dict[str, Any]) -> str:
    failure = ""
    output = ""
    with open(os.devnull, "w", encoding="utf-8") as errlog:
        async with stdio_client(server_parameters(), errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments=arguments)
                if result.isError:
                    texts = [item.text for item in result.content if hasattr(item, "text")]
                    failure = "\n".join(texts) or f"邮箱 MCP 工具 {name} 调用失败"
                else:
                    structured = getattr(result, "structuredContent", None)
                    if structured is None:
                        structured = getattr(result, "structured_content", None)
                    if structured is not None:
                        output = json.dumps(structured, ensure_ascii=False)
                    else:
                        texts = [item.text for item in result.content if hasattr(item, "text")]
                        output = "\n".join(texts)
    if failure:
        raise RuntimeError(_redact_secrets(failure))
    return output


def call_email_tool(name: str, arguments: dict[str, Any]) -> str:
    return _run(_call_tool(name, arguments))
