"""Tests for pilot-launch features: password reset, login throttling, DRM video,
nginx video hand-off, policy pages and database migrations."""
import os
import re

import pytest

from kasina import create_app
from kasina.config import Config, TestConfig
from kasina.models import Course, Lesson, User, db
from kasina.seed import seed


@pytest.fixture
def app(tmp_path):
    class Cfg(TestConfig):
        UPLOAD_DIR = str(tmp_path / "uploads")

    app = create_app(Cfg)
    with app.app_context():
        seed()
    return app


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    def fake_send(to, subject, body):
        sent.append({"to": to, "subject": subject, "body": body})
        return True

    import kasina.routes.admin as admin_routes
    import kasina.routes.auth as auth_routes

    monkeypatch.setattr(auth_routes, "send_email", fake_send)
    monkeypatch.setattr(admin_routes, "send_email", fake_send)
    return sent


def register(client, email="student@example.com"):
    client.post("/register", data={"full_name": "Hana Tesfaye", "email": email, "password": "secret123",
                                   "password2": "secret123", "agree": "1"})


def login(client, email, password):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=True)


# --- Password reset ------------------------------------------------------------

def test_password_reset_flow(app, outbox):
    c = app.test_client()
    register(c)
    c.post("/logout")
    r = c.post("/forgot-password", data={"email": "student@example.com"}, follow_redirects=True)
    assert b"If an account exists" in r.data
    assert len(outbox) == 1 and outbox[0]["to"] == "student@example.com"
    link = re.search(r"http://localhost(/reset-password/\S+)", outbox[0]["body"]).group(1)
    r = c.post(link, data={"password": "newpass456", "password2": "newpass456"}, follow_redirects=True)
    assert b"password has been changed" in r.data
    assert b"Welcome back" in login(c, "student@example.com", "newpass456").data
    # The link is single-use
    c.post("/logout")
    r = c.post(link, data={"password": "again7890", "password2": "again7890"}, follow_redirects=True)
    assert b"invalid or has expired" in r.data


def test_forgot_password_does_not_reveal_accounts(app, outbox):
    c = app.test_client()
    r = c.post("/forgot-password", data={"email": "nobody@example.com"}, follow_redirects=True)
    assert b"If an account exists" in r.data
    assert outbox == []


# --- Login throttling ------------------------------------------------------------

def test_login_throttled_after_repeated_failures(app):
    c = app.test_client()
    register(c)
    c.post("/logout")
    for _ in range(5):
        assert b"Incorrect email or password" in login(c, "student@example.com", "wrong").data
    r = c.post("/login", data={"email": "student@example.com", "password": "secret123"})
    assert r.status_code == 429  # even the right password is refused for now
    # A password reset clears the block
    with app.app_context():
        from kasina.routes.auth import make_reset_token

        token = make_reset_token(User.query.filter_by(email="student@example.com").one())
    c.post(f"/reset-password/{token}", data={"password": "newpass456", "password2": "newpass456"})
    assert b"Welcome back" in login(c, "student@example.com", "newpass456").data


# --- DRM video (VdoCipher) ---------------------------------------------------------

class FakeResponse:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


def _vdo_lesson(app):
    with app.app_context():
        lesson = Lesson.query.filter_by(kind="video").first()
        lesson.video_provider = "vdocipher"
        lesson.vdocipher_id = "abcdef1234567890abcdef1234567890"
        db.session.commit()
        return lesson.id, lesson.course_id


def test_vdocipher_playback_uses_per_student_otp_with_watermark(app, monkeypatch):
    app.config["VDOCIPHER_API_SECRET"] = "secret-key"
    lid, cid = _vdo_lesson(app)
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        return FakeResponse(200, {"otp": "OTP123", "playbackInfo": "PB456"})

    monkeypatch.setattr("kasina.video.requests.post", fake_post)
    c = app.test_client()
    register(c)
    c.post(f"/pay/course/{cid}/demo")
    r = c.get(f"/learn/lesson/{lid}")
    assert b"player.vdocipher.com/v2/?otp=OTP123&amp;playbackInfo=PB456" in r.data
    call = calls[0]
    assert call["url"].endswith("/videos/abcdef1234567890abcdef1234567890/otp")
    assert call["headers"]["Authorization"] == "Apisecret secret-key"
    assert "student@example.com" in call["json"]["annotate"]


def test_vdocipher_not_requested_for_students_without_access(app, monkeypatch):
    app.config["VDOCIPHER_API_SECRET"] = "secret-key"
    lid, _ = _vdo_lesson(app)
    monkeypatch.setattr("kasina.video.requests.post", lambda *a, **k: pytest.fail("OTP requested"))
    c = app.test_client()
    register(c)
    assert c.get(f"/learn/lesson/{lid}").status_code == 302


