from __future__ import annotations

import tempfile
import tkinter as tk
import unittest
from pathlib import Path

from PIL import Image

from light_image_editor.ui.generation_workspace import (
    GenerationCandidateGrid,
    GenerationCandidateItem,
)


class GenerationCandidateGridTests(unittest.TestCase):
    def test_grid_renders_candidates_and_reports_selection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root_path = Path(directory)
            first_path = root_path / "first.png"
            second_path = root_path / "second.png"
            Image.new("RGB", (40, 30), (80, 120, 180)).save(first_path)
            Image.new("RGBA", (30, 40), (190, 80, 70, 160)).save(second_path)
            selected: list[str] = []
            root = tk.Tk()
            root.withdraw()
            try:
                grid = GenerationCandidateGrid(root, on_select=selected.append)
                grid.pack()
                items = (
                    GenerationCandidateItem("rev-1", "asset-1", first_path, "第一张"),
                    GenerationCandidateItem(
                        "rev-2",
                        "asset-2",
                        second_path,
                        "第二张",
                        favorite=True,
                    ),
                )
                grid.set_candidates(items)
                root.update_idletasks()

                self.assertEqual(grid.items, items)
                self.assertEqual(grid.selected_revision_id, "rev-1")
                self.assertTrue(grid.select("rev-2"))
                self.assertEqual(selected, ["rev-2"])
                self.assertEqual(grid.selected_revision_id, "rev-2")
                self.assertIn("★", str(grid._cards["rev-2"].cget("text")))
                self.assertNotIn("★", str(grid._cards["rev-1"].cget("text")))
                self.assertFalse(grid.select("missing"))
            finally:
                root.destroy()

    def test_grid_rejects_duplicate_revision_identity(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "candidate.png"
            Image.new("RGB", (8, 8), "white").save(path)
            root = tk.Tk()
            root.withdraw()
            try:
                grid = GenerationCandidateGrid(root, on_select=lambda _revision_id: None)
                with self.assertRaises(ValueError):
                    grid.set_candidates(
                        (
                            GenerationCandidateItem("same", "asset-1", path, "一"),
                            GenerationCandidateItem("same", "asset-2", path, "二"),
                        )
                    )
            finally:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
