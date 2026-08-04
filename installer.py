from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tkinter as tk
import winreg
from pathlib import Path
from tkinter import messagebox, ttk
from urllib.parse import quote

from light_image_editor.identity import (
    DESCRIPTION,
    EXECUTABLE_NAME,
    PRODUCT_NAME_ZH,
    PROG_ID,
    REGISTRY_NAME,
)
from light_image_editor.single_instance import request_running_app_exit

APP_NAME = PRODUCT_NAME_ZH
EXE_NAME = EXECUTABLE_NAME
APP_REGISTRY_NAME = REGISTRY_NAME
SUPPORTED_EXTENSIONS = (
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
)
PROJECT_EXTENSION = ".lieproj"
PROJECT_PROG_ID = "LightImageEditor.Project"
CONTEXT_MENU_KEY = r"Software\Classes\SystemFileAssociations\image\shell\LightImageEditor"
CAPABILITIES_KEY = rf"Software\{APP_REGISTRY_NAME}\Capabilities"
REGISTERED_APPLICATIONS_KEY = r"Software\RegisteredApplications"
PROG_ID_KEY = rf"Software\Classes\{PROG_ID}"
APPLICATION_KEY = rf"Software\Classes\Applications\{EXE_NAME}"
LEGAL_DOCUMENTS = ("LICENSE", "NOTICE.md", "PRIVACY.md", "THIRD_PARTY_LICENSES.md")


def resource_path(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).parent))
    return base / name


def create_shortcut(target: Path, shortcut: Path) -> None:
    shortcut.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut('{shortcut}')
