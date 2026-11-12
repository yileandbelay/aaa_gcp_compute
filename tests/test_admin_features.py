"""Students vs staff, instructor permissions, enrolling students, YouTube/Vimeo and link
resources, and the contact page."""
import re

import pytest

from academy import create_app
from academy.config import TestConfig
from academy.models import (Attachment, ContactMessage, Course, Enrollment, Invitation, Lesson, User, db)
from academy.seed import seed


@pytest.fixture
def app(tmp_path):
    cfg = type("Cfg", (TestConfig,), {"UPLOAD_DIR": str(tmp_path / "uploads")})
    app = create_app(cfg)
    with app.app_context():
        seed()
    return app


@pytest.fixture
def outbox(monkeypatch):
    sent = []

    def fake(to, subject, body, reply_to=None):
        sent.append({"to": to, "subject": subject, "body": body, "reply_to": reply_to})
        return True

    for mod in ("academy.routes.admin", "academy.routes.auth", "academy.routes.main", "academy.enrollment"):
        monkeypatch.setattr(f"{mod}.send_email", fake)
    return sent


def login(c, email, password):
    return c.post("/login", data={"email": email, "password": password})


def register(c, email="student@example.com", name="Abebe Kebede"):
    return c.post("/register", data={"full_name": name, "email": email, "password": "secret123",
                                     "password2": "secret123", "agree": "1"})


def admin_client(app):
    c = app.test_client()
    login(c, "admin@example.com", "admin12345")
    return c


def instructor_client(app):
    c = app.test_client()
    login(c, "instructor@example.com", "instructor123")
    return c


def ids(app):
    with app.app_context():
        pm = Course.query.filter_by(slug="project-management-fundamentals").one()
        excel = Course.query.filter_by(slug="excel-for-the-workplace").one()
        return pm.id, excel.id


def make_student(app, email="s@example.com", name="Sam Student"):
    with app.app_context():
        u = User(full_name=name, email=email, role="student")
        u.set_password("secret123")
        db.session.add(u)
        db.session.commit()
        return u.id


# --- Students vs staff ----------------------------------------------------------------

def test_students_and_staff_are_listed_separately(app):
    make_student(app, "hana@example.com", "Hana Student")
    admin = admin_client(app)
    students = admin.get("/admin/students").data.decode()
    assert "Hana Student" in students and "Academy Admin" not in students and "Meklit Haile" not in students
    staff = admin.get("/admin/staff").data.decode()
    assert "Academy Admin" in staff and "Meklit Haile" in staff and "Hana Student" not in staff
    assert b"hana@example.com" in admin.get("/admin/students.csv").data
    assert b"admin@example.com" not in admin.get("/admin/students.csv").data


def test_registering_makes_a_student_not_staff(app):
    c = app.test_client()
    register(c)
    with app.app_context():
        assert User.query.filter_by(email="student@example.com").one().role == "student"


def test_admin_adds_staff_who_set_their_own_password(app, outbox):
    admin = admin_client(app)
    r = admin.post("/admin/staff/new", data={"full_name": "Selam Teacher", "email": "selam@example.com",
                                             "role": "instructor"}, follow_redirects=True)
    assert b"Selam Teacher" in r.data
    with app.app_context():
        u = User.query.filter_by(email="selam@example.com").one()
        assert u.role == "instructor"
    mail = outbox[-1]
    assert mail["to"] == "selam@example.com"
    link = re.search(r"http://localhost(/reset-password/\S+)", mail["body"]).group(1)
    c = app.test_client()
    c.post(link, data={"password": "mypassword1", "password2": "mypassword1"})
    assert b"Selam Teacher" in login(c, "selam@example.com", "mypassword1").data or \
        c.get("/admin/").status_code == 200
    # Staff are not counted as students
    assert b"Selam Teacher" not in admin.get("/admin/students").data


