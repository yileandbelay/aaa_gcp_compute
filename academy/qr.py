"""QR codes as inline SVG, made on the server (no images, no JavaScript)."""
import segno
from markupsafe import Markup


def qr_svg(url, title=""):
    """An SVG QR code for ``url``. Black on white with a quiet border, as scanners expect.
    ``title`` is the accessible name. The markup is generated here from our own URL, never from user text."""
    code = segno.make(url, error="m", micro=False)
    return Markup(code.svg_inline(scale=3, border=2, dark="#000000", light="#ffffff", title=title or None,
                                  svgclass="cert-qr-svg", lineclass=None))
