# -*- mode: python ; coding: utf-8 -*-
"""Deterministic PyInstaller recipe for the portable one-file executable."""

from pathlib import Path


ROOT = Path(SPECPATH).resolve().parents[1]
ICON = ROOT / "resources" / "icons" / "app.ico"
VERSION_INFO = ROOT / "build" / "version" / "ImageEditor.version.txt"
LOCALES = ROOT / "resources" / "locales"
UPDATE_KEYS = ROOT / "resources" / "update"
PYPDFIUM_LICENSES = (
    ROOT / ".venv" / "Lib" / "site-packages" / "pypdfium2-5.12.1.dist-info" / "licenses"
)
PSDTOOLS_LICENSES = (
    ROOT / ".venv" / "Lib" / "site-packages" / "psd_tools-1.17.4.dist-info" / "licenses"
)
ATTRS_LICENSES = ROOT / ".venv" / "Lib" / "site-packages" / "attrs-26.1.0.dist-info" / "licenses"

a = Analysis(
    [str(ROOT / "image_editor.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[
        (str(LOCALES), "resources/locales"),
        (str(UPDATE_KEYS), "resources/update"),
        (str(PYPDFIUM_LICENSES), "licenses/pypdfium2"),
        (str(PSDTOOLS_LICENSES), "licenses/psd-tools"),
        (str(ATTRS_LICENSES), "licenses/attrs"),
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
    name="LightImageEditorPortable",
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