def test_staff_link_is_shown_when_email_is_not_set_up(app):
    admin = admin_client(app)  # real send_email: no MAIL_SERVER in tests, so it only logs
    r = admin.post("/admin/staff/new", data={"full_name": "No Mail", "email": "nomail@example.com",
                                             "role": "admin"}, follow_redirects=True)
    assert b"email is not set up" in r.data and b"/reset-password/" in r.data


def test_adding_staff_with_a_student_email_promotes_that_account(app):
    make_student(app, "promote@example.com", "Promote Me")
    admin = admin_client(app)
    admin.post("/admin/staff/new", data={"full_name": "Promote Me", "email": "promote@example.com",
                                         "role": "instructor"})
    with app.app_context():
        assert User.query.filter_by(email="promote@example.com").one().role == "instructor"
    assert b"Promote Me" in admin.get("/admin/staff").data


def test_changing_role_moves_user_between_lists(app):
    uid = make_student(app, "mover@example.com", "Mover")
    admin = admin_client(app)
    assert b"Mover" in admin.get("/admin/students").data
    admin.post(f"/admin/users/{uid}", data={"action": "role", "role": "admin"})
    assert b"Mover" in admin.get("/admin/staff").data and b"Mover" not in admin.get("/admin/students").data
    admin.post(f"/admin/users/{uid}", data={"action": "role", "role": "student"})
    assert b"Mover" in admin.get("/admin/students").data and b"Mover" not in admin.get("/admin/staff").data


def test_admin_cannot_change_own_role_or_suspend_self(app):
    admin = admin_client(app)
    with app.app_context():
        me = User.query.filter_by(email="admin@example.com").one().id
    admin.post(f"/admin/users/{me}", data={"action": "role", "role": "student"})
    admin.post(f"/admin/users/{me}", data={"action": "toggle_active"})
    with app.app_context():
        user = db.session.get(User, me)
        assert user.role == "admin" and user.is_active_account


def test_suspended_staff_are_locked_out_immediately(app):
    uid = make_student(app, "boss@example.com", "Second Admin")
    admin = admin_client(app)
    admin.post(f"/admin/users/{uid}", data={"action": "role", "role": "admin"})
    second = app.test_client()
    login(second, "boss@example.com", "secret123")
    assert second.get("/admin/").status_code == 200
    admin.post(f"/admin/users/{uid}", data={"action": "toggle_active"})
    assert second.get("/admin/").status_code == 302  # an open session stops working too


def test_old_student_url_still_works(app):
    uid = make_student(app)
    assert admin_client(app).get(f"/admin/students/{uid}").status_code == 200


def test_invite_links_last_longer_than_reset_links(app):
    from academy.routes.auth import make_reset_token, user_from_reset_token

    uid = make_student(app)
    with app.app_context():
        user = db.session.get(User, uid)
        invite, reset = make_reset_token(user, invite=True), make_reset_token(user)
        app.config["PASSWORD_RESET_TTL"] = 0
        assert user_from_reset_token(reset) is None
        assert user_from_reset_token(invite) is not None


# --- Instructor (course admin) permissions ----------------------------------------------

def test_instructor_manages_only_their_own_courses(app):
    pm, excel = ids(app)
    with app.app_context():
        c = db.session.get(Course, excel)
        c.instructor_id = User.query.filter_by(email="admin@example.com").one().id  # not the instructor's
        db.session.commit()
        other_lesson = c.lessons[0].id
    ins = instructor_client(app)
    assert ins.get(f"/admin/courses/{pm}").status_code == 200
    for url in (f"/admin/courses/{excel}", f"/admin/courses/{excel}/students",
                f"/admin/lessons/{other_lesson}", f"/admin/courses/{excel}/lessons/new"):
        assert ins.get(url).status_code == 403, url
    assert ins.post(f"/admin/lessons/{other_lesson}/delete").status_code == 403
    assert ins.post(f"/admin/courses/{excel}/announce", data={"title": "x"}).status_code == 403
    page = ins.get("/admin/courses").data.decode()
    assert "Project Management Fundamentals" in page and "Excel for the Workplace" not in page


