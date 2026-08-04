"""Built-in export presets expressed as document-state changes."""

from __future__ import annotations

from dataclasses import replace

from .document import DocumentState

EXPORT_PRESETS = ("自定义", "社交媒体", "头像", "邮件", "原质量")


def apply_export_preset(
    state: DocumentState, preset: str, source_format: str | None
) -> DocumentState:
    if preset == "社交媒体":
        return replace(
            state,
            export_preset=preset,
            output_format="JPEG",
            quality=88,
            target_size_text="",
            width_text="",
            height_text="",
            max_side_text="2048",
            preserve_metadata=True,
        )
    if preset == "头像":
        return replace(
            state,
            export_preset=preset,
            output_format="PNG",
            quality=95,
            target_size_text="",
            width_text="1024",
            height_text="1024",
            max_side_text="",
            preserve_metadata=True,
        )
    if preset == "邮件":
        return replace(
            state,
            export_preset=preset,
            output_format="JPEG",
            quality=80,
            target_size_text="",
            width_text="",
            height_text="",
            max_side_text="1600",
            preserve_metadata=False,
        )
    if preset == "原质量":
        output_format = (
            source_format if source_format in {"JPEG", "PNG", "WEBP", "BMP", "TIFF"} else "PNG"
        )
        return replace(
            state,
            export_preset=preset,
            output_format=output_format,
            quality=95,
            target_size_text="",
            width_text="",
            height_text="",
            max_side_text="",
            preserve_metadata=True,
        )
    if preset != "自定义":
        raise ValueError(f"未知导出预设：{preset}")
    return replace(state, export_preset="自定义")
