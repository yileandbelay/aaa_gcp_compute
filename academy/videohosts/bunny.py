"""Bunny Stream hosting (https://bunny.net/stream/).

- Playback: Bunny's iframe player. When "Embed view token authentication" is on for
  the library, each embed URL needs ``token`` and ``expires``; we sign them per student
  (SHA256 of token key + video ID + expiry). Without it the player works unsigned,
  so turn token authentication on in the library's Security settings.
- Upload: the browser uploads straight to Bunny with the TUS resumable protocol.
  Our server creates the (empty) video and signs the upload, so the library API key
  never reaches the browser.
- Bunny does not burn a per-student watermark into the video, so the lesson page
  overlays the student's email instead (see VIDEO_WATERMARK).
"""
import hashlib
import re
import time

import requests

from .base import VideoError, VideoProvider

API_BASE = "https://video.bunnycdn.com"
EMBED_BASE = "https://iframe.mediadelivery.net/embed"
TUS_ENDPOINT = "https://video.bunnycdn.com/tusupload"

# Processing states as reported by the Bunny API; unknown values are shown as-is.
STATUS_LABELS = {0: "created", 1: "uploaded", 2: "processing", 3: "transcoding", 4: "ready",
                 5: "error", 6: "upload failed"}


def sha256_hex(text):
    return hashlib.sha256(text.encode()).hexdigest()


class Bunny(VideoProvider):
    key = "bunny"
    label = "Bunny Stream"
    help = "The video ID (a GUID) shown in your Bunny library, or upload below."
    ref_pattern = re.compile(r"^[0-9a-fA-F-]{32,40}$")

    def enabled(self):
        return bool(self.config.get("BUNNY_LIBRARY_ID") and self.config.get("BUNNY_API_KEY"))

    def _headers(self):
        return {"AccessKey": self.config["BUNNY_API_KEY"], "Accept": "application/json",
                "Content-Type": "application/json"}

    def embed_url(self, ref, user):
        url = f"{EMBED_BASE}/{self.config['BUNNY_LIBRARY_ID']}/{ref}"
        token_key = self.config.get("BUNNY_TOKEN_KEY")
        if not token_key:
            return url
        expires = int(time.time()) + self.config["BUNNY_TOKEN_TTL"]
        return f"{url}?token={sha256_hex(token_key + ref + str(expires))}&expires={expires}"

    def upload_info(self, title):
        library = str(self.config["BUNNY_LIBRARY_ID"])
        try:
            r = requests.post(f"{API_BASE}/library/{library}/videos", headers=self._headers(),
                              json={"title": title[:100]}, timeout=15)
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            raise VideoError(f"Video service unreachable: {exc}") from exc
        if r.status_code != 200 or not data.get("guid"):
            raise VideoError(data.get("Message") or data.get("message") or f"Video service error ({r.status_code})")
        guid = data["guid"]
        expire = int(time.time()) + 6 * 3600  # long enough for a big file on a slow connection
        signature = sha256_hex(library + self.config["BUNNY_API_KEY"] + str(expire) + guid)
        return {
            "video_ref": guid, "method": "tus", "endpoint": TUS_ENDPOINT,
            "headers": {"AuthorizationSignature": signature, "AuthorizationExpire": str(expire),
                        "VideoId": guid, "LibraryId": library},
        }

    def status(self, ref):
        try:
            r = requests.get(f"{API_BASE}/library/{self.config['BUNNY_LIBRARY_ID']}/videos/{ref}",
                             headers=self._headers(), timeout=15)
            if r.status_code == 200:
                code = r.json().get("status")
                return STATUS_LABELS.get(code, f"status {code}")
        except (requests.RequestException, ValueError):
            pass
        return None
