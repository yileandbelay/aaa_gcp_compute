import io
import os
import re

import pytest

from academy import create_app
from academy.config import TestConfig
from academy.models import Certificate, Course, Lesson, Payment, User, db
from academy.seed import seed


@pytest.fixture
def app(tmp_path):
    class Cfg(TestConfig):
        UPLOAD_DIR = str(tmp_path / "uploads")

    app = create_app(Cfg)
    with app.app_context():
        seed()
    yield app


@pytest.fixture
def client(app):
    return app.test_client()


def register(client, email="student@example.com", name="Hana Tesfaye"):
    return client.post("/register", data={
        "full_name": name, "email": email, "phone": "+251911000000", "city": "Addis Ababa",
        "password": "secret123", "password2": "secret123", "agree": "1",
    }, follow_redirects=True)


def login(client, email, password):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=True)


def course(app, slug="project-management-fundamentals"):
    with app.app_context():
        return Course.query.filter_by(slug=slug).first().id


def test_public_pages(client):
    assert client.get("/").status_code == 200
    r = client.get("/courses")
    assert b"Project Management Fundamentals" in r.data
    assert client.get("/courses/project-management-fundamentals").status_code == 200


def test_registration_validation(client):
    r = client.post("/register", data={"full_name": "x", "email": "bad", "password": "1", "password2": "2"})
    assert r.status_code == 400
    assert b"valid email" in r.data
    assert register(client).status_code == 200
    client.post("/logout")
    r = register(client)  # duplicate
    assert b"already exists" in r.data


def test_paid_lessons_are_protected(app, client):
    with app.app_context():
        c = Course.query.filter_by(slug="project-management-fundamentals").first()
        preview, paid = c.lessons[0], c.lessons[1]
        preview_id, paid_id = preview.id, paid.id
    # Anonymous: preview visible, paid redirects to login
    assert client.get(f"/learn/lesson/{preview_id}").status_code == 200
    assert client.get(f"/learn/lesson/{paid_id}").status_code == 302
    register(client)
    r = client.get(f"/learn/lesson/{paid_id}")
    assert r.status_code == 302 and "/courses/" in r.location
    # Video endpoint denied without enrollment
    assert client.get(f"/learn/lesson/{paid_id}/video?t=x").status_code == 403


def test_demo_payment_unlocks_course_and_video_token(app, client, tmp_path):
    cid = course(app)
    with app.app_context():
        lesson = Lesson.query.filter_by(course_id=cid, kind="video").first()
        os.makedirs(os.path.join(app.config["UPLOAD_DIR"], "videos"), exist_ok=True)
        with open(os.path.join(app.config["UPLOAD_DIR"], "videos", "v.mp4"), "wb") as fh:
            fh.write(b"\x00" * 2048)
        lesson.video_file = "v.mp4"
        db.session.commit()
        lid = lesson.id
    register(client)
    assert client.get(f"/pay/course/{cid}").status_code == 200
    client.post(f"/pay/course/{cid}/demo")
    r = client.get(f"/learn/lesson/{lid}")
    assert r.status_code == 200
    m = re.search(rb'src="(/learn/lesson/\d+/video\?t=[^"]+)"', r.data)
    assert m
    url = m.group(1).decode().replace("&amp;", "&")
    v = client.get(url, headers={"Range": "bytes=0-99"})
    assert v.status_code == 206 and len(v.data) == 100
    # Token is bound to the user: another enrolled student cannot reuse it
    other = client.application.test_client()
    register(other, email="other@example.com")
    other.post(f"/pay/course/{cid}/demo")
    assert other.get(url).status_code == 403


def test_manual_payment_approval_flow(app, client):
    cid = course(app)
    register(client)
    r = client.post(f"/pay/course/{cid}/manual", data={
        "reference": "FT123", "receipt": (io.BytesIO(b"%PDF-1.4"), "receipt.pdf"),
    }, content_type="multipart/form-data", follow_redirects=True)
    assert b"being reviewed" in r.data
    with app.app_context():
        p = Payment.query.filter_by(status="submitted").one()
        pid = p.id
        student_id = p.user_id
        assert not db.session.get(User, student_id).has_access(db.session.get(Course, cid))
    # Student cannot approve their own payment
    assert client.post(f"/admin/payments/{pid}/review", data={"decision": "approve"}).status_code == 403
    admin = app.test_client()
    login(admin, "admin@example.com", "admin12345")
    assert admin.get(f"/pay/receipt/{pid}").status_code == 200
    admin.post(f"/admin/payments/{pid}/review", data={"decision": "approve"})
    with app.app_context():
        assert db.session.get(Payment, pid).status == "paid"
        assert db.session.get(User, student_id).has_access(db.session.get(Course, cid))


def test_rejects_bad_receipt_type(app, client):
    cid = course(app)
    register(client)
    r = client.post(f"/pay/course/{cid}/manual", data={
        "receipt": (io.BytesIO(b"x"), "evil.html"),
    }, content_type="multipart/form-data")
    assert r.status_code == 400


