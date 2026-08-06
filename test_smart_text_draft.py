from __future__ import annotations

import unittest

from light_image_editor.domain import TextReplacementDraft, TextReplacementHistory


class TextReplacementDraftTests(unittest.TestCase):
    def test_draft_normalizes_text_color_and_provider_parameters(self) -> None:
        draft = TextReplacementDraft(
            region=(0.1, 0.2, 0.9, 0.7),
            text="  新标题  ",
            font_family="微软雅黑",
            font_size=40,
            color="#C62828",
            align="center",
            rotation=5,
        )

        self.assertEqual(draft.text, "新标题")
        self.assertEqual(draft.color, "#c62828")
        self.assertEqual(dict(draft.to_parameters())["operation"], "replace_text")
        self.assertEqual(dict(draft.to_parameters())["region_right"], 0.9)

    def test_draft_rejects_unsafe_region_text_and_style(self) -> None:
        with self.assertRaises(ValueError):
            TextReplacementDraft(region=(0.5, 0.2, 0.4, 0.8), text="文字")
        with self.assertRaises(ValueError):
            TextReplacementDraft(region=(0.1, 0.2, 0.9, 0.8), text=" ")
        with self.assertRaises(ValueError):
            TextReplacementDraft(
                region=(0.1, 0.2, 0.9, 0.8),
                text="文字",
                color="red",
            )

    def test_perspective_quad_is_validated_and_serialized_as_scalars(self) -> None:
        quad = (0.12, 0.24, 0.86, 0.20, 0.82, 0.66, 0.16, 0.72)
        draft = TextReplacementDraft(
            region=(0.1, 0.15, 0.9, 0.75),
            text="透视标题",
            perspective_quad=quad,
        )

        parameters = dict(draft.to_parameters())

        self.assertEqual(draft.perspective_quad, quad)
        self.assertEqual(parameters["perspective_tl_x"], quad[0])
        self.assertEqual(parameters["perspective_bl_y"], quad[7])
        with self.assertRaises(ValueError):
            TextReplacementDraft(
                region=(0.1, 0.15, 0.9, 0.75),
                text="自交四边形",
                perspective_quad=(0.1, 0.2, 0.8, 0.7, 0.8, 0.2, 0.1, 0.7),
            )
        with self.assertRaises(ValueError):
            TextReplacementDraft(
                region=(0.1, 0.15, 0.9, 0.75),
                text="越界四边形",
                perspective_quad=(0.0, 0.2, 0.8, 0.2, 0.8, 0.7, 0.1, 0.7),
            )

    def test_curve_is_normalized_serialized_and_rejects_multiline_or_out_of_range(self) -> None:
        draft = TextReplacementDraft(
            region=(0.1, 0.2, 0.9, 0.8),
            text="弧形标题 2026",
            curve=0.65,
        )

        self.assertEqual(draft.curve, 0.65)
        self.assertEqual(dict(draft.to_parameters())["curve"], 0.65)
        with self.assertRaisesRegex(ValueError, "-100% 到 100%"):
            TextReplacementDraft(
                region=(0.1, 0.2, 0.9, 0.8),
                text="弧形标题",
                curve=1.01,
            )
        with self.assertRaisesRegex(ValueError, "不支持多行"):
            TextReplacementDraft(
                region=(0.1, 0.2, 0.9, 0.8),
                text="第一行\n第二行",
                curve=-0.5,
            )

    def test_visual_effect_is_strict_serialized_and_part_of_history_identity(self) -> None:
        draft = TextReplacementDraft(
            region=(0.1, 0.2, 0.9, 0.8),
            text="描边标题",
            visual_effect="outline",
            effect_color="#F5F5F5",
            effect_size=6,
        )

        parameters = dict(draft.to_parameters())
        self.assertEqual(draft.effect_color, "#f5f5f5")
        self.assertEqual(parameters["visual_effect"], "outline")
        self.assertEqual(parameters["effect_color"], "#f5f5f5")
        self.assertEqual(parameters["effect_size"], 6)
        self.assertNotEqual(draft, draft.__class__(region=draft.region, text=draft.text))

        glow = TextReplacementDraft(
            region=(0.1, 0.2, 0.9, 0.8),
            text="发光标题",
            visual_effect="glow",
            effect_color="#80D8FF",
            effect_size=8,
        )
        self.assertEqual(dict(glow.to_parameters())["visual_effect"], "glow")
        self.assertEqual(glow.effect_color, "#80d8ff")

        for invalid in ("emboss", "", True, ["outline"]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                TextReplacementDraft(
                    region=(0.1, 0.2, 0.9, 0.8),
                    text="无效字效",
                    visual_effect=invalid,  # type: ignore[arg-type]
                )
        for invalid_size in (-1, 65, True, 2.5):
            with self.subTest(invalid_size=invalid_size), self.assertRaises(ValueError):
                TextReplacementDraft(
                    region=(0.1, 0.2, 0.9, 0.8),
                    text="无效强度",
                    effect_size=invalid_size,  # type: ignore[arg-type]
                )
        with self.assertRaisesRegex(ValueError, "至少为 1"):
            TextReplacementDraft(
                region=(0.1, 0.2, 0.9, 0.8),
                text="描边标题",
                visual_effect="outline",
                effect_size=0,
            )
        with self.assertRaisesRegex(ValueError, "至少为 1"):
            TextReplacementDraft(
                region=(0.1, 0.2, 0.9, 0.8),
                text="发光标题",
                visual_effect="glow",
                effect_size=0,
            )
        with self.assertRaisesRegex(ValueError, "#RRGGBB"):
            TextReplacementDraft(
                region=(0.1, 0.2, 0.9, 0.8),
                text="渐变标题",
                visual_effect="gradient",
                effect_color="blue",
            )

    def test_history_undo_redo_and_branch_replacement_are_deterministic(self) -> None:
        history = TextReplacementHistory(limit=3)
        first = TextReplacementDraft(region=(0.1, 0.2, 0.9, 0.8), text="第一版")
        second = first.with_text("第二版")
        third = second.with_text("第三版")

        self.assertTrue(history.push(first))
        self.assertTrue(history.push(second))
        self.assertTrue(history.push(third))
        self.assertEqual(history.undo(), second)
        replacement = second.with_text("替代版本")
        self.assertTrue(history.push(replacement))
        self.assertFalse(history.can_redo)
        self.assertIsNone(history.redo())
        self.assertEqual(history.length, 3)


if __name__ == "__main__":
    unittest.main()
