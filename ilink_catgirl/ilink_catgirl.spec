# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys

from PyInstaller.utils.hooks import collect_all


ILINK_DIR = Path(SPECPATH).resolve()
PROJECT_ROOT = ILINK_DIR.parent

mcp_datas, mcp_binaries, mcp_hiddenimports = collect_all("mcp")
jpeg_dll = Path(sys.executable).resolve().parent / "Library" / "bin" / "jpeg8.dll"
image_binaries = [(str(jpeg_dll), ".")] if jpeg_dll.is_file() else []

a = Analysis(
    [str(ILINK_DIR / "main.py")],
    pathex=[str(ILINK_DIR), str(PROJECT_ROOT)],
    binaries=[*mcp_binaries, *image_binaries],
    datas=[
        *mcp_datas,
        (str(PROJECT_ROOT / "聊天助手.txt"), "."),
        (str(ILINK_DIR / "主动问候语.txt"), "."),
        (str(ILINK_DIR / "定时提醒开场白.txt"), "."),
        (str(ILINK_DIR / "mcp_servers.example.json"), "."),
    ],
    hiddenimports=[
        *mcp_hiddenimports,
        "anyio._backends._asyncio",
        "chat_logger",
        "weather_mcp_client",
        "weather_mcp_server",
        "web_mcp_client",
        "web_mcp_server",
        "document_mcp_client",
        "document_mcp_server",
        "email_mcp_client",
        "email_mcp_server",
        "pypdf",
        "docx",
        "openpyxl",
        "pptx",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "IPython",
        "jedi",
        "matplotlib",
        "numpy",
        "pandas",
        "PyQt5",
        "scipy",
        "sqlalchemy",
        "tkinter",
    ],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Catgirl微信机器人",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Catgirl微信机器人",
)
