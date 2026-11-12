"""Application configuration, read from environment variables."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


def _bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "instance", "academy.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Uploaded lecture videos and payment receipts live OUTSIDE the static
    # folder so they can only be reached through access-checked routes.
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "instance", "uploads"))
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "1024")) * 1024 * 1024
    ALLOWED_VIDEO_EXT = {"mp4", "webm", "ogg", "mov", "m4v"}
    ALLOWED_RECEIPT_EXT = {"png", "jpg", "jpeg", "pdf"}
    ALLOWED_MATERIAL_EXT = {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "csv", "txt", "zip",
                            "png", "jpg", "jpeg"}

    # Optional modules ------------------------------------------------------
    FEATURE_DISCUSSION = _bool("FEATURE_DISCUSSION", True)  # per-lesson Q&A
    FEATURE_ANNOUNCEMENTS = _bool("FEATURE_ANNOUNCEMENTS", True)
    FEATURE_CERTIFICATES = _bool("FEATURE_CERTIFICATES", True)
    FEATURE_REVIEWS = _bool("FEATURE_REVIEWS", True)  # star ratings and feedback on courses

    # Content protection (tune to how strict you want to be) ----------------
    # Block right-click / copy on lesson text.
    PROTECT_LESSON_TEXT = _bool("PROTECT_LESSON_TEXT", False)
    # Float the student's email over locally hosted videos.
    VIDEO_WATERMARK = _bool("VIDEO_WATERMARK", True)
    # Show the browser's download button on locally hosted videos.
    ALLOW_VIDEO_DOWNLOAD = _bool("ALLOW_VIDEO_DOWNLOAD", False)
    # Let students download lesson materials (handouts, slides).
    ALLOW_MATERIAL_DOWNLOAD = _bool("ALLOW_MATERIAL_DOWNLOAD", True)

    # How long a signed video URL stays valid (seconds).
    VIDEO_TOKEN_TTL = int(os.environ.get("VIDEO_TOKEN_TTL", "7200"))

    # Branding ---------------------------------------------------------------
    # Languages offered on the site (codes of translation files in academy/translations/).
    LANGUAGES = [c.strip() for c in os.environ.get("LANGUAGES", "en,am").split(",") if c.strip()]
    DEFAULT_LANGUAGE = os.environ.get("DEFAULT_LANGUAGE", "en")
    # Use the visitor's browser language on their first visit (they can always switch).
    LANGUAGE_AUTODETECT = _bool("LANGUAGE_AUTODETECT", True)
    # The first default was "Mek Online Academy"; servers installed then still carry it in their settings file.
    SITE_NAME = os.environ.get("SITE_NAME") if os.environ.get("SITE_NAME") not in (None, "", "Mek Online Academy") else "Mek Academy"
    # Short name (max 16 characters) used where space is limited, e.g. the Chapa checkout title.
    SITE_SHORT_NAME = os.environ.get("SITE_SHORT_NAME", "Mek Academy")[:16]
    SITE_TAGLINE = os.environ.get("SITE_TAGLINE", "Professional training you can take anywhere.")
    SITE_HEADLINE = os.environ.get("SITE_HEADLINE", "Build skills that move your career forward.")
    SITE_INTRO = os.environ.get(
        "SITE_INTRO",
        "Practical, professional training taught by experienced instructors. Watch lessons, download "
        "materials, test yourself and earn a certificate - on your phone or computer.",
    )
    # Main colour of buttons, links and highlights (any CSS colour).
    # Optional override of the main colour (buttons, links). Empty = the built-in emerald.
    BRAND_COLOR = os.environ.get("BRAND_COLOR", "")
    # Optional folder with "templates/" and/or "static/" sub-folders whose files
    # override the built-in ones (e.g. themes/my-academy). Lets one codebase run
    # several differently branded academies without editing the core.
    THEME_DIR = os.environ.get("THEME_DIR", "")
    # Prefix for payment references shown on receipts (e.g. MEK-1a2b3c...).
    TX_PREFIX = os.environ.get("TX_PREFIX", "MEK")
    CURRENCY = os.environ.get("CURRENCY", "ETB")

    # Payments --------------------------------------------------------------
    # Chapa (https://chapa.co) online payments. Leave empty to disable.
    CHAPA_SECRET_KEY = os.environ.get("CHAPA_SECRET_KEY", "")
    CHAPA_API_BASE = os.environ.get("CHAPA_API_BASE", "https://api.chapa.co/v1")
    # Manual bank transfer / mobile money: student uploads a receipt, admin approves.
    MANUAL_PAYMENT_ENABLED = _bool("MANUAL_PAYMENT_ENABLED", True)
    MANUAL_PAYMENT_INSTRUCTIONS = os.environ.get(
        "MANUAL_PAYMENT_INSTRUCTIONS",
        "Transfer the course fee to our bank account or via mobile money, then upload your "
        "receipt below. (Set MANUAL_PAYMENT_INSTRUCTIONS to show your real account details.)",
    )
    # Demo mode marks payments as paid instantly. NEVER enable in production.
    PAYMENT_DEMO_MODE = _bool("PAYMENT_DEMO_MODE", False)

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    REMEMBER_COOKIE_HTTPONLY = True
    # Set true when running behind nginx / a load balancer so generated
    # external URLs (payment callbacks, certificates) use the right scheme/host.
    BEHIND_PROXY = _bool("BEHIND_PROXY", False)

    # Video -----------------------------------------------------------------
    # VdoCipher DRM hosting (recommended for paid content). Leave empty to use
    # only locally stored videos.
    VDOCIPHER_API_SECRET = os.environ.get("VDOCIPHER_API_SECRET", "")
    VDOCIPHER_API_BASE = os.environ.get("VDOCIPHER_API_BASE", "https://dev.vdocipher.com/api")
    VDOCIPHER_OTP_TTL = int(os.environ.get("VDOCIPHER_OTP_TTL", "300"))
    # Bunny Stream (https://bunny.net/stream/). Library ID and the library's API key are on
    # the library's "API" page; the token key is on its "Security" page (enable
    # "Embed view token authentication" there). Token key is optional but recommended.
    BUNNY_LIBRARY_ID = os.environ.get("BUNNY_LIBRARY_ID", "")
    BUNNY_API_KEY = os.environ.get("BUNNY_API_KEY", "")
    BUNNY_TOKEN_KEY = os.environ.get("BUNNY_TOKEN_KEY", "")
    BUNNY_TOKEN_TTL = int(os.environ.get("BUNNY_TOKEN_TTL", "14400"))
    # When nginx serves local videos (see deploy/nginx.conf), the app only checks
    # access and hands the file off with X-Accel-Redirect - far more concurrent viewers.
    USE_X_ACCEL_REDIRECT = _bool("USE_X_ACCEL_REDIRECT", False)

    # Email (password reset, payment notifications) --------------------------
    # Without MAIL_SERVER, emails are written to the log instead of sent.
    MAIL_SERVER = os.environ.get("MAIL_SERVER", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USERNAME = os.environ.get("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_USE_TLS = _bool("MAIL_USE_TLS", True)
    MAIL_FROM = os.environ.get("MAIL_FROM", "Mek Academy <no-reply@localhost>")
    PASSWORD_RESET_TTL = int(os.environ.get("PASSWORD_RESET_TTL", "3600"))

    # Login throttling: block after this many failures in the window.
    LOGIN_MAX_FAILURES = int(os.environ.get("LOGIN_MAX_FAILURES", "5"))
    LOGIN_WINDOW_MIN = int(os.environ.get("LOGIN_WINDOW_MIN", "15"))

    # Contact details shown on policy pages.
    CONTACT_EMAIL = os.environ.get("CONTACT_EMAIL", "info@example.com")
    CONTACT_PHONE = os.environ.get("CONTACT_PHONE", "+251 9XX XXX XXX")
    # Shown on the Contact page when set.
    CONTACT_ADDRESS = os.environ.get("CONTACT_ADDRESS", "")
    CONTACT_HOURS = os.environ.get("CONTACT_HOURS", "")
    CONTACT_WHATSAPP = os.environ.get("CONTACT_WHATSAPP", "")  # number with country code, e.g. 2519XXXXXXXX
    CONTACT_TELEGRAM = os.environ.get("CONTACT_TELEGRAM", "")  # username without @
    # Message form on the Contact page: messages are saved for admins and emailed to CONTACT_EMAIL.
    CONTACT_FORM = _bool("CONTACT_FORM", True)
    CONTACT_MAX_PER_HOUR = int(os.environ.get("CONTACT_MAX_PER_HOUR", "5"))  # per IP address
    # How long a "set your password" link for a new staff account stays valid (seconds).
    INVITE_TTL = int(os.environ.get("INVITE_TTL", str(7 * 24 * 3600)))


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    PAYMENT_DEMO_MODE = True
    WTF_CSRF_ENABLED = False
    CSRF_ENABLED = False