def test_instructor_cannot_use_admin_only_areas(app):
    ins = instructor_client(app)
    for url in ("/admin/students", "/admin/staff", "/admin/payments", "/admin/messages"):
        assert ins.get(url).status_code == 403, url
    assert ins.post("/admin/staff/new", data={"full_name": "X Y", "email": "x@y.zz"}).status_code == 403
    assert ins.get("/admin/").status_code == 200


def test_new_course_belongs_to_its_creator_and_admin_can_reassign(app):
    ins = instructor_client(app)
    r = ins.post("/admin/courses/new", data={"title": "Time Management", "price": "0"})
    cid = int(re.search(r"/admin/courses/(\d+)", r.location).group(1))
    with app.app_context():
        c = db.session.get(Course, cid)
        assert c.instructor.email == "instructor@example.com"
        admin_id = User.query.filter_by(email="admin@example.com").one().id
    admin = admin_client(app)
    admin.post(f"/admin/courses/{cid}", data={"title": "Time Management", "price": "0", "instructor_id": admin_id})
    with app.app_context():
        assert db.session.get(Course, cid).instructor_id == admin_id
    assert ins.get(f"/admin/courses/{cid}").status_code == 403  # no longer theirs


# --- Enrolling students -------------------------------------------------------------------

def test_admin_enrolls_picked_students_and_pasted_emails(app, outbox):
    pm, _ = ids(app)
    sid = make_student(app, "picked@example.com", "Picked Student")
    make_student(app, "typed@example.com", "Typed Student")
    admin = admin_client(app)
    page = admin.get(f"/admin/courses/{pm}/students").data.decode()
    assert "picked@example.com" in page and "admin@example.com" not in page.split("pickbox")[1]
    r = admin.post(f"/admin/courses/{pm}/students", data={
        "action": "enroll", "student_ids": [sid], "notify": "1",
        "emails": "typed@example.com, newperson@example.com\nnot-an-email\ninstructor@example.com\npicked@example.com",
    }, follow_redirects=True)
    text = r.data.decode()
    assert "2 enrolled" in text and "1 invited" in text and "1 already had access" in text
    assert "instructor@example.com" in text and "not-an-email" in text
    with app.app_context():
        picked = User.query.filter_by(email="picked@example.com").one()
        enr = Enrollment.query.filter_by(user_id=picked.id, course_id=pm).one()
        assert enr.status == "active" and enr.source == "assigned" and enr.assigned_by.email == "admin@example.com"
        assert Invitation.query.filter_by(email="newperson@example.com", course_id=pm).count() == 1
    subjects = [m["subject"] for m in outbox]
    assert sum("enrolled" in s for s in subjects) == 2 and any("invited" in s for s in subjects)
    assert b"newperson@example.com" in admin.get(f"/admin/courses/{pm}/students").data  # waiting list


def test_invited_person_gets_the_course_when_they_register(app):
    pm, _ = ids(app)
    admin = admin_client(app)
    admin.post(f"/admin/courses/{pm}/students", data={"action": "enroll", "emails": "Later@Example.com"})
    c = app.test_client()
    r = register(c, email="later@example.com")
    assert b"enrolled in Project Management Fundamentals" in c.get(r.location).data
    with app.app_context():
        user = User.query.filter_by(email="later@example.com").one()
        assert user.has_access(db.session.get(Course, pm))
        assert Invitation.query.count() == 0


def test_enroll_without_notify_sends_no_email_and_can_cancel_invite(app, outbox):
    pm, _ = ids(app)
    sid = make_student(app)
    admin = admin_client(app)
    admin.post(f"/admin/courses/{pm}/students", data={"action": "enroll", "student_ids": [sid],
                                                      "emails": "x@example.com"})
    assert outbox == []
    with app.app_context():
        inv = Invitation.query.one().id
    admin.post(f"/admin/courses/{pm}/students", data={"action": "cancel_invite", "invitation_id": inv})
    with app.app_context():
        assert Invitation.query.count() == 0


