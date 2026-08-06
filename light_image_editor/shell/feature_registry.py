"""Feature metadata and validation for the four-mode application shell."""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass

from .mode_router import ModeId

_FEATURE_ID = re.compile(r"^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$")
_MODIFIER_ALIASES = {
    "control": "Ctrl",
    "ctrl": "Ctrl",
    "alt": "Alt",
    "option": "Alt",
    "shift": "Shift",
    "meta": "Meta",
    "cmd": "Meta",
    "command": "Meta",
    "win": "Meta",
}
_MODIFIER_ORDER = ("Ctrl", "Alt", "Shift", "Meta")


class FeatureRegistryError(ValueError):
    """Raised when feature metadata would make shell navigation ambiguous."""


def normalize_shortcut(shortcut: str) -> str:
    """Return a stable display form and reject ambiguous shortcut declarations."""

    raw = shortcut.strip()
    if not raw:
        raise FeatureRegistryError("shortcut must not be blank")
    parts = [part.strip() for part in raw.split("+")]
    if any(not part for part in parts):
        raise FeatureRegistryError(f"invalid shortcut: {shortcut!r}")
    modifiers: set[str] = set()
    keys: list[str] = []
    for part in parts:
        modifier = _MODIFIER_ALIASES.get(part.casefold())
        if modifier is not None:
            if modifier in modifiers:
                raise FeatureRegistryError(f"duplicate shortcut modifier: {modifier}")
            modifiers.add(modifier)
        else:
            keys.append(part)
    if len(keys) != 1:
        raise FeatureRegistryError("shortcut must contain exactly one non-modifier key")
    key = (
        keys[0].upper()
        if len(keys[0]) == 1 or re.fullmatch(r"f\d{1,2}", keys[0], re.I)
        else keys[0].title()
    )
    ordered = [modifier for modifier in _MODIFIER_ORDER if modifier in modifiers]
    return "+".join((*ordered, key))


@dataclass(frozen=True, slots=True)
class FeatureDefinition:
    """A user-facing capability owned by one shell mode or the global shell."""

    id: str
    mode: ModeId | None
    label: str
    visible_entry: str
    shortcut: str | None = None
    command: str | None = None
    enabled: bool = True
    description: str = ""

    def __post_init__(self) -> None:
        feature_id = self.id.strip()
        if not _FEATURE_ID.fullmatch(feature_id):
            raise FeatureRegistryError(f"invalid feature id: {self.id!r}")
        object.__setattr__(self, "id", feature_id)
        try:
            mode = (
                None
                if self.mode is None
                else self.mode
                if isinstance(self.mode, ModeId)
                else ModeId(self.mode)
            )
        except ValueError as error:
            raise FeatureRegistryError(f"invalid feature mode: {self.mode!r}") from error
        object.__setattr__(self, "mode", mode)
        label = self.label.strip()
        if not label:
            raise FeatureRegistryError("feature label must not be blank")
        object.__setattr__(self, "label", label)
        visible_entry = self.visible_entry.strip()
        if not visible_entry:
            raise FeatureRegistryError(f"feature {feature_id!r} has no visible entry")
        object.__setattr__(self, "visible_entry", visible_entry)
        if self.shortcut is not None:
            object.__setattr__(self, "shortcut", normalize_shortcut(self.shortcut))
        if self.command is not None:
            command = self.command.strip()
            if not command:
                raise FeatureRegistryError("feature command must not be blank")
            object.__setattr__(self, "command", command)

    @property
    def feature_id(self) -> str:
        return self.id

    @property
    def mode_id(self) -> ModeId | None:
        return self.mode

    @property
    def entry_point(self) -> str:
        return self.visible_entry


