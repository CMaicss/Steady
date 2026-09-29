import argparse
import os
import sys
from pathlib import Path

from . import APP_NAME, VERSION
from .i18n import _, LANGUAGES, configure, read_language


def main():
    options = argparse.ArgumentParser(add_help=False)
    options.add_argument("--data-dir")
    options.add_argument("--language", choices=["auto", *dict(LANGUAGES)])
    early, remaining = options.parse_known_args()
    directory = Path(early.data_dir).expanduser() if early.data_dir else Path(os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local/share")) / "steady"
    language = early.language or os.environ.get("STEADY_LANGUAGE") or read_language(directory)
    configure(language)
    parser = argparse.ArgumentParser(description=_("{app_name} — Local tasks and progress").format(app_name=APP_NAME))
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {VERSION}")
    parser.add_argument("--data-dir", help=_("Use a separate data directory (defaults to XDG_DATA_HOME)"))
    parser.add_argument("--language", choices=["auto", *dict(LANGUAGES)], help=_("Interface language for this launch only; auto follows the system"))
    arguments = parser.parse_args()
    try:
        from .application import Application
    except (ImportError, ValueError) as error:
        print(_("Could not load GTK/libadwaita: {error}\nOn Ubuntu/Debian, install python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 and run with /usr/bin/python3.").format(error=error), file=sys.stderr)
        return 1
    return Application(arguments.data_dir, language=language).run([sys.argv[0]])


if __name__ == "__main__":
    raise SystemExit(main())
