# -*- mode: python ; coding: utf-8 -*-
"""Deterministic PyInstaller recipe for the application executable."""

from pathlib import Path


ROOT = Path(SPECPATH).resolve().parents[1]
ICON = ROOT / "resources" / "icons" / "app.ico"
VERSION_INFO = ROOT / "build" / "version" / "ImageEditor.version.txt"
LOCALES = ROOT / "resources" / "locales"

a = Analysis(
    [str(ROOT / "image_editor.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(LOCALES), "resources/locales")],
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
    [],
    name="ImageEditor",
    exclude_binaries=True,
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

collect = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="LightImageEditor",
)
