"""DRM video hosting through VdoCipher (https://www.vdocipher.com).

VdoCipher encrypts videos with Widevine/FairPlay DRM. Browsers that support it
show a black frame to screen recorders, and every playback session carries a
per-student watermark burned into the stream.

Flow: our server asks VdoCipher for a short-lived OTP for one video and one
student; the page embeds VdoCipher's player with that OTP. The API secret never
leaves the server.
"""
import json

import requests
from flask import current_app

PLAYER_URL = "https://player.vdocipher.com/v2/"


class VideoError(Exception):
    pass


def vdocipher_enabled():
    return bool(current_app.config.get("VDOCIPHER_API_SECRET"))


def _headers():
    return {
        "Authorization": f"Apisecret {current_app.config['VDOCIPHER_API_SECRET']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _api(path):
    return current_app.config["VDOCIPHER_API_BASE"].rstrip("/") + path


def watermark_for(user):
    """Moving text watermark with the student's identity, burned into playback."""
    text = f"{user.full_name} · {user.email}"
    return json.dumps([{
        "type": "rtext", "text": text, "alpha": "0.55", "color": "0xFFFFFF",
        "size": "14", "interval": "5000",
    }])


def playback_otp(video_id, user):
    """Return {"otp": ..., "playbackInfo": ...} for one student session."""
    body = {"ttl": current_app.config["VDOCIPHER_OTP_TTL"], "annotate": watermark_for(user)}
    try:
        r = requests.post(_api(f"/videos/{video_id}/otp"), headers=_headers(), json=body, timeout=15)
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise VideoError(f"Video service unreachable: {exc}") from exc
    if r.status_code != 200 or "otp" not in data:
        raise VideoError(data.get("message") or f"Video service error ({r.status_code})")
    return {"otp": data["otp"], "playbackInfo": data["playbackInfo"]}


def player_url(otp_data):
    from urllib.parse import urlencode

    return PLAYER_URL + "?" + urlencode({"otp": otp_data["otp"], "playbackInfo": otp_data["playbackInfo"]})


def upload_credentials(title):
    """Create an empty video on VdoCipher and get a one-time upload form.

    Returns {"videoId": ..., "clientPayload": {"uploadLink": ..., "policy": ..., ...}}.
    The browser then POSTs the file straight to uploadLink, so large videos never
    pass through our server.
    """
    try:
        r = requests.put(_api("/videos"), params={"title": title[:100]}, headers=_headers(), timeout=15)
        data = r.json()
    except (requests.RequestException, ValueError) as exc:
        raise VideoError(f"Video service unreachable: {exc}") from exc
    if r.status_code != 200 or "clientPayload" not in data:
        raise VideoError(data.get("message") or f"Video service error ({r.status_code})")
    return {"videoId": data["videoId"], "clientPayload": data["clientPayload"]}


def video_status(video_id):
    """Processing status as reported by VdoCipher (e.g. "ready", "Queued"), or None."""
    try:
        r = requests.get(_api(f"/videos/{video_id}"), headers=_headers(), timeout=15)
        if r.status_code == 200:
            return r.json().get("status")
    except (requests.RequestException, ValueError):
        pass
    return None
