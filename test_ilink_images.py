"""Tests for transient WeChat image understanding."""

from __future__ import annotations

import base64
from collections import defaultdict
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import sys
import threading
import unittest

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


PROJECT_DIR = Path(__file__).resolve().parent
ILINK_DIR = PROJECT_DIR / "ilink_catgirl"
if str(ILINK_DIR) not in sys.path:
    sys.path.insert(0, str(ILINK_DIR))

import bot
from weixin_ilink import (
    DownloadedImage,
    ILinkClient,
    InboundImage,
    extract_inbound_message,
    extract_inbound_text,
)


def encrypt_for_weixin(plaintext: bytes, key: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


class FakeMessage:
    def __init__(self, content: str) -> None:
        self.content = content
        self.tool_calls = None

    def model_dump(self, **_kwargs):
        return {"role": "assistant", "content": self.content}


class ILinkImageTests(unittest.TestCase):
    def test_image_message_is_extracted_without_requiring_text(self) -> None:
        raw = {
            "message_type": 1,
            "message_state": 0,
            "message_id": "1001",
            "from_user_id": "owner",
            "context_token": "context",
            "create_time_ms": 123,
            "item_list": [
                {
                    "type": 2,
                    "image_item": {
                        "aeskey": "00112233445566778899aabbccddeeff",
                        "mid_size": 1024,
                        "media": {
                            "encrypt_query_param": "encrypted-param",
                            "encrypt_type": 1,
                        },
                    },
                }
            ],
        }

        inbound = extract_inbound_message(raw)

        self.assertIsNotNone(inbound)
        assert inbound is not None
        self.assertEqual(inbound.text, "")
        self.assertEqual(len(inbound.images), 1)
        self.assertEqual(inbound.images[0].aes_key, "00112233445566778899aabbccddeeff")
        self.assertIsNone(extract_inbound_text(raw))

    def test_cdn_image_is_decrypted_in_memory(self) -> None:
        plaintext = b"\xff\xd8\xff" + b"catgirl-image"
        key = bytes.fromhex("00112233445566778899aabbccddeeff")
        ciphertext = encrypt_for_weixin(plaintext, key)
        encoded_key = base64.b64encode(key.hex().encode("ascii")).decode("ascii")

        response = mock.MagicMock()
        response.headers = {"Content-Length": str(len(ciphertext))}
        response.iter_bytes.return_value = [ciphertext[:8], ciphertext[8:]]
        stream = mock.MagicMock()
        stream.__enter__.return_value = response
        stream.__exit__.return_value = False

        client = ILinkClient()
        client.http.close()
        client.http = mock.MagicMock()
        client.http.stream.return_value = stream
        image = client.download_image(
            InboundImage(
                encrypt_query_param="A+B/==",
                aes_key=encoded_key,
                encrypt_type=1,
                expected_size=len(ciphertext),
            )
        )

        self.assertEqual(image.data, plaintext)
        self.assertEqual(image.mime_type, "image/jpeg")
        requested_url = client.http.stream.call_args.args[1]
        self.assertIn("encrypted_query_param=A%2BB%2F%3D%3D", requested_url)

    def test_vision_request_does_not_persist_base64_image(self) -> None:
        create = mock.Mock(
            return_value=SimpleNamespace(
                choices=[SimpleNamespace(message=FakeMessage("图中是一只猫。"))]
            )
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
                    "name": "fake_weather_tool",
                    "description": "测试工具不应进入视觉请求",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
        engine.tool_callers = {}
        engine.active_user_id = ""

        answer = engine.reply(
            "owner",
            "这是什么？",
            images=[DownloadedImage(b"\xff\xd8\xffdemo", "image/jpeg")],
        )

        self.assertEqual(answer, "图中是一只猫。")
        self.assertEqual(create.call_args.kwargs["model"], engine.vision_model)
        self.assertNotIn("tools", create.call_args.kwargs)
        self.assertNotIn("tool_choice", create.call_args.kwargs)
        request_text = str(create.call_args.kwargs["messages"])
        self.assertIn("data:image/jpeg;base64", request_text)
        memory_text = str(engine.memories["owner"])
        self.assertNotIn("base64", memory_text)
        self.assertNotIn("data:image", memory_text)
        self.assertIn("这是什么？", memory_text)
        self.assertIn("图中是一只猫。", memory_text)


if __name__ == "__main__":
    unittest.main()
