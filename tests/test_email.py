"""Email: every send is logged with a plain reason; port 465 works; failures are retried only when temporary; the welcome and
certificate emails exist; and the admin can see and test it all. SMTP is replaced by a fake that records what happens."""
import smtplib
import socket
import threading

import pytest

from academy import create_app, mail
from academy.config import TestConfig
from academy.enrollment import enroll_student
from academy.models import Course, EmailLog, Lesson, User, db
from academy.seed import seed


class FakeSMTP:
    """Stands in for smtplib.SMTP and SMTP_SSL: records the connection and the messages."""

    instances = []
    failures = []          # exceptions to raise on successive send_message calls (consumed in order)
    login_error = None
    ssl = False

    def __init__(self, host, port, timeout=None, context=None):
        self.host, self.port, self.timeout = host, port, timeout
        self.starttls_called, self.logged_in, self.sent = False, None, []
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def starttls(self, context=None):
        self.starttls_called = True

    def login(self, user, password):
        if type(self).login_error:
            raise type(self).login_error
        self.logged_in = (user, password)

    def send_message(self, msg):
        if type(self).failures:
            raise type(self).failures.pop(0)
        self.sent.append(msg)


class FakeSMTPSSL(FakeSMTP):
    ssl = True


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch):
    FakeSMTP.instances, FakeSMTP.failures, FakeSMTP.login_error = [], [], None
    monkeypatch.setattr(mail.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(mail.smtplib, "SMTP_SSL", FakeSMTPSSL)
    monkeypatch.setattr(mail, "RETRY_DELAYS", (0, 0))
    return FakeSMTP


def make_app(tmp_path, **settings):
    class Cfg(TestConfig):
        UPLOAD_DIR = str(tmp_path / "uploads")
        MAIL_SERVER = "smtp.test"
        MAIL_PORT = 587
        MAIL_USERNAME = "no-reply@mek-academy.test"
        MAIL_PASSWORD = "app-password"
        MAIL_FROM = "Mek Academy <no-reply@mek-academy.test>"

    for key, value in settings.items():
        setattr(Cfg, key, value)
    app = create_app(Cfg)
    with app.app_context():
        seed()
    return app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def log_rows(app):
    with app.app_context():
        return [(r.kind, r.to_email, r.status, r.error, r.attempts) for r in EmailLog.query.order_by(EmailLog.id)]


def send(app, **kwargs):
    with app.test_request_context():
        return mail.send_email("someone@example.com", "Hello", "Body text", kind=kwargs.pop("kind", "test"), **kwargs)


def register(client, email="learner@example.com", name="Hana Tesfaye", lang=None):
    if lang:
        client.get(f"/lang/{lang}")
    return client.post("/register", data={"full_name": name, "email": email, "password": "secret123",
                                          "password2": "secret123", "agree": "1"})


def admin_client(app):
    c = app.test_client()
    c.post("/login", data={"email": "admin@example.com", "password": "admin12345"})
    return c


# --- sending and the log ---------------------------------------------------------------------------------------------

def test_without_a_mail_server_nothing_is_sent_but_the_attempt_is_recorded(tmp_path):
    app = make_app(tmp_path, MAIL_SERVER="")
    assert send(app) is False
    assert FakeSMTP.instances == []
    assert log_rows(app) == [("test", "someone@example.com", "not_configured", None, 1)]


def test_port_587_upgrades_with_starttls_and_logs_in(app):
    assert send(app) is True
    smtp = FakeSMTP.instances[0]
    assert (smtp.host, smtp.port, smtp.starttls_called) == ("smtp.test", 587, True)
    assert smtp.logged_in == ("no-reply@mek-academy.test", "app-password")
    assert smtp.sent[0]["From"] == "Mek Academy <no-reply@mek-academy.test>"
    assert log_rows(app) == [("test", "someone@example.com", "sent", None, 1)]


def test_port_465_uses_implicit_tls_without_starttls(tmp_path):
    app = make_app(tmp_path, MAIL_PORT=465)
    assert send(app) is True
    smtp = FakeSMTP.instances[0]
    assert smtp.ssl and not smtp.starttls_called and smtp.logged_in


def test_ssl_can_be_forced_on_or_off_whatever_the_port(tmp_path):
    forced = make_app(tmp_path / "a", MAIL_PORT=2525, MAIL_USE_SSL="true")
    send(forced)
    assert FakeSMTP.instances[-1].ssl
    off = make_app(tmp_path / "b", MAIL_PORT=465, MAIL_USE_SSL="false")
    send(off)
    assert not FakeSMTP.instances[-1].ssl


def test_the_body_of_a_message_is_never_stored(app):
    send(app)
    with app.app_context():
        row = EmailLog.query.one()
        assert "Body text" not in " ".join(str(getattr(row, c.name)) for c in EmailLog.__table__.columns)


@pytest.mark.parametrize("exc, expected", [
    (smtplib.SMTPAuthenticationError(535, b"bad"), "username or password"),
    (smtplib.SMTPSenderRefused(550, b"no", "x@y"), "refused the sender address"),
    (smtplib.SMTPRecipientsRefused({"a@b": (550, b"no")}), "refused the recipient"),
    (socket.timeout(), "Timed out talking to smtp.test:587"),
    (socket.gaierror(), "was not found"),
    (ConnectionRefusedError(), "refused"),
    (smtplib.SMTPServerDisconnected("closed"), "MAIL_USE_SSL=true"),
])
def test_a_failed_send_is_logged_with_a_reason_a_person_can_act_on(app, exc, expected):
    FakeSMTP.failures = [exc]
    assert send(app, wait=True) is False
    (kind, to, status, error, attempts), = log_rows(app)
    assert status == "failed" and expected in error and attempts == 1


def test_a_wrong_password_is_not_retried_but_a_timeout_is(tmp_path, monkeypatch):
    app = make_app(tmp_path, MAIL_ASYNC=True)
    monkeypatch.setattr(mail.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda s: target(*args)})())
    FakeSMTP.failures = [smtplib.SMTPAuthenticationError(535, b"bad")]
    send(app)
    assert log_rows(app)[-1][2:] == ("failed", log_rows(app)[-1][3], 1)

    FakeSMTP.failures = [socket.timeout(), smtplib.SMTPResponseException(451, b"try later")]
    send(app)
    kind, to, status, error, attempts = log_rows(app)[-1]
    assert status == "sent" and attempts == 3          # two temporary failures, then success


