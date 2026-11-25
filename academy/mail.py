"""Email over SMTP (Gmail / Google Workspace, Namecheap Private Email, SendGrid, Mailgun, ...), with a log of every attempt.

Why a log: an email that does not arrive is invisible. Every send is recorded in the ``email_log`` table (who, what kind,
sent / failed / not configured, and a plain-language reason). Admin > Email shows it and can send a test message.

How a send works:
* Without MAIL_SERVER (local development) nothing is sent; the message is logged and recorded as "not configured".
* Port 465 (or MAIL_USE_SSL=true) uses implicit TLS; otherwise MAIL_USE_TLS=true upgrades the connection with STARTTLS.
* By default sending happens in a background thread (MAIL_ASYNC), so a slow mail server never slows a sign-up or a payment page.
  Temporary failures (timeouts, 4xx replies) are retried; permanent ones (wrong password, refused sender) are not.
"""
import smtplib
import socket
import ssl
import threading
import time
from datetime import timedelta
from email.message import EmailMessage

from flask import current_app
from sqlalchemy.orm import Session

from .models import EmailLog, db, utcnow

RETRY_DELAYS = (5, 25)      # seconds to wait before the 2nd and 3rd attempt (background sending only)
LOG_KEEP_DAYS = 90


def mail_configured():
    return bool(current_app.config.get("MAIL_SERVER"))


def uses_ssl(cfg):
    """Implicit TLS (port 465) versus plain connection that may upgrade with STARTTLS."""
    value = str(cfg.get("MAIL_USE_SSL", "")).strip().lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    return int(cfg["MAIL_PORT"]) == 465


def security_label(cfg):
    if uses_ssl(cfg):
        return "SSL (implicit TLS)"
    return "STARTTLS" if cfg.get("MAIL_USE_TLS") else "none (not encrypted)"


def explain(exc, cfg):
    """A plain-language reason for a failed send, with what to check."""
    host, port = cfg.get("MAIL_SERVER"), cfg.get("MAIL_PORT")
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return ("The mail server rejected the username or password. Check MAIL_USERNAME and MAIL_PASSWORD "
                "(with Gmail or Google Workspace use an app password, not the account password).")
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return (f"The mail server refused the sender address {cfg.get('MAIL_FROM')}. The From address must belong to the "
                "mailbox or domain you log in with (MAIL_FROM).")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return "The mail server refused the recipient address (it may not exist)."
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return (f"Timed out talking to {host}:{port}. Check MAIL_SERVER and MAIL_PORT, and that this server may connect "
                "outwards on that port (some hosts block port 25; use 587 or 465).")
    if isinstance(exc, (socket.gaierror,)):
        return f"The mail server name {host} was not found. Check MAIL_SERVER."
    if isinstance(exc, ConnectionRefusedError):
        return f"Connection to {host}:{port} was refused. Check MAIL_SERVER and MAIL_PORT."
    if isinstance(exc, ssl.SSLCertVerificationError):
        return (f"The certificate of {host} could not be verified ({exc.verify_message}). Use the host name printed on the "
                "certificate as MAIL_SERVER, or set MAIL_VERIFY_TLS=false if you accept the risk.")
    if isinstance(exc, (ssl.SSLError, smtplib.SMTPServerDisconnected)):
        return (f"The secure connection to {host}:{port} failed. Port 465 needs MAIL_USE_SSL=true; port 587 needs "
                "MAIL_USE_TLS=true and MAIL_USE_SSL=false.")
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return "The mail server does not support this login or encryption mode. Check MAIL_USE_TLS / MAIL_USE_SSL."
    return f"{type(exc).__name__}: {exc}"


def is_temporary(exc):
    """Worth trying again: connection trouble and 4xx replies. Wrong passwords and refused senders are not."""
    if isinstance(exc, (smtplib.SMTPAuthenticationError, smtplib.SMTPSenderRefused, smtplib.SMTPRecipientsRefused,
                        smtplib.SMTPNotSupportedError, ssl.SSLCertVerificationError)):
        return False
    if isinstance(exc, smtplib.SMTPResponseException):
        return 400 <= exc.smtp_code < 500
    return isinstance(exc, (OSError, smtplib.SMTPException))


def configuration_problems(cfg):
    """Plain-language problems found in the email settings, before anything is sent."""
    problems = []
    if not cfg.get("MAIL_SERVER"):
        return ["MAIL_SERVER is empty, so the site sends no email at all. Set the MAIL_* settings in /etc/academy.env and restart."]
    sender = (cfg.get("MAIL_FROM") or "").lower()
    if "localhost" in sender or "example.com" in sender:
        problems.append("MAIL_FROM is still an example address. Use an address at your own domain, for example "
                        "\"Mek Academy <no-reply@your-domain>\".")
    if not cfg.get("MAIL_USERNAME") or not cfg.get("MAIL_PASSWORD"):
        problems.append("MAIL_USERNAME or MAIL_PASSWORD is empty. Most mail services refuse to send without logging in.")
    if int(cfg["MAIL_PORT"]) == 465 and not uses_ssl(cfg):
        problems.append("Port 465 needs MAIL_USE_SSL=true.")
    if int(cfg["MAIL_PORT"]) == 587 and uses_ssl(cfg):
        problems.append("Port 587 uses STARTTLS: set MAIL_USE_SSL=false and MAIL_USE_TLS=true.")
    if int(cfg["MAIL_PORT"]) == 25:
        problems.append("Port 25 is blocked by many hosting providers. Prefer 587 or 465.")
    return problems


