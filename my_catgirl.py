from __future__ import annotations

import json
import os
from collections import deque
from datetime import datetime
from pathlib import Path
import platform
import sys
import tempfile
import time
import traceback
from typing import Hashable, Iterable


def application_directory() -> Path:
    """Directory for editable config and logs in source and frozen builds."""

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_resource(name: str) -> Path:
    """Locate a read-only file bundled by PyInstaller."""

    bundle_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return bundle_root / name


def configure_console() -> None:
    """Use UTF-8 in a Windows console opened by double-clicking the EXE."""

    if os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def load_runtime_dependencies() -> None:
    """Import optional and third-party modules inside the guarded entrypoint."""

    global ChatLogger, Message, OpenAI, WeChat
    global call_weather_tool, list_weather_tools, load_dotenv

    from openai import OpenAI as OpenAIClient

    from chat_logger import ChatLogger as RuntimeChatLogger
    from weather_mcp_client import (
        call_weather_tool as runtime_call_weather_tool,
        list_weather_tools as runtime_list_weather_tools,
    )
    from wechat_uia import Message as RuntimeMessage, WeChat as RuntimeWeChat

    try:
        from dotenv import load_dotenv as runtime_load_dotenv
    except ImportError:
        runtime_load_dotenv = None

    OpenAI = OpenAIClient
    ChatLogger = RuntimeChatLogger
    Message = RuntimeMessage
    WeChat = RuntimeWeChat
    call_weather_tool = runtime_call_weather_tool
    list_weather_tools = runtime_list_weather_tools
    load_dotenv = runtime_load_dotenv