$shortcut.TargetPath = '{target}'
$shortcut.WorkingDirectory = '{target.parent}'
$shortcut.IconLocation = '{target},0'
$shortcut.Save()
"""
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], check=True
    )


def request_app_shutdown() -> None:
    """Ask the running application to save/cancel/exit through its IPC channel."""

    response = request_running_app_exit()
    if not response.get("ok") or not response.get("accepted", True):
        raise RuntimeError("主程序仍在运行或用户取消退出；安装已安全取消。")


def register_context_menu(target: Path) -> None:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CONTEXT_MENU_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"用{APP_NAME}编辑")
        winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, str(target))
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CONTEXT_MENU_KEY + r"\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{target}" "%1"')


def open_command(target: Path) -> str:
    return f'"{target}" "%1"'


def default_apps_uri(os_build: int | None = None) -> str:
    if os_build is None:
        try:
            os_build = sys.getwindowsversion().build
        except AttributeError:
            os_build = 0
    if os_build >= 22000:
        return f"ms-settings:defaultapps?registeredAppUser={quote(APP_REGISTRY_NAME, safe='')}"
    return "ms-settings:defaultapps"


def notify_shell_associations_changed() -> None:
    try:
        import ctypes

        ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)
    except (AttributeError, OSError):
        pass


def register_file_associations(target: Path) -> None:
    command = open_command(target)

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROG_ID_KEY) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"{APP_NAME} 图像")
        winreg.SetValueEx(key, "FriendlyTypeName", 0, winreg.REG_SZ, f"{APP_NAME} 图像")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROG_ID_KEY + r"\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{target}",0')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, PROG_ID_KEY + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CAPABILITIES_KEY) as key:
        winreg.SetValueEx(key, "ApplicationName", 0, winreg.REG_SZ, APP_NAME)
        winreg.SetValueEx(
            key,
            "ApplicationDescription",
            0,
            winreg.REG_SZ,
            DESCRIPTION,
        )
        winreg.SetValueEx(key, "ApplicationIcon", 0, winreg.REG_SZ, f'"{target}",0')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, CAPABILITIES_KEY + r"\FileAssociations") as key:
        for extension in SUPPORTED_EXTENSIONS:
            winreg.SetValueEx(key, extension, 0, winreg.REG_SZ, PROG_ID)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTERED_APPLICATIONS_KEY) as key:
        winreg.SetValueEx(key, APP_REGISTRY_NAME, 0, winreg.REG_SZ, CAPABILITIES_KEY)

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, APPLICATION_KEY) as key:
        winreg.SetValueEx(key, "FriendlyAppName", 0, winreg.REG_SZ, APP_NAME)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, APPLICATION_KEY + r"\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{target}",0')
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, APPLICATION_KEY + r"\shell\open\command"
    ) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, APPLICATION_KEY + r"\SupportedTypes") as key:
        for extension in SUPPORTED_EXTENSIONS:
            winreg.SetValueEx(key, extension, 0, winreg.REG_SZ, "")

    project_key = rf"Software\Classes\{PROJECT_PROG_ID}"
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, project_key) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f"{APP_NAME} 编辑项目")
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, project_key + r"\DefaultIcon") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, f'"{target}",0')
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, project_key + r"\shell\open\command") as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, command)
    with winreg.CreateKey(
        winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROJECT_EXTENSION}"
    ) as key:
        winreg.SetValueEx(key, "", 0, winreg.REG_SZ, PROJECT_PROG_ID)

    for extension in SUPPORTED_EXTENSIONS:
        with winreg.CreateKey(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\{extension}\OpenWithProgids",
        ) as key:
            winreg.SetValueEx(key, PROG_ID, 0, winreg.REG_NONE, b"")
    notify_shell_associations_changed()


def delete_registry_tree(root, subkey: str) -> None:
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
            while True:
                try:
                    child = winreg.EnumKey(key, 0)
                except OSError:
                    break
                delete_registry_tree(root, subkey + "\\" + child)
        winreg.DeleteKey(root, subkey)
    except FileNotFoundError:
        pass


def unregister_context_menu() -> None:
    delete_registry_tree(winreg.HKEY_CURRENT_USER, CONTEXT_MENU_KEY)


def delete_registry_value(root, subkey: str, value_name: str) -> None:
    try:
        with winreg.OpenKey(root, subkey, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, value_name)
    except FileNotFoundError:
        pass


def unregister_file_associations() -> None:
    delete_registry_tree(winreg.HKEY_CURRENT_USER, PROG_ID_KEY)
    delete_registry_tree(winreg.HKEY_CURRENT_USER, rf"Software\{APP_REGISTRY_NAME}")
    delete_registry_tree(winreg.HKEY_CURRENT_USER, APPLICATION_KEY)
    delete_registry_tree(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROJECT_PROG_ID}")
    delete_registry_tree(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROJECT_EXTENSION}")
    delete_registry_value(winreg.HKEY_CURRENT_USER, REGISTERED_APPLICATIONS_KEY, APP_REGISTRY_NAME)
    for extension in SUPPORTED_EXTENSIONS:
        delete_registry_value(
            winreg.HKEY_CURRENT_USER,
            rf"Software\Classes\{extension}\OpenWithProgids",
            PROG_ID,
        )
    notify_shell_associations_changed()


def open_default_apps_settings() -> None:
    os.startfile(default_apps_uri())


def install_app() -> Path:
    source = resource_path(EXE_NAME)
    if not source.exists():
        raise FileNotFoundError(f"安装包缺少 {EXE_NAME}")

    install_dir = Path(os.environ["LOCALAPPDATA"]) / "LightImageEditor"
    install_dir.mkdir(parents=True, exist_ok=True)
    target = install_dir / EXE_NAME
    request_app_shutdown()
    shutil.copy2(source, target)
    for filename in LEGAL_DOCUMENTS:
        source_document = resource_path(filename)
        if not source_document.exists():
            raise FileNotFoundError(f"安装包缺少 {filename}")
        shutil.copy2(source_document, install_dir / filename)

    start_menu = (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / f"{APP_NAME}.lnk"
    )
    desktop = Path(os.environ["USERPROFILE"]) / "Desktop" / f"{APP_NAME}.lnk"
    create_shortcut(target, start_menu)
    create_shortcut(target, desktop)
    register_context_menu(target)
    register_file_associations(target)
    return target


def uninstall_app() -> None:
    install_dir = Path(os.environ["LOCALAPPDATA"]) / "LightImageEditor"
    start_menu = (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / f"{APP_NAME}.lnk"
    )
    desktop = Path(os.environ["USERPROFILE"]) / "Desktop" / f"{APP_NAME}.lnk"
    for shortcut in (start_menu, desktop):
        if shortcut.exists():
            shortcut.unlink()
    unregister_context_menu()
    unregister_file_associations()
    if install_dir.exists():
        shutil.rmtree(install_dir)


class Installer(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} 安装程序")
        self.geometry("460x270")
        self.resizable(False, False)
        self.columnconfigure(0, weight=1)

        ttk.Label(self, text=APP_NAME, font=("Microsoft YaHei UI", 18, "bold")).grid(
            row=0, column=0, sticky="w", padx=24, pady=(22, 8)
        )
        ttk.Label(
            self,
            text="安装到当前用户账户，无需管理员权限。安装完成后会创建开始菜单和桌面快捷方式。",
            wraplength=390,
        ).grid(row=1, column=0, sticky="ew", padx=24)
        self.open_defaults_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self,
            text="安装后打开 Windows“默认应用”设置",
            variable=self.open_defaults_var,
        ).grid(row=2, column=0, sticky="w", padx=24, pady=(16, 0))
        ttk.Label(
            self,
            text="Windows 会列出本软件支持的图片格式，默认应用仍由你亲自确认。",
            wraplength=400,
        ).grid(row=3, column=0, sticky="w", padx=44, pady=(4, 0))
        self.status = tk.StringVar(value="准备安装")
        ttk.Label(self, textvariable=self.status).grid(
            row=4, column=0, sticky="w", padx=24, pady=(18, 8)
        )
        button_row = ttk.Frame(self)
        button_row.grid(row=5, column=0, sticky="e", padx=24, pady=(10, 0))
        ttk.Button(button_row, text="安装", command=self.install).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(button_row, text="取消", command=self.destroy).grid(row=0, column=1)

    def install(self) -> None:
        try:
            self.status.set("正在复制文件并创建快捷方式...")
            self.update_idletasks()
            target = install_app()
        except Exception as exc:
            messagebox.showerror(APP_NAME, f"安装失败：{exc}")
            self.status.set("安装失败")
            return
        self.status.set("安装完成")
        if self.open_defaults_var.get():
            try:
                open_default_apps_settings()
            except OSError as exc:
                messagebox.showwarning(APP_NAME, f"无法打开 Windows 默认应用设置：{exc}")
        if messagebox.askyesno(APP_NAME, "安装完成。现在打开图片编辑器吗？"):
            subprocess.Popen([str(target)], cwd=str(target.parent))
        self.destroy()


if __name__ == "__main__":
    if "--silent" in sys.argv:
        install_app()
    elif "--uninstall-silent" in sys.argv:
        uninstall_app()
    else:
        Installer().mainloop()
