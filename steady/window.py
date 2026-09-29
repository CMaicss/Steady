import json
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from . import APP_ID, APP_NAME, VERSION
from .storage import Store, atomic_json
from .timeutils import deadline_status, format_timestamp, utc_now
from .widgets import clear_box, confirm, error_dialog, label, text_of

EVENT_LABELS = {"created": "创建任务", "updated": "修改任务", "progress": "新增进展", "closed": "任务闭环", "reopened": "重新打开", "todo": "Todo 操作"}
FIELD_LABELS = {"title": "任务标题", "description": "任务说明", "deadline": "截止时间"}
TASK_FILTERS = (("未闭环", "open"), ("已闭环", "closed"), ("全部", None))
HISTORY_FILTERS = (("全部类型", None), ("仅进展", ("progress",)), ("任务修改", ("updated",)), ("创建任务", ("created",)), ("闭环 / 重开", ("closed", "reopened")), ("Todo 操作", ("todo",)))
TODO_LABELS = {"added": "添加 Todo", "renamed": "编辑 Todo", "completed": "完成 Todo", "unchecked": "取消完成 Todo", "deleted": "删除 Todo"}


@Gtk.Template(filename=str(Path(__file__).with_name("window.ui")))
class MainWindow(Adw.ApplicationWindow):
    __gtype_name__ = "SteadyWindow"

    toast_overlay = Gtk.Template.Child()
    split = Gtk.Template.Child()
    menu_button = Gtk.Template.Child()
    task_filter = Gtk.Template.Child()
    search = Gtk.Template.Child()
    list_stack = Gtk.Template.Child()
    list_scroll = Gtk.Template.Child()
    task_list = Gtk.Template.Child()
    more_tasks = Gtk.Template.Child()
    list_empty = Gtk.Template.Child()
    detail_page = Gtk.Template.Child()
    detail_stack = Gtk.Template.Child()
    detail_scroll = Gtk.Template.Child()
    task_status = Gtk.Template.Child()
    task_title = Gtk.Template.Child()
    task_description = Gtk.Template.Child()
    task_deadline = Gtk.Template.Child()
    task_meta = Gtk.Template.Child()
    history_heading = Gtk.Template.Child()
    history_list = Gtk.Template.Child()
    more_events = Gtk.Template.Child()
    history_filter = Gtk.Template.Child()
    history_empty = Gtk.Template.Child()
    todo_expander = Gtk.Template.Child()
    todo_list = Gtk.Template.Child()
    todo_empty = Gtk.Template.Child()
    todo_input_box = Gtk.Template.Child()
    todo_input = Gtk.Template.Child()
    more_todos = Gtk.Template.Child()
    bottom_box = Gtk.Template.Child()
    composer = Gtk.Template.Child()
    progress_input = Gtk.Template.Child()
    draft_hint = Gtk.Template.Child()
    closed_hint = Gtk.Template.Child()
    edit_button = Gtk.Template.Child()
    status_button = Gtk.Template.Child()

    def __init__(self, application, store):
        super().__init__(application=application)
        self.store = store
        self.state_path = store.path.parent / "state.json"
        self.state = self._read_state()
        self.drafts = self.state.get("drafts", {})
        self.todo_drafts = self.state.get("todo_drafts", {})
        self.todo_limit = 100
        self.todo_task_id = None
        self.resetting_todo = False
        self.selected_id = None
        self.selected_task = None
        self.task_limit = 100
        self.event_limit = 100
        self.task_rows = []
        self.refreshing = False
        self.loading_draft = False
        self.draft_timer = 0
        self.refresh_timer = 0
        self.active_form = None
        self.discard_on_exit = False
        self.state_error_shown = False
        self.actions = {}
        self.set_default_size(self.state.get("width", 1080), self.state.get("height", 760))
        if self.state.get("maximized", False):
            self.maximize()
        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 760sp"))
        breakpoint.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(breakpoint)
        for name, callback in {
            "new-task": self.new_task, "edit-task": self.edit_task,
            "change-status": self.change_status, "save-progress": self.save_progress,
            "delete-task": self.delete_task, "add-todo": self.add_todo,
            "search": self.focus_search, "export": self.export_backup,
            "restore": self.restore_backup, "data-folder": self.open_data_folder,
            "shortcuts": self.show_shortcuts, "about": self.show_about,
            "quit": self.close,
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda action, parameter, callback=callback: self._run(callback))
            self.add_action(action)
            self.actions[name] = action
        task_menu = Gio.Menu()
        task_menu.append("编辑任务…", "win.edit-task")
        task_menu.append("删除任务…", "win.delete-task")
        self.edit_button.set_menu_model(task_menu)
        self.history_filter.set_model(Gtk.StringList.new([item[0] for item in HISTORY_FILTERS]))
        selected_filter = self.state.get("history_filter", 0)
        self.history_filter.set_selected(selected_filter if type(selected_filter) is int and 0 <= selected_filter < len(HISTORY_FILTERS) else 0)
        self.history_filter.connect("notify::selected", self._history_filter_changed)
        menu = Gio.Menu()
        files = Gio.Menu()
        files.append("导出备份…", "win.export")
        files.append("从备份恢复…", "win.restore")
        files.append("打开数据文件夹", "win.data-folder")
        menu.append_section(None, files)
        help_menu = Gio.Menu()
        help_menu.append("键盘快捷键", "win.shortcuts")
        help_menu.append(f"关于 {APP_NAME}", "win.about")
        help_menu.append("退出", "win.quit")
        menu.append_section(None, help_menu)
        self.menu_button.set_menu_model(menu)
        self.task_filter.set_model(Gtk.StringList.new([item[0] for item in TASK_FILTERS]))
        self.task_filter.connect("notify::selected", self._filter_changed)
        self.search.connect("search-changed", self._search_changed)
        self.search.connect("stop-search", lambda entry: entry.set_text(""))
        self.task_list.connect("row-selected", self._row_selected)
        self.task_list.connect("row-activated", lambda box, row: self.split.set_show_content(True))
        self.more_tasks.connect("clicked", lambda button: self._run(self._load_tasks))
        self.more_events.connect("clicked", lambda button: self._run(self._load_events))
        self.more_todos.connect("clicked", lambda button: self._run(self._load_todos))
        self.todo_input.connect("activate", lambda entry: self._run(self.add_todo))
        self.todo_input.connect("changed", self._draft_changed)
        self.progress_input.get_buffer().connect("changed", self._draft_changed)
        self.progress_input.get_buffer().connect("insert-text", self._check_draft_length)
        self.connect("close-request", self._close_requested)
        self.connect("notify::is-active", lambda *args: self._run(self._tick) if self.is_active() else None)
        self.refresh(self.state.get("selected_id"))
        self.split.set_show_content(False)
        self.refresh_timer = GLib.timeout_add_seconds(30, self._tick_safely)

    def _run(self, callback):
        try:
            return callback()
        except Exception as error:
            error_dialog(self, "操作未完成", error)
            return None

    def _read_state(self):
        if not self.state_path.exists():
            return {}
        try:
            with self.state_path.open(encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict) or not isinstance(data.get("drafts", {}), dict):
                raise ValueError("设置格式无效")
            drafts = data.get("drafts", {})
            if any(not key.isdigit() or not isinstance(value, str) or len(value) > 50000 for key, value in drafts.items()):
                raise ValueError("草稿格式无效")
            todo_drafts = data.get("todo_drafts", {})
            if not isinstance(todo_drafts, dict) or any(not key.isdigit() or not isinstance(value, str) or len(value) > 500 for key, value in todo_drafts.items()):
                raise ValueError("Todo 草稿格式无效")
            for key, default, minimum, maximum in (("width", 1080, 360, 3840), ("height", 760, 480, 2160)):
                value = data.get(key, default)
                data[key] = min(maximum, max(minimum, value)) if type(value) is int else default
            return data
        except (ValueError, OSError) as error:
            backup = self.state_path.with_name(f"state-unreadable-{utc_now().replace(':', '-')}.json")
            try:
                self.state_path.rename(backup)
            except OSError:
                raise OSError(f"无法读取或保护设置文件：{self.state_path}；{error}") from error
            GLib.idle_add(lambda: (self.toast("设置文件无法读取，原文件已保留在数据目录。"), False)[1])
            return {}

    def _stash_draft(self):
        if self.selected_task and self.selected_task["status"] == "open":
            content = text_of(self.progress_input)
            key = str(self.selected_id)
            if content:
                self.drafts[key] = content
            else:
                self.drafts.pop(key, None)
            todo_text = self.todo_input.get_text()
            if todo_text:
                self.todo_drafts[key] = todo_text
            else:
                self.todo_drafts.pop(key, None)

    def _write_state(self):
        self._stash_draft()
        width, height = self.get_default_size()
        self.state.update(width=max(360, width), height=max(480, height), maximized=self.is_maximized(), drafts=self.drafts, todo_drafts=self.todo_drafts, selected_id=self.selected_id, history_filter=self.history_filter.get_selected())
        try:
            atomic_json(self.state_path, self.state)
            self.state_error_shown = False
            if self.selected_id and text_of(self.progress_input):
                self.draft_hint.set_text("草稿已本地保存 · Ctrl+Enter 提交")
            return True
        except OSError as error:
            self.draft_hint.set_text("草稿保存失败，请勿退出")
            if not self.state_error_shown:
                self.state_error_shown = True
                error_dialog(self, "草稿／窗口状态未保存", error)
            return False

    def _draft_changed(self, buffer):
        if self.loading_draft:
            return
        content = text_of(self.progress_input)
        if len(content) > 50000:
            self.draft_hint.set_text("超过 50,000 字限制，请缩短内容后保存")
            self.actions["save-progress"].set_enabled(False)
            return
        self.actions["save-progress"].set_enabled(bool(content.strip()) and bool(self.selected_task) and self.selected_task["status"] == "open")
        self.actions["add-todo"].set_enabled(bool(self.todo_input.get_text().strip()) and bool(self.selected_task) and self.selected_task["status"] == "open")
        self.draft_hint.set_text("正在保存草稿…" if content else "Ctrl+Enter 保存进展")
        if self.draft_timer:
            GLib.source_remove(self.draft_timer)
        self.draft_timer = GLib.timeout_add(450, self._save_draft_later)

    def _check_draft_length(self, buffer, location, text, length):
        if not self.loading_draft and buffer.get_char_count() + len(text) > 50000:
            buffer.stop_emission_by_name("insert-text")
            self.toast("一条进展最多 50,000 字，请分多条记录")

    def _save_draft_later(self):
        self.draft_timer = 0
        self._write_state()
        return False

    def _filter_changed(self, dropdown, parameter):
        self.task_limit = 100
        self._run(self.refresh)

    def _search_changed(self, entry):
        self.task_limit = 100
        self._run(self.refresh)

    def _load_tasks(self):
        self.task_limit += 100
        position = self.list_scroll.get_vadjustment().get_value()
        self.refresh()
        GLib.idle_add(lambda: (self.list_scroll.get_vadjustment().set_value(position), False)[1])

    def _load_events(self):
        self.event_limit += 100
        position = self.detail_scroll.get_vadjustment().get_value()
        self._render_history()
        GLib.idle_add(lambda: (self.detail_scroll.get_vadjustment().set_value(position), False)[1])

    def refresh(self, preferred_id=None):
        self._stash_draft()
        previous_id = self.selected_id
        desired = preferred_id if preferred_id is not None else previous_id
        status = TASK_FILTERS[self.task_filter.get_selected()][1]
        tasks = self.store.list_tasks(status, self.search.get_text(), self.task_limit + 1)
        while preferred_id is not None and len(tasks) > self.task_limit and not any(task["id"] == preferred_id for task in tasks[:self.task_limit]):
            self.task_limit += 100
            tasks = self.store.list_tasks(status, self.search.get_text(), self.task_limit + 1)
        total = self.store.counts()[1]
        self.more_tasks.set_visible(len(tasks) > self.task_limit)
        tasks = tasks[:self.task_limit]
        self.refreshing = True
        clear_box(self.task_list)
        self.task_rows = []
        chosen = None
        for task in tasks:
            row = self._task_row(task)
            self.task_list.append(row)
            self.task_rows.append(row)
            if task["id"] == desired:
                chosen = row
        if tasks:
            self.list_stack.set_visible_child_name("tasks")
            chosen = chosen or self.task_rows[0]
            self.task_list.select_row(chosen)
        else:
            self.list_stack.set_visible_child_name("empty")
            query = self.search.get_text().strip()
            if query:
                title, description = "没有找到任务", "试试其他关键词，或切换任务状态筛选。"
            elif total == 0:
                title, description = "还没有任务", "点击 + 创建任务。截止时间可以留空。"
            elif status == "closed":
                title, description = "没有已闭环任务", "闭环后的任务会显示在这里。"
            else:
                title, description = "没有未闭环任务", "所有任务都已闭环，可以在“已闭环”或“全部”中查看。"
            self.list_empty.set_title(title)
            self.list_empty.set_description(description)
            self.list_empty.set_icon_name("system-search-symbolic" if query else "object-select-symbolic")
        self.refreshing = False
        self.selected_id = chosen.task["id"] if chosen else None
        if self.selected_id != previous_id:
            self.event_limit = 100
            self.todo_limit = 100
        self._render_detail()

    def _task_row(self, task):
        row = Gtk.ListBoxRow()
        row.task = task
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        box.add_css_class("task-row")
        title = label(task["title"], "heading")
        title.set_lines(2)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(title)
        preview = task["latest_progress"] or task["description"] or "还没有进展，记录下一步。"
        preview_label = label(" ".join(preview.split()), "dim-label")
        preview_label.set_lines(2)
        preview_label.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(preview_label)
        footer = Gtk.Box(spacing=12)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, valign=Gtk.Align.END)
        row.deadline_label = label(style="deadline-badge")
        details.append(row.deadline_label)
        if task["todo_total"]:
            details.append(label(f"Todo {task['todo_done']} / {task['todo_total']}", "caption"))
        footer.append(details)
        closed = task["status"] == "closed"
        prefix = "闭环于" if closed else "更新于"
        timestamp = task["closed_at"] if closed else task["updated_at"]
        separator = "\n" if task["deadline"] or task["todo_total"] or closed else " "
        row.timestamp_label = label(prefix + separator + format_timestamp(timestamp), "dim-label", wrap=False)
        row.timestamp_label.add_css_class("caption")
        row.timestamp_label.set_xalign(1)
        row.timestamp_label.set_justify(Gtk.Justification.RIGHT)
        row.timestamp_label.set_valign(Gtk.Align.END)
        row.timestamp_label.set_tooltip_text(prefix + " " + format_timestamp(timestamp, True))
        footer.append(row.timestamp_label)
        box.append(footer)
        row.set_child(box)
        self._update_row_deadline(row)
        return row

    def _update_row_deadline(self, row):
        task = row.task
        text, style = deadline_status(task["deadline"], task["closed_at"])
        if task["status"] == "closed":
            text = "已闭环" + (" · " + text if text else "")
        row.deadline_label.set_text(text)
        row.deadline_label.set_visible(bool(text))
        self._status_style(row.deadline_label, style)
        row.deadline_label.set_tooltip_text("截止于 " + format_timestamp(task["deadline"]) if task["deadline"] else "未设置截止时间")

    @staticmethod
    def _status_style(widget, style):
        for name in ("warning", "error", "success"):
            widget.remove_css_class(name)
        if style:
            widget.add_css_class(style)

    def _row_selected(self, box, row):
        if self.refreshing or row is None:
            return
        self._write_state()
        self.selected_id = row.task["id"]
        self.event_limit = 100
        self.todo_limit = 100
        self._run(self._render_detail)
        self.split.set_show_content(True)

    def _render_detail(self):
        self.selected_task = self.store.get_task(self.selected_id) if self.selected_id else None
        task = self.selected_task
        self.detail_stack.set_visible_child_name("task" if task else "empty")
        self.bottom_box.set_visible(bool(task))
        self.edit_button.set_visible(bool(task))
        self.status_button.set_visible(bool(task))
        opened = bool(task and task["status"] == "open")
        self.actions["edit-task"].set_enabled(opened)
        self.actions["change-status"].set_enabled(bool(task))
        self.actions["delete-task"].set_enabled(bool(task))
        self.actions["save-progress"].set_enabled(False)
        self.composer.set_visible(opened)
        self.closed_hint.set_visible(bool(task) and not opened)
        self.loading_draft = True
        self.progress_input.get_buffer().set_text(self.drafts.get(str(self.selected_id), "") if opened else "")
        self.todo_input.set_text(self.todo_drafts.get(str(self.selected_id), "") if opened else "")
        self.loading_draft = False
        content = text_of(self.progress_input)
        self.actions["save-progress"].set_enabled(opened and bool(content.strip()) and len(content) <= 50000)
        self.actions["add-todo"].set_enabled(opened and bool(self.todo_input.get_text().strip()))
        self.draft_hint.set_text("已恢复本地草稿 · Ctrl+Enter 提交" if content else "Ctrl+Enter 保存进展")
        if not task:
            return
        self.task_title.set_text(task["title"])
        self.task_description.set_text(task["description"])
        self.task_description.set_visible(bool(task["description"]))
        self.task_status.set_text("未闭环" if opened else "已闭环")
        self._status_style(self.task_status, "" if opened else "success")
        self.task_meta.set_text(f"创建于 {format_timestamp(task['created_at'], True)}\n最近更新 {format_timestamp(task['updated_at'], True)}" + (f"\n闭环于 {format_timestamp(task['closed_at'], True)}" if task["closed_at"] else ""))
        self.status_button.set_label("闭环任务" if opened else "重新打开")
        self._update_detail_deadline()
        self._render_todos()
        self._render_history()
        self.detail_scroll.get_vadjustment().set_value(0)

    def _update_detail_deadline(self):
        task = self.selected_task
        if not task:
            return
        if not task["deadline"]:
            self.task_deadline.set_text("未设置截止时间")
            self._status_style(self.task_deadline, "")
            self.task_deadline.add_css_class("dim-label")
        else:
            text, style = deadline_status(task["deadline"], task["closed_at"])
            self.task_deadline.set_text(f"截止于 {format_timestamp(task['deadline'])} · {text}")
            self.task_deadline.remove_css_class("dim-label")
            self._status_style(self.task_deadline, style)

    def _render_history(self):
        clear_box(self.history_list)
        kinds = HISTORY_FILTERS[self.history_filter.get_selected()][1]
        count = self.store.event_count(self.selected_id, kinds)
        total = self.store.event_count(self.selected_id)
        self.history_heading.set_text(f"活动记录 · {count}" + (f" / {total}" if kinds is not None else ""))
        self.history_empty.set_visible(count == 0)
        empty_text = "尚无活动记录，可添加一条进展。" if self.selected_task["status"] == "open" else "尚无活动记录。重新打开任务后可继续添加进展。"
        self.history_empty.set_text(empty_text if kinds is None else "当前筛选下没有活动记录。")
        self.history_list.set_visible(count > 0)
        self.more_events.set_visible(count > self.event_limit)
        for event in self.store.events(self.selected_id, self.event_limit, kinds):
            row = Gtk.ListBoxRow(activatable=False, selectable=False)
            row.event_id = event["id"]
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            box.add_css_class("event-row")
            header = Gtk.Box(spacing=4)
            heading = label(EVENT_LABELS[event["kind"]], "heading")
            heading.set_hexpand(True)
            header.append(heading)
            row.edit_button = self._icon_button("document-edit-symbolic", "编辑活动记录", lambda event_id=event["id"]: self.edit_event(event_id))
            row.delete_button = self._icon_button("user-trash-symbolic", "删除活动记录", lambda event_id=event["id"]: self.delete_event(event_id))
            header.append(row.edit_button)
            header.append(row.delete_button)
            box.append(header)
            stamp_text = format_timestamp(event["created_at"], True)
            if event["edited_at"]:
                stamp_text += "\n编辑于 " + format_timestamp(event["edited_at"], True)
            stamp = label(stamp_text, "dim-label", selectable=True)
            stamp.add_css_class("caption")
            box.append(stamp)
            body = self._event_text(event)
            if body:
                box.append(label(body, selectable=True))
            if event["edited_at"] and event["kind"] in ("created", "updated", "todo"):
                original = Gtk.Expander(label="原始操作信息")
                original.set_child(label(self._original_event_text(event), "dim-label", selectable=True))
                box.append(original)
            row.set_child(box)
            self.history_list.append(row)

    @staticmethod
    def _original_event_text(event):
        changes = event["changes"]
        if event["kind"] == "created":
            return "\n".join(value for value in (changes["title"], changes["description"], "截止时间：" + format_timestamp(changes["deadline"])) if value)
        if event["kind"] == "updated":
            lines = []
            for key, change in changes.items():
                before, after = change["before"], change["after"]
                if key == "deadline":
                    before, after = format_timestamp(before), format_timestamp(after)
                lines.append(f"{FIELD_LABELS[key]}\n原：{before or '（空）'}\n新：{after or '（空）'}")
            return "\n\n".join(lines)
        if event["kind"] == "todo":
            return TODO_LABELS[changes["action"]] + "：" + changes["title"] + ("\n原内容：" + changes["before"] if "before" in changes else "")
        return event["content"]

    def _event_text(self, event):
        return event["content"] if event["edited_at"] is not None else self._original_event_text(event)

    def _icon_button(self, icon, tooltip, callback):
        button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, valign=Gtk.Align.START)
        button.add_css_class("flat")
        button.connect("clicked", lambda button: self._run(callback))
        return button

    def _history_filter_changed(self, dropdown, parameter):
        self.event_limit = 100
        if self.selected_id:
            self._run(self._render_history)
        self._write_state()

    def _refresh_after_mutation(self, task_id):
        position = self.detail_scroll.get_vadjustment().get_value()
        self.refresh(task_id)
        if self.selected_id == task_id:
            GLib.idle_add(lambda: (self.detail_scroll.get_vadjustment().set_value(position), False)[1])
        self._write_state()

    def edit_event(self, event_id):
        if self.active_form:
            return
        from .dialogs import TextDialog
        event = self.store.get_event(event_id)
        initial = self._event_text(event)
        def save(content):
            changed = content.strip() != initial.strip()
            if changed:
                self.store.edit_event(event_id, content)
                self._refresh_after_mutation(event["task_id"])
            self.toast("活动记录已更新" if changed else "没有需要保存的修改")
        self._present_form(TextDialog("编辑活动记录", EVENT_LABELS[event["kind"]], initial, save, explanation="仅修改这条记录的显示内容，不更改任务或 Todo 的当前状态。原发生时间保留，并记录编辑时间。"))

    def delete_event(self, event_id):
        if self.active_form:
            return
        event = self.store.get_event(event_id)
        def remove():
            self.store.delete_event(event_id)
            self._refresh_after_mutation(event["task_id"])
            self.toast("活动记录已删除，任务当前状态未改变")
        return confirm(self, "删除这条活动记录？", f"{EVENT_LABELS[event['kind']]} · {format_timestamp(event['created_at'], True)}\n\n仅删除记录，不回滚任务或 Todo 的当前状态。删除后无法撤销，可事先导出备份。", "删除记录", lambda: self._run(remove), True)

    def delete_task(self):
        if self.active_form or not self.selected_task:
            return
        task = self.selected_task
        task_id = task["id"]
        def remove():
            self.store.delete_task(task_id)
            self.drafts.pop(str(task_id), None)
            self.todo_drafts.pop(str(task_id), None)
            self.selected_id = None
            self.selected_task = None
            self.refresh()
            self._write_state()
            self.toast("任务及其活动记录、Todo 已删除")
        return confirm(self, "删除任务？", f"“{task['title']}”的所有活动记录、Todo 和未提交草稿也会删除。\n\n此操作无法撤销；如需保留历史，请改用闭环，或先导出备份。", "删除任务", lambda: self._run(remove), True)

    def _render_todos(self):
        done, total = self.store.todo_counts(self.selected_id)
        self.todo_expander.set_label(f"Todo 清单 · {done} / {total}" if total else "Todo 清单 · 可选")
        if self.todo_task_id != self.selected_id:
            self.todo_task_id = self.selected_id
            self.todo_expander.set_expanded(bool(total or self.todo_input.get_text()))
        opened = self.selected_task["status"] == "open"
        self.todo_input_box.set_visible(opened)
        self.todo_empty.set_visible(total == 0)
        self.todo_list.set_visible(total > 0)
        self.more_todos.set_visible(total > self.todo_limit)
        clear_box(self.todo_list)
        for todo in self.store.todos(self.selected_id, self.todo_limit):
            row = Gtk.ListBoxRow(activatable=False, selectable=False)
            row.todo = todo
            box = Gtk.Box(spacing=6)
            box.add_css_class("todo-row")
            row.check_button = Gtk.CheckButton(active=bool(todo["completed"]), valign=Gtk.Align.START, sensitive=opened, hexpand=True)
            row.check_button.update_property([Gtk.AccessibleProperty.LABEL], [todo["title"]])
            row.check_button.connect("toggled", lambda button, todo=todo: self._toggle_todo(button, todo))
            box.append(row.check_button)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
            title = label(todo["title"], "dim-label" if todo["completed"] else None)
            if todo["completed"]:
                attributes = Pango.AttrList()
                attributes.insert(Pango.attr_strikethrough_new(True))
                title.set_attributes(attributes)
            texts.append(title)
            if todo["completed_at"]:
                stamp = label("完成于 " + format_timestamp(todo["completed_at"]), "dim-label")
                stamp.add_css_class("caption")
                texts.append(stamp)
            row.check_button.set_child(texts)
            row.edit_button = self._icon_button("document-edit-symbolic", "编辑 Todo", lambda todo_id=todo["id"]: self.edit_todo(todo_id))
            row.delete_button = self._icon_button("user-trash-symbolic", "删除 Todo", lambda todo_id=todo["id"]: self.delete_todo(todo_id))
            row.edit_button.set_visible(opened)
            row.delete_button.set_visible(opened)
            box.append(row.edit_button)
            box.append(row.delete_button)
            row.set_child(box)
            self.todo_list.append(row)

    def _load_todos(self):
        self.todo_limit += 100
        self._render_todos()

    def add_todo(self):
        if not self.selected_task or self.active_form:
            return
        task_id = self.selected_id
        self.store.add_todo(task_id, self.todo_input.get_text())
        self.todo_input.set_text("")
        self.todo_drafts.pop(str(task_id), None)
        self.todo_expander.set_expanded(True)
        self._refresh_after_mutation(task_id)
        self.todo_input.grab_focus()

    def _toggle_todo(self, button, todo):
        if self.resetting_todo:
            return
        try:
            self.store.set_todo_completed(todo["id"], button.get_active())
            self._refresh_after_mutation(todo["task_id"])
        except Exception as error:
            self.resetting_todo = True
            try:
                button.set_active(bool(todo["completed"]))
            finally:
                self.resetting_todo = False
            error_dialog(self, "Todo 状态未保存", error)

    def edit_todo(self, todo_id):
        if self.active_form:
            return
        from .dialogs import TextDialog
        todo = self.store.get_todo(todo_id)
        def save(title):
            self.store.rename_todo(todo_id, title)
            self._refresh_after_mutation(todo["task_id"])
        self._present_form(TextDialog("编辑 Todo", "Todo 内容", todo["title"], save, multiline=False))

    def delete_todo(self, todo_id):
        if self.active_form:
            return
        todo = self.store.get_todo(todo_id)
        def remove():
            self.store.delete_todo(todo_id)
            self._refresh_after_mutation(todo["task_id"])
        return confirm(self, "删除这个 Todo？", f"“{todo['title']}”将从清单移除，之前的操作记录会保留。", "删除 Todo", lambda: self._run(remove), True)

    def _tick(self):
        if self.get_mapped():
            for row in self.task_rows:
                self._update_row_deadline(row)
            self._update_detail_deadline()
        return True

    def _tick_safely(self):
        self._run(self._tick)
        return True

    def toast(self, text):
        self.toast_overlay.add_toast(Adw.Toast(title=text, timeout=4))

    def _present_form(self, form):
        self.active_form = form
        form.connect("closed", lambda dialog: setattr(self, "active_form", None))
        form.present(self)

    def new_task(self):
        if self.active_form:
            return
        from .dialogs import TaskDialog
        def create(title, description, deadline):
            task_id = self.store.create_task(title, description, deadline)
            self.search.set_text("")
            self.task_filter.set_selected(0)
            self.refresh(task_id)
            self.split.set_show_content(True)
            self._write_state()
            self.toast("任务已创建")
        self._present_form(TaskDialog(None, create))

    def edit_task(self):
        if self.active_form or not self.selected_task:
            return
        from .dialogs import TaskDialog
        task_id = self.selected_id
        def update(title, description, deadline):
            changed = self.store.update_task(task_id, title, description, deadline)
            self.refresh(task_id)
            self.toast("任务已更新，修改已记录" if changed else "没有需要保存的修改")
        self._present_form(TaskDialog(self.selected_task, update))

    def change_status(self):
        if self.active_form or not self.selected_task:
            return
        from .dialogs import StatusDialog
        task_id = self.selected_id
        closing = self.selected_task["status"] == "open"
        if closing and text_of(self.progress_input).strip():
            error_dialog(self, "还有未提交的进展", "请先添加这条进展，或清空输入框，再闭环任务。草稿不会被静默丢弃。")
            return
        if closing and self.todo_input.get_text().strip():
            error_dialog(self, "还有未添加的 Todo", "请先添加这个 Todo，或清空输入框，再闭环任务。")
            return
        def change(note):
            if closing:
                self.store.close_task(task_id, note)
            else:
                self.store.reopen_task(task_id, note)
            self.loading_draft = True
            self.progress_input.get_buffer().set_text("")
            self.todo_input.set_text("")
            self.loading_draft = False
            self.drafts.pop(str(task_id), None)
            self.todo_drafts.pop(str(task_id), None)
            self.refresh(task_id)
            self._write_state()
            self.toast("任务已闭环，可在“全部”中查看" if closing else "任务已重新打开")
        done, total = self.store.todo_counts(task_id)
        self._present_form(StatusDialog(self.selected_task, closing, change, total - done))

    def save_progress(self):
        if not self.selected_task or self.active_form:
            return
        task_id = self.selected_id
        self.store.add_progress(task_id, text_of(self.progress_input))
        self.progress_input.get_buffer().set_text("")
        self.drafts.pop(str(task_id), None)
        self.refresh(task_id)
        self._write_state()
        self.progress_input.grab_focus()
        self.toast("进展已保存")

    def focus_search(self):
        if not self.active_form:
            self.split.set_show_content(False)
            self.search.grab_focus()

    def _file_dialog(self, title):
        dialog = Gtk.FileDialog(title=title, modal=True)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        file_filter = Gtk.FileFilter(name=f"{APP_NAME} 备份（JSON）")
        file_filter.add_pattern("*.json")
        filters.append(file_filter)
        all_files = Gtk.FileFilter(name="所有文件")
        all_files.add_pattern("*")
        filters.append(all_files)
        dialog.set_filters(filters)
        return dialog

    def export_backup(self):
        if self.active_form:
            return
        dialog = self._file_dialog("导出任务及完整历史")
        dialog.set_initial_name(f"{APP_NAME.lower()}-backup-{utc_now()[:10]}.json")
        dialog.save(self, None, self._export_selected)

    def _export_selected(self, dialog, result):
        try:
            file = dialog.save_finish(result)
            if not file.get_path():
                raise ValueError("请选择本地文件位置")
            self.store.export_backup(file.get_path())
            self.toast("备份已导出，包含任务、活动记录和 Todo（不含草稿）")
        except GLib.Error as error:
            if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                error_dialog(self, "导出失败", error)
        except Exception as error:
            error_dialog(self, "导出失败", error)

    def restore_backup(self):
        if self.active_form:
            return
        self._write_state()
        if any(value.strip() for value in (*self.drafts.values(), *self.todo_drafts.values())):
            error_dialog(self, "请先处理未提交的草稿", "恢复备份会替换任务。请先提交或清空各任务的进展和 Todo 草稿，避免草稿与恢复后的任务混淆。")
            return
        self._file_dialog(f"选择 {APP_NAME} 备份").open(self, None, self._restore_selected)

    def _restore_selected(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if not file.get_path():
                raise ValueError("请选择本地备份文件")
            data = Store.read_backup(file.get_path())
            confirm(self, "用备份替换当前任务？", f"备份包含 {len(data['tasks'])} 项任务、{len(data['events'])} 条活动记录、{len(data['todos'])} 项 Todo。\n\n恢复前会自动备份现有数据到数据目录的 backups 文件夹。此操作不是合并。", "恢复备份", lambda: self._run(lambda: self._restore(data)), True)
        except GLib.Error as error:
            if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                error_dialog(self, "恢复失败", error)
        except Exception as error:
            error_dialog(self, "无法读取备份", error)

    def _restore(self, data):
        backup_path = self.store.restore_backup(data)
        self.drafts.clear()
        self.todo_drafts.clear()
        self.selected_id = None
        self.selected_task = None
        self.search.set_text("")
        self.task_filter.set_selected(0)
        self.refresh()
        self._write_state()
        self.toast("备份已恢复，原数据已自动备份")
        return backup_path

    def open_data_folder(self):
        Gio.AppInfo.launch_default_for_uri(self.store.path.parent.as_uri(), None)

    def show_shortcuts(self):
        dialog = Adw.AlertDialog(heading="键盘快捷键", body="Ctrl+N　新建任务\nCtrl+F　搜索任务和进展\nCtrl+Enter　添加进展\nCtrl+Q　退出并保存草稿\n\n任务列表可使用方向键导航。\n截止时间可随时添加、修改或移除。")
        dialog.add_response("ok", "知道了")
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.present(self)

    def show_about(self):
        dialog = Adw.AboutDialog(application_name=APP_NAME, application_icon=APP_ID, version=VERSION, comments="简单、离线的任务与进展管理。\n每一步都有时间，每件事都能闭环。", developer_name=APP_NAME)
        dialog.present(self)

    def _close_requested(self, window):
        if self.active_form:
            self.active_form.close()
            return True
        if self.draft_timer:
            GLib.source_remove(self.draft_timer)
            self.draft_timer = 0
        if not self.discard_on_exit and not self._write_state():
            def discard():
                self.discard_on_exit = True
                self.close()
            confirm(self, "仍要退出？", "部分草稿未能写入磁盘。你可以取消退出并提交进展，或继续退出并放弃未保存的草稿。", "仍然退出", discard, True)
            return True
        if self.refresh_timer:
            GLib.source_remove(self.refresh_timer)
            self.refresh_timer = 0
        return False