def safe_console_write(message: str) -> None:
    """Write a diagnostic even if one standard stream is unavailable."""

    for stream_name in ("stderr", "stdout"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            stream.write(message + "\n")
            stream.flush()
            return
        except (OSError, TypeError, ValueError):
            continue


def write_crash_report(
    error: BaseException,
    report_directory: str | Path | None = None,
) -> Path | None:
    """Persist a complete fatal traceback, with a temp-directory fallback."""

    if report_directory is not None:
        directories = [Path(report_directory)]
    else:
        try:
            primary = application_directory() / "chat_logs"
        except (OSError, RuntimeError):
            primary = Path.cwd() / "chat_logs"
        directories = [
            primary,
            Path(tempfile.gettempdir()) / "Catgirl微信助手" / "chat_logs",
        ]

    occurred_at = datetime.now()
    filename = occurred_at.strftime("crash_%Y-%m-%d_%H-%M-%S")
    filename += f"_{os.getpid()}.log"
    traceback_text = "".join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    report = (
        f"崩溃时间：{occurred_at:%Y-%m-%d %H:%M:%S}\n"
        f"异常类型：{type(error).__name__}\n"
        f"异常信息：{error}\n"
        f"Python：{sys.version}\n"
        f"系统：{platform.platform()}\n"
        f"可执行文件：{sys.executable}\n"
        f"工作目录：{Path.cwd()}\n"
        f"PyInstaller：{bool(getattr(sys, 'frozen', False))}\n\n"
        "完整调用栈：\n"
        f"{traceback_text}"
    )

    last_error: OSError | None = None
    for directory in dict.fromkeys(directories):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path = directory / filename
            path.write_text(report, encoding="utf-8", errors="replace")
            return path
        except OSError as write_error:
            last_error = write_error

    safe_console_write(f"无法写入崩溃日志：{last_error}")
    return None


def wait_for_user_after_crash() -> None:
    """Keep a double-clicked Windows EXE console visible after a fatal error."""

    if not getattr(sys, "frozen", False):
        return

    safe_console_write("程序已停止。按回车退出，或直接关闭此窗口。")
    try:
        input()
    except (EOFError, OSError, ValueError):
        # A damaged stdin handle cannot accept input. Keep the process alive so
        # the user can still read the error and close the console manually.
        if os.name == "nt":
            while True:
                time.sleep(3600)


def run_entrypoint() -> None:
    """Dispatch the interactive bot or its non-interactive MCP child process."""

    if "--weather-mcp-server" in sys.argv:
        from weather_mcp_server import run_server

        run_server()
        return
    main()


class MessageTracker:
    """Remember visible message rows and return only newly observed friends."""

    def __init__(self, max_entries: int = 5000) -> None:
        self.max_entries = max_entries
        self._seen: set[Hashable] = set()
        self._order: deque[Hashable] = deque()

    @staticmethod
    def _key(message: Message) -> Hashable:
        return message.id, message.content

    def _remember(self, key: Hashable) -> bool:
        if key in self._seen:
            return False
        self._seen.add(key)
        self._order.append(key)
        while len(self._order) > self.max_entries:
            expired = self._order.popleft()
            self._seen.discard(expired)
        return True

    def seed(self, messages: Iterable[Message]) -> None:
        for message in messages:
            self._remember(self._key(message))

    def new_friend_messages(self, messages: Iterable[Message]) -> list[Message]:
        new_friends = []
        for message in messages:
            is_new = self._remember(self._key(message))
            if is_new and message.type == "friend":
                new_friends.append(message)
        return new_friends


def ask_model(client, model, memory, weather_tools, chat_log) -> str:
    """Run Chat Completions and satisfy any MCP-backed weather tool calls."""

    for _ in range(10):
        response = client.chat.completions.create(
            model=model,
            messages=memory,
            tools=weather_tools,
            tool_choice="auto",
        )
        message = response.choices[0].message
        memory.append(message.model_dump(exclude_none=True))

        if not message.tool_calls:
            return message.content or "本喵暂时没有想到怎么回答。"

        for tool_call in message.tool_calls:
            name = tool_call.function.name
            raw_arguments = tool_call.function.arguments or "{}"
            try:
                arguments = json.loads(raw_arguments)
                tool_result = call_weather_tool(name, arguments)
            except Exception as error:
                tool_result = json.dumps(
                    {"error": str(error)},
                    ensure_ascii=False,
                )

            chat_log.tool(name, raw_arguments, tool_result)
            memory.append(
                {
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": tool_result,
                }
            )

    raise RuntimeError("模型连续调用工具次数过多，已停止本轮请求")


def main() -> None:
    configure_console()
    load_runtime_dependencies()
    app_directory = application_directory()
    if load_dotenv is not None:
        load_dotenv(app_directory / ".env")

    api_key = os.getenv("API_KEY_2")
    api_url = os.getenv("API_URL_2")
    model = os.getenv("MODEL_2", "deepseek-v4-flash")
    if not api_key:
        raise RuntimeError("缺少 API_KEY_2 环境变量")

    chat_log = ChatLogger(app_directory / "chat_logs")
    print(f"📝 本次聊天日志：{chat_log.path}")
    chat_log.system(f"模型：{model}")

    print("✅ API_KEY 已加载")
    client = OpenAI(api_key=api_key, base_url=api_url)

    with bundled_resource("聊天助手.txt").open("r", encoding="utf-8") as file:
        content = file.read()
    memory = [{"role": "system", "content": content}]

    weather_tools = list_weather_tools()
    chat_log.system(
        "天气 MCP 已连接，可用工具："
        + "、".join(tool["function"]["name"] for tool in weather_tools)
    )

    wx = None
    try:
        wx = WeChat()
        target = os.getenv("WECHAT_TARGET", "Inmost")
        wx.ChatWith(target, exact=True)
        chat_log.system(f"已连接微信会话：{target}")

        # 启动时把全部可见行设为基线，旧消息即使方向识别有误也不会被回复。
        initial_messages = wx.GetAllMessage()
        message_tracker = MessageTracker()
        message_tracker.seed(initial_messages)

        print("🚀 开始监听消息...")
        online_notice_pending = True

        while True:
            try:
                msg_list = wx.GetAllMessage()
                if online_notice_pending:
                    notice = "心跳维持中,已上线！"
                    wx.SendMsg(notice)
                    chat_log.assistant(notice)
                    online_notice_pending = False

                new_friend_messages = message_tracker.new_friend_messages(msg_list)
                if not new_friend_messages:
                    time.sleep(2)
                    continue

                # 一轮内若收到多条连续文本，按微信显示顺序合并为一次提问。
                user_content = "\n".join(
                    message.content
                    for message in new_friend_messages
                    if message.content is not None
                ).strip()
                if not user_content:
                    time.sleep(2)
                    continue
                chat_log.user(user_content)

                if "拍了拍" in user_content or not user_content.strip():
                    chat_log.system("忽略拍一拍或空消息")
                    continue

                command = user_content.strip()
                if command == "退出":
                    answer = "下次见！"
                    wx.SendMsg(answer)
                    chat_log.assistant(answer)
                    print("拜拜!")
                    return

                if command == "清空记忆":
                    memory = [{"role": "system", "content": content}]
                    answer = "已清空对话记忆，让我们重新认识吧"
                    wx.SendMsg(answer)
                    chat_log.assistant(answer)
                    print("已清空对话记忆，让我们重新认识吧！")
                    continue

                user_q = "你说:" + user_content
                memory.append({"role": "user", "content": user_q})
                print(f"📤 发送给 AI: {user_q[:30]}...")

                answer = ask_model(
                    client,
                    model,
                    memory,
                    weather_tools,
                    chat_log,
                )
                print(f"📥 AI 回复: {answer[:50]}...")

                wx.SendMsg(answer)
                chat_log.assistant(answer)
                print("✅ 消息已发送")
                time.sleep(2)

            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                print(f"⚠️ 错误: {message}")
                chat_log.error(message)
                try:
                    wx.SendMsg(f"⚠️ 错误: {message}")
                except Exception as notify_error:
                    chat_log.error(
                        "错误通知发送失败："
                        f"{type(notify_error).__name__}: {notify_error}"
                    )
                time.sleep(3)
    finally:
        if wx is not None:
            wx.Close()
        chat_log.system("程序结束")


if __name__ == "__main__":
    try:
        run_entrypoint()
    except Exception as fatal_error:
        crash_path = write_crash_report(fatal_error)
        safe_console_write("\n❌ 程序发生无法继续运行的错误。")
        safe_console_write(f"{type(fatal_error).__name__}: {fatal_error}")
        if crash_path is not None:
            safe_console_write(f"完整崩溃日志：{crash_path}")
        if "--weather-mcp-server" not in sys.argv:
            wait_for_user_after_crash()
        raise SystemExit(1)
