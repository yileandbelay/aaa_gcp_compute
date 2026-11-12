"""A small translation layer.

Source strings are English, written in the templates and routes as ``_("text")``. A
language is a JSON file in ``academy/translations/`` mapping each English string to its
translation (``am.json`` is Amharic). A missing entry simply shows the English text, so
a partly translated language never breaks a page.

To add a language: copy ``am.json`` to ``<code>.json``, translate the values, and add the
code to ``LANGUAGES`` (and its name to ``LANGUAGE_NAMES`` below). A theme folder can
override or extend any catalog with ``THEME_DIR/translations/<code>.json``.

Placeholders use Python's ``%(name)s`` style so word order can differ between languages:
``_("Welcome, %(name)s!", name=user.full_name)``. Write a literal percent sign as ``%%``
in any string that has placeholders.
"""
import json
import os
from datetime import datetime

from flask import current_app, has_request_context, request
from markupsafe import Markup

LANGUAGE_NAMES = {"en": "English", "am": "አማርኛ"}  # each language written in itself
LANGUAGE_SHORT = {"en": "EN", "am": "አማ"}  # the compact switcher in the header
COOKIE = "lang"

# Gregorian month names in Amharic, for dates shown to students.
AM_MONTHS = ["ጃንዋሪ", "ፌብሩዋሪ", "ማርች", "ኤፕሪል", "ሜይ", "ጁን", "ጁላይ", "ኦገስት", "ሴፕቴምበር", "ኦክቶበር",
             "ኖቬምበር", "ዲሴምበር"]
AM_MONTHS_SHORT = ["ጃንዩ", "ፌብሩ", "ማርች", "ኤፕሪ", "ሜይ", "ጁን", "ጁላይ", "ኦገስ", "ሴፕቴ", "ኦክቶ", "ኖቬም", "ዲሴም"]


def _load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str) and v}


def load_catalogs(app):
    """{language: {english: translation}} from the built-in and theme folders."""
    folders = [os.path.join(os.path.dirname(__file__), "translations")]
    theme = app.config.get("THEME_DIR")
    if theme:
        folders.append(os.path.join(os.path.abspath(theme), "translations"))
    catalogs = {}
    for code in app.config["LANGUAGES"]:
        merged = {}
        for folder in folders:
            merged.update(_load(os.path.join(folder, f"{code}.json")))
        catalogs[code] = merged
    return catalogs


def current_language():
    cfg = current_app.config
    available = cfg["LANGUAGES"]
    if has_request_context():
        chosen = request.cookies.get(COOKIE)
        if chosen in available:
            return chosen
        if cfg["LANGUAGE_AUTODETECT"] and request.accept_languages:
            best = request.accept_languages.best_match(available)
            if best:
                return best
    return cfg["DEFAULT_LANGUAGE"] if cfg["DEFAULT_LANGUAGE"] in available else available[0]


def gettext(message, **values):
    lang = current_language()
    text = current_app.extensions["i18n"].get(lang, {}).get(message, message)
    if not values:
        return text
    if any(isinstance(v, Markup) for v in values.values()):
        # A placeholder holds HTML (for example a link): escape the other values, keep this one.
        return Markup(text) % values
    return text % values


def ngettext(singular, plural, n, **values):
    """Pick the singular or plural source string by ``n``; ``%(n)s`` is filled in."""
    return gettext(singular if n == 1 else plural, n=n, **values)


def localdate(value, fmt="%d %b %Y"):
    """Format a date; month names are translated for Amharic."""
    if not isinstance(value, datetime):
        return value or ""
    text = value.strftime(fmt)
    if current_language() == "am":
        if "%B" in fmt:
            text = text.replace(value.strftime("%B"), AM_MONTHS[value.month - 1])
        elif "%b" in fmt:
            text = text.replace(value.strftime("%b"), AM_MONTHS_SHORT[value.month - 1])
    return text


def duration(minutes):
    """Minutes as people say them: "15 min", "1 h", "1 h 20 min". Empty for nothing or less."""
    try:
        total = int(round(float(minutes or 0)))
    except (TypeError, ValueError):
        return ""
    if total <= 0:
        return ""
    hours, mins = divmod(total, 60)
    if not hours:
        return gettext("%(n)s min", n=mins)
    if not mins:
        return gettext("%(n)s h", n=hours)
    return f"{gettext('%(n)s h', n=hours)} {gettext('%(n)s min', n=mins)}"