def test_remove_restore_and_discard_access(app):
    pm, _ = ids(app)
    sid = make_student(app)
    admin = admin_client(app)
    admin.post(f"/admin/courses/{pm}/students", data={"action": "enroll", "student_ids": [sid]})
    with app.app_context():
        eid = Enrollment.query.one().id
    admin.post(f"/admin/courses/{pm}/students", data={"action": "revoke", "enrollment_id": eid})
    with app.app_context():
        assert Enrollment.query.one().status == "revoked"
    admin.post(f"/admin/courses/{pm}/students", data={"action": "activate", "enrollment_id": eid})
    with app.app_context():
        assert Enrollment.query.one().status == "active"
    admin.post(f"/admin/courses/{pm}/students", data={"action": "remove", "enrollment_id": eid})
    with app.app_context():
        assert Enrollment.query.count() == 0


def test_instructor_enrolls_by_email_without_seeing_all_students(app):
    pm, _ = ids(app)
    make_student(app, "private@example.com", "Private Person")
    ins = instructor_client(app)
    page = ins.get(f"/admin/courses/{pm}/students").data.decode()
    assert "private@example.com" not in page and "pickbox" not in page
    ins.post(f"/admin/courses/{pm}/students", data={"action": "enroll", "emails": "private@example.com"})
    with app.app_context():
        assert Enrollment.query.filter_by(course_id=pm).count() == 1


def test_enrollment_source_is_recorded_for_paid_and_free(app):
    pm, excel = ids(app)
    c = app.test_client()
    register(c)
    c.post(f"/pay/course/{pm}/demo")
    c.post(f"/pay/course/{excel}")
    with app.app_context():
        sources = {e.course_id: e.source for e in Enrollment.query.all()}
    assert sources == {pm: "purchase", excel: "free"}


def test_student_page_lists_only_courses_not_yet_assigned(app):
    pm, excel = ids(app)
    sid = make_student(app)
    admin = admin_client(app)
    admin.post(f"/admin/users/{sid}", data={"action": "grant", "course_id": pm})
    page = admin.get(f"/admin/users/{sid}").data.decode()
    assert "Project Management Fundamentals" in page and "Assigned" in page
    assert f'<option value="{pm}">' not in page and f'<option value="{excel}">' in page


# --- Resources: files, links, YouTube/Vimeo -------------------------------------------------

def lesson_post(admin, course_id, **data):
    base = {"title": "Lesson X", "kind": "text", "is_preview_present": "1"}
    return admin.post(f"/admin/courses/{course_id}/lessons/new", data={**base, **data})


def test_links_are_saved_validated_and_shown_to_students(app):
    pm, _ = ids(app)
    admin = admin_client(app)
    r = lesson_post(admin, pm, kind="link", links=(
        "Free course | https://example.org/course\n"
        "example.com/page\n"
        "Bad | javascript:alert(1)\n"
        "Data | data:text/html,hi\n"))
    assert r.status_code == 302
    with app.app_context():
        lesson = Lesson.query.filter_by(title="Lesson X").one()
        got = {(a.original_name, a.url) for a in lesson.attachments}
        assert got == {("Free course", "https://example.org/course"), ("example.com", "https://example.com/page")}
        lid = lesson.id
        att_id = [a.id for a in lesson.attachments][0]
    c = app.test_client()
    register(c)
    c.post(f"/pay/course/{pm}/demo")
    page = c.get(f"/learn/lesson/{lid}").data.decode()
    assert 'href="https://example.org/course" target="_blank" rel="noopener noreferrer"' in page
    assert "javascript:" not in page
    assert c.get(f"/learn/material/{att_id}").status_code == 404  # links are not downloads


