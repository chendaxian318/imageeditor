"""Background rendering for responsive large-image previews."""

from __future__ import annotations

import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageCms

from image_ops import EditState, apply_edits
from light_image_editor.document import metadata_from_image

_DISPLAY_PROFILE = ImageCms.createProfile("sRGB")


@dataclass(frozen=True)
class PreviewFrame:
    """A UI-sized bitmap and the working image coordinates it represents."""

    working: Image.Image
    image: Image.Image
    scale: float


def render_preview(
    original: Image.Image,
    edit_state: EditState,
    *,
    compare_original: bool,
    canvas_size: tuple[int, int],
    zoom_percent: int | None,
) -> PreviewFrame:
    """Apply edits and resize without touching Tk from the worker thread."""

    if compare_original:
        working = original
    elif edit_state == EditState():
        working = original
    else:
        working = apply_edits(original, edit_state)

    canvas_w = max(100, canvas_size[0])
    canvas_h = max(100, canvas_size[1])
    if zoom_percent is None:
        scale = min(canvas_w / working.width, canvas_h / working.height, 1.0)
    else:
        scale = zoom_percent / 100.0

    preview_size = (
        max(1, int(working.width * scale)),
        max(1, int(working.height * scale)),
    )
    if preview_size == working.size:
        display = working.copy()
    else:
        display = working.resize(preview_size, Image.Resampling.LANCZOS)
    display = color_manage_for_display(display, metadata_from_image(original).icc_profile)
    return PreviewFrame(working=working, image=display, scale=display.width / working.width)


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
            )
            self._pending = future
            return future

    def shutdown(self, *, wait: bool = True) -> None:
        with self._lock:
            pending = self._pending
        if pending is not None and not pending.running():
            pending.cancel()
        self._executor.shutdown(wait=wait, cancel_futures=True)
