"""Emails to a learner about their own account and progress: a welcome after registering, and the certificate.

They are written in the language the learner is using on the site (English or Amharic) and never raise: a failed email
must not stop a sign-up or the last lesson of a course. What happened to each one is in Admin > Email.
"""
from flask import current_app, url_for

from .i18n import gettext as _
from .mail import send_email


def welcome(user):
    site = _(current_app.config["SITE_NAME"])
    lines = [
        _("Hello %(name)s,", name=user.full_name), "",
        _("Your account is ready. You can now browse courses and enroll."), "",
        _("Browse courses: %(url)s", url=url_for("main.catalog", _external=True)),
        _("Sign in: %(url)s", url=url_for("auth.login", _external=True)), "",
        _("If you did not create this account, please ignore this email."), site,
    ]
    send_email(user.email, _("Welcome to %(site)s", site=site), "\n".join(lines), kind="welcome")


def certificate_issued(cert):
    user, course = cert.user, cert.course
    site = _(current_app.config["SITE_NAME"])
    lines = [
        _("Congratulations, %(name)s!", name=user.full_name), "",
        _("You have completed %(course)s. Your certificate of completion is ready.", course=course.title), "",
        _("View and download it here: %(url)s", url=url_for("main.certificate", code=cert.code, _external=True)),
        _("Anyone can check it with the certificate ID %(code)s at the same address.", code=cert.code), "", site,
    ]
    send_email(user.email, _("Your certificate: %(course)s", course=course.title), "\n".join(lines), kind="certificate")


def safely(notice, *args):
    """Run one of the notices above; an email problem is logged, never shown to the learner as an error."""
    try:
        notice(*args)
    except Exception:  # noqa: BLE001
        current_app.logger.exception("Could not send the %s email", notice.__name__)
