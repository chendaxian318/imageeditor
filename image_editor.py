"""Backward-compatible script entry point for Light Image Editor.

New code should import from :mod:`light_image_editor`, while this module stays
in place for existing tests, shortcuts, and PyInstaller build definitions.
"""

from light_image_editor.app import main
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


if __name__ == "__main__":
    raise SystemExit(main())
