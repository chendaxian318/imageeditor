"""Probe the current Windows host and four Material You DPI layout scales."""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import ctypes
import json
import platform
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from PIL import ImageDraw

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from light_image_editor.dpi import WorkArea, dpi_scale, fit_geometry_to_work_area, parse_geometry
from light_image_editor.session import SessionStore
from light_image_editor.settings import SettingsStore
from light_image_editor.shell import ModeId
from light_image_editor.ui.antialias import render_antialiased
from light_image_editor.ui.main_window import ImageEditorApp
from light_image_editor.version import __version__
from light_image_editor.windows_effects import rounded_region_metrics

PROBE_DPIS = (96, 120, 144, 192)


@dataclass(frozen=True, slots=True)
class CompatibilityThresholds:
    required_scales: tuple[int, ...] = PROBE_DPIS
    minimum_windows_10_build: int = 19045
    minimum_windows_11_build: int = 22000
    required_pointer_bits: int = 64
    minimum_virtual_transitions: int = 1_000


def _windows_host() -> dict[str, object]:
    if sys.platform != "win32":
        return {
            "is_windows": False,
            "major": 0,
            "minor": 0,
            "build": 0,
            "release": platform.release(),
            "pointer_bits": ctypes.sizeof(ctypes.c_void_p) * 8,
            "per_monitor_v2_api": False,
            "get_dpi_for_system_api": False,
        }
    version = sys.getwindowsversion()
    user32 = ctypes.windll.user32
    return {
        "is_windows": True,
        "major": version.major,
        "minor": version.minor,
        "build": version.build,
        "release": platform.release(),
        "pointer_bits": ctypes.sizeof(ctypes.c_void_p) * 8,
        "per_monitor_v2_api": hasattr(user32, "SetProcessDpiAwarenessContext"),
        "get_dpi_for_system_api": hasattr(user32, "GetDpiForSystem"),
    }


def _antialias_probe(scale: float) -> bool:
    size = max(24, round(24 * scale))

    def paint(draw: ImageDraw.ImageDraw, supersample: int) -> None:
        draw.rounded_rectangle(
            (
                2 * supersample,
                2 * supersample,
                (size - 2) * supersample,
                (size - 2) * supersample,
            ),
            radius=max(2, round(8 * scale)) * supersample,
            fill="#67507a",
        )

    image = render_antialiased(size, size, paint)
    try:
        alphas = {
            cast(tuple[int, int, int, int], image.getpixel((x, y)))[3]
            for y in range(image.height)
            for x in range(image.width)
        }
        return 0 in alphas and 255 in alphas and any(0 < value < 255 for value in alphas)
    finally:
        image.close()


def _inside(parent: ImageEditorApp, widget: Any, *, tolerance: int = 4) -> bool:
    left = widget.winfo_rootx() - parent.winfo_rootx()
    top = widget.winfo_rooty() - parent.winfo_rooty()
    return (
        left >= -tolerance
        and top >= -tolerance
        and left + widget.winfo_width() <= parent.winfo_width() + tolerance
        and top + widget.winfo_height() <= parent.winfo_height() + tolerance
    )


def _probe_scale(app: ImageEditorApp, dpi: int) -> dict[str, object]:
    scale = dpi / 96.0
    app.tk.call("tk", "scaling", dpi / 72.0)
    app.update_idletasks()
    app.update()
    mode_results: dict[str, bool] = {}
    for mode in ModeId:
        app._activate_mode(mode)
        app.update_idletasks()
        app.update()
        mode_ok = (
            app.mode_router.active_mode is mode
            and app.mode_bar.winfo_ismapped()
            and app.task_tray.trigger_button.winfo_ismapped()
            and all(button.winfo_ismapped() for button in app.mode_buttons.values())
            and _inside(app, app.mode_bar)
            and _inside(app, app.command_dock)
        )
        if mode is ModeId.GENERATE:
            mode_ok = (
                mode_ok
                and app.generate_workspace.winfo_ismapped()
                and _inside(app, app.generate_workspace)
                and app.generation_candidate_panel.winfo_rootx()
                > app.generation_setup_panel.winfo_rootx()
            )
        elif mode is ModeId.EDIT:
            mode_ok = mode_ok and app.panel_shell.winfo_ismapped() and _inside(app, app.panel_shell)
        mode_results[mode.value] = mode_ok
    font_width = int(app.tk.call("font", "measure", "TkDefaultFont", "中文控件"))
    region = rounded_region_metrics(1240, 780, 28, scale)
    expected_diameter = min(1240, 780, round(56 * scale))
    return {
        "dpi": dpi,
        "scale_percent": round(scale * 100),
        "tk_scaling": round(float(app.tk.call("tk", "scaling")), 6),
        "mode_layouts": mode_results,
        "all_mode_layouts_fit": all(mode_results.values()),
        "font_measurement_px": font_width,
        "font_rendering_available": font_width > 0,
        "antialiased_rounding": _antialias_probe(scale),
        "rounded_region_diameter": region[2],
        "rounded_region_scales": region[2] == expected_diameter,
    }


