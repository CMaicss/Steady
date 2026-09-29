import gettext
import json
import os
import sys
from pathlib import Path

DOMAIN = "steady"
LOCALE_DIR = Path(__file__).with_name("locale")
LANGUAGES = (
    ("zh_CN", "简体中文"), ("zh_TW", "繁體中文"), ("en", "English"),
    ("de", "Deutsch"), ("fr", "Français"), ("ru", "Русский"),
    ("es", "Español"), ("ja", "日本語"),
)
SYSTEM_LANGUAGE = os.environ.get("LANGUAGE", "")
translation = gettext.NullTranslations()


def normalize_language(value):
    value = value.split(".")[0].split("@")[0].replace("-", "_").lower()
    parts = value.split("_")
    if parts[0] == "zh":
        if "hans" in parts:
            return "zh_CN"
        return "zh_TW" if any(part in ("tw", "hk", "mo", "hant") for part in parts) else "zh_CN"
    return parts[0] if parts[0] in dict(LANGUAGES) else None


def resolve_language(value="auto"):
    if value == "auto":
        value = SYSTEM_LANGUAGE or os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG", "en")
    for candidate in value.split(":"):
        language = normalize_language(candidate)
        if language:
            return language
    return "en"


def configure(language="auto"):
    global translation
    selected = resolve_language(language)
    translation = gettext.translation(DOMAIN, LOCALE_DIR, languages=[selected], fallback=True)
    os.environ["LANGUAGE"] = selected
    return selected


def read_language(directory):
    path = Path(directory) / "language.json"
    try:
        if path.stat().st_size > 4096:
            raise ValueError(_("Invalid language setting"))
        data = json.loads(path.read_text(encoding="utf-8"))
        value = data.get("language") if isinstance(data, dict) else None
        if not isinstance(value, str) or value != "auto" and value not in dict(LANGUAGES):
            raise ValueError(_("Invalid language setting"))
        return value
    except FileNotFoundError:
        return "auto"
    except (OSError, ValueError) as error:
        print(_("Could not read the language setting; using the system language: {error}").format(error=error), file=sys.stderr)
        return "auto"


def _(message):
    return translation.gettext(message)


def ngettext(singular, plural, count):
    return translation.ngettext(singular, plural, count)


def pgettext(context, message):
    return translation.pgettext(context, message)


def N_(message):
    return message


def translated_template(path):
    import xml.etree.ElementTree as ET
    tree = ET.parse(path)
    for element in tree.iter():
        if element.get("translatable") == "yes":
            element.text = _(element.text or "")
            del element.attrib["translatable"]
    return ET.tostring(tree.getroot(), encoding="unicode")


configure(os.environ.get("STEADY_LANGUAGE", "auto"))
