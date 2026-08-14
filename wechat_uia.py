"""Minimal WeChat 4.x adapter built directly on Windows UI Automation.

Only the small API surface needed by the bot is implemented. The adapter does
not parse WeChat's session list, inject code, or depend on wxauto.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Literal

from pywinauto import Desktop
import win32api
import win32con
import win32gui
import win32process


CHAT_INPUT_ID = "chat_input_field"
CHAT_LIST_ID = "chat_message_list"
CHAT_NAME_ID_SUFFIX = "current_chat_name_label"
SEARCH_LIST_ID = "search_list"
SYSTEM_TIME_PATTERN = re.compile(
    r"^(?:(?:今天|昨天|前天|星期[一二三四五六日天]|周[一二三四五六日天])\s*)?"
    r"(?:(?:上午|下午|晚上|凌晨|中午)\s*)?\d{1,2}:\d{2}$"
)
SYSTEM_DATE_PATTERN = re.compile(
    r"^(?:\d{4}年)?\d{1,2}月\d{1,2}日"
    r"(?:\s*(?:(?:上午|下午|晚上|凌晨|中午)\s*)?\d{1,2}:\d{2})?$"
)


class WeChatUIAError(RuntimeError):
    """Raised when an expected WeChat window or control cannot be found."""


@dataclass(frozen=True)
class Message:
    """A visible message in the current chat."""

    id: tuple[int, ...] | tuple[str, int]
    type: Literal["friend", "self", "system"]
    content: str
    sender: str | None = None


class WeChat:
    """Small WeChat 4.x client backed by Microsoft's UI Automation API."""

    def __init__(
        self,
        wechat_exe: str | os.PathLike[str] | None = None,
        timeout: float = 10,
        **_: object,
    ) -> None:
        configured_path = wechat_exe or os.getenv("WECHAT_EXE")
        self.wechat_exe = Path(configured_path) if configured_path else None
        self.timeout = timeout
        self._last_restore_attempt = 0.0
        self._recent_outgoing: deque[tuple[str, float]] = deque()
        self._direction_cache: dict[
            tuple[tuple[int, ...] | tuple[str, int], str],
            Literal["friend", "self", "system"],
        ] = {}
        self._window = self._connect_window()
        self._main_hwnd = self._window.handle
        self.BringToFront()

    @staticmethod
    def _visible_windows():
        return Desktop(backend="uia").windows(title="微信", control_type="Window")

    def _connect_window(self):
        windows = self._visible_windows()
        if not windows:
            self._restore_window()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                time.sleep(0.25)
                windows = self._visible_windows()
                if windows:
                    break

        if not windows:
            configured = str(self.wechat_exe) if self.wechat_exe else "未设置"
            raise WeChatUIAError(
                "请先打开微信主窗口，或在 .env 中设置 WECHAT_EXE。"
                f"当前配置：{configured}"
            )

        self._window = self._largest_window(windows)
        return self._window

    @staticmethod
    def _largest_window(windows):
        return max(
            windows,
            key=lambda window: window.rectangle().width()
            * window.rectangle().height(),
        )

    def _restore_window(self) -> None:
        candidates = [
            self.wechat_exe,
            Path(r"D:\wechat\Weixin\Weixin.exe"),
            Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
            / "Tencent"
            / "Weixin"
            / "Weixin.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"))
            / "Tencent"
            / "Weixin"
            / "Weixin.exe",
        ]
        executable = next(
            (path for path in candidates if path is not None and path.is_file()),
            None,
        )
        if executable is not None:
            subprocess.Popen([str(executable)], close_fds=True)

    def _refresh_window(self):
        windows = self._visible_windows()
        if not windows:
            return self._connect_window()
        self._window = self._largest_window(windows)
        self._main_hwnd = self._window.handle
        return self._window

    @staticmethod
    def _wechat_window_handles() -> list[int]:
        handles: list[int] = []

        def collect(hwnd: int, _: object) -> None:
            if win32gui.GetWindowText(hwnd) == "微信":
                handles.append(hwnd)

        win32gui.EnumWindows(collect, None)
        return handles

    @staticmethod
    def _largest_handle(handles: list[int]) -> int | None:
        def area(hwnd: int) -> int:
            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            return max(0, right - left) * max(0, bottom - top)

        return max(handles, key=area) if handles else None

    def BringToFront(self) -> bool:
        """Restore WeChat and make it the active foreground window."""

        hwnd = getattr(self, "_main_hwnd", 0)
        if not hwnd or not win32gui.IsWindow(hwnd):
            hwnd = self._largest_handle(self._wechat_window_handles()) or 0

        if not hwnd:
            now = time.monotonic()
            if now - self._last_restore_attempt >= 2:
                self._last_restore_attempt = now
                self._restore_window()
            return False

        self._main_hwnd = hwnd
        foreground_hwnd = win32gui.GetForegroundWindow()
        current_thread = win32api.GetCurrentThreadId()
        foreground_thread = (
            win32process.GetWindowThreadProcessId(foreground_hwnd)[0]
            if foreground_hwnd
            else 0
        )
        target_thread = win32process.GetWindowThreadProcessId(hwnd)[0]
        attached_threads: list[int] = []
        try:
            # Windows normally blocks a background process from stealing focus.
            # Sharing input queues temporarily gives this watchdog permission to
            # restore the explicitly requested always-foreground window.
            for thread_id in {foreground_thread, target_thread}:
                if thread_id and thread_id != current_thread:
                    win32process.AttachThreadInput(current_thread, thread_id, True)
                    attached_threads.append(thread_id)
            if win32gui.IsIconic(hwnd) or not win32gui.IsWindowVisible(hwnd):
                win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            return win32gui.GetForegroundWindow() == hwnd
        except win32gui.error:
            # Windows may reject SetForegroundWindow. A temporary topmost toggle
            # makes the window visible, and the next watchdog pass retries focus.
            flags = win32con.SWP_NOMOVE | win32con.SWP_NOSIZE | win32con.SWP_SHOWWINDOW
            try:
                win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0, flags)
                win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0, flags)
            except win32gui.error:
                return False
            return win32gui.GetForegroundWindow() == hwnd
        finally:
            for thread_id in reversed(attached_threads):
                try:
                    win32process.AttachThreadInput(current_thread, thread_id, False)
                except win32gui.error:
                    pass

    def Close(self) -> None:
        """Release adapter resources without changing the WeChat window."""

    def _control_by_id(self, automation_id: str):
        controls = [
            control
            for control in self._refresh_window().descendants()
            if control.element_info.automation_id == automation_id
        ]
        if not controls:
            raise WeChatUIAError(f"微信控件不存在：{automation_id}")
        return controls[0]

    def _current_chat_name(self) -> str:
        labels = [
            control
            for control in self._refresh_window().descendants(control_type="Text")
            if control.element_info.automation_id.endswith(CHAT_NAME_ID_SUFFIX)
        ]
        return labels[0].window_text().strip() if labels else ""

    def ChatInfo(self) -> dict[str, str]:
        return {"chat_type": "unknown", "chat_name": self._current_chat_name()}

    def ChatWith(self, who: str, exact: bool = True, **_: object) -> str:
        """Switch chat through search without parsing the session list."""

        who = who.strip()
        if not who:
            raise ValueError("联系人昵称不能为空")
        if self._current_chat_name() == who:
            return who

        search_edits = [
            control
            for control in self._refresh_window().descendants(control_type="Edit")
            if control.element_info.automation_id != CHAT_INPUT_ID
        ]
        if not search_edits:
            raise WeChatUIAError("找不到微信搜索框")

        search = search_edits[0]
        search.set_edit_text(who)
        deadline = time.monotonic() + self.timeout
        chosen = None
        try:
            while time.monotonic() < deadline:
                time.sleep(0.2)
                results = [
                    control
                    for control in self._refresh_window().descendants(
                        control_type="ListItem"
                    )
                    if control.parent().element_info.automation_id == SEARCH_LIST_ID
                ]
                if exact:
                    matches = [
                        item for item in results if item.window_text().strip() == who
                    ]
                else:
                    needle = who.casefold()
                    matches = [
                        item
                        for item in results
                        if needle in item.window_text().strip().casefold()
                    ]
                if matches:
                    chosen = matches[0]
                    break

            if chosen is None:
                raise WeChatUIAError(f"找不到微信联系人：{who}")

            chosen.click_input()
            deadline = time.monotonic() + self.timeout
            while time.monotonic() < deadline:
                time.sleep(0.2)
                current = self._current_chat_name()
                if current == who or (not exact and who.casefold() in current.casefold()):
                    return current
            raise WeChatUIAError(f"切换微信联系人超时：{who}")
        except Exception:
            try:
                search.set_edit_text("")
            except Exception:
                pass
            raise

    @staticmethod
    def _pixel_score(image, box: tuple[int, int, int, int], background) -> int:
        return sum(
            1
            for pixel in image.crop(box).getdata()
            if sum(abs(pixel[channel] - background[channel]) for channel in range(3))
            > 90
        )

    @staticmethod
    def _message_background(image) -> tuple[int, int, int]:
        """Estimate row background from its border instead of its bubble."""

        width, height = image.size
        samples = []
        for y in {0, min(2, height - 1), max(0, height - 3), height - 1}:
            samples.extend(image.getpixel((x, y)) for x in range(0, width, 4))
        return Counter(samples).most_common(1)[0][0]

    @staticmethod
    def _normalize_message_content(content: str) -> str:
        return content.replace("\r\n", "\n").replace("\r", "\n").strip()

    @staticmethod
    def _is_system_message_content(content: str) -> bool:
        normalized = " ".join(content.split())
        return bool(
            SYSTEM_TIME_PATTERN.fullmatch(normalized)
            or SYSTEM_DATE_PATTERN.fullmatch(normalized)
        )

    def _remember_outgoing(self, content: str) -> None:
        normalized = self._normalize_message_content(content)
        now = time.monotonic()
        self._recent_outgoing.append((normalized, now))
        self._purge_recent_outgoing(now)

    def _purge_recent_outgoing(self, now: float | None = None) -> None:
        current = time.monotonic() if now is None else now
        while self._recent_outgoing and current - self._recent_outgoing[0][1] > 180:
            self._recent_outgoing.popleft()

    def _is_recent_outgoing(self, content: str) -> bool:
        self._purge_recent_outgoing()
        normalized = self._normalize_message_content(content)
        return any(text == normalized for text, _ in self._recent_outgoing)

    def _message_direction(self, item) -> Literal["friend", "self", "system"]:
        """Classify a row from the avatar shown at its left or right edge."""

        image = item.capture_as_image().convert("RGB")
        width, height = image.size
        if width < 40 or height < 12:
            return "system"

        background = self._message_background(image)
        edge_width = min(70, width // 4)
        bottom = max(6, height - 5)
        left = self._pixel_score(image, (8, 5, edge_width, bottom), background)
        right = self._pixel_score(
            image,
            (width - edge_width, 5, width - 8, bottom),
            background,
        )

        if left > max(80, right * 1.4):
            return "friend"
        if right > max(80, left * 1.4):
            return "self"
        return "system"

    def GetAllMessage(self) -> list[Message]:
        """Return the currently visible messages from the selected chat."""

        message_list = self._control_by_id(CHAT_LIST_ID)
        chat_name = self._current_chat_name()
        messages: list[Message] = []
        for index, item in enumerate(message_list.children(control_type="ListItem")):
            content = item.window_text().strip()
            if not content:
                continue
            runtime_id = getattr(item.element_info, "runtime_id", None)
            message_id = (
                tuple(runtime_id)
                if runtime_id
                else (content, index)
            )
            cache_key = (message_id, content)
            if self._is_system_message_content(content):
                direction = "system"
            elif self._is_recent_outgoing(content):
                direction = "self"
            else:
                direction = self._direction_cache.get(cache_key)
            if direction is None:
                direction = self._message_direction(item)
            self._direction_cache[cache_key] = direction
            if len(self._direction_cache) > 5000:
                self._direction_cache.clear()
                self._direction_cache[cache_key] = direction
            messages.append(
                Message(
                    id=message_id,
                    type=direction,
                    content=content,
                    sender=chat_name if direction == "friend" else None,
                )
            )
        return messages

    def SendMsg(self, msg: str, who: str | None = None, **_: object) -> None:
        """Send a text message to the current or requested chat."""

        if who:
            self.ChatWith(who)
        if not isinstance(msg, str) or not msg:
            raise ValueError("发送内容不能为空")

        message_list = self._control_by_id(CHAT_LIST_ID)
        old_item_ids = {
            tuple(runtime_id)
            for item in message_list.children(control_type="ListItem")
            if (runtime_id := getattr(item.element_info, "runtime_id", None))
        }

        editor = self._control_by_id(CHAT_INPUT_ID)
        editor.set_edit_text(msg)

        # Setting a UIA value does not always notify WeChat's Qt layer. A
        # space/backspace pair preserves the text while triggering its normal
        # input event, which enables the Send button reliably.
        editor.click_input()
        editor.type_keys("{SPACE}{BACKSPACE}", pause=0.03)

        deadline = time.monotonic() + 5
        send_enabled = False
        while time.monotonic() < deadline:
            buttons = [
                control
                for control in self._refresh_window().descendants(
                    control_type="Button"
                )
                if control.window_text().startswith("发送")
            ]
            if buttons and buttons[0].is_enabled():
                send_enabled = True
                break
            time.sleep(0.1)

        if not send_enabled:
            raise WeChatUIAError("微信发送按钮未启用，消息已保留在输入框")

        # Use WeChat's own keyboard shortcut. This is more reliable than the
        # Qt button's InvokePattern and does not depend on screen coordinates.
        editor.type_keys("%s", pause=0.05)

        # WeChat's Qt accessibility tree can lag several seconds behind the
        # visible UI. Confirm against a newly created message row rather than
        # treating a temporarily stale input value as a failure.
        deadline = time.monotonic() + 12
        while time.monotonic() < deadline:
            time.sleep(0.25)
            current_list = self._control_by_id(CHAT_LIST_ID)
            for item in current_list.children(control_type="ListItem"):
                runtime_id = getattr(item.element_info, "runtime_id", None)
                item_id = tuple(runtime_id) if runtime_id else None
                if (
                    item_id is not None
                    and item_id not in old_item_ids
                    and item.window_text().strip() == msg
                ):
                    self._direction_cache[(item_id, msg)] = "self"
                    self._remember_outgoing(msg)
                    return

        draft = self._control_by_id(CHAT_INPUT_ID).window_text().strip()
        if not draft:
            # The input being cleared is a valid fallback when WeChat has not
            # exposed the new message row yet.
            self._remember_outgoing(msg)
            return
        raise WeChatUIAError("微信在 12 秒内没有确认发送，消息仍保留在输入框")
