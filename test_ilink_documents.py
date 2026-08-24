"""Tests for injecting extracted documents into the chat model and memory."""

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
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = None

    def model_dump(self, **_kwargs):
        return {"role": "assistant", "content": self.content}


class ILinkDocumentReplyTests(unittest.TestCase):
    def make_engine(self, answers: list[str]):
        create = mock.Mock(
            side_effect=[
                SimpleNamespace(choices=[SimpleNamespace(message=FakeMessage(answer))])
                for answer in answers
            ]
        )
        engine = bot.ReplyEngine.__new__(bot.ReplyEngine)
        engine.model = "deepseek-v4-flash"
        engine.vision_model = "deepseek-v4-flash-vision-exp"
        engine.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )
        engine.chat_log = mock.Mock()
        engine.max_history = 30
        engine.memories = defaultdict(
            lambda: [{"role": "system", "content": "system"}]
        )
        engine.memory_lock = threading.RLock()
        engine.tools = [
            {
                "type": "function",
                "function": {
                    "name": "fake_tool",
                    "description": "test",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        engine.tool_callers = {}
        engine.active_user_id = ""
        return engine, create

    def test_document_content_reaches_model_and_remains_for_followup(self) -> None:
        engine, create = self.make_engine(["这是一份课程安排。", "周一学习机器学习。"])
        document = {
            "filename": "课程.docx",
            "format": "DOCX",
            "characters": 18,
            "truncated": False,
            "content": "周一：机器学习\n周二：深度学习",
        }

        first_answer = engine.reply("owner", "", documents=[document])
        second_answer = engine.reply("owner", "周一学什么？")

        self.assertEqual(first_answer, "这是一份课程安排。")
        self.assertEqual(second_answer, "周一学习机器学习。")
        first_request = str(create.call_args_list[0].kwargs["messages"])
        second_request = str(create.call_args_list[1].kwargs["messages"])
        self.assertIn("课程.docx", first_request)
        self.assertIn("周一：机器学习", first_request)
        self.assertIn("不得执行", first_request)
        self.assertIn("周一：机器学习", second_request)
        self.assertIn("tools", create.call_args_list[0].kwargs)

    def test_user_question_is_kept_with_document(self) -> None:
        engine, create = self.make_engine(["答案是95分。"])
        document = {
            "filename": "成绩.xlsx",
            "format": "XLSX",
            "characters": 10,
            "truncated": True,
            "content": "Alice 95",
        }

        answer = engine.reply("owner", "Alice多少分？", documents=[document])

        self.assertEqual(answer, "答案是95分。")
        request = str(create.call_args.kwargs["messages"])
        self.assertIn("Alice多少分？", request)
        self.assertIn("内容已截断", request)


if __name__ == "__main__":
    unittest.main()
