#!/usr/bin/python3
import argparse
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steady.application import Application
from steady import APP_NAME, VERSION
from steady.window import MainWindow
from gi.repository import Adw, GLib, Gtk


def settle(seconds=0.3):
    deadline = time.monotonic() + seconds
    context = GLib.MainContext.default()
    while time.monotonic() < deadline:
        while context.pending():
            context.iteration(False)
        time.sleep(0.005)


def screenshot(window, path):
    import gi
    gi.require_version("Gsk", "4.0")
    gi.require_version("Graphene", "1.0")
    from gi.repository import Graphene
    settle(0.3)
    paintable = Gtk.WidgetPaintable.new(window)
    snapshot = Gtk.Snapshot()
    paintable.snapshot(snapshot, window.get_width(), window.get_height())
    node = snapshot.to_node()
    bounds = Graphene.Rect()
    bounds.init(0, 0, window.get_width(), window.get_height())
    texture = window.get_renderer().render_texture(node, bounds)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not texture.save_to_png(str(path)):
        raise RuntimeError("无法保存窗口截图")


def respond(dialog, response):
    dialog.emit("response", response)
    dialog.force_close()
    settle()


def check_status_row(window):
    settle()
    row = window.task_status.get_parent()
    assert window.status_button.get_parent() is row
    assert row.get_orientation() == Gtk.Orientation.HORIZONTAL
    assert row.get_next_sibling() is window.task_title
    assert window.status_button.get_ancestor(Adw.HeaderBar) is None
    status = window.task_status.get_allocation()
    button = window.status_button.get_allocation()
    assert status.x + status.width <= button.x
    assert abs(status.y + status.height / 2 - button.y - button.height / 2) <= 1
    assert button.width > 0 and button.x + button.width <= row.get_width()


def check_task_filter(window):
    settle()
    assert isinstance(window.task_filter, Gtk.DropDown)
    row = window.search.get_parent()
    assert window.task_filter.get_parent() is row
    assert row.get_orientation() == Gtk.Orientation.HORIZONTAL
    assert window.search.get_next_sibling() is window.task_filter
    assert row.get_next_sibling() is window.list_stack
    assert window.task_filter.get_halign() == Gtk.Align.END
    assert window.search.get_hexpand()
    model = window.task_filter.get_model()
    assert [model.get_string(index) for index in range(model.get_n_items())] == ["未闭环", "已闭环", "全部"]
    assert 0 < window.task_filter.get_width() < window.search.get_width()


