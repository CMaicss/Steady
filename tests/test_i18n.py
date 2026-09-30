import ast
import contextlib
import gettext
import io
import json
import os
import string
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

from steady import i18n
from steady.storage import Store, ValidationError, atomic_json
from steady.timeutils import deadline_status

ROOT = Path(__file__).resolve().parents[1]


def source_messages():
    messages = {}
    for path in (ROOT / "steady").glob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name) or node.func.id not in ("_", "N_", "ngettext", "pgettext"):
                continue
            if node.func.id == "pgettext":
                messages[node.args[0].value + "\x04" + node.args[1].value] = None
                continue
            if node.args and isinstance(node.args[0], ast.Constant):
                messages[node.args[0].value] = node.args[1].value if node.func.id == "ngettext" else None
    for node in ET.parse(ROOT / "steady/window.ui").iter("property"):
        if node.get("translatable") == "yes":
            messages[node.text] = None
    return messages


class TranslationTests(unittest.TestCase):
    def test_all_catalogs_cover_source_and_format_fields(self):
        messages = source_messages()
        self.assertGreater(len(messages), 250)
        formatter = string.Formatter()
        for language, name in i18n.LANGUAGES:
            with self.subTest(language=language):
                catalog = gettext.translation(i18n.DOMAIN, i18n.LOCALE_DIR, languages=[language])
                self.assertEqual(catalog.info()["language"], language)
                for message, plural in messages.items():
                    expected = {(field, spec, conversion) for literal, field, spec, conversion in formatter.parse(message) if field}
                    variants = [catalog._catalog.get((message, index)) for index in range(3 if language == "ru" else 1 if language in ("zh_CN", "zh_TW", "ja") else 2)] if plural else [catalog._catalog.get(message)]
                    for text in variants:
                        self.assertTrue(text, (language, message))
                        actual = {(field, spec, conversion) for literal, field, spec, conversion in formatter.parse(text) if field}
                        self.assertEqual(expected, actual, (language, message))
                        text.format(**{field: 12 if spec else "Example" for field, spec, conversion in expected})

    def test_task_dates_share_one_line_in_all_languages(self):
        message = "Created {created_at} · Last updated {updated_at}"
        created = "2026-09-29 10:20:30"
        updated = "2026-09-30 11:22:33"
        for language, name in i18n.LANGUAGES:
            with self.subTest(language=language):
                catalog = gettext.translation(i18n.DOMAIN, i18n.LOCALE_DIR, languages=[language])
                text = catalog.gettext(message).format(created_at=created, updated_at=updated)
                self.assertIn(created, text)
                self.assertIn(updated, text)
                self.assertIn(" · ", text)
                self.assertNotIn("\n", text)

    def test_locale_aliases_and_fallback(self):
        self.assertEqual(i18n.resolve_language("zh-Hans-HK"), "zh_CN")
        cases = {"zh-CN": "zh_CN", "zh_SG.UTF-8": "zh_CN", "zh-Hans": "zh_CN", "zh-Hant": "zh_TW", "zh-Hant-HK": "zh_TW", "zh_TW": "zh_TW", "zh_HK": "zh_TW", "zh_MO": "zh_TW", "en_GB.UTF-8": "en", "de_DE@euro": "de", "fr_CA": "fr", "es_MX": "es", "ru_RU": "ru", "ja_JP": "ja", "pt_BR:fr:en": "fr", "pt_BR": "en", "C": "en", "C.UTF-8": "en"}
        for value, expected in cases.items():
            self.assertEqual(i18n.resolve_language(value), expected, value)
        with patch.object(i18n, "SYSTEM_LANGUAGE", "zh_HK:en"), patch.dict(os.environ, {"LC_ALL": "de_DE"}):
            self.assertEqual(i18n.resolve_language(), "zh_TW")
        with patch.object(i18n, "SYSTEM_LANGUAGE", ""), patch.dict(os.environ, {"LC_ALL": "fr_CA", "LC_MESSAGES": "de_DE", "LANG": "ja_JP"}):
            self.assertEqual(i18n.resolve_language(), "fr")

    def test_language_preference_validation_and_preservation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "language.json"
            self.assertEqual(i18n.read_language(directory), "auto")
            for value in ("auto", *dict(i18n.LANGUAGES)):
                atomic_json(path, {"language": value})
                self.assertEqual(i18n.read_language(directory), value)
            for content in ("{", "[]", '{"language": []}', '{"language": "../de"}', "x" * 5000):
                path.write_text(content)
                with contextlib.redirect_stderr(io.StringIO()) as warning:
                    self.assertEqual(i18n.read_language(directory), "auto")
                self.assertTrue(warning.getvalue())
                self.assertEqual(path.read_text(), content)

    def test_template_translation_is_escaped_and_not_translated_twice(self):
        with patch.object(i18n, "_", side_effect=lambda text: "<Translated> & " + text):
            template = ET.fromstring(i18n.translated_template(ROOT / "steady/window.ui"))
        self.assertFalse(any(element.get("translatable") for element in template.iter()))
        search = template.find(".//object[@id='search']/property[@name='placeholder-text']")
        self.assertEqual(search.text, "<Translated> & Search tasks and progress")
        self.assertEqual(template.find(".//template/property[@name='title']").text, "Steady")

    def test_plural_rules_and_english_fallback(self):
        variants = {
            "en": ["1 day", "2 days", "5 days", "11 days", "21 days", "22 days"],
            "de": ["1 Tag", "2 Tage", "5 Tage", "11 Tage", "21 Tage", "22 Tage"],
            "fr": ["1 jour", "2 jours", "5 jours", "11 jours", "21 jours", "22 jours"],
            "ru": ["1 день", "2 дня", "5 дней", "11 дней", "21 день", "22 дня"],
            "es": ["1 día", "2 días", "5 días", "11 días", "21 días", "22 días"],
            "ja": [f"{count} 日" for count in (1, 2, 5, 11, 21, 22)],
            "zh_CN": [f"{count} 天" for count in (1, 2, 5, 11, 21, 22)],
            "zh_TW": [f"{count} 天" for count in (1, 2, 5, 11, 21, 22)],
        }
        for language, expected in variants.items():
            catalog = gettext.translation(i18n.DOMAIN, i18n.LOCALE_DIR, languages=[language])
            with patch.object(i18n, "translation", catalog):
                self.assertEqual([i18n.ngettext("{count} day", "{count} days", count).format(count=count) for count in (1, 2, 5, 11, 21, 22)], expected)
                self.assertEqual(i18n._("An untranslated message"), "An untranslated message")
                self.assertEqual(deadline_status(None), ("", ""))
        with patch.object(i18n, "LOCALE_DIR", ROOT / ".artifacts/missing-catalogs"), patch.object(i18n, "translation"), patch.dict(os.environ):
            i18n.configure("de")
            self.assertEqual(i18n._("New Task"), "New Task")

    def test_language_does_not_change_persisted_data(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(Path(directory) / "tasks.sqlite3")
            self.addCleanup(store.close)
            title = "任务 — Aufgabe — Задача — タスク"
            task_id = store.create_task(title)
            store.add_progress(task_id, "完成原型 · prototype ready")
            todo_id = store.add_todo(task_id, "レビュー")
            store.set_todo_completed(todo_id, True)
            baseline = store.export_data()
            for language, name in i18n.LANGUAGES:
                with patch.object(i18n, "translation", gettext.translation(i18n.DOMAIN, i18n.LOCALE_DIR, languages=[language])):
                    with self.assertRaises(ValidationError) as raised:
                        store.create_task("")
                    self.assertEqual(str(raised.exception), i18n._("Required field: {label}").format(label=i18n._("Task title")))
                    exported = store.export_data()
                    for key in ("tasks", "events", "todos"):
                        self.assertEqual(exported[key], baseline[key])
            with self.assertRaises(ValidationError):
                store.export_backup(Path(directory) / "language.json")

    def test_cli_languages_without_installed_system_locales(self):
        expected = {"en": "Local tasks and progress", "zh_CN": "本地任务与进展管理", "zh_TW": "本機任務與進度管理", "de": "Lokale Aufgaben und Fortschritte", "fr": "Tâches et progression en local", "ru": "Локальные задачи и ход работы", "es": "Tareas y avances locales", "ja": "ローカルのタスクと進捗管理"}
        with tempfile.TemporaryDirectory() as directory:
            command = [sys.executable, "-m", "steady", "--data-dir", directory]
            env = dict(os.environ, LC_ALL="unavailable.UTF-8", LANGUAGE="pt_BR", STEADY_LANGUAGE="fr")
            for language, description in expected.items():
                result = subprocess.run([*command, "--language", language, "--help"], cwd=ROOT, env=env, text=True, capture_output=True, check=True)
                self.assertIn(description, result.stdout)
            atomic_json(Path(directory) / "language.json", {"language": "ja"})
            env.pop("STEADY_LANGUAGE")
            result = subprocess.run([*command, "--help"], cwd=ROOT, env=env, text=True, capture_output=True, check=True)
            self.assertIn(expected["ja"], result.stdout)
            result = subprocess.run([*command, "--language", "auto", "--help"], cwd=ROOT, env=env, text=True, capture_output=True, check=True)
            self.assertIn(expected["en"], result.stdout)
            self.assertFalse((Path(directory) / "tasks.sqlite3").exists())


if __name__ == "__main__":
    unittest.main()
