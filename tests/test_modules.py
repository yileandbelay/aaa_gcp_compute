"""Tests for the reusable/modular parts: branding, theme override, feature
switches, course materials, categories, protection settings and JSON seeding."""
import io
import json

import pytest

from academy import create_app
from academy.config import TestConfig
from academy.models import Attachment, Course, Lesson, Payment, db
from academy.seed import seed


def make_app(tmp_path, **overrides):
    attrs = {"UPLOAD_DIR": str(tmp_path / "uploads"), **overrides}
    cfg = type("Cfg", (TestConfig,), attrs)
    app = create_app(cfg)
    with app.app_context():
        seed()
    return app


@pytest.fixture
def app(tmp_path):
    return make_app(tmp_path)


def register(client, email="student@example.com"):
    client.post("/register", data={"full_name": "Abebe Kebede", "email": email, "password": "secret123",
                                   "password2": "secret123", "agree": "1"})


def login(client, email, password):
    client.post("/login", data={"email": email, "password": password})


def pm_course(app):
    with app.app_context():
        c = Course.query.filter_by(slug="project-management-fundamentals").one()
        return c.id, [l.id for l in c.lessons]


# --- Branding & theme -------------------------------------------------------------

def test_branding_comes_from_settings(tmp_path):
    app = make_app(tmp_path, SITE_NAME="Acme Skills Institute", BRAND_COLOR="#0a7d55", TX_PREFIX="ACME")
    c = app.test_client()
    page = c.get("/").data
    assert b"Acme Skills Institute" in page and b"--brand: #0a7d55" in page
    cid, _ = pm_course(app)
    register(c)
    c.post(f"/pay/course/{cid}/demo")
    with app.app_context():
        assert Payment.query.one().tx_ref.startswith("ACME-")


def test_theme_folder_overrides_templates_and_adds_css(tmp_path):
    theme = tmp_path / "theme"
    (theme / "templates").mkdir(parents=True)
    (theme / "static").mkdir()
    (theme / "templates" / "index.html").write_text('{% extends "base.html" %}{% block content %}Custom home{% endblock %}')
    (theme / "static" / "theme.css").write_text("body{color:red}")
    app = make_app(tmp_path, THEME_DIR=str(theme))
    c = app.test_client()
    page = c.get("/").data
    assert b"Custom home" in page and b"/theme/theme.css" in page
    assert c.get("/theme/theme.css").data == b"body{color:red}"
    assert b"Courses" in c.get("/courses").data  # other pages still use the built-in templates


def test_healthz(app):
    assert app.test_client().get("/healthz").get_json() == {"status": "ok"}


# --- Catalog -----------------------------------------------------------------------

def test_catalog_category_filter_and_search(app):
    c = app.test_client()
    r = c.get("/courses")
    assert b"Business &amp; Management" in r.data and b"IT &amp; Digital Skills" in r.data
    r = c.get("/courses?category=IT+%26+Digital+Skills")
    assert b"Excel for the Workplace" in r.data and b"Project Management Fundamentals" not in r.data
    r = c.get("/courses?q=project")
    assert b"Project Management Fundamentals" in r.data and b"Excel for the Workplace" not in r.data


def test_course_page_shows_instructor_and_hours(app):
    page = app.test_client().get("/courses/project-management-fundamentals").data
    assert b"Meklit Haile" in page and b"PMP-certified" in page and b"12 h of training" in page


# --- Course materials -----------------------------------------------------------------

def _upload_material(app, lesson_id, name="handout.pdf", data=b"%PDF-1.4 handout"):
    admin = app.test_client()
    login(admin, "admin@example.com", "admin12345")
    r = admin.post(f"/admin/lessons/{lesson_id}", data={
        "title": "With handout", "kind": "text", "materials": (io.BytesIO(data), name),
    }, content_type="multipart/form-data")
    return admin, r


def test_materials_download_requires_enrollment(app):
    cid, lessons = pm_course(app)
    _upload_material(app, lessons[2])
    with app.app_context():
        att = Attachment.query.one()
        assert att.original_name == "handout.pdf" and att.size_bytes == 16
        aid = att.id
    s = app.test_client()
    register(s)
    assert s.get(f"/learn/material/{aid}").status_code == 403
    s.post(f"/pay/course/{cid}/demo")
    page = s.get(f"/learn/lesson/{lessons[2]}").data
    assert b"handout.pdf" in page
    r = s.get(f"/learn/material/{aid}")
    assert r.status_code == 200 and r.data == b"%PDF-1.4 handout"
    assert "attachment" in r.headers["Content-Disposition"]


def test_material_upload_rejects_unsafe_types(app):
    _, lessons = pm_course(app)
    _, r = _upload_material(app, lessons[0], name="script.html", data=b"<script>")
    assert r.status_code == 400


