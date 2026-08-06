from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from light_image_editor.session import SessionStore
from light_image_editor.settings import SettingsStore
from light_image_editor.shell import (
    FeatureDefinition,
    FeatureRegistry,
    ModeId,
    create_default_feature_registry,
)
from light_image_editor.ui.feature_palette import (
    FeatureCommandPalette,
    FeaturePaletteScope,
    build_feature_palette_items,
    filter_feature_palette_items,
)
from light_image_editor.ui.main_window import ImageEditorApp


class FeaturePaletteModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = create_default_feature_registry()
        self.items = build_feature_palette_items(self.registry)

    def test_default_registry_is_fully_indexed_with_chinese_entry_labels(self) -> None:
        self.assertEqual(len(self.registry), 135)
        self.assertEqual(len(self.items), len(self.registry))
        self.assertEqual(
            {item.feature_id for item in self.items},
            {feature.id for feature in self.registry},
        )
        self.assertTrue(all(item.entry_label for item in self.items))
        self.assertTrue(all("." not in item.entry_label for item in self.items))

    def test_search_matches_chinese_labels_shortcuts_and_multiple_tokens(self) -> None:
        background = filter_feature_palette_items(
            self.items,
            query="智能 背景",
            active_mode=ModeId.EDIT,
        )
        self.assertEqual(background[0].feature_id, "edit.smart.remove_background")

        shortcut = filter_feature_palette_items(
            self.items,
            query="Ctrl+K",
            active_mode=ModeId.VIEWER,
        )
        self.assertEqual(tuple(item.feature_id for item in shortcut), ("global.feature_palette",))

        exact = filter_feature_palette_items(
            self.items,
            query="智能改图",
            active_mode=ModeId.VIEWER,
        )
        self.assertEqual(exact[0].feature_id, "edit.smart")

    def test_scope_filters_current_global_and_each_explicit_mode(self) -> None:
        current = filter_feature_palette_items(
            self.items,
            scope=FeaturePaletteScope.CURRENT,
            active_mode=ModeId.CONVERT,
        )
        self.assertTrue(current)
        self.assertTrue(all(item.feature.mode in {None, ModeId.CONVERT} for item in current))
        self.assertTrue(any(item.feature.mode is None for item in current))
        self.assertTrue(any(item.feature.mode is ModeId.CONVERT for item in current))

        global_items = filter_feature_palette_items(
            self.items,
            scope=FeaturePaletteScope.GLOBAL,
            active_mode=ModeId.CONVERT,
        )
        self.assertTrue(all(item.feature.mode is None for item in global_items))

        generate = filter_feature_palette_items(
            self.items,
            scope=FeaturePaletteScope.GENERATE,
            active_mode=ModeId.VIEWER,
        )
        self.assertTrue(generate)
        self.assertTrue(all(item.feature.mode is ModeId.GENERATE for item in generate))

    def test_unknown_scope_recovers_to_complete_stable_inventory(self) -> None:
        filtered = filter_feature_palette_items(
            self.items,
            scope="损坏的筛选值",
            active_mode=ModeId.VIEWER,
        )
        self.assertEqual(
            tuple(item.feature_id for item in filtered),
            tuple(feature.id for feature in self.registry),
        )


class FeaturePaletteUiTests(unittest.TestCase):
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

    def test_dock_opens_single_searchable_palette_and_executes_real_callback(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            try:
                self.assertTrue(app.bind("<Control-k>"))
                app.dock_menu_button.command()
                app.update_idletasks()
                app.update()
                palette = app._feature_palette_window
                self.assertIsNotNone(palette)
                assert palette is not None
                self.assertTrue(palette.winfo_ismapped())
                self.assertEqual(len(palette.items), len(app.feature_registry))
                self.assertEqual(len(palette.tree.get_children()), 135)
                self.assertEqual(
                    palette.search_field.placeholder.cget("text"),
                    "搜索功能、模块、入口或快捷键",
                )
                self.assertEqual(palette.tree.accessible_name, "全部功能搜索结果")

                app._show_feature_palette()
                self.assertIs(app._feature_palette_window, palette)

                palette.query_var.set("智能改图")
                app.update_idletasks()
                self.assertGreaterEqual(len(palette.tree.get_children()), 1)
                self.assertEqual(palette.tree.get_children()[0], "edit.smart")
                self.assertEqual(palette.tree.selection(), ("edit.smart",))
                palette._invoke_selected()
                app.update_idletasks()
                self.assertIsNone(app._feature_palette_window)
                self.assertEqual(app.mode_router.active_mode, ModeId.EDIT)
                self.assertEqual(app.active_inspector_tab, "smart")
            finally:
                self._close_app(app)

    def test_disabled_feature_is_visible_but_cannot_invoke(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = self._create_app(directory)
            invoked: list[str] = []
            registry = FeatureRegistry(
                (
                    FeatureDefinition(
                        "global.disabled_demo",
                        None,
                        "暂不可用示例",
                        "dock.disabled_demo",
                        command="disabled_demo",
                        enabled=False,
                    ),
                )
            )
            palette = FeatureCommandPalette(
                app,
                registry,
                active_mode=ModeId.VIEWER,
                on_invoke=invoked.append,
            )
            try:
                app.update_idletasks()
                self.assertEqual(palette.tree.get_children(), ("global.disabled_demo",))
                self.assertEqual(palette.invoke_button.cget("state"), "disabled")
                palette._invoke_selected()
                self.assertEqual(invoked, [])
                self.assertTrue(palette.winfo_exists())
            finally:
                if palette.winfo_exists():
                    palette.close()
                self._close_app(app)


if __name__ == "__main__":
    unittest.main()
