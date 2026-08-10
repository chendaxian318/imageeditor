from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from light_image_editor.windows_effects import BorderlessWindowController, rounded_region_metrics


class RoundedWindowMetricsTests(unittest.TestCase):
    def test_region_uses_scaled_corner_diameter(self) -> None:
        self.assertEqual(rounded_region_metrics(1240, 780, 28), (1240, 780, 56))
        self.assertEqual(rounded_region_metrics(1240, 780, 28, 1.5), (1240, 780, 84))

    def test_region_clamps_invalid_or_tiny_dimensions(self) -> None:
        self.assertEqual(rounded_region_metrics(0, -4, 28), (1, 1, 0))
        self.assertEqual(rounded_region_metrics(20, 12, 28), (20, 12, 12))


class BorderlessWindowDragTests(unittest.TestCase):
    def test_start_drag_clears_region_and_keeps_tk_event_loop_active(self) -> None:
        controller = object.__new__(BorderlessWindowController)
        controller.window = Mock()
        controller.window.winfo_width.return_value = 800
        controller.window.winfo_rootx.return_value = 100
        controller.window.winfo_x.return_value = 100
        controller.window.winfo_y.return_value = 60
        controller._fullscreen = False
        controller._maximized = False
        controller._drag_offset = None
        controller._drag_window_handle = None
        event = SimpleNamespace(x_root=300, y_root=90)

        with (
            patch(
                "light_image_editor.windows_effects._root_window_handle",
                return_value=1234,
            ),
            patch("light_image_editor.windows_effects._clear_window_region") as clear_region,
        ):
            result = controller._start_drag(event)

        self.assertEqual(result, "break")
        clear_region.assert_called_once_with(1234)
        self.assertTrue(controller.moving)
        self.assertEqual(controller._drag_offset, (200, 30))
        self.assertEqual(controller._drag_window_handle, 1234)
        controller.window.event_generate.assert_called_once_with("<<WindowMoveStarted>>")

    def test_drag_coalesces_pointer_samples_into_one_native_frame(self) -> None:
        controller = object.__new__(BorderlessWindowController)
        controller.window = Mock()
        controller.window.after.return_value = "drag-frame"
        controller._fullscreen = False
        controller._maximized = False
        controller._drag_offset = (20, 10)
        controller._drag_frame_job = None
        controller._pending_drag_position = None
        controller._drag_window_handle = 1234
        event = SimpleNamespace(x_root=500, y_root=320)

        result = controller._drag(event)
        controller._drag(SimpleNamespace(x_root=508, y_root=325))

        self.assertEqual(result, "break")
        self.assertEqual(controller._pending_drag_position, (488, 315))
        controller.window.after.assert_called_once_with(
            16,
            controller._flush_drag_position,
        )
        controller.window.geometry.assert_not_called()

        with patch(
            "light_image_editor.windows_effects._set_native_window_position",
            return_value=True,
        ) as native_move:
            controller._flush_drag_position()

        native_move.assert_called_once_with(1234, 488, 315)
        self.assertIsNone(controller._pending_drag_position)
        controller.window.geometry.assert_not_called()

    def test_resize_coalesces_pointer_samples_into_one_native_frame(self) -> None:
        controller = object.__new__(BorderlessWindowController)
        controller.window = Mock()
        controller.window.minsize.return_value = (640, 480)
        controller.window.after.return_value = "resize-frame"
        controller._resize_origin = (100, 100, 10, 20, 800, 600)
        controller._resize_direction = "se"
        controller._resize_frame_job = None
        controller._pending_resize_bounds = None
        controller._resize_window_handle = 4321

        result = controller._resize(SimpleNamespace(x_root=120, y_root=130))
        controller._resize(SimpleNamespace(x_root=130, y_root=140))

        self.assertEqual(result, "break")
        self.assertEqual(controller._pending_resize_bounds, (10, 20, 830, 640))
        controller.window.after.assert_called_once_with(16, controller._flush_resize_bounds)
        controller.window.geometry.assert_not_called()

        with patch(
            "light_image_editor.windows_effects._set_native_window_bounds",
            return_value=True,
        ) as native_resize:
            controller._flush_resize_bounds()

        native_resize.assert_called_once_with(4321, 10, 20, 830, 640)
        self.assertIsNone(controller._pending_resize_bounds)
        controller.window.geometry.assert_not_called()
        controller.window.update_idletasks.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
