"""Minimal email sending over SMTP (works with Gmail/Google Workspace, SendGrid, Mailgun...)."""
import smtplib
from email.message import EmailMessage

from flask import current_app


def send_email(to, subject, body, reply_to=None):
    """Send a plain-text email. Returns True if handed to the SMTP server.

    Without MAIL_SERVER configured (local development) the message is logged
    instead, so links such as password resets can still be followed.
    """
    cfg = current_app.config
    if not cfg.get("MAIL_SERVER"):
        current_app.logger.warning("Email not configured; would send to %s: %s\n%s", to, subject, body)
        return False
    msg = EmailMessage()
    msg["From"] = cfg["MAIL_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    try:
        with smtplib.SMTP(cfg["MAIL_SERVER"], cfg["MAIL_PORT"], timeout=15) as smtp:
            if cfg["MAIL_USE_TLS"]:
                smtp.starttls()
            if cfg["MAIL_USERNAME"]:
                smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
            smtp.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        current_app.logger.error("Failed to send email to %s: %s", to, exc)
        return False
