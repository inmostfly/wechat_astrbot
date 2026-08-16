"""Minimal synchronous client for Tencent's Weixin iLink Bot HTTP API."""

from __future__ import annotations

import base64
from collections import deque
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import random
import re
import time
from typing import Any, Iterable
from urllib.parse import urljoin
from uuid import uuid4

import httpx


DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
DEFAULT_BOT_TYPE = "3"
CHANNEL_VERSION = "2.4.6"
ILINK_APP_ID = "bot"
ILINK_APP_CLIENT_VERSION = "132102"
BOT_AGENT = "CatgirlLite/0.1.0"


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
class InboundText:
    key: str
    from_user_id: str
    text: str
    context_token: str
    message_id: str
    create_time_ms: int


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


def extract_inbound_text(message: dict[str, Any]) -> InboundText | None:
    """Accept only completed/new user text; bot messages can never trigger replies."""

    if int(message.get("message_type") or 0) != 1:
        return None
    if int(message.get("message_state") or 0) == 1:
        return None
    from_user_id = str(message.get("from_user_id") or "")
    context_token = str(message.get("context_token") or "")
    if not from_user_id or not context_token:
        return None

    text_parts: list[str] = []
    for item in message.get("item_list") or []:
        if int(item.get("type") or 0) != 1:
            continue
        text = str((item.get("text_item") or {}).get("text") or "").strip()
        if text:
            text_parts.append(text)
    text = "\n".join(text_parts).strip()
    if not text:
        return None

    message_id = str(message.get("message_id") or "")
    create_time_ms = int(message.get("create_time_ms") or 0)
    stable_source = "|".join(
        [message_id, from_user_id, str(create_time_ms), context_token, text]
    )
    key = hashlib.sha256(stable_source.encode("utf-8")).hexdigest()
    return InboundText(
        key=key,
        from_user_id=from_user_id,
        text=text,
        context_token=context_token,
        message_id=message_id,
        create_time_ms=create_time_ms,
    )


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
