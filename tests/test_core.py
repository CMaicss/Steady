import copy
import os
import sqlite3
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from steady.storage import Store, ValidationError
from steady.timeutils import deadline_status, local_deadline, normalize_timestamp, parse_timestamp


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "tasks.sqlite3"
        self.store = Store(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_complete_lifecycle_without_deadline(self):
        task_id = self.store.create_task("  长期事项  ", "无需截止时间")
        self.assertIsNone(self.store.get_task(task_id)["deadline"])
        self.store.add_progress(task_id, "第一步\n第二步")
        self.store.close_task(task_id, "已完成")
        self.assertEqual(self.store.list_tasks(), [])
        self.assertEqual(len(self.store.list_tasks(status=None)), 1)
        with self.assertRaises(ValidationError):
            self.store.add_progress(task_id, "不应保存")
        self.store.reopen_task(task_id, "继续跟进")
        self.store.add_progress(task_id, "复核完成")
        self.store.close_task(task_id)
        self.assertEqual([event["kind"] for event in self.store.events(task_id)], ["closed", "progress", "reopened", "closed", "progress", "created"])
        for event in self.store.events(task_id):
            self.assertIsNotNone(parse_timestamp(event["created_at"]).tzinfo)
        self.store.validate_backup(self.store.export_data())

    def test_deadline_added_changed_and_removed(self):
        task_id = self.store.create_task("任务")
        for deadline in ("2026-10-01T18:00:00+08:00", "2026-10-03T18:00:00+08:00", None):
            self.store.update_task(task_id, "任务", deadline=deadline)
            self.assertEqual(self.store.get_task(task_id)["deadline"], normalize_timestamp(deadline) if deadline else None)
        changes = self.store.events(task_id)[0]["changes"]["deadline"]
        self.assertIsNotNone(changes["before"])
        self.assertIsNone(changes["after"])
        self.store.validate_backup(self.store.export_data())

    def test_noop_does_not_create_history(self):
        task_id = self.store.create_task("任务")
        original = self.store.get_task(task_id)
        self.assertFalse(self.store.update_task(task_id, "任务"))
        self.assertEqual(self.store.get_task(task_id), original)
        self.assertEqual(self.store.event_count(task_id), 1)

    def test_atomic_write_rolls_back_task_and_event(self):
        task_id = self.store.create_task("任务")
        original = self.store.get_task(task_id)
        with patch.object(self.store, "_event", side_effect=sqlite3.OperationalError("disk full")):
            with self.assertRaises(sqlite3.OperationalError):
                self.store.close_task(task_id)
            with self.assertRaises(sqlite3.OperationalError):
                self.store.create_task("不能留下半条数据")
        self.assertEqual(self.store.get_task(task_id), original)
        self.assertEqual(self.store.counts(), (1, 1))
        self.assertEqual(self.store.event_count(task_id), 1)

    def test_search_in_history_and_literal_wildcards(self):
        matching = self.store.create_task("100%_done\\")
        self.store.create_task("其他任务")
        self.store.add_progress(matching, "很久以前的独特记录")
        self.store.add_progress(matching, "最近记录")
        for query in ("100%_done\\", "%", "_", "独特记录"):
            self.assertEqual([task["id"] for task in self.store.list_tasks(query=query)], [matching])
        self.assertEqual(self.store.list_tasks(query="没有这个词"), [])
        self.assertEqual(len(self.store.list_tasks(limit=1)), 1)

    def test_sorting_and_filtering(self):
        undated = self.store.create_task("无日期")
        future = self.store.create_task("未来", deadline="2030-01-01T00:00:00Z")
        overdue = self.store.create_task("逾期", deadline="2020-01-01T00:00:00Z")
        closed = self.store.create_task("关闭", deadline="2010-01-01T00:00:00Z")
        self.store.close_task(closed)
        self.assertEqual([task["id"] for task in self.store.list_tasks(status=None)], [overdue, future, undated, closed])
        self.assertEqual([task["id"] for task in self.store.list_tasks(status="open")], [overdue, future, undated])
        self.assertEqual([task["id"] for task in self.store.list_tasks(status="closed", limit=1)], [closed])
        self.assertEqual(self.store.counts(), (3, 4))

    def test_status_filter_combines_search_and_pagination(self):
        closed = self.store.create_task("目标任务")
        self.store.add_progress(closed, "独特进展")
        self.store.close_task(closed)
        latest = self.store.create_task("新闭环任务")
        self.store.close_task(latest)
        opened = self.store.create_task("目标任务", deadline="2030-01-01T00:00:00Z")
        self.store.add_progress(opened, "独特进展")
        for number in range(101):
            self.store.create_task(f"未闭环任务 {number}")
        self.assertEqual([task["id"] for task in self.store.list_tasks(status="closed", limit=1)], [latest])
        self.assertEqual([task["id"] for task in self.store.list_tasks(status="closed", limit=2)], [latest, closed])
        self.assertEqual([task["id"] for task in self.store.list_tasks(status="closed", query="独特进展", limit=1)], [closed])
        self.assertEqual([task["id"] for task in self.store.list_tasks(query="独特进展")], [opened])
        self.assertEqual(len(self.store.list_tasks(status=None, query="目标任务")), 2)
        self.assertEqual(self.store.list_tasks(status="closed", query="不存在"), [])
        self.store.reopen_task(closed)
        self.assertEqual(self.store.list_tasks(status="closed", query="独特进展"), [])
        with self.assertRaises(ValidationError):
            self.store.list_tasks(status="unknown")

    def test_invalid_inputs_leave_no_events(self):
        for title in (" ", "x" * 201, "nul\0text", None):
            with self.assertRaises(ValidationError):
                self.store.create_task(title)
        for deadline in ("", "2026-10-01T18:00:00", "not a date", 17):
            with self.assertRaises(ValidationError):
                self.store.create_task("bad", deadline=deadline)
        self.assertEqual(self.store.counts(), (0, 0))
        task_id = self.store.create_task("任务")
        for note in ("  ", "x" * 50001, None):
            with self.assertRaises(ValidationError):
                self.store.add_progress(task_id, note)
        self.assertEqual(self.store.event_count(task_id), 1)

    def test_reopen_and_close_guards(self):
        task_id = self.store.create_task("任务")
        with self.assertRaises(ValidationError):
            self.store.reopen_task(task_id)
        self.store.close_task(task_id)
        for operation in (lambda: self.store.close_task(task_id), lambda: self.store.update_task(task_id, "修改")):
            with self.assertRaises(ValidationError):
                operation()
        self.assertEqual(self.store.event_count(task_id), 2)

    def test_data_persists_across_connections(self):
        task_id = self.store.create_task("保留")
        self.store.add_progress(task_id, "内容")
        other = Store(self.path)
        try:
            self.assertEqual(other.get_task(task_id)["title"], "保留")
            self.assertEqual(other.event_count(task_id), 2)
        finally:
            other.close()

    def test_backup_roundtrip_and_safety_copy(self):
        task_id = self.store.create_task("备份任务", deadline="2026-10-01T18:00:00+08:00")
        self.store.add_progress(task_id, "进展")
        self.store.close_task(task_id, "结束")
        self.store.reopen_task(task_id)
        path = self.path.parent / "export.json"
        self.store.export_backup(path)
        original = Store.read_backup(path)
        self.store.create_task("恢复前的数据")
        safety = self.store.restore_backup(original)
        self.assertEqual(len(Store.read_backup(safety)["tasks"]), 2)
        restored = self.store.export_data()
        self.assertEqual(original["tasks"], restored["tasks"])
        self.assertEqual(original["events"], restored["events"])

    def test_malformed_backup_cannot_replace_data(self):
        task_id = self.store.create_task("不可丢失")
        data = self.store.export_data()
        malformed = []
        bad = copy.deepcopy(data)
        bad["events"] = None
        malformed.append(bad)
        bad = copy.deepcopy(data)
        bad["tasks"][0]["title"] = ""
        malformed.append(bad)
        bad = copy.deepcopy(data)
        bad["events"][0]["task_id"] = 999
        malformed.append(bad)
        for bad in malformed:
            with self.assertRaises(ValidationError):
                self.store.restore_backup(bad)
            self.assertEqual(self.store.get_task(task_id)["title"], "不可丢失")

    def test_backup_cannot_overwrite_database_or_sidecars(self):
        for path in (self.path, Path(str(self.path) + "-wal"), self.path.parent / "state.json"):
            with self.assertRaises(ValidationError):
                self.store.export_backup(path)

    def test_unsupported_schema_rejected_without_reset(self):
        self.store.connection.execute("PRAGMA user_version=99")
        with self.assertRaises(ValidationError):
            Store(self.path)
        self.assertEqual(self.store.connection.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_non_app_database_is_not_adopted(self):
        foreign = self.path.parent / "foreign.sqlite3"
        connection = sqlite3.connect(foreign)
        connection.execute("CREATE TABLE other(value)")
        connection.close()
        with self.assertRaises(ValidationError):
            Store(foreign)

    def test_backup_read_error_and_size_limit(self):
        target = self.path.parent / "bad.json"
        target.write_text("{broken", encoding="utf-8")
        with self.assertRaises(ValidationError):
            Store.read_backup(target)
        with patch("steady.storage.MAX_BACKUP_BYTES", 4):
            with self.assertRaises(ValidationError):
                Store.read_backup(target)

    def test_oversized_export_does_not_overwrite_existing_backup(self):
        self.store.create_task("任务")
        target = self.path.parent / "backup.json"
        target.write_text("existing", encoding="utf-8")
        with patch("steady.storage.MAX_BACKUP_BYTES", 4):
            with self.assertRaises(ValidationError):
                self.store.export_backup(target)
        self.assertEqual(target.read_text(), "existing")

    def test_restore_failure_rolls_back_original_data(self):
        incoming_id = self.store.create_task("触发失败")
        incoming = self.store.export_data()
        self.store.update_task(incoming_id, "原数据")
        self.store.connection.execute("CREATE TRIGGER fail_restore BEFORE INSERT ON tasks WHEN NEW.title='触发失败' BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        original = self.store.export_data()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.restore_backup(incoming)
        self.assertEqual(self.store.export_data()["tasks"], original["tasks"])
        self.assertEqual(self.store.export_data()["events"], original["events"])

    def test_progress_failure_preserves_updated_time(self):
        task_id = self.store.create_task("任务")
        original = self.store.get_task(task_id)
        self.store.connection.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON tasks BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.add_progress(task_id, "不应留下半条历史")
        self.assertEqual(self.store.get_task(task_id), original)
        self.assertEqual(self.store.event_count(task_id), 1)

    def test_backup_rejects_malformed_history_snapshots(self):
        self.store.create_task("任务")
        data = self.store.export_data()
        data["events"][0]["changes"]["description"] = ["不能显示为文本"]
        with self.assertRaises(ValidationError):
            self.store.restore_backup(data)

    def test_atomic_json_failure_keeps_previous_file(self):
        from steady.storage import atomic_json
        target = self.path.parent / "state.json"
        atomic_json(target, {"draft": "原草稿"})
        with patch("steady.storage.os.replace", side_effect=OSError("permission denied")):
            with self.assertRaises(OSError):
                atomic_json(target, {"draft": "新草稿"})
        self.assertIn("原草稿", target.read_text())
        self.assertEqual(list(self.path.parent.glob(".state.json.*")), [])


class TimeTests(unittest.TestCase):
    def test_deadline_boundaries(self):
        now = datetime(2026, 9, 29, 12, tzinfo=timezone.utc)
        cases = [
            (None, ("", "")),
            ("2026-09-29T12:00:30Z", ("即将到期", "warning")),
            ("2026-09-29T12:00:00Z", ("已到期", "error")),
            ("2026-09-29T11:59:30Z", ("已逾期不足 1 分钟", "error")),
            ("2026-09-29T13:00:00Z", ("剩余 1 小时", "warning")),
            ("2026-09-30T12:00:00Z", ("剩余 1 天", "")),
            ("2026-09-27T09:00:00Z", ("已逾期 2 天 3 小时", "error")),
        ]
        for deadline, expected in cases:
            with self.subTest(deadline=deadline):
                self.assertEqual(deadline_status(deadline, now=now), expected)

    def test_closed_countdown_is_frozen(self):
        self.assertEqual(deadline_status("2026-10-01T12:00:00Z", "2026-10-01T12:00:00Z"), ("按时闭环", "success"))
        self.assertEqual(deadline_status("2026-10-01T12:00:00Z", "2026-10-01T12:00:01Z"), ("逾期闭环", "warning"))
        self.assertEqual(deadline_status(None, "2026-10-01T12:00:01Z"), ("", ""))

    def test_local_time_and_dst_validation(self):
        original = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "Asia/Shanghai"
            time.tzset()
            self.assertEqual(local_deadline("2026-09-29", 23, 59), "2026-09-29T15:59:00.000000+00:00")
            with self.assertRaises(ValueError):
                local_deadline("2026-02-30", 23, 59)
            os.environ["TZ"] = "America/New_York"
            time.tzset()
            with self.assertRaises(ValueError):
                local_deadline("2026-03-08", 2, 30)
        finally:
            if original is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = original
            time.tzset()


if __name__ == "__main__":
    unittest.main()
