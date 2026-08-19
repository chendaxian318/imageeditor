import unittest
from pathlib import Path

from installer import (
    APP_REGISTRY_NAME,
    CONTEXT_MENU_EXTENSION_KEYS,
    LEGAL_DOCUMENTS,
    PROJECT_EXTENSION,
    PROJECT_PROG_ID,
    SUPPORTED_EXTENSIONS,
    default_apps_uri,
    open_command,
)


class InstallerTests(unittest.TestCase):
    def test_default_install_directory_uses_named_folder_on_d_drive(self) -> None:
        script = Path("packaging/windows/LightImageEditor.iss").read_text(encoding="utf-8")
        self.assertIn(r"DefaultDirName=D:\CSImage", script)
        self.assertIn("DisableDirPage=no", script)

    def test_directory_picker_does_not_scan_disconnected_network_drives(self) -> None:
        script = Path("packaging/windows/LightImageEditor.iss").read_text(encoding="utf-8")
        self.assertIn("AllowNetworkDrive=no", script)
        self.assertIn("AllowUNCPath=no", script)
        self.assertIn("WizardForm.DirEdit.AutoCompleteFiles := False", script)
        self.assertIn("WizardForm.DirBrowseButton.OnClick := @ShowLocalFolderBrowser", script)
        self.assertIn("GetDriveTypeW(DrivePath) = 3", script)
        self.assertIn("TNewListBox.Create", script)
        self.assertIn("RefreshLocalFolderList", script)
        self.assertIn("FindFirst(AddBackslash(FolderCurrentDirectory)", script)
        self.assertIn("FILE_ATTRIBUTE_REPARSE_POINT", script)
        self.assertNotIn("TFolderTreeView.Create", script)

    def test_installer_carries_required_legal_documents(self) -> None:
        self.assertEqual(
            LEGAL_DOCUMENTS,
            ("LICENSE", "NOTICE.md", "PRIVACY.md", "THIRD_PARTY_LICENSES.md"),
        )

    def test_user_facing_installer_notice_does_not_expose_repository_urls(self) -> None:
        script = Path("packaging/windows/LightImageEditor.iss").read_text(encoding="utf-8")
        self.assertIn(r"InfoAfterFile={#SourcePath}\INSTALLER_NOTICE.txt", script)
        self.assertNotIn("AppPublisherURL=", script)
        self.assertNotIn("AppSupportURL=", script)
        self.assertNotIn("AppUpdatesURL=", script)
        notice = Path("packaging/windows/INSTALLER_NOTICE.txt").read_text(encoding="utf-8")
        for forbidden in ("http://", "https://", "github", "gitee"):
            self.assertNotIn(forbidden, notice.casefold())

    def test_license_agreement_defaults_to_accepted(self) -> None:
        script = Path("packaging/windows/LightImageEditor.iss").read_text(encoding="utf-8")
        self.assertIn(r"LicenseFile={#SourcePath}\INSTALLER_LICENSE.txt", script)
        self.assertIn("WizardForm.LicenseAcceptedRadio.Checked := True", script)

    def test_user_facing_installer_license_does_not_expose_repository_urls(self) -> None:
        license_text = Path("packaging/windows/INSTALLER_LICENSE.txt").read_text(encoding="utf-8")
        for forbidden in ("http://", "https://", "github", "gitee"):
            self.assertNotIn(forbidden, license_text.casefold())

    def test_upgrade_removes_stale_pyinstaller_runtime_before_copying_viewer(self) -> None:
        script = Path("packaging/windows/LightImageEditor.iss").read_text(encoding="utf-8")
        self.assertIn("[InstallDelete]", script)
        self.assertIn('Type: filesandordirs; Name: "{app}\\_internal"', script)

    def test_upgrade_can_replace_an_idle_broken_executable(self) -> None:
        script = Path("packaging/windows/LightImageEditor.iss").read_text(encoding="utf-8")
        self.assertIn("function ExecutableCanBeReplaced", script)
        self.assertIn("fmOpenReadWrite or fmShareExclusive", script)
        self.assertNotIn(".upgrade-probe", script)
        self.assertIn("if ExecutableCanBeReplaced(ExecutablePath) then", script)
        self.assertLess(
            script.index("if ExecutableCanBeReplaced(ExecutablePath) then"),
            script.index("'--request-exit-for-upgrade'"),
        )

    def test_supported_extensions_cover_documented_image_formats(self) -> None:
        self.assertEqual(
            SUPPORTED_EXTENSIONS,
            (
                ".jpg",
                ".jpeg",
                ".png",
                ".apng",
                ".webp",
                ".avif",
                ".heic",
                ".heif",
                ".bmp",
                ".tif",
                ".tiff",
                ".gif",
                ".psd",
                ".psdt",
                ".pdf",
                ".svg",
                ".ai",
                ".eps",
                ".emf",
                ".wmf",
                ".tga",
                ".dds",
                ".ico",
                ".exr",
                ".hdr",
                ".ktx2",
            ),
        )

    def test_non_raster_formats_have_explicit_context_menu_keys(self) -> None:
        self.assertEqual(
            CONTEXT_MENU_EXTENSION_KEYS,
            (
                r"Software\Classes\SystemFileAssociations\.psd\shell\LightImageEditor",
                r"Software\Classes\SystemFileAssociations\.psdt\shell\LightImageEditor",
                r"Software\Classes\SystemFileAssociations\.svg\shell\LightImageEditor",
                r"Software\Classes\SystemFileAssociations\.ai\shell\LightImageEditor",
                r"Software\Classes\SystemFileAssociations\.eps\shell\LightImageEditor",
                r"Software\Classes\SystemFileAssociations\.emf\shell\LightImageEditor",
                r"Software\Classes\SystemFileAssociations\.wmf\shell\LightImageEditor",
                r"Software\Classes\SystemFileAssociations\.tga\shell\LightImageEditor",
                r"Software\Classes\SystemFileAssociations\.dds\shell\LightImageEditor",
                r"Software\Classes\SystemFileAssociations\.ico\shell\LightImageEditor",
                r"Software\Classes\SystemFileAssociations\.exr\shell\LightImageEditor",
                r"Software\Classes\SystemFileAssociations\.hdr\shell\LightImageEditor",
                r"Software\Classes\SystemFileAssociations\.ktx2\shell\LightImageEditor",
            ),
        )
        script = Path("packaging/windows/LightImageEditor.iss").read_text(encoding="utf-8")
        for extension in (
            ".psd",
            ".psdt",
            ".svg",
            ".ai",
            ".eps",
            ".emf",
            ".wmf",
            ".tga",
            ".dds",
            ".ico",
            ".exr",
            ".hdr",
            ".ktx2",
        ):
            self.assertIn(
                rf"Software\Classes\SystemFileAssociations\{extension}\shell\LightImageEditor",
                script,
            )
            self.assertIn(f'ValueName: "{extension}"; ValueData: "{{#ProgId}}"', script)

    def test_open_command_quotes_executable_and_file(self) -> None:
        target = Path(r"C:\Users\Test User\LightImageEditor\CSImage.exe")
        self.assertEqual(open_command(target), f'"{target}" "%1"')

    def test_hidden_project_format_is_not_registered_by_viewer_installer(self) -> None:
        self.assertEqual(PROJECT_EXTENSION, ".lieproj")
        self.assertEqual(PROJECT_PROG_ID, "LightImageEditor.Project")
        script = Path("packaging/windows/LightImageEditor.iss").read_text(encoding="utf-8")
        self.assertNotIn("LightImageEditor.Project", script)
        self.assertNotIn(r"Software\Classes\.lieproj", script)

    def test_default_apps_uri_targets_per_user_registration_on_windows_11(self) -> None:
        self.assertEqual(
            default_apps_uri(os_build=22631),
            f"ms-settings:defaultapps?registeredAppUser={APP_REGISTRY_NAME}",
        )

    def test_default_apps_uri_uses_general_page_on_windows_10(self) -> None:
        self.assertEqual(default_apps_uri(os_build=19045), "ms-settings:defaultapps")


if __name__ == "__main__":
    unittest.main()
