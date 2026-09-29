#!/usr/bin/python3
import argparse
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def check(language, output):
    os.environ["STEADY_LANGUAGE"] = language
    from steady.application import Application
    from steady.i18n import _, LANGUAGES, pgettext, read_language
    from steady.widgets import text_of
    from steady.window import MainWindow
    from tools.check_ui import settle, screenshot, check_status_row, check_task_metadata
    from gi.repository import Adw

    exceptions = []
    original_hook = sys.excepthook
    sys.excepthook = lambda kind, error, traceback: exceptions.append(error)
    with tempfile.TemporaryDirectory(prefix="steady-i18n-") as directory:
        app = Application(directory, non_unique=True, language=language)
        app.register(None)
        app.activate()
        window = app.window
        try:
            assert isinstance(window, MainWindow)
            assert window.search.get_placeholder_text() == _("Search tasks and progress")
            assert window.task_filter.get_model().get_string(0) == _("Open")
            assert window.history_filter.get_model().get_string(1) == _("Progress Only")
            assert window.todo_empty.get_text() == _("Break tasks into small steps. Using a checklist is entirely optional.")
            task_id = app.store.create_task("Release plan · 发布计划 · リリース計画", "Local sample — 示例数据", (datetime.now(timezone.utc) + timedelta(days=2, hours=3)).isoformat())
            app.store.add_progress(task_id, "Prototype ready · 原型已完成")
            todo_id = app.store.add_todo(task_id, "Review translations · 核对翻译")
            app.store.set_todo_completed(todo_id, True)
            window.refresh(task_id)
            window.split.set_show_content(True)
            window.todo_expander.set_expanded(True)
            baseline = app.store.export_data()
            assert window.task_status.get_text() == pgettext("task-status", "Open")
            assert window.status_button.get_label() == _("Close Task")
            window.new_task()
            settle()
            dialog = window.active_form
            assert dialog.get_title() == _("New Task")
            assert dialog.title_row.get_title() == _("Task Title · Required")
            dialog.save_button.emit("clicked")
            assert dialog.error_label.get_text() == _("Required field: {label}").format(label=_("Task title"))
            dialog.force_close()
            settle()
            window.progress_input.get_buffer().set_text("Draft — 未提交草稿 — Черновик")
            window.todo_input.set_text("Next step — 下一步")
            window.show_language()
            settle()
            dialog = window.active_form
            assert dialog.get_title() == _("Language")
            assert dialog.language_row.get_model().get_string(0) == _("System Default")
            for index, (code, name) in enumerate(LANGUAGES, 1):
                assert dialog.language_row.get_model().get_string(index) == name
            if output:
                screenshot(window, output / f"{language}-language.png")
            dialog.language_row.set_selected(1)
            with patch("steady.window.atomic_json", side_effect=OSError("Read-only test directory")):
                dialog.save_button.emit("clicked")
            assert dialog.error_label.get_visible() and dialog.save_button.get_sensitive()
            assert not (Path(directory) / "language.json").exists()
            dialog.save_button.emit("clicked")
            settle()
            assert read_language(directory) == "zh_CN"
            assert text_of(window.progress_input) == "Draft — 未提交草稿 — Черновик"
            assert window.todo_input.get_text() == "Next step — 下一步"
            style = Adw.StyleManager.get_default()
            for width in (1120, 360):
                window.set_default_size(width, 860)
                for mode, name in ((Adw.ColorScheme.FORCE_LIGHT, "light"), (Adw.ColorScheme.FORCE_DARK, "dark")):
                    style.set_color_scheme(mode)
                    settle(0.4)
                    check_status_row(window)
                    if output:
                        screenshot(window, output / f"{language}-{width}-{name}.png")
                if width == 360:
                    window.split.set_show_content(False)
                    settle()
                    assert window.search.get_width() > 40
                    assert window.task_filter.get_width() > 0
                    check_task_metadata(window)
                    if output:
                        screenshot(window, output / f"{language}-list.png")
                    window.split.set_show_content(True)
            window.close()
            settle()
            app.window = MainWindow(app, app.store)
            window = app.window
            window.present()
            settle()
            assert text_of(window.progress_input) == "Draft — 未提交草稿 — Черновик"
            assert window.todo_input.get_text() == "Next step — 下一步"
            for key in ("tasks", "events", "todos"):
                assert app.store.export_data()[key] == baseline[key]
            assert not exceptions, exceptions
            print(f"PASS {language}: native template, dialogs, filters, drafts, language setting, and adaptive light/dark layout")
        finally:
            window.close()
            settle()
            app.store.close()
            app.store = None
            sys.excepthook = original_hook


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Check every translation in isolated native GTK windows.")
    parser.add_argument("--language")
    parser.add_argument("--screenshots", type=Path)
    arguments = parser.parse_args()
    if arguments.language:
        check(arguments.language, arguments.screenshots)
    else:
        for language in ("zh_CN", "zh_TW", "en", "de", "fr", "ru", "es", "ja"):
            command = [sys.executable, str(Path(__file__).resolve()), "--language", language]
            if arguments.screenshots:
                command += ["--screenshots", str(arguments.screenshots.resolve())]
            subprocess.run(command, cwd=ROOT, check=True, timeout=120)
