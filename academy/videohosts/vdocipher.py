"""VdoCipher DRM hosting (https://www.vdocipher.com).

Videos are encrypted with Widevine/FairPlay DRM and the viewer's name and email
are burned into the stream. Our server asks VdoCipher for a short-lived OTP for
one video and one student; the API secret never leaves the server.
"""
import json
import re
from urllib.parse import urlencode

import requests

from .base import VideoError, VideoProvider

PLAYER_URL = "https://player.vdocipher.com/v2/"


class VdoCipher(VideoProvider):
    key = "vdocipher"
    label = "VdoCipher (DRM, encrypted)"
    help = "The video ID shown in the VdoCipher dashboard, or upload below."
    ref_pattern = re.compile(r"^[A-Za-z0-9]{8,64}$")
    burned_watermark = True

    def enabled(self):
        return bool(self.config.get("VDOCIPHER_API_SECRET"))

    def _headers(self):
        return {
            "Authorization": f"Apisecret {self.config['VDOCIPHER_API_SECRET']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _api(self, path):
        return self.config["VDOCIPHER_API_BASE"].rstrip("/") + path

    @staticmethod
    def watermark_for(user):
        """Moving text watermark with the student's identity, burned into playback."""
        return json.dumps([{
            "type": "rtext", "text": f"{user.full_name} · {user.email}", "alpha": "0.55",
            "color": "0xFFFFFF", "size": "14", "interval": "5000",
        }])

    def embed_url(self, ref, user):
        body = {"ttl": self.config["VDOCIPHER_OTP_TTL"], "annotate": self.watermark_for(user)}
        try:
            r = requests.post(self._api(f"/videos/{ref}/otp"), headers=self._headers(), json=body, timeout=15)
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            raise VideoError(f"Video service unreachable: {exc}") from exc
        if r.status_code != 200 or "otp" not in data:
            raise VideoError(data.get("message") or f"Video service error ({r.status_code})")
        return PLAYER_URL + "?" + urlencode({"otp": data["otp"], "playbackInfo": data["playbackInfo"]})

    def upload_info(self, title):
        try:
            r = requests.put(self._api("/videos"), params={"title": title[:100]}, headers=self._headers(),
                             timeout=15)
            data = r.json()
        except (requests.RequestException, ValueError) as exc:
            raise VideoError(f"Video service unreachable: {exc}") from exc
        if r.status_code != 200 or "clientPayload" not in data:
            raise VideoError(data.get("message") or f"Video service error ({r.status_code})")
        return {"video_ref": data["videoId"], "method": "form", "clientPayload": data["clientPayload"]}

    def status(self, ref):
        try:
            r = requests.get(self._api(f"/videos/{ref}"), headers=self._headers(), timeout=15)
            if r.status_code == 200:
                return r.json().get("status")
        except (requests.RequestException, ValueError):
            pass
        return None
