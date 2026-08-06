from __future__ import annotations

import unittest

from light_image_editor.shell import (
    AssetHandoff,
    FeatureDefinition,
    FeatureRegistry,
    FeatureRegistryError,
    ModeContext,
    ModeId,
    ModeRouter,
    ModeTransition,
    RevisionHandoff,
    create_default_feature_registry,
)


class ModeRouterTests(unittest.TestCase):
    def test_mode_ids_are_stable(self) -> None:
        self.assertEqual(
            tuple(mode.value for mode in ModeId),
            ("viewer", "convert", "edit", "generate"),
        )

    def test_switch_preserves_mode_state_and_carries_asset_revision(self) -> None:
        router = ModeRouter()
        asset = AssetHandoff("asset-1", uri="file:///demo.png", display_name="demo.png")
        revision = RevisionHandoff("revision-3", asset.id, number=3, dirty=True)
        router.update_state({"zoom": "150%", "scroll_x": 12})
        router.set_handoff(asset, revision)

        transition = router.switch(ModeId.EDIT)
        self.assertEqual(transition.previous_mode, ModeId.VIEWER)
        self.assertEqual(transition.mode, ModeId.EDIT)
        self.assertEqual(transition.context.asset_id, "asset-1")
        self.assertEqual(transition.context.revision_id, "revision-3")
        self.assertEqual(transition.context.handoff_from, ModeId.VIEWER)
        router.update_state({"tool": "crop"})

        router.switch(ModeId.VIEWER)
        self.assertEqual(router.context.state["zoom"], "150%")
        self.assertEqual(router.context.state["scroll_x"], 12)
        router.switch(ModeId.EDIT)
        self.assertEqual(router.context.state["tool"], "crop")

    def test_explicit_context_can_open_a_mode_without_inheriting_handoff(self) -> None:
        router = ModeRouter()
        router.set_handoff(AssetHandoff("old"), RevisionHandoff("old-rev", "old"))
        context = ModeContext(ModeId.CONVERT, state={"format": "PNG"})

        router.switch(ModeId.CONVERT, context=context)

        self.assertIsNone(router.context.asset)
        self.assertEqual(router.context.state, {"format": "PNG"})

    def test_context_rejects_revision_from_another_asset(self) -> None:
        with self.assertRaises(ValueError):
            ModeContext(
                ModeId.EDIT,
                asset=AssetHandoff("asset-a"),
                revision=RevisionHandoff("revision", "asset-b"),
            )
        with self.assertRaises(ValueError):
            ModeContext(
                ModeId.EDIT,
                revision=RevisionHandoff("revision", "asset-a"),
            )

    def test_subscriber_receives_updates_and_can_unsubscribe(self) -> None:
        router = ModeRouter()
        events: list[ModeTransition] = []
        unsubscribe = router.subscribe(events.append, emit_current=True)
        router.switch(ModeId.CONVERT)
        router.update_state({"quality": 90})
        unsubscribe()
        router.switch(ModeId.EDIT)

        self.assertEqual([event.reason for event in events], ["snapshot", "switch", "state"])
        self.assertEqual(events[1].mode, ModeId.CONVERT)


