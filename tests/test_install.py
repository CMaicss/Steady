import configparser
import contextlib
import io
import shlex
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from steady import APP_NAME, VERSION
from steady.storage import Store
from tools.install import install


class InstallTests(unittest.TestCase):
    def test_activation_service_and_installation_preserve_data(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "prefix with space $ % '"
            data_dir = prefix / "share" / "steady"
            data_dir.mkdir(parents=True)
            database = data_dir / "tasks.sqlite3"
            database.write_bytes(b"existing database")
            state = data_dir / "state.json"
            state.write_text('{"drafts": {"1": "unsaved text"}}', encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                install(prefix)
            self.assertEqual(database.read_bytes(), b"existing database")
            self.assertIn("unsaved text", state.read_text())
            desktop = configparser.ConfigParser(interpolation=None)
            desktop.read(prefix / "share/applications/io.github.steady.Steady.desktop")
            self.assertEqual(APP_NAME, "Steady")
            self.assertEqual(desktop["Desktop Entry"]["Name"], APP_NAME)
            self.assertEqual(desktop["Desktop Entry"]["Name[zh_CN]"], APP_NAME)
            self.assertTrue(desktop["Desktop Entry"].getboolean("DBusActivatable"))
            metadata = ET.parse(prefix / "share/metainfo/io.github.steady.Steady.metainfo.xml")
            self.assertEqual(metadata.findtext("name"), APP_NAME)
            service = configparser.ConfigParser(interpolation=None)
            service.read(prefix / "share/dbus-1/services/io.github.steady.Steady.service")
            self.assertEqual(service["D-BUS Service"]["Name"], "io.github.steady.Steady")
            command = shlex.split(service["D-BUS Service"]["Exec"])
            self.assertEqual(command, [str(prefix / "bin/steady")])
            result = subprocess.run([*command, "--version"], capture_output=True, text=True, check=True)
            self.assertEqual(result.stdout.strip(), f"{APP_NAME} {VERSION}")
            self.assertFalse((prefix / "bin/mtodo").exists())
            help_result = subprocess.run([*command, "--help"], capture_output=True, text=True, check=True)
            self.assertIn("Steady — 本地任务与进展管理", help_result.stdout)
            self.assertNotIn("Mtodo", help_result.stdout)
            self.assertEqual(len(list((prefix / "share/applications").glob("*.desktop"))), 1)
            self.assertTrue(list((data_dir / "steady/__pycache__").glob("*.pyc")))

    def test_legacy_installation_moves_data_and_removes_old_entries(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            legacy = prefix / "share/mtodo"
            store = Store(legacy / "tasks.sqlite3")
            task_id = store.create_task("迁移后保留的任务")
            store.add_progress(task_id, "迁移前的进展")
            store.add_todo(task_id, "待完成步骤")
            store.export_backup(legacy / "backups/previous.json")
            store.close()
            (legacy / "state.json").write_text('{"drafts":{"1":"待提交草稿"}}')
            (legacy / "steady-note.txt").write_text("额外文件也应保留")
            old_entries = [
                prefix / "bin/mtodo",
                prefix / "share/applications/io.github.mtodo.Mtodo.desktop",
                prefix / "share/dbus-1/services/io.github.mtodo.Mtodo.service",
                prefix / "share/metainfo/io.github.mtodo.Mtodo.metainfo.xml",
                prefix / "share/icons/hicolor/scalable/apps/io.github.mtodo.Mtodo.svg",
                legacy / "mtodo/icons/hicolor/scalable/apps/io.github.mtodo.Mtodo.svg",
            ]
            for path in old_entries:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("旧版安装文件")
            snapshots = {name: (legacy / name).read_bytes() for name in ("tasks.sqlite3", "state.json", "backups/previous.json", "steady-note.txt")}
            with patch("tools.install.require_closed_apps") as guard, contextlib.redirect_stdout(io.StringIO()):
                install(prefix)
            guard.assert_called_once()
            current = prefix / "share/steady"
            self.assertFalse(legacy.exists())
            for name, expected in snapshots.items():
                self.assertEqual((current / name).read_bytes(), expected)
            self.assertFalse(list(prefix.rglob("*mtodo*")))
            restored = Store(current / "tasks.sqlite3")
            try:
                self.assertEqual(restored.get_task(task_id)["title"], "迁移后保留的任务")
                self.assertEqual(restored.todo_counts(task_id), (0, 1))
            finally:
                restored.close()

    def test_running_application_prevents_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            legacy = prefix / "share/mtodo"
            legacy.mkdir(parents=True)
            (legacy / "tasks.sqlite3").write_bytes(b"original data")
            with patch("tools.install.require_closed_apps", side_effect=RuntimeError("仍在运行")):
                with self.assertRaisesRegex(RuntimeError, "仍在运行"):
                    install(prefix)
            self.assertEqual((legacy / "tasks.sqlite3").read_bytes(), b"original data")
            self.assertFalse((prefix / "share/steady").exists())

    def test_conflicting_directories_are_not_merged_or_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory)
            for name in ("mtodo", "steady"):
                folder = prefix / "share" / name
                folder.mkdir(parents=True)
                (folder / "tasks.sqlite3").write_text(name)
            with patch("tools.install.require_closed_apps") as guard:
                with self.assertRaisesRegex(RuntimeError, "新旧目录同时存在"):
                    install(prefix)
            guard.assert_not_called()
            for name in ("mtodo", "steady"):
                self.assertEqual((prefix / "share" / name / "tasks.sqlite3").read_text(), name)

    def test_custom_xdg_data_directory_is_migrated_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            prefix = home / ".local"
            data_root = home / "custom-data"
            old_package = prefix / "share/mtodo/mtodo"
            old_package.mkdir(parents=True)
            (old_package / "__init__.py").write_text("")
            (data_root / "mtodo").mkdir(parents=True)
            (data_root / "mtodo/tasks.sqlite3").write_bytes(b"separate database")
            with patch("tools.install.Path.home", return_value=home), patch.dict("os.environ", {"XDG_DATA_HOME": str(data_root)}), patch("tools.install.require_closed_apps") as guard, patch("tools.install.update_favorite_entry"), contextlib.redirect_stdout(io.StringIO()):
                install(prefix)
            guard.assert_called_once()
            self.assertTrue((prefix / "share/steady/steady/__init__.py").exists())
            self.assertEqual((data_root / "steady/tasks.sqlite3").read_bytes(), b"separate database")
            self.assertFalse((data_root / "mtodo").exists())


if __name__ == "__main__":
    unittest.main()
