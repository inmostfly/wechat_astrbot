"""Tests for inbound Weixin document envelopes and in-memory decryption."""

from __future__ import annotations

import base64
from pathlib import Path
import sys
from unittest import mock
import unittest

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


PROJECT_DIR = Path(__file__).resolve().parent
ILINK_DIR = PROJECT_DIR / "ilink_catgirl"
if str(ILINK_DIR) not in sys.path:
    sys.path.insert(0, str(ILINK_DIR))

from weixin_ilink import ILinkClient, InboundFile, extract_inbound_message


def encrypt_for_weixin(plaintext: bytes, key: bytes) -> bytes:
    padder = padding.PKCS7(128).padder()
    padded = padder.update(plaintext) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
    return encryptor.update(padded) + encryptor.finalize()


class ILinkFileTests(unittest.TestCase):
    def test_file_only_message_is_extracted(self) -> None:
        raw = {
            "message_type": 1,
            "message_state": 0,
            "message_id": "file-1001",
            "from_user_id": "owner",
            "context_token": "context",
            "create_time_ms": 123,
            "item_list": [
                {
                    "type": 4,
                    "file_item": {
                        "file_name": "../../课程资料.docx",
                        "len": "2048",
                        "md5": "abc123",
                        "media": {
                            "encrypt_query_param": "encrypted-file-param",
                            "aes_key": "MDAxMTIyMzM0NDU1NjY3Nzg4OTlhYWJiY2NkZGVlZmY=",
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
        self.assertEqual(len(inbound.files), 1)
        self.assertEqual(inbound.files[0].file_name, "课程资料.docx")
        self.assertEqual(inbound.files[0].expected_size, 2048)
        self.assertEqual(inbound.files[0].md5, "abc123")

    def test_cdn_file_is_decrypted_in_memory(self) -> None:
        plaintext = b"document-content"
        key = bytes.fromhex("00112233445566778899aabbccddeeff")
        ciphertext = encrypt_for_weixin(plaintext, key)
        # File messages have been observed using base64(hex-text) AES keys.
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
        downloaded = client.download_file(
            InboundFile(
                file_name="notes.txt",
                encrypt_query_param="A+B/==",
                aes_key=encoded_key,
                encrypt_type=1,
                expected_size=len(plaintext),
                md5="abc123",
            )
        )

        self.assertEqual(downloaded.file_name, "notes.txt")
        self.assertEqual(downloaded.data, plaintext)
        self.assertEqual(downloaded.md5, "abc123")
        requested_url = client.http.stream.call_args.args[1]
        self.assertIn("encrypted_query_param=A%2BB%2F%3D%3D", requested_url)


if __name__ == "__main__":
    unittest.main()
