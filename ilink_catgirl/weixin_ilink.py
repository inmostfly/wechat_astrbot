"""Minimal synchronous client for Tencent's Weixin iLink Bot HTTP API."""

from __future__ import annotations

import base64
import binascii
from collections import deque
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import random
import re
import time
from typing import Any, Iterable
from urllib.parse import urlencode, urljoin, urlparse
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_BOT_TYPE = "3"
CHANNEL_VERSION = "2.4.6"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = "132102"
BOT_AGENT = "CatgirlLite/0.1.0"
WEIXIN_CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"


class ILinkError(RuntimeError):
    """Base class for iLink protocol or network failures."""


class LoginExpiredError(ILinkError):
    """Raised when Weixin reports that the saved bot session has expired."""


@dataclass
class LoginChallenge:
    qrcode: str
    qrcode_url: str


@dataclass
class ILinkSession:
    bot_token: str
    bot_id: str
    base_url: str = DEFAULT_BASE_URL
    owner_user_id: str = ""
    get_updates_buf: str = ""
    recent_message_keys: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "ILinkSession":
        return cls(
            bot_token=str(value.get("bot_token") or ""),
            bot_id=str(value.get("bot_id") or ""),
            base_url=str(value.get("base_url") or DEFAULT_BASE_URL),
            owner_user_id=str(value.get("owner_user_id") or ""),
            get_updates_buf=str(value.get("get_updates_buf") or ""),
            recent_message_keys=[
                str(item) for item in (value.get("recent_message_keys") or [])
            ][-500:],
        )


@dataclass(frozen=True)
class InboundImage:
    encrypt_query_param: str = ""
    aes_key: str = ""
    direct_url: str = ""
    encrypt_type: int = 0
    expected_size: int = 0


@dataclass(frozen=True)
class DownloadedImage:
    data: bytes
    mime_type: str


@dataclass(frozen=True)
class InboundFile:
    file_name: str
    encrypt_query_param: str = ""
    aes_key: str = ""
    direct_url: str = ""
    encrypt_type: int = 0
    expected_size: int = 0
    md5: str = ""


@dataclass(frozen=True)
class DownloadedFile:
    file_name: str
    data: bytes
    md5: str = ""


@dataclass(frozen=True)
class InboundMessage:
    key: str
    from_user_id: str
    text: str
    context_token: str
    message_id: str
    create_time_ms: int
    images: tuple[InboundImage, ...] = ()
    files: tuple[InboundFile, ...] = ()


# Keep the old public name for code that imported it before image support existed.
InboundText = InboundMessage


def decode_weixin_aes_key(value: str) -> bytes:
    """Accept the AES key encodings observed in inbound iLink media."""

    encoded = value.strip()
    if re.fullmatch(r"[0-9a-fA-F]{32}", encoded):
        return bytes.fromhex(encoded)
    try:
        decoded = base64.b64decode(
            encoded + "=" * (-len(encoded) % 4),
            altchars=b"-_",
            validate=True,
        )
    except (ValueError, binascii.Error) as error:
        raise ILinkError("微信图片的 AES 密钥编码无效") from error
    if len(decoded) == 16:
        return decoded
    if len(decoded) == 32 and re.fullmatch(rb"[0-9a-fA-F]{32}", decoded):
        return bytes.fromhex(decoded.decode("ascii"))
    raise ILinkError("微信图片的 AES 密钥长度无效")


def decrypt_weixin_media(ciphertext: bytes, aes_key: str) -> bytes:
    """Decrypt AES-128-ECB media and remove PKCS#7 padding."""

    if not ciphertext or len(ciphertext) % 16:
        raise ILinkError("微信图片密文长度无效")
    try:
        decryptor = Cipher(
            algorithms.AES(decode_weixin_aes_key(aes_key)),
            modes.ECB(),
        ).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        unpadder = padding.PKCS7(128).unpadder()
        return unpadder.update(padded) + unpadder.finalize()
    except ValueError as error:
        raise ILinkError("微信图片解密失败，AES 密钥或填充不正确") from error


