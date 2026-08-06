from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from light_image_editor.session import SessionStore
from light_image_editor.settings import SettingsStore
from light_image_editor.ui.main_window import ANIMATION_SPEED_FACTORS, ImageEditorApp


class AnimationTimelineControlTests(unittest.TestCase):
    @staticmethod
    def _create_app(directory: str) -> ImageEditorApp:
        root = Path(directory)
        settings = SettingsStore(root / "settings.json")
        settings.save(
            {
                "schema_version": 1,
                "onboarding_completed": True,
                "theme_mode": "light",
            }
        )
        app = ImageEditorApp(
            settings_store=settings,
            session_store=SessionStore(root / "session.json"),
        )
        for job in app._startup_jobs:
            app.after_cancel(job)
        app._startup_jobs.clear()
        app.update_idletasks()
        app.update()
        return app

    @staticmethod
    def _close_app(app: ImageEditorApp) -> None:
        if app.winfo_exists():
            app._shutdown_services()
            app.destroy()

    @staticmethod
    def _write_animation(path: Path, *, duration: int = 1000) -> None:
        frames = [Image.new("RGB", (48, 32), color) for color in ("#d55f5f", "#65a86e", "#5f8bd5")]
        try:
            frames[0].save(
                path,
                save_all=True,
                append_images=frames[1:],
                duration=[duration, duration, duration],
                loop=0,
            )
        finally:
            for frame in frames:
                frame.close()

    @staticmethod
    def _open_animation(app: ImageEditorApp, path: Path) -> None:
        app.open_path(path, confirm=False)
        deadline = time.monotonic() + 5
        while app._animation_sequence is None and time.monotonic() < deadline:
            app.update()
            time.sleep(0.01)
        if app._animation_sequence is None:
            raise AssertionError(f"动画载入超时：{path}")
        app.update_idletasks()

    @staticmethod
    def _canvas_labels(app: ImageEditorApp) -> set[str]:
        return {
            str(app.canvas.itemcget(item, "text"))
            for item in app.canvas.find_all()
            if app.canvas.type(item) == "text"
        }

    def test_frame_slider_seeks_and_pauses_while_speed_scales_schedule(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "进度动画.gif"
            self._write_animation(source)
            app = self._create_app(directory)
            try:
                self._open_animation(app, source)
                sequence = app._animation_sequence
                assert sequence is not None
                self.assertEqual(app.animation_frame_slider.minimum, 1.0)
                self.assertEqual(app.animation_frame_slider.maximum, 3.0)
                self.assertEqual(
                    tuple(app.animation_speed_box.cget("values")),
                    tuple(ANIMATION_SPEED_FACTORS),
                )

                app.animation_frame_slider._set_value(3)

                self.assertFalse(app._animation_playing)
                self.assertIsNone(app._animation_job)
                self.assertEqual(app._animation_displayed_index, 2)
                self.assertEqual(app.animation_frame_var.get(), 3)
                self.assertEqual(app.animation_position_var.get(), "第 3 / 3 帧")
                self.assertIs(app.base_preview_image, sequence.frames[2])
                self.assertIn("动画已暂停", app.status_var.get())

                expected_delays = {
                    "0.5×": 2000,
                    "1×": 1000,
                    "1.5×": 667,
                    "2×": 500,
                }
                for label, expected in expected_delays.items():
                    app.animation_speed_var.set(label)
                    self.assertEqual(app._animation_delay_ms(1000), expected)

                app.animation_speed_var.set("2×")
                app._animation_speed_changed()
                app._animation_playing = True
                app._animation_index = 0
                with patch.object(app, "after", return_value="after#speed") as schedule:
                    app._show_animation_frame()
                self.assertEqual(schedule.call_args.args[0], 500)
                self.assertEqual(schedule.call_args.args[1], app._show_animation_frame)
                self.assertEqual(app._animation_job, "after#speed")
                app._animation_playing = False
                app._animation_job = None
            finally:
                self._close_app(app)

    def test_file_comparison_pauses_animation_and_blocks_seek_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "主动画.gif"
            comparison = root / "静态参考.png"
            self._write_animation(source, duration=120)
            Image.new("RGB", (64, 48), "#735f9e").save(comparison)
            app = self._create_app(directory)
            try:
                self._open_animation(app, source)
                self.assertTrue(app._animation_playing)
                generation = app.preview_ready_generation
                self.assertTrue(app._begin_file_comparison(comparison))
                deadline = time.monotonic() + 5
                while (
                    not app.file_compare_var.get() or app.preview_ready_generation == generation
                ) and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)

                self.assertTrue(app.file_compare_var.get())
                self.assertFalse(app._animation_playing)
                self.assertIsNone(app._animation_job)
                app.update_idletasks()
                self.assertFalse(app.animation_controls.winfo_ismapped())
                labels = self._canvas_labels(app)
                self.assertIn(source.name, labels)
                self.assertIn(comparison.name, labels)
                displayed = app._animation_displayed_index

                app._toggle_animation_playback()
                app._step_animation(1)
                app.animation_frame_slider._set_value(2)
                app._show_animation_frame()
                app.update_idletasks()

                self.assertFalse(app._animation_playing)
                self.assertEqual(app._animation_displayed_index, displayed)
                self.assertEqual(app.animation_frame_var.get(), displayed + 1)
                self.assertEqual(self._canvas_labels(app), labels)
            finally:
                self._close_app(app)


if __name__ == "__main__":
    unittest.main()
