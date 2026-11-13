"""Public (free preview) lessons: guests can see every course locked, watch the public lessons,
and admins decide per lesson whether it is public or locked."""
import os
import re

import pytest

from academy import create_app
from academy.config import TestConfig
from academy.models import Course, Lesson, User, db
from academy.seed import seed


@pytest.fixture
def app(tmp_path):
    cfg = type("Cfg", (TestConfig,), {"UPLOAD_DIR": str(tmp_path / "uploads"),
                                      "BUNNY_LIBRARY_ID": "123", "BUNNY_API_KEY": "key",
                                      "BUNNY_TOKEN_KEY": "tok"})
    app = create_app(cfg)
    with app.app_context():
        seed()
    return app


def login(c, email, password, **extra):
    return c.post("/login", data={"email": email, "password": password, **extra})


def lessons(app, slug="project-management-fundamentals"):
    with app.app_context():
        course = Course.query.filter_by(slug=slug).one()
        return course.id, [l.id for l in course.lessons]


def set_public(app, lesson_id, public):
    with app.app_context():
        db.session.get(Lesson, lesson_id).is_preview = public
        db.session.commit()


GUID = "0123456789abcdef0123456789abcdef"


def make_video_lesson(app, lesson_id, public, provider="bunny"):
    with app.app_context():
        l = db.session.get(Lesson, lesson_id)
        l.kind, l.is_preview, l.video_provider = "video", public, provider
        if provider == "bunny":
            l.video_ref = GUID
        else:
            l.video_file = "intro.mp4"
            folder = os.path.join(app.config["UPLOAD_DIR"], "videos")
            os.makedirs(folder, exist_ok=True)
            with open(os.path.join(folder, "intro.mp4"), "wb") as fh:
                fh.write(b"0" * 2048)
        db.session.commit()


# --- Guests see every course, locked ----------------------------------------------------------

def test_guest_sees_the_whole_syllabus_with_locks_and_public_lessons(app):
    cid, ids_ = lessons(app)
    set_public(app, ids_[0], True)
    for lid in ids_[1:]:
        set_public(app, lid, False)
    page = app.test_client().get("/courses/project-management-fundamentals").data.decode()
    syllabus = page.split('id="syllabus"')[1].split("</ol>")[0]
    assert syllabus.count("<li>") >= len(ids_)                      # every lesson is listed
    assert f'href="/learn/lesson/{ids_[0]}"' in syllabus and f'href="/learn/lesson/{ids_[1]}"' not in syllabus
    assert syllabus.count('class="small muted locked"') == len(ids_) - 1 and "Free preview" in syllabus


def test_guest_enroll_buttons_lead_to_the_student_login(app):
    page = app.test_client().get("/courses/excel-for-the-workplace").data.decode()
    assert re.search(r'href="/login\?area=student&amp;next=/courses/excel-for-the-workplace"', page)
    login_page = app.test_client().get("/login?area=student&next=/courses/excel-for-the-workplace").data.decode()
    assert "Welcome back" in login_page and "next=/courses/excel-for-the-workplace" in login_page


def test_catalog_lists_every_published_course_for_guests_but_no_drafts(app):
    with app.app_context():
        db.session.add(Course(slug="secret-draft", title="Secret Draft", is_published=False))
        db.session.commit()
    page = app.test_client().get("/courses").data.decode()
    assert "Project Management Fundamentals" in page and "Excel for the Workplace" in page
    assert "Secret Draft" not in page


# --- Watching public lessons without an account -----------------------------------------------

def test_guest_can_open_a_public_lesson_but_not_a_locked_one(app):
    _, ids_ = lessons(app)
    set_public(app, ids_[0], True)
    set_public(app, ids_[1], False)
    c = app.test_client()
    page = c.get(f"/learn/lesson/{ids_[0]}")
    assert page.status_code == 200 and "free preview lesson" in page.data.decode()
    locked = c.get(f"/learn/lesson/{ids_[1]}")
    assert locked.status_code == 302 and "/login" in locked.headers["Location"]


def test_guest_plays_a_public_hosted_video(app):
    _, ids_ = lessons(app)
    make_video_lesson(app, ids_[0], public=True)
    page = app.test_client().get(f"/learn/lesson/{ids_[0]}").data.decode()
    assert "https://iframe.mediadelivery.net/embed/123/" + GUID in page and "token=" in page
    assert 'class="watermark"' not in page            # no email to show: this visitor is not signed in


def test_guest_does_not_get_the_video_of_a_locked_lesson(app):
    _, ids_ = lessons(app)
    make_video_lesson(app, ids_[1], public=False)
    c = app.test_client()
    assert "iframe.mediadelivery.net" not in c.get(f"/learn/lesson/{ids_[1]}", follow_redirects=True).data.decode()