def test_temporary_failures_give_up_after_three_tries(tmp_path, monkeypatch):
    app = make_app(tmp_path, MAIL_ASYNC=True)
    monkeypatch.setattr(mail.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda s: target(*args)})())
    FakeSMTP.failures = [socket.timeout(), socket.timeout(), socket.timeout(), socket.timeout()]
    send(app)
    kind, to, status, error, attempts = log_rows(app)[-1]
    assert status == "failed" and attempts == 3 and "Timed out" in error


def test_background_sending_does_not_make_the_caller_wait(tmp_path, monkeypatch):
    app = make_app(tmp_path, MAIL_ASYNC=True)
    started = []
    monkeypatch.setattr(mail.threading, "Thread", lambda target, args, daemon: type("T", (), {"start": lambda s: started.append(target)})())
    assert send(app) is True          # handed over, not yet delivered
    assert started and FakeSMTP.instances == []


def test_the_real_background_thread_delivers_and_logs(tmp_path):
    app = make_app(tmp_path, MAIL_ASYNC=True, SQLALCHEMY_DATABASE_URI=f"sqlite:///{tmp_path / 'm.db'}")
    send(app)
    for _ in range(100):
        if log_rows(app):
            break
        threading.Event().wait(0.05)
    assert log_rows(app)[0][2] == "sent"


def test_old_log_entries_are_removed(app):
    from datetime import timedelta

    from academy.models import utcnow

    with app.app_context():
        db.session.add(EmailLog(to_email="old@example.com", kind="x", status="sent", created_at=utcnow() - timedelta(days=120)))
        db.session.commit()
    send(app)
    assert [r[1] for r in log_rows(app)] == ["someone@example.com"]


def test_settings_problems_are_explained(tmp_path):
    app = make_app(tmp_path, MAIL_FROM="Mek <no-reply@localhost>", MAIL_USERNAME="", MAIL_PORT=465, MAIL_USE_SSL="false")
    text = " ".join(mail.configuration_problems(app.config))
    assert "example address" in text and "MAIL_USERNAME" in text and "Port 465 needs MAIL_USE_SSL" in text
    empty = make_app(tmp_path / "e", MAIL_SERVER="")
    assert "sends no email at all" in mail.configuration_problems(empty.config)[0]
    healthy = make_app(tmp_path / "h")
    assert mail.configuration_problems(healthy.config) == []


# --- the emails learners were missing --------------------------------------------------------------------------------

def test_registering_sends_a_welcome_email(app):
    client = app.test_client()
    assert register(client).status_code == 302
    (msg,) = FakeSMTP.instances[0].sent
    assert msg["To"] == "learner@example.com" and msg["Subject"] == "Welcome to Mek Academy"
    body = msg.get_content()
    assert "Hello Hana Tesfaye," in body and "/courses" in body and "/login" in body
    assert log_rows(app)[0][:3] == ("welcome", "learner@example.com", "sent")


def test_the_welcome_email_follows_the_language_the_learner_chose(app):
    register(app.test_client(), lang="am")
    (msg,) = FakeSMTP.instances[0].sent
    assert msg["Subject"].startswith("ወደ ") and "ኮርሶችን ይምረጡ" in msg.get_content()


def test_a_broken_mail_server_never_stops_a_sign_up(app):
    FakeSMTP.failures = [socket.timeout()]
    client = app.test_client()
    assert register(client).status_code == 302          # registered and signed in
    assert client.get("/dashboard").status_code == 200
    assert log_rows(app)[0][2] == "failed"


def finish_one_lesson_course(app, client):
    with app.app_context():
        course = Course(slug="short", title="Short course", price=0, is_published=True)
        db.session.add(course)
        db.session.flush()
        lesson = Lesson(course_id=course.id, title="Only lesson", position=1, kind="text", body="x")
        db.session.add(lesson)
        db.session.commit()
        user = User.query.filter_by(email="learner@example.com").one()
        enroll_student(user, course)
        db.session.commit()
        lid = lesson.id
    client.post(f"/learn/lesson/{lid}/complete")
    return lid