class FeatureRegistry:
    """Ordered registry with ID, mode, entry-point and shortcut validation."""

    def __init__(self, features: Iterable[FeatureDefinition] = ()) -> None:
        self._features: dict[str, FeatureDefinition] = {}
        self._shortcuts: dict[str, str] = {}
        self.extend(features)

    def __len__(self) -> int:
        return len(self._features)

    def __iter__(self) -> Iterator[FeatureDefinition]:
        return iter(self._features.values())

    def __contains__(self, feature_id: object) -> bool:
        return feature_id in self._features

    def __getitem__(self, feature_id: str) -> FeatureDefinition:
        return self._features[feature_id]

    @property
    def features(self) -> tuple[FeatureDefinition, ...]:
        return tuple(self._features.values())

    @property
    def modes(self) -> frozenset[ModeId]:
        return frozenset(
            feature.mode for feature in self._features.values() if feature.mode is not None
        )

    @property
    def global_features(self) -> tuple[FeatureDefinition, ...]:
        return tuple(feature for feature in self._features.values() if feature.mode is None)

    @property
    def missing_modes(self) -> frozenset[ModeId]:
        return frozenset(ModeId) - self.modes

    def register(self, feature: FeatureDefinition) -> FeatureDefinition:
        if feature.id in self._features:
            raise FeatureRegistryError(f"duplicate feature id: {feature.id}")
        if feature.shortcut is not None:
            key = feature.shortcut.casefold()
            conflict_id = self._shortcuts.get(key)
            if conflict_id is not None:
                raise FeatureRegistryError(
                    f"shortcut {feature.shortcut} is already owned by {conflict_id}"
                )
            self._shortcuts[key] = feature.id
        self._features[feature.id] = feature
        return feature

    def extend(self, features: Iterable[FeatureDefinition]) -> None:
        for feature in features:
            self.register(feature)

    def get(self, feature_id: str) -> FeatureDefinition | None:
        return self._features.get(feature_id)

    def for_mode(
        self,
        mode: ModeId | str,
        *,
        include_disabled: bool = True,
        include_global: bool = False,
    ) -> tuple[FeatureDefinition, ...]:
        try:
            mode_id = mode if isinstance(mode, ModeId) else ModeId(mode)
        except ValueError as error:
            raise FeatureRegistryError(f"invalid feature mode: {mode!r}") from error
        return tuple(
            feature
            for feature in self._features.values()
            if (feature.mode == mode_id or (include_global and feature.mode is None))
            and (include_disabled or feature.enabled)
        )

    def visible_entries(
        self,
        mode: ModeId | str,
        *,
        include_global: bool = False,
    ) -> tuple[str, ...]:
        return tuple(
            feature.visible_entry for feature in self.for_mode(mode, include_global=include_global)
        )

    def resolve_shortcut(self, shortcut: str) -> FeatureDefinition | None:
        normalized = normalize_shortcut(shortcut)
        feature_id = self._shortcuts.get(normalized.casefold())
        return self._features.get(feature_id) if feature_id is not None else None

    def validate(self, *, require_all_modes: bool = False) -> None:
        """Revalidate invariants, optionally requiring coverage of all four modes."""

        if require_all_modes and self.missing_modes:
            missing = ", ".join(sorted(mode.value for mode in self.missing_modes))
            raise FeatureRegistryError(f"feature registry has no definitions for: {missing}")
        seen_ids: set[str] = set()
        seen_shortcuts: set[str] = set()
        for feature in self._features.values():
            if feature.id in seen_ids:
                raise FeatureRegistryError(f"duplicate feature id: {feature.id}")
            seen_ids.add(feature.id)
            if not feature.visible_entry.strip():
                raise FeatureRegistryError(f"feature {feature.id!r} has no visible entry")
            if feature.shortcut is not None:
                shortcut = normalize_shortcut(feature.shortcut).casefold()
                if shortcut in seen_shortcuts:
                    raise FeatureRegistryError(f"duplicate shortcut: {feature.shortcut}")
                seen_shortcuts.add(shortcut)


