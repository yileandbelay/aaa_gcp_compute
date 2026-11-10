"""Application configuration, read from environment variables."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))


def _bool(name, default=False):
    return os.environ.get(name, str(default)).lower() in ("1", "true", "yes", "on")


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-change-me")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(BASE_DIR, "instance", "kasina.db")
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Uploaded lecture videos and payment receipts live OUTSIDE the static
    # folder so they can only be reached through access-checked routes.
    UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(BASE_DIR, "instance", "uploads"))
    MAX_CONTENT_LENGTH = int(os.environ.get("MAX_UPLOAD_MB", "1024")) * 1024 * 1024
    ALLOWED_VIDEO_EXT = {"mp4", "webm", "ogg", "mov", "m4v"}
    ALLOWED_RECEIPT_EXT = {"png", "jpg", "jpeg", "pdf"}

    # How long a signed video URL stays valid (seconds).
    VIDEO_TOKEN_TTL = int(os.environ.get("VIDEO_TOKEN_TTL", "7200"))

    SITE_NAME = os.environ.get("SITE_NAME", "Kasina Cakery Academy")
    CURRENCY = os.environ.get("CURRENCY", "ETB")

    # Payments --------------------------------------------------------------
    # Chapa (https://chapa.co) online payments. Leave empty to disable.
    CHAPA_SECRET_KEY = os.environ.get("CHAPA_SECRET_KEY", "")
    CHAPA_API_BASE = os.environ.get("CHAPA_API_BASE", "https://api.chapa.co/v1")
    # Manual bank transfer / mobile money: student uploads a receipt, admin approves.
    MANUAL_PAYMENT_ENABLED = _bool("MANUAL_PAYMENT_ENABLED", True)
    MANUAL_PAYMENT_INSTRUCTIONS = os.environ.get(
        "MANUAL_PAYMENT_INSTRUCTIONS",
        "Transfer the course fee to Kasina Cakery, Account No. 1000XXXXXXXX "
        "(Commercial Bank of Ethiopia) or via telebirr, then upload your receipt below.",
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


class TestConfig(Config):
    TESTING = True
    SECRET_KEY = "test"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    PAYMENT_DEMO_MODE = True
    WTF_CSRF_ENABLED = False
    CSRF_ENABLED = False