def test_vdocipher_failure_shows_message(app, monkeypatch):
    app.config["VDOCIPHER_API_SECRET"] = "secret-key"
    lid, cid = _vdo_lesson(app)
    monkeypatch.setattr("kasina.video.requests.post",
                        lambda *a, **k: FakeResponse(401, {"message": "Invalid API secret"}))
    c = app.test_client()
    register(c)
    c.post(f"/pay/course/{cid}/demo")
    assert b"could not be loaded right now" in c.get(f"/learn/lesson/{lid}").data


def test_admin_gets_upload_credentials_and_saves_video_id(app, monkeypatch):
    app.config["VDOCIPHER_API_SECRET"] = "secret-key"
    payload = {"videoId": "1234567890abcdef", "clientPayload": {"uploadLink": "https://upload.example",
                                                                "policy": "p", "key": "k"}}
    monkeypatch.setattr("kasina.video.requests.put", lambda *a, **k: FakeResponse(200, payload))
    admin = app.test_client()
    login(admin, "admin@kasina.local", "admin12345")
    r = admin.post("/admin/vdocipher/upload-credentials", json={"title": "Stacking tiers"})
    assert r.status_code == 200 and r.get_json()["videoId"] == "1234567890abcdef"
    # Students cannot get upload credentials
    s = app.test_client()
    register(s)
    assert s.post("/admin/vdocipher/upload-credentials", json={}).status_code == 403

    with app.app_context():
        cid = Course.query.first().id
    admin.post(f"/admin/courses/{cid}/lessons/new", data={
        "title": "Stacking tiers", "kind": "video", "video_provider": "vdocipher",
        "vdocipher_id": "1234567890abcdef"})
    with app.app_context():
        lesson = Lesson.query.filter_by(title="Stacking tiers").one()
        assert lesson.video_provider == "vdocipher" and lesson.has_video
    r = admin.post(f"/admin/courses/{cid}/lessons/new", data={
        "title": "Bad", "kind": "video", "video_provider": "vdocipher", "vdocipher_id": "../../etc"})
    assert r.status_code == 400


# --- Local video via nginx -------------------------------------------------------------

def test_x_accel_redirect_hands_video_to_nginx(app):
    app.config["USE_X_ACCEL_REDIRECT"] = True
    with app.app_context():
        lesson = Lesson.query.filter_by(kind="video").first()
        lesson.video_file = "abc.mp4"
        db.session.commit()
        lid, cid = lesson.id, lesson.course_id
    c = app.test_client()
    register(c)
    c.post(f"/pay/course/{cid}/demo")
    page = c.get(f"/learn/lesson/{lid}").data
    url = re.search(rb'src="(/learn/lesson/\d+/video\?t=[^"]+)"', page).group(1).decode()
    r = c.get(url)
    assert r.headers["X-Accel-Redirect"] == "/_protected_videos/abc.mp4"
    assert r.mimetype == "video/mp4" and r.data == b""


# --- Payment emails -------------------------------------------------------------------

def test_student_emailed_when_receipt_approved(app, outbox):
    import io

    from kasina.models import Payment

    with app.app_context():
        cid = Course.query.filter_by(slug="cake-baking-fundamentals").one().id
    s = app.test_client()
    register(s)
    s.post(f"/pay/course/{cid}/manual", data={"receipt": (io.BytesIO(b"%PDF"), "r.pdf")},
           content_type="multipart/form-data")
    with app.app_context():
        pid = Payment.query.one().id
    admin = app.test_client()
    login(admin, "admin@kasina.local", "admin12345")
    admin.post(f"/admin/payments/{pid}/review", data={"decision": "approve"})
    assert outbox[-1]["to"] == "student@example.com" and "enrolled" in outbox[-1]["subject"]


# --- Policies -------------------------------------------------------------------------

@pytest.mark.parametrize("page", ["terms", "privacy", "refunds"])
def test_policy_pages(app, page):
    assert app.test_client().get(f"/policies/{page}").status_code == 200


def test_unknown_policy_404(app):
    assert app.test_client().get("/policies/../base").status_code == 404


# --- Migrations -----------------------------------------------------------------------

def test_migrations_match_models(tmp_path):
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from flask_migrate import upgrade

    class Cfg(Config):
        SQLALCHEMY_DATABASE_URI = "sqlite:///" + str(tmp_path / "m.db")
        UPLOAD_DIR = str(tmp_path / "up")

    app = create_app(Cfg)
    with app.app_context():
        upgrade()
        with db.engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), db.metadata)
        assert diff == [], diff
    assert os.path.exists(tmp_path / "m.db")
