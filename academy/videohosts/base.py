"""Interface every hosted-video provider implements.

To add a provider (Vimeo, Mux, Cloudflare Stream...): subclass ``VideoProvider``
in a new module, set the three class attributes, implement the methods, and
register an instance in ``videohosts/__init__.py``. The lesson editor, the
lesson page and the upload button pick it up automatically.
"""
import re

from flask import current_app


class VideoError(Exception):
    """A video service refused or could not be reached. The message is safe to log."""


class VideoProvider:
    key = ""            # stored in Lesson.video_provider
    label = ""          # shown in the lesson editor
    help = ""           # one-line help under the video ID field
    ref_pattern = re.compile(r"^[A-Za-z0-9-]{8,64}$")  # what a valid video ID looks like
    burned_watermark = False  # True if the host burns the viewer's identity into the stream
    overlay_watermark = True  # False for hosts whose videos are public anyway (YouTube)

    @property
    def config(self):
        return current_app.config

    def enabled(self):
        """True when this provider's credentials are configured."""
        raise NotImplementedError

    def valid_ref(self, ref):
        return bool(self.ref_pattern.match(ref or ""))

    def normalize_ref(self, text):
        """Turn what an admin typed (an ID, or a pasted URL) into the stored reference.
        Raise ValueError if it isn't valid."""
        text = (text or "").strip()
        if not self.valid_ref(text):
            raise ValueError(f"That does not look like a {self.label} video ID.")
        return text

    def display_ref(self, ref):
        """What to show in the editor for a stored reference."""
        return ref

    def embed_url(self, ref, user):
        """URL for the iframe player, personalised for ``user``."""
        raise NotImplementedError

    def upload_info(self, title):
        """Create an empty video and return what the browser needs to upload to it.

        Return a dict with ``video_ref`` (the new video's ID), ``method`` ("form" or
        "tus") and the method-specific fields. Never include a secret API key.
        """
        raise NotImplementedError

    def status(self, ref):
        """Short processing status for the admin (e.g. "ready"), or None if unknown."""
        return None