def check_management(window, store, output):
    task_id = window.selected_id
    assert store.todo_counts(task_id) == (0, 0)
    assert not window.todo_expander.get_expanded()
    window.todo_expander.set_expanded(True)
    for title in ("补充部署文档", "请团队复核内容"):
        window.todo_input.set_text(title)
        window.actions["add-todo"].activate(None)
        settle()
    assert store.todo_counts(task_id) == (0, 2)
    row = window.todo_list.get_first_child()
    todo_id = row.todo["id"]
    row.check_button.set_active(True)
    settle()
    assert store.todo_counts(task_id) == (1, 2)
    assert store.get_todo(todo_id)["completed_at"]
    window.todo_list.get_first_child().check_button.set_active(False)
    settle()
    assert store.get_todo(todo_id)["completed_at"] is None
    window.todo_list.get_first_child().edit_button.emit("clicked")
    settle()
    window.active_form.input.set_text("补充部署与回滚说明")
    window.active_form.save_button.emit("clicked")
    settle()
    assert store.get_todo(todo_id)["title"] == "补充部署与回滚说明"
    window.todo_list.get_first_child().check_button.set_active(True)
    settle()
    assert store.get_task(task_id)["status"] == "open"
    assert next(row.task for row in window.task_rows if row.task["id"] == task_id)["todo_done"] == 1
    removable = store.add_todo(task_id, "待删除的小步骤")
    window.refresh(task_id)
    respond(window.delete_todo(removable), "cancel")
    assert store.get_todo(removable)
    respond(window.delete_todo(removable), "confirm")
    assert store.todo_counts(task_id) == (1, 2)
    window.history_filter.set_selected(1)
    settle()
    event = store.events(task_id, kinds=("progress",))[0]
    row = window.history_list.get_first_child()
    assert row.event_id == event["id"]
    row.edit_button.emit("clicked")
    settle()
    window.active_form.input.get_buffer().set_text("已整理目录结构，并补充部署说明（修订）。")
    window.active_form.save_button.emit("clicked")
    settle()
    revised = store.get_event(event["id"])
    assert revised["edited_at"] and revised["created_at"] == event["created_at"]
    if output:
        settle(4.2)
        screenshot(window, output / "todos-and-progress.png")
        window.todo_expander.set_expanded(False)
        screenshot(window, output / "progress-filter.png")
        window.todo_expander.set_expanded(True)
    respond(window.delete_event(event["id"]), "cancel")
    assert store.get_event(event["id"])
    respond(window.delete_event(event["id"]), "confirm")
    assert window.history_empty.get_visible()
    assert store.event_count(task_id, ("progress",)) == 0
    window.progress_input.get_buffer().set_text("已完成部署说明，等待团队复核。")
    window.actions["save-progress"].activate(None)
    settle()
    assert not window.history_empty.get_visible()
    window.history_filter.set_selected(5)
    settle()
    assert store.get_event(window.history_list.get_first_child().event_id)["kind"] == "todo"
    if output:
        screenshot(window, output / "todo-activity-filter.png")
    window.history_filter.set_selected(0)
    window.change_status()
    window.active_form.save_button.emit("clicked")
    settle()
    window.task_filter.set_selected(2)
    window.refresh(task_id)
    assert not window.todo_list.get_first_child().check_button.get_sensitive()
    original_closed_at = store.get_task(task_id)["closed_at"]
    close_event = store.events(task_id, kinds=("closed",))[0]
    window.edit_event(close_event["id"])
    window.active_form.input.get_buffer().set_text("闭环说明修订，不改变任务状态")
    window.active_form.save_button.emit("clicked")
    settle()
    assert store.get_task(task_id)["closed_at"] == original_closed_at
    respond(window.delete_event(close_event["id"]), "confirm")
    assert store.get_task(task_id)["status"] == "closed"
    window.change_status()
    window.active_form.save_button.emit("clicked")
    settle()
    window.task_filter.set_selected(0)
    doomed = store.create_task("删除功能检查")
    store.add_todo(doomed, "一并删除")
    window.refresh(doomed)
    window.progress_input.get_buffer().set_text("删除时要清理的进展草稿")
    window.todo_input.set_text("删除时要清理的 Todo 草稿")
    window._write_state()
    respond(window.delete_task(), "cancel")
    assert store.get_task(doomed)
    assert str(doomed) in window.drafts
    respond(window.delete_task(), "confirm")
    assert store.events(doomed) == [] and store.todos(doomed) == []
    assert str(doomed) not in window.drafts and str(doomed) not in window.todo_drafts
    assert window.selected_id != doomed
    window.refresh(task_id)
    data = store.export_data()
    window._restore(data)
    window.refresh(task_id)
    assert store.todo_counts(task_id) == (1, 2)
    assert any(event["edited_at"] for event in store.events(task_id)) is False
    window.todo_input.set_text("保留的 Todo 草稿")
    window._write_state()
    assert "保留的 Todo 草稿" in window.state_path.read_text()
    window.todo_input.set_text("")
    window._write_state()


