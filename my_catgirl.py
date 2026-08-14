import json
import os
from collections import deque
from pathlib import Path
import sys
import time
from typing import Hashable, Iterable

from openai import OpenAI

from chat_logger import ChatLogger
from weather_mcp_client import call_weather_tool, list_weather_tools
from wechat_uia import Message, WeChat

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


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
    app_directory = application_directory()
    if load_dotenv is not None:
        load_dotenv(app_directory / ".env")

    api_key = os.getenv("API_KEY_2")
    api_url = os.getenv("API_URL_2")
    model = os.getenv("MODEL_2", "deepseek-v4-flash")
    if not api_key:
        print("❌ 错误：缺少 API_KEY_2 环境变量")
        sys.exit(1)

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
                wx.SendMsg(f"⚠️ 错误: {message}")
                time.sleep(3)
    finally:
        if wx is not None:
            wx.Close()
        chat_log.system("程序结束")


if __name__ == "__main__":
    if "--weather-mcp-server" in sys.argv:
        from weather_mcp_server import run_server

        run_server()
    else:
        main()
