#!/usr/bin/python3
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*command):
    subprocess.run(command, cwd=ROOT, check=True)


def update():
    template = "po/steady.pot"
    run("xgettext", "--language=Python", "--from-code=UTF-8", "--keyword=_", "--keyword=N_", "--keyword=ngettext:1,2", "--keyword=pgettext:1c,2", "--package-name=Steady", "--no-wrap", "--output=" + template, *(str(path.relative_to(ROOT)) for path in sorted((ROOT / "steady").glob("*.py"))))
    run("xgettext", "--language=Glade", "--from-code=UTF-8", "--join-existing", "--package-name=Steady", "--no-wrap", "--output=" + template, "steady/window.ui")
    content = (ROOT / template).read_text(encoding="utf-8")
    (ROOT / template).write_text(content[content.index('msgid ""'):], encoding="utf-8")
    for catalog in sorted((ROOT / "po").glob("*.po")):
        run("msgmerge", "--update", "--backup=none", "--no-wrap", str(catalog), template)


def compile_catalogs():
    for catalog in sorted((ROOT / "po").glob("*.po")):
        output = ROOT / "steady/locale" / catalog.stem / "LC_MESSAGES/steady.mo"
        output.parent.mkdir(parents=True, exist_ok=True)
        run("msgfmt", "--check", "--check-format", "--statistics", "--output-file=" + str(output), str(catalog))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Update gettext sources and compile bundled translations (requires GNU gettext).")
    parser.add_argument("--update", action="store_true", help="Extract messages and merge catalogs before compiling")
    if parser.parse_args().update:
        update()
    compile_catalogs()
