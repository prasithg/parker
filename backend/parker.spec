# PyInstaller spec for the Parker engine sidecar (`make sidecar`).
#
# onedir, not onefile: the natives (ctranslate2, PortAudio, ffmpeg via
# PyAV, onnxruntime for the whisper VAD) ship as real dylibs next to the
# executable, and the menu-bar shell respawning a crashed engine must
# not pay a self-extract on every start (docs/desktop-architecture.md).
#
# Build: cd backend && .venv/bin/pyinstaller parker.spec --noconfirm
# Output: backend/dist/parker/parker (+ _internal/)
# Verify: scripts/sidecar_smoke.sh — clean shell, /health, selftest, doctor.

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
)

hiddenimports = [
    # uvicorn.run("app.main:app") imports the app by string — invisible
    # to static analysis, so name the whole app package explicitly.
    "app.main",
    *collect_submodules("app"),
    # uvicorn's loop/protocol/lifespan classes load by config string.
    "uvicorn.logging",
    "uvicorn.loops.auto",
    "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan.on",
    # anyio backend is chosen at runtime by starlette.
    "anyio._backends._asyncio",
]

datas = [
    # faster-whisper ships the silero VAD onnx as package data.
    *collect_data_files("faster_whisper"),
]

binaries = [
    # Belt and braces for the two fiddly natives: the wheels carry their
    # dylibs inside the package; make sure they are collected even if
    # binary dependency analysis misses a link.
    *collect_dynamic_libs("ctranslate2"),
    *collect_dynamic_libs("sounddevice"),
]

a = Analysis(
    ["pyinstaller_entry.py"],
    pathex=["."],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Test-only / never imported by the engine at runtime.
        "pytest",
        "_pytest",
        "tkinter",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="parker",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="parker",
)
