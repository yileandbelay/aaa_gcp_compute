"""Tests for the pluggable video hosts (Bunny Stream, VdoCipher) and the migration
that generalised the stored video ID."""
import hashlib
import re

import pytest
from sqlalchemy import text

from academy import create_app
from academy.config import Config, TestConfig
from academy.models import Course, Lesson, db
from academy.seed import seed

GUID = "3f2b8c1e-9d4a-4e6b-8a7c-1d2e3f4a5b6c"


class FakeResponse:
    def __init__(self, status, data):
        self.status_code, self._data = status, data

    def json(self):
        return self._data


@pytest.fixture
def app(tmp_path):
    cfg = type("Cfg", (TestConfig,), {
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "BUNNY_LIBRARY_ID": "12345", "BUNNY_API_KEY": "lib-api-key", "BUNNY_TOKEN_KEY": "token-key",
    })
    app = create_app(cfg)
    with app.app_context():
        seed()
    return app


def login(c, email, password):
    c.post("/login", data={"email": email, "password": password})


def register(c, email="student@example.com"):
    c.post("/register", data={"full_name": "Abebe Kebede", "email": email, "password": "secret123",
                              "password2": "secret123", "agree": "1"})


def bunny_lesson(app, ref=GUID):
    with app.app_context():
        lesson = Lesson.query.filter_by(kind="video").first()
        lesson.video_provider, lesson.video_ref = "bunny", ref
        db.session.commit()
        return lesson.id, lesson.course_id


def sha(text_):
    return hashlib.sha256(text_.encode()).hexdigest()


# --- Playback -----------------------------------------------------------------------

def test_bunny_embed_is_signed_and_watermarked(app):
    lid, cid = bunny_lesson(app)
    s = app.test_client()
    register(s)
    s.post(f"/pay/course/{cid}/demo")
    page = s.get(f"/learn/lesson/{lid}").data.decode()
    m = re.search(r'src="(https://iframe\.mediadelivery\.net/embed/12345/%s)\?token=([0-9a-f]{64})&amp;expires=(\d+)"' % GUID, page)
    assert m, page[:2000]
    assert m.group(2) == sha("token-key" + GUID + m.group(3))
    assert 'class="watermark">student@example.com' in page  # Bunny cannot burn one in, so we overlay it


def test_bunny_without_token_key_uses_plain_embed(app):
    app.config["BUNNY_TOKEN_KEY"] = ""
    lid, cid = bunny_lesson(app)
    s = app.test_client()
    register(s)
    s.post(f"/pay/course/{cid}/demo")
    assert f'src="https://iframe.mediadelivery.net/embed/12345/{GUID}"' in s.get(f"/learn/lesson/{lid}").data.decode()


def test_bunny_embed_not_given_to_students_without_access(app):
    lid, _ = bunny_lesson(app)
    s = app.test_client()
    register(s)
    r = s.get(f"/learn/lesson/{lid}")
    assert r.status_code == 302 and b"mediadelivery" not in r.data


def test_watermark_can_be_switched_off(app):
    app.config["VIDEO_WATERMARK"] = False
    lid, cid = bunny_lesson(app)
    s = app.test_client()
    register(s)
    s.post(f"/pay/course/{cid}/demo")
    assert 'class="watermark"' not in s.get(f"/learn/lesson/{lid}").data.decode()


def test_unconfigured_host_shows_message_not_player(app):
    app.config["BUNNY_API_KEY"] = ""
    lid, cid = bunny_lesson(app)
    s = app.test_client()
    register(s)
    s.post(f"/pay/course/{cid}/demo")
    page = s.get(f"/learn/lesson/{lid}").data.decode()
    assert "not configured yet" in page and "mediadelivery" not in page


# --- Upload (admin) ---------------------------------------------------------------------

def test_bunny_upload_info_signs_tus_upload_without_leaking_api_key(app, monkeypatch):
    calls = []

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        return FakeResponse(200, {"guid": GUID})

    monkeypatch.setattr("academy.videohosts.bunny.requests.post", fake_post)
    admin = app.test_client()
    login(admin, "admin@example.com", "admin12345")
    r = admin.post("/admin/video/bunny/upload", json={"title": "Intro"})
    assert r.status_code == 200
    d = r.get_json()
    assert calls[0]["url"] == "https://video.bunnycdn.com/library/12345/videos"
    assert calls[0]["headers"]["AccessKey"] == "lib-api-key" and calls[0]["json"] == {"title": "Intro"}
    h = d["headers"]
    assert d["method"] == "tus" and d["video_ref"] == GUID and h["VideoId"] == GUID and h["LibraryId"] == "12345"
    assert h["AuthorizationSignature"] == sha("12345" + "lib-api-key" + h["AuthorizationExpire"] + GUID)
    assert "lib-api-key" not in r.get_data(as_text=True)