def init_app(app):
    app.extensions["i18n"] = load_catalogs(app)
    app.jinja_env.globals.update(
        _=gettext, _n=ngettext, current_lang=current_language,
        language_names=LANGUAGE_NAMES, language_short=LANGUAGE_SHORT, available_languages=lambda: app.config["LANGUAGES"])
    app.jinja_env.filters["localdate"] = localdate
    app.jinja_env.filters["duration"] = duration

    @app.context_processor
    def translated_site_name():
        """The academy's name in the visitor's language (the catalog key is the English name)."""
        return {"site_name": gettext(app.config["SITE_NAME"])}


# --- Finding the strings that need translating -------------------------------------------------
# Templates that stay in English (the staff area and the long legal pages).
UNTRANSLATED_TEMPLATE_DIRS = ("admin", "policies")


def _template_strings(env, folder):
    from jinja2 import nodes

    found = set()
    for root, dirs, files in os.walk(folder):
        rel = os.path.relpath(root, folder)
        if rel.split(os.sep)[0] in UNTRANSLATED_TEMPLATE_DIRS:
            dirs[:] = []
            continue
        for name in files:
            if not name.endswith(".html"):
                continue
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as fh:
                tree = env.parse(fh.read())
            for call in tree.find_all(nodes.Call):
                if isinstance(call.node, nodes.Name) and call.node.name in ("_", "_n"):
                    n = 2 if call.node.name == "_n" else 1
                    for arg in call.args[:n]:
                        if isinstance(arg, nodes.Const) and isinstance(arg.value, str):
                            found.add(arg.value)
    return found


def _python_strings(folder):
    import ast

    found = set()
    for root, _dirs, files in os.walk(folder):
        if os.path.basename(root) in ("migrations", "admin"):
            continue
        for name in files:
            if not name.endswith(".py") or name == "admin.py":
                continue
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for call in ast.walk(tree):
                if (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                        and call.func.id in ("_", "gettext") and call.args
                        and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, str)):
                    found.add(call.args[0].value)
    return found


def dynamic_strings():
    """Strings that are translated by a variable (``_(level)``) rather than a literal."""
    from .config import Config
    from .models import Payment
    from .routes.main import LENGTHS, SORTS

    fixed = {"Beginner", "Intermediate", "Advanced", "Business",
             "Paid", "Submitted", "Initiated", "Failed", "Rejected",
             "Manual", "Chapa", "Demo", "Student", "Instructor", "Admin"}
    fixed |= {label for _key, label in LENGTHS} | {label for _key, label in SORTS}
    fixed |= {label for _key, label in Payment.CHANNELS}
    fixed |= {Config.SITE_NAME, Config.SITE_TAGLINE, Config.SITE_HEADLINE, Config.SITE_INTRO, Config.MANUAL_PAYMENT_INSTRUCTIONS}
    return fixed


def source_strings(app):
    """Every English string the student-facing site can show, for translators."""
    package = os.path.dirname(__file__)
    found = _template_strings(app.jinja_env, os.path.join(package, "templates"))
    found |= _python_strings(package)
    # Plural pairs come as two strings; both are keys in the catalog.
    return found | dynamic_strings()


def register_cli(app):
    import click

    @app.cli.command("i18n-update")
    @click.argument("code")
    def i18n_update(code):
        """Add any new English strings to translations/CODE.json (empty values to fill in)."""
        path = os.path.join(os.path.dirname(__file__), "translations", f"{code}.json")
        existing = {}
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                existing = json.load(fh)
        keys = sorted(source_strings(app))
        merged = {k: existing.get(k, "") for k in keys}
        stale = [k for k in existing if k not in merged]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(merged, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
        todo = sum(1 for v in merged.values() if not v)
        click.echo(f"{path}: {len(merged)} strings, {todo} still to translate"
                   + (f", {len(stale)} no longer used were dropped" if stale else ""))

    @app.cli.command("i18n-missing")
    @click.argument("code")
    def i18n_missing(code):
        """List English strings that CODE does not translate yet."""
        catalog = app.extensions["i18n"].get(code, {})
        missing = sorted(k for k in source_strings(app) if k not in catalog)
        for key in missing:
            click.echo(key)
        click.echo(f"{len(missing)} missing", err=True)
