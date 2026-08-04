import unittest
from pathlib import Path

from installer import (
    APP_REGISTRY_NAME,
    LEGAL_DOCUMENTS,
    PROJECT_EXTENSION,
    PROJECT_PROG_ID,
    SUPPORTED_EXTENSIONS,
    default_apps_uri,
    open_command,
)


class InstallerTests(unittest.TestCase):
    def test_installer_carries_required_legal_documents(self) -> None:
        self.assertEqual(
            LEGAL_DOCUMENTS,
            ("LICENSE", "NOTICE.md", "THIRD_PARTY_LICENSES.md"),
        )

    def test_supported_extensions_cover_documented_image_formats(self) -> None:
        self.assertEqual(
            SUPPORTED_EXTENSIONS,
            (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"),
        )

    def test_open_command_quotes_executable_and_file(self) -> None:
        target = Path(r"C:\Users\Test User\LightImageEditor\ImageEditor.exe")
        self.assertEqual(open_command(target), f'"{target}" "%1"')

    def test_non_destructive_project_has_its_own_file_type(self) -> None:
        self.assertEqual(PROJECT_EXTENSION, ".lieproj")
        self.assertEqual(PROJECT_PROG_ID, "LightImageEditor.Project")
        script = Path("packaging/windows/LightImageEditor.iss").read_text(encoding="utf-8")
        self.assertIn("LightImageEditor.Project", script)
        self.assertIn(r"Software\Classes\.lieproj", script)

    def test_default_apps_uri_targets_per_user_registration_on_windows_11(self) -> None:
        self.assertEqual(
            default_apps_uri(os_build=22631),
            f"ms-settings:defaultapps?registeredAppUser={APP_REGISTRY_NAME}",
        )

    def test_default_apps_uri_uses_general_page_on_windows_10(self) -> None:
        self.assertEqual(default_apps_uri(os_build=19045), "ms-settings:defaultapps")


if __name__ == "__main__":
    unittest.main()