def test_finishing_a_course_emails_the_certificate_link_once(app):
    client = app.test_client()
    register(client)
    FakeSMTP.instances.clear()
    lid = finish_one_lesson_course(app, client)
    sent = [m for i in FakeSMTP.instances for m in i.sent]
    (msg,) = sent
    assert msg["Subject"] == "Your certificate: Short course"
    from academy.models import Certificate

    with app.app_context():
        code = Certificate.query.one().code
    assert f"/certificates/{code}" in msg.get_content() and code in msg.get_content()
    client.post(f"/learn/lesson/{lid}/complete")       # completing again must not email again
    assert len([m for i in FakeSMTP.instances for m in i.sent]) == 1


# --- the admin screens -----------------------------------------------------------------------------------------------

def test_only_admins_can_open_the_email_page(app):
    assert admin_client(app).get("/admin/email").status_code == 200
    student = app.test_client()
    register(student)
    assert student.get("/admin/email").status_code in (302, 403)
    teacher = app.test_client()
    teacher.post("/login", data={"email": "instructor@example.com", "password": "instructor123"})
    assert teacher.get("/admin/email").status_code in (302, 403)


def test_the_email_page_shows_settings_without_the_password_and_the_log(app):
    register(app.test_client())
    page = admin_client(app).get("/admin/email").data.decode()
    assert "smtp.test" in page and "STARTTLS" in page and "no-reply@mek-academy.test" in page
    assert "app-password" not in page and "password set" in page
    assert "learner@example.com" in page and "welcome" in page


def test_the_test_button_reports_success_and_the_real_reason_for_failure(app):
    admin = admin_client(app)
    page = admin.post("/admin/email", data={"to": "me@example.com"}).data.decode()
    assert "Sent" in page and "me@example.com" in page
    FakeSMTP.failures = [smtplib.SMTPAuthenticationError(535, b"bad")]
    page = admin.post("/admin/email", data={"to": "me@example.com"}).data.decode()
    assert "Failed" in page and "username or password" in page
    assert "Enter a valid email" in admin.post("/admin/email", data={"to": "nonsense"}).data.decode()


def test_the_dashboard_warns_when_email_is_not_set_up_or_failing(tmp_path):
    off = make_app(tmp_path / "off", MAIL_SERVER="")
    assert "Email problem" in admin_client(off).get("/admin/").data.decode()

    ok = make_app(tmp_path / "ok")
    admin = admin_client(ok)
    assert "Email problem" not in admin.get("/admin/").data.decode()
    FakeSMTP.failures = [smtplib.SMTPAuthenticationError(535, b"bad")]
    register(ok.test_client())
    page = admin.get("/admin/").data.decode()
    assert "Email problem" in page and "1 of the last 1" in page


def test_a_test_email_does_not_count_towards_the_dashboard_warning(app):
    admin = admin_client(app)
    FakeSMTP.failures = [smtplib.SMTPAuthenticationError(535, b"bad")]
    admin.post("/admin/email", data={"to": "me@example.com"})
    assert "Email problem" not in admin.get("/admin/").data.decode()


def test_the_command_line_test_reports_the_outcome(app):
    runner = app.test_cli_runner()
    ok = runner.invoke(args=["email-test", "--to", "me@example.com"])
    assert ok.exit_code == 0 and "accepted the message" in ok.output and "smtp.test:587" in ok.output
    assert "app-password" not in ok.output
    FakeSMTP.failures = [smtplib.SMTPAuthenticationError(535, b"bad")]
    bad = runner.invoke(args=["email-test", "--to", "me@example.com"])
    assert bad.exit_code == 1 and "FAILED" in bad.output and "username or password" in bad.output


def test_erasing_an_account_removes_the_address_from_the_email_log(app):
    from academy.privacy import erase_account

    client = app.test_client()
    register(client)
    with app.app_context():
        user = User.query.filter_by(email="learner@example.com").one()
        erase_account(user)
        db.session.commit()
        assert EmailLog.query.filter_by(to_email="learner@example.com").count() == 0
        assert EmailLog.query.filter_by(to_email="erased", kind="welcome").count() == 1


def test_the_staff_invitation_reports_whether_the_email_really_went(tmp_path):
    """The invitation screen tells the admin to copy the link when email failed, so it must wait for the real result."""
    app = make_app(tmp_path, MAIL_ASYNC=True)
    admin = admin_client(app)

    def invite(email):
        return admin.post("/admin/staff/new", data={"full_name": "Meklit Haile", "email": email, "role": "instructor"},
                          follow_redirects=True).data.decode()

    FakeSMTP.failures = [socket.timeout()]
    failed = invite("one@example.com")
    assert "could not be sent" in failed and "emailed to one@example.com" not in failed and "/reset-password/" in failed
    worked = invite("two@example.com")
    assert "emailed to two@example.com" in worked and "could not be sent" not in worked