class FeatureRegistryTests(unittest.TestCase):
    def test_default_inventory_covers_all_four_modes(self) -> None:
        registry = create_default_feature_registry()

        self.assertEqual(registry.missing_modes, frozenset())
        self.assertTrue(registry.for_mode(ModeId.VIEWER))
        self.assertTrue(registry.for_mode(ModeId.CONVERT))
        self.assertTrue(registry.for_mode(ModeId.EDIT))
        generate = registry.for_mode(ModeId.GENERATE)
        self.assertEqual(
            tuple(feature.id for feature in generate),
            (
                "generate.workspace",
                "generate.run_mock",
                "generate.cancel_mock",
                "generate.candidate_history",
                "generate.negative_prompt",
                "generate.reference_image",
                "generate.reference_role_strength",
                "generate.style_preset",
                "generate.fixed_seed",
                "generate.provider_selection",
                "generate.retry_failed",
                "generate.candidate_variation",
                "generate.candidate_favorite",
                "generate.candidate_favorite_filter",
                "generate.candidate_remove",
                "generate.open_candidate",
                "generate.send_to_edit",
                "generate.send_to_convert",
            ),
        )
        self.assertTrue(all(feature.enabled for feature in generate))
        resolved = registry.resolve_shortcut("f1")
        self.assertIsNotNone(resolved)
        if resolved is None:
            self.fail("F1 was not registered")
        self.assertEqual(resolved.id, "global.shortcut_help")

    def test_global_features_are_separate_and_opt_in_for_mode_queries(self) -> None:
        global_feature = FeatureDefinition(
            "global.settings",
            None,
            "设置",
            "menu.edit.settings",
            command="open_settings",
        )
        viewer_feature = FeatureDefinition(
            "viewer.open",
            ModeId.VIEWER,
            "打开",
            "dock.open",
            command="open_file",
        )
        registry = FeatureRegistry([global_feature, viewer_feature])

        self.assertEqual(registry.global_features, (global_feature,))
        self.assertEqual(registry.modes, frozenset({ModeId.VIEWER}))
        self.assertEqual(registry.missing_modes, frozenset(ModeId) - {ModeId.VIEWER})
        self.assertEqual(registry.for_mode(ModeId.VIEWER), (viewer_feature,))
        self.assertEqual(
            registry.for_mode(ModeId.VIEWER, include_global=True),
            (global_feature, viewer_feature),
        )
        self.assertEqual(
            registry.visible_entries(ModeId.VIEWER, include_global=True),
            ("menu.edit.settings", "dock.open"),
        )

    def test_default_inventory_keeps_global_shell_features_out_of_mode_owners(self) -> None:
        registry = create_default_feature_registry()
        global_ids = {feature.id for feature in registry.global_features}
        required_global_ids = {
            "global.open_project",
            "global.paste_from_clipboard",
            "global.recent_files",
            "global.recent_directories",
            "global.recent_projects",
            "global.recent_exports",
            "global.rename_current",
            "global.copy_current",
            "global.move_current",
            "global.reveal_current",
            "global.delete_current",
            "global.open_recycle_bin",
            "global.save_project",
            "global.toggle_edit_panel",
            "global.workflow",
            "global.settings",
            "global.diagnostic_report",
            "global.shortcut_help",
            "global.onboarding",
            "global.task_tray",
            "global.feature_palette",
        }

        self.assertTrue(required_global_ids <= global_ids)
        self.assertTrue(all(feature.mode is None for feature in registry.global_features))
        self.assertTrue(
            all(feature.id.startswith("global.") for feature in registry.global_features)
        )
        for mode in ModeId:
            with self.subTest(mode=mode):
                owned_ids = {feature.id for feature in registry.for_mode(mode)}
                combined_ids = {
                    feature.id for feature in registry.for_mode(mode, include_global=True)
                }
                self.assertTrue(owned_ids.isdisjoint(global_ids))
                self.assertEqual(combined_ids, owned_ids | global_ids)

    def test_default_inventory_contains_core_legacy_menu_and_panel_features(self) -> None:
        registry = create_default_feature_registry()
        required_ids = {
            "viewer.first",
            "viewer.last",
            "viewer.zoom_50",
            "viewer.actual_size",
            "viewer.zoom_150",
            "viewer.zoom_200",
            "viewer.slideshow_settings",
            "viewer.animation_previous_frame",
            "viewer.animation_toggle",
            "viewer.animation_next_frame",
            "viewer.library",
            "viewer.split_compare",
            "viewer.file_compare",
            "convert.apply_crop",
            "convert.reset_crop",
            "convert.rotate_left",
            "convert.rotate",
            "convert.flip_horizontal",
            "convert.flip_vertical",
            "edit.reset",
            "edit.annotation.select",
            "edit.annotation.text",
            "edit.annotation.arrow",
            "edit.annotation.line",
            "edit.annotation.rectangle",
            "edit.annotation.ellipse",
            "edit.annotation.freehand",
            "edit.annotation.mosaic",
            "edit.annotation.blur",
            "edit.annotation.redact",
            "edit.annotation.number",
            "edit.annotation.perspective_tool",
            "edit.smart.background_color",
            "edit.smart.background_image",
            "edit.smart.background_apply",
            "edit.smart.repair_select",
            "edit.smart.repair_apply",
            "edit.smart.repair_auto_select",
            "edit.smart.repair_next_candidate",
            "edit.smart.mask_add",
            "edit.smart.mask_remove",
            "edit.smart.mask_undo",
            "edit.smart.mask_reset",
            "edit.smart.recommend_font",
        }

        self.assertTrue(required_ids <= {feature.id for feature in registry})
        for feature in registry:
            with self.subTest(feature_id=feature.id):
                self.assertTrue(feature.visible_entry.strip())
                self.assertIsNotNone(feature.command)
                self.assertTrue(feature.command and feature.command.strip())

    def test_default_shortcuts_are_unique_and_exclude_context_or_custom_actions(self) -> None:
        registry = create_default_feature_registry()
        shortcuts = [feature.shortcut for feature in registry if feature.shortcut is not None]

        self.assertEqual(len(shortcuts), len({shortcut.casefold() for shortcut in shortcuts}))
        for contextual_or_custom in (
            "Enter",
            "Space",
            "Ctrl+O",
            "Ctrl+S",
            "Ctrl+Shift+S",
            "Ctrl+Z",
            "Ctrl+Y",
            "Ctrl+R",
            "V",
            "C",
            "T",
            "A",
            "R",
            "M",
            "B",
        ):
            with self.subTest(shortcut=contextual_or_custom):
                self.assertIsNone(registry.resolve_shortcut(contextual_or_custom))

    def test_default_inventory_matches_visible_four_mode_ownership(self) -> None:
        registry = create_default_feature_registry()
        expected = {
            "convert.crop": (ModeId.CONVERT, "dock.crop"),
            "convert.rotate": (ModeId.CONVERT, "dock.rotate"),
            "viewer.compare_original": (ModeId.VIEWER, "dock.compare"),
            "viewer.split_compare": (ModeId.VIEWER, "dock.split_compare"),
            "generate.workspace": (ModeId.GENERATE, "mode.generate.workspace"),
            "generate.run_mock": (ModeId.GENERATE, "mode.generate.run_mock"),
            "generate.cancel_mock": (ModeId.GENERATE, "mode.generate.cancel_mock"),
        }

        for feature_id, (mode, visible_entry) in expected.items():
            with self.subTest(feature_id=feature_id):
                feature = registry[feature_id]
                self.assertEqual(feature.mode, mode)
                self.assertEqual(feature.visible_entry, visible_entry)
                self.assertTrue(feature.enabled)

        for legacy_id in ("edit.crop", "edit.rotate", "edit.compare"):
            with self.subTest(legacy_id=legacy_id):
                self.assertNotIn(legacy_id, registry)

        self.assertIn("离线任务验证", registry["generate.workspace"].label)
        self.assertIn("离线模拟服务", registry["generate.workspace"].description)
        self.assertIn("真实智能模型", registry["generate.workspace"].description)

    def test_duplicate_feature_ids_are_rejected(self) -> None:
        feature = FeatureDefinition("viewer.open", ModeId.VIEWER, "打开", "dock.open")
        registry = FeatureRegistry([feature])

        with self.assertRaises(FeatureRegistryError):
            registry.register(feature)

    def test_duplicate_shortcuts_are_rejected(self) -> None:
        with self.assertRaises(FeatureRegistryError):
            FeatureRegistry(
                [
                    FeatureDefinition(
                        "viewer.one", ModeId.VIEWER, "功能一", "viewer.one", "Ctrl+K"
                    ),
                    FeatureDefinition("edit.two", ModeId.EDIT, "功能二", "edit.two", "control+k"),
                ]
            )

    def test_feature_requires_valid_mode_entry_and_shortcut(self) -> None:
        with self.assertRaises(FeatureRegistryError):
            FeatureDefinition("viewer.bad", "unknown", "错误", "viewer.bad")  # type: ignore[arg-type]
        with self.assertRaises(FeatureRegistryError):
            FeatureDefinition("viewer.hidden", ModeId.VIEWER, "隐藏", "")
        with self.assertRaises(FeatureRegistryError):
            FeatureDefinition("viewer.shortcut", ModeId.VIEWER, "快捷键", "viewer.x", "Ctrl+")

    def test_validate_can_require_four_mode_coverage(self) -> None:
        registry = FeatureRegistry(
            [FeatureDefinition("viewer.open", ModeId.VIEWER, "打开", "dock.open")]
        )

        with self.assertRaises(FeatureRegistryError):
            registry.validate(require_all_modes=True)


if __name__ == "__main__":
    unittest.main()
