"""Executable entrypoint for the lightweight Catgirl Weixin bot."""

from __future__ import annotations

from datetime import datetime
import os
from pathlib import Path
import sys
import traceback


PROJECT_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)


def configure_console() -> None:
    if os.name == "nt":
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is not None and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def record_fatal_error(error: BaseException) -> Path | None:
    log_dir = PROJECT_DIR / "logs"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / datetime.now().strftime("crash_%Y-%m-%d_%H-%M-%S.log")
        path.write_text(
            "".join(traceback.format_exception(type(error), error, error.__traceback__)),
            encoding="utf-8",
        )
        return path
    except OSError:
        return None


def should_pause() -> bool:
    value = os.getenv("PAUSE_ON_ERROR", "true").strip().lower()
    return os.name == "nt" and value not in {"0", "false", "no", "off"}


def run_internal_server() -> bool:
    """Run an MCP subprocess inside the frozen executable when requested."""

    arguments = set(sys.argv[1:])
    if "--weather-mcp-server" in arguments:
        from weather_mcp_server import run_server

        run_server()
        return True
    if "--web-mcp-server" in arguments:
        from web_mcp_server import run_server

        run_server()
        return True
    if "--document-mcp-server" in arguments:
        from document_mcp_server import run_server

        run_server()
        return True
    return False


def main() -> int:
    configure_console()
    internal_server = any(
        argument
        in {
            "--weather-mcp-server",
            "--web-mcp-server",
            "--document-mcp-server",
        }
        for argument in sys.argv[1:]
    )
    try:
        if run_internal_server():
            return 0
        from bot import run_bot

        run_bot()
        return 0
    except KeyboardInterrupt:
        print("\n程序已由用户停止。")
        return 0
    except BaseException as error:
        path = record_fatal_error(error)
        print(f"程序遇到致命错误：{error}", file=sys.stderr)
        if path:
            print(f"完整崩溃记录：{path}", file=sys.stderr)
        if should_pause() and not internal_server:
            try:
                input("按回车键关闭窗口……")
            except (EOFError, OSError):
                pass
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
