import json
from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from . import APP_ID, APP_NAME, VERSION
from .i18n import _, N_, pgettext, translated_template
from .storage import Store, atomic_json
from .timeutils import deadline_status, format_timestamp, utc_now
from .widgets import clear_box, confirm, error_dialog, label, text_of

EVENT_LABELS = {"created": N_("Create Task"), "updated": N_("Task Updated"), "progress": N_("Progress Added"), "closed": N_("Task Closed"), "reopened": N_("Reopen"), "todo": N_("Todo Actions")}
FIELD_LABELS = {"title": N_("Task title"), "description": N_("Task description"), "deadline": N_("Deadline")}
TASK_FILTERS = ((N_("Open"), "open"), (N_("Closed"), "closed"), (N_("All"), None))
HISTORY_FILTERS = ((N_("All Types"), None), (N_("Progress Only"), ("progress",)), (N_("Task Updates"), ("updated",)), (N_("Create Task"), ("created",)), (N_("Close / Reopen"), ("closed", "reopened")), (N_("Todo Actions"), ("todo",)))
TODO_LABELS = {"added": N_("Add Todo"), "renamed": N_("Edit Todo"), "completed": N_("Complete Todo"), "unchecked": N_("Uncheck Todo"), "deleted": N_("Delete Todo")}


