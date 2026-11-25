"""Certificates: a clear place to find and download them (completion, course page, dashboard), a real PDF, and a graceful
fallback when the server cannot make PDFs."""
import re

import pytest

from academy import certificates, create_app
from academy.config import TestConfig
from academy.enrollment import enroll_student
from academy.models import Certificate, Course, Lesson, User, db
from academy.privacy import erase_account
from academy.seed import seed


def pdf_works():
    try:
        import weasyprint  # noqa: F401

        return True
    except (ImportError, OSError):
        return False


needs_pdf = pytest.mark.skipif(not pdf_works(), reason="WeasyPrint and its system libraries are not installed")


@pytest.fixture
def app(tmp_path):
    class Cfg(TestConfig):
        UPLOAD_DIR = str(tmp_path / "uploads")

    app = create_app(Cfg)
    with app.app_context():
        seed()
    return app


@pytest.fixture(autouse=True)
def reset_pdf_state():
    certificates._available = None
    certificates._CACHE.clear()
    yield
    certificates._available = None
    certificates._CACHE.clear()


def pdf_structure(data):
    """The PDF's text with every compressed stream inflated, so page objects can be found without a PDF library."""
    import zlib

    parts = [data]
    for chunk in re.findall(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        try:
            parts.append(zlib.decompress(chunk))
        except zlib.error:
            pass
    return b"\n".join(parts)


def register(client, email="learner@example.com", name="Hana Tesfaye"):
    return client.post("/register", data={"full_name": name, "email": email, "password": "secret123",
                                          "password2": "secret123", "agree": "1"})


def make_course(app, slug="short", title="Short course", lessons=1):
    with app.app_context():
        course = Course(slug=slug, title=title, price=0, is_published=True, hours=3)
        db.session.add(course)
        db.session.flush()
        for i in range(lessons):
            db.session.add(Lesson(course_id=course.id, title=f"Lesson {i + 1}", position=i + 1, kind="text", body="x"))
        db.session.commit()
        return course.id, [lesson.id for lesson in course.lessons]


def learner_with_certificate(app, **kw):
    client = app.test_client()
    register(client)
    cid, lids = make_course(app, **kw)
    with app.app_context():
        enroll_student(User.query.filter_by(email="learner@example.com").one(), db.session.get(Course, cid))
        db.session.commit()
    for lid in lids:
        client.post(f"/learn/lesson/{lid}/complete")
    with app.app_context():
        code = Certificate.query.one().code
    return client, code, cid, lids


# --- where a learner finds their certificate -------------------------------------------------------------------------

def test_no_banner_until_the_course_is_complete(app):
    client = app.test_client()
    register(client)
    cid, lids = make_course(app, lessons=2)
    with app.app_context():
        enroll_student(User.query.filter_by(email="learner@example.com").one(), db.session.get(Course, cid))
        db.session.commit()
    client.post(f"/learn/lesson/{lids[0]}/complete")
    page = client.get(f"/learn/lesson/{lids[1]}").data.decode()
    assert "Course complete" not in page and "View certificate" not in page
    assert "Course complete" not in client.get("/courses/short").data.decode()


def test_completing_the_last_lesson_shows_the_certificate_buttons_on_the_lesson_and_course_pages(app):
    client, code, cid, lids = learner_with_certificate(app)
    for page in (client.get(f"/learn/lesson/{lids[0]}").data.decode(), client.get("/courses/short").data.decode()):
        assert "Course complete! Your certificate is ready." in page
        assert f"/certificates/{code}" in page and "View certificate" in page


def test_the_dashboard_has_a_my_certificates_section_with_both_buttons(app):
    client, code, cid, lids = learner_with_certificate(app)
    page = client.get("/dashboard").data.decode()
    assert 'id="certificates"' in page and "My certificates" in page and 'href="#certificates"' in page
    assert f"/certificates/{code}" in page and "View certificate" in page
    # the finished course card also carries the buttons, and the certificates come before the course list
    assert page.index('id="certificates"') < page.index("My courses")
    assert page.count("View certificate") == 2


@needs_pdf
def test_download_button_is_offered_where_a_pdf_can_be_made(app):
    client, code, cid, lids = learner_with_certificate(app)
    page = client.get("/dashboard").data.decode()
    assert f"/certificates/{code}/download" in page and "Download PDF" in page
    assert f"/certificates/{code}/download" in client.get(f"/certificates/{code}").data.decode()


def test_without_pdf_support_the_buttons_and_the_page_fall_back_to_print(app, monkeypatch):
    client, code, cid, lids = learner_with_certificate(app)
    monkeypatch.setattr(certificates, "_available", False)
    dash = client.get("/dashboard").data.decode()
    assert "Download PDF" not in dash and "View certificate" in dash
    page = client.get(f"/certificates/{code}").data.decode()
    assert "Print / Save as PDF" in page and "Download PDF" not in page
    assert client.get(f"/certificates/{code}/download").status_code == 404


def test_a_guest_sees_no_banner_but_can_open_and_verify_a_certificate(app):
    client, code, cid, lids = learner_with_certificate(app)
    guest = app.test_client()
    assert b"Certificate of Completion" in guest.get(f"/certificates/{code}").data
    assert b"My certificates" not in guest.get(f"/certificates/{code}").data      # only the owner gets that link
    assert b"My certificates" in client.get(f"/certificates/{code}").data


def test_passing_the_last_quiz_shows_the_certificate_on_the_result_page(app):
    client = app.test_client()
    register(client)
    with app.app_context():
        course = Course.query.filter_by(slug="project-management-fundamentals").one()
        enroll_student(User.query.filter_by(email="learner@example.com").one(), course)
        db.session.commit()
        lessons = [lesson.id for lesson in course.lessons]
        a = course.assessments[0]
        aid = a.id
        right = {f"q{q.id}": str(q.correct_index) for q in a.questions}
    for lid in lessons:
        client.post(f"/learn/lesson/{lid}/complete")
    r = client.post(f"/assessments/{aid}/start", follow_redirects=True)
    page = client.post(r.request.path, data=right, follow_redirects=True).data.decode()
    assert "You passed" in page and "Course complete! Your certificate is ready." in page and "View certificate" in page


def test_labels_follow_the_language(app):
    client, code, cid, lids = learner_with_certificate(app)
    client.get("/lang/am")
    page = client.get("/dashboard").data.decode()
    assert "የእኔ የምስክር ወረቀቶች" in page and "የምስክር ወረቀቱን ይመልከቱ" in page


# --- the PDF ---------------------------------------------------------------------------------------------------------

@needs_pdf
def test_the_pdf_is_a_real_one_page_a4_landscape_file_with_a_sensible_name(app):
    client, code, cid, lids = learner_with_certificate(app)
    r = client.get(f"/certificates/{code}/download")
    assert r.status_code == 200 and r.mimetype == "application/pdf"
    assert r.headers["Content-Disposition"] == f'attachment; filename="certificate-{code}.pdf"'
    assert r.data.startswith(b"%PDF-")
    structure = pdf_structure(r.data)
    assert len(re.findall(rb"/Type\s*/Page\b", structure)) == 1
    assert re.search(rb"/MediaBox\s*\[\s*0\s+0\s+841\.\d+\s+595\.\d+\s*\]", structure)      # A4 landscape


@needs_pdf
def test_anyone_with_the_id_can_download_but_an_unknown_id_is_a_404(app):
    client, code, cid, lids = learner_with_certificate(app)
    assert app.test_client().get(f"/certificates/{code}/download").status_code == 200
    assert app.test_client().get(f"/certificates/{code.lower()}/download").status_code == 200
    assert app.test_client().get("/certificates/NOSUCHCODE/download").status_code == 404


@needs_pdf
def test_the_pdf_is_made_once_and_reused(app, monkeypatch):
    client, code, cid, lids = learner_with_certificate(app)
    import weasyprint

    calls = []
    real = weasyprint.HTML

    class Counting(real):
        def write_pdf(self, *a, **k):
            calls.append(1)
            return super().write_pdf(*a, **k)

    monkeypatch.setattr(weasyprint, "HTML", Counting)
    first = client.get(f"/certificates/{code}/download").data
    second = client.get(f"/certificates/{code}/download").data
    assert first == second and len(calls) == 1


@needs_pdf
def test_an_erased_account_is_not_served_from_the_cache(app):
    client, code, cid, lids = learner_with_certificate(app)
    before = client.get(f"/certificates/{code}/download").data
    with app.app_context():
        erase_account(User.query.filter_by(email="learner@example.com").one())
        db.session.commit()
    after = app.test_client().get(f"/certificates/{code}/download").data
    assert before != after


def test_certificates_switched_off_means_no_page_and_no_download(tmp_path):
    class Cfg(TestConfig):
        UPLOAD_DIR = str(tmp_path / "up")
        FEATURE_CERTIFICATES = False

    app = create_app(Cfg)
    assert app.test_client().get("/certificates/ANYCODE/download").status_code == 404
    assert app.test_client().get("/certificates/ANYCODE").status_code == 404


# --- the email -------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("available", [True, False])
def test_the_certificate_email_links_the_pdf_only_when_one_can_be_made(tmp_path, monkeypatch, available):
    import smtplib

    sent = []

    class FakeSMTP:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, **k):
            pass

        def login(self, *a):
            pass

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)

    class Cfg(TestConfig):
        UPLOAD_DIR = str(tmp_path / "up")
        MAIL_SERVER = "smtp.test"
        MAIL_FROM = "Mek <no-reply@mek.test>"

    app = create_app(Cfg)
    with app.app_context():
        seed()
    monkeypatch.setattr(certificates, "_available", available)
    client = app.test_client()
    register(client)
    sent.clear()
    cid, lids = make_course(app)
    with app.app_context():
        enroll_student(User.query.filter_by(email="learner@example.com").one(), db.session.get(Course, cid))
        db.session.commit()
    client.post(f"/learn/lesson/{lids[0]}/complete")
    body = [m for m in sent if m["Subject"].startswith("Your certificate")][0].get_content()
    assert "/certificates/" in body
    assert ("/download" in body) is available
