"""Regression tests for stable chat memory around model tool calls."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys
import threading
import unittest


PROJECT_DIR = Path(__file__).resolve().parent
ILINK_DIR = PROJECT_DIR / "ilink_catgirl"
if str(ILINK_DIR) not in sys.path:
    sys.path.insert(0, str(ILINK_DIR))

import bot


class FakeMessage:
    def __init__(self, content: str | None, tool_calls=None) -> None:
        self.content = content
        self.tool_calls = tool_calls

    def model_dump(self, **_kwargs):
        message = {"role": "assistant", "content": self.content}
        if self.tool_calls:
            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in self.tool_calls
            ]
        return message


def make_tool_call(call_id: str = "call_weather"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(
            name="fake_weather",
            arguments='{"location":"邓州"}',
        ),
    )


def make_engine(responses) -> bot.ReplyEngine:
    engine = bot.ReplyEngine.__new__(bot.ReplyEngine)
    engine.model = "deepseek-v4-flash"
    engine.vision_model = "deepseek-v4-flash-vision-exp"
    engine.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=mock.Mock(side_effect=responses))
        )
    )
    engine.chat_log = mock.Mock()
    engine.max_history = 6
    engine.memories = defaultdict(
        lambda: [{"role": "system", "content": "system"}]
    )
    engine.memory_lock = threading.RLock()
    engine.tools = [
        {
            "type": "function",
            "function": {
                "name": "fake_weather",
                "description": "test",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    engine.tool_callers = {
        "fake_weather": lambda _name, _arguments: '{"weather":"晴"}'
    }
    engine.active_user_id = ""
    return engine


class ILinkMemoryTests(unittest.TestCase):
    def test_tool_protocol_is_request_local_but_final_turn_is_remembered(self) -> None:
        tool_call = make_tool_call()
        first = SimpleNamespace(
            choices=[
                SimpleNamespace(message=FakeMessage(None, tool_calls=[tool_call]))
            ]
        )
        second = SimpleNamespace(
            choices=[SimpleNamespace(message=FakeMessage("邓州今天晴。"))]
        )
        engine = make_engine([first, second])

        answer = engine.reply("owner", "查询邓州天气")

        self.assertEqual(answer, "邓州今天晴。")
        create = engine.client.chat.completions.create
        second_request = create.call_args_list[1].kwargs["messages"]
        self.assertTrue(second_request[-3].get("tool_calls"))
        self.assertEqual(second_request[-2]["role"], "tool")
        self.assertEqual(second_request[-2]["tool_call_id"], tool_call.id)

        memory = engine.memories["owner"]
        self.assertEqual(
            memory,
            [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "查询邓州天气"},
                {"role": "assistant", "content": "邓州今天晴。"},
            ],
        )
        self.assertNotIn("tool", [message["role"] for message in memory])
        self.assertFalse(any(message.get("tool_calls") for message in memory))

    def test_failed_model_request_does_not_pollute_memory(self) -> None:
        engine = make_engine([RuntimeError("temporary API failure")])

        with self.assertRaisesRegex(RuntimeError, "temporary API failure"):
            engine.reply("owner", "这条请求会失败")

        self.assertEqual(
            engine.memories["owner"],
            [{"role": "system", "content": "system"}],
        )

    def test_trim_removes_legacy_tool_fragments(self) -> None:
        engine = make_engine([])
        memory = [
            {"role": "system", "content": "system"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [{"id": "call_old"}],
            },
            {"role": "tool", "tool_call_id": "call_old", "content": "old"},
            {"role": "assistant", "content": "旧回答"},
            {"role": "user", "content": "新问题"},
        ]

        engine._trim(memory)

        self.assertEqual(
            memory,
            [
                {"role": "system", "content": "system"},
                {"role": "assistant", "content": "旧回答"},
                {"role": "user", "content": "新问题"},
            ],
        )


if __name__ == "__main__":
    unittest.main()