def test_material_delete_removes_file(app):
    import os

    _, lessons = pm_course(app)
    admin, _ = _upload_material(app, lessons[0])
    with app.app_context():
        att = Attachment.query.one()
        path = os.path.join(app.config["UPLOAD_DIR"], "materials", att.stored_name)
        aid = att.id
    assert os.path.exists(path)
    admin.post(f"/admin/materials/{aid}/delete")
    assert not os.path.exists(path)
    with app.app_context():
        assert Attachment.query.count() == 0


def test_material_download_can_be_switched_off(tmp_path):
    app = make_app(tmp_path, ALLOW_MATERIAL_DOWNLOAD=False)
    cid, lessons = pm_course(app)
    _upload_material(app, lessons[0])
    with app.app_context():
        aid = Attachment.query.one().id
    s = app.test_client()
    register(s)
    s.post(f"/pay/course/{cid}/demo")
    assert s.get(f"/learn/material/{aid}").status_code == 403


# --- Protection settings -----------------------------------------------------------------

def _enrolled_video_page(app):
    cid, lessons = pm_course(app)
    with app.app_context():
        lesson = db.session.get(Lesson, lessons[1])
        lesson.video_file = "v.mp4"
        db.session.commit()
    s = app.test_client()
    register(s)
    s.post(f"/pay/course/{cid}/demo")
    return s.get(f"/learn/lesson/{lessons[1]}").data


def test_default_protection_is_light(app):
    page = _enrolled_video_page(app)
    assert b'oncopy="return false"' not in page  # lesson text can be copied
    assert b'class="watermark"' in page and b"nodownload" in page


def test_strict_protection_settings(tmp_path):
    app = make_app(tmp_path, PROTECT_LESSON_TEXT=True)
    assert b'oncopy="return false"' in _enrolled_video_page(app)


def test_relaxed_video_settings(tmp_path):
    app = make_app(tmp_path, VIDEO_WATERMARK=False, ALLOW_VIDEO_DOWNLOAD=True)
    page = _enrolled_video_page(app)
    assert b'class="watermark"' not in page and b"nodownload" not in page


# --- Feature switches --------------------------------------------------------------------

def test_discussion_can_be_switched_off(tmp_path):
    app = make_app(tmp_path, FEATURE_DISCUSSION=False)
    cid, lessons = pm_course(app)
    s = app.test_client()
    register(s)
    s.post(f"/pay/course/{cid}/demo")
    assert b"Questions &amp; discussion" not in s.get(f"/learn/lesson/{lessons[0]}").data
    assert s.post(f"/learn/lesson/{lessons[0]}/comment", data={"body": "hi"}).status_code == 404


def test_certificates_can_be_switched_off(tmp_path):
    app = make_app(tmp_path, FEATURE_CERTIFICATES=False)
    cid, lessons = pm_course(app)
    s = app.test_client()
    register(s)
    s.post(f"/pay/course/{cid}/demo")
    with app.app_context():
        a = db.session.get(Course, cid).assessments[0]
        right = {f"q{q.id}": str(q.correct_index) for q in a.questions}
        aid = a.id
    for lid in lessons:
        s.post(f"/learn/lesson/{lid}/complete")
    r = s.post(f"/assessments/{aid}/start", follow_redirects=True)
    s.post(r.request.path, data=right)
    from academy.models import Certificate

    with app.app_context():
        assert Certificate.query.count() == 0


def test_announcements_can_be_switched_off(tmp_path):
    app = make_app(tmp_path, FEATURE_ANNOUNCEMENTS=False)
    cid, _ = pm_course(app)
    admin = app.test_client()
    login(admin, "admin@example.com", "admin12345")
    assert admin.post(f"/admin/courses/{cid}/announce", data={"title": "x"}).status_code == 404


# --- Seeding from a JSON file --------------------------------------------------------------

def test_seed_from_custom_json(tmp_path):
    data = {"courses": [{"slug": "first-aid", "title": "First Aid at Work", "category": "Health & Safety",
                         "hours": 6, "price": "800",
                         "lessons": [{"title": "Primary survey", "body": "Danger, Response, Airway..."}],
                         "assessments": [{"title": "Check", "questions": [
                             {"text": "First step?", "options": ["Check for danger", "Call a friend"], "answer": 1}]}]}]}
    path = tmp_path / "courses.json"
    path.write_text(json.dumps(data))
    app = create_app(type("Cfg", (TestConfig,), {"UPLOAD_DIR": str(tmp_path / "u")}))
    with app.app_context():
        seed(str(path))
        seed(str(path))  # running twice does not duplicate
        course = Course.query.filter_by(slug="first-aid").one()
        assert Course.query.count() == 1
        assert course.assessments[0].questions[0].correct_index == 0
        assert float(course.hours) == 6
