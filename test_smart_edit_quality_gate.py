from __future__ import annotations

import unittest

from benchmarks.run_smart_edit_gate import (
    PROFILES,
    SmartEditGateThresholds,
    evaluate_smart_edit_gate,
    run,
)


def _passing_result() -> dict[str, object]:
    scale = {
        "expected_size": [200, 120],
        "output_sizes": [[200, 120], [200, 120]],
        "modes": ["RGBA", "RGBA"],
        "maximum_alpha_mae": 0.5,
        "deterministic_pixels": True,
        "elapsed_ms": {"p95": 100.0},
    }
    styled_estimates = {
        "plain": ("#1c2028", None, None),
        "outline": ("#f8f8f8", "#19376e", 5),
        "shadow": ("#f5a528", "#504641", 9),
        "gradient": ("#8660b0", "#7274ba", None),
        "glow": ("#f8f8ff", "#20b8ff", 7),
    }
    return {
        "upscale": {
            "scales": {
                "2": scale,
                "4": {
                    **scale,
                    "expected_size": [400, 240],
                    "output_sizes": [[400, 240], [400, 240]],
                },
            }
        },
        "remove_background": {
            "case_count": 3,
            "cases": [
                {
                    "minimum_foreground_recall": 1.0,
                    "minimum_background_removal": 1.0,
                    "deterministic_pixels": True,
                    "elapsed_ms": {"p95": 100.0},
                }
                for _index in range(3)
            ],
        },
        "edge_background": {
            "case_count": 2,
            "cases": [
                {
                    "maximum_foreground_alpha_mae": 0.0,
                    "minimum_background_removal": 1.0,
                    "deterministic_pixels": True,
                    "elapsed_ms": {"p95": 100.0},
                }
                for _index in range(2)
            ],
        },
        "alpha_refinement": {
            "preserve_alpha": 255,
            "remove_alpha": 0,
            "source_lineage_matches": True,
        },
        "background_replacement": {
            "case_count": 2,
            "cases": [
                {
                    "expected_background_pixel": [242, 184, 181],
                    "background_pixels": [[242, 184, 181], [242, 184, 181]],
                    "expected_subject_pixel": [225, 82, 90, 255],
                    "subject_pixels": [[225, 82, 90, 255], [225, 82, 90, 255]],
                    "modes": ["RGBA", "RGBA"],
                    "alpha_extrema": [[255, 255], [255, 255]],
                    "source_had_transparency": True,
                    "expected_background_sha256": None,
                    "background_sha256": [None, None],
                    "deterministic_pixels": True,
                    "elapsed_ms": {"p95": 100.0},
                }
                for _index in range(2)
            ],
        },
        "region_repair": {
            "maximum_region_mae": 0.5,
            "maximum_alpha_mae": 0.5,
            "minimum_outside_unchanged": 1.0,
            "source_lineage_matches": True,
            "deterministic_pixels": True,
            "elapsed_ms": {"p95": 100.0},
        },
        "mask_repair": {
            "candidate_count": 2,
            "minimum_selected_change": 1.0,
            "minimum_outside_mask_unchanged": 1.0,
            "minimum_unselected_subject_unchanged": 1.0,
            "minimum_manual_exclusion_unchanged": 1.0,
            "source_lineage_matches": True,
            "provenance_stores_identity_only": True,
            "deterministic_pixels": True,
            "elapsed_ms": {"p95": 100.0},
        },
        "text_replacement": {
            "minimum_region_change": 0.2,
            "minimum_outside_unchanged": 1.0,
            "minimum_red_pixels": 100,
            "deterministic_pixels": True,
            "elapsed_ms": {"p95": 100.0},
            "vertical": {
                "minimum_red_pixels": 100,
                "minimum_aspect_ratio": 2.0,
                "minimum_outside_unchanged": 1.0,
                "deterministic_pixels": True,
                "elapsed_ms": {"p95": 100.0},
            },
            "perspective": {
                "minimum_red_pixels": 100,
                "minimum_outside_quad_unchanged": 1.0,
                "deterministic_pixels": True,
                "elapsed_ms": {"p95": 100.0},
            },
            "curved": {
                "minimum_red_pixels": 100,
                "minimum_center_delta": 12.0,
                "minimum_outside_unchanged": 1.0,
                "deterministic_pixels": True,
                "elapsed_ms": {"p95": 100.0},
            },
            "styled": {
                "case_count": 5,
                "cases": [
                    {
                        "visual_effect": effect,
                        "minimum_primary_pixels": 500,
                        "minimum_secondary_pixels": 0 if effect == "plain" else 500,
                        "minimum_secondary_vertical_offset": (
                            10.0 if effect in {"shadow", "gradient"} else 0.0
                        ),
                        "minimum_halo_pixels": 500 if effect == "glow" else 0,
                        "minimum_outside_unchanged": 1.0,
                        "provenance_matches": True,
                        "deterministic_pixels": True,
                        "elapsed_ms": {"p95": 100.0},
                    }
                    for effect in ("plain", "outline", "shadow", "gradient", "glow")
                ],
            },
        },
        "text_detection": {
            "case_count": 8,
            "cases": [
                {
                    "detected": True,
                    "overlap": 0.8,
                    "angle_error": 0.0,
                    "curve_error": 0.0,
                    "visual_effect": "plain",
                    "estimated_fill_color": "#202020",
                    "fill_color_error": 0,
                    "deterministic": True,
                }
                for _index in range(8)
            ],
            "perspective": {
                "detected": True,
                "overlap": 0.8,
                "scale_delta": 0.08,
                "deterministic": True,
                "elapsed_ms": {"p95": 100.0},
            },
            "multi_plane": {
                "detected": True,
                "candidate_count": 2,
                "distinct_planes": True,
                "overlaps": [0.9, 0.9],
                "reading_order": True,
                "opposite_scale_directions": True,
                "scale_directions": [0.08, -0.08],
                "deterministic": True,
                "elapsed_ms": {"p95": 100.0},
            },
            "occluded_multi_plane": {
                "detected": True,
                "candidate_count": 2,
                "distinct_planes": True,
                "reading_order": True,
                "expected_region_iou": 0.15,
                "ink_occlusion_ratio": 0.05,
                "source_color_distance": 24,
                "source_axis_similarity": 0.9965,
                "coverages": [0.9, 0.9],
                "containments": [0.75, 0.75],
                "opposite_scale_directions": True,
                "scale_directions": [0.08, -0.08],
                "fill_color_errors": [1, 1],
                "deterministic": True,
                "elapsed_ms": {"p95": 100.0},
            },
            "three_plane_overlap": {
                "detected": True,
                "candidate_count": 3,
                "distinct_planes": True,
                "reading_order": True,
                "pairwise_region_ious": [0.08, 0.10, 0.18],
                "pairwise_ink_occlusions": [0.02, 0.04, 0.18],
                "coverages": [0.9, 0.9, 0.9],
                "containments": [0.75, 0.75, 0.75],
                "divergent_scale_directions": True,
                "scale_directions": [0.08, 0.04, -0.08],
                "fill_color_errors": [1, 1, 1],
                "deterministic": True,
                "elapsed_ms": {"p95": 100.0},
                "same_color_safety": {
                    "perspective_candidate_count": 1,
                    "deterministic": True,
                    "elapsed_ms": {"p95": 100.0},
                },
            },
            "curved": {
                "case_count": 6,
                "cases": [
                    {
                        "detected": True,
                        "overlap": 0.9,
                        "curve_error": 0.05,
                        "deterministic": True,
                    }
                    for _index in range(6)
                ],
            },
            "styled": {
                "case_count": 5,
                "cases": [
                    {
                        "expected_effect": effect,
                        "detected": True,
                        "detected_effect": effect,
                        "estimated_fill_color": styled_estimates[effect][0],
                        "fill_color_error": 0,
                        "expected_effect_color": styled_estimates[effect][1],
                        "estimated_effect_color": styled_estimates[effect][1],
                        "effect_color_error": 0,
                        "expected_effect_size": styled_estimates[effect][2],
                        "estimated_effect_size": styled_estimates[effect][2],
                        "effect_size_error": 0,
                        "overlap": 0.9,
                        "deterministic": True,
                        "elapsed_ms": {"p95": 100.0},
                    }
                    for effect in ("plain", "outline", "shadow", "gradient", "glow")
                ],
            },
            "surface_background": {
                "case_count": 2,
                "cases": [
                    {
                        "expected_effect": effect,
                        "detected": True,
                        "detected_effect": effect,
                        "estimated_fill_color": fill_color,
                        "fill_color_error": 0,
                        "expected_effect_color": effect_color,
                        "estimated_effect_color": effect_color,
                        "effect_color_error": 0,
                        "expected_effect_size": effect_size,
                        "estimated_effect_size": effect_size,
                        "effect_size_error": 0,
                        "overlap": 0.9,
                        "deterministic": True,
                        "elapsed_ms": {"p95": 100.0},
                    }
                    for effect, fill_color, effect_color, effect_size in (
                        ("plain", "#202830", None, None),
                        ("glow", "#f8f8ff", "#20b8ff", 7),
                    )
                ],
            },
            "structured_background": {
                "case_count": 2,
                "cases": [
                    {
                        "expected_effect": effect,
                        "detected": True,
                        "detected_effect": effect,
                        "estimated_fill_color": fill_color,
                        "fill_color_error": 0,
                        "expected_effect_color": effect_color,
                        "estimated_effect_color": effect_color,
                        "effect_color_error": 0,
                        "expected_effect_size": effect_size,
                        "estimated_effect_size": effect_size,
                        "effect_size_error": 0,
                        "overlap": 0.9,
                        "deterministic": True,
                        "elapsed_ms": {"p95": 100.0},
                    }
                    for effect, fill_color, effect_color, effect_size in (
                        ("plain", "#18222c", None, None),
                        ("glow", "#f8f8ff", "#20b8ff", 7),
                    )
                ],
            },
            "photo_background": {
                "case_count": 8,
                "cases": [
                    {
                        "seed": seed,
                        "expected_effect": effect,
                        "detected": True,
                        "top_ranked_match": True,
                        "detected_effect": effect,
                        "rotation": 0.0,
                        "estimated_fill_color": fill_color,
                        "fill_color_error": 0,
                        "expected_effect_color": effect_color,
                        "estimated_effect_color": effect_color,
                        "effect_color_error": 0,
                        "expected_effect_size": effect_size,
                        "estimated_effect_size": effect_size,
                        "effect_size_error": 0,
                        "overlap": 0.9,
                        "region_width": 0.6,
                        "region_height": 0.2,
                        "deterministic": True,
                        "elapsed_ms": {"p95": 100.0},
                    }
                    for seed in (20260806, 20260808, 20260811, 20260813)
                    for effect, fill_color, effect_color, effect_size in (
                        ("plain", "#111820", None, None),
                        ("glow", "#f8f8ff", "#20b8ff", 7),
                    )
                ],
                "negative_case_count": 4,
                "negative_cases": [
                    {
                        "seed": seed,
                        "candidate_count": 0,
                        "deterministic": True,
                        "elapsed_ms": {"count": 1, "p95": 100.0},
                    }
                    for seed in (20260806, 20260808, 20260811, 20260813)
                ],
                "negative_candidate_count": 0,
                "negative_deterministic": True,
                "negative_elapsed_ms": {"count": 4, "p95": 100.0},
                "layout_case_count": 5,
                "layout_cases": [
                    {
                        "layout": layout,
                        "required_match_count": 2 if layout == "multiline" else 1,
                        "matched_count": 2 if layout == "multiline" else 1,
                        "top_ranked_match": True,
                        "reading_order": True,
                        "minimum_overlap": 0.9,
                        "maximum_fill_color_error": 0,
                        "deterministic": True,
                        "elapsed_ms": {"p95": 100.0},
                    }
                    for layout in ("small", "top", "bottom", "occluded", "multiline")
                ],
                "jpeg_case_count": 15,
                "jpeg_cases": [
                    {
                        "quality": quality,
                        "layout": layout,
                        "expected_effect": "glow" if layout == "glow" else "plain",
                        "required_match_count": 2 if layout == "multiline" else 1,
                        "matched_count": 2 if layout == "multiline" else 1,
                        "top_ranked_match": True,
                        "reading_order": True,
                        "minimum_overlap": 0.9,
                        "maximum_fill_color_error": 0,
                        "maximum_effect_color_error": 0,
                        "maximum_effect_size_error": 0,
                        "deterministic": True,
                        "elapsed_ms": {"p95": 100.0},
                    }
                    for quality in (95, 75, 50)
                    for layout in ("plain", "glow", "small", "multiline", "occluded")
                ],
                "jpeg_negative_case_count": 12,
                "jpeg_negative_cases": [
                    {
                        "quality": quality,
                        "seed": seed,
                        "candidate_count": 0,
                        "deterministic": True,
                        "elapsed_ms": {"p95": 100.0},
                    }
                    for quality in (95, 75, 50)
                    for seed in (20260806, 20260808, 20260811, 20260813)
                ],
                "jpeg_negative_candidate_count": 0,
                "jpeg_negative_deterministic": True,
            },
            "elapsed_ms": {"p95": 100.0},
        },
        "curved_ocr_rectification": {
            "case_count": 4,
            "cases": [
                {
                    "input_delta_px": 10.0,
                    "maximum_residual_delta_px": 2.0,
                    "maximum_residual_ratio": 0.2,
                    "deterministic_pixels": True,
                    "elapsed_ms": {"p95": 100.0},
                }
                for _index in range(4)
            ],
            "elapsed_ms": {"p95": 100.0},
        },
        "font_matching": {
            "candidate_fonts": ["微软雅黑", "宋体", "黑体"],
            "case_count": 2,
            "cases": [
                {
                    "expected_font": family,
                    "top_font": family,
                    "top_score": 0.8,
                    "top1_correct": True,
                    "deterministic": True,
                    "elapsed_ms": {"p95": 100.0},
                }
                for family in ("微软雅黑", "宋体")
            ],
            "elapsed_ms": {"p95": 100.0},
        },
        "smart_selection": {
            "case_count": 7,
            "cases": [
                {
                    "name": name,
                    "detected": True,
                    "expected_source": source,
                    "source": source,
                    "expected_min_candidates": minimum_candidates,
                    "candidate_count": minimum_candidates,
                    "expected_min_color_split": minimum_color_split,
                    "color_split_candidate_count": minimum_color_split,
                    "split_probe_exclusive": True,
                    "expected_min_shape_split": minimum_shape_split,
                    "shape_split_candidate_count": minimum_shape_split,
                    "shape_probe_exclusive": True,
                    "coverage": 1.0,
                    "iou": 0.85,
                    "deterministic": True,
                    "elapsed_ms": {"p95": 100.0},
                }
                for (
                    name,
                    source,
                    minimum_candidates,
                    minimum_color_split,
                    minimum_shape_split,
                ) in (
                    ("centered_contrast", "contrast", 1, 0, 0),
                    ("edge_gradient", "contrast", 1, 0, 0),
                    ("transparent_alpha", "alpha", 1, 0, 0),
                    ("multiple_subjects", "contrast", 2, 0, 0),
                    ("touching_color_split", "contrast", 3, 2, 0),
                    ("same_color_shape_split", "contrast", 3, 0, 2),
                    ("same_color_moderate_overlap", "contrast", 3, 0, 2),
                )
            ],
            "elapsed_ms": {"p95": 100.0},
        },
    }


