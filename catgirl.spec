# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import sys


jpeg_dll = Path(sys.executable).resolve().parent / "Library" / "bin" / "jpeg8.dll"
extra_binaries = [(str(jpeg_dll), ".")] if jpeg_dll.is_file() else []

a = Analysis(
    ["my_catgirl.py"],
    pathex=[],
    binaries=extra_binaries,
    datas=[("聊天助手.txt", ".")],
    hiddenimports=["anyio._backends._asyncio"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # These packages exist in the development environment but are only
    # optional integrations of dependencies; the bot does not use them.
    excludes=[
        "IPython",
        "jedi",
        "matplotlib",
        "numpy",
        "pandas",
        "parso",
        "PyQt5",
        "scipy",
        "sqlalchemy",
        "tkinter",
        "zmq",
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
    name="Catgirl微信助手",
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
    name="Catgirl微信助手",
)
