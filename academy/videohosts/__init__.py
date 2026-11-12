"""Hosted video providers. Register new ones in PROVIDERS."""
from .base import VideoError, VideoProvider  # noqa: F401
from .bunny import Bunny
from .external import External
from .vdocipher import VdoCipher

PROVIDERS = {p.key: p for p in (Bunny(), VdoCipher(), External())}


def get(key):
    """The provider with this key, or None (e.g. for "local")."""
    return PROVIDERS.get(key)


def enabled_providers():
    return [p for p in PROVIDERS.values() if p.enabled()]


def uploading_providers():
    """Enabled providers that accept uploads from the lesson editor (not just pasted links)."""
    return [p for p in enabled_providers() if p.key != "external"]
