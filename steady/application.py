import hashlib
import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from . import APP_ID, APP_NAME
from .i18n import _, configure, read_language
from .storage import Store


class Application(Adw.Application):
    def __init__(self, data_dir=None, non_unique=False, language=None):
        default_directory = (Path(GLib.get_user_data_dir()) / "steady").resolve()
        directory = Path(data_dir).expanduser().resolve() if data_dir else default_directory
        self.language = configure(language or os.environ.get("STEADY_LANGUAGE") or read_language(directory))
        app_id = APP_ID
        if directory != default_directory:
            app_id += ".Profile" + hashlib.sha256(str(directory).encode()).hexdigest()[:12]
        super().__init__(application_id=app_id, flags=Gio.ApplicationFlags.NON_UNIQUE if non_unique else Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.data_dir = directory
        self.default_data_dir = default_directory
        self.store = None
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        GLib.set_application_name(APP_NAME)
        Gtk.Window.set_default_icon_name(APP_ID)
        icon_path = Path(__file__).with_name("icons")
        display = Gdk.Display.get_default()
        if display:
            Gtk.IconTheme.get_for_display(display).add_search_path(str(icon_path))
            provider = Gtk.CssProvider()
            provider.load_from_path(str(Path(__file__).with_name("style.css")))
            Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.set_accels_for_action("win.new-task", ["<Primary>n"])
        self.set_accels_for_action("win.search", ["<Primary>f"])
        self.set_accels_for_action("win.save-progress", ["<Primary>Return", "<Primary>KP_Enter"])
        self.set_accels_for_action("win.quit", ["<Primary>q"])

    def do_activate(self):
        if self.window:
            self.window.present()
            return
        try:
            if Gtk.get_minor_version() < 12 or (Adw.get_major_version() == 1 and Adw.get_minor_version() < 5):
                raise RuntimeError(_("{app_name} requires GTK ≥ 4.12 and libadwaita ≥ 1.5. Install newer system libraries or use Flatpak.").format(app_name=APP_NAME))
            from .window import MainWindow
            if self.data_dir == self.default_data_dir and not (self.data_dir / "tasks.sqlite3").exists() and (self.data_dir.with_name("mtodo") / "tasks.sqlite3").exists():
                raise RuntimeError(_("Legacy task data found. Close the old application, then run python3 tools/install.py from the source directory to migrate. Existing data will not be overwritten and no empty database will be created."))
            self.store = Store(self.data_dir / "tasks.sqlite3")
            self.window = MainWindow(self, self.store)
            self.window.present()
        except Exception as error:
            print(f"{APP_NAME}: {error}", file=sys.stderr)
            self.window = Adw.ApplicationWindow(application=self, title=APP_NAME, default_width=520, default_height=360)
            page = Adw.StatusPage(title=_("Could not open task data"), description=_("{error}\n\nData location: {data_dir}\n\nExisting data will not be erased. Check directory permissions and disk space, or start with a separate data directory and restore a backup.").format(error=error, data_dir=self.data_dir), icon_name="dialog-error-symbolic")
            self.window.set_content(page)
            self.window.present()

    def do_shutdown(self):
        if self.store:
            self.store.close()
            self.store = None
        Adw.Application.do_shutdown(self)
