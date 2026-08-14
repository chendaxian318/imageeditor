"""Reject production bundles that accidentally include hidden product areas."""

from __future__ import annotations

import argparse
import ast
import json
from pathlib import Path
from runpy import run_path

ROOT = Path(__file__).resolve().parents[1]
BUILD_CONFIG = run_path(str(ROOT / "packaging" / "windows" / "viewer_build_config.py"))
HIDDEN_DEVELOPMENT_MODULES = BUILD_CONFIG["HIDDEN_DEVELOPMENT_MODULES"]
UNUSED_QT_MODULES = BUILD_CONFIG["UNUSED_QT_MODULES"]
EXCLUDED_BUNDLE_PATHS = BUILD_CONFIG["EXCLUDED_BUNDLE_PATHS"]

REQUIRED_MODULES = (
    "light_image_editor.app",
    "light_image_editor.commands",
    "light_image_editor.identity",
    "light_image_editor.image_loader",
    "light_image_editor.pdf_support",
    "light_image_editor.psd_support",
    "light_image_editor.qt_viewer",
    "PIL",
    "psd_tools",
    "pypdfium2",
)
REQUIRED_FILES = (
    "CSImage.exe",
    "_internal/PySide6/Qt6Core.dll",
    "_internal/PySide6/Qt6Gui.dll",
    "_internal/PySide6/Qt6Widgets.dll",
    "_internal/pypdfium2_raw/pdfium.dll",
)
MAX_BUNDLE_BYTES = 145 * 1024 * 1024
MAX_BUNDLE_FILES = 400


def _module_names(pyz_toc: Path) -> set[str]:
    parsed = ast.literal_eval(pyz_toc.read_text(encoding="utf-8"))
    return {entry[0] for entry in parsed[1] if isinstance(entry, tuple) and entry}


def _bundle_paths(app_dir: Path) -> tuple[set[str], int]:
    files = tuple(path for path in app_dir.rglob("*") if path.is_file())
    normalized: set[str] = set()
    for path in files:
        relative = path.relative_to(app_dir).as_posix().casefold()
        if relative.startswith("_internal/"):
            relative = relative.removeprefix("_internal/")
        normalized.add(relative)
    return normalized, sum(path.stat().st_size for path in files)


def verify_viewer_bundle(app_dir: Path, pyz_toc: Path) -> dict[str, object]:
    problems: list[str] = []
    module_names = _module_names(pyz_toc)
    bundle_paths, bundle_bytes = _bundle_paths(app_dir)
    file_count = sum(1 for path in app_dir.rglob("*") if path.is_file())

    for module in REQUIRED_MODULES:
        if module not in module_names:
            problems.append(f"required runtime module is missing: {module}")

    forbidden_modules = (
        "tkinter",
        "_tkinter",
        *HIDDEN_DEVELOPMENT_MODULES,
        *UNUSED_QT_MODULES,
    )
    for module in forbidden_modules:
        if module in module_names or any(name.startswith(f"{module}.") for name in module_names):
            problems.append(f"hidden module was bundled: {module}")

    for relative in REQUIRED_FILES:
        if not (app_dir / Path(relative)).is_file():
            problems.append(f"required runtime file is missing: {relative}")

    for relative in sorted(EXCLUDED_BUNDLE_PATHS & bundle_paths):
        problems.append(f"optional Qt artifact was bundled: {relative}")

    if bundle_bytes > MAX_BUNDLE_BYTES:
        problems.append(f"bundle is too large: {bundle_bytes} bytes > {MAX_BUNDLE_BYTES} bytes")
    if file_count > MAX_BUNDLE_FILES:
        problems.append(f"bundle has too many files: {file_count} > {MAX_BUNDLE_FILES}")

    return {
        "status": "passed" if not problems else "failed",
        "bundle_bytes": bundle_bytes,
        "bundle_mib": round(bundle_bytes / (1024 * 1024), 1),
        "file_count": file_count,
        "module_count": len(module_names),
        "problems": problems,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--pyz-toc", type=Path, required=True)
    args = parser.parse_args()

    result = verify_viewer_bundle(args.app_dir.resolve(), args.pyz_toc.resolve())
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
