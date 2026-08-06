from __future__ import annotations

import tkinter as tk
import unittest

from light_image_editor.accessibility import set_accessible_name
from light_image_editor.theme import DARK_TOKENS, LIGHT_TOKENS
from light_image_editor.ui.conversion_workspace import ConversionWorkspace
from light_image_editor.ui.material import MaterialButton


class MaterialButtonTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self) -> None:
        self.root.update_idletasks()
        self.root.destroy()

    def test_disabled_state_blocks_public_pointer_and_keyboard_invocation(self) -> None:
        calls: list[str] = []
        button = MaterialButton(
            self.root,
            text="执行",
            command=lambda: calls.append("执行"),
            width=88,
            height=34,
            variant="tonal",
        )
        button.pack()
        self.root.update_idletasks()

        self.assertEqual(button.cget("text"), "执行")
        self.assertEqual(button.accessible_name, "执行")
        self.assertTrue(button.instate(("!disabled",)))
        self.assertEqual(button.state(("disabled",)), ("!disabled",))
        self.assertIn("disabled", button.state())
        self.assertEqual(button.cget("state"), "disabled")
        self.assertFalse(bool(int(button.cget("takefocus"))))

        button.invoke()
        button._invoke(tk.Event())
        button._keyboard_pressed(tk.Event())
        button._keyboard_released(tk.Event())
        button._clicked(tk.Event())
        self.assertEqual(calls, [])

        self.assertEqual(button.state(("!disabled",)), ("disabled",))
        self.assertNotIn("disabled", button.state())
        self.assertTrue(bool(int(button.cget("takefocus"))))
        button.invoke()
        self.assertEqual(calls, ["执行"])

    def test_keyboard_and_pointer_release_invoke_once_and_keep_focus_state(self) -> None:
        calls: list[str] = []
        button = MaterialButton(
            self.root,
            text="转换",
            command=lambda: calls.append("转换"),
            width=88,
            height=34,
            variant="filled",
        )
        button.pack()
        self.root.update_idletasks()

        self.assertTrue(button.bind("<KeyPress-space>"))
        self.assertTrue(button.bind("<KeyRelease-space>"))
        button._keyboard_pressed(tk.Event())
        self.assertIn("pressed", button.state())
        button._keyboard_released(tk.Event())
        self.assertNotIn("pressed", button.state())
        self.assertEqual(calls, ["转换"])

        inside = tk.Event()
        inside.x = button.winfo_width() // 2
        inside.y = button.winfo_height() // 2
        button._pointer_pressed(inside)
        button._pointer_released(inside)
        self.assertEqual(calls, ["转换", "转换"])

        outside = tk.Event()
        outside.x = button.winfo_width() + 1
        outside.y = button.winfo_height() + 1
        button._pointer_pressed(inside)
        button._pointer_released(outside)
        self.assertEqual(calls, ["转换", "转换"])

        button._focus_in(tk.Event())
        self.assertIn("focus", button.state())
        button._focus_out(tk.Event())
        self.assertNotIn("focus", button.state())

    def test_text_configuration_accessibility_and_dark_disabled_palette(self) -> None:
        button = MaterialButton(
            self.root,
            text="排除所选",
            command=lambda: None,
            width=88,
            height=34,
            variant="tonal",
        )
        button.pack()
        button.configure(text="启用所选")
        button.apply_palette(
            surface=DARK_TOKENS.surface,
            container=DARK_TOKENS.surface_container,
            hover=DARK_TOKENS.surface_high,
            foreground=DARK_TOKENS.foreground,
            accent=DARK_TOKENS.accent,
            on_accent=DARK_TOKENS.on_accent,
            disabled_container=DARK_TOKENS.surface_alt,
            disabled_foreground=DARK_TOKENS.muted,
            focus=DARK_TOKENS.focus,
            border=DARK_TOKENS.border,
        )
        button.state(("disabled",))
        self.root.update_idletasks()

        self.assertEqual(button.cget("text"), "启用所选")
        self.assertEqual(button.accessible_name, "启用所选")
        self.assertEqual(button.cget("background"), DARK_TOKENS.surface)
        fill, foreground, outline = button._colors()
        self.assertEqual(fill, DARK_TOKENS.surface_alt)
        self.assertEqual(foreground, DARK_TOKENS.muted)
        self.assertIsNone(outline)
        self.assertEqual(button.type(button.find_all()[0]), "image")

    def test_icon_button_shows_accessible_name_as_delayed_tooltip(self) -> None:
        self.root.deiconify()
        button = MaterialButton(
            self.root,
            text="⌗",
            command=lambda: None,
            width=38,
            height=34,
            variant="icon",
        )
        button.pack()
        set_accessible_name(button, "裁剪")
        self.root.update_idletasks()

        button._enter(tk.Event())
        self.assertIsNotNone(button._tooltip_job)
        assert button._tooltip_job is not None
        button.after_cancel(button._tooltip_job)
        button._tooltip_job = None
        button._show_tooltip()

        self.assertIsNotNone(button._tooltip_window)
        assert button._tooltip_window is not None
        labels = [
            child
            for child in button._tooltip_window.winfo_children()
            if isinstance(child, tk.Label)
        ]
        self.assertEqual(len(labels), 1)
        self.assertEqual(labels[0].cget("text"), "裁剪")

        button._leave(tk.Event())
        self.assertIsNone(button._tooltip_job)
        self.assertIsNone(button._tooltip_window)


class ConversionWorkspaceMaterialLayoutTests(unittest.TestCase):
    def test_action_buttons_use_material_palette_without_compact_overflow(self) -> None:
        root = tk.Tk()
        root.geometry("220x680")
        root.update_idletasks()
        workspace = ConversionWorkspace(root, tokens=LIGHT_TOKENS, width=220)
        workspace.pack(fill="both", expand=True)
        root.update_idletasks()
        root.update()
        try:
            buttons = workspace._material_action_buttons
            self.assertEqual(len(buttons), 8)
            self.assertTrue(all(isinstance(button, MaterialButton) for button in buttons))
            self.assertEqual(
                tuple(button.accessible_name for button in buttons),
                (
                    "添加文件",
                    "添加文件夹",
                    "排除所选",
                    "移除所选",
                    "清空队列",
                    "重试任务",
                    "打开结果",
                    "定位结果",
                ),
            )

            workspace_left = workspace.winfo_rootx()
            workspace_right = workspace_left + workspace.winfo_width()
            for button in buttons:
                self.assertGreaterEqual(button.winfo_rootx(), workspace_left)
                self.assertLessEqual(
                    button.winfo_rootx() + button.winfo_width(),
                    workspace_right,
                )
                self.assertEqual(button.cget("background"), LIGHT_TOKENS.surface)

            workspace.apply_tokens(DARK_TOKENS)
            root.update_idletasks()
            for button in buttons:
                self.assertEqual(button.cget("background"), DARK_TOKENS.surface)
                self.assertEqual(button._accent, DARK_TOKENS.accent)
                self.assertEqual(button._disabled_foreground, DARK_TOKENS.muted)

            workspace_bottom = workspace.winfo_rooty() + workspace.winfo_height()
            for child in workspace.winfo_children():
                self.assertLessEqual(child.winfo_rooty() + child.winfo_height(), workspace_bottom)
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
