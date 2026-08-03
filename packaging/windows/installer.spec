# -*- mode: python ; coding: utf-8 -*-
"""Deterministic PyInstaller recipe for the bootstrap installer executable."""

from pathlib import Path


ROOT = Path(SPECPATH).resolve().parents[1]
ICON = ROOT / "resources" / "icons" / "app.ico"
VERSION_INFO = ROOT / "build" / "version" / "ImageEditorSetup.version.txt"
APPLICATION_EXE = ROOT / "release" / "ImageEditor.exe"

a = Analysis(
    [str(ROOT / "installer.py")],
    pathex=[str(ROOT)],
    binaries=[(str(APPLICATION_EXE), ".")],
    datas=[
        (str(ROOT / "LICENSE"), "."),
        (str(ROOT / "NOTICE.md"), "."),
        (str(ROOT / "THIRD_PARTY_LICENSES.md"), "."),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ImageEditorSetup",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    icon=str(ICON),
    version=str(VERSION_INFO),
    codesign_identity=None,
    entitlements_file=None,
)
