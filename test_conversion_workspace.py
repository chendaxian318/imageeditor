from __future__ import annotations

import tempfile
import time
import tkinter as tk
import unittest
from pathlib import Path
from types import SimpleNamespace

from light_image_editor.domain.conversion_jobs import ConversionQueue
from light_image_editor.shell.mode_router import AssetHandoff, RevisionHandoff
from light_image_editor.theme import DARK_TOKENS
from light_image_editor.ui.conversion_workspace import ConversionWorkspace


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"image")
    return path.resolve()


class ConversionWorkspaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self) -> None:
        self.root.update_idletasks()
        self.root.destroy()

    def test_natural_queue_callbacks_activation_and_handoff_deduplication(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            image10 = _touch(root / "图10.png")
            image2 = _touch(root / "图2.png")
            image1 = _touch(root / "图1.png")
            activated: list[Path] = []
            snapshots: list[tuple[Path, ...]] = []
            workspace = ConversionWorkspace(
                self.root,
                on_activate=activated.append,
                on_queue_changed=lambda items: snapshots.append(tuple(item.path for item in items)),
            )
            workspace.pack(fill="both", expand=True)

            added = workspace.add_files((image10, image2, image1))

            self.assertEqual([item.path.name for item in added], ["图1.png", "图2.png", "图10.png"])
            self.assertEqual(
                [workspace.tree.item(iid, "values")[2] for iid in workspace.tree.get_children()],
                ["图1.png", "图2.png", "图10.png"],
            )
            self.assertEqual(snapshots[-1], (image1, image2, image10))
            self.assertEqual(workspace.current_selection, image1)
            self.assertTrue(workspace.activate_selected())
            self.assertEqual(activated, [image1])

            handoff = _touch(root / "交接.png")
            asset = AssetHandoff("asset-1", uri=handoff.as_uri())
            revision = RevisionHandoff("revision-3", asset.id, number=3)
            first = workspace.add_handoff(handoff, asset, revision)
            duplicate = workspace.add_handoff(handoff, asset, revision)

            self.assertIsNotNone(first)
            self.assertIsNone(duplicate)
            queued_handoff = workspace.queue.get(handoff)
            self.assertIsNotNone(queued_handoff)
            assert queued_handoff is not None
            self.assertIs(queued_handoff.asset, asset)
            self.assertIs(queued_handoff.revision, revision)
            self.assertEqual(workspace.selected_path, handoff)
            self.assertEqual(len(snapshots), 2)

            late = _touch(root / "稍后登记.png")
            workspace.add_files((late,))
            late_asset = AssetHandoff("asset-late", uri=late.as_uri())
            late_revision = RevisionHandoff("revision-late", late_asset.id, number=1)
            enriched = workspace.add_handoff(late, late_asset, late_revision)
            duplicate_enrichment = workspace.add_handoff(late, late_asset, late_revision)

            self.assertIsNotNone(enriched)
            assert enriched is not None
            self.assertIs(enriched.asset, late_asset)
            self.assertIs(enriched.revision, late_revision)
            self.assertIsNone(duplicate_enrichment)
            self.assertEqual(len(workspace.items), 5)
            self.assertEqual(len(snapshots), 4)

    def test_enable_remove_clear_and_keyboard_paths(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _touch(root / "第一张.png")
            second = _touch(root / "第二张.png")
            changed: list[int] = []
            workspace = ConversionWorkspace(
                self.root,
                on_queue_changed=lambda items: changed.append(len(items)),
            )
            workspace.pack(fill="both", expand=True)
            workspace.add_files((first, second))

            self.assertTrue(workspace.tree.bind("<Return>"))
            self.assertTrue(workspace.tree.bind("<space>"))
            self.assertTrue(workspace.tree.bind("<Delete>"))
            excluded = workspace.toggle_selected()
            self.assertIsNotNone(excluded)
            assert excluded is not None
            self.assertFalse(excluded.enabled)
            self.assertEqual(workspace.toggle_button.cget("text"), "启用所选")
            enabled = workspace.toggle_selected()
            self.assertIsNotNone(enabled)
            assert enabled is not None
            self.assertTrue(enabled.enabled)

            removed = workspace.remove_selected()
            self.assertIsNotNone(removed)
            self.assertEqual(len(workspace.items), 1)
            self.assertIsNone(workspace.selected_path)
            cleared = workspace.clear()
            self.assertEqual(len(cleared), 1)
            self.assertEqual(workspace.items, ())
            self.assertEqual(workspace.queue_summary_var.get(), "尚未添加图片")
            self.assertEqual(changed, [2, 2, 2, 1, 0])

    def test_host_request_callbacks_add_paths_without_opening_dialogs(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            direct = _touch(root / "直接.png")
            nested = _touch(root / "文件夹" / "嵌套.jpg")
            workspace = ConversionWorkspace(
                self.root,
                on_request_files=lambda: direct,
                on_request_folder=lambda: nested.parent,
            )
            workspace.pack(fill="both", expand=True)

            self.assertEqual(workspace.request_files()[0].path, direct)
            self.assertEqual(workspace.request_folder()[0].path, nested)
            self.assertEqual({item.path for item in workspace.items}, {direct, nested})
            self.assertEqual(workspace.add_files_button.cget("text"), "添加文件")
            self.assertEqual(workspace.add_folder_button.cget("text"), "添加文件夹")

    def test_recovery_can_replace_queue_without_emitting_user_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _touch(root / "恢复1.png")
            second = _touch(root / "恢复2.png")
            queue = ConversionQueue()
            queue.add_files((first, second))
            snapshots: list[int] = []
            workspace = ConversionWorkspace(
                self.root,
                on_queue_changed=lambda items: snapshots.append(len(items)),
            )
            workspace.pack(fill="both", expand=True)

            workspace.set_queue(queue)

            self.assertEqual(len(workspace.items), 2)
            self.assertEqual(snapshots, [])
            workspace.set_queue(ConversionQueue(), notify=True)
            self.assertEqual(snapshots, [0])

    def test_task_records_and_theme_tokens_update_status_and_result(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = _touch(root / "成功.png")
            second = _touch(root / "失败.png")
            workspace = ConversionWorkspace(self.root, tokens=DARK_TOKENS)
            workspace.pack(fill="both", expand=True)
            workspace.add_files((first, second))
            first_input = workspace.queue.get(first)
            second_input = workspace.queue.get(second)
            assert first_input is not None and second_input is not None

            records = (
                SimpleNamespace(
                    input=first_input,
                    status="succeeded",
                    output=root / "输出" / "成功.webp",
                    error=None,
                ),
                SimpleNamespace(
                    input=second_input,
                    status="failed",
                    output=None,
                    error="目标目录不可写",
                ),
            )
            workspace.set_task_records(records)

            rows = {
                workspace.tree.item(iid, "values")[2]: workspace.tree.item(iid, "values")[1]
                for iid in workspace.tree.get_children()
            }
            self.assertEqual(rows, {"成功.png": "成功", "失败.png": "失败"})
            self.assertEqual(workspace.task_summary_var.get(), "共 2 项 · 等待 0 · 处理中 0")
            self.assertEqual(workspace.task_result_var.get(), "成功 1 · 失败 1 · 取消 0")
            self.assertIn("目标目录不可写", workspace.task_detail_var.get())
            style = tk.ttk.Style(self.root)
            self.assertEqual(
                style.lookup(workspace._tree_style, "fieldbackground"),
                DARK_TOKENS.surface,
            )

    def test_task_filters_and_result_callbacks_keep_input_queue_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source_paths = [_touch(root / f"来源{index}.png") for index in range(5)]
            output = root / "完成.webp"
            output.write_bytes(b"result")
            retries: list[tuple[object, ...]] = []
            opened: list[Path] = []
            revealed: list[Path] = []
            queue_changes: list[int] = []
            workspace = ConversionWorkspace(
                self.root,
                on_queue_changed=lambda items: queue_changes.append(len(items)),
                on_retry_tasks=retries.append,
                on_open_result=opened.append,
                on_reveal_result=revealed.append,
            )
            workspace.pack(fill="both", expand=True)
            workspace.add_files(source_paths)
            inputs = workspace.items
            records = (
                SimpleNamespace(input=inputs[0], status="pending", output=None, error=None),
                SimpleNamespace(input=inputs[1], status="running", output=None, error=None),
                SimpleNamespace(input=inputs[2], status="succeeded", output=output, error=None),
                SimpleNamespace(
                    input=inputs[3],
                    status="failed",
                    output=root / "不应打开.png",
                    error="编码失败",
                ),
                SimpleNamespace(input=inputs[4], status="cancelled", output=None, error=None),
            )
            original_queue = workspace.items
            workspace.set_task_records(records)

            self.assertIs(workspace.task_records[2], records[2])
            self.assertEqual(
                tuple(workspace.task_filter_box.cget("values")),
                (
                    "全部",
                    "处理中",
                    "成功",
                    "失败",
                    "取消",
                ),
            )
            workspace.set_task_filter("成功")
            self.assertEqual(len(workspace.filtered_task_records), 1)
            self.assertEqual(len(workspace.task_tree.get_children()), 1)
            self.assertTrue(workspace.select_task(records[2]))
            self.assertIs(workspace.selected_task, records[2])
            self.assertNotIn("disabled", workspace.open_result_button.state())
            self.assertNotIn("disabled", workspace.reveal_result_button.state())
            self.assertIn("disabled", workspace.retry_task_button.state())
            self.assertTrue(workspace.open_selected_result())
            self.assertTrue(workspace.reveal_selected_result())
            self.assertEqual(opened, [output])
            self.assertEqual(revealed, [output])

            workspace.set_task_filter("失败")
            self.assertTrue(workspace.select_task(records[3]))
            self.assertNotIn("disabled", workspace.retry_task_button.state())
            self.assertIn("disabled", workspace.open_result_button.state())
            self.assertIn("disabled", workspace.reveal_result_button.state())
            self.assertFalse(workspace.open_selected_result())
            self.assertFalse(workspace.reveal_selected_result())
            self.assertTrue(workspace.retry_selected_task())
            self.assertEqual(retries, [(records[3],)])

            workspace.set_task_filter("取消")
            self.assertTrue(workspace.select_task(records[4]))
            self.assertTrue(workspace.retry_selected_task())
            self.assertEqual(retries[-1], (records[4],))
            workspace.set_task_filter("处理中")
            self.assertEqual(workspace.filtered_task_records, (records[1],))
            workspace.set_task_filter("全部")
            self.assertEqual(len(workspace.task_tree.get_children()), 5)
            self.assertEqual(workspace.items, original_queue)
            self.assertEqual(queue_changes, [5])
            self.assertTrue(workspace.tree.bind("<Return>"))
            self.assertTrue(workspace.tree.bind("<space>"))
            self.assertTrue(workspace.tree.bind("<Delete>"))

    def test_success_without_output_and_callbacks_without_selection_are_disabled(self) -> None:
        workspace = ConversionWorkspace(
            self.root,
            on_retry_tasks=lambda _records: None,
            on_open_result=lambda _path: None,
            on_reveal_result=lambda _path: None,
        )
        workspace.pack(fill="both", expand=True)
        record = SimpleNamespace(
            input=SimpleNamespace(path=Path("无输出.png")),
            status="succeeded",
            output=None,
            planned_output=Path("仅计划.webp"),
            error=None,
        )
        workspace.set_task_records((record,))
        workspace.select_task(record)

        self.assertIn("disabled", workspace.retry_task_button.state())
        self.assertIn("disabled", workspace.open_result_button.state())
        self.assertIn("disabled", workspace.reveal_result_button.state())
        self.assertFalse(workspace.retry_selected_task())
        self.assertFalse(workspace.open_selected_result())
        self.assertFalse(workspace.reveal_selected_result())

    def test_one_thousand_task_rows_filter_with_native_items(self) -> None:
        workspace = ConversionWorkspace(self.root)
        workspace.pack(fill="both", expand=True)
        statuses = ("pending", "running", "succeeded", "failed", "cancelled")
        records = tuple(
            SimpleNamespace(
                input=SimpleNamespace(path=Path(f"task-{index}.png")),
                status=statuses[index % len(statuses)],
                output=Path(f"task-{index}.webp") if index % 5 == 2 else None,
                error="失败" if index % 5 == 3 else None,
            )
            for index in range(1000)
        )
        workspace.set_task_records(records)

        started = time.perf_counter()
        for _index in range(5):
            for label in ("处理中", "成功", "失败", "取消", "全部"):
                workspace.set_task_filter(label)
        elapsed = time.perf_counter() - started

        self.assertLess(elapsed, 5.0)
        self.assertEqual(len(workspace.task_tree.get_children()), 1000)
        workspace.set_task_filter("失败")
        children = workspace.task_tree.get_children()
        self.assertEqual(len(children), 200)
        self.assertTrue(all(workspace.task_tree.item(iid, "image") == "" for iid in children))
        self.assertEqual(workspace.items, ())

    def test_one_thousand_rows_remain_native_text_items(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [_touch(root / f"image-{index}.png") for index in range(1000)]
            workspace = ConversionWorkspace(self.root)
            workspace.pack(fill="both", expand=True)

            workspace.add_files(reversed(paths))

            children = workspace.tree.get_children()
            self.assertEqual(len(children), 1000)
            self.assertEqual(workspace.tree.item(children[0], "values")[2], "image-0.png")
            self.assertEqual(workspace.tree.item(children[-1], "values")[2], "image-999.png")
            self.assertTrue(all(workspace.tree.item(iid, "image") == "" for iid in children))
            self.assertFalse(hasattr(workspace, "_photos"))


if __name__ == "__main__":
    unittest.main()
