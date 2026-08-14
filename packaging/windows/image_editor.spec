# -*- mode: python ; coding: utf-8 -*-
"""Deterministic PyInstaller recipe for the application executable."""

from pathlib import Path
from runpy import run_path


ROOT = Path(SPECPATH).resolve().parents[1]
ICON = ROOT / "resources" / "icons" / "app.ico"
VERSION_INFO = ROOT / "build" / "version" / "CSImage.version.txt"
LOCALES = ROOT / "resources" / "locales"
UPDATE_KEYS = ROOT / "resources" / "update"
PYPDFIUM_LICENSES = (
    ROOT / ".venv" / "Lib" / "site-packages" / "pypdfium2-5.12.1.dist-info" / "licenses"
)
PSDTOOLS_LICENSES = (
    ROOT / ".venv" / "Lib" / "site-packages" / "psd_tools-1.17.4.dist-info" / "licenses"
)
ATTRS_LICENSES = ROOT / ".venv" / "Lib" / "site-packages" / "attrs-26.1.0.dist-info" / "licenses"
BUILD_CONFIG = run_path(str(ROOT / "packaging" / "windows" / "viewer_build_config.py"))
VIEWER_ENTRYPOINT = BUILD_CONFIG["VIEWER_ENTRYPOINT"]
VIEWER_EXCLUDES = BUILD_CONFIG["VIEWER_EXCLUDES"]
KEEP_VIEWER_BUNDLE_ENTRY = BUILD_CONFIG["keep_viewer_bundle_entry"]

a = Analysis(
    [str(ROOT / VIEWER_ENTRYPOINT)],
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
    excludes=list(VIEWER_EXCLUDES),
    noarchive=False,
    optimize=0,
)
a.binaries = [entry for entry in a.binaries if KEEP_VIEWER_BUNDLE_ENTRY(entry)]
a.datas = [entry for entry in a.datas if KEEP_VIEWER_BUNDLE_ENTRY(entry)]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    name="CSImage",
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