def test_upload_info_reports_host_errors(app, monkeypatch):
    monkeypatch.setattr("academy.videohosts.bunny.requests.post",
                        lambda *a, **k: FakeResponse(401, {"Message": "Authorization has been denied"}))
    admin = app.test_client()
    login(admin, "admin@example.com", "admin12345")
    r = admin.post("/admin/video/bunny/upload", json={"title": "Intro"})
    assert r.status_code == 502 and "denied" in r.get_json()["error"]


def test_upload_info_requires_staff_and_a_configured_host(app):
    s = app.test_client()
    register(s)
    assert s.post("/admin/video/bunny/upload", json={}).status_code == 403
    admin = app.test_client()
    login(admin, "admin@example.com", "admin12345")
    assert admin.post("/admin/video/vdocipher/upload", json={}).status_code == 400  # no VdoCipher secret set
    assert admin.post("/admin/video/nope/upload", json={}).status_code == 400


def test_lesson_editor_offers_configured_hosts_and_saves_bunny_id(app):
    admin = app.test_client()
    login(admin, "admin@example.com", "admin12345")
    with app.app_context():
        cid = Course.query.first().id
    page = admin.get(f"/admin/courses/{cid}/lessons/new").data.decode()
    assert 'value="bunny"' in page and 'value="vdocipher"' not in page and "tus-js-client@4.1.0" in page
    assert re.search(r'value="bunny"\s+checked', page)  # a new lesson defaults to the configured host
    admin.post(f"/admin/courses/{cid}/lessons/new", data={
        "title": "Hosted", "kind": "video", "video_provider": "bunny", "video_ref": GUID})
    with app.app_context():
        lesson = Lesson.query.filter_by(title="Hosted").one()
        assert (lesson.video_provider, lesson.video_ref, lesson.has_video) == ("bunny", GUID, True)
    r = admin.post(f"/admin/courses/{cid}/lessons/new", data={
        "title": "Bad", "kind": "video", "video_provider": "bunny", "video_ref": "../../etc"})
    assert r.status_code == 400


def test_bunny_status_label(app, monkeypatch):
    from academy import videohosts

    monkeypatch.setattr("academy.videohosts.bunny.requests.get", lambda *a, **k: FakeResponse(200, {"status": 4}))
    with app.app_context():
        assert videohosts.get("bunny").status(GUID) == "ready"
    monkeypatch.setattr("academy.videohosts.bunny.requests.get", lambda *a, **k: FakeResponse(200, {"status": 99}))
    with app.app_context():
        assert videohosts.get("bunny").status(GUID) == "status 99"


def test_no_hosts_configured_still_offers_this_server(tmp_path):
    app = create_app(type("Cfg", (TestConfig,), {"UPLOAD_DIR": str(tmp_path / "u")}))
    with app.app_context():
        seed()
        cid = Course.query.first().id
    admin = app.test_client()
    login(admin, "admin@example.com", "admin12345")
    page = admin.get(f"/admin/courses/{cid}/lessons/new").data.decode()
    assert 'value="local"' in page and 'value="bunny"' not in page and "tus-js-client" not in page


# --- Migration ----------------------------------------------------------------------------

def test_migration_keeps_existing_vdocipher_ids(tmp_path):
    from flask_migrate import upgrade

    cfg = type("Cfg", (Config,), {"SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "m.db"),
                                  "UPLOAD_DIR": str(tmp_path / "up")})
    app = create_app(cfg)
    with app.app_context():
        upgrade(revision="237cd953ced9")  # the schema before the change
        db.session.execute(text("INSERT INTO course (slug, title, price, is_published) "
                                "VALUES ('c', 'C', 0, 1)"))
        db.session.execute(text("INSERT INTO lesson (course_id, title, kind, video_provider, vdocipher_id, position) "
                                "VALUES (1, 'L', 'video', 'vdocipher', 'abcdef1234567890', 1)"))
        db.session.commit()
        upgrade()
        row = db.session.execute(text("SELECT video_provider, video_ref FROM lesson")).one()
        assert tuple(row) == ("vdocipher", "abcdef1234567890")
