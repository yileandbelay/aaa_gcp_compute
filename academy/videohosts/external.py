"""Videos that live on YouTube or Vimeo: the admin pastes a link, we embed the player.

There is nothing to configure and nothing to upload. The video is as protected as it is
on that site, so use it for free or public content; for paid lessons prefer Bunny Stream
or VdoCipher. YouTube links use the privacy-friendly youtube-nocookie.com player.
"""
import re
from urllib.parse import parse_qs, urlparse

from .base import VideoProvider

YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
VIMEO_ID = re.compile(r"^\d{5,12}$")
STORED = re.compile(r"^(yt:[A-Za-z0-9_-]{11}|vimeo:\d{5,12})$")


def parse_video_url(text):
    """Return "yt:<id>" or "vimeo:<id>" for a YouTube/Vimeo link or bare ID, else None."""
    text = (text or "").strip()
    if STORED.match(text):
        return text
    if YOUTUBE_ID.match(text):
        return f"yt:{text}"
    if not re.match(r"^https?://", text, re.I):
        text = "https://" + text
    try:
        url = urlparse(text)
    except ValueError:
        return None
    host = (url.hostname or "").lower().removeprefix("www.").removeprefix("m.")
    parts = [p for p in url.path.split("/") if p]
    if host in ("youtube.com", "youtube-nocookie.com", "music.youtube.com"):
        vid = None
        if parts[:1] == ["watch"]:
            vid = (parse_qs(url.query).get("v") or [None])[0]
        elif parts[:1] in (["embed"], ["shorts"], ["live"], ["v"]) and len(parts) > 1:
            vid = parts[1]
        return f"yt:{vid}" if vid and YOUTUBE_ID.match(vid) else None
    if host == "youtu.be" and parts and YOUTUBE_ID.match(parts[0]):
        return f"yt:{parts[0]}"
    if host in ("vimeo.com", "player.vimeo.com"):
        vid = next((p for p in parts if VIMEO_ID.match(p)), None)
        return f"vimeo:{vid}" if vid else None
    return None


class External(VideoProvider):
    key = "external"
    label = "YouTube or Vimeo link"
    help = "Paste the video's web address, for example https://www.youtube.com/watch?v=..."
    ref_pattern = STORED
    burned_watermark = False
    overlay_watermark = False  # public videos: no point watermarking them

    def enabled(self):
        return True

    def normalize_ref(self, text):
        ref = parse_video_url(text)
        if not ref:
            raise ValueError("That is not a YouTube or Vimeo video link.")
        return ref

    def display_ref(self, ref):
        kind, _, vid = (ref or "").partition(":")
        if kind == "yt":
            return f"https://www.youtube.com/watch?v={vid}"
        if kind == "vimeo":
            return f"https://vimeo.com/{vid}"
        return ref or ""

    def embed_url(self, ref, user):
        kind, _, vid = ref.partition(":")
        if kind == "yt":
            return f"https://www.youtube-nocookie.com/embed/{vid}?rel=0"
        if kind == "vimeo":
            return f"https://player.vimeo.com/video/{vid}"
        raise ValueError("Unknown video reference")