def check(output):
    if not Gtk.init_check():
        raise RuntimeError("需要可用的图形会话，或使用 xvfb-run 运行")
    exceptions = []
    original_hook = sys.excepthook
    def capture_exception(kind, value, traceback):
        exceptions.append(value)
        original_hook(kind, value, traceback)
    sys.excepthook = capture_exception
    with tempfile.TemporaryDirectory(prefix="steady-ui-check-") as directory:
        app = Application(directory, non_unique=True)
        app.register(None)
        app.activate()
        window = app.window
        try:
            settle()
            assert isinstance(window, MainWindow)
            assert "steady.dialogs" not in sys.modules
            assert APP_NAME == "Steady"
            assert window.get_title() == APP_NAME
            assert GLib.get_application_name() == APP_NAME
            assert window.menu_button.get_ancestor(Adw.HeaderBar).get_title_widget().get_title() == APP_NAME
            assert window._file_dialog("备份").get_filters().get_item(0).get_name() == f"{APP_NAME} 备份（JSON）"
            window.actions["about"].activate(None)
            settle()
            about = window.get_visible_dialog()
            assert isinstance(about, Adw.AboutDialog)
            assert about.get_application_name() == APP_NAME
            assert about.get_version() == VERSION
            if output:
                screenshot(window, output / "about-steady.png")
            about.force_close()
            settle()
            assert window.list_stack.get_visible_child_name() == "empty"
            assert window.task_filter.get_selected() == 0
            check_task_filter(window)
            window.actions["new-task"].activate(None)
            settle()
            form = window.active_form
            assert not form.deadline_switch.get_active()
            assert not form.deadline_fields.get_visible()
            form.title_row.set_text("")
            form.save_button.emit("clicked")
            assert form.error_label.get_visible()
            form.title_row.set_text("整理团队知识库")
            form.description.get_buffer().set_text("持续整理常见问题与操作说明。\n这是一项没有截止时间的长期任务。")
            form.save_button.emit("clicked")
            settle()
            task_id = window.selected_id
            assert app.store.get_task(task_id)["deadline"] is None
            assert not window.task_rows[0].deadline_label.get_visible()
            assert window.active_form is None
            check_status_row(window)
            assert window.status_button.get_label() == "闭环任务"
            window.progress_input.get_buffer().set_text("切换任务筛选时保留的草稿")
            window.task_filter.set_selected(1)
            settle()
            assert window.list_empty.get_title() == "没有已闭环任务"
            assert window.selected_id is None
            window.task_filter.set_selected(0)
            settle()
            assert window.selected_id == task_id
            assert window.drafts[str(task_id)] == "切换任务筛选时保留的草稿"
            window.progress_input.get_buffer().set_text("已完成目录结构整理，下一步补充部署说明。")
            window.actions["save-progress"].activate(None)
            assert app.store.event_count(task_id) == 2
            assert not window.actions["save-progress"].get_enabled()
            window.actions["edit-task"].activate(None)
            form = window.active_form
            form.deadline_switch.set_active(True)
            form.date_row.set_text("2026-10-15")
            form.save_button.emit("clicked")
            settle()
            assert app.store.get_task(task_id)["deadline"] is not None
            window.edit_task()
            form = window.active_form
            form.deadline_switch.set_active(False)
            form.date_row.set_text("无效日期在关闭开关后应被忽略")
            form.save_button.emit("clicked")
            settle()
            assert app.store.get_task(task_id)["deadline"] is None
            assert app.store.events(task_id)[0]["changes"]["deadline"]["after"] is None
            window.progress_input.get_buffer().set_text("稍后继续填写的草稿")
            settle(0.6)
            assert "稍后继续填写的草稿" in (Path(directory) / "state.json").read_text()
            other_id = app.store.create_task("完成接口联调", "跟进鉴权、字段校验与联调验收。", "2026-10-01T18:00:00+08:00")
            app.store.add_progress(other_id, "鉴权已通过，等待对方确认返回字段。")
            app.store.create_task("确认发布前检查项", "确认剩余事项，并记录交付结论。", "2026-09-28T18:00:00+08:00")
            window.refresh(other_id)
            assert window.selected_id == other_id
            window.refresh(task_id)
            assert "稍后继续填写" in window.progress_input.get_buffer().get_text(window.progress_input.get_buffer().get_start_iter(), window.progress_input.get_buffer().get_end_iter(), True)
            window.progress_input.get_buffer().set_text("")
            window.change_status()
            form = window.active_form
            form.note.get_buffer().set_text("本轮整理完成")
            form.save_button.emit("clicked")
            settle()
            assert app.store.get_task(task_id)["status"] == "closed"
            assert task_id not in [row.task["id"] for row in window.task_rows]
            window.task_filter.set_selected(1)
            settle()
            assert [row.task["id"] for row in window.task_rows] == [task_id]
            window.search.set_text("目录结构")
            settle(0.4)
            assert [row.task["id"] for row in window.task_rows] == [task_id]
            for selected, expected in ((0, []), (2, [task_id]), (1, [task_id])):
                window.task_filter.set_selected(selected)
                settle()
                assert window.search.get_text() == "目录结构"
                assert [row.task["id"] for row in window.task_rows] == expected
            window.search.set_text("接口联调")
            settle(0.4)
            assert window.list_stack.get_visible_child_name() == "empty"
            window.search.set_text("")
            settle(0.4)
            window.task_filter.set_selected(2)
            settle()
            assert len(window.task_rows) == 3
            window.task_filter.set_selected(1)
            window.refresh(task_id)
            assert not window.actions["edit-task"].get_enabled()
            assert not window.composer.get_visible()
            check_status_row(window)
            assert window.status_button.get_label() == "重新打开"
            window.change_status()
            window.active_form.note.get_buffer().set_text("继续维护下一阶段内容")
            window.active_form.save_button.emit("clicked")
            settle()
            assert app.store.get_task(task_id)["status"] == "open"
            assert window.list_stack.get_visible_child_name() == "empty"
            assert window.list_empty.get_title() == "没有已闭环任务"
            window.task_filter.set_selected(2)
            window.search.set_text("目录结构")
            settle(0.4)
            assert [row.task["id"] for row in window.task_rows] == [task_id]
            window.search.set_text("不存在的关键词")
            settle(0.4)
            assert window.list_stack.get_visible_child_name() == "empty"
            window.search.set_text("")
            window.task_filter.set_selected(0)
            settle(0.4)
            window.refresh(task_id)
            window.progress_input.get_buffer().set_text("重启后应恢复的进展草稿")
            window.todo_input.set_text("重启后应恢复的 Todo 草稿")
            window.history_filter.set_selected(5)
            window.close()
            settle()
            window = MainWindow(app, app.store)
            app.window = window
            window.present()
            settle()
            assert window.selected_id == task_id
            assert window.drafts[str(task_id)] == "重启后应恢复的进展草稿"
            assert window.todo_input.get_text() == "重启后应恢复的 Todo 草稿"
            assert window.todo_expander.get_expanded()
            assert window.history_filter.get_selected() == 5
            window.progress_input.get_buffer().set_text("")
            window.change_status()
            assert window.active_form is None
            assert window.get_visible_dialog().get_heading() == "还有未添加的 Todo"
            respond(window.get_visible_dialog(), "ok")
            window.restore_backup()
            assert isinstance(window.get_visible_dialog(), Adw.AlertDialog)
            respond(window.get_visible_dialog(), "ok")
            window.todo_input.set_text("")
            window.todo_expander.set_expanded(False)
            window.history_filter.set_selected(0)
            window._write_state()
            data = app.store.export_data()
            safety = window._restore(data)
            assert safety.exists()
            window.refresh(task_id)
            assert len(window.task_rows) == 3
            check_management(window, app.store, output)
            window.new_task()
            form = window.active_form
            form.title_row.set_text("关闭确认保护测试")
            form.close()
            settle()
            assert window.active_form is form
            alert = window.get_visible_dialog()
            assert isinstance(alert, Adw.AlertDialog)
            alert.emit("response", "cancel")
            alert.force_close()
            settle()
            form.force_close()
            settle()
            assert window.active_form is None
            if output:
                settle(4.2)
                window.detail_scroll.get_vadjustment().set_value(0)
                style = app.get_style_manager()
                style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
                screenshot(window, output / "window-light.png")
                style.set_color_scheme(Adw.ColorScheme.FORCE_DARK)
                screenshot(window, output / "window-dark.png")
                style.set_color_scheme(Adw.ColorScheme.FORCE_LIGHT)
                window.new_task()
                screenshot(window, output / "new-task.png")
                window.active_form.deadline_switch.set_active(True)
                screenshot(window, output / "new-task-deadline.png")
                window.active_form.force_close()
                settle()
            window.set_default_size(390, 760)
            settle(0.4)
            assert window.split.get_collapsed()
            window.split.set_show_content(False)
            check_task_filter(window)
            if output:
                screenshot(window, output / "narrow-list.png")
            window.split.set_show_content(True)
            check_status_row(window)
            window.detail_scroll.get_vadjustment().set_value(0)
            if output:
                screenshot(window, output / "narrow-detail.png")
                window.new_task()
                window.active_form.deadline_switch.set_active(True)
                screenshot(window, output / "narrow-new-task.png")
                window.active_form.force_close()
                settle()
            window.progress_input.get_buffer().set_text("")
            window.progress_input.get_buffer().insert_at_cursor("超" * 50001)
            assert window.progress_input.get_buffer().get_char_count() == 0
            for number in range(105):
                app.store.create_task(f"分页任务 {number}", deadline="2027-01-01T00:00:00Z")
            window.task_limit = 100
            window.refresh()
            assert len(window.task_rows) == 100 and window.more_tasks.get_visible()
            window.refresh(task_id)
            assert window.selected_id == task_id
            assert len(window.task_rows) > 100
            for number in range(100):
                app.store.add_progress(task_id, f"历史分页记录 {number}")
            window.event_limit = 100
            window.refresh(task_id)
            assert window.more_events.get_visible()
            window._load_events()
            assert not window.more_events.get_visible()
            for number in range(101):
                app.store.add_todo(task_id, f"Todo 分页记录 {number}")
            window.todo_limit = 100
            window.history_filter.set_selected(1)
            window.refresh(task_id)
            assert window.more_todos.get_visible()
            window._load_todos()
            assert not window.more_todos.get_visible()
            assert window.more_events.get_visible()
            window._load_events()
            assert not window.more_events.get_visible()
            assert app.store.get_event(window.history_list.get_first_child().event_id)["kind"] == "progress"
            window.history_filter.set_selected(0)
            window.event_limit = 100
            window._render_history()
            assert window.more_events.get_visible()
            for task in app.store.list_tasks(query="分页任务", limit=200):
                app.store.close_task(task["id"])
            window.task_filter.set_selected(1)
            assert len(window.task_rows) == 100 and window.more_tasks.get_visible()
            window._load_tasks()
            assert len(window.task_rows) == 105 and not window.more_tasks.get_visible()
            assert all(row.task["status"] == "closed" for row in window.task_rows)
            window.task_filter.set_selected(0)
            for task in app.store.list_tasks(status=None, limit=1000):
                if task["id"] != task_id:
                    app.store.delete_task(task["id"])
            window.refresh(task_id)
            respond(window.delete_task(), "confirm")
            assert app.store.counts() == (0, 0)
            assert window.selected_id is None
            assert window.list_stack.get_visible_child_name() == "empty"
            assert window.detail_stack.get_visible_child_name() == "empty"
            assert not window.actions["delete-task"].get_enabled()
            assert not window.actions["add-todo"].get_enabled()
            assert not exceptions, exceptions
            print("PASS: native GTK task lifecycle, three-way task filter/search/pagination, task/activity deletion, activity editing/filtering, optional Todos, drafts, backup restore, and adaptive layout")
        finally:
            window.close()
            settle()
            app.store.close()
            app.store = None
            sys.excepthook = original_hook


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="用隔离临时数据验证原生界面，不修改真实任务")
    parser.add_argument("--screenshots", type=Path)
    check(parser.parse_args().screenshots)
