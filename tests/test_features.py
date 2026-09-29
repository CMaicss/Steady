import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from steady.storage import Store, ValidationError


class FeatureTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tasks.sqlite3"
        self.store = Store(self.path)
        self.task_id = self.store.create_task("任务", "说明")

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def legacy_backup(self):
        data = self.store.export_data()
        data["format"] = "mtodo-backup"
        data["schema"] = 1
        data.pop("todos")
        for event in data["events"]:
            event.pop("edited_at")
        return data

    def test_todos_are_optional_and_task_closure_is_independent(self):
        self.assertEqual(self.store.todo_counts(self.task_id), (0, 0))
        self.assertIsNone(self.store.get_task(self.task_id)["deadline"])
        todo_id = self.store.add_todo(self.task_id, "独立步骤")
        self.store.close_task(self.task_id)
        self.assertEqual(self.store.get_todo(todo_id)["completed"], 0)
        for action in (lambda: self.store.add_todo(self.task_id, "不能添加"),
                       lambda: self.store.rename_todo(todo_id, "不能修改"),
                       lambda: self.store.set_todo_completed(todo_id, True),
                       lambda: self.store.delete_todo(todo_id)):
            with self.assertRaises(ValidationError):
                action()
        self.store.reopen_task(self.task_id)
        self.store.set_todo_completed(todo_id, True)
        self.assertEqual(self.store.get_task(self.task_id)["status"], "open")

    def test_todo_check_uncheck_and_rename_preserve_identity(self):
        todo_id = self.store.add_todo(self.task_id, "  第一步  ")
        original = self.store.get_todo(todo_id)
        self.assertEqual(original["title"], "第一步")
        self.store.set_todo_completed(todo_id, True)
        checked = self.store.get_todo(todo_id)
        self.assertEqual(self.store.todo_counts(self.task_id), (1, 1))
        self.assertEqual(checked["completed_at"], checked["updated_at"])
        self.assertFalse(self.store.set_todo_completed(todo_id, True))
        self.store.set_todo_completed(todo_id, False)
        self.assertIsNone(self.store.get_todo(todo_id)["completed_at"])
        self.store.rename_todo(todo_id, "第一步（修订）")
        self.assertEqual(self.store.get_todo(todo_id)["created_at"], original["created_at"])
        self.assertFalse(self.store.rename_todo(todo_id, "第一步（修订）"))
        events = self.store.events(self.task_id, kinds=("todo",))
        self.assertEqual([event["changes"]["action"] for event in events], ["renamed", "unchecked", "completed", "added"])
        self.assertEqual(events[0]["changes"]["before"], "第一步")

    def test_deleting_todo_keeps_its_past_activity(self):
        todo_id = self.store.add_todo(self.task_id, "步骤")
        self.store.delete_todo(todo_id)
        self.assertEqual(self.store.todos(self.task_id), [])
        self.assertEqual(self.store.todo_counts(self.task_id), (0, 0))
        self.assertEqual([event["changes"]["action"] for event in self.store.events(self.task_id, kinds=("todo",))], ["deleted", "added"])
        self.store.validate_backup(self.store.export_data())

    def test_progress_edit_keeps_creation_time_and_updates_search(self):
        self.store.add_progress(self.task_id, "旧内容")
        event = self.store.events(self.task_id)[0]
        self.store.edit_event(event["id"], "新内容")
        edited = self.store.get_event(event["id"])
        self.assertEqual(edited["created_at"], event["created_at"])
        self.assertIsNotNone(edited["edited_at"])
        self.assertEqual(edited["edited_at"], self.store.get_task(self.task_id)["updated_at"])
        self.assertEqual(self.store.list_tasks(query="旧内容"), [])
        self.assertEqual(self.store.list_tasks(query="新内容")[0]["latest_progress"], "新内容")
        self.assertFalse(self.store.edit_event(event["id"], "新内容"))
        with self.assertRaises(ValidationError):
            self.store.edit_event(event["id"], " ")

    def test_editing_or_deleting_system_events_does_not_change_state(self):
        todo_id = self.store.add_todo(self.task_id, "步骤")
        self.store.set_todo_completed(todo_id, True)
        self.store.update_task(self.task_id, "新标题", "新说明", "2030-01-01T18:00:00+08:00")
        self.store.close_task(self.task_id)
        original = self.store.get_task(self.task_id)
        todo = self.store.get_todo(todo_id)
        for event in self.store.events(self.task_id):
            self.store.edit_event(event["id"], "手动修正记录")
            self.assertEqual(self.store.get_event(event["id"])["changes"], event["changes"])
            self.store.delete_event(event["id"])
        self.assertEqual(self.store.events(self.task_id), [])
        current = self.store.get_task(self.task_id)
        for key in ("title", "description", "deadline", "status", "closed_at", "created_at"):
            self.assertEqual(current[key], original[key])
        self.assertEqual(self.store.get_todo(todo_id), todo)
        self.store.validate_backup(self.store.export_data())

    def test_delete_latest_progress_reveals_previous_preview(self):
        self.store.add_progress(self.task_id, "上一次进展")
        self.store.add_progress(self.task_id, "删除这次进展")
        self.store.delete_event(self.store.events(self.task_id)[0]["id"])
        self.assertEqual(self.store.list_tasks()[0]["latest_progress"], "上一次进展")
        self.assertEqual(self.store.list_tasks(query="删除这次进展"), [])

    def test_delete_task_cascades_and_does_not_touch_other_tasks(self):
        other_id = self.store.create_task("保留任务")
        self.store.add_todo(self.task_id, "删除步骤")
        kept = self.store.add_todo(other_id, "保留步骤")
        self.store.add_progress(self.task_id, "删除历史")
        self.store.close_task(self.task_id)
        self.store.delete_task(self.task_id)
        self.assertEqual(self.store.counts(), (1, 1))
        self.assertEqual(self.store.events(self.task_id), [])
        self.assertEqual(self.store.todos(self.task_id), [])
        self.assertEqual(self.store.get_todo(kept)["title"], "保留步骤")
        self.assertEqual(self.store.connection.execute("PRAGMA foreign_key_check").fetchall(), [])
        self.store.delete_task(other_id)
        self.assertEqual(self.store.counts(), (0, 0))

    def test_history_filter_applies_before_limit(self):
        self.store.add_progress(self.task_id, "很早的进展")
        for number in range(105):
            self.store.add_todo(self.task_id, f"步骤 {number}")
        self.store.close_task(self.task_id)
        self.store.reopen_task(self.task_id)
        self.assertEqual(len(self.store.events(self.task_id, limit=1, kinds=("progress",))), 1)
        self.assertEqual(self.store.event_count(self.task_id, ("todo",)), 105)
        self.assertEqual(len(self.store.events(self.task_id, limit=100, kinds=("todo",))), 100)
        self.assertEqual(self.store.event_count(self.task_id, ("closed", "reopened")), 2)
        self.assertEqual(self.store.events(self.task_id, kinds=()), [])
        self.assertEqual(self.store.event_count(self.task_id, ()), 0)
        with self.assertRaises(ValidationError):
            self.store.events(self.task_id, kinds=("invalid'",))

    def test_todo_validation_does_not_write_partial_data(self):
        for value in (" ", "x" * 501, "bad\0text", None):
            with self.assertRaises(ValidationError):
                self.store.add_todo(self.task_id, value)
        self.assertEqual(self.store.todo_counts(self.task_id), (0, 0))
        with patch.object(self.store, "_todo_event", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.add_todo(self.task_id, "不应保存")
        self.assertEqual(self.store.todo_counts(self.task_id), (0, 0))
        todo_id = self.store.add_todo(self.task_id, "步骤")
        original = self.store.get_todo(todo_id)
        with patch.object(self.store, "_todo_event", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.set_todo_completed(todo_id, True)
            with self.assertRaises(sqlite3.OperationalError):
                self.store.delete_todo(todo_id)
        self.assertEqual(self.store.get_todo(todo_id), original)

    def test_event_edit_and_delete_roll_back_when_task_update_fails(self):
        self.store.add_progress(self.task_id, "不可丢失")
        original = self.store.events(self.task_id)[0]
        self.store.connection.execute("CREATE TRIGGER fail_event_update BEFORE UPDATE ON tasks BEGIN SELECT RAISE(ABORT, 'failed'); END")
        for operation in (lambda: self.store.edit_event(original["id"], "新内容"), lambda: self.store.delete_event(original["id"])):
            with self.assertRaises(sqlite3.IntegrityError):
                operation()
            self.assertEqual(self.store.get_event(original["id"]), original)

    def test_backup_roundtrip_with_removed_events_and_todos(self):
        todo_id = self.store.add_todo(self.task_id, "步骤")
        self.store.set_todo_completed(todo_id, True)
        self.store.add_progress(self.task_id, "原内容")
        self.store.edit_event(self.store.events(self.task_id)[0]["id"], "修订内容")
        self.store.delete_event(self.store.events(self.task_id, kinds=("created",))[0]["id"])
        backup = self.store.export_data()
        self.store.delete_task(self.task_id)
        self.store.restore_backup(backup)
        for key in ("tasks", "events", "todos"):
            self.assertEqual(self.store.export_data()[key], backup[key])

    def test_legacy_backup_upgrade_and_validation(self):
        self.store.add_progress(self.task_id, "旧版进展")
        legacy = self.legacy_backup()
        original = copy.deepcopy(legacy)
        normalized = self.store.validate_backup(legacy)
        self.assertEqual(legacy, original)
        self.assertEqual(normalized["schema"], 2)
        self.assertEqual(normalized["format"], "steady-backup")
        self.assertEqual(normalized["todos"], [])
        self.assertTrue(all(event["edited_at"] is None for event in normalized["events"]))
        self.store.restore_backup(legacy)
        self.assertEqual(self.store.events(self.task_id)[0]["content"], "旧版进展")
        legacy["events"] = []
        with self.assertRaises(ValidationError):
            self.store.validate_backup(legacy)

    def test_legacy_v2_backup_is_readable_after_package_rename(self):
        self.store.add_todo(self.task_id, "旧版 Todo")
        backup = self.store.export_data()
        backup["format"] = "mtodo-backup"
        original = copy.deepcopy(backup)
        self.store.delete_task(self.task_id)
        self.store.restore_backup(backup)
        self.assertEqual(backup, original)
        self.assertEqual(self.store.export_data()["format"], "steady-backup")
        self.assertEqual(self.store.todo_counts(self.task_id), (0, 1))

    def test_migration_preserves_v1_and_creates_readable_backup(self):
        self.store.add_progress(self.task_id, "从旧版本迁移")
        legacy = self.legacy_backup()
        path = self.path.parent / "legacy.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript("""
            CREATE TABLE tasks(id INTEGER PRIMARY KEY AUTOINCREMENT,title TEXT,description TEXT,status TEXT,deadline TEXT,created_at TEXT,updated_at TEXT,closed_at TEXT);
            CREATE TABLE task_events(id INTEGER PRIMARY KEY AUTOINCREMENT,task_id INTEGER REFERENCES tasks(id),kind TEXT,content TEXT,changes TEXT,created_at TEXT);
            CREATE INDEX events_task_id ON task_events(task_id,id DESC);
            PRAGMA user_version=1;
        """)
        import json
        connection.executemany("INSERT INTO tasks VALUES(:id,:title,:description,:status,:deadline,:created_at,:updated_at,:closed_at)", legacy["tasks"])
        connection.executemany("INSERT INTO task_events VALUES(:id,:task_id,:kind,:content,:changes,:created_at)", [{**event, "changes": json.dumps(event["changes"])} for event in legacy["events"]])
        connection.commit()
        connection.close()
        migrated = Store(path)
        try:
            self.assertEqual(migrated.connection.execute("PRAGMA user_version").fetchone()[0], 2)
            self.assertEqual(migrated.events(self.task_id)[0]["content"], "从旧版本迁移")
            self.assertEqual(migrated.todo_counts(self.task_id), (0, 0))
            migrated.add_todo(self.task_id, "升级后添加")
            migrated.delete_task(self.task_id)
            self.assertEqual(migrated.todos(self.task_id), [])
        finally:
            migrated.close()
        backups = list((path.parent / "backups").glob("before-schema-2-*.sqlite3"))
        self.assertEqual(len(backups), 1)
        backup = sqlite3.connect(backups[0])
        try:
            self.assertEqual(backup.execute("PRAGMA user_version").fetchone()[0], 1)
            self.assertEqual(backup.execute("SELECT count(*) FROM task_events").fetchone()[0], 2)
        finally:
            backup.close()

    def test_backup_rejects_invalid_todos_and_activity_metadata(self):
        self.store.add_todo(self.task_id, "步骤")
        backup = self.store.export_data()
        for key, value in (("completed", 7), ("completed", True), ("completed_at", "invalid"), ("title", " "), ("task_id", 999), ("created_at", "2026-09-29T12:00:00")):
            bad = copy.deepcopy(backup)
            bad["todos"][0][key] = value
            with self.assertRaises(ValueError):
                self.store.validate_backup(bad)
        bad = copy.deepcopy(backup)
        bad["todos"].append(copy.deepcopy(bad["todos"][0]))
        with self.assertRaises(ValidationError):
            self.store.validate_backup(bad)
        bad = copy.deepcopy(backup)
        bad["events"][-1]["changes"]["action"] = "unknown"
        with self.assertRaises(ValidationError):
            self.store.validate_backup(bad)


if __name__ == "__main__":
    unittest.main()
