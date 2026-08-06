"""Background rendering for responsive large-image previews."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageCms

from image_ops import EditState, apply_edits
from light_image_editor.commands import EditTimeline
from light_image_editor.document import metadata_from_image
from light_image_editor.domain.recipe_executor import execute_transform_recipe
from light_image_editor.domain.recipes import TransformRecipe
from light_image_editor.renderer import render_image

_DISPLAY_PROFILE = ImageCms.createProfile("sRGB")


@dataclass(frozen=True)
class SplitPreviewLayout:
    """Geometry of two complete before/after panels inside a preview bitmap."""

    divider_x: int
    before_panel: tuple[int, int, int, int]
    after_panel: tuple[int, int, int, int]


@dataclass(frozen=True)
class PreviewFrame:
    """A UI-sized bitmap and the working image coordinates it represents."""

    working: Image.Image
    image: Image.Image
    scale: float
    owns_working: bool = False
    split_layout: SplitPreviewLayout | None = None
    split_labels: tuple[str, str] | None = None

    def close(self) -> None:
        """Release worker-owned pixels when a stale preview is discarded."""

        self.image.close()
        if self.owns_working:
            self.working.close()


def render_preview(
    original: Image.Image,
    edit_state: EditState,
    *,
    compare_original: bool,
    canvas_size: tuple[int, int],
    zoom_percent: int | None,
    timeline: EditTimeline | None = None,
    split_compare: bool = False,
    comparison_image: Image.Image | None = None,
    comparison_labels: tuple[str, str] | None = None,
    display_proxy: Image.Image | None = None,
    conversion_recipe: TransformRecipe | None = None,
) -> PreviewFrame:
    """Apply edits and resize without touching Tk from the worker thread."""

    if compare_original:
        working = original
        owns_working = False
    elif conversion_recipe is not None:
        working = execute_transform_recipe(original, conversion_recipe)
        owns_working = True
    elif timeline is not None and timeline.cursor > 0:
        working = render_image(original, timeline)
        owns_working = True
    elif edit_state == EditState():
        working = original
        owns_working = False
    else:
        working = apply_edits(original, edit_state)
        owns_working = True

    canvas_w = max(100, canvas_size[0])
    canvas_h = max(100, canvas_size[1])
    if comparison_image is not None:
        return _render_split_preview(
            working,
            comparison_image,
            frame_working=working,
            owns_working=owns_working,
            canvas_size=(canvas_w, canvas_h),
            zoom_percent=zoom_percent,
            left_icc_profile=metadata_from_image(original).icc_profile,
            right_icc_profile=metadata_from_image(comparison_image).icc_profile,
            labels=comparison_labels or ("当前图片", "对比图片"),
        )
    if split_compare and not compare_original:
        return _render_split_preview(
            original,
            working,
            frame_working=working,
            owns_working=owns_working,
            canvas_size=(canvas_w, canvas_h),
            zoom_percent=zoom_percent,
            left_icc_profile=metadata_from_image(original).icc_profile,
            right_icc_profile=metadata_from_image(original).icc_profile,
            labels=("编辑前", "编辑后"),
        )
    if zoom_percent is None:
        scale = min(canvas_w / working.width, canvas_h / working.height, 1.0)
    else:
        scale = zoom_percent / 100.0

    preview_size = (
        max(1, int(working.width * scale)),
        max(1, int(working.height * scale)),
    )
    display_source = display_proxy if display_proxy is not None else working
    if preview_size == display_source.size:
        display = display_source.copy()
    else:
        display = display_source.resize(
            preview_size,
            Image.Resampling.LANCZOS,
            reducing_gap=3.0 if scale < 1.0 else None,
        )
    managed = color_manage_for_display(display, metadata_from_image(original).icc_profile)
    if managed is not display:
        display.close()
    display = managed
    return PreviewFrame(
        working=working,
        image=display,
        scale=display.width / working.width,
        owns_working=owns_working,
    )


def _display_copy(
    image: Image.Image,
    size: tuple[int, int],
    icc_profile: bytes | None,
) -> Image.Image:
    if size == image.size:
        resized = image.copy()
    else:
        resized = image.resize(
            size,
            Image.Resampling.LANCZOS,
            reducing_gap=3.0 if size[0] < image.width or size[1] < image.height else None,
        )
    managed = color_manage_for_display(resized, icc_profile)
    if managed is not resized:
        resized.close()
    return managed


def _paste_with_alpha(canvas: Image.Image, image: Image.Image, position: tuple[int, int]) -> None:
    if "A" in image.getbands():
        rgb = image.convert("RGB")
        alpha = image.getchannel("A")
        try:
            canvas.paste(rgb, position, alpha)
        finally:
            rgb.close()
            alpha.close()
    else:
        canvas.paste(image, position)


def _render_split_preview(
    left_image: Image.Image,
    right_image: Image.Image,
    *,
    frame_working: Image.Image,
    owns_working: bool,
    canvas_size: tuple[int, int],
    zoom_percent: int | None,
    left_icc_profile: bytes | None,
    right_icc_profile: bytes | None,
    labels: tuple[str, str],
) -> PreviewFrame:
    """Render two complete images at one common scale in equal-width panels."""

    gap = 8
    max_width = max(left_image.width, right_image.width)
    max_height = max(left_image.height, right_image.height)
    if zoom_percent is None:
        available_panel_width = max(1, (canvas_size[0] - gap) // 2)
        scale = min(available_panel_width / max_width, canvas_size[1] / max_height, 1.0)
    else:
        scale = zoom_percent / 100.0
    panel_width = max(1, round(max_width * scale))
    panel_height = max(1, round(max_height * scale))
    before_size = (
        max(1, round(left_image.width * scale)),
        max(1, round(left_image.height * scale)),
    )
    after_size = (
        max(1, round(right_image.width * scale)),
        max(1, round(right_image.height * scale)),
    )
    before = _display_copy(left_image, before_size, left_icc_profile)
    after = _display_copy(right_image, after_size, right_icc_profile)
    combined = Image.new("RGB", (panel_width * 2 + gap, panel_height), "#1f232b")
    before_position = (
        (panel_width - before.width) // 2,
        (panel_height - before.height) // 2,
    )
    after_position = (
        panel_width + gap + (panel_width - after.width) // 2,
        (panel_height - after.height) // 2,
    )
    try:
        _paste_with_alpha(combined, before, before_position)
        _paste_with_alpha(combined, after, after_position)
    finally:
        before.close()
        after.close()
    layout = SplitPreviewLayout(
        divider_x=panel_width + gap // 2,
        before_panel=(0, 0, panel_width, panel_height),
        after_panel=(panel_width + gap, 0, panel_width * 2 + gap, panel_height),
    )
    return PreviewFrame(
        working=frame_working,
        image=combined,
        scale=scale,
        owns_working=owns_working,
        split_layout=layout,
        split_labels=labels,
    )


def color_manage_for_display(image: Image.Image, icc_profile: bytes | None) -> Image.Image:
    """Convert an embedded source profile to the Windows/Tk sRGB display space."""

    if not icc_profile:
        return image
    try:
        source_profile = ImageCms.ImageCmsProfile(BytesIO(icc_profile))
        alpha = image.getchannel("A") if "A" in image.getbands() else None
        converted = ImageCms.profileToProfile(
            image.convert("RGB"),
            source_profile,
            _DISPLAY_PROFILE,
            outputMode="RGB",
        )
        if converted is None:
            return image
        if alpha is not None:
            converted.putalpha(alpha)
        return converted
    except (OSError, SyntaxError, TypeError, ValueError):
        # A malformed optional profile must not prevent the image from being viewed.
        return image


class PreviewRenderer:
    """Latest-request-wins single-worker renderer for the Tk viewer."""

    def __init__(self) -> None:
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="image-preview")
        self._pending: Future[PreviewFrame] | None = None
        self._lock = threading.Lock()

    def render(
        self,
        original: Image.Image,
        edit_state: EditState,
        *,
        compare_original: bool,
        canvas_size: tuple[int, int],
        zoom_percent: int | None,
        timeline: EditTimeline | None = None,
        split_compare: bool = False,
        comparison_image: Image.Image | None = None,
        comparison_labels: tuple[str, str] | None = None,
        display_proxy: Image.Image | None = None,
        conversion_recipe: TransformRecipe | None = None,
    ) -> Future[PreviewFrame]:
        with self._lock:
            if self._pending is not None and not self._pending.running():
                self._pending.cancel()
            future = self._executor.submit(
                render_preview,
                original,
                edit_state,
                compare_original=compare_original,
                canvas_size=canvas_size,
                zoom_percent=zoom_percent,
                timeline=timeline,
                split_compare=split_compare,
                comparison_image=comparison_image,
                comparison_labels=comparison_labels,
                display_proxy=display_proxy,
                conversion_recipe=conversion_recipe,
            )
            self._pending = future
            return future

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            pending = self._pending
        if pending is not None and not pending.running():
            pending.cancel()
        self._executor.shutdown(wait=wait, cancel_futures=True)
