# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec file for Office Claw Sidecar.

Builds a standalone executable that Tauri can launch as a sidecar.
Output is named with the platform target triple to match Tauri's
externalBin naming convention.

Key considerations:
- Hidden imports for keyring backends (OS-specific credential stores)
- Hidden imports for uvicorn internals (dynamic imports)
- console=False to prevent a console window on Windows
"""
import platform
import sys

block_cipher = None

# Determine the Tauri-compatible target triple
TARGET_TRIPLES = {
    ("Windows", "AMD64"):   "x86_64-pc-windows-msvc",
    ("Windows", "x86_64"):  "x86_64-pc-windows-msvc",
    ("Darwin",  "x86_64"):  "x86_64-apple-darwin",
    ("Darwin",  "arm64"):   "aarch64-apple-darwin",
    ("Linux",   "x86_64"):  "x86_64-unknown-linux-gnu",
    ("Linux",   "aarch64"): "aarch64-unknown-linux-gnu",
}

system = platform.system()
machine = platform.machine()
if system == "Windows" and machine == "x86_64":
    machine = "AMD64"

triple = TARGET_TRIPLES.get((system, machine), f"unknown-{system}-{machine}")
exe_name = f"office-claw-sidecar-{triple}"

a = Analysis(
    ['office_claw_sidecar/main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        # Keyring backends
        'keyring.backends.Windows',
        'keyring.backends.macOS',
        'keyring.backends.SecretService',
        # uvicorn dynamically imports these at runtime
        'uvicorn.logging',
        'uvicorn.loops.auto',
        'uvicorn.loops.asyncio',
        'uvicorn.protocols.http.auto',
        'uvicorn.protocols.http.h11_impl',
        'uvicorn.protocols.websockets.auto',
        'uvicorn.lifespan.on',
        'uvicorn.lifespan.off',
        # httpx/httpcore transports
        'httpcore._backends.auto',
        'httpcore._backends.anyio',
        # python-telegram-bot
        'telegram',
        'telegram.ext',
        # google-api-python-client (httplib2 uses pyparsing which needs unittest)
        'unittest',
        'httplib2',
        'pyparsing',
        'googleapiclient',
        'google.auth',
        'google.auth.transport.requests',
        'google.oauth2.credentials',
        'google_auth_oauthlib.flow',
        # pandas / openpyxl / xlsxwriter (Excel AI)
        'pandas',
        'pandas._libs.tslibs.base',
        'openpyxl',
        'xlsxwriter',
        # python-docx / reportlab (Document AI)
        'docx',
        'reportlab',
        'reportlab.pdfgen',
        'reportlab.lib.pagesizes',
        'reportlab.platypus',
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        'tkinter',
        'matplotlib',
        'scipy',
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=exe_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No console window on Windows
    icon=None,
)