def test_link_can_be_removed(app):
    pm, _ = ids(app)
    admin = admin_client(app)
    lesson_post(admin, pm, links="Keep me | https://example.org/a")
    with app.app_context():
        att = Attachment.query.filter_by(original_name="Keep me").one()
        aid, lid = att.id, att.lesson_id
    admin.post(f"/admin/materials/{aid}/delete")
    with app.app_context():
        assert Attachment.query.filter_by(lesson_id=lid).count() == 0


def test_youtube_link_is_normalised_and_embedded_without_watermark(app):
    pm, _ = ids(app)
    admin = admin_client(app)
    lesson_post(admin, pm, kind="video", video_provider="external",
                video_ref="https://youtu.be/dQw4w9WgXcQ?t=10")
    with app.app_context():
        lesson = Lesson.query.filter_by(title="Lesson X").one()
        assert (lesson.video_provider, lesson.video_ref, lesson.has_video) == ("external", "yt:dQw4w9WgXcQ", True)
        lid = lesson.id
    editor = admin.get(f"/admin/lessons/{lid}").data.decode()
    assert 'value="https://www.youtube.com/watch?v=dQw4w9WgXcQ"' in editor
    c = app.test_client()
    register(c)
    c.post(f"/pay/course/{pm}/demo")
    page = c.get(f"/learn/lesson/{lid}").data.decode()
    assert 'src="https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?rel=0"' in page
    assert 'class="watermark"' not in page


def test_vimeo_link_and_bad_links(app):
    pm, _ = ids(app)
    admin = admin_client(app)
    lesson_post(admin, pm, title="Vim", kind="video", video_provider="external", video_ref="https://vimeo.com/123456789")
    with app.app_context():
        assert Lesson.query.filter_by(title="Vim").one().video_ref == "vimeo:123456789"
    for bad in ("https://evil.example/watch?v=dQw4w9WgXcQ", "javascript:alert(1)", "https://youtube.com.evil.io/watch?v=dQw4w9WgXcQ"):
        r = lesson_post(admin, pm, title="Bad", kind="video", video_provider="external", video_ref=bad)
        assert r.status_code == 400, bad


def test_quick_add_creates_lesson_and_opens_editor(app):
    pm, _ = ids(app)
    admin = admin_client(app)
    r = admin.post(f"/admin/courses/{pm}/lessons/new", data={"title": "Quick one", "kind": "video", "action": "edit"})
    assert re.search(r"/admin/lessons/\d+$", r.location)
    with app.app_context():
        lesson = Lesson.query.filter_by(title="Quick one").one()
        assert lesson.kind == "video" and lesson.is_preview is False
    r = lesson_post(admin, pm, title="Another", action="save_add")
    assert r.location.endswith(f"/admin/courses/{pm}/lessons/new")


def test_editing_without_the_preview_marker_keeps_the_preview_flag(app):
    pm, _ = ids(app)
    admin = admin_client(app)
    with app.app_context():
        lesson = Lesson.query.filter_by(course_id=pm, is_preview=True).first()
        lid = lesson.id
    admin.post(f"/admin/lessons/{lid}", data={"title": "Renamed", "kind": "text"})  # a partial form
    with app.app_context():
        assert db.session.get(Lesson, lid).is_preview is True


def test_seed_includes_a_link_lesson(app):
    with app.app_context():
        lesson = Lesson.query.filter_by(kind="link").one()
        assert any(a.is_link for a in lesson.attachments)


# --- Contact page ---------------------------------------------------------------------------

def test_contact_page_shows_details(app):
    app.config.update(CONTACT_EMAIL="hello@mek-academy.com", CONTACT_PHONE="+251 911 000 000",
                      CONTACT_WHATSAPP="+251911000000", CONTACT_TELEGRAM="@mekacademy",
                      CONTACT_ADDRESS="Bole, Addis Ababa", CONTACT_HOURS="Mon-Fri 9:00-17:00")
    page = app.test_client().get("/contact").data.decode()
    for needle in ("hello@mek-academy.com", "+251 911 000 000", "https://wa.me/251911000000",
                   "https://t.me/mekacademy", "Bole, Addis Ababa", "Mon-Fri 9:00-17:00", "Send a message"):
        assert needle in page, needle
    assert 'href="/contact"' in page  # in the navigation