def test_guest_streams_a_public_video_stored_on_the_server(app):
    _, ids_ = lessons(app)
    make_video_lesson(app, ids_[0], public=True, provider="local")
    c = app.test_client()
    page = c.get(f"/learn/lesson/{ids_[0]}").data.decode()
    src = re.search(r'<video[^>]* src="([^"]+)"', page).group(1).replace("&amp;", "&")
    assert c.get(src).status_code == 200
    assert c.get(f"/learn/lesson/{ids_[0]}/video").status_code == 403           # no token
    assert c.get(src.split("?")[0] + "?t=forged").status_code == 403


def test_a_stream_link_made_for_a_guest_does_not_work_after_the_lesson_is_locked(app):
    _, ids_ = lessons(app)
    make_video_lesson(app, ids_[0], public=True, provider="local")
    c = app.test_client()
    src = re.search(r'<video[^>]* src="([^"]+)"', c.get(f"/learn/lesson/{ids_[0]}").data.decode()).group(1)
    set_public(app, ids_[0], False)
    assert c.get(src.replace("&amp;", "&")).status_code == 403


def test_public_lesson_hides_files_from_guests_and_shows_the_enroll_note(app):
    _, ids_ = lessons(app)
    set_public(app, ids_[0], True)
    page = app.test_client().get(f"/learn/lesson/{ids_[0]}").data.decode()
    assert 'id="materials"' not in page
    assert "Enroll to unlock the rest of the course." in page
    assert re.search(r'href="/login\?next=/courses/project-management-fundamentals"[^>]*>Enroll<', page)
    sidebar = page.split("sidebar-body")[1].split("</details>")[0]
    assert "locked-lesson" in sidebar and f'href="/learn/lesson/{ids_[1]}"' not in sidebar


def test_enrolled_students_do_not_see_the_preview_note(app):
    _, ids_ = lessons(app)
    set_public(app, ids_[0], True)
    c = app.test_client()
    c.post("/register", data={"full_name": "Free Student", "email": "fs@example.com", "password": "secret123",
                              "password2": "secret123", "agree": "1"})
    with app.app_context():
        from academy.enrollment import enroll_student
        enroll_student(User.query.filter_by(email="fs@example.com").one(),
                       Course.query.filter_by(slug="project-management-fundamentals").one())
        db.session.commit()
    page = c.get(f"/learn/lesson/{ids_[0]}").data.decode()
    assert "free preview lesson" not in page


def test_public_lesson_note_in_amharic(app):
    _, ids_ = lessons(app)
    set_public(app, ids_[0], True)
    c = app.test_client()
    c.set_cookie("lang", "am")
    page = c.get(f"/learn/lesson/{ids_[0]}").data.decode()
    assert "ይህ ነጻ የቅድመ እይታ ትምህርት ነው።" in page and "This is a free preview lesson." not in page


# --- Admin: lock or make public, per lesson ---------------------------------------------------

def test_admin_switches_a_lesson_between_public_and_locked(app):
    cid, ids_ = lessons(app)
    set_public(app, ids_[0], False)
    admin = app.test_client()
    login(admin, "admin@example.com", "admin12345", area="staff")
    page = admin.get(f"/admin/courses/{cid}/lessons").data.decode()
    lesson_table = page.split('id="lessons"')[1].split("Quick add")[0]
    assert "Make public" in lesson_table and "Lock it" not in lesson_table
    admin.post(f"/admin/lessons/{ids_[0]}/public")
    with app.app_context():
        assert db.session.get(Lesson, ids_[0]).is_preview is True
    assert "Lock it" in admin.get(f"/admin/courses/{cid}/lessons").data.decode()
    admin.post(f"/admin/lessons/{ids_[0]}/public")
    with app.app_context():
        assert db.session.get(Lesson, ids_[0]).is_preview is False


def test_only_an_admin_may_switch_a_lesson(app):
    """PRD v1.1 CRS-5 / NX-20: a hired teacher does not decide what is free."""
    cid, ids_ = lessons(app)
    with app.app_context():
        other = User(email="other@example.com", full_name="Other Teacher", role="instructor")
        other.set_password("secret123")
        student = User(email="s@example.com", full_name="S Student")
        student.set_password("secret123")
        db.session.add_all([other, student])
        db.session.get(Course, cid).instructor_id = User.query.filter_by(email="instructor@example.com").one().id
        db.session.commit()
        before = db.session.get(Lesson, ids_[0]).is_preview
    for email, pw, area in (("other@example.com", "secret123", "staff"), ("s@example.com", "secret123", "student"),
                            ("instructor@example.com", "instructor123", "staff")):      # the course's own teacher too
        c = app.test_client()
        login(c, email, pw, area=area)
        assert c.post(f"/admin/lessons/{ids_[0]}/public").status_code == 403, email
    assert app.test_client().post(f"/admin/lessons/{ids_[0]}/public").status_code in (302, 400, 401, 403)
    with app.app_context():
        assert db.session.get(Lesson, ids_[0]).is_preview is before
    admin = app.test_client()
    login(admin, "admin@example.com", "admin12345", area="staff")
    assert admin.post(f"/admin/lessons/{ids_[0]}/public").status_code == 302
    with app.app_context():
        assert db.session.get(Lesson, ids_[0]).is_preview is (not before)