def test_full_course_completion_issues_certificate(app, client):
    cid = course(app)
    register(client)
    client.post(f"/pay/course/{cid}/demo")
    with app.app_context():
        c = db.session.get(Course, cid)
        lesson_ids = [l.id for l in c.lessons]
        a = c.assessments[0]
        aid = a.id
        wrong = {f"q{q.id}": str((q.correct_index + 1) % len(q.options)) for q in a.questions}
        right = {f"q{q.id}": str(q.correct_index) for q in a.questions}
    for lid in lesson_ids:
        client.post(f"/learn/lesson/{lid}/complete")
    # Failing attempt does not reveal answers
    r = client.post(f"/assessments/{aid}/start", follow_redirects=True)
    r = client.post(r.request.path, data=wrong, follow_redirects=True)
    assert b"Not quite there yet" in r.data and b"opt correct" not in r.data
    # Passing attempt issues certificate
    r = client.post(f"/assessments/{aid}/start", follow_redirects=True)
    r = client.post(r.request.path, data=right, follow_redirects=True)
    assert b"You passed" in r.data
    with app.app_context():
        cert = Certificate.query.one()
        code = cert.code
    assert b"Certificate of Completion" in client.get(f"/certificates/{code}").data


def test_attempt_limit(app, client):
    cid = course(app)
    register(client)
    client.post(f"/pay/course/{cid}/demo")
    with app.app_context():
        aid = db.session.get(Course, cid).assessments[0].id
    for _ in range(3):
        r = client.post(f"/assessments/{aid}/start", follow_redirects=True)
        client.post(r.request.path, data={})
    r = client.post(f"/assessments/{aid}/start", follow_redirects=True)
    assert b"no attempts remaining" in r.data


def test_free_course_enrollment(app, client):
    cid = course(app, "excel-for-the-workplace")
    register(client)
    r = client.post(f"/pay/course/{cid}", follow_redirects=True)
    assert b"You are enrolled" in r.data


def test_admin_requires_role(app, client):
    register(client)
    assert client.get("/admin/").status_code == 403
    admin = app.test_client()
    login(admin, "admin@example.com", "admin12345")
    assert admin.get("/admin/").status_code == 200
    assert admin.get("/admin/students").status_code == 200
    assert b"student@example.com" in admin.get("/admin/students.csv").data


def test_admin_creates_course_lesson_and_question(app):
    admin = app.test_client()
    login(admin, "admin@example.com", "admin12345")
    r = admin.post("/admin/courses/new", data={"title": "Customer Service Excellence", "price": "3000", "is_published": "1"})
    cid = int(re.search(r"/admin/courses/(\d+)", r.location).group(1))
    admin.post(f"/admin/courses/{cid}/lessons/new", data={
        "title": "Handling complaints", "kind": "video", "body": "Listen first.",
        "video": (io.BytesIO(b"fakevideo"), "complaints.mp4"),
    }, content_type="multipart/form-data")
    r = admin.post(f"/admin/courses/{cid}/assessments/new", data={"title": "Quiz 1", "pass_mark": "60"})
    aid = int(r.location.rstrip("/").split("/")[-1])
    admin.post(f"/admin/assessments/{aid}/questions",
               data={"text": "Step one when a customer complains?", "options": "1\n4\n10", "correct": "2"})
    with app.app_context():
        c = db.session.get(Course, cid)
        assert c.slug == "customer-service-excellence" and c.lessons[0].video_file.endswith(".mp4")
        assert c.assessments[0].questions[0].options == ["1", "4", "10"]
        assert c.assessments[0].questions[0].correct_index == 1


def test_markdown_is_sanitized(app):
    from academy.utils import render_markdown

    with app.app_context():
        html = str(render_markdown("hi <script>alert(1)</script> [x](javascript:alert(1))"))
    assert "<script>" not in html and "javascript:" not in html


def test_csrf_enforced_when_enabled(tmp_path):
    class Cfg(TestConfig):
        CSRF_ENABLED = True
        UPLOAD_DIR = str(tmp_path)

    app = create_app(Cfg)
    c = app.test_client()
    assert c.post("/login", data={"email": "a@b.c", "password": "x"}).status_code == 400


def test_chapa_flow_verifies_with_provider(app, client, monkeypatch):
    from academy.routes import pay as pay_routes

    app.config["CHAPA_SECRET_KEY"] = "CHASECK_TEST-x"
    monkeypatch.setattr(pay_routes, "chapa_initialize", lambda *a, **k: "https://checkout.chapa.co/x")
    cid = course(app)
    register(client)
    r = client.post(f"/pay/course/{cid}/chapa")
    assert r.status_code == 302 and r.location.startswith("https://checkout.chapa.co")
    with app.app_context():
        tx_ref = Payment.query.filter_by(method="chapa").one().tx_ref
    # A forged webhook does nothing if the provider does not confirm the payment
    monkeypatch.setattr(pay_routes, "chapa_verify", lambda p: None)
    client.post("/pay/chapa/webhook", json={"tx_ref": tx_ref, "status": "success"})
    with app.app_context():
        assert Payment.query.filter_by(tx_ref=tx_ref).one().status == "initiated"
    monkeypatch.setattr(pay_routes, "chapa_verify", lambda p: "CHAPA-REF-1")
    r = client.get(f"/pay/chapa/return/{tx_ref}", follow_redirects=True)
    assert b"course is unlocked" in r.data
    with app.app_context():
        assert Payment.query.filter_by(tx_ref=tx_ref).one().status == "paid"
