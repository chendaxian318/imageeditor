from __future__ import annotations

import os
import tempfile
import unittest
import uuid
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

from PIL import Image

from image_ops import EditState
from light_image_editor.app import _dispatch_ui_command
from light_image_editor.document import Document
from light_image_editor.files import FOF_ALLOWUNDO, FOF_NOCONFIRMATION, rename_file
from light_image_editor.image_loader import ImageLoader, ImageLoadFailure, decode_image
from light_image_editor.metadata import image_properties
from light_image_editor.navigation import NavigationSession, expand_activation_paths
from light_image_editor.preview import PreviewRenderer, render_preview
from light_image_editor.session import SessionStore
from light_image_editor.settings import SettingsStore
from light_image_editor.single_instance import SingleInstanceController, activation_message
from light_image_editor.ui.main_window import ImageEditorApp


class NavigationTests(unittest.TestCase):
    def test_500_image_folder_uses_natural_order(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            for index in reversed(range(500)):
                (folder / f"image{index}.png").touch()

            session = NavigationSession.from_activation([folder / "image250.png"])

            self.assertEqual(len(session.files), 500)
            self.assertEqual(session.files[0].name, "image0.png")
            self.assertEqual(session.files[10].name, "image10.png")
            self.assertEqual(session.position, (251, 500))
            target = session.target(1)
            self.assertIsNotNone(target)
            self.assertEqual(target.name if target else "", "image251.png")

    def test_multiple_activation_paths_preserve_order_and_remove_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            first = folder / "image10.png"
            second = folder / "image2.png"
            first.touch()
            second.touch()

            expanded = expand_activation_paths([first, second, first])
            session = NavigationSession.from_activation([first, second])

            self.assertEqual([path.name for path in expanded], ["image10.png", "image2.png"])
            self.assertEqual([path.name for path in session.files], ["image10.png", "image2.png"])
            self.assertEqual(session.source_label, "选择的文件")


class ImageLoaderTests(unittest.TestCase):
    def test_background_loader_decodes_and_prefetches_without_unbounded_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            paths = []
            for index in range(6):
                path = folder / f"{index}.png"
                Image.new("RGB", (64, 48), (index, 20, 30)).save(path)
                paths.append(path)
            loader = ImageLoader(max_workers=2, cache_size=3)
            try:
                loader.prefetch(paths)
                image = loader.load(paths[-1]).result(timeout=5)
                self.assertEqual(image.size, (64, 48))
            finally:
                loader.shutdown()

    def test_corrupt_file_has_a_friendly_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.jpg"
            path.write_bytes(b"not an image")

            with self.assertRaises(ImageLoadFailure) as raised:
                decode_image(path)

            self.assertEqual(raised.exception.kind, "damaged")
            self.assertIn("损坏", raised.exception.user_message)

    def test_inflight_callers_receive_independent_images_and_cached_preview(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "large.png"
            Image.new("RGB", (2000, 1200), "#245680").save(path)
            loader = ImageLoader(max_workers=1, cache_size=2)
            try:
                first_future = loader.load(path)
                second_future = loader.load(path)
                first = first_future.result(timeout=10)
                second = second_future.result(timeout=10)
                first.close()
                self.assertEqual(second.getpixel((10, 10)), (36, 86, 128))
                proxy = loader.cached_preview(path)
                self.assertIsNotNone(proxy)
                assert proxy is not None
                self.assertLessEqual(max(proxy.size), 1600)
                proxy.close()
                second.close()
            finally:
                loader.shutdown()

    def test_navigation_can_take_prefetched_bitmap_without_losing_screen_proxy(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "prefetched.png"
            Image.new("RGB", (2000, 1200), "#5a7896").save(path)
            loader = ImageLoader(max_workers=1, cache_size=2)
            try:
                warm = loader.load(path).result(timeout=10)
                warm.close()

                document_image = loader.load(path, take_cached=True).result(timeout=10)
                proxy = loader.cached_preview(path)

                self.assertEqual(document_image.getpixel((20, 20)), (90, 120, 150))
                self.assertIsNotNone(proxy)
                assert proxy is not None
                self.assertLessEqual(max(proxy.size), 1600)
                proxy.close()
                document_image.close()
            finally:
                loader.shutdown()

    def test_large_preview_is_rendered_to_canvas_size_in_background(self) -> None:
        image = Image.new("RGB", (4000, 3000), "navy")
        renderer = PreviewRenderer()
        try:
            frame = renderer.render(
                image,
                EditState(),
                compare_original=False,
                canvas_size=(1000, 700),
                zoom_percent=None,
            ).result(timeout=5)
        finally:
            renderer.shutdown()
            image.close()

        self.assertEqual(frame.image.size, (933, 700))
        self.assertLess(frame.scale, 1.0)

    def test_preview_applies_edits_without_mutating_source(self) -> None:
        image = Image.new("RGB", (80, 40), "red")
        frame = render_preview(
            image,
            EditState(rotation=90),
            compare_original=False,
            canvas_size=(100, 100),
            zoom_percent=None,
        )
        self.assertEqual(frame.working.size, (40, 80))
        self.assertEqual(image.size, (80, 40))

    def test_preview_can_compare_two_different_files_with_distinct_labels(self) -> None:
        primary = Image.new("RGB", (40, 30), "red")
        secondary = Image.new("RGB", (40, 30), "blue")
        try:
            frame = render_preview(
                primary,
                EditState(),
                compare_original=False,
                canvas_size=(120, 60),
                zoom_percent=None,
                comparison_image=secondary,
                comparison_labels=("主图.png", "参考图.png"),
            )
            try:
                self.assertIsNotNone(frame.split_layout)
                assert frame.split_layout is not None
                self.assertEqual(frame.split_labels, ("主图.png", "参考图.png"))
                self.assertEqual(frame.image.getpixel((5, 5)), (255, 0, 0))
                self.assertEqual(
                    frame.image.getpixel((frame.split_layout.after_panel[0] + 5, 5)),
                    (0, 0, 255),
                )
                self.assertIs(frame.working, primary)
            finally:
                frame.close()
        finally:
            primary.close()
            secondary.close()

    def test_preview_proxy_keeps_full_image_coordinates(self) -> None:
        original = Image.new("RGB", (4000, 3000), "red")
        proxy = Image.new("RGB", (400, 300), "blue")
        try:
            frame = render_preview(
                original,
                EditState(),
                compare_original=False,
                canvas_size=(200, 150),
                zoom_percent=None,
                display_proxy=proxy,
            )
            try:
                self.assertIs(frame.working, original)
                self.assertEqual(frame.image.size, (200, 150))
                self.assertEqual(frame.image.getpixel((100, 75)), (0, 0, 255))
                self.assertAlmostEqual(frame.scale, 0.05)
            finally:
                frame.close()
        finally:
            original.close()
            proxy.close()


class MetadataAndFileTests(unittest.TestCase):
    def test_properties_include_file_image_and_basic_exif_values(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "camera.jpg"
            exif = Image.Exif()
            exif[271] = "Codex Camera"
            Image.new("RGB", (80, 60), "navy").save(path, exif=exif)
            with Image.open(path) as image:
                properties = dict(image_properties(path, image))

            self.assertEqual(properties["尺寸"], "80 × 60 像素")
            self.assertEqual(properties["相机厂商"], "Codex Camera")
            self.assertIn("文件大小", properties)

    def test_rename_rejects_overwrite_and_invalid_names(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            source = folder / "source.png"
            existing = folder / "existing.png"
            source.touch()
            existing.touch()
            with self.assertRaises(FileExistsError):
                rename_file(source, existing.name)
            with self.assertRaises(ValueError):
                rename_file(source, "bad:name.png")

    def test_recycle_configuration_requires_undo_semantics(self) -> None:
        self.assertNotEqual(FOF_ALLOWUNDO & 0x0040, 0)
        self.assertNotEqual(FOF_NOCONFIRMATION & 0x0010, 0)


class SingleInstanceTests(unittest.TestCase):
    def test_activation_message_carries_all_paths(self) -> None:
        message = activation_message(["one.png", "two.png"])
        self.assertEqual(message["command"], "open")
        self.assertEqual(len(message["paths"]), 2)

    @unittest.skipUnless(os.name == "nt", "Windows named-pipe integration")
    def test_second_controller_redirects_to_primary(self) -> None:
        app_id = f"io.github.imageeditor.test.{uuid.uuid4().hex}"
        primary = SingleInstanceController(app_id=app_id, connect_timeout=3)
        secondary = SingleInstanceController(app_id=app_id, connect_timeout=3)
        received: list[dict[str, object]] = []

        def receive(message):
            received.append(dict(message))
            return {"ok": True}

        try:
            self.assertTrue(primary.acquire_primary())
            primary.start(receive)
            self.assertFalse(secondary.acquire_primary())
            response = secondary.send({"command": "open", "paths": ["last.png"]})
            self.assertTrue(response["ok"])
            self.assertEqual(received[-1]["paths"], ["last.png"])
        finally:
            secondary.close()
            primary.close()

    @unittest.skipUnless(os.name == "nt", "Windows named-pipe integration")
    def test_twenty_activations_reuse_primary_and_deliver_last_path(self) -> None:
        app_id = f"io.github.imageeditor.test.{uuid.uuid4().hex}"
        primary = SingleInstanceController(app_id=app_id, connect_timeout=3)
        secondary = SingleInstanceController(app_id=app_id, connect_timeout=3)
        received: list[str] = []

        def receive(message):
            received.extend(str(path) for path in message.get("paths", []))
            return {"ok": True}

        try:
            self.assertTrue(primary.acquire_primary())
            primary.start(receive)
            for index in range(20):
                self.assertFalse(secondary.acquire_primary())
                secondary.send(activation_message([f"image-{index}.png"]))
            self.assertEqual(len(received), 20)
            self.assertEqual(Path(received[-1]).name, "image-19.png")
        finally:
            secondary.close()
            primary.close()

    def test_ui_dispatch_activates_and_allows_safe_upgrade_exit(self) -> None:
        class FakeApp:
            def __init__(self) -> None:
                self.paths: list[str] = []

            def after(self, _delay: int, callback):
                callback()

            def handle_activation(self, paths):
                self.paths = list(paths)

            def request_exit_for_upgrade(self) -> bool:
                return False

        app = FakeApp()
        viewer_app = cast(Any, app)
        open_response = _dispatch_ui_command(viewer_app, {"command": "open", "paths": ["a", "b"]})
        exit_response = _dispatch_ui_command(viewer_app, {"command": "request_exit", "timeout": 1})

        self.assertEqual(app.paths, ["a", "b"])
        self.assertEqual(open_response, {"ok": True, "accepted": 2})
        self.assertEqual(exit_response, {"ok": True, "accepted": False})

    @unittest.skipUnless(os.name == "nt", "real Tk upgrade-safety integration")
    def test_real_ui_preserves_unsaved_work_when_upgrade_is_cancelled(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = ImageEditorApp(
                settings_store=SettingsStore(Path(directory) / "settings.json"),
                session_store=SessionStore(Path(directory) / "session.json"),
            )
            app.withdraw()
            try:
                original = Image.new("RGB", (80, 60), "navy")
                app.document = Document.from_decoded(
                    original, None, app._new_document_state(original)
                )
                app.original = app.document.image
                app._load_current_image("unsaved.png")
                app.rotate(90)
                self.assertTrue(app.has_unsaved_changes())

                with patch(
                    "light_image_editor.ui.main_window.messagebox.askyesnocancel",
                    return_value=None,
                ):
                    self.assertFalse(app.request_exit_for_upgrade())
                self.assertEqual(app.winfo_exists(), 1)
                self.assertTrue(app.has_unsaved_changes())

                with (
                    patch(
                        "light_image_editor.ui.main_window.messagebox.askyesnocancel",
                        return_value=False,
                    ),
                    patch.object(app, "_finish_upgrade_exit") as finish_exit,
                ):
                    self.assertTrue(app.request_exit_for_upgrade())
                    app.after(80, app.quit)
                    app.mainloop()
                    finish_exit.assert_called_once_with()
            finally:
                if app.winfo_exists():
                    app._shutdown_services()
                    app.destroy()


if __name__ == "__main__":
    unittest.main()
