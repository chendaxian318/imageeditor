"""Backward-compatible script entry point for Light Image Editor.

New code should import from :mod:`light_image_editor`, while this module stays
in place for existing tests, shortcuts, and PyInstaller build definitions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from light_image_editor.app import main

if TYPE_CHECKING:
    from light_image_editor.files import copy_image_to_clipboard, validate_filename_affix
    from light_image_editor.settings import CONFIG_PATH
    from light_image_editor.ui import ImageEditorApp

__all__ = [
    "CONFIG_PATH",
    "ImageEditorApp",
    "copy_image_to_clipboard",
    "main",
    "validate_filename_affix",
]


def __getattr__(name: str) -> Any:
    """Load legacy compatibility exports only when a caller actually uses them."""

    value: Any
    if name == "ImageEditorApp":
        from light_image_editor.ui import ImageEditorApp

        value = ImageEditorApp
    elif name in {"copy_image_to_clipboard", "validate_filename_affix"}:
        from light_image_editor import files

        value = getattr(files, name)
    elif name == "CONFIG_PATH":
        from light_image_editor.settings import CONFIG_PATH

        value = CONFIG_PATH
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


if __name__ == "__main__":
    raise SystemExit(main())