def _virtual_monitor_probe(iterations: int = 1_000) -> dict[str, object]:
    monitors = (
        ("主屏", WorkArea(0, 0, 2560, 1400), 96),
        ("左侧副屏", WorkArea(-1920, 0, 0, 1040), 120),
        ("上方副屏", WorkArea(0, -1440, 2560, 0), 144),
        ("右侧高 DPI", WorkArea(2560, -200, 4480, 880), 192),
        ("小工作区", WorkArea(-800, -600, 0, 0), 192),
    )
    failures: list[str] = []
    clamped = 0
    samples: list[dict[str, object]] = []
    for index in range(iterations):
        name, work, dpi = monitors[index % len(monitors)]
        scale = dpi_scale(dpi)
        desired_width = round(1240 * scale)
        desired_height = round(780 * scale)
        desired_x = work.left - 120 if index % 2 else work.right - desired_width + 120
        desired_y = work.top - 90 if index % 3 else work.bottom - desired_height + 90
        raw = f"{desired_width}x{desired_height}+{desired_x}+{desired_y}"
        fitted = fit_geometry_to_work_area(raw, work)
        parsed = None if fitted is None else parse_geometry(fitted)
        if parsed is None:
            failures.append(f"{name}: 无法解析 {fitted}")
            continue
        width, height, x, y = parsed
        inside = (
            x >= work.left + 16
            and y >= work.top + 16
            and x + width <= work.right - 16
            and y + height <= work.bottom - 16
        )
        if not inside:
            failures.append(f"{name}: {fitted} 越出工作区")
        clamped += fitted != raw
        if index < len(monitors):
            samples.append(
                {
                    "name": name,
                    "dpi": dpi,
                    "work_area": [work.left, work.top, work.right, work.bottom],
                    "requested": raw,
                    "fitted": fitted,
                    "inside": inside,
                }
            )
    return {
        "passed": not failures and clamped == iterations,
        "transitions": iterations,
        "negative_coordinate_monitors": 3,
        "clamped_transitions": clamped,
        "failures": failures[:10],
        "samples": samples,
    }


def evaluate_compatibility_probe(
    result: dict[str, Any],
    thresholds: CompatibilityThresholds | None = None,
) -> list[str]:
    limits = thresholds or CompatibilityThresholds()
    violations: list[str] = []
    host = result["host"]
    if not bool(host["is_windows"]):
        violations.append("探针未在 Windows 实机运行")
    build = int(host["build"])
    major = int(host["major"])
    minimum_build = (
        limits.minimum_windows_11_build
        if major >= 10 and build >= 22000
        else limits.minimum_windows_10_build
    )
    if build < minimum_build:
        violations.append(f"Windows 构建 {build} < {minimum_build}")
    if int(host["pointer_bits"]) != limits.required_pointer_bits:
        violations.append(f"进程不是 {limits.required_pointer_bits} 位")
    if not bool(host["per_monitor_v2_api"]):
        violations.append("系统缺少 Per-Monitor V2 DPI API")
    observed = tuple(int(item["dpi"]) for item in result["scales"])
    if observed != limits.required_scales:
        violations.append(f"DPI 样本 {observed} 与要求 {limits.required_scales} 不一致")
    for item in result["scales"]:
        dpi = int(item["dpi"])
        if not bool(item["all_mode_layouts_fit"]):
            violations.append(f"{dpi} DPI 四模式布局越界")
        if not bool(item["font_rendering_available"]):
            violations.append(f"{dpi} DPI 中文字体不可测量")
        if not bool(item["antialiased_rounding"]):
            violations.append(f"{dpi} DPI 圆角抗锯齿失败")
        if not bool(item["rounded_region_scales"]):
            violations.append(f"{dpi} DPI 窗口圆角缩放错误")
    virtual = result["virtual_monitors"]
    if int(virtual["transitions"]) < limits.minimum_virtual_transitions:
        violations.append("虚拟多屏移窗样本不足")
    if not bool(virtual["passed"]):
        violations.append("虚拟多屏负坐标或混合 DPI 移窗失败")
    return violations


def run() -> dict[str, Any]:
    host = _windows_host()
    with tempfile.TemporaryDirectory(prefix="light-image-editor-compat-") as directory:
        root = Path(directory)
        settings = SettingsStore(root / "settings.json")
        settings.save(
            {
                "schema_version": 1,
                "onboarding_completed": True,
                "theme_mode": "light",
            }
        )
        app = ImageEditorApp(
            settings_store=settings,
            session_store=SessionStore(root / "session.json"),
        )
        try:
            for job in app._startup_jobs:
                app.after_cancel(job)
            app._startup_jobs.clear()
            app.update_idletasks()
            app.update()
            actual_dpi = round(float(app.winfo_fpixels("1i")))
            screen = [app.winfo_screenwidth(), app.winfo_screenheight()]
            scales = [_probe_scale(app, dpi) for dpi in PROBE_DPIS]
        finally:
            if app.winfo_exists():
                app._shutdown_services()
                app.destroy()
    result: dict[str, Any] = {
        "schema_version": 1,
        "application_version": __version__,
        "captured_at_utc": datetime.now(UTC).isoformat(),
        "host": host,
        "display": {"actual_dpi": actual_dpi, "screen_pixels": screen},
        "scales": scales,
        "virtual_monitors": _virtual_monitor_probe(),
        "thresholds": asdict(CompatibilityThresholds()),
    }
    result["violations"] = evaluate_compatibility_probe(result)
    result["passed"] = not result["violations"]
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-fail", action="store_true")
    arguments = parser.parse_args()
    result = run()
    serialized = json.dumps(result, ensure_ascii=False, indent=2)
    print(serialized)
    if arguments.output is not None:
        destination = arguments.output.resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(serialized + "\n", encoding="utf-8")
    return 0 if result["passed"] or arguments.no_fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
