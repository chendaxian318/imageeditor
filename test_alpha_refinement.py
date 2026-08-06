from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote, urlparse

from PIL import Image

from light_image_editor.domain import (
    AlphaBrushStroke,
    AssetRevision,
    ContentEditRequest,
    Provenance,
    parse_alpha_strokes,
    serialize_alpha_strokes,
)
from light_image_editor.providers import MOCK_MODEL_ID, MockProvider


class AlphaRefinementTests(unittest.TestCase):
    def test_strokes_round_trip_strictly_and_reject_invalid_coordinates(self) -> None:
        strokes = (
            AlphaBrushStroke("preserve", 0.05, ((0.1, 0.2), (0.9, 0.2))),
            AlphaBrushStroke("remove", 0.02, ((0.5, 0.5),)),
        )
        self.assertEqual(parse_alpha_strokes(serialize_alpha_strokes(strokes)), strokes)
        with self.assertRaises(ValueError):
            AlphaBrushStroke("unknown", 0.05, ((0.5, 0.5),))
        with self.assertRaises(ValueError):
            AlphaBrushStroke("remove", 0.05, ((1.1, 0.5),))
        with self.assertRaises(ValueError):
            parse_alpha_strokes('[{"mode":"remove"}]')

    def test_provider_applies_ordered_preserve_and_remove_strokes_to_real_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_path = root / "transparent.png"
            Image.new("RGBA", (100, 60), (180, 40, 55, 0)).save(source_path)
            source = AssetRevision.from_content_uri(
                asset_id="asset-alpha",
                sequence=2,
                content_uri=source_path.resolve().as_uri(),
                media_type="image/png",
                provenance=Provenance.imported(source=source_path.name),
                width=100,
                height=60,
                revision_id="rev-alpha-source",
            )
            strokes = (
                AlphaBrushStroke("preserve", 0.10, ((0.2, 0.5), (0.8, 0.5))),
                AlphaBrushStroke("remove", 0.03, ((0.5, 0.5),)),
            )
            provider = MockProvider(output_directory=root / "outputs")

            job = provider.submit_edit(
                ContentEditRequest(
                    source_revision=source,
                    prompt="细化透明边缘",
                    model=MOCK_MODEL_ID,
                    parameters=(
                        ("operation", "refine_alpha"),
                        ("strokes", serialize_alpha_strokes(strokes)),
                    ),
                    request_id="request-alpha-refine",
                    idempotency_key="key-alpha-refine",
                )
            )

            revision = job.outputs[0].current_revision
            path = Path(unquote(urlparse(revision.content_uri).path.lstrip("/")))
            with Image.open(path) as output:
                output.load()
                self.assertEqual(output.mode, "RGBA")
                self.assertEqual(output.getpixel((25, 30))[3], 255)
                self.assertEqual(output.getpixel((50, 30))[3], 0)
                self.assertEqual(output.getpixel((5, 5))[3], 0)
            self.assertEqual(
                revision.provenance.source_revision_ids,
                (source.revision_id,),
            )
            self.assertEqual(revision.provenance.parameters["operation"], "refine_alpha")


if __name__ == "__main__":
    unittest.main()
