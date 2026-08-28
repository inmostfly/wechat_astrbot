"""Config-driven, allowlisted stdio MCP loader for the iLink bot."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client


MAX_CONFIG_BYTES = 256 * 1024
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_RESULT_CHARS = 30_000


@dataclass(frozen=True)
class ExternalMCPServer:
    name: str
    command: str
    args: tuple[str, ...]
    cwd: Path | None
    environment: dict[str, str]
    allowed_tools: frozenset[str]
    public_prefix: str
    timeout_seconds: float
    max_result_chars: int

    def parameters(self) -> StdioServerParameters:
        return StdioServerParameters(
            command=self.command,
            args=list(self.args),
            cwd=str(self.cwd) if self.cwd is not None else None,
            env=self.environment,
        )


@dataclass(frozen=True)
class ToolBinding:
    server: ExternalMCPServer
    actual_name: str


class GenericMCPManager:
    """Load explicitly allowed tools from external stdio MCP servers."""

    def __init__(self, config_path: str | Path, chat_log: Any) -> None:
        self.config_path = Path(config_path)
        self.chat_log = chat_log
        self._bindings: dict[str, ToolBinding] = {}

    def list_tools(self) -> list[dict[str, Any]]:
        """Read configuration, inspect enabled servers and return model schemas."""

        servers = self._load_config()
        schemas = asyncio.run(self._list_all(servers))
        return schemas

    def call_tool(self, public_name: str, arguments: dict[str, Any]) -> str:
        """Call one previously discovered tool by its namespaced public name."""

        binding = self._bindings.get(public_name)
        if binding is None:
            raise RuntimeError(f"通用 MCP 工具未注册或未获授权：{public_name}")
        return asyncio.run(self._call_one(binding, arguments))

    def _load_config(self) -> list[ExternalMCPServer]:
        try:
            size = self.config_path.stat().st_size
        except FileNotFoundError:
            return []
        if size > MAX_CONFIG_BYTES:
            raise ValueError("MCP 配置文件超过 256 KiB，拒绝加载")
        try:
            raw_config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError(f"无法读取 MCP 配置：{error}") from error
        raw_servers = raw_config.get("servers") if isinstance(raw_config, dict) else None
        if not isinstance(raw_servers, dict):
            raise ValueError("MCP 配置必须包含 servers 对象")

        servers: list[ExternalMCPServer] = []
        for name, raw in raw_servers.items():
            if not isinstance(raw, dict) or not raw.get("enabled", False):
                continue
            try:
                servers.append(self._parse_server(str(name), raw))
            except Exception as error:
                self.chat_log.error(f"外部 MCP 配置无效 [{name}]：{error}")
        return servers

    def _parse_server(
        self, name: str, raw: dict[str, Any]
    ) -> ExternalMCPServer:
        command = str(raw.get("command") or "").strip()
        if not command:
            raise ValueError("缺少 command")
        raw_args = raw.get("args") or []
        if not isinstance(raw_args, list) or not all(
            isinstance(value, str) for value in raw_args
        ):
            raise ValueError("args 必须是字符串数组")
        raw_allowed = raw.get("allowed_tools") or []
        if not isinstance(raw_allowed, list) or not all(
            isinstance(value, str) and value.strip() for value in raw_allowed
        ):
            raise ValueError("allowed_tools 必须是非空工具名数组")
        allowed_tools = frozenset(value.strip() for value in raw_allowed)
        if not allowed_tools:
            raise ValueError("必须显式填写 allowed_tools；不允许默认暴露全部工具")

        cwd: Path | None = None
        raw_cwd = str(raw.get("cwd") or "").strip()
        if raw_cwd:
            cwd = Path(raw_cwd)
            if not cwd.is_absolute():
                cwd = (self.config_path.parent / cwd).resolve()
            if not cwd.is_dir():
                raise ValueError(f"cwd 不存在或不是目录：{cwd}")

        environment = get_default_environment()
        raw_environment = raw.get("env") or {}
        if not isinstance(raw_environment, dict):
            raise ValueError("env 必须是“子进程变量名: .env变量名”对象")
        for child_name, source_name in raw_environment.items():
            child_name = str(child_name).strip()
            source_name = str(source_name).strip()
            if not child_name or not source_name:
                raise ValueError("env 中的变量名不能为空")
            value = os.getenv(source_name)
            if value is None or not value.strip():
                raise ValueError(f"环境变量 {source_name} 尚未配置")
            environment[child_name] = value

        public_prefix = self._safe_name(str(raw.get("name_prefix") or name))
        timeout_seconds = max(
            1.0,
            min(float(raw.get("timeout_seconds") or DEFAULT_TIMEOUT_SECONDS), 300.0),
        )
        max_result_chars = max(
            500,
            min(
                int(raw.get("max_result_chars") or DEFAULT_MAX_RESULT_CHARS),
                200_000,
            ),
        )
        return ExternalMCPServer(
            name=name,
            command=command,
            args=tuple(raw_args),
            cwd=cwd,
            environment=environment,
            allowed_tools=allowed_tools,
            public_prefix=public_prefix,
            timeout_seconds=timeout_seconds,
            max_result_chars=max_result_chars,
        )

    @staticmethod
    def _safe_name(value: str) -> str:
        normalized = re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_-")
        if not normalized:
            raise ValueError("服务名称无法转换为合法工具前缀")
        return normalized[:30]

    async def _list_all(
        self, servers: list[ExternalMCPServer]
    ) -> list[dict[str, Any]]:
        schemas: list[dict[str, Any]] = []
        self._bindings.clear()
        for server in servers:
            try:
                discovered = await self._list_one(server)
            except Exception as error:
                self.chat_log.error(f"外部 MCP 无法连接 [{server.name}]：{error}")
                continue
            exposed_names: list[str] = []
            for tool in discovered:
                if tool.name not in server.allowed_tools:
                    continue
                public_name = f"{server.public_prefix}__{self._safe_name(tool.name)}"[:64]
                if public_name in self._bindings:
                    self.chat_log.error(f"外部 MCP 工具名称冲突，已跳过：{public_name}")
                    continue
                self._bindings[public_name] = ToolBinding(server, tool.name)
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": public_name,
                            "description": (
                                f"[{server.name}] {tool.description or tool.name}"
                            ),
                            "parameters": tool.inputSchema
                            or {"type": "object", "properties": {}},
                        },
                    }
                )
                exposed_names.append(public_name)
            missing = sorted(server.allowed_tools - {tool.name for tool in discovered})
            if missing:
                self.chat_log.error(
                    f"外部 MCP 未提供配置中的工具 [{server.name}]：{'、'.join(missing)}"
                )
            if exposed_names:
                self.chat_log.system(
                    f"外部 MCP 已授权 [{server.name}]：{'、'.join(exposed_names)}"
                )
        return schemas

    async def _list_one(self, server: ExternalMCPServer):
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            async with asyncio.timeout(server.timeout_seconds):
                async with stdio_client(server.parameters(), errlog=errlog) as (
                    read,
                    write,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        response = await session.list_tools()
                        return list(response.tools)

    async def _call_one(
        self, binding: ToolBinding, arguments: dict[str, Any]
    ) -> str:
        server = binding.server
        with open(os.devnull, "w", encoding="utf-8") as errlog:
            async with asyncio.timeout(server.timeout_seconds):
                async with stdio_client(server.parameters(), errlog=errlog) as (
                    read,
                    write,
                ):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(
                            binding.actual_name,
                            arguments=arguments,
                        )
        return self._format_result(
            result,
            binding.actual_name,
            server.max_result_chars,
        )

    @staticmethod
    def _format_result(result: Any, tool_name: str, max_result_chars: int) -> str:
        texts = [item.text for item in result.content if hasattr(item, "text")]
        if result.isError:
            raise RuntimeError(
                "\n".join(texts)
                or f"外部 MCP 工具 {tool_name} 调用失败"
            )
        structured = getattr(result, "structuredContent", None)
        if structured is None:
            structured = getattr(result, "structured_content", None)
        output = (
            json.dumps(structured, ensure_ascii=False, default=str)
            if structured is not None
            else "\n".join(texts)
        )
        if len(output) > max_result_chars:
            output = output[: max_result_chars - 20] + "\n[结果已截断]"
        return output