@Gtk.Template(string=translated_template(Path(__file__).with_name("window.ui")))
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
            "language": self.show_language,
            "quit": self.close,
        }.items():
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda action, parameter, callback=callback: self._run(callback))
            self.add_action(action)
            self.actions[name] = action
        task_menu = Gio.Menu()
        task_menu.append(_("Edit Task…"), "win.edit-task")
        task_menu.append(_("Delete Task…"), "win.delete-task")
        self.edit_button.set_menu_model(task_menu)
        self.history_filter.set_model(Gtk.StringList.new([_(item[0]) for item in HISTORY_FILTERS]))
        selected_filter = self.state.get("history_filter", 0)
        self.history_filter.set_selected(selected_filter if type(selected_filter) is int and 0 <= selected_filter < len(HISTORY_FILTERS) else 0)
        self.history_filter.connect("notify::selected", self._history_filter_changed)
        menu = Gio.Menu()
        files = Gio.Menu()
        files.append(_("Export Backup…"), "win.export")
        files.append(_("Restore Backup…"), "win.restore")
        files.append(_("Open Data Folder"), "win.data-folder")
        menu.append_section(None, files)
        help_menu = Gio.Menu()
        help_menu.append(_("Language…"), "win.language")
        help_menu.append(_("Keyboard Shortcuts"), "win.shortcuts")
        help_menu.append(_("About {app_name}").format(app_name=APP_NAME), "win.about")
        help_menu.append(_("Quit"), "win.quit")
        menu.append_section(None, help_menu)
        self.menu_button.set_menu_model(menu)
        self.task_filter.set_model(Gtk.StringList.new([_(item[0]) for item in TASK_FILTERS]))
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
            error_dialog(self, _("Action could not be completed"), error)
            return None

    def _read_state(self):
        if not self.state_path.exists():
            return {}
        try:
            with self.state_path.open(encoding="utf-8") as stream:
                data = json.load(stream)
            if not isinstance(data, dict) or not isinstance(data.get("drafts", {}), dict):
                raise ValueError(_("Invalid settings format"))
            drafts = data.get("drafts", {})
            if any(not key.isdigit() or not isinstance(value, str) or len(value) > 50000 for key, value in drafts.items()):
                raise ValueError(_("Invalid draft format"))
            todo_drafts = data.get("todo_drafts", {})
            if not isinstance(todo_drafts, dict) or any(not key.isdigit() or not isinstance(value, str) or len(value) > 500 for key, value in todo_drafts.items()):
                raise ValueError(_("Invalid Todo draft format"))
            for key, default, minimum, maximum in (("width", 1080, 360, 3840), ("height", 760, 480, 2160)):
                value = data.get(key, default)
                data[key] = min(maximum, max(minimum, value)) if type(value) is int else default
            return data
        except (ValueError, OSError) as error:
            backup = self.state_path.with_name(f"state-unreadable-{utc_now().replace(':', '-')}.json")
            try:
                self.state_path.rename(backup)
            except OSError:
                raise OSError(_("Could not read or protect settings file: {state_path}; {error}").format(state_path=self.state_path, error=error)) from error
            GLib.idle_add(lambda: (self.toast(_("Could not read settings. The original file was kept in the data directory.")), False)[1])
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
                self.draft_hint.set_text(_("Draft saved locally · Ctrl+Enter to submit"))
            return True
        except OSError as error:
            self.draft_hint.set_text(_("Draft could not be saved. Do not quit"))
            if not self.state_error_shown:
                self.state_error_shown = True
                error_dialog(self, _("Draft / window state not saved"), error)
            return False

    def _draft_changed(self, buffer):
        if self.loading_draft:
            return
        content = text_of(self.progress_input)
        if len(content) > 50000:
            self.draft_hint.set_text(_("Over the 50,000-character limit. Shorten the text before saving"))
            self.actions["save-progress"].set_enabled(False)
            return
        self.actions["save-progress"].set_enabled(bool(content.strip()) and bool(self.selected_task) and self.selected_task["status"] == "open")
        self.actions["add-todo"].set_enabled(bool(self.todo_input.get_text().strip()) and bool(self.selected_task) and self.selected_task["status"] == "open")
        self.draft_hint.set_text(_("Saving draft…") if content else _("Ctrl+Enter to save progress"))
        if self.draft_timer:
            GLib.source_remove(self.draft_timer)
        self.draft_timer = GLib.timeout_add(450, self._save_draft_later)

    def _check_draft_length(self, buffer, location, text, length):
        if not self.loading_draft and buffer.get_char_count() + len(text) > 50000:
            buffer.stop_emission_by_name("insert-text")
            self.toast(_("A progress note can contain up to 50,000 characters. Split it into several notes"))

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
                title, description = _("No tasks found"), _("Try other keywords or change the task status filter.")
            elif total == 0:
                title, description = _("No tasks yet"), _("Click + to create a task. The deadline can be left empty.")
            elif status == "closed":
                title, description = _("No closed tasks"), _("Closed tasks will appear here.")
            else:
                title, description = _("No open tasks"), _("All tasks are closed. Find them under “Closed” or “All”.")
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
        preview = task["latest_progress"] or task["description"] or _("No progress yet. Record your next step.")
        preview_label = label(" ".join(preview.split()), "dim-label")
        preview_label.set_lines(2)
        preview_label.set_ellipsize(Pango.EllipsizeMode.END)
        box.append(preview_label)
        footer = Gtk.Box(spacing=12)
        details = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, valign=Gtk.Align.END)
        row.deadline_label = label(style="deadline-badge")
        details.append(row.deadline_label)
        footer.append(details)
        closed = task["status"] == "closed"
        prefix = _("Closed at") if closed else _("Updated at")
        timestamp = task["closed_at"] if closed else task["updated_at"]
        separator = "\n" if task["deadline"] or closed else " "
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
            text = _("Closed") + (" · " + text if text else "")
        row.deadline_label.set_text(text)
        row.deadline_label.set_visible(bool(text))
        self._status_style(row.deadline_label, style)
        row.deadline_label.set_tooltip_text(_("Due ") + format_timestamp(task["deadline"]) if task["deadline"] else _("No deadline"))

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
        self.draft_hint.set_text(_("Local draft restored · Ctrl+Enter to submit") if content else _("Ctrl+Enter to save progress"))
        if not task:
            return
        self.task_title.set_text(task["title"])
        self.task_description.set_text(task["description"])
        self.task_description.set_visible(bool(task["description"]))
        self.task_status.set_text(pgettext("task-status", "Open") if opened else pgettext("task-status", "Closed"))
        self._status_style(self.task_status, "" if opened else "success")
        self.task_meta.set_text(_("Created {created_at} · Last updated {updated_at}").format(created_at=format_timestamp(task['created_at'], True), updated_at=format_timestamp(task['updated_at'], True)) + (_("\nClosed {closed_at}").format(closed_at=format_timestamp(task['closed_at'], True)) if task["closed_at"] else ""))
        self.status_button.set_label(_("Close Task") if opened else _("Reopen"))
        self._update_detail_deadline()
        self._render_todos()
        self._render_history()
        self.detail_scroll.get_vadjustment().set_value(0)

    def _update_detail_deadline(self):
        task = self.selected_task
        if not task:
            return
        if not task["deadline"]:
            self.task_deadline.set_text(_("No deadline"))
            self._status_style(self.task_deadline, "")
            self.task_deadline.add_css_class("dim-label")
        else:
            text, style = deadline_status(task["deadline"], task["closed_at"])
            self.task_deadline.set_text(_("Due {deadline} · {text}").format(deadline=format_timestamp(task['deadline']), text=text))
            self.task_deadline.remove_css_class("dim-label")
            self._status_style(self.task_deadline, style)

    def _render_history(self):
        clear_box(self.history_list)
        kinds = HISTORY_FILTERS[self.history_filter.get_selected()][1]
        count = self.store.event_count(self.selected_id, kinds)
        total = self.store.event_count(self.selected_id)
        self.history_heading.set_text(_("Activity · {count}").format(count=count) + (f" / {total}" if kinds is not None else ""))
        self.history_empty.set_visible(count == 0)
        empty_text = _("No activity yet. Add a progress note.") if self.selected_task["status"] == "open" else _("No activity yet. Reopen the task to add progress.")
        self.history_empty.set_text(empty_text if kinds is None else _("No activity matches this filter."))
        self.history_list.set_visible(count > 0)
        self.more_events.set_visible(count > self.event_limit)
        for event in self.store.events(self.selected_id, self.event_limit, kinds):
            row = Gtk.ListBoxRow(activatable=False, selectable=False)
            row.event_id = event["id"]
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
            box.add_css_class("event-row")
            header = Gtk.Box(spacing=4)
            heading = label(_(EVENT_LABELS[event["kind"]]), "heading")
            heading.set_hexpand(True)
            header.append(heading)
            row.edit_button = self._icon_button("document-edit-symbolic", _("Edit Activity"), lambda event_id=event["id"]: self.edit_event(event_id))
            row.delete_button = self._icon_button("user-trash-symbolic", _("Delete Activity Record"), lambda event_id=event["id"]: self.delete_event(event_id))
            header.append(row.edit_button)
            header.append(row.delete_button)
            box.append(header)
            stamp_text = format_timestamp(event["created_at"], True)
            if event["edited_at"]:
                stamp_text += "\n" + _("Edited {time}").format(time=format_timestamp(event["edited_at"], True))
            stamp = label(stamp_text, "dim-label", selectable=True)
            stamp.add_css_class("caption")
            box.append(stamp)
            body = self._event_text(event)
            if body:
                box.append(label(body, selectable=True))
            if event["edited_at"] and event["kind"] in ("created", "updated", "todo"):
                original = Gtk.Expander(label=_("Original Action Details"))
                original.set_child(label(self._original_event_text(event), "dim-label", selectable=True))
                box.append(original)
            row.set_child(box)
            self.history_list.append(row)

    @staticmethod
    def _original_event_text(event):
        changes = event["changes"]
        if event["kind"] == "created":
            return "\n".join(value for value in (changes["title"], changes["description"], _("Deadline: {time}").format(time=format_timestamp(changes["deadline"]))) if value)
        if event["kind"] == "updated":
            lines = []
            for key, change in changes.items():
                before, after = change["before"], change["after"]
                if key == "deadline":
                    before, after = format_timestamp(before), format_timestamp(after)
                lines.append(_("{field}\nBefore: {before}\nAfter: {after}").format(field=_(FIELD_LABELS[key]), before=before or _("(empty)"), after=after or _("(empty)")))
            return "\n\n".join(lines)
        if event["kind"] == "todo":
            text = _("{action}: {title}").format(action=_(TODO_LABELS[changes["action"]]), title=changes["title"])
            return text + ("\n" + _("Original text: {before}").format(before=changes["before"]) if "before" in changes else "")
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
            self.toast(_("Activity updated") if changed else _("No changes to save"))
        self._present_form(TextDialog(_("Edit Activity"), _(EVENT_LABELS[event["kind"]]), initial, save, explanation=_("Only the displayed text of this record is changed, not the current task or Todo state. The original time is kept and the edit time is recorded.")))

    def delete_event(self, event_id):
        if self.active_form:
            return
        event = self.store.get_event(event_id)
        def remove():
            self.store.delete_event(event_id)
            self._refresh_after_mutation(event["task_id"])
            self.toast(_("Activity deleted; the task's current state is unchanged"))
        return confirm(self, _("Delete this activity?"), _("{kind} · {created_at}\n\nOnly this record will be deleted; the current task or Todo state will not be reverted. This cannot be undone. You can export a backup first.").format(kind=_(EVENT_LABELS[event['kind']]), created_at=format_timestamp(event['created_at'], True)), _("Delete Activity"), lambda: self._run(remove), True)

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
            self.toast(_("Task, activity history and Todo items deleted"))
        return confirm(self, _("Delete task?"), _("All activity, Todo items and unsubmitted drafts for “{title}” will also be deleted.\n\nThis cannot be undone. Close the task instead to keep its history, or export a backup first.").format(title=task['title']), _("Delete Task"), lambda: self._run(remove), True)

    def _render_todos(self):
        done, total = self.store.todo_counts(self.selected_id)
        self.todo_expander.set_label(_("Todo List · {done} / {total}").format(done=done, total=total) if total else _("Todo List · Optional"))
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
                stamp = label(_("Completed {time}").format(time=format_timestamp(todo["completed_at"])), "dim-label")
                stamp.add_css_class("caption")
                texts.append(stamp)
            row.check_button.set_child(texts)
            row.edit_button = self._icon_button("document-edit-symbolic", _("Edit Todo"), lambda todo_id=todo["id"]: self.edit_todo(todo_id))
            row.delete_button = self._icon_button("user-trash-symbolic", _("Delete Todo"), lambda todo_id=todo["id"]: self.delete_todo(todo_id))
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
            error_dialog(self, _("Todo state not saved"), error)

    def edit_todo(self, todo_id):
        if self.active_form:
            return
        from .dialogs import TextDialog
        todo = self.store.get_todo(todo_id)
        def save(title):
            self.store.rename_todo(todo_id, title)
            self._refresh_after_mutation(todo["task_id"])
        self._present_form(TextDialog(_("Edit Todo"), _("Todo text"), todo["title"], save, multiline=False))

    def delete_todo(self, todo_id):
        if self.active_form:
            return
        todo = self.store.get_todo(todo_id)
        def remove():
            self.store.delete_todo(todo_id)
            self._refresh_after_mutation(todo["task_id"])
        return confirm(self, _("Delete this Todo?"), _("“{title}” will be removed from the list. Previous activity records will be kept.").format(title=todo['title']), _("Delete Todo"), lambda: self._run(remove), True)

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
            self.toast(_("Task created"))
        self._present_form(TaskDialog(None, create))

    def edit_task(self):
        if self.active_form or not self.selected_task:
            return
        from .dialogs import TaskDialog
        task_id = self.selected_id
        def update(title, description, deadline):
            changed = self.store.update_task(task_id, title, description, deadline)
            self.refresh(task_id)
            self.toast(_("Task updated and changes recorded") if changed else _("No changes to save"))
        self._present_form(TaskDialog(self.selected_task, update))

    def change_status(self):
        if self.active_form or not self.selected_task:
            return
        from .dialogs import StatusDialog
        task_id = self.selected_id
        closing = self.selected_task["status"] == "open"
        if closing and text_of(self.progress_input).strip():
            error_dialog(self, _("Unsubmitted progress"), _("Add this progress note or clear the input before closing the task. Drafts will not be silently discarded."))
            return
        if closing and self.todo_input.get_text().strip():
            error_dialog(self, _("Unsubmitted Todo"), _("Add this Todo or clear the input before closing the task."))
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
            self.toast(_("Task closed. Find it under “All”") if closing else _("Task reopened"))
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
        self.toast(_("Progress saved"))

    def focus_search(self):
        if not self.active_form:
            self.split.set_show_content(False)
            self.search.grab_focus()

    def _file_dialog(self, title):
        dialog = Gtk.FileDialog(title=title, modal=True)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        file_filter = Gtk.FileFilter(name=_("{app_name} Backup (JSON)").format(app_name=APP_NAME))
        file_filter.add_pattern("*.json")
        filters.append(file_filter)
        all_files = Gtk.FileFilter(name=_("All Files"))
        all_files.add_pattern("*")
        filters.append(all_files)
        dialog.set_filters(filters)
        return dialog

    def export_backup(self):
        if self.active_form:
            return
        dialog = self._file_dialog(_("Export tasks and full history"))
        dialog.set_initial_name(f"{APP_NAME.lower()}-backup-{utc_now()[:10]}.json")
        dialog.save(self, None, self._export_selected)

    def _export_selected(self, dialog, result):
        try:
            file = dialog.save_finish(result)
            if not file.get_path():
                raise ValueError(_("Choose a local file location"))
            self.store.export_backup(file.get_path())
            self.toast(_("Backup exported with tasks, activity and Todo items (excluding drafts)"))
        except GLib.Error as error:
            if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                error_dialog(self, _("Export failed"), error)
        except Exception as error:
            error_dialog(self, _("Export failed"), error)

    def restore_backup(self):
        if self.active_form:
            return
        self._write_state()
        if any(value.strip() for value in (*self.drafts.values(), *self.todo_drafts.values())):
            error_dialog(self, _("Handle unsubmitted drafts first"), _("Restoring a backup replaces your tasks. Submit or clear progress and Todo drafts for all tasks first, so they are not associated with the wrong tasks."))
            return
        self._file_dialog(_("Select a {app_name} backup").format(app_name=APP_NAME)).open(self, None, self._restore_selected)

    def _restore_selected(self, dialog, result):
        try:
            file = dialog.open_finish(result)
            if not file.get_path():
                raise ValueError(_("Choose a local backup file"))
            data = Store.read_backup(file.get_path())
            confirm(self, _("Replace current tasks with this backup?"), _("Backup contents — Tasks: {tasks_count}; activity records: {events_count}; Todo items: {todos_count}.\n\nExisting data will be saved to the backups folder in the data directory before restoring. This replaces data; it does not merge it.").format(tasks_count=len(data['tasks']), events_count=len(data['events']), todos_count=len(data['todos'])), _("Restore Backup"), lambda: self._run(lambda: self._restore(data)), True)
        except GLib.Error as error:
            if not error.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                error_dialog(self, _("Restore failed"), error)
        except Exception as error:
            error_dialog(self, _("Could not read backup"), error)

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
        self.toast(_("Backup restored; previous data was backed up automatically"))
        return backup_path

    def open_data_folder(self):
        Gio.AppInfo.launch_default_for_uri(self.store.path.parent.as_uri(), None)

    def show_shortcuts(self):
        dialog = Adw.AlertDialog(heading=_("Keyboard Shortcuts"), body=_("Ctrl+N  New task\nCtrl+F  Search tasks and progress\nCtrl+Enter  Add progress\nCtrl+Q  Quit and save drafts\n\nUse the arrow keys to navigate the task list.\nDeadlines can be added, changed or removed at any time."))
        dialog.add_response("ok", _("OK"))
        dialog.set_default_response("ok")
        dialog.set_close_response("ok")
        dialog.present(self)

    def show_about(self):
        dialog = Adw.AboutDialog(application_name=APP_NAME, application_icon=APP_ID, version=VERSION, comments=_("Simple, offline task and progress management.\nEvery step recorded, every task brought to a close."), developer_name=APP_NAME)
        dialog.present(self)

    def show_language(self):
        if self.active_form:
            return
        from .dialogs import LanguageDialog
        def save(language):
            atomic_json(self.store.path.parent / "language.json", {"language": language})
            self.toast(_("Language saved. Quit normally and reopen Steady to apply it; your drafts will be kept."))
        self._present_form(LanguageDialog(self.store.path.parent, save))

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
            confirm(self, _("Quit anyway?"), _("Some drafts could not be written to disk. Cancel and submit your progress, or quit and discard unsaved drafts."), _("Quit Anyway"), discard, True)
            return True
        if self.refresh_timer:
            GLib.source_remove(self.refresh_timer)
            self.refresh_timer = 0
        return False
