"""Settings an admin can change without touching the server (see models.SiteSetting)."""
from flask import current_app

from .i18n import current_language, gettext as _
from .models import SiteSetting, db

PAYMENT_INSTRUCTIONS = "payment_instructions"          # shown in every language unless a translation is set
PAYMENT_INSTRUCTIONS_AM = "payment_instructions_am"    # optional Amharic version


def get_setting(key, default=""):
    row = db.session.get(SiteSetting, key)
    return row.value if row and row.value else default


def set_setting(key, value):
    value = (value or "").strip()
    row = db.session.get(SiteSetting, key)
    if not value:
        if row:
            db.session.delete(row)
        return
    if row:
        row.value = value
    else:
        db.session.add(SiteSetting(key=key, value=value))


def payment_instructions():
    """``(text, is_sample)``: how to pay offline, in the visitor's language when an admin wrote one.
    ``is_sample`` is True while nobody has entered real bank or Telebirr details."""
    if current_language() == "am":
        text = get_setting(PAYMENT_INSTRUCTIONS_AM)
        if text:
            return text, False
    text = get_setting(PAYMENT_INSTRUCTIONS)
    if text:
        return text, False
    from .config import Config

    configured = current_app.config["MANUAL_PAYMENT_INSTRUCTIONS"]
    sample = configured == Config.MANUAL_PAYMENT_INSTRUCTIONS or "XXXX" in configured
    return _(Config.MANUAL_PAYMENT_INSTRUCTIONS if sample else configured), sample
