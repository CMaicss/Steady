import copy
import json
import os
import sqlite3
import tempfile
from pathlib import Path

from . import APP_NAME, VERSION
from .i18n import _
from .timeutils import normalize_timestamp, utc_now

MAX_BACKUP_BYTES = 32 * 1024 * 1024
EVENT_KINDS = {"created", "updated", "progress", "closed", "reopened", "todo"}
TODO_ACTIONS = {"added", "renamed", "completed", "unchecked", "deleted"}


class ValidationError(ValueError):
    pass


def text_value(value, label, maximum, required=False):
    if not isinstance(value, str) or "\0" in value:
        raise ValidationError(_("{label} must be valid text").format(label=label))
    value = value.strip()
    if required and not value:
        raise ValidationError(_("Required field: {label}").format(label=label))
    if len(value) > maximum:
        raise ValidationError(_("{label} cannot exceed {maximum:,} characters").format(label=label, maximum=maximum))
    return value


def deadline_value(value):
    if value is None:
        return None
    try:
        return normalize_timestamp(value)
    except (ValueError, TypeError, OverflowError) as error:
        raise ValidationError(_("Invalid deadline")) from error


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.connection = sqlite3.connect(self.path, timeout=5)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys = ON")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise ValidationError(_("This database was created by a newer version. Open it with a newer {app_name}").format(app_name=APP_NAME))
            existing = self.connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            if version == 0 and existing:
                raise ValidationError(_("This file is not a {app_name} database. Opening was stopped to protect your data").format(app_name=APP_NAME))
            self.connection.execute("PRAGMA journal_mode = WAL")
            self.connection.execute("PRAGMA synchronous = FULL")
            if version == 0:
                self.connection.executescript("""
                    BEGIN;
                    CREATE TABLE tasks (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                        description TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL CHECK(status IN ('open', 'closed')),
                        deadline TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        closed_at TEXT,
                        CHECK((status = 'open' AND closed_at IS NULL) OR
                              (status = 'closed' AND closed_at IS NOT NULL))
                    );
                    CREATE TABLE task_events (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        task_id INTEGER NOT NULL REFERENCES tasks(id),
                        kind TEXT NOT NULL CHECK(kind IN ('created','updated','progress','closed','reopened')),
                        content TEXT NOT NULL DEFAULT '',
                        changes TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    );
                    CREATE INDEX events_task_id ON task_events(task_id, id DESC);
                    CREATE INDEX tasks_status_deadline ON tasks(status, deadline);
                    PRAGMA user_version = 1;
                    COMMIT;
                """)
            result = self.connection.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise ValidationError(_("Database integrity check failed. Restore a backup; the original file has not been overwritten"))
            if version < 2:
                self._migrate_v2(backup=version == 1)
            os.chmod(self.path, 0o600)
        except Exception:
            self.connection.close()
            raise

    def _migrate_v2(self, backup):
        if backup:
            directory = self.path.parent / "backups"
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            path = directory / f"before-schema-2-{utc_now().replace(':', '-')}.sqlite3"
            destination = sqlite3.connect(path)
            try:
                self.connection.backup(destination)
            finally:
                destination.close()
            os.chmod(path, 0o600)
        try:
            self.connection.executescript("""
                BEGIN IMMEDIATE;
                ALTER TABLE task_events RENAME TO task_events_v1;
                CREATE TABLE task_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL CHECK(kind IN ('created','updated','progress','closed','reopened','todo')),
                    content TEXT NOT NULL DEFAULT '',
                    changes TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    edited_at TEXT
                );
                INSERT INTO task_events(id,task_id,kind,content,changes,created_at)
                    SELECT id,task_id,kind,content,changes,created_at FROM task_events_v1;
                DROP TABLE task_events_v1;
                CREATE INDEX events_task_id ON task_events(task_id, id DESC);
                CREATE INDEX events_task_kind ON task_events(task_id, kind, id DESC);
                CREATE TABLE todos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                    title TEXT NOT NULL CHECK(length(trim(title)) > 0),
                    completed INTEGER NOT NULL DEFAULT 0 CHECK(completed IN (0,1)),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    CHECK((completed=0 AND completed_at IS NULL) OR
                          (completed=1 AND completed_at IS NOT NULL))
                );
                CREATE INDEX todos_task_id ON todos(task_id, id);
                PRAGMA user_version = 2;
                COMMIT;
            """)
        except Exception:
            self.connection.rollback()
            raise

    def close(self):
        self.connection.close()

    def get_task(self, task_id):
        row = self.connection.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            raise ValidationError(_("Task not found; a different backup may have been restored"))
        return dict(row)

    def _open_task(self, task_id):
        task = self.get_task(task_id)
        if task["status"] != "open":
            raise ValidationError(_("This task is closed. Reopen it first"))
        return task

    def _event(self, task_id, kind, when, content="", changes=None):
        self.connection.execute(
            "INSERT INTO task_events(task_id,kind,content,changes,created_at) VALUES(?,?,?,?,?)",
            (task_id, kind, content, json.dumps(changes or {}, ensure_ascii=False), when),
        )

    def create_task(self, title, description="", deadline=None):
        title = text_value(title, _("Task title"), 200, True)
        description = text_value(description, _("Task description"), 20000)
        deadline = deadline_value(deadline)
        when = utc_now()
        with self.connection:
            cursor = self.connection.execute(
                "INSERT INTO tasks(title,description,status,deadline,created_at,updated_at) VALUES(?,?,'open',?,?,?)",
                (title, description, deadline, when, when),
            )
            task_id = cursor.lastrowid
            self._event(task_id, "created", when, changes={"title": title, "description": description, "deadline": deadline})
        return task_id

    def update_task(self, task_id, title, description="", deadline=None):
        values = {"title": text_value(title, _("Task title"), 200, True),
                  "description": text_value(description, _("Task description"), 20000), "deadline": deadline_value(deadline)}
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            task = self._open_task(task_id)
            changes = {key: {"before": task[key], "after": value} for key, value in values.items() if task[key] != value}
            if not changes:
                return False
            when = utc_now()
            self.connection.execute(
                "UPDATE tasks SET title=?,description=?,deadline=?,updated_at=? WHERE id=?",
                (values["title"], values["description"], values["deadline"], when, task_id),
            )
            self._event(task_id, "updated", when, changes=changes)
        return True

    def add_progress(self, task_id, content):
        content = text_value(content, _("Progress note"), 50000, True)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self._open_task(task_id)
            when = utc_now()
            self._event(task_id, "progress", when, content)
            self.connection.execute("UPDATE tasks SET updated_at=? WHERE id=?", (when, task_id))

    def close_task(self, task_id, note=""):
        note = text_value(note, _("Closure note"), 50000)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self._open_task(task_id)
            when = utc_now()
            self.connection.execute("UPDATE tasks SET status='closed',closed_at=?,updated_at=? WHERE id=?", (when, when, task_id))
            self._event(task_id, "closed", when, note)

    def reopen_task(self, task_id, note=""):
        note = text_value(note, _("Reopening note"), 50000)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if self.get_task(task_id)["status"] != "closed":
                raise ValidationError(_("This task is already open"))
            when = utc_now()
            self.connection.execute("UPDATE tasks SET status='open',closed_at=NULL,updated_at=? WHERE id=?", (when, task_id))
            self._event(task_id, "reopened", when, note)

    def delete_task(self, task_id):
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.get_task(task_id)
            self.connection.execute("DELETE FROM tasks WHERE id=?", (task_id,))

    def get_event(self, event_id):
        row = self.connection.execute("SELECT * FROM task_events WHERE id=?", (event_id,)).fetchone()
        if row is None:
            raise ValidationError(_("Activity not found or already deleted"))
        return {**dict(row), "changes": json.loads(row["changes"])}

    def edit_event(self, event_id, content):
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            event = self.get_event(event_id)
            content = text_value(content, _("Activity text"), 50000, event["kind"] == "progress")
            if event["content"] == content and (event["edited_at"] is not None or event["kind"] not in ("created", "updated", "todo")):
                return False
            when = utc_now()
            self.connection.execute("UPDATE task_events SET content=?,edited_at=? WHERE id=?", (content, when, event_id))
            self.connection.execute("UPDATE tasks SET updated_at=? WHERE id=?", (when, event["task_id"]))
        return True

    def delete_event(self, event_id):
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            event = self.get_event(event_id)
            self.connection.execute("DELETE FROM task_events WHERE id=?", (event_id,))
            self.connection.execute("UPDATE tasks SET updated_at=? WHERE id=?", (utc_now(), event["task_id"]))

    def get_todo(self, todo_id):
        row = self.connection.execute("SELECT * FROM todos WHERE id=?", (todo_id,)).fetchone()
        if row is None:
            raise ValidationError(_("Todo not found or already deleted"))
        return dict(row)

    def todos(self, task_id, limit=100):
        return [dict(row) for row in self.connection.execute("SELECT * FROM todos WHERE task_id=? ORDER BY id LIMIT ?", (task_id, limit))]

    def todo_counts(self, task_id):
        row = self.connection.execute("SELECT COUNT(*) AS total, COALESCE(SUM(completed),0) AS done FROM todos WHERE task_id=?", (task_id,)).fetchone()
        return row["done"], row["total"]

    def _todo_event(self, task_id, todo_id, action, title, when, before=None):
        changes = {"todo_id": todo_id, "action": action, "title": title}
        if before is not None:
            changes["before"] = before
        self._event(task_id, "todo", when, changes=changes)
        self.connection.execute("UPDATE tasks SET updated_at=? WHERE id=?", (when, task_id))

    def add_todo(self, task_id, title):
        title = text_value(title, _("Todo text"), 500, True)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self._open_task(task_id)
            when = utc_now()
            cursor = self.connection.execute("INSERT INTO todos(task_id,title,created_at,updated_at) VALUES(?,?,?,?)", (task_id, title, when, when))
            todo_id = cursor.lastrowid
            self._todo_event(task_id, todo_id, "added", title, when)
        return todo_id

    def rename_todo(self, todo_id, title):
        title = text_value(title, _("Todo text"), 500, True)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            todo = self.get_todo(todo_id)
            self._open_task(todo["task_id"])
            if todo["title"] == title:
                return False
            when = utc_now()
            self.connection.execute("UPDATE todos SET title=?,updated_at=? WHERE id=?", (title, when, todo_id))
            self._todo_event(todo["task_id"], todo_id, "renamed", title, when, todo["title"])
        return True

    def set_todo_completed(self, todo_id, completed):
        if type(completed) is not bool:
            raise ValidationError(_("Todo state must be checked or unchecked"))
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            todo = self.get_todo(todo_id)
            self._open_task(todo["task_id"])
            if bool(todo["completed"]) == completed:
                return False
            when = utc_now()
            self.connection.execute("UPDATE todos SET completed=?,completed_at=?,updated_at=? WHERE id=?", (int(completed), when if completed else None, when, todo_id))
            self._todo_event(todo["task_id"], todo_id, "completed" if completed else "unchecked", todo["title"], when)
        return True

    def delete_todo(self, todo_id):
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            todo = self.get_todo(todo_id)
            self._open_task(todo["task_id"])
            self.connection.execute("DELETE FROM todos WHERE id=?", (todo_id,))
            self._todo_event(todo["task_id"], todo_id, "deleted", todo["title"], utc_now())

    def counts(self):
        row = self.connection.execute("SELECT COUNT(*) AS total, COALESCE(SUM(status='open'),0) AS opened FROM tasks").fetchone()
        return row["opened"], row["total"]

    def list_tasks(self, status="open", query="", limit=100):
        if status not in ("open", "closed", None):
            raise ValidationError(_("Unknown task status"))
        escaped = query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        pattern = f"%{escaped}%"
        rows = self.connection.execute(r"""
            SELECT tasks.*,
                (SELECT COUNT(*) FROM todos WHERE task_id=tasks.id) AS todo_total,
                (SELECT COUNT(*) FROM todos WHERE task_id=tasks.id AND completed=1) AS todo_done,
                (SELECT content FROM task_events WHERE task_id=tasks.id AND kind='progress' ORDER BY id DESC LIMIT 1) AS latest_progress
            FROM tasks
            WHERE (? IS NULL OR status=?) AND
                (?='' OR title LIKE ? ESCAPE '\' OR description LIKE ? ESCAPE '\' OR
                 EXISTS(SELECT 1 FROM task_events WHERE task_id=tasks.id AND content LIKE ? ESCAPE '\'))
            ORDER BY (status='closed'),
                CASE WHEN status='open' THEN deadline IS NULL END,
                CASE WHEN status='open' THEN deadline END,
                CASE WHEN status='closed' THEN closed_at END DESC,
                updated_at DESC, id DESC
            LIMIT ?
        """, (status, status, query.strip(), pattern, pattern, pattern, limit)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _events_where(task_id, kinds):
        if kinds is None:
            return "task_id=?", (task_id,)
        kinds = tuple(kinds)
        if any(kind not in EVENT_KINDS for kind in kinds):
            raise ValidationError(_("Unknown activity type"))
        return "task_id=? AND kind IN (" + ",".join("?" for kind in kinds) + ")", (task_id, *kinds)

    def events(self, task_id, limit=100, kinds=None):
        where, parameters = self._events_where(task_id, kinds)
        rows = self.connection.execute(f"SELECT * FROM task_events WHERE {where} ORDER BY id DESC LIMIT ?", (*parameters, limit)).fetchall()
        return [{**dict(row), "changes": json.loads(row["changes"])} for row in rows]

    def event_count(self, task_id, kinds=None):
        where, parameters = self._events_where(task_id, kinds)
        return self.connection.execute(f"SELECT COUNT(*) FROM task_events WHERE {where}", parameters).fetchone()[0]

    def export_data(self):
        with self.connection:
            self.connection.execute("BEGIN")
            tasks = [dict(row) for row in self.connection.execute("SELECT * FROM tasks ORDER BY id")]
            events = [dict(row) for row in self.connection.execute("SELECT * FROM task_events ORDER BY id")]
            todos = [dict(row) for row in self.connection.execute("SELECT * FROM todos ORDER BY id")]
        for event in events:
            event["changes"] = json.loads(event["changes"])
        return {"format": "steady-backup", "schema": 2, "app_version": VERSION, "exported_at": utc_now(), "tasks": tasks, "events": events, "todos": todos}

    def export_backup(self, path):
        protected = [self.path, self.path.parent / "state.json", self.path.parent / "language.json", Path(str(self.path) + "-wal"), Path(str(self.path) + "-shm")]
        if Path(path).resolve() in [item.resolve() for item in protected]:
            raise ValidationError(_("A backup cannot overwrite the application database or settings"))
        data = self.export_data()
        if len(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")) > MAX_BACKUP_BYTES:
            raise ValidationError(_("The backup exceeds 32 MiB and cannot be exported as a restorable JSON backup. Close the application and back up the entire data directory."))
        atomic_json(path, data)

    @staticmethod
    def read_backup(path):
        with Path(path).open("rb") as stream:
            contents = stream.read(MAX_BACKUP_BYTES + 1)
        if len(contents) > MAX_BACKUP_BYTES:
            raise ValidationError(_("The backup exceeds 32 MB and cannot be imported"))
        try:
            return Store.validate_backup(json.loads(contents))
        except (ValueError, TypeError, KeyError, OverflowError, RecursionError) as error:
            raise ValidationError(_("Invalid backup: {error}").format(error=error)) from error

    @staticmethod
    def validate_backup(data):
        if not isinstance(data, dict) or data.get("format") not in ("steady-backup", "mtodo-backup") or type(data.get("schema")) is not int or data["schema"] not in (1, 2):
            raise ValidationError(_("Not a supported {app_name} backup").format(app_name=APP_NAME))
        data = copy.deepcopy(data)
        legacy = data["schema"] == 1
        if not isinstance(data.get("tasks"), list) or not isinstance(data.get("events"), list):
            raise ValidationError(_("Missing tasks or activity history"))
        tasks, events, histories = {}, set(), {}
        for task in data["tasks"]:
            task_id = task["id"]
            if type(task_id) is not int or not 0 < task_id < 2**63 or task_id in tasks:
                raise ValidationError(_("Invalid or duplicate task ID"))
            if set(task) != {"id", "title", "description", "status", "deadline", "created_at", "updated_at", "closed_at"}:
                raise ValidationError(_("Incomplete task fields"))
            text_value(task["title"], _("Task title"), 200, True)
            text_value(task["description"], _("Task description"), 20000)
            task["deadline"] = deadline_value(task["deadline"])
            task["created_at"] = normalize_timestamp(task["created_at"])
            task["updated_at"] = normalize_timestamp(task["updated_at"])
            if task["status"] not in ("open", "closed") or (task["status"] == "closed") != (task["closed_at"] is not None):
                raise ValidationError(_("Task status and closure time do not match"))
            if task["closed_at"]:
                task["closed_at"] = normalize_timestamp(task["closed_at"])
            tasks[task_id] = task
            histories[task_id] = []
        for event in data["events"]:
            event_id = event["id"]
            if type(event_id) is not int or not 0 < event_id < 2**63 or event_id in events:
                raise ValidationError(_("Invalid or duplicate activity ID"))
            fields = {"id", "task_id", "kind", "content", "changes", "created_at"}
            if not legacy:
                fields.add("edited_at")
            if set(event) != fields or type(event["task_id"]) is not int or event["task_id"] not in tasks:
                raise ValidationError(_("Invalid activity or missing parent task"))
            if event["kind"] not in EVENT_KINDS or (legacy and event["kind"] == "todo") or not isinstance(event["changes"], dict):
                raise ValidationError(_("Invalid activity type"))
            text_value(event["content"], _("History note"), 50000, event["kind"] == "progress")
            event["created_at"] = normalize_timestamp(event["created_at"])
            event["edited_at"] = None if legacy or event["edited_at"] is None else normalize_timestamp(event["edited_at"])
            Store._validate_event_changes(event)
            events.add(event_id)
            histories[event["task_id"]].append(event)
        for task_id, history in (histories.items() if legacy else []):
            history.sort(key=lambda event: event["id"])
            if not history or history[0]["kind"] != "created":
                raise ValidationError(_("Task is missing its creation record"))
            initial = history[0]["changes"]
            if set(initial) != {"title", "description", "deadline"}:
                raise ValidationError(_("Creation record is missing its task snapshot"))
            text_value(initial["title"], _("Historical task title"), 200, True)
            text_value(initial["description"], _("Historical task description"), 20000)
            initial["deadline"] = deadline_value(initial["deadline"])
            current = {**initial, "status": "open", "closed_at": None}
            for event in history[1:]:
                kind = event["kind"]
                changes = event["changes"]
                if kind == "created" or (kind != "updated" and changes):
                    raise ValidationError(_("Invalid activity structure"))
                if kind == "reopened":
                    if current["status"] != "closed":
                        raise ValidationError(_("Invalid state in reopening record"))
                    current.update(status="open", closed_at=None)
                elif current["status"] != "open":
                    raise ValidationError(_("A closed task was modified without being reopened"))
                elif kind == "closed":
                    current.update(status="closed", closed_at=event["created_at"])
                elif kind == "updated":
                    if not changes or set(changes) - {"title", "description", "deadline"}:
                        raise ValidationError(_("Invalid update record fields"))
                    for key, change in changes.items():
                        if not isinstance(change, dict) or set(change) != {"before", "after"} or change["before"] != current[key]:
                            raise ValidationError(_("Before/after values in update record do not match"))
                        if key == "deadline":
                            change["after"] = deadline_value(change["after"])
                        else:
                            text_value(change["after"], key, 200 if key == "title" else 20000, key == "title")
                        current[key] = change["after"]
            task = tasks[task_id]
            if any(task[key] != value for key, value in current.items()) or task["created_at"] != history[0]["created_at"] or task["updated_at"] != history[-1]["created_at"]:
                raise ValidationError(_("Task and activity history do not match"))
        todos = [] if legacy else data.get("todos")
        if not isinstance(todos, list):
            raise ValidationError(_("The backup is missing its Todo list"))
        todo_ids = set()
        for todo in todos:
            if set(todo) != {"id", "task_id", "title", "completed", "created_at", "updated_at", "completed_at"}:
                raise ValidationError(_("Incomplete Todo fields"))
            todo_id = todo["id"]
            if type(todo_id) is not int or not 0 < todo_id < 2**63 or todo_id in todo_ids:
                raise ValidationError(_("Invalid or duplicate Todo ID"))
            if type(todo["task_id"]) is not int or todo["task_id"] not in tasks:
                raise ValidationError(_("Todo is missing its parent task"))
            text_value(todo["title"], _("Todo text"), 500, True)
            if type(todo["completed"]) is not int or todo["completed"] not in (0, 1) or bool(todo["completed"]) != (todo["completed_at"] is not None):
                raise ValidationError(_("Todo state and completion time do not match"))
            todo["created_at"] = normalize_timestamp(todo["created_at"])
            todo["updated_at"] = normalize_timestamp(todo["updated_at"])
            if todo["completed_at"] is not None:
                todo["completed_at"] = normalize_timestamp(todo["completed_at"])
            todo_ids.add(todo_id)
        data.update(format="steady-backup", schema=2, todos=todos)
        return data

    @staticmethod
    def _validate_event_changes(event):
        changes = event["changes"]
        kind = event["kind"]
        if kind == "created":
            if set(changes) != {"title", "description", "deadline"}:
                raise ValidationError(_("Creation record is missing its task snapshot"))
            text_value(changes["title"], _("Historical title"), 200, True)
            text_value(changes["description"], _("History note"), 20000)
            changes["deadline"] = deadline_value(changes["deadline"])
        elif kind == "updated":
            if not changes or set(changes) - {"title", "description", "deadline"}:
                raise ValidationError(_("Invalid update record fields"))
            for key, change in changes.items():
                if not isinstance(change, dict) or set(change) != {"before", "after"}:
                    raise ValidationError(_("Invalid before/after values in update record"))
                for side in ("before", "after"):
                    if key == "deadline":
                        change[side] = deadline_value(change[side])
                    else:
                        text_value(change[side], _("Historical content"), 200 if key == "title" else 20000, key == "title")
        elif kind == "todo":
            expected = {"todo_id", "action", "title"}
            if changes.get("action") == "renamed":
                expected.add("before")
            if set(changes) != expected or changes["action"] not in TODO_ACTIONS:
                raise ValidationError(_("Invalid Todo activity record"))
            if type(changes["todo_id"]) is not int or not 0 < changes["todo_id"] < 2**63:
                raise ValidationError(_("Invalid Todo activity ID"))
            text_value(changes["title"], _("Historical Todo text"), 500, True)
            if "before" in changes:
                text_value(changes["before"], _("Historical Todo text"), 500, True)
        elif changes:
            raise ValidationError(_("Activity contains unknown fields"))

    def restore_backup(self, data):
        data = self.validate_backup(data)
        stamp = utc_now().replace(":", "-")
        backup_path = self.path.parent / "backups" / f"before-restore-{stamp}.json"
        self.export_backup(backup_path)
        with self.connection:
            self.connection.execute("DELETE FROM task_events")
            self.connection.execute("DELETE FROM tasks")
            self.connection.executemany(
                "INSERT INTO tasks(id,title,description,status,deadline,created_at,updated_at,closed_at) VALUES(:id,:title,:description,:status,:deadline,:created_at,:updated_at,:closed_at)",
                data["tasks"],
            )
            self.connection.executemany(
                "INSERT INTO task_events(id,task_id,kind,content,changes,created_at,edited_at) VALUES(:id,:task_id,:kind,:content,:changes,:created_at,:edited_at)",
                [{**event, "changes": json.dumps(event["changes"], ensure_ascii=False)} for event in data["events"]],
            )
            self.connection.executemany(
                "INSERT INTO todos(id,task_id,title,completed,created_at,updated_at,completed_at) VALUES(:id,:task_id,:title,:completed,:created_at,:updated_at,:completed_at)",
                data["todos"],
            )
        return backup_path
