"""Human-readable, per-run chat logging."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import threading


class ChatLogger:
    """Write one UTF-8 log file for each bot run."""

    def __init__(self, directory: str | Path = "chat_logs") -> None:
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        started_at = datetime.now()
        self.path = self.directory / started_at.strftime(
            "chat_%Y-%m-%d_%H-%M-%S.log"
        )
        self._lock = threading.Lock()
        self._write("SYSTEM", "程序启动，开始记录本次会话")

    def _write(self, role: str, content: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        safe_content = str(content).replace("\r\n", "\n").replace("\r", "\n")
        indented = safe_content.replace("\n", "\n    ")
        line = f"[{timestamp}] [{role}]\n    {indented}\n\n"
        with self._lock:
            with self.path.open("a", encoding="utf-8", newline="\n") as file:
                file.write(line)

    def user(self, content: str) -> None:
        self._write("USER", content)

    def assistant(self, content: str) -> None:
        self._write("ASSISTANT", content)

    def tool(self, name: str, arguments: str, result: str) -> None:
        self._write(
            f"TOOL:{name}",
            f"参数: {arguments}\n结果: {result}",
        )

    def system(self, content: str) -> None:
        self._write("SYSTEM", content)

    def error(self, content: str) -> None:
        self._write("ERROR", content)