def _build(to, subject, body, reply_to):
    msg = EmailMessage()
    msg["From"] = current_app.config["MAIL_FROM"]
    msg["To"] = to
    msg["Subject"] = subject
    if reply_to:
        msg["Reply-To"] = reply_to
    msg.set_content(body)
    return msg


def _smtp_send(cfg, msg):
    host, port, timeout = cfg["MAIL_SERVER"], int(cfg["MAIL_PORT"]), cfg["MAIL_TIMEOUT"]
    verify = str(cfg.get("MAIL_VERIFY_TLS", "true")).strip().lower() not in ("0", "false", "no", "off")
    context = ssl.create_default_context()
    if not verify:
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    if uses_ssl(cfg):
        smtp = smtplib.SMTP_SSL(host, port, timeout=timeout, context=context)
    else:
        smtp = smtplib.SMTP(host, port, timeout=timeout)
    with smtp:
        if not uses_ssl(cfg) and cfg["MAIL_USE_TLS"]:
            smtp.starttls(context=context)
        if cfg["MAIL_USERNAME"]:
            smtp.login(cfg["MAIL_USERNAME"], cfg["MAIL_PASSWORD"])
        smtp.send_message(msg)


def _record(to, subject, kind, status, error=None, attempts=1):
    """Write one log row on its own connection, so it never commits or rolls back the caller's work. Never raises."""
    try:
        with Session(db.engine) as session:
            session.add(EmailLog(to_email=to[:255], subject=(subject or "")[:300], kind=kind, status=status,
                                 error=(error or None) and error[:500], attempts=attempts))
            session.query(EmailLog).filter(EmailLog.created_at < utcnow() - timedelta(days=LOG_KEEP_DAYS)).delete()
            session.commit()
    except Exception:  # noqa: BLE001 - logging must never break sending
        current_app.logger.exception("Could not record the email log entry for %s", to)


def _deliver(msg, to, subject, kind, retries):
    """Send with up to ``retries`` extra attempts for temporary failures. Records the outcome. Returns True if sent."""
    cfg = current_app.config
    attempts, error = 0, None
    delays = (None,) + tuple(RETRY_DELAYS[:retries])
    for delay in delays:
        if delay:
            time.sleep(delay)
        attempts += 1
        try:
            _smtp_send(cfg, msg)
            _record(to, subject, kind, "sent", attempts=attempts)
            return True
        except Exception as exc:  # noqa: BLE001 - smtplib raises many kinds; all are reported
            error = explain(exc, cfg)
            current_app.logger.error("Email to %s failed (attempt %d): %s | %s", to, attempts, error, exc)
            if not is_temporary(exc):
                break
    _record(to, subject, kind, "failed", error=error, attempts=attempts)
    return False


def _background(app, msg, to, subject, kind):
    with app.app_context():
        _deliver(msg, to, subject, kind, retries=len(RETRY_DELAYS))


def send_email(to, subject, body, reply_to=None, kind="other", wait=False):
    """Send a plain-text email.

    Returns True if it was sent (``wait=True``) or handed to the background sender (default); False if email is not
    configured or the send failed. Pass ``wait=True`` where the screen needs to know the real outcome.
    """
    cfg = current_app.config
    if not cfg.get("MAIL_SERVER"):
        current_app.logger.warning("Email not configured; would send to %s: %s\n%s", to, subject, body)
        _record(to, subject, kind, "not_configured")
        return False
    msg = _build(to, subject, body, reply_to)
    if cfg.get("MAIL_ASYNC") and not wait:
        threading.Thread(target=_background, args=(current_app._get_current_object(), msg, to, subject, kind),
                         daemon=True).start()
        return True
    return _deliver(msg, to, subject, kind, retries=0)


def mail_health():
    """For the admin dashboard: is email set up, and did the last few sends work?"""
    if not mail_configured():
        return {"configured": False, "failed": 0, "recent": 0, "last_error": None}
    recent = EmailLog.query.filter(EmailLog.kind != "test").order_by(EmailLog.id.desc()).limit(10).all()
    failed = [r for r in recent if r.status == "failed"]
    return {"configured": True, "failed": len(failed), "recent": len(recent),
            "last_error": failed[0].error if failed else None}