def test_contact_form_saves_and_emails_the_academy(app, outbox):
    app.config["CONTACT_EMAIL"] = "hello@mek-academy.com"
    c = app.test_client()
    r = c.post("/contact", data={"name": "Hana", "email": "hana@example.com", "subject": "Fees",
                                 "message": "How do I pay by bank transfer?"}, follow_redirects=True)
    assert b"your message has been sent" in r.data
    with app.app_context():
        m = ContactMessage.query.one()
        assert (m.name, m.email, m.subject, m.is_read) == ("Hana", "hana@example.com", "Fees", False)
    mail = outbox[-1]
    assert mail["to"] == "hello@mek-academy.com" and mail["reply_to"] == "hana@example.com"
    assert "bank transfer" in mail["body"] and "Fees" in mail["subject"]


def test_contact_form_validation_honeypot_and_rate_limit(app, outbox):
    c = app.test_client()
    r = c.post("/contact", data={"name": "H", "email": "bad", "message": "short"})
    assert r.status_code == 400 and b"valid email" in r.data and b"your name" in r.data
    with app.app_context():
        assert ContactMessage.query.count() == 0
    c.post("/contact", data={"name": "Bot", "email": "bot@example.com", "message": "buy cheap pills now",
                             "website": "http://spam.example"})
    with app.app_context():
        assert ContactMessage.query.count() == 0  # pretended success, nothing stored
    app.config["CONTACT_MAX_PER_HOUR"] = 2
    ok = {"name": "Hana", "email": "hana@example.com", "message": "A real question about courses"}
    for _ in range(2):
        assert c.post("/contact", data=ok).status_code == 302
    r = c.post("/contact", data=ok)
    assert r.status_code == 400 and b"several messages" in r.data


def test_contact_does_not_email_the_placeholder_address(app, outbox):
    app.test_client().post("/contact", data={"name": "Hana", "email": "hana@example.com",
                                             "message": "A real question about courses"})
    assert outbox == []
    with app.app_context():
        assert ContactMessage.query.count() == 1


def test_contact_form_can_be_switched_off(app):
    app.config["CONTACT_FORM"] = False
    c = app.test_client()
    assert b"Send a message" not in c.get("/contact").data
    c.post("/contact", data={"name": "Hana", "email": "hana@example.com", "message": "A real question here"})
    with app.app_context():
        assert ContactMessage.query.count() == 0


def test_admin_reads_and_manages_messages(app):
    app.test_client().post("/contact", data={"name": "Hana", "email": "hana@example.com",
                                             "subject": "Fees", "message": "How do I pay?"})
    admin = admin_client(app)
    page = admin.get("/admin/messages").data.decode()
    assert "How do I pay?" in page and "nav-badge" in page
    with app.app_context():
        mid = ContactMessage.query.one().id
    admin.post(f"/admin/messages/{mid}", data={"action": "read"})
    assert "nav-badge" not in admin.get("/admin/messages").data.decode()
    admin.post(f"/admin/messages/{mid}", data={"action": "delete"})
    with app.app_context():
        assert ContactMessage.query.count() == 0


def test_migration_downgrade_and_upgrade_roundtrip(tmp_path):
    from flask_migrate import downgrade, upgrade

    from academy.config import Config

    cfg = type("Cfg", (Config,), {"SQLALCHEMY_DATABASE_URI": "sqlite:///" + str(tmp_path / "m.db"),
                                  "UPLOAD_DIR": str(tmp_path / "up")})
    app = create_app(cfg)
    with app.app_context():
        upgrade()
        downgrade(revision="a1b2c3d4e5f6")
        upgrade()