BASE_FEATURE_DEFINITIONS = (
    FeatureDefinition(
        "global.open_project",
        None,
        "打开编辑项目",
        "menu.file.open_project",
        command="open_project_dialog",
    ),
    FeatureDefinition(
        "global.paste_from_clipboard",
        None,
        "从剪贴板粘贴",
        "menu.file.paste",
        command="paste_from_clipboard",
    ),
    FeatureDefinition(
        "global.recent_files",
        None,
        "最近打开",
        "menu.file.recent.files",
        command="open_recent_file",
    ),
    FeatureDefinition(
        "global.recent_directories",
        None,
        "最近目录",
        "menu.file.recent.directories",
        command="open_recent_directory",
    ),
    FeatureDefinition(
        "global.recent_projects",
        None,
        "最近编辑项目",
        "menu.file.recent.projects",
        command="open_recent_project",
    ),
    FeatureDefinition(
        "global.recent_exports",
        None,
        "最近导出位置",
        "menu.file.recent.exports",
        command="open_recent_export_directory",
    ),
    FeatureDefinition(
        "global.rename_current",
        None,
        "重命名",
        "menu.file.rename",
        "F2",
        "rename_current",
    ),
    FeatureDefinition(
        "global.copy_current",
        None,
        "复制到",
        "menu.file.copy_to",
        command="copy_current",
    ),
    FeatureDefinition(
        "global.move_current",
        None,
        "移动到",
        "menu.file.move_to",
        command="move_current",
    ),
    FeatureDefinition(
        "global.reveal_current",
        None,
        "在资源管理器中显示",
        "menu.file.reveal",
        "Ctrl+Shift+E",
        "reveal_current",
    ),
    FeatureDefinition(
        "global.delete_current",
        None,
        "移入回收站",
        "menu.file.recycle_current",
        command="delete_current_to_recycle_bin",
    ),
    FeatureDefinition(
        "global.open_recycle_bin",
        None,
        "打开回收站",
        "menu.file.open_recycle_bin",
        command="open_recycle_bin",
    ),
    FeatureDefinition(
        "global.save_project",
        None,
        "保存编辑项目",
        "menu.file.save_project",
        command="save_project_dialog",
    ),
    FeatureDefinition("global.exit", None, "退出", "menu.file.exit", command="close"),
    FeatureDefinition(
        "global.toggle_edit_panel",
        None,
        "显示编辑工具",
        "menu.view.edit_panel",
        command="toggle_edit_panel",
    ),
    FeatureDefinition(
        "global.workflow",
        None,
        "收藏与快捷键",
        "menu.edit.workflow",
        command="configure_workflow",
    ),
    FeatureDefinition(
        "global.settings",
        None,
        "设置",
        "menu.edit.settings",
        "Ctrl+Comma",
        "open_settings",
    ),
    FeatureDefinition(
        "global.diagnostic_report",
        None,
        "导出问题报告",
        "menu.info.diagnostics",
        command="export_diagnostic_report",
    ),
    FeatureDefinition(
        "global.shortcut_help",
        None,
        "快捷键帮助",
        "menu.info.shortcut_help",
        "F1",
        "show_shortcut_help",
    ),
    FeatureDefinition(
        "global.onboarding",
        None,
        "首次使用引导",
        "menu.info.onboarding",
        command="show_onboarding",
    ),
    FeatureDefinition(
        "global.task_tray",
        None,
        "统一任务托盘",
        "shell.task_tray",
        command="show_task_tray",
        description="集中查看转图、智能改图和生图任务，并执行前往、取消或重试。",
    ),
    FeatureDefinition(
        "global.feature_palette",
        None,
        "全部功能",
        "dock.all_features",
        "Ctrl+K",
        "show_feature_palette",
        description="搜索全部已登记功能，并通过原有真实回调直接执行。",
    ),
    FeatureDefinition("viewer.open", ModeId.VIEWER, "打开图片", "dock.open", command="open_file"),
    FeatureDefinition(
        "viewer.open_folder",
        ModeId.VIEWER,
        "打开文件夹",
        "mode.viewer.actions.open_folder",
        "Ctrl+Shift+O",
        "open_directory",
    ),
    FeatureDefinition(
        "viewer.library",
        ModeId.VIEWER,
        "资源库",
        "sidebar.library",
        "Ctrl+B",
        "toggle_thumbnail_sidebar",
    ),
    FeatureDefinition(
        "viewer.previous",
        ModeId.VIEWER,
        "上一张",
        "menu.view.previous",
        command="navigate_previous",
    ),
    FeatureDefinition(
        "viewer.next", ModeId.VIEWER, "下一张", "menu.view.next", command="navigate_next"
    ),
    FeatureDefinition(
        "viewer.first", ModeId.VIEWER, "第一张", "menu.view.first", command="navigate_first"
    ),
    FeatureDefinition(
        "viewer.last", ModeId.VIEWER, "最后一张", "menu.view.last", command="navigate_last"
    ),
    FeatureDefinition(
        "viewer.fit", ModeId.VIEWER, "适合画布", "canvas.tools.fit", "Ctrl+0", "reset_view"
    ),
    FeatureDefinition(
        "viewer.pan",
        ModeId.VIEWER,
        "平移画布",
        "canvas.tools.pan",
        command="activate_pan",
    ),
    FeatureDefinition(
        "viewer.zoom_50",
        ModeId.VIEWER,
        "缩放到 50%",
        "menu.view.zoom_50",
        command="zoom_50",
    ),
    FeatureDefinition(
        "viewer.actual_size",
        ModeId.VIEWER,
        "实际尺寸",
        "menu.view.actual_size",
        "Ctrl+1",
        "actual_size",
    ),
    FeatureDefinition(
        "viewer.zoom_150",
        ModeId.VIEWER,
        "缩放到 150%",
        "menu.view.zoom_150",
        command="zoom_150",
    ),
    FeatureDefinition(
        "viewer.zoom_200",
        ModeId.VIEWER,
        "缩放到 200%",
        "menu.view.zoom_200",
        command="zoom_200",
    ),
    FeatureDefinition(
        "viewer.fullscreen", ModeId.VIEWER, "全屏", "dock.fullscreen", "F11", "toggle_fullscreen"
    ),
    FeatureDefinition(
        "viewer.immersive",
        ModeId.VIEWER,
        "沉浸式看图",
        "menu.view.immersive",
        "Ctrl+F11",
        "toggle_immersive",
    ),
    FeatureDefinition(
        "viewer.slideshow", ModeId.VIEWER, "幻灯片", "menu.view.slideshow", "F5", "toggle_slideshow"
    ),
    FeatureDefinition(
        "viewer.slideshow_settings",
        ModeId.VIEWER,
        "幻灯片设置",
        "menu.view.slideshow_settings",
        command="configure_slideshow",
    ),
    FeatureDefinition(
        "viewer.animation_previous_frame",
        ModeId.VIEWER,
        "上一帧",
        "viewer.animation.previous",
        command="animation_previous",
    ),
    FeatureDefinition(
        "viewer.animation_toggle",
        ModeId.VIEWER,
        "播放或暂停动画",
        "viewer.animation.toggle",
        command="animation_toggle",
    ),
    FeatureDefinition(
        "viewer.animation_next_frame",
        ModeId.VIEWER,
        "下一帧",
        "viewer.animation.next",
        command="animation_next",
    ),
    FeatureDefinition(
        "viewer.properties",
        ModeId.VIEWER,
        "图片属性",
        "menu.info.properties",
        "Alt+Enter",
        "show_properties",
    ),
    FeatureDefinition("convert.save_as", ModeId.CONVERT, "保存为", "dock.save", command="save_as"),
    FeatureDefinition(
        "convert.quick_export", ModeId.CONVERT, "快速导出", "dock.export", "Ctrl+E", "quick_export"
    ),
    FeatureDefinition(
        "convert.copy_result",
        ModeId.CONVERT,
        "复制编辑结果",
        "menu.file.copy_result",
        "Ctrl+Shift+C",
        "copy_result",
    ),
    FeatureDefinition(
        "convert.settings",
        ModeId.CONVERT,
        "格式与压缩",
        "panel.convert.export",
        command="export_settings",
    ),
    FeatureDefinition(
        "convert.resize",
        ModeId.CONVERT,
        "调整尺寸",
        "panel.convert.resize",
        command="resize_settings",
    ),
    FeatureDefinition(
        "convert.queue_add_files",
        ModeId.CONVERT,
        "添加转换文件",
        "workspace.convert.queue.add_files",
        command="conversion_add_files",
    ),
    FeatureDefinition(
        "convert.queue_add_folder",
        ModeId.CONVERT,
        "添加转换文件夹",
        "workspace.convert.queue.add_folder",
        command="conversion_add_folder",
    ),
    FeatureDefinition(
        "convert.recipe_import",
        ModeId.CONVERT,
        "导入转换配方",
        "panel.convert.recipe.import",
        command="conversion_recipe_import",
    ),
    FeatureDefinition(
        "convert.recipe_export",
        ModeId.CONVERT,
        "导出转换配方",
        "panel.convert.recipe.export",
        command="conversion_recipe_export",
    ),
    FeatureDefinition(
        "convert.queue_run",
        ModeId.CONVERT,
        "开始转换队列",
        "panel.convert.queue.run",
        command="conversion_queue_run",
    ),
    FeatureDefinition(
        "convert.queue_cancel",
        ModeId.CONVERT,
        "取消转换队列",
        "panel.convert.queue.cancel",
        command="conversion_queue_cancel",
    ),
    FeatureDefinition(
        "convert.batch_files",
        ModeId.CONVERT,
        "批量处理文件",
        "menu.file.batch_files",
        command="batch_process",
    ),
    FeatureDefinition(
        "convert.batch_folder",
        ModeId.CONVERT,
        "批量处理文件夹",
        "menu.file.batch_folder",
        command="batch_process_folder",
    ),
    FeatureDefinition(
        "convert.animation",
        ModeId.CONVERT,
        "导出动画",
        "menu.file.animation",
        command="export_current_animation",
    ),
    FeatureDefinition(
        "convert.crop",
        ModeId.CONVERT,
        "进入裁剪",
        "dock.crop",
        command="enter_crop_tool",
    ),
    FeatureDefinition(
        "convert.apply_crop",
        ModeId.CONVERT,
        "应用裁剪",
        "panel.edit.annotation.apply_crop",
        command="apply_crop",
    ),
    FeatureDefinition(
        "convert.reset_crop",
        ModeId.CONVERT,
        "重置裁剪",
        "menu.edit.reset_crop",
        command="reset_crop",
    ),
    FeatureDefinition(
        "convert.rotate_left",
        ModeId.CONVERT,
        "左转 90°",
        "menu.edit.rotate_left",
        command="rotate_left",
    ),
    FeatureDefinition(
        "convert.rotate", ModeId.CONVERT, "向右旋转", "dock.rotate", command="rotate_right"
    ),
    FeatureDefinition(
        "convert.fine_rotation",
        ModeId.CONVERT,
        "精细旋转",
        "panel.edit.annotation.geometry.rotation",
        command="fine_rotation_changed",
        description="当前可见入口仍位于改图的标注面板。",
    ),
    FeatureDefinition(
        "convert.auto_straighten",
        ModeId.CONVERT,
        "自动拉直",
        "panel.edit.annotation.geometry.auto_straighten",
        command="auto_straighten",
        description="当前可见入口仍位于改图的标注面板。",
    ),
    FeatureDefinition(
        "convert.flip_horizontal",
        ModeId.CONVERT,
        "水平翻转",
        "menu.edit.flip_horizontal",
        command="flip_horizontal",
    ),
    FeatureDefinition(
        "convert.flip_vertical",
        ModeId.CONVERT,
        "垂直翻转",
        "menu.edit.flip_vertical",
        command="flip_vertical",
    ),
    FeatureDefinition("edit.undo", ModeId.EDIT, "撤销", "dock.undo", command="undo"),
    FeatureDefinition("edit.redo", ModeId.EDIT, "重做", "dock.redo", command="redo"),
    FeatureDefinition(
        "edit.reset",
        ModeId.EDIT,
        "重置快速编辑",
        "menu.edit.reset",
        command="reset_edits",
    ),
    FeatureDefinition(
        "edit.adjust", ModeId.EDIT, "调整", "panel.edit.adjust", command="show_adjustments"
    ),
    FeatureDefinition(
        "edit.smart", ModeId.EDIT, "智能改图", "panel.edit.smart", command="show_smart_edit"
    ),
    FeatureDefinition(
        "edit.smart.upscale_2x",
        ModeId.EDIT,
        "2× 本地高清",
        "panel.edit.smart.upscale_2x",
        command="smart_upscale_2x",
    ),
    FeatureDefinition(
        "edit.smart.upscale_4x",
        ModeId.EDIT,
        "4× 本地高清",
        "panel.edit.smart.upscale_4x",
        command="smart_upscale_4x",
    ),
    FeatureDefinition(
        "edit.smart.remove_background",
        ModeId.EDIT,
        "离线智能去背景",
        "panel.edit.smart.remove_background",
        command="smart_remove_background",
    ),
    FeatureDefinition(
        "edit.smart.alpha_preserve",
        ModeId.EDIT,
        "保留主体画笔",
        "panel.edit.smart.alpha_preserve",
        command="smart_alpha_preserve",
        description="在透明结果上恢复需要保留的主体区域。",
    ),
    FeatureDefinition(
        "edit.smart.alpha_remove",
        ModeId.EDIT,
        "删除背景画笔",
        "panel.edit.smart.alpha_remove",
        command="smart_alpha_remove",
        description="在透明结果上继续擦除残留背景。",
    ),
    FeatureDefinition(
        "edit.smart.alpha_clear",
        ModeId.EDIT,
        "清除边缘细化笔划",
        "panel.edit.smart.alpha_clear",
        command="smart_alpha_clear",
    ),
    FeatureDefinition(
        "edit.smart.alpha_apply",
        ModeId.EDIT,
        "应用去背景边缘细化",
        "panel.edit.smart.alpha_apply",
        command="smart_alpha_apply",
        description="按顺序应用保留与删除笔划，并创建可追溯的新修订。",
    ),
    FeatureDefinition(
        "edit.smart.background_color",
        ModeId.EDIT,
        "选择背景底色",
        "panel.edit.smart.background_color",
        command="smart_choose_background_color",
        description="为纯色背景或透明自定义背景区域选择本地合成底色。",
    ),
    FeatureDefinition(
        "edit.smart.background_image",
        ModeId.EDIT,
        "选择自定义背景图",
        "panel.edit.smart.background_image",
        command="smart_choose_background_image",
        description="选择本地背景图，应用时安全解码并居中铺满裁切。",
    ),
    FeatureDefinition(
        "edit.smart.background_apply",
        ModeId.EDIT,
        "应用背景替换",
        "panel.edit.smart.background_apply",
        command="smart_apply_background",
        description="将透明去背景修订与纯色或自定义图片合成并创建新修订。",
    ),
    FeatureDefinition(
        "edit.smart.repair_auto_select",
        ModeId.EDIT,
        "智能识别主体区域",
        "panel.edit.smart.repair_auto_select",
        command="smart_detect_repair_region",
        description="完全离线识别主要主体或显著区域，并在画布生成可继续校正的擦除框。",
    ),
    FeatureDefinition(
        "edit.smart.repair_next_candidate",
        ModeId.EDIT,
        "下一个主体候选",
        "panel.edit.smart.repair_next_candidate",
        command="smart_next_repair_region",
        description="在多个离线主体候选之间循环切换，并将所选区域映射到画布。",
    ),
    FeatureDefinition(
        "edit.smart.mask_add",
        ModeId.EDIT,
        "加入主体蒙版画笔",
        "panel.edit.smart.mask_add",
        command="smart_mask_add",
        description="在自动主体蒙版上补回漏选区域，笔划只保存在本地。",
    ),
    FeatureDefinition(
        "edit.smart.mask_remove",
        ModeId.EDIT,
        "移出主体蒙版画笔",
        "panel.edit.smart.mask_remove",
        command="smart_mask_remove",
        description="从自动主体蒙版中移除误选区域，修复时不修改这些像素。",
    ),
    FeatureDefinition(
        "edit.smart.mask_undo",
        ModeId.EDIT,
        "撤销主体蒙版笔划",
        "panel.edit.smart.mask_undo",
        command="smart_mask_undo",
    ),
    FeatureDefinition(
        "edit.smart.mask_reset",
        ModeId.EDIT,
        "重置自动主体蒙版",
        "panel.edit.smart.mask_reset",
        command="smart_mask_reset",
    ),
    FeatureDefinition(
        "edit.smart.repair_select",
        ModeId.EDIT,
        "框选物体擦除区域",
        "panel.edit.smart.repair_select",
        command="smart_select_repair_region",
        description="在画布框选需要擦除的物体或局部瑕疵，并允许继续校正区域。",
    ),
    FeatureDefinition(
        "edit.smart.repair_apply",
        ModeId.EDIT,
        "擦除物体并局部修复",
        "panel.edit.smart.repair_apply",
        command="smart_apply_region_repair",
        description="离线读取选区四周像素重建局部背景，并创建可追溯的新修订。",
    ),
    FeatureDefinition(
        "edit.smart.replace_text",
        ModeId.EDIT,
        "替换图片文字",
        "panel.edit.smart.replace_text",
        command="smart_replace_text",
    ),
    FeatureDefinition(
        "edit.smart.detect_text_regions",
        ModeId.EDIT,
        "自动检测文字区域",
        "panel.edit.smart.detect_text_regions",
        command="smart_detect_text_regions",
        description="完全离线分析可能的文字行，结果可在画布中继续手工校正。",
    ),
    FeatureDefinition(
        "edit.smart.next_text_region",
        ModeId.EDIT,
        "下一个文字区域",
        "panel.edit.smart.next_text_region",
        command="smart_next_text_region",
        description="在多个离线文字区域建议之间切换。",
    ),
    FeatureDefinition(
        "edit.smart.recognize_text",
        ModeId.EDIT,
        "识别框内原文字",
        "panel.edit.smart.recognize_text",
        command="smart_recognize_text",
        description="使用可选的本地文字识别组件读取框选内容，并填入可编辑的新文字输入框。",
    ),
    FeatureDefinition(
        "edit.smart.recommend_font",
        ModeId.EDIT,
        "推荐相似字体",
        "panel.edit.smart.recommend_font",
        command="smart_recommend_font",
        description="完全离线比较框选原文字与本机候选中文字体轮廓，并回填最相似候选。",
    ),
    FeatureDefinition(
        "edit.smart.send_to_convert",
        ModeId.EDIT,
        "发送高清结果到转图",
        "panel.edit.smart.send_to_convert",
        command="smart_send_to_convert",
    ),
    FeatureDefinition(
        "edit.smart.compare_result",
        ModeId.EDIT,
        "对比智能改图结果",
        "panel.edit.smart.compare_result",
        command="smart_compare_result",
    ),
    FeatureDefinition(
        "edit.smart.discard_result",
        ModeId.EDIT,
        "放弃智能改图候选",
        "panel.edit.smart.discard_result",
        command="smart_discard_result",
    ),
    FeatureDefinition(
        "edit.annotation", ModeId.EDIT, "标注", "panel.edit.annotation", command="show_annotations"
    ),
    FeatureDefinition(
        "edit.annotation.select",
        ModeId.EDIT,
        "选择标注",
        "panel.edit.annotation.tools.select",
        command="select_annotation_tool",
    ),
    FeatureDefinition(
        "edit.annotation.text",
        ModeId.EDIT,
        "文字",
        "panel.edit.annotation.tools.text",
        command="select_text_tool",
    ),
    FeatureDefinition(
        "edit.annotation.arrow",
        ModeId.EDIT,
        "箭头",
        "panel.edit.annotation.tools.arrow",
        command="select_arrow_tool",
    ),
    FeatureDefinition(
        "edit.annotation.line",
        ModeId.EDIT,
        "直线",
        "panel.edit.annotation.tools.line",
        command="select_line_tool",
    ),
    FeatureDefinition(
        "edit.annotation.rectangle",
        ModeId.EDIT,
        "矩形",
        "panel.edit.annotation.tools.rectangle",
        command="select_rectangle_tool",
    ),
    FeatureDefinition(
        "edit.annotation.ellipse",
        ModeId.EDIT,
        "椭圆",
        "panel.edit.annotation.tools.ellipse",
        command="select_ellipse_tool",
    ),
    FeatureDefinition(
        "edit.annotation.freehand",
        ModeId.EDIT,
        "画笔",
        "panel.edit.annotation.tools.freehand",
        command="select_freehand_tool",
    ),
    FeatureDefinition(
        "edit.annotation.mosaic",
        ModeId.EDIT,
        "马赛克",
        "panel.edit.annotation.tools.mosaic",
        command="select_mosaic_tool",
    ),
    FeatureDefinition(
        "edit.annotation.blur",
        ModeId.EDIT,
        "模糊",
        "panel.edit.annotation.tools.blur",
        command="select_blur_tool",
    ),
    FeatureDefinition(
        "edit.annotation.redact",
        ModeId.EDIT,
        "遮挡",
        "panel.edit.annotation.tools.redact",
        command="select_redact_tool",
    ),
    FeatureDefinition(
        "edit.annotation.number",
        ModeId.EDIT,
        "序号",
        "panel.edit.annotation.tools.number",
        command="select_number_tool",
    ),
    FeatureDefinition(
        "edit.annotation.perspective_tool",
        ModeId.EDIT,
        "透视",
        "panel.edit.annotation.tools.perspective",
        command="select_perspective_tool",
    ),
    FeatureDefinition(
        "edit.perspective",
        ModeId.EDIT,
        "应用透视校正",
        "panel.edit.annotation.perspective.apply",
        command="apply_perspective",
    ),
    FeatureDefinition(
        "edit.watermark",
        ModeId.EDIT,
        "文字水印",
        "panel.edit.annotation.watermark",
        command="update_watermark",
    ),
    FeatureDefinition(
        "edit.canvas_expand",
        ModeId.EDIT,
        "画布扩展",
        "panel.edit.annotation.canvas_expand",
        command="apply_canvas_expansion",
    ),
    FeatureDefinition(
        "edit.history", ModeId.EDIT, "历史", "panel.edit.history", command="show_history"
    ),
    FeatureDefinition(
        "viewer.compare_original",
        ModeId.VIEWER,
        "对比原图",
        "dock.compare",
        command="compare_original",
    ),
    FeatureDefinition(
        "viewer.split_compare",
        ModeId.VIEWER,
        "编辑前后分屏",
        "dock.split_compare",
        command="toggle_split_compare",
    ),
    FeatureDefinition(
        "viewer.file_compare",
        ModeId.VIEWER,
        "两张图片并排比较",
        "mode.viewer.file_compare",
        command="toggle_file_comparison",
    ),
    FeatureDefinition(
        "generate.workspace",
        ModeId.GENERATE,
        "离线任务验证",
        "mode.generate.workspace",
        command="show_generate_workspace",
        description=(
            "使用离线模拟服务验证任务提交、取消与候选结果流转；不代表真实智能模型已接入。"
        ),
    ),
    FeatureDefinition(
        "generate.run_mock",
        ModeId.GENERATE,
        "运行离线测试",
        "mode.generate.run_mock",
        command="start_mock_generation",
    ),
    FeatureDefinition(
        "generate.cancel_mock",
        ModeId.GENERATE,
        "取消离线任务",
        "mode.generate.cancel_mock",
        command="cancel_mock_generation",
    ),
    FeatureDefinition(
        "generate.candidate_history",
        ModeId.GENERATE,
        "生成候选历史",
        "mode.generate.candidate_grid",
        command="show_generate_workspace",
        description="显示最近安全入库的生成候选并保留选择状态。",
    ),
    FeatureDefinition(
        "generate.negative_prompt",
        ModeId.GENERATE,
        "负面提示词",
        "mode.generate.negative_prompt",
        command="show_generate_workspace",
        description="将不希望出现的内容作为独立参数提交给支持该能力的生成服务。",
    ),
    FeatureDefinition(
        "generate.reference_image",
        ModeId.GENERATE,
        "管理生成参考图",
        "mode.generate.reference_image",
        command="choose_generation_reference",
        description=(
            "从当前素材或本地文件添加最多四张参考图，并可逐张移除或全部清空；"
            "所有项目保持精确修订身份。"
        ),
    ),
    FeatureDefinition(
        "generate.reference_role_strength",
        ModeId.GENERATE,
        "参考图角色与强度",
        "mode.generate.reference_controls",
        command="show_generate_workspace",
        description="逐张设置参考图用于内容、风格或构图，以及各自的影响强度。",
    ),
    FeatureDefinition(
        "generate.style_preset",
        ModeId.GENERATE,
        "生成风格预设",
        "mode.generate.style_preset",
        command="show_generate_workspace",
        description="使用服务明确支持的中文风格预设生成候选。",
    ),
    FeatureDefinition(
        "generate.fixed_seed",
        ModeId.GENERATE,
        "固定生成种子",
        "mode.generate.fixed_seed",
        command="show_generate_workspace",
        description="固定随机种子以便复现相同参数下的候选像素。",
    ),
    FeatureDefinition(
        "generate.provider_selection",
        ModeId.GENERATE,
        "选择生成服务",
        "mode.generate.provider_selection",
        command="show_generate_workspace",
        description="显示服务可用状态、执行位置、模型能力和费用策略。",
    ),
    FeatureDefinition(
        "generate.retry_failed",
        ModeId.GENERATE,
        "重试失败任务",
        "mode.generate.retry_failed",
        command="retry_failed_generation",
        description="只在用户明确操作后以新请求身份重试失败或状态未知的任务。",
    ),
    FeatureDefinition(
        "generate.candidate_variation",
        ModeId.GENERATE,
        "生成候选变体",
        "mode.generate.candidate.variation",
        command="generate_candidate_variation",
        description="基于所选候选生成新候选，并记录来源修订和变化强度。",
    ),
    FeatureDefinition(
        "generate.candidate_favorite",
        ModeId.GENERATE,
        "收藏生成候选",
        "mode.generate.candidate.favorite",
        command="generate_toggle_candidate_favorite",
        description="收藏状态独立持久化，收藏候选不会被最近历史滚动淘汰。",
    ),
    FeatureDefinition(
        "generate.candidate_favorite_filter",
        ModeId.GENERATE,
        "仅显示收藏候选",
        "mode.generate.candidate.favorite_filter",
        command="generate_toggle_favorites_filter",
        description="在全部候选与本机收藏候选之间切换。",
    ),
    FeatureDefinition(
        "generate.candidate_remove",
        ModeId.GENERATE,
        "删除生成候选",
        "mode.generate.candidate.remove",
        command="generate_remove_candidate",
        description="从候选工作区和最近历史移除，但保留素材修订用于任务来源追溯。",
    ),
    FeatureDefinition(
        "generate.open_candidate",
        ModeId.GENERATE,
        "打开生成候选",
        "mode.generate.candidate.open",
        command="generate_open_candidate",
    ),
    FeatureDefinition(
        "generate.send_to_edit",
        ModeId.GENERATE,
        "发送生成候选到改图",
        "mode.generate.candidate.send_to_edit",
        command="generate_send_to_edit",
    ),
    FeatureDefinition(
        "generate.send_to_convert",
        ModeId.GENERATE,
        "发送生成候选到转图",
        "mode.generate.candidate.send_to_convert",
        command="generate_send_to_convert",
    ),
)

EXISTING_FEATURES = BASE_FEATURE_DEFINITIONS


def create_default_feature_registry() -> FeatureRegistry:
    registry = FeatureRegistry(BASE_FEATURE_DEFINITIONS)
    registry.validate(require_all_modes=True)
    return registry


def default_feature_registry() -> FeatureRegistry:
    return create_default_feature_registry()
