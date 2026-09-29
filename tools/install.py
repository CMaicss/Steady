#!/usr/bin/python3
import argparse
import compileall
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from steady import APP_ID

LEGACY_APP_ID = "io.github.mtodo.Mtodo"


def require_closed_apps():
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    names = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames", None, GLib.VariantType.new("(as)"), Gio.DBusCallFlags.NONE, 5000, None).unpack()[0]
    if any(name == app_id or name.startswith(app_id + ".Profile") for name in names for app_id in (LEGACY_APP_ID, APP_ID)):
        raise RuntimeError("迁移前请先正常关闭所有 Steady 窗口（包括旧版），以保存草稿并释放数据库。")


def migrate_legacy_installation(prefix):
    roots = [prefix / "share"]
    if prefix == (Path.home() / ".local").resolve():
        data_root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share").expanduser().resolve()
        if data_root not in roots:
            roots.append(data_root)
    moves = []
    for root in roots:
        legacy, current = root / "mtodo", root / "steady"
        if not legacy.exists() and not legacy.is_symlink():
            continue
        if legacy.is_symlink() or not legacy.is_dir():
            raise RuntimeError(f"旧目录不是普通目录，请先检查：{legacy}")
        if current.exists() or current.is_symlink():
            raise RuntimeError(f"新旧目录同时存在，为避免覆盖数据，已停止安装：{legacy} 和 {current}。请先分别备份并确认要保留的数据。")
        if (legacy / "mtodo").exists() and (legacy / "steady").exists():
            raise RuntimeError(f"旧目录内的程序目录存在命名冲突，请先检查：{legacy}")
        moves.append((legacy, current))
    legacy_desktop = prefix / "share/applications" / f"{LEGACY_APP_ID}.desktop"
    if moves or legacy_desktop.exists() or (prefix / "bin/mtodo").exists():
        require_closed_apps()
    for legacy, current in moves:
        legacy.rename(current)
        old_package = current / "mtodo"
        if old_package.is_dir():
            old_package.rename(current / "steady")
        print(f"已迁移目录：{legacy} → {current}")


def update_favorite_entry():
    try:
        from gi.repository import Gio
        source = Gio.SettingsSchemaSource.get_default()
        schema = source.lookup("org.gnome.shell", True) if source else None
        if not schema:
            return
        settings = Gio.Settings.new_full(schema, None, None)
        favorites = settings.get_strv("favorite-apps")
        old_entry, new_entry = f"{LEGACY_APP_ID}.desktop", f"{APP_ID}.desktop"
        if old_entry in favorites:
            updated = list(dict.fromkeys(new_entry if item == old_entry else item for item in favorites))
            if not settings.set_strv("favorite-apps", updated):
                raise RuntimeError("系统拒绝更新固定入口")
            Gio.Settings.sync()
    except Exception as error:
        print(f"提示：请在 Dock 中重新固定 Steady，自动更新固定入口失败：{error}")


def install(prefix):
    root = Path(__file__).resolve().parents[1]
    prefix = prefix.expanduser().resolve()
    migrate_legacy_installation(prefix)
    package_dir = prefix / "share" / "steady"
    source = root / "steady"
    target = package_dir / "steady"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    if not compileall.compile_dir(target, quiet=1, force=True):
        raise RuntimeError("程序字节码预编译失败，安装未完成")
    bin_dir = prefix / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    executable = bin_dir / "steady"
    launcher = (root / "data" / "steady.in").read_text().replace("@PACKAGE_DIR@", repr(str(package_dir)))
    executable.write_text(launcher, encoding="utf-8")
    executable.chmod(0o755)
    desktop_dir = prefix / "share" / "applications"
    desktop_dir.mkdir(parents=True, exist_ok=True)
    command = str(executable).replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`").replace("$", "\\$").replace("%", "%%")
    desktop = (root / "data" / "io.github.steady.Steady.desktop.in").read_text().replace("@EXEC@", f'"{command}"')
    (desktop_dir / "io.github.steady.Steady.desktop").write_text(desktop, encoding="utf-8")
    service_dir = prefix / "share" / "dbus-1" / "services"
    service_dir.mkdir(parents=True, exist_ok=True)
    service = (root / "data" / "io.github.steady.Steady.service.in").read_text().replace("@SERVICE_EXEC@", shlex.quote(str(executable)))
    (service_dir / "io.github.steady.Steady.service").write_text(service, encoding="utf-8")
    icon_dir = prefix / "share" / "icons" / "hicolor" / "scalable" / "apps"
    icon_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / "icons" / "hicolor" / "scalable" / "apps" / "io.github.steady.Steady.svg", icon_dir)
    metadata_dir = prefix / "share" / "metainfo"
    metadata_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(root / "data" / "io.github.steady.Steady.metainfo.xml", metadata_dir)
    for path in (
        bin_dir / "mtodo",
        desktop_dir / f"{LEGACY_APP_ID}.desktop",
        service_dir / f"{LEGACY_APP_ID}.service",
        metadata_dir / f"{LEGACY_APP_ID}.metainfo.xml",
        icon_dir / f"{LEGACY_APP_ID}.svg",
        target / "icons/hicolor/scalable/apps" / f"{LEGACY_APP_ID}.svg",
    ):
        path.unlink(missing_ok=True)
    if prefix == (Path.home() / ".local").resolve():
        update_favorite_entry()
    if shutil.which("update-desktop-database"):
        subprocess.run(["update-desktop-database", str(desktop_dir)], check=False)
    print(f"已安装：{executable}\n可从应用菜单搜索 Steady 启动。任务数据不会随安装更新被覆盖。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="将 Steady 安装到当前用户，不修改系统 Python")
    parser.add_argument("--prefix", type=Path, default=Path.home() / ".local")
    install(parser.parse_args().prefix)
