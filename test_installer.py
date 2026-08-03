from pathlib import Path
import unittest

from installer import APP_REGISTRY_NAME, SUPPORTED_EXTENSIONS, default_apps_uri, open_command


class InstallerTests(unittest.TestCase):
    def test_supported_extensions_cover_documented_image_formats(self) -> None:
        self.assertEqual(
            SUPPORTED_EXTENSIONS,
            (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"),
        )

    def test_open_command_quotes_executable_and_file(self) -> None:
        target = Path(r"C:\Users\Test User\LightImageEditor\ImageEditor.exe")
        self.assertEqual(open_command(target), f'"{target}" "%1"')

    def test_default_apps_uri_targets_per_user_registration_on_windows_11(self) -> None:
        self.assertEqual(
            default_apps_uri(os_build=22631),
            f"ms-settings:defaultapps?registeredAppUser={APP_REGISTRY_NAME}",
        )

    def test_default_apps_uri_uses_general_page_on_windows_10(self) -> None:
        self.assertEqual(default_apps_uri(os_build=19045), "ms-settings:defaultapps")


if __name__ == "__main__":
    unittest.main()
