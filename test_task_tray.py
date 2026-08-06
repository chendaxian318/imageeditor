from __future__ import annotations

import tkinter as tk
import unittest
from datetime import UTC, datetime, timedelta

from light_image_editor.shell import ModeId
from light_image_editor.ui.task_tray import (
    TaskTray,
    TaskTrayModel,
    UnifiedTaskItem,
    UnifiedTaskKind,
    UnifiedTaskStatus,
)


def _item(
    task_id: str,
    *,
    status: UnifiedTaskStatus = UnifiedTaskStatus.RUNNING,
    updated_offset: int = 0,
    cancellable: bool | None = None,
    retryable: bool = False,
) -> UnifiedTaskItem:
    created = datetime(2026, 8, 5, 8, 0, tzinfo=UTC)
    return UnifiedTaskItem(
        task_id=task_id,
        kind=UnifiedTaskKind.GENERATION,
        title=f"任务 {task_id}",
        status=status,
        mode=ModeId.GENERATE,
        detail="测试任务",
        progress_current=1,
        progress_total=2,
        cancellable=(not status.terminal if cancellable is None else cancellable),
        retryable=retryable,
        created_at=created,
        updated_at=created + timedelta(seconds=updated_offset),
    )


class TaskTrayModelTests(unittest.TestCase):
    def test_task_item_rejects_incomplete_progress_and_terminal_cancel(self) -> None:
        with self.assertRaisesRegex(ValueError, "进度"):
            UnifiedTaskItem(
                task_id="bad-progress",
                kind=UnifiedTaskKind.CONVERSION,
                title="错误进度",
                status=UnifiedTaskStatus.RUNNING,
                mode=ModeId.CONVERT,
                progress_current=1,
            )
        with self.assertRaisesRegex(ValueError, "不能取消"):
            _item(
                "bad-cancel",
                status=UnifiedTaskStatus.SUCCEEDED,
                cancellable=True,
            )

    def test_model_orders_active_first_and_trims_old_terminal_items(self) -> None:
        model = TaskTrayModel(maximum_items=2)
        model.upsert(_item("old", status=UnifiedTaskStatus.SUCCEEDED))
        model.upsert(_item("active", updated_offset=2))
        model.upsert(
            _item("new", status=UnifiedTaskStatus.FAILED, updated_offset=3, retryable=True)
        )

        self.assertEqual(tuple(item.task_id for item in model.items), ("active", "new"))
        self.assertEqual(model.active_count, 1)

    def test_upsert_preserves_original_creation_time_and_clear_keeps_active(self) -> None:
        model = TaskTrayModel((_item("job"),))
        original = model.get("job")
        assert original is not None
        completed = _item("job", status=UnifiedTaskStatus.SUCCEEDED, updated_offset=5)
        model.upsert(completed)

        restored = model.get("job")
        assert restored is not None
        self.assertEqual(restored.created_at, original.created_at)
        model.upsert(_item("active", updated_offset=6))
        self.assertEqual(model.clear_finished(), 1)
        self.assertEqual(tuple(item.task_id for item in model.items), ("active",))

    def test_task_item_json_round_trip_is_strict(self) -> None:
        original = _item("round-trip", status=UnifiedTaskStatus.UNKNOWN, retryable=True)
        restored = UnifiedTaskItem.from_json(original.to_json())
        self.assertEqual(restored, original)
        invalid = original.to_json()
        invalid["unexpected"] = True
        with self.assertRaisesRegex(ValueError, "未知字段"):
            UnifiedTaskItem.from_json(invalid)
        invalid_type = original.to_json()
        invalid_type["title"] = 42
        with self.assertRaisesRegex(ValueError, "必须是字符串"):
            UnifiedTaskItem.from_json(invalid_type)


class TaskTrayWidgetTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self) -> None:
        if self.root.winfo_exists():
            self.root.destroy()

    def test_widget_routes_activate_cancel_retry_and_updates_summary(self) -> None:
        activated: list[str] = []
        cancelled: list[str] = []
        retried: list[str] = []
        changed: list[tuple[str, ...]] = []
        tray = TaskTray(
            self.root,
            self.root,
            on_activate=lambda item: activated.append(item.task_id),
            on_cancel=lambda item: cancelled.append(item.task_id),
            on_retry=lambda item: retried.append(item.task_id),
            on_changed=lambda items: changed.append(tuple(item.task_id for item in items)),
        )
        tray.trigger_button.grid()
        tray.upsert(_item("running"))
        tray.upsert(_item("failed", status=UnifiedTaskStatus.FAILED, retryable=True))
        self.root.update_idletasks()

        self.assertEqual(tray.model.active_count, 1)
        self.assertEqual(tray.trigger_button.cget("text"), "任务 1")
        self.assertTrue(tray.select_task("running"))
        self.assertTrue(tray.cancel_selected())
        self.assertEqual(cancelled, ["running"])
        self.assertTrue(tray.activate_selected())
        self.assertEqual(activated, ["running"])
        self.assertTrue(tray.select_task("failed"))
        self.assertTrue(tray.retry_selected())
        self.assertEqual(retried, ["failed"])
        self.assertTrue(changed)

    def test_popup_visibility_and_clear_finished_are_independent_of_source_history(self) -> None:
        tray = TaskTray(self.root, self.root)
        tray.trigger_button.grid()
        tray.set_items(
            (
                _item("active"),
                _item("done", status=UnifiedTaskStatus.SUCCEEDED),
            ),
            notify=False,
        )
        tray.show()
        self.root.update_idletasks()
        self.assertTrue(tray._visible)
        tray.hide()
        self.assertFalse(tray._visible)
        self.assertEqual(tray.clear_finished(), 1)
        self.assertIsNotNone(tray.get("active"))
        self.assertIsNone(tray.get("done"))


if __name__ == "__main__":
    unittest.main()
