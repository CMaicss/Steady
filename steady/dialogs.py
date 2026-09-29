from datetime import datetime

from gi.repository import Adw, GLib, Gtk

from .i18n import _, LANGUAGES, read_language
from .timeutils import deadline_status, local_deadline, parse_timestamp
from .widgets import confirm, label, text_editor, text_of


class FormDialog(Adw.Dialog):
    def __init__(self, title, action):
        super().__init__(title=title, content_width=500, content_height=560, can_close=False)
        toolbar = Adw.ToolbarView()
        header = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        cancel = Gtk.Button(label=_("Cancel"))
        cancel.connect("clicked", lambda button: self._request_close())
        header.pack_start(cancel)
        self.save_button = Gtk.Button(label=action)
        self.save_button.add_css_class("suggested-action")
        self.save_button.connect("clicked", lambda button: self._save())
        header.pack_end(self.save_button)
        toolbar.add_top_bar(header)
        self.form = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20, margin_top=20, margin_bottom=24, margin_start=24, margin_end=24)
        self.error_label = label(style="error")
        self.error_label.set_focusable(True)
        self.error_label.set_visible(False)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.form)
        toolbar.set_content(scroll)
        self.set_child(toolbar)
        self.set_default_widget(self.save_button)
        self.connect("close-attempt", lambda dialog: self._request_close())

    def _request_close(self):
        if self._signature() != self.initial:
            confirm(self, _("Discard unsaved changes?"), _("Your changes will not be saved if you close this dialog."), _("Discard Changes"), self.force_close, True)
        else:
            self.force_close()

    def _failed(self, error):
        self.error_label.set_text(str(error))
        self.error_label.set_visible(True)
        self.error_label.grab_focus()
        self.save_button.set_sensitive(True)


class LanguageDialog(FormDialog):
    def __init__(self, directory, callback):
        super().__init__(_("Language"), _("Save"))
        self.set_content_height(320)
        self.callback = callback
        self.languages = ["auto", *dict(LANGUAGES)]
        group = Adw.PreferencesGroup(description=_("The language will change on the next launch. Task content is not translated, and unsubmitted drafts are kept."))
        self.language_row = Adw.ComboRow(title=_("Interface Language"), model=Gtk.StringList.new([_("System Default"), *(name for code, name in LANGUAGES)]))
        self.language_row.set_selected(self.languages.index(read_language(directory)))
        group.add(self.language_row)
        self.form.append(group)
        self.form.append(self.error_label)
        self.initial = self._signature()

    def _signature(self):
        return self.languages[self.language_row.get_selected()]

    def _save(self):
        self.save_button.set_sensitive(False)
        try:
            self.callback(self._signature())
        except Exception as error:
            self._failed(error)
            return
        self.force_close()