def detect_image_mime(data: bytes) -> str:
    """Return a DeepSeek-supported image MIME type from magic bytes."""

    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise ILinkError("微信图片格式不受识图模型支持，仅支持 JPEG、PNG、GIF、WebP")


class SessionStore:
    """Persist credentials, long-poll cursor, and a bounded deduplication cache."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> ILinkSession | None:
        if not self.path.exists():
            return None
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise ILinkError(f"无法读取登录状态 {self.path}：{error}") from error
        session = ILinkSession.from_dict(value)
        if not session.bot_token or not session.bot_id:
            raise ILinkError(f"登录状态文件缺少 bot_token 或 bot_id：{self.path}")
        return session

    def save(self, session: ILinkSession) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(asdict(session), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temporary.replace(self.path)
        try:
            self.path.chmod(0o600)
        except OSError:
            pass


class ILinkClient:
    """Log in, long-poll messages, and reply through the documented iLink API."""

    def __init__(
        self,
        session: ILinkSession | None = None,
        *,
        login_base_url: str = DEFAULT_BASE_URL,
        bot_type: str = DEFAULT_BOT_TYPE,
    ) -> None:
        self.session = session
        self.login_base_url = login_base_url.rstrip("/")
        self.bot_type = bot_type
        self.http = httpx.Client(follow_redirects=True)

    def close(self) -> None:
        self.http.close()

    @staticmethod
    def _base_info() -> dict[str, str]:
        return {"channel_version": CHANNEL_VERSION, "bot_agent": BOT_AGENT}

    @staticmethod
    def _wechat_uin() -> str:
        number = random.SystemRandom().randrange(0, 2**32)
        return base64.b64encode(str(number).encode("utf-8")).decode("ascii")

    @classmethod
    def _headers(cls, token: str = "") -> dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "X-WECHAT-UIN": cls._wechat_uin(),
            "iLink-App-Id": ILINK_APP_ID,
            "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
        }
        if token.strip():
            headers["Authorization"] = f"Bearer {token.strip()}"
        return headers

    def _post(
        self,
        base_url: str,
        endpoint: str,
        body: dict[str, Any],
        *,
        token: str = "",
        timeout_seconds: float = 15.0,
    ) -> dict[str, Any]:
        url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
        try:
            response = self.http.post(
                url,
                headers=self._headers(token),
                json=body,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            value = response.json()
        except httpx.TimeoutException as error:
            raise ILinkError(f"iLink 请求超时：{endpoint}") from error
        except httpx.HTTPStatusError as error:
            raise ILinkError(
                f"iLink HTTP {error.response.status_code}：{endpoint}"
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise ILinkError(f"iLink 网络或响应错误：{endpoint}：{error}") from error
        if not isinstance(value, dict):
            raise ILinkError(f"iLink 返回了非对象 JSON：{endpoint}")
        return value

    def _get(
        self,
        base_url: str,
        endpoint: str,
        *,
        params: dict[str, str],
        timeout_seconds: float = 35.0,
    ) -> dict[str, Any]:
        url = urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))
        try:
            response = self.http.get(
                url,
                headers={
                    "iLink-App-Id": ILINK_APP_ID,
                    "iLink-App-ClientVersion": ILINK_APP_CLIENT_VERSION,
                },
                params=params,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            value = response.json()
        except httpx.TimeoutException:
            return {"status": "wait"}
        except httpx.HTTPStatusError as error:
            raise ILinkError(
                f"iLink HTTP {error.response.status_code}：{endpoint}"
            ) from error
        except (httpx.HTTPError, ValueError) as error:
            raise ILinkError(f"iLink 网络或响应错误：{endpoint}：{error}") from error
        if not isinstance(value, dict):
            raise ILinkError(f"iLink 返回了非对象 JSON：{endpoint}")
        return value

    def request_login_qr(self, local_tokens: Iterable[str] = ()) -> LoginChallenge:
        response = self._post(
            self.login_base_url,
            f"ilink/bot/get_bot_qrcode?bot_type={self.bot_type}",
            {"local_token_list": [token for token in local_tokens if token][-10:]},
        )
        qrcode = str(response.get("qrcode") or "")
        qrcode_url = str(response.get("qrcode_img_content") or "")
        if not qrcode or not qrcode_url:
            raise ILinkError("获取二维码成功，但响应缺少 qrcode 或 qrcode_img_content")
        return LoginChallenge(qrcode=qrcode, qrcode_url=qrcode_url)

    @staticmethod
    def save_login_qr(challenge: LoginChallenge, path: str | Path) -> Path | None:
        """Save a scannable PNG when qrcode is installed; the URL is always printable."""

        try:
            import qrcode
        except ImportError:
            return None
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        image = qrcode.make(challenge.qrcode_url)
        image.save(output)
        return output

    @staticmethod
    def _redirect_base_url(host: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9.-]+(?::[0-9]{1,5})?", host):
            raise ILinkError(f"服务器返回了不安全的重定向主机：{host!r}")
        return f"https://{host}"

    def wait_for_login(
        self,
        challenge: LoginChallenge,
        *,
        timeout_seconds: int = 480,
    ) -> ILinkSession:
        deadline = time.monotonic() + max(timeout_seconds, 30)
        polling_base_url = self.login_base_url
        verify_code = ""
        scanned_announced = False

        while time.monotonic() < deadline:
            params = {"qrcode": challenge.qrcode}
            if verify_code:
                params["verify_code"] = verify_code
            status = self._get(
                polling_base_url,
                "ilink/bot/get_qrcode_status",
                params=params,
            )
            state = str(status.get("status") or "wait")

            if state == "wait":
                pass
            elif state == "scaned":
                verify_code = ""
                if not scanned_announced:
                    print("二维码已扫描，等待手机确认……")
                    scanned_announced = True
            elif state == "need_verifycode":
                verify_code = input("请输入手机微信显示的配对数字：").strip()
            elif state == "verify_code_blocked":
                raise ILinkError("配对数字多次错误，登录暂时被阻止，请稍后重试")
            elif state == "scaned_but_redirect":
                host = str(status.get("redirect_host") or "")
                if not host:
                    raise ILinkError("扫码后需要重定向，但响应缺少 redirect_host")
                polling_base_url = self._redirect_base_url(host)
            elif state == "binded_redirect":
                raise ILinkError(
                    "此机器人已绑定过，但本地没有可用凭据；请恢复 data/session.json，"
                    "或在微信端解除后重新连接"
                )
            elif state == "expired":
                raise ILinkError("登录二维码已过期，请重新启动程序生成新二维码")
            elif state == "confirmed":
                token = str(status.get("bot_token") or "")
                bot_id = str(status.get("ilink_bot_id") or "")
                if not token or not bot_id:
                    raise ILinkError("登录已确认，但响应缺少 bot_token 或 ilink_bot_id")
                session = ILinkSession(
                    bot_token=token,
                    bot_id=bot_id,
                    base_url=str(status.get("baseurl") or polling_base_url),
                    owner_user_id=str(status.get("ilink_user_id") or ""),
                )
                self.session = session
                return session
            else:
                raise ILinkError(f"未知的二维码状态：{state}")

            time.sleep(1)
        raise ILinkError("等待扫码超时，请重新启动程序")

    def _require_session(self) -> ILinkSession:
        if self.session is None:
            raise ILinkError("尚未登录微信机器人")
        return self.session

    def get_updates(self, timeout_ms: int = 35_000) -> dict[str, Any]:
        session = self._require_session()
        response = self._post(
            session.base_url,
            "ilink/bot/getupdates",
            {
                "get_updates_buf": session.get_updates_buf,
                "base_info": self._base_info(),
            },
            token=session.bot_token,
            timeout_seconds=max(timeout_ms / 1000 + 5, 10),
        )
        if response.get("errcode") == -14:
            raise LoginExpiredError("微信机器人登录态已过期，需要重新扫码")
        ret = int(response.get("ret") or 0)
        if ret != 0:
            raise ILinkError(
                f"getupdates 失败：ret={ret}，{response.get('errmsg') or '无说明'}"
            )
        return response

    def notify_start(self) -> None:
        self._notify("ilink/bot/msg/notifystart")

    def notify_stop(self) -> None:
        self._notify("ilink/bot/msg/notifystop")

    def _notify(self, endpoint: str) -> None:
        session = self._require_session()
        response = self._post(
            session.base_url,
            endpoint,
            {"base_info": self._base_info()},
            token=session.bot_token,
            timeout_seconds=10,
        )
        ret = int(response.get("ret") or 0)
        if ret != 0:
            raise ILinkError(
                f"{endpoint} 失败：ret={ret}，{response.get('errmsg') or '无说明'}"
            )

    @staticmethod
    def _media_download_url(media: InboundImage | InboundFile) -> str:
        if media.encrypt_query_param:
            return (
                f"{WEIXIN_CDN_BASE_URL}/download?"
                + urlencode({"encrypted_query_param": media.encrypt_query_param})
            )
        parsed = urlparse(media.direct_url)
        hostname = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not (
            hostname == "cdn.weixin.qq.com"
            or hostname.endswith(".cdn.weixin.qq.com")
        ):
            raise ILinkError("微信图片下载地址不是受信任的 HTTPS CDN")
        return media.direct_url

    @staticmethod
    def _image_download_url(image: InboundImage) -> str:
        """Compatibility wrapper retained for image-specific callers/tests."""

        return ILinkClient._media_download_url(image)

    def _download_media(
        self,
        media: InboundImage | InboundFile,
        *,
        max_bytes: int,
        label: str,
    ) -> bytes:
        """Download and decrypt one trusted iLink CDN media object in memory."""

        max_bytes = max(1024, int(max_bytes))
        if media.expected_size > max_bytes:
            raise ILinkError(
                f"微信{label}超过处理大小限制（最大 {max_bytes // 1024 // 1024} MiB）"
            )
        url = self._media_download_url(media)
        download_limit = max_bytes + 16
        encrypted = bytearray()
        try:
            with self.http.stream("GET", url, timeout=30) as response:
                response.raise_for_status()
                content_length = int(response.headers.get("Content-Length") or 0)
                if content_length > download_limit:
                    raise ILinkError(f"微信{label}下载内容超过大小限制")
                for chunk in response.iter_bytes():
                    encrypted.extend(chunk)
                    if len(encrypted) > download_limit:
                        raise ILinkError(f"微信{label}下载内容超过大小限制")
        except httpx.TimeoutException as error:
            raise ILinkError(f"微信{label} CDN 下载超时") from error
        except httpx.HTTPStatusError as error:
            raise ILinkError(
                f"微信{label} CDN 返回 HTTP {error.response.status_code}"
            ) from error
        except httpx.HTTPError as error:
            raise ILinkError(f"微信{label} CDN 下载失败：{error}") from error

        if not encrypted:
            raise ILinkError(f"微信{label} CDN 返回了空内容")
        if media.aes_key:
            plaintext = decrypt_weixin_media(bytes(encrypted), media.aes_key)
        elif media.encrypt_type == 1 or media.encrypt_query_param:
            raise ILinkError(f"微信{label}缺少 AES 解密密钥")
        else:
            plaintext = bytes(encrypted)
        if len(plaintext) > max_bytes:
            raise ILinkError(f"微信{label}解密后超过大小限制")
        return plaintext

    def download_image(
        self,
        image: InboundImage,
        *,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> DownloadedImage:
        """Download and decrypt one inbound image entirely in memory."""

        plaintext = self._download_media(
            image,
            max_bytes=max_bytes,
            label="图片",
        )
        return DownloadedImage(data=plaintext, mime_type=detect_image_mime(plaintext))

    def download_file(
        self,
        file: InboundFile,
        *,
        max_bytes: int = 15 * 1024 * 1024,
    ) -> DownloadedFile:
        """Download and decrypt an inbound document without writing it to disk."""

        plaintext = self._download_media(
            file,
            max_bytes=max_bytes,
            label="文件",
        )
        return DownloadedFile(file_name=file.file_name, data=plaintext, md5=file.md5)

    def send_text(
        self,
        to_user_id: str,
        context_token: str,
        text: str,
        *,
        max_chars: int = 1800,
    ) -> int:
        session = self._require_session()
        if not to_user_id or not context_token:
            raise ILinkError("发送消息缺少 to_user_id 或 context_token")
        chunks = list(self._split_text(text.strip(), max_chars=max_chars))
        if not chunks:
            chunks = ["暂时没有可发送的内容。"]
        for chunk in chunks:
            response = self._post(
                session.base_url,
                "ilink/bot/sendmessage",
                {
                    "msg": {
                        "to_user_id": to_user_id,
                        "client_id": f"catgirl-lite-{uuid4().hex}",
                        "message_type": 2,
                        "message_state": 2,
                        "context_token": context_token,
                        "item_list": [
                            {
                                "type": 1,
                                "is_completed": True,
                                "text_item": {"text": chunk},
                            }
                        ],
                    },
                    "base_info": self._base_info(),
                },
                token=session.bot_token,
            )
            ret = int(response.get("ret") or 0)
            if ret != 0:
                raise ILinkError(
                    f"sendmessage 失败：ret={ret}，{response.get('errmsg') or '无说明'}"
                )
        return len(chunks)

    @staticmethod
    def _split_text(text: str, *, max_chars: int) -> Iterable[str]:
        max_chars = max(100, max_chars)
        remaining = text
        while len(remaining) > max_chars:
            split_at = max(
                remaining.rfind("\n", 0, max_chars + 1),
                remaining.rfind("。", 0, max_chars + 1),
                remaining.rfind("！", 0, max_chars + 1),
                remaining.rfind("？", 0, max_chars + 1),
            )
            if split_at < max_chars // 2:
                split_at = max_chars
            else:
                split_at += 1
            yield remaining[:split_at]
            remaining = remaining[split_at:].lstrip()
        if remaining:
            yield remaining


def _mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _first_string(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""


def _extract_inbound_image(item: dict[str, Any]) -> InboundImage | None:
    image_item = _mapping(item.get("image_item"))
    if not image_item:
        return None
    media = _mapping(image_item.get("media"))
    encrypt_query_param = _first_string(
        media.get("encrypt_query_param"),
        image_item.get("encrypt_query_param"),
    )
    direct_url = _first_string(
        media.get("url"),
        media.get("full_url"),
        media.get("download_url"),
        image_item.get("url"),
    )
    if not encrypt_query_param and not direct_url:
        return None
    aes_key = _first_string(
        image_item.get("aeskey"),
        image_item.get("aes_key"),
        media.get("aes_key"),
        media.get("aeskey"),
    )
    expected_size = 0
    for value in (
        image_item.get("hd_size"),
        image_item.get("mid_size"),
        image_item.get("raw_size"),
        media.get("size"),
    ):
        try:
            expected_size = max(expected_size, int(value or 0))
        except (TypeError, ValueError):
            pass
    try:
        encrypt_type = int(media.get("encrypt_type") or 0)
    except (TypeError, ValueError):
        encrypt_type = 0
    return InboundImage(
        encrypt_query_param=encrypt_query_param,
        aes_key=aes_key,
        direct_url=direct_url,
        encrypt_type=encrypt_type,
        expected_size=expected_size,
    )


def _safe_inbound_filename(value: Any) -> str:
    name = str(value or "").replace("\\", "/").split("/")[-1]
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip()
    return name[:255] or "未命名文件"


def _extract_inbound_file(item: dict[str, Any]) -> InboundFile | None:
    file_item = _mapping(item.get("file_item"))
    if not file_item:
        return None
    media = _mapping(file_item.get("media"))
    encrypt_query_param = _first_string(
        media.get("encrypt_query_param"),
        file_item.get("encrypt_query_param"),
    )
    direct_url = _first_string(
        media.get("full_url"),
        media.get("url"),
        media.get("download_url"),
        file_item.get("url"),
    )
    if not encrypt_query_param and not direct_url:
        return None
    aes_key = _first_string(
        file_item.get("aeskey"),
        file_item.get("aes_key"),
        media.get("aes_key"),
        media.get("aeskey"),
    )
    try:
        expected_size = max(0, int(file_item.get("len") or media.get("size") or 0))
    except (TypeError, ValueError):
        expected_size = 0
    try:
        encrypt_type = int(media.get("encrypt_type") or 0)
    except (TypeError, ValueError):
        encrypt_type = 0
    return InboundFile(
        file_name=_safe_inbound_filename(file_item.get("file_name")),
        encrypt_query_param=encrypt_query_param,
        aes_key=aes_key,
        direct_url=direct_url,
        encrypt_type=encrypt_type,
        expected_size=expected_size,
        md5=_first_string(file_item.get("md5")),
    )


def extract_inbound_message(message: dict[str, Any]) -> InboundMessage | None:
    """Accept completed/new user text and images; bot messages never trigger replies."""

    if int(message.get("message_type") or 0) != 1:
        return None
    if int(message.get("message_state") or 0) == 1:
        return None
    from_user_id = str(message.get("from_user_id") or "")
    context_token = str(message.get("context_token") or "")
    if not from_user_id or not context_token:
        return None

    text_parts: list[str] = []
    images: list[InboundImage] = []
    files: list[InboundFile] = []
    for item in message.get("item_list") or []:
        if not isinstance(item, dict):
            continue
        item_type = int(item.get("type") or 0)
        if item_type == 1 or item.get("text_item"):
            text = str((item.get("text_item") or {}).get("text") or "").strip()
            if text:
                text_parts.append(text)
        if item_type == 2 or item.get("image_item"):
            image = _extract_inbound_image(item)
            if image is not None:
                images.append(image)
        if item_type == 4 or item.get("file_item"):
            inbound_file = _extract_inbound_file(item)
            if inbound_file is not None:
                files.append(inbound_file)
    text = "\n".join(text_parts).strip()
    if not text and not images and not files:
        return None

    message_id = str(message.get("message_id") or "")
    create_time_ms = int(message.get("create_time_ms") or 0)
    image_fingerprints = [
        image.encrypt_query_param or image.direct_url for image in images
    ]
    file_fingerprints = [
        file.encrypt_query_param or file.direct_url for file in files
    ]
    stable_source = "|".join(
        [
            message_id,
            from_user_id,
            str(create_time_ms),
            context_token,
            text,
            *image_fingerprints,
            *file_fingerprints,
        ]
    )
    key = hashlib.sha256(stable_source.encode("utf-8")).hexdigest()
    return InboundMessage(
        key=key,
        from_user_id=from_user_id,
        text=text,
        context_token=context_token,
        message_id=message_id,
        create_time_ms=create_time_ms,
        images=tuple(images),
        files=tuple(files),
    )


def extract_inbound_text(message: dict[str, Any]) -> InboundText | None:
    """Compatibility helper that keeps the former text-only behavior."""

    inbound = extract_inbound_message(message)
    return inbound if inbound is not None and inbound.text else None


class RecentMessageKeys:
    def __init__(self, initial: Iterable[str] = (), max_entries: int = 500) -> None:
        self.max_entries = max_entries
        self._order = deque((str(item) for item in initial), maxlen=max_entries)
        self._seen = set(self._order)

    def contains(self, key: str) -> bool:
        return key in self._seen

    def add(self, key: str) -> None:
        if key in self._seen:
            return
        if len(self._order) == self.max_entries:
            self._seen.discard(self._order[0])
        self._order.append(key)
        self._seen.add(key)

    def as_list(self) -> list[str]:
        return list(self._order)
