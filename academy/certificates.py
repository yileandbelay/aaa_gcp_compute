"""Certificate PDFs, and finding a learner's certificate for a course.

The PDF is made on the server by WeasyPrint from ``certificate_pdf.html`` (one A4 landscape page, the same look as the screen). WeasyPrint
is optional: it needs the Pango library and a font with Ethiopic letters installed on the server (the installer adds both). Without
it the site still works: the download button is hidden and the certificate page keeps "Print / Save as PDF".
"""
from collections import OrderedDict

from flask import current_app, render_template, request, url_for
from flask_login import current_user

from .models import Certificate

_available = None
_CACHE = OrderedDict()   # a rendered PDF is reused: the certificate never changes unless the holder erases their account
_CACHE_SIZE = 64


def pdf_available():
    """True when WeasyPrint and its system libraries load. Checked once."""
    global _available
    if _available is None:
        try:
            import weasyprint  # noqa: F401 - imported only to see whether it works

            _available = True
        except (ImportError, OSError):   # OSError: the Pango / Cairo libraries are missing
            current_app.logger.warning("WeasyPrint is not available: certificate PDF download is off (Print still works).")
            _available = False
    return _available


def certificate_for(course):
    """The signed-in learner's certificate for ``course``, or None. Used by the pages that show the download buttons."""
    if not current_app.config.get("FEATURE_CERTIFICATES", True) or not current_user.is_authenticated:
        return None
    return Certificate.query.filter_by(user_id=current_user.id, course_id=course.id).first()


def holder_name(cert):
    from .i18n import gettext as _

    return _("Holder details removed on request") if cert.user.deleted_at else cert.user.full_name


def pdf_filename(cert):
    return f"certificate-{cert.code}.pdf"


def render_pdf(cert):
    """The certificate as PDF bytes."""
    from weasyprint import HTML

    from .i18n import current_language

    key = (cert.id, holder_name(cert), cert.course.title, cert.issued_at.isoformat(), current_language())
    if key in _CACHE:
        _CACHE.move_to_end(key)
        return _CACHE[key]
    html = render_template("certificate_pdf.html", cert=cert, holder=holder_name(cert),
                           verify_url=url_for("main.certificate", code=cert.code, _external=True))
    pdf = HTML(string=html, base_url=request.url_root).write_pdf()
    _CACHE[key] = pdf
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return pdf