class SmartEditQualityGateTests(unittest.TestCase):
    def test_profiles_keep_smoke_small_and_standard_multibackground(self) -> None:
        self.assertLess(
            int(PROFILES["smoke"]["image_size"][0]),
            int(PROFILES["standard"]["image_size"][0]),
        )
        self.assertEqual(len(PROFILES["smoke"]["backgrounds"]), 1)
        self.assertGreaterEqual(len(PROFILES["standard"]["backgrounds"]), 3)

    def test_passing_quality_result_has_no_violations(self) -> None:
        self.assertEqual(evaluate_smart_edit_gate(_passing_result()), [])

    def test_gate_reports_all_independent_quality_failures(self) -> None:
        result = _passing_result()
        result["upscale"]["scales"]["2"]["maximum_alpha_mae"] = 3.0  # type: ignore[index]
        result["upscale"]["scales"]["4"]["deterministic_pixels"] = False  # type: ignore[index]
        result["remove_background"]["case_count"] = 2  # type: ignore[index]
        first_case = result["remove_background"]["cases"][0]  # type: ignore[index]
        first_case["minimum_foreground_recall"] = 0.9
        first_case["minimum_background_removal"] = 0.8
        edge_case = result["edge_background"]["cases"][0]  # type: ignore[index]
        edge_case["maximum_foreground_alpha_mae"] = 10.0
        edge_case["minimum_background_removal"] = 0.8
        result["alpha_refinement"]["preserve_alpha"] = 0  # type: ignore[index]
        result["alpha_refinement"]["source_lineage_matches"] = False  # type: ignore[index]
        replacement_case = result["background_replacement"]["cases"][0]  # type: ignore[index]
        replacement_case["background_pixels"] = [[0, 0, 0]]
        replacement_case["alpha_extrema"] = [[0, 255]]
        replacement_case["deterministic_pixels"] = False
        result["region_repair"]["maximum_region_mae"] = 12.0  # type: ignore[index]
        result["region_repair"]["minimum_outside_unchanged"] = 0.8  # type: ignore[index]
        result["region_repair"]["source_lineage_matches"] = False  # type: ignore[index]
        result["mask_repair"]["minimum_selected_change"] = 0.1  # type: ignore[index]
        result["mask_repair"]["minimum_outside_mask_unchanged"] = 0.8  # type: ignore[index]
        result["mask_repair"]["minimum_unselected_subject_unchanged"] = 0.7  # type: ignore[index]
        result["mask_repair"]["minimum_manual_exclusion_unchanged"] = 0.6  # type: ignore[index]
        result["mask_repair"]["provenance_stores_identity_only"] = False  # type: ignore[index]
        result["text_replacement"]["minimum_region_change"] = 0.0  # type: ignore[index]
        result["text_replacement"]["minimum_outside_unchanged"] = 0.9  # type: ignore[index]
        result["text_replacement"]["minimum_red_pixels"] = 0  # type: ignore[index]
        result["text_replacement"]["vertical"]["minimum_aspect_ratio"] = 0.5  # type: ignore[index]
        result["text_replacement"]["vertical"]["deterministic_pixels"] = False  # type: ignore[index]
        result["text_replacement"]["perspective"]["minimum_red_pixels"] = 0  # type: ignore[index]
        result["text_replacement"]["perspective"]["minimum_outside_quad_unchanged"] = 0.9  # type: ignore[index]
        result["text_replacement"]["curved"]["minimum_center_delta"] = 0.0  # type: ignore[index]
        result["text_replacement"]["curved"]["minimum_outside_unchanged"] = 0.9  # type: ignore[index]
        result["text_replacement"]["curved"]["deterministic_pixels"] = False  # type: ignore[index]
        styled_replacement = result["text_replacement"]["styled"]  # type: ignore[index]
        styled_replacement["case_count"] = 1
        styled_replacement["cases"][0]["minimum_secondary_pixels"] = 1
        styled_replacement["cases"][1]["minimum_primary_pixels"] = 0
        styled_replacement["cases"][2]["minimum_secondary_vertical_offset"] = 0.0
        styled_replacement["cases"][3]["visual_effect"] = "plain"
        styled_replacement["cases"][3]["minimum_outside_unchanged"] = 0.9
        styled_replacement["cases"][3]["provenance_matches"] = False
        styled_replacement["cases"][3]["deterministic_pixels"] = False
        styled_replacement["cases"][3]["elapsed_ms"]["p95"] = 2_000.0
        styled_replacement["cases"][4]["minimum_halo_pixels"] = 0
        detection_case = result["text_detection"]["cases"][0]  # type: ignore[index]
        detection_case["detected"] = False
        result["text_detection"]["cases"][1]["overlap"] = 0.1  # type: ignore[index]
        result["text_detection"]["cases"][2]["angle_error"] = 10.0  # type: ignore[index]
        result["text_detection"]["cases"][3]["curve_error"] = 0.5  # type: ignore[index]
        result["text_detection"]["cases"][4]["visual_effect"] = "shadow"  # type: ignore[index]
        result["text_detection"]["cases"][5]["fill_color_error"] = 255  # type: ignore[index]
        result["text_detection"]["perspective"]["detected"] = False  # type: ignore[index]
        result["text_detection"]["perspective"]["scale_delta"] = 0.0  # type: ignore[index]
        result["text_detection"]["multi_plane"]["distinct_planes"] = False  # type: ignore[index]
        result["text_detection"]["multi_plane"]["reading_order"] = False  # type: ignore[index]
        result["text_detection"]["multi_plane"]["scale_directions"] = [0.0, 0.0]  # type: ignore[index]
        occluded_multi_plane = result["text_detection"]["occluded_multi_plane"]  # type: ignore[index]
        occluded_multi_plane["detected"] = False
        occluded_multi_plane["candidate_count"] = 1
        occluded_multi_plane["distinct_planes"] = False
        occluded_multi_plane["reading_order"] = False
        occluded_multi_plane["expected_region_iou"] = 0.0
        occluded_multi_plane["ink_occlusion_ratio"] = 0.0
        occluded_multi_plane["source_color_distance"] = 0
        occluded_multi_plane["source_axis_similarity"] = 1.0
        occluded_multi_plane["coverages"] = [0.0, 0.0]
        occluded_multi_plane["containments"] = [0.0, 0.0]
        occluded_multi_plane["opposite_scale_directions"] = False
        occluded_multi_plane["scale_directions"] = [0.0, 0.0]
        occluded_multi_plane["fill_color_errors"] = [255, 255]
        occluded_multi_plane["deterministic"] = False
        occluded_multi_plane["elapsed_ms"]["p95"] = 2_000.0
        three_plane = result["text_detection"]["three_plane_overlap"]  # type: ignore[index]
        three_plane["detected"] = False
        three_plane["candidate_count"] = 1
        three_plane["distinct_planes"] = False
        three_plane["reading_order"] = False
        three_plane["pairwise_region_ious"] = [0.0]
        three_plane["pairwise_ink_occlusions"] = [0.0]
        three_plane["coverages"] = [0.0]
        three_plane["containments"] = [0.0]
        three_plane["divergent_scale_directions"] = False
        three_plane["scale_directions"] = [0.0]
        three_plane["fill_color_errors"] = [255]
        three_plane["deterministic"] = False
        three_plane["elapsed_ms"]["p95"] = 2_000.0
        three_plane["same_color_safety"]["perspective_candidate_count"] = 3
        three_plane["same_color_safety"]["deterministic"] = False
        three_plane["same_color_safety"]["elapsed_ms"]["p95"] = 2_000.0
        result["text_detection"]["curved"]["case_count"] = 1  # type: ignore[index]
        result["text_detection"]["curved"]["cases"][0]["curve_error"] = 0.5  # type: ignore[index]
        result["text_detection"]["curved"]["cases"][1]["detected"] = False  # type: ignore[index]
        result["text_detection"]["curved"]["cases"][2]["deterministic"] = False  # type: ignore[index]
        result["text_detection"]["styled"]["case_count"] = 1  # type: ignore[index]
        result["text_detection"]["styled"]["cases"][0]["detected"] = False  # type: ignore[index]
        result["text_detection"]["styled"]["cases"][1]["overlap"] = 0.1  # type: ignore[index]
        result["text_detection"]["styled"]["cases"][2]["deterministic"] = False  # type: ignore[index]
        result["text_detection"]["styled"]["cases"][3]["detected_effect"] = "plain"  # type: ignore[index]
        result["text_detection"]["styled"]["cases"][1]["fill_color_error"] = 255  # type: ignore[index]
        result["text_detection"]["styled"]["cases"][2]["effect_color_error"] = 255  # type: ignore[index]
        result["text_detection"]["styled"]["cases"][3]["effect_size_error"] = 65  # type: ignore[index]
        result["text_detection"]["surface_background"]["case_count"] = 1  # type: ignore[index]
        result["text_detection"]["surface_background"]["cases"][0]["detected"] = False  # type: ignore[index]
        result["text_detection"]["surface_background"]["cases"][1]["effect_color_error"] = 255  # type: ignore[index]
        result["text_detection"]["surface_background"]["cases"][1]["effect_size_error"] = 65  # type: ignore[index]
        result["text_detection"]["structured_background"]["case_count"] = 1  # type: ignore[index]
        result["text_detection"]["structured_background"]["cases"][0]["detected"] = False  # type: ignore[index]
        result["text_detection"]["structured_background"]["cases"][1]["effect_color_error"] = 255  # type: ignore[index]
        result["text_detection"]["structured_background"]["cases"][1]["effect_size_error"] = 65  # type: ignore[index]
        result["text_detection"]["photo_background"]["case_count"] = 1  # type: ignore[index]
        result["text_detection"]["photo_background"]["negative_case_count"] = 1  # type: ignore[index]
        result["text_detection"]["photo_background"]["negative_candidate_count"] = 1  # type: ignore[index]
        result["text_detection"]["photo_background"]["cases"][0]["top_ranked_match"] = False  # type: ignore[index]
        result["text_detection"]["photo_background"]["cases"][0]["region_width"] = 1.0  # type: ignore[index]
        result["text_detection"]["photo_background"]["cases"][1]["effect_color_error"] = 255  # type: ignore[index]
        result["text_detection"]["photo_background"]["layout_case_count"] = 1  # type: ignore[index]
        result["text_detection"]["photo_background"]["layout_cases"][0]["matched_count"] = 0  # type: ignore[index]
        result["text_detection"]["photo_background"]["layout_cases"][1]["reading_order"] = False  # type: ignore[index]
        result["text_detection"]["photo_background"]["jpeg_case_count"] = 1  # type: ignore[index]
        result["text_detection"]["photo_background"]["jpeg_negative_case_count"] = 1  # type: ignore[index]
        result["text_detection"]["photo_background"]["jpeg_negative_candidate_count"] = 1  # type: ignore[index]
        result["text_detection"]["photo_background"]["jpeg_cases"][0]["matched_count"] = 0  # type: ignore[index]
        result["text_detection"]["photo_background"]["jpeg_cases"][1][
            "maximum_effect_color_error"
        ] = 255  # type: ignore[index]
        result["text_detection"]["elapsed_ms"]["p95"] = 2_000.0  # type: ignore[index]
        result["curved_ocr_rectification"]["case_count"] = 1  # type: ignore[index]
        result["curved_ocr_rectification"]["cases"][0]["input_delta_px"] = 0.0  # type: ignore[index]
        result["curved_ocr_rectification"]["cases"][1]["maximum_residual_delta_px"] = 20.0  # type: ignore[index]
        result["curved_ocr_rectification"]["cases"][2]["maximum_residual_ratio"] = 1.0  # type: ignore[index]
        result["curved_ocr_rectification"]["cases"][3]["deterministic_pixels"] = False  # type: ignore[index]
        result["font_matching"]["case_count"] = 1  # type: ignore[index]
        result["font_matching"]["cases"][0]["top1_correct"] = False  # type: ignore[index]
        result["font_matching"]["cases"][1]["deterministic"] = False  # type: ignore[index]
        result["font_matching"]["elapsed_ms"]["p95"] = 2_000.0  # type: ignore[index]
        result["smart_selection"]["case_count"] = 2  # type: ignore[index]
        result["smart_selection"]["cases"][0]["detected"] = False  # type: ignore[index]
        result["smart_selection"]["cases"][1]["coverage"] = 0.2  # type: ignore[index]
        result["smart_selection"]["cases"][1]["iou"] = 0.1  # type: ignore[index]
        result["smart_selection"]["cases"][2]["source"] = "contrast"  # type: ignore[index]
        result["smart_selection"]["cases"][2]["deterministic"] = False  # type: ignore[index]
        result["smart_selection"]["cases"][3]["candidate_count"] = 1  # type: ignore[index]
        result["smart_selection"]["cases"][4]["color_split_candidate_count"] = 1  # type: ignore[index]
        result["smart_selection"]["cases"][4]["split_probe_exclusive"] = False  # type: ignore[index]
        result["smart_selection"]["cases"][5]["shape_split_candidate_count"] = 1  # type: ignore[index]
        result["smart_selection"]["cases"][5]["shape_probe_exclusive"] = False  # type: ignore[index]
        result["smart_selection"]["cases"][6]["shape_split_candidate_count"] = 1  # type: ignore[index]
        result["smart_selection"]["cases"][6]["shape_probe_exclusive"] = False  # type: ignore[index]
        result["smart_selection"]["elapsed_ms"]["p95"] = 2_000.0  # type: ignore[index]

        violations = evaluate_smart_edit_gate(result, SmartEditGateThresholds())

        self.assertGreaterEqual(len(violations), 10)
        self.assertTrue(any("受控照片式" in message for message in violations))
        self.assertTrue(any("Alpha MAE" in message for message in violations))
        self.assertTrue(any("背景替换" in message for message in violations))
        self.assertTrue(any("物体擦除" in message for message in violations))
        self.assertTrue(any("像素蒙版修复" in message for message in violations))
        self.assertTrue(any("前景召回率" in message for message in violations))
        self.assertTrue(any("贴边去背景" in message for message in violations))
        self.assertTrue(any("源修订血缘" in message for message in violations))
        self.assertTrue(any("区域外" in message for message in violations))
        self.assertTrue(any("文字区域检测" in message for message in violations))
        self.assertTrue(any("双平面" in message for message in violations))
        self.assertTrue(any("局部遮挡双色双平面" in message for message in violations))
        self.assertTrue(any("局部遮挡异色三平面" in message for message in violations))
        self.assertTrue(any("同色遮挡多平面" in message for message in violations))
        self.assertTrue(any("复杂字效" in message for message in violations))
        self.assertTrue(any("参数估计" in message for message in violations))
        self.assertTrue(any("非均匀背景" in message for message in violations))
        self.assertTrue(any("结构纹理背景" in message for message in violations))
        self.assertTrue(any("字效替换" in message for message in violations))
        self.assertTrue(any("发光文字" in message for message in violations))
        self.assertTrue(any("OCR 校直" in message for message in violations))
        self.assertTrue(any("字体推荐" in message for message in violations))
        self.assertTrue(any("智能选区" in message for message in violations))

    def test_tiny_real_gate_exercises_all_four_smart_edit_paths(self) -> None:
        result = run("smoke", image_size=(64, 48), repetitions=2)

        self.assertTrue(result["passed"], result["violations"])
        self.assertEqual(set(result["upscale"]["scales"]), {"2", "4"})
        self.assertEqual(result["remove_background"]["case_count"], 1)
        self.assertEqual(result["edge_background"]["case_count"], 2)
        self.assertTrue(
            all(
                case["maximum_foreground_alpha_mae"] == 0
                for case in result["edge_background"]["cases"]
            )
        )
        self.assertLessEqual(result["region_repair"]["maximum_region_mae"], 3.0)
        self.assertEqual(result["region_repair"]["minimum_outside_unchanged"], 1.0)
        self.assertEqual(result["mask_repair"]["minimum_outside_mask_unchanged"], 1.0)
        self.assertEqual(
            result["mask_repair"]["minimum_unselected_subject_unchanged"],
            1.0,
        )
        self.assertEqual(
            result["mask_repair"]["minimum_manual_exclusion_unchanged"],
            1.0,
        )
        self.assertEqual(result["alpha_refinement"]["preserve_alpha"], 255)
        self.assertEqual(result["background_replacement"]["case_count"], 2)
        self.assertTrue(
            all(case["deterministic_pixels"] for case in result["background_replacement"]["cases"])
        )
        self.assertGreater(result["text_replacement"]["minimum_red_pixels"], 0)
        self.assertGreater(
            result["text_replacement"]["vertical"]["minimum_aspect_ratio"],
            1.5,
        )
        self.assertEqual(
            result["text_replacement"]["vertical"]["minimum_outside_unchanged"],
            1.0,
        )
        self.assertEqual(result["text_replacement"]["styled"]["case_count"], 5)
        self.assertTrue(
            all(
                case["provenance_matches"]
                and case["deterministic_pixels"]
                and case["minimum_outside_unchanged"] == 1.0
                for case in result["text_replacement"]["styled"]["cases"]
            )
        )
        self.assertEqual(result["text_detection"]["case_count"], 8)
        self.assertTrue(all(case["detected"] for case in result["text_detection"]["cases"]))
        occluded_multi_plane = result["text_detection"]["occluded_multi_plane"]
        self.assertTrue(occluded_multi_plane["detected"])
        self.assertEqual(occluded_multi_plane["candidate_count"], 2)
        self.assertTrue(occluded_multi_plane["distinct_planes"])
        self.assertTrue(occluded_multi_plane["reading_order"])
        self.assertGreaterEqual(occluded_multi_plane["expected_region_iou"], 0.10)
        self.assertGreaterEqual(occluded_multi_plane["ink_occlusion_ratio"], 0.04)
        self.assertGreaterEqual(occluded_multi_plane["source_color_distance"], 12)
        self.assertLessEqual(occluded_multi_plane["source_color_distance"], 24)
        self.assertGreaterEqual(occluded_multi_plane["source_axis_similarity"], 0.996)
        self.assertLess(occluded_multi_plane["source_axis_similarity"], 0.998)
        self.assertGreaterEqual(min(occluded_multi_plane["coverages"]), 0.85)
        self.assertGreaterEqual(min(occluded_multi_plane["containments"]), 0.65)
        self.assertLessEqual(max(occluded_multi_plane["fill_color_errors"]), 4)
        three_plane = result["text_detection"]["three_plane_overlap"]
        self.assertTrue(three_plane["detected"])
        self.assertEqual(three_plane["candidate_count"], 3)
        self.assertTrue(three_plane["distinct_planes"])
        self.assertTrue(three_plane["reading_order"])
        self.assertEqual(len(three_plane["pairwise_region_ious"]), 3)
        self.assertGreaterEqual(min(three_plane["pairwise_region_ious"]), 0.01)
        self.assertGreaterEqual(min(three_plane["pairwise_ink_occlusions"]), 0.01)
        self.assertGreaterEqual(max(three_plane["pairwise_ink_occlusions"]), 0.15)
        self.assertGreaterEqual(min(three_plane["coverages"]), 0.85)
        self.assertGreaterEqual(min(three_plane["containments"]), 0.65)
        self.assertLessEqual(max(three_plane["fill_color_errors"]), 4)
        self.assertTrue(three_plane["divergent_scale_directions"])
        self.assertTrue(three_plane["deterministic"])
        self.assertLessEqual(
            three_plane["same_color_safety"]["perspective_candidate_count"],
            1,
        )
        self.assertTrue(three_plane["same_color_safety"]["deterministic"])
        self.assertEqual(result["text_detection"]["styled"]["case_count"], 5)
        self.assertTrue(
            all(
                case["detected_effect"] == case["expected_effect"]
                for case in result["text_detection"]["styled"]["cases"]
            )
        )
        self.assertTrue(
            all(
                case["fill_color_error"] <= 8
                and case["effect_color_error"] <= 8
                and case["effect_size_error"] <= 2
                for case in result["text_detection"]["styled"]["cases"]
            )
        )
        self.assertEqual(result["text_detection"]["surface_background"]["case_count"], 2)
        self.assertTrue(
            all(
                case["detected_effect"] == case["expected_effect"]
                and case["fill_color_error"] <= 8
                and case["effect_color_error"] <= 8
                and case["effect_size_error"] <= 2
                for case in result["text_detection"]["surface_background"]["cases"]
            )
        )
        self.assertEqual(result["text_detection"]["structured_background"]["case_count"], 2)
        self.assertTrue(
            all(
                case["detected_effect"] == case["expected_effect"]
                and case["fill_color_error"] <= 8
                and case["effect_color_error"] <= 12
                and case["effect_size_error"] <= 2
                for case in result["text_detection"]["structured_background"]["cases"]
            )
        )
        self.assertEqual(result["text_detection"]["photo_background"]["case_count"], 8)
        self.assertEqual(
            result["text_detection"]["photo_background"]["negative_case_count"],
            4,
        )
        self.assertEqual(
            result["text_detection"]["photo_background"]["negative_candidate_count"],
            0,
        )
        self.assertTrue(
            all(
                case["detected_effect"] == case["expected_effect"]
                and case["top_ranked_match"]
                and case["fill_color_error"] <= 5
                and case["effect_color_error"] <= 12
                and case["effect_size_error"] <= 4
                and case["region_width"] <= 0.75
                and case["region_height"] <= 0.30
                for case in result["text_detection"]["photo_background"]["cases"]
            )
        )
        self.assertEqual(
            result["text_detection"]["photo_background"]["layout_case_count"],
            5,
        )
        self.assertTrue(
            all(
                case["matched_count"] == case["required_match_count"]
                and case["top_ranked_match"]
                and case["reading_order"]
                and case["minimum_overlap"] >= 0.85
                and case["maximum_fill_color_error"] <= 5
                for case in result["text_detection"]["photo_background"]["layout_cases"]
            )
        )
        self.assertEqual(
            result["text_detection"]["photo_background"]["jpeg_case_count"],
            15,
        )
        self.assertEqual(
            result["text_detection"]["photo_background"]["jpeg_negative_case_count"],
            12,
        )
        self.assertEqual(
            result["text_detection"]["photo_background"]["jpeg_negative_candidate_count"],
            0,
        )
        self.assertTrue(
            all(
                case["matched_count"] == case["required_match_count"]
                and case["top_ranked_match"]
                and case["reading_order"]
                and case["minimum_overlap"] >= 0.85
                and case["maximum_fill_color_error"] <= 16
                and case["maximum_effect_color_error"] <= 8
                and case["maximum_effect_size_error"] <= 1
                for case in result["text_detection"]["photo_background"]["jpeg_cases"]
            )
        )
        self.assertEqual(result["curved_ocr_rectification"]["case_count"], 4)
        self.assertTrue(
            all(
                case["maximum_residual_ratio"] <= 0.45
                for case in result["curved_ocr_rectification"]["cases"]
            )
        )
        self.assertEqual(result["font_matching"]["case_count"], 2)
        self.assertTrue(all(case["top1_correct"] for case in result["font_matching"]["cases"]))
        self.assertEqual(result["smart_selection"]["case_count"], 7)
        self.assertTrue(all(case["detected"] for case in result["smart_selection"]["cases"]))


if __name__ == "__main__":
    unittest.main()
