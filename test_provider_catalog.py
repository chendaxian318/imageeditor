from __future__ import annotations

import unittest

from light_image_editor.providers import ProviderCapability, ProviderManifest
from light_image_editor.services.provider_catalog import (
    GenerationProviderCatalog,
    GenerationProviderProfile,
    ProviderAvailability,
)


def manifest(provider_id: str, *, offline: bool) -> ProviderManifest:
    return ProviderManifest(
        provider_id=provider_id,
        name=provider_id,
        version="1",
        capabilities=(ProviderCapability.GENERATION,),
        models=("image-model",),
        offline=offline,
    )


class GenerationProviderCatalogTests(unittest.TestCase):
    def test_catalog_exposes_ready_default_and_cost_estimate(self) -> None:
        unavailable = GenerationProviderProfile(
            manifest=manifest("cloud.pending", offline=False),
            model_id="image-model",
            display_name="云端服务",
            availability=ProviderAvailability.NEEDS_CONFIGURATION,
            status_detail="需要配置",
            estimated_cost_minor_per_image=8,
        )
        offline = GenerationProviderProfile(
            manifest=manifest("offline.ready", offline=True),
            model_id="image-model",
            display_name="本地服务",
            status_detail="已就绪",
        )
        catalog = GenerationProviderCatalog((unavailable, offline))

        self.assertIs(catalog.default(), offline)
        self.assertFalse(unavailable.selectable)
        self.assertTrue(offline.selectable)
        self.assertEqual(unavailable.estimated_cost_minor(4), 32)
        self.assertIs(catalog.by_display_name("云端服务"), unavailable)

    def test_catalog_rejects_ambiguous_or_incompatible_profiles(self) -> None:
        profile = GenerationProviderProfile(
            manifest=manifest("offline.ready", offline=True),
            model_id="image-model",
            display_name="本地服务",
        )
        with self.assertRaises(ValueError):
            GenerationProviderCatalog((profile, profile))
        with self.assertRaises(ValueError):
            GenerationProviderProfile(
                manifest=manifest("offline.other", offline=True),
                model_id="unknown-model",
                display_name="错误服务",
            )
        with self.assertRaises(ValueError):
            GenerationProviderProfile(
                manifest=manifest("cloud.cost", offline=False),
                model_id="image-model",
                display_name="费用错误",
                estimated_cost_minor_per_image=-1,
            )


if __name__ == "__main__":
    unittest.main()