class TaskDialog(FormDialog):
    def __init__(self, task, callback):
        super().__init__(_("Edit Task") if task else _("New Task"), _("Save") if task else _("Create Task"))
        self.callback = callback
        group = Adw.PreferencesGroup(title=_("Task Information"))
        self.title_row = Adw.EntryRow(title=_("Task Title · Required"), text=task["title"] if task else "")
        group.add(self.title_row)
        self.form.append(group)
        description_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        description_box.append(label(_("Task Description · Optional"), "heading"))
        self.description, frame = text_editor(90, _("Task description, optional"))
        self.description.get_buffer().set_text(task["description"] if task else "")
        description_box.append(frame)
        self.form.append(description_box)
        deadline_group = Adw.PreferencesGroup(title=_("Deadline"), description=_("Tasks can be created and worked on without a deadline."))
        self.deadline_switch = Adw.SwitchRow(title=_("Set a Deadline"), subtitle=_("Optional; tasks without a deadline have no countdown"))
        deadline_group.add(self.deadline_switch)
        self.form.append(deadline_group)
        self.deadline_fields = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        date_group = Adw.PreferencesGroup()
        self.date_row = Adw.EntryRow(title=_("Date · YYYY-MM-DD"))
        self.date_row.set_input_purpose(Gtk.InputPurpose.FREE_FORM)
        date_group.add(self.date_row)
        calendar_button = Gtk.MenuButton(icon_name="x-office-calendar-symbolic", tooltip_text=_("Choose a date"), valign=Gtk.Align.CENTER)
        popover = Gtk.Popover()
        self.calendar = Gtk.Calendar()
        popover.set_child(self.calendar)
        calendar_button.set_popover(popover)
        self.date_row.add_suffix(calendar_button)
        self.deadline_fields.append(date_group)
        time_box = Gtk.Box(spacing=10)
        time_box.append(label(_("Time"), "heading"))
        self.hour = Gtk.SpinButton.new_with_range(0, 23, 1)
        self.minute = Gtk.SpinButton.new_with_range(0, 59, 1)
        self.hour.set_numeric(True)
        self.minute.set_numeric(True)
        self.hour.set_wrap(True)
        self.minute.set_wrap(True)
        self.hour.update_property([Gtk.AccessibleProperty.LABEL], [_("Deadline: hour")])
        self.minute.update_property([Gtk.AccessibleProperty.LABEL], [_("Deadline: minute")])
        time_box.append(self.hour)
        time_box.append(Gtk.Label(label=":"))
        time_box.append(self.minute)
        self.deadline_fields.append(time_box)
        self.deadline_hint = label(style="dim-label")
        self.deadline_fields.append(self.deadline_hint)
        self.form.append(self.deadline_fields)
        self.form.append(self.error_label)
        due = parse_timestamp(task["deadline"]).astimezone() if task and task["deadline"] else datetime.now().replace(hour=23, minute=59)
        self.date_row.set_text(due.strftime("%Y-%m-%d"))
        self.hour.set_value(due.hour)
        self.minute.set_value(due.minute)
        self.calendar.select_day(GLib.DateTime.new_local(due.year, due.month, due.day, 12, 0, 0))
        enabled = bool(task and task["deadline"])
        self.deadline_switch.set_active(enabled)
        self.deadline_fields.set_visible(enabled)
        self.set_content_height(700 if enabled else 560)
        self.deadline_switch.connect("notify::active", self._deadline_toggled)
        self.calendar.connect("day-selected", self._calendar_selected, popover)
        self.date_row.connect("changed", lambda entry: self._update_hint())
        self.hour.connect("value-changed", lambda spin: self._update_hint())
        self.minute.connect("value-changed", lambda spin: self._update_hint())
        self.initial = self._signature()
        self._update_hint()
        self.original_deadline = task["deadline"] if task else None
        self.initial_deadline_fields = self._signature()[3:]
        self.set_focus(self.title_row)

    def _signature(self):
        return (self.title_row.get_text(), text_of(self.description), self.deadline_switch.get_active(), self.date_row.get_text(), self.hour.get_value_as_int(), self.minute.get_value_as_int())

    def _deadline_toggled(self, switch, parameter):
        self.deadline_fields.set_visible(switch.get_active())
        self.set_content_height(700 if switch.get_active() else 560)
        self._update_hint()

    def _calendar_selected(self, calendar, popover):
        self.date_row.set_text(calendar.get_date().format("%Y-%m-%d"))
        popover.popdown()

    def _deadline(self):
        if not self.deadline_switch.get_active():
            return None
        if getattr(self, "original_deadline", None) and self._signature()[3:] == self.initial_deadline_fields:
            return self.original_deadline
        return local_deadline(self.date_row.get_text(), self.hour.get_value_as_int(), self.minute.get_value_as_int())

    def _update_hint(self):
        try:
            due = self._deadline()
            text, style = deadline_status(due)
            self.deadline_hint.set_text(text if style == "error" else _("Local time, defaulting to 23:59. You can change or remove the deadline at any time."))
        except ValueError:
            self.deadline_hint.set_text(_("Example date: 2026-09-29"))

    def _save(self):
        self.save_button.set_sensitive(False)
        try:
            self.hour.update()
            self.minute.update()
            self.callback(self.title_row.get_text(), text_of(self.description), self._deadline())
        except Exception as error:
            self._failed(error)
            return
        self.force_close()


class TextDialog(FormDialog):
    def __init__(self, title, field, initial, callback, multiline=True, explanation=""):
        super().__init__(title, _("Save"))
        self.set_content_height(430 if multiline else 250)
        self.callback = callback
        self.multiline = multiline
        if explanation:
            self.form.append(label(explanation, "dim-label"))
        self.form.append(label(field, "heading"))
        if multiline:
            self.input, frame = text_editor(180, field)
            self.input.get_buffer().set_text(initial)
            self.form.append(frame)
        else:
            self.input = Gtk.Entry(text=initial, max_length=500, activates_default=True)
            self.form.append(self.input)
        self.form.append(self.error_label)
        self.initial = self._signature()
        self.set_focus(self.input)

    def _signature(self):
        return text_of(self.input) if self.multiline else self.input.get_text()

    def _save(self):
        self.save_button.set_sensitive(False)
        try:
            self.callback(self._signature())
        except Exception as error:
            self._failed(error)
            return
        self.force_close()


class StatusDialog(FormDialog):
    def __init__(self, task, closing, callback, pending_todos=0):
        super().__init__(_("Close Task") if closing else _("Reopen Task"), _("Confirm Closure") if closing else _("Reopen"))
        self.set_content_height(390)
        self.callback = callback
        self.form.append(label(task["title"], "title-3"))
        self.form.append(label(_("The task will leave the open list. All history will be kept.") if closing else _("The task will return to the open list. Previous closure records will be kept."), "dim-label"))
        if closing and pending_todos:
            self.form.append(label(_("Unfinished Todo items: {pending_todos}. You can still close this task; these items will not be checked automatically.").format(pending_todos=pending_todos), "warning"))
        self.form.append(label(_("Closure Note · Optional") if closing else _("Reopening Note · Optional"), "heading"))
        self.note, frame = text_editor(110, _("Closure note") if closing else _("Reopening note"))
        self.form.append(frame)
        self.form.append(self.error_label)
        self.initial = self._signature()
        self.set_focus(self.note)

    def _signature(self):
        return text_of(self.note)

    def _save(self):
        self.save_button.set_sensitive(False)
        try:
            self.callback(text_of(self.note))
        except Exception as error:
            self._failed(error)
            return
        self.force_close()
